"""Paradox (HOI4) .mesh / .anim + .dds -> glTF 2.0 binary (.glb) for the sand-table renderer (three.js GLTFLoader).

    python -I -X utf8 pdx2glb.py --dump <file.mesh|file.anim>
    python -I -X utf8 pdx2glb.py --mesh <m.mesh> [--anim id=<a.anim> ...] [--attach <locator>=<w.mesh> ...] --tex-dir <dir> --out <o.glb>
    python -I -X utf8 pdx2glb.py --batch <jobs.json>      (see hoi4_models.json next to this file)

The game files are read as data only (struct parsing); nothing in them is executed. Textures go through ffmpeg
(DDS DXT1/DXT5 -> PNG) and are embedded in the glb, so a glb is self-contained.

PDX binary layout ("@@b@" header): a tree of objects and properties.
  object   = '['*depth + name + '\\0'
  property = '!' + len(u8) + name + type('i'|'f'|'s') + count(i32) + data   (strings: i32 length + bytes incl. '\\0')
Meshes: object/<shape>/mesh{p, n, ta, u0, u1, tri, aabb_min, aabb_max, material{shader, diff, n, spec}, skin{bones, ix, w}},
        object/<shape>/skeleton/<bone>{ix, pa, tx (inverse bind, 3x4 column-major)}, locator/<name>{p, q, pa}.
Anims:  info{fps, sa (samples), j (joints)} + info/<bone>{sa ("tqs" subset that is sampled), t, q, s (initial values)},
        samples{t, q, s}: frame-major, bones in the order of info, only the channels listed in their sa.
Coordinates: PDX is left-handed y-up; glTF is right-handed y-up -> z is negated (positions, normals, translations,
quaternions (x, y, z, w) -> (-x, -y, z, w), bind matrices S*M*S) and triangle winding is reversed.
"""
import argparse
import json
import math
import os
import pathlib
import struct
import subprocess
import sys
import tempfile


# ---------------------------------------------------------------- PDX tree
class Node:
    def __init__(self, name, depth):
        self.name, self.depth, self.props, self.kids = name, depth, {}, []

    def get(self, name):
        for k in self.kids:
            if k.name == name:
                return k
        return None

    def __repr__(self):
        return f"Node({self.name!r}, props={list(self.props)}, kids={[k.name for k in self.kids]})"


def parse_pdx(path):
    b = pathlib.Path(path).read_bytes()
    if b[:4] != b"@@b@":
        raise ValueError(f"{path}: not a PDX binary file")
    pos, root, stack = 4, Node("<root>", 0), None
    stack = [root]
    n = len(b)
    while pos < n:
        c = b[pos:pos + 1]
        if c == b"!":
            ln = b[pos + 1]
            name = b[pos + 2:pos + 2 + ln].decode("latin-1")
            pos += 2 + ln
            typ = b[pos:pos + 1].decode("latin-1")
            cnt = struct.unpack_from("<i", b, pos + 1)[0]
            pos += 5
            if typ == "i":
                val = list(struct.unpack_from(f"<{cnt}i", b, pos)); pos += 4 * cnt
            elif typ == "f":
                val = list(struct.unpack_from(f"<{cnt}f", b, pos)); pos += 4 * cnt
            elif typ == "s":
                val = []
                for _ in range(cnt):
                    sl = struct.unpack_from("<i", b, pos)[0]; pos += 4
                    val.append(b[pos:pos + sl].split(b"\0")[0].decode("latin-1")); pos += sl
            else:
                raise ValueError(f"{path}: unknown property type {typ!r} at {pos}")
            stack[-1].props[name] = val
        elif c == b"[":
            d = 0
            while b[pos:pos + 1] == b"[":
                d += 1; pos += 1
            e = b.index(b"\0", pos)
            name = b[pos:e].decode("latin-1"); pos = e + 1
            while len(stack) > d:
                stack.pop()
            node = Node(name, d)
            stack[-1].kids.append(node)
            stack.append(node)
        else:
            raise ValueError(f"{path}: unexpected byte {c!r} at {pos}")
    return root


def dump(node, ind=0, out=sys.stdout):
    for k, v in node.props.items():
        s = v if len(v) <= 6 else f"{v[:6]}... ({len(v)})"
        print("  " * ind + f"!{k} = {s}", file=out)
    for k in node.kids:
        print("  " * ind + f"[{k.name}]", file=out)
        dump(k, ind + 1, out)


# ---------------------------------------------------------------- maths (column-major 4x4 as 16-lists)
def mat_from_tx(tx):                      # PDX 3x4 column-major -> 4x4 column-major
    return [tx[0], tx[1], tx[2], 0, tx[3], tx[4], tx[5], 0, tx[6], tx[7], tx[8], 0, tx[9], tx[10], tx[11], 1]


def mat_mul(a, b):
    r = [0.0] * 16
    for c in range(4):
        for rr in range(4):
            r[c * 4 + rr] = sum(a[k * 4 + rr] * b[c * 4 + k] for k in range(4))
    return r


def mat_inv(m):                           # general 4x4 inverse (column-major)
    a = [[m[c * 4 + r] for c in range(4)] for r in range(4)]   # row-major copy
    inv = [[float(r == c) for c in range(4)] for r in range(4)]
    for i in range(4):
        p = max(range(i, 4), key=lambda r: abs(a[r][i]))
        a[i], a[p] = a[p], a[i]; inv[i], inv[p] = inv[p], inv[i]
        d = a[i][i]
        if abs(d) < 1e-12:
            raise ValueError("singular matrix")
        a[i] = [x / d for x in a[i]]; inv[i] = [x / d for x in inv[i]]
        for r in range(4):
            if r != i:
                f = a[r][i]
                a[r] = [x - f * y for x, y in zip(a[r], a[i])]
                inv[r] = [x - f * y for x, y in zip(inv[r], inv[i])]
    return [inv[r][c] for c in range(4) for r in range(4)]


S_FLIP = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 1]


def flip_mat(m):
    return mat_mul(S_FLIP, mat_mul(m, S_FLIP))


def decompose(m):                         # -> t, q (x, y, z, w), s (vec3); assumes no shear
    t = [m[12], m[13], m[14]]
    cols = [[m[0], m[1], m[2]], [m[4], m[5], m[6]], [m[8], m[9], m[10]]]
    s = [math.sqrt(sum(x * x for x in c)) or 1.0 for c in cols]
    r = [[cols[c][rr] / s[c] for c in range(3)] for rr in range(3)]   # row-major rotation
    tr = r[0][0] + r[1][1] + r[2][2]
    if tr > 0:
        w = math.sqrt(1 + tr) * 2
        q = [(r[2][1] - r[1][2]) / w, (r[0][2] - r[2][0]) / w, (r[1][0] - r[0][1]) / w, 0.25 * w]
    elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        w = math.sqrt(1 + r[0][0] - r[1][1] - r[2][2]) * 2
        q = [0.25 * w, (r[0][1] + r[1][0]) / w, (r[0][2] + r[2][0]) / w, (r[2][1] - r[1][2]) / w]
    elif r[1][1] > r[2][2]:
        w = math.sqrt(1 + r[1][1] - r[0][0] - r[2][2]) * 2
        q = [(r[0][1] + r[1][0]) / w, 0.25 * w, (r[1][2] + r[2][1]) / w, (r[0][2] - r[2][0]) / w]
    else:
        w = math.sqrt(1 + r[2][2] - r[0][0] - r[1][1]) * 2
        q = [(r[0][2] + r[2][0]) / w, (r[1][2] + r[2][1]) / w, 0.25 * w, (r[1][0] - r[0][1]) / w]
    n = math.sqrt(sum(x * x for x in q)) or 1.0
    return t, [x / n for x in q], s


def flip_q(q):
    return [-q[0], -q[1], q[2], q[3]]


def flip_v(v):
    return [v[0], v[1], -v[2]]


# ---------------------------------------------------------------- textures
def dds_to_png(src, tex_dir, cache):
    if src in cache:
        return cache[src]
    out = None
    if src and os.path.isfile(src):
        fd, tmp = tempfile.mkstemp(suffix=".png"); os.close(fd)
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, tmp], capture_output=True)
        if r.returncode == 0 and os.path.getsize(tmp) > 0:
            out = pathlib.Path(tmp).read_bytes()
        else:
            print("  ! texture failed", src, r.stderr.decode("utf-8", "replace")[:200])
        os.remove(tmp)
    elif src:
        print("  ! texture missing", src)
    cache[src] = out
    return out


TEX_ALIAS = {}                                     # per job: texture name in the mesh -> file name on disk (job "tex_alias")
FORCE_MASK = [False]                               # per job: alpha-tested leaves even when the shader name does not say so (job "alpha_mask")


def find_tex(name, dirs):
    if not name:
        return None
    name = TEX_ALIAS.get(name, name)
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


# ---------------------------------------------------------------- glb writer
class GLB:
    def __init__(self):
        self.bin = bytearray()
        self.hidden = set()                            # zero-scaled helper bones (singular inverse bind)
        self.j = {"asset": {"version": "2.0", "generator": "ai-commander-arena pdx2glb.py"}, "scenes": [{"nodes": []}], "scene": 0,
                  "nodes": [], "meshes": [], "accessors": [], "bufferViews": [], "buffers": [], "materials": [],
                  "textures": [], "images": [], "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
                  "skins": [], "animations": []}

    def view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        off = len(self.bin); self.bin += data
        v = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target:
            v["target"] = target
        self.j["bufferViews"].append(v)
        return len(self.j["bufferViews"]) - 1

    def acc(self, fmt, comps, vals, typ, target=None, minmax=False, normalized=False):
        n = len(vals) // comps
        data = struct.pack(f"<{len(vals)}{fmt}", *vals)
        ct = {"f": 5126, "H": 5123, "I": 5125, "B": 5121}[fmt]
        a = {"bufferView": self.view(data, target), "componentType": ct, "count": n, "type": typ}
        if normalized:
            a["normalized"] = True
        if minmax:
            a["min"] = [min(vals[i::comps]) for i in range(comps)]
            a["max"] = [max(vals[i::comps]) for i in range(comps)]
        self.j["accessors"].append(a)
        return len(self.j["accessors"]) - 1

    def image(self, png):
        self.j["images"].append({"bufferView": self.view(png), "mimeType": "image/png"})
        self.j["textures"].append({"sampler": 0, "source": len(self.j["images"]) - 1})
        return len(self.j["textures"]) - 1

    def node(self, d):
        self.j["nodes"].append(d)
        return len(self.j["nodes"]) - 1

    def write(self, path):
        j = {k: v for k, v in self.j.items() if v != []}
        j["buffers"] = [{"byteLength": len(self.bin)}]
        js = json.dumps(j, separators=(",", ":")).encode("utf-8")
        while len(js) % 4:
            js += b" "
        while len(self.bin) % 4:
            self.bin.append(0)
        total = 12 + 8 + len(js) + 8 + len(self.bin)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, total))
            f.write(struct.pack("<II", len(js), 0x4E4F534A)); f.write(js)
            f.write(struct.pack("<II", len(self.bin), 0x004E4942)); f.write(self.bin)


# ---------------------------------------------------------------- conversion
def collect(mesh_root):
    """-> shapes: [(shape name, [mesh nodes])], bones: {name: {ix, pa, tx}} (ordered by ix), locators: [node]"""
    shapes, bones, locs = [], {}, []
    obj = mesh_root.get("object")
    for sh in (obj.kids if obj else []):
        ms = [k for k in sh.kids if k.name == "mesh"]
        if ms and not (sh.name.lower().startswith("lodshape") and sh.name[8:] not in ("", "0")):
            shapes.append((sh.name, ms))           # map scenery ships lodShape0..4: keep the full-detail one
        sk = sh.get("skeleton")
        if sk:
            for bn in sk.kids:
                if bn.name not in bones:
                    bones[bn.name] = {"ix": bn.props["ix"][0], "pa": bn.props.get("pa", [-1])[0], "tx": bn.props["tx"]}
    loc = mesh_root.get("locator")
    if loc:
        locs = loc.kids
    return shapes, dict(sorted(bones.items(), key=lambda kv: kv[1]["ix"])), locs


def add_model(g, mesh_path, tex_dirs, tex_cache, mat_cache, parent_node=None, bone_nodes=None, prefix=""):
    """Add one .mesh's shapes (and skeleton, if it has one) to g. Returns (mesh node ids, bone name -> node id, skin id)."""
    root = parse_pdx(mesh_path)
    shapes, bones, locs = collect(root)
    bnode, skin = {}, None
    if bones:
        by_ix = {b["ix"]: name for name, b in bones.items()}
        world, hidden = {}, set()
        for name, b in bones.items():                      # bind world (glTF space) = flip(inverse(inverse bind))
            try:
                world[name] = flip_mat(mat_inv(mat_from_tx(b["tx"])))
            except ValueError:                             # zero-scaled helper bone: keep its parent's frame
                pa = by_ix.get(b["pa"]) if b["pa"] >= 0 else None
                world[name] = list(world[pa]) if pa in world else [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
                hidden.add(name)                           # its vertices are collapsed in game: keep them hidden
        for name, b in bones.items():
            pa = by_ix.get(b["pa"]) if b["pa"] >= 0 else None
            local = mat_mul(mat_inv(world[pa]), world[name]) if pa else world[name]
            t, q, s = decompose(local)
            bnode[name] = g.node({"name": prefix + name, "translation": t, "rotation": q, "scale": [0, 0, 0] if name in hidden else s, "children": []})
            if name in hidden:
                g.hidden.add(prefix + name)
        roots = []
        for name, b in bones.items():
            pa = by_ix.get(b["pa"]) if b["pa"] >= 0 else None
            if pa:
                g.j["nodes"][bnode[pa]]["children"].append(bnode[name])
            else:
                roots.append(bnode[name])
        ibm = []
        for name in bones:
            ibm += mat_inv(world[name])
        joints = [bnode[n] for n in bones]
        g.j["skins"].append({"joints": joints, "inverseBindMatrices": g.acc("f", 16, ibm, "MAT4"), "skeleton": roots[0] if roots else joints[0]})
        skin = len(g.j["skins"]) - 1
        ix_to_joint = {b["ix"]: k for k, b in enumerate(bones.values())}
    mesh_nodes = []
    for shape_name, ms in shapes:
        prims = []
        for m in ms:
            mt = m.get("material")
            if mt and mt.props.get("shader", [""])[0].lower() in ("collision", "pdxmeshcollision"):
                continue                                   # pick / collision boxes are not drawn in game
            if "p" not in m.props or "tri" not in m.props:
                continue                                   # empty placeholder meshes (some mods ship them)
            P, N, U, TRI = m.props["p"], m.props.get("n"), m.props.get("u0"), m.props["tri"]
            nv = len(P) // 3
            pos = []
            for i in range(nv):
                pos += [P[i * 3], P[i * 3 + 1], -P[i * 3 + 2]]
            attrs = {"POSITION": g.acc("f", 3, pos, "VEC3", 34962, minmax=True)}
            if N:
                nn = []
                for i in range(nv):
                    nn += [N[i * 3], N[i * 3 + 1], -N[i * 3 + 2]]
                attrs["NORMAL"] = g.acc("f", 3, nn, "VEC3", 34962)
            if U:
                attrs["TEXCOORD_0"] = g.acc("f", 2, list(U), "VEC2", 34962)
            sk = m.get("skin")
            if sk and skin is not None:
                per = len(sk.props["ix"]) // nv            # slots per vertex (always 4; "bones" = slots in use)
                IX, Wt = sk.props["ix"], sk.props["w"]
                js, ws = [], []
                for i in range(nv):
                    jj, ww = [0, 0, 0, 0], [0.0, 0.0, 0.0, 0.0]
                    for k in range(min(per, 4)):
                        bi, w = IX[i * per + k], Wt[i * per + k]
                        if bi >= 0 and w > 0:
                            jj[k] = ix_to_joint.get(bi, 0); ww[k] = w
                    sw = sum(ww)
                    if sw <= 0:
                        ww = [1.0, 0, 0, 0]
                    else:
                        ww = [w / sw for w in ww]
                    js += jj; ws += ww
                attrs["JOINTS_0"] = g.acc("H", 4, js, "VEC4", 34962)
                attrs["WEIGHTS_0"] = g.acc("f", 4, ws, "VEC4", 34962)
            idx = []
            for i in range(0, len(TRI), 3):
                idx += [TRI[i], TRI[i + 2], TRI[i + 1]]
            prim = {"attributes": attrs, "indices": g.acc("I" if nv > 65535 else "H", 1, idx, "SCALAR", 34963)}
            mat = m.get("material")
            if mat:
                key = (mat.props.get("diff", [""])[0], mat.props.get("n", [""])[0], mat.props.get("shader", [""])[0], "" if mat.props.get("diff", [""])[0] else mesh_path)
                if key not in mat_cache:
                    md = {"name": os.path.splitext(key[0])[0] or "pdx", "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 0.78},
                          "extras": {"shader": key[2]}}
                    png = dds_to_png(find_tex(key[0], tex_dirs), None, tex_cache) if key[0] else None
                    if not png:                            # no texture named, or the entity's meshsettings may swap the texture: try <mesh>_diffuse.dds
                        alt = pathlib.Path(mesh_path).stem + "_diffuse.dds"
                        png = dds_to_png(find_tex(alt, tex_dirs), None, tex_cache)
                        if png:
                            print("  . using", alt, "for", key[0])
                    if key[0] and not png:
                        print("  ! diffuse not found:", key[0])
                    if png:
                        md["pbrMetallicRoughness"]["baseColorTexture"] = {"index": g.image(png)}
                    else:                                  # untextured (e.g. a weapon whose texture the entity supplies): gunmetal
                        md["pbrMetallicRoughness"]["baseColorFactor"] = [0.2, 0.19, 0.17, 1.0]
                    if key[1]:
                        md["extras"]["normalTexture"] = key[1]   # PDX normal maps are swizzled; kept out of the shading
                    if "alpha" in key[2].lower() or FORCE_MASK[0]:
                        md["alphaMode"] = "MASK"; md["alphaCutoff"] = 0.5
                    if "alpha" in key[2].lower() or "doublesided" in key[2].lower():
                        md["doubleSided"] = True
                    g.j["materials"].append(md); mat_cache[key] = len(g.j["materials"]) - 1
                prim["material"] = mat_cache[key]
            prims.append(prim)
        prims = [p for p in prims if not p.pop("_skip", False)]
        if not prims:
            continue
        g.j["meshes"].append({"name": prefix + shape_name, "primitives": prims})
        nd = {"name": prefix + shape_name, "mesh": len(g.j["meshes"]) - 1}
        if skin is not None and any("JOINTS_0" in p["attributes"] for p in prims):
            nd["skin"] = skin
        mesh_nodes.append(g.node(nd))
    # locators: children of their parent bone (bone-local p / q), else of the model root
    loc_nodes = {}
    for L in locs:
        p = L.props.get("p", [0, 0, 0]); q = L.props.get("q", [0, 0, 0, 1])
        nid = g.node({"name": prefix + "loc:" + L.name, "translation": flip_v(p), "rotation": flip_q(q)})
        pa = L.props.get("pa", [None])[0]
        if pa and pa in bnode:
            g.j["nodes"][bnode[pa]]["children"].append(nid)
        else:
            mesh_nodes.append(nid)
        loc_nodes[L.name] = nid
    bone_roots = [bnode[n] for n, b in bones.items() if b["pa"] < 0 or b["pa"] not in {bb["ix"] for bb in bones.values()}]
    return mesh_nodes, bnode, bone_roots, loc_nodes


def add_anim(g, anim_path, name, bnode, rest):
    root = parse_pdx(anim_path)
    info = root.get("info")
    fps, sa = info.props["fps"][0], info.props["sa"][0]
    samples = root.get("samples")
    S = {k: samples.props.get(k, []) if samples else [] for k in ("t", "q", "s")}
    cur = {"t": 0, "q": 0, "s": 0}
    bones = info.kids
    tracks = {b.name: {"t": [], "q": [], "s": []} for b in bones}
    for f in range(sa):
        for b in bones:
            chans = b.props.get("sa", [""])[0]
            for ch, w in (("t", 3), ("q", 4), ("s", 1)):
                if ch in chans:
                    tracks[b.name][ch].append(S[ch][cur[ch]:cur[ch] + w]); cur[ch] += w
    used = {k: len(v) for k, v in S.items()}
    if any(cur[k] != used[k] for k in cur):
        print(f"  ! {anim_path}: sample count mismatch read {cur} of {used}")
    times = [f / fps for f in range(sa)]
    t_acc = g.acc("f", 1, times, "SCALAR", minmax=True)
    t0 = g.acc("f", 1, [0.0], "SCALAR", minmax=True)
    channels, samplers = [], []
    for b in bones:
        if b.name not in bnode or b.name in rest or b.name in g.hidden:       # skeleton roots keep the bind pose: the game's clips turn the
            continue                                     # whole unit 45 degrees on the root for its map view
        nid = bnode[b.name]
        for ch, path in (("t", "translation"), ("q", "rotation"), ("s", "scale")):
            keys = tracks[b.name][ch]
            if keys:
                if ch == "t":
                    vals = [x for k in keys for x in flip_v(k)]
                elif ch == "q":
                    vals = [x for k in keys for x in flip_q(k)]
                else:
                    vals = [x for k in keys for x in (k[0], k[0], k[0])]
                inp = t_acc
            else:
                init = b.props.get(ch)
                if not init:
                    continue
                vals = flip_v(init) if ch == "t" else flip_q(init) if ch == "q" else [init[0]] * 3
                inp = t0
            out = g.acc("f", 4 if ch == "q" else 3, vals, "VEC4" if ch == "q" else "VEC3")
            samplers.append({"input": inp, "output": out, "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1, "target": {"node": nid, "path": path}})
    if channels:
        g.j["animations"].append({"name": name, "channels": channels, "samplers": samplers})
    return {"name": name, "fps": fps, "frames": sa, "duration": round((sa - 1) / fps, 3) if sa > 1 else 0}


def convert(job, tex_dirs_default=()):
    g = GLB()
    mesh = job["mesh"]
    tex_dirs = [os.path.dirname(mesh)] + list(job.get("tex_dirs", [])) + list(tex_dirs_default)
    tex_cache, mat_cache = {}, {}
    TEX_ALIAS.clear(); TEX_ALIAS.update(job.get("tex_alias", {})); FORCE_MASK[0] = bool(job.get("alpha_mask"))
    mesh_nodes, bnode, bone_roots, locs = add_model(g, mesh, tex_dirs, tex_cache, mat_cache)
    root_names = {g.j["nodes"][n]["name"] for n in bone_roots}
    top = g.node({"name": job.get("name", pathlib.Path(mesh).stem), "children": bone_roots + mesh_nodes})
    g.j["scenes"][0]["nodes"].append(top)
    report = {"name": job.get("name"), "mesh": mesh, "bones": list(bnode), "locators": list(locs), "anims": [], "attach": []}
    # attachments (weapons): a second mesh parented to a locator (or bone) of the first
    for spec in job.get("attach", []):
        at, wmesh = spec["at"], spec["mesh"]
        parent = locs.get(at) if at in locs else bnode.get(at)
        if parent is None:
            print(f"  ! attach point {at} not found in {mesh}")
            continue
        wn, wb, wroots, wlocs = add_model(g, wmesh, [os.path.dirname(wmesh)] + tex_dirs, tex_cache, mat_cache, prefix="att:")
        grp = g.node({"name": "attach:" + at, "children": wroots + wn})
        g.j["nodes"][parent].setdefault("children", []).append(grp)
        report["attach"].append({"at": at, "mesh": wmesh})
    for aid, apath in job.get("anims", {}).items():
        report["anims"].append(add_anim(g, apath, aid, bnode, root_names))
    out = pathlib.Path(job["out"]); out.parent.mkdir(parents=True, exist_ok=True)
    g.write(out)
    report["out"] = str(out); report["bytes"] = out.stat().st_size
    print(f"{out.name}: {len(g.j['meshes'])} meshes, {len(bnode)} bones, {len(report['anims'])} anims, {report['bytes']} bytes")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump")
    ap.add_argument("--mesh")
    ap.add_argument("--anim", action="append", default=[], help="id=<file.anim>")
    ap.add_argument("--attach", action="append", default=[], help="<locator or bone>=<file.mesh>")
    ap.add_argument("--tex-dir", action="append", default=[])
    ap.add_argument("--out")
    ap.add_argument("--batch", help="jobs JSON: {root, out_dir, jobs: [{name, mesh, anims{id: file}, attach[{at, mesh}]}]}")
    ap.add_argument("--only", default="", help="with --batch: comma-separated job names")
    a = ap.parse_args()
    if a.dump:
        dump(parse_pdx(a.dump)); return
    if a.batch:
        spec = json.loads(pathlib.Path(a.batch).read_text(encoding="utf-8"))
        root, out_dir = spec["root"], spec["out_dir"]
        only = set(x for x in a.only.split(",") if x)
        reports = []
        rp = lambda p: p if os.path.isabs(p) else os.path.join(root, p)
        for jb in spec["jobs"]:
            if only and jb["name"] not in only:
                continue
            job = {"name": jb["name"], "mesh": rp(jb["mesh"]), "out": os.path.join(out_dir, jb["name"] + ".glb"),
                   "anims": {k: rp(v) for k, v in jb.get("anims", {}).items()},
                   "attach": [{"at": x["at"], "mesh": rp(x["mesh"])} for x in jb.get("attach", [])],
                   "tex_dirs": [rp(x) for x in jb.get("tex_dirs", [])] + [root, os.path.join(root, "tanks")],
                   "tex_alias": jb.get("tex_alias", {}), "alpha_mask": jb.get("alpha_mask", False)}
            r = convert(job); r.update({k: jb[k] for k in ("what", "class") if k in jb}); reports.append(r)
        man = pathlib.Path(out_dir) / ("manifest.json" if not only else "manifest-partial.json")
        man.write_text(json.dumps(reports, ensure_ascii=False, indent=1), encoding="utf-8")
        print("manifest", man)
        return
    job = {"mesh": a.mesh, "out": a.out, "anims": dict(x.split("=", 1) for x in a.anim),
           "attach": [{"at": x.split("=", 1)[0], "mesh": x.split("=", 1)[1]} for x in a.attach], "tex_dirs": a.tex_dir}
    print(json.dumps(convert(job), indent=1))


if __name__ == "__main__":
    main()
