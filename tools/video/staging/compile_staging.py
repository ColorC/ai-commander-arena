"""Staging compiler: a staging file (STANDARD.md) + the commentary lines -> make_segment beats -> shot module and timeline
that render.py takes as they are.

    python -I -X utf8 compile_staging.py <case | staging json> [seg ...] [--keys] [--out <dir>]

- <case> reads $ARENA_DATA/video/program/staging/<case>.json; a path reads that file (its "case" names the game).
- Per segment: <out>/<case>-<seg>.spec.json (make_segment spec), <out>/shot-<case>-<seg>.js (shot module),
  <out>/<case>-<seg>-timeline.md. Default <out>: $ARENA_DATA/video/program/staging/out.
- --keys: one key frame per shot, 1 s into its last line (render.py --stills-only renders just those).
Render:  python -I -X utf8 tools/video/render.py --data <manifest data> --shot <out>/shot-<case>-<seg>.js --out-dir <dir> [--stills-only]

One shot = one beat: its lines are the subtitles (reading time from make_segment, 260 characters a minute), `time.at` freezes
the clock there, `time.from/to` plays the battle from -> to over the lines. The camera frames `focus` at the shot's time
(units: where they are, over the whole from-to span) with the zoom's distance / pitch below and a slow push. Cues become
`stagingCues` (fpc-symbology.js draws them): unit codes -> unit indices, (col,row) -> hex numbers, HH:MM -> game seconds,
`on` -> the start of that line."""
import json
import math
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
VID = HERE.parent
sys.path.insert(0, str(VID))
import make_segment  # noqa: E402
import os

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[3] / "data")

P = ARENA_DATA / "video/program/staging"
A, HX = 0.5 / math.sqrt(3), 0.5
# zoom -> camera (km, degrees): distance range (fit grows it up to the max), pitch
ZOOM = {"close": dict(dist=5.0, max=8.0, pitch=42), "mid": dict(dist=10.5, max=16.0, pitch=50), "wide": dict(dist=22.0, max=30.0, pitch=60)}
NEAR_R = {"close": 1.2, "mid": 2.0}   # units within this many km of the focus get their names (when there is room)
SIDE = {"blue": 0, "red": 1, 0: 0, 1: 1}


def hex_xz(h):
    c, r = h // 100, h % 100
    return A * (1 + 1.5 * (c - 1)), HX * (r - 0.5 + (0.5 if c % 2 == 0 else 0))


def hexnum(s):
    if isinstance(s, int):
        return s
    if isinstance(s, list):
        return s[0] * 100 + s[1]
    m = re.fullmatch(r"\((\d+),\s*(\d+)\)", s.strip())
    return int(m.group(1)) * 100 + int(m.group(2))


def load_data(p):
    s = pathlib.Path(p).read_text(encoding="utf-8")
    return json.loads(s[s.index("{"):s.rindex("}") + 1])


def lines_of(md):
    segs, cur = {}, None
    for ln in md.splitlines():
        m = re.match(r"##\s*第\s*(\d+)\s*段", ln)
        if m:
            cur = int(m.group(1)); segs[cur] = []; continue
        m = re.match(r"^(Claude|GPT)[：:](.*)$", ln.strip())
        if cur is not None and m:
            segs[cur].append({"speaker": m.group(1), "text": m.group(2).strip()})
    return segs


class Game:
    def __init__(self, data_path):
        d = load_data(data_path)
        self.g = d["game"]
        self.cols, self.rows = d["terrain"]["cols"], d["terrain"]["rows"]
        self.h0 = self.g["h0"]
        self.idx = {u[0]: i for i, u in enumerate(self.g["u"])}
        self.map_w, self.map_h = A * (2 + 1.5 * (self.cols - 1)), HX * (self.rows + 0.5)

    def gsec(self, t):
        h, m, *s = (int(x) for x in str(t).split(":"))
        return ((h * 60 + m - self.h0) % 1440) * 60 + (s[0] if s else 0)

    def unit(self, code):
        if code not in self.idx:
            raise SystemExit(f"unknown unit code {code!r}")
        return self.idx[code]

    def hex_at(self, i, g):
        h = None
        for r in self.g["p"][i]:
            if r[0] > g:
                break
            if r[1]:
                h = r[1]
        return h


def focus_points(G, fo, g0, g1):
    if fo.get("view") == "whole":
        return [(0.0, 0.0), (G.map_w, G.map_h)]
    if "hex" in fo:
        return [hex_xz(hexnum(fo["hex"]))]
    if "hexes" in fo:
        return [hex_xz(hexnum(h)) for h in fo["hexes"]]
    pts = []
    for code in fo.get("units", []):
        i = G.unit(code)
        for g in sorted({g0, (g0 + g1) / 2, g1}):
            h = G.hex_at(i, g)
            if h:
                pts.append(hex_xz(h))
    return pts or [(G.map_w / 2, G.map_h / 2)]


def frame(pts):
    xs, zs = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2, max(xs) - min(xs) + 0.6, max(zs) - min(zs) + 0.6


def camera(G, fo, zoom, g0, g1):
    """[start, end] camera of a shot and its centre. v2 (11-07, "no meaningless slow push"): no push; a shot on units
    that move pans with them (start framed on where they are early in the shot, end on where they are late); the whole
    map fills the frame (no table edge)."""
    Z = ZOOM[zoom]
    whole = fo.get("view") == "whole"
    cx, cz, ex, ez = frame(focus_points(G, fo, g0, g1))
    # fov 30, 16:9: the view is about 0.95 d wide and 0.54 d / sin(pitch) deep at the target
    sp = math.sin(math.radians(Z["pitch"]))
    d = ex / 1.05 if whole else min(max(Z["dist"], ex / 0.62, ez / (0.36 / sp)), Z["max"])   # whole: fill the width
    d = round(d, 2)
    cam = lambda x, z: {"at": [round(x, 3), round(z + 0.04 * d, 3)], "dist": d, "pitch": Z["pitch"], "yaw": 0, "focus": 0.55, "blur": 0.6}
    if "units" in fo and g1 > g0:
        a = frame(focus_points(G, fo, g0, g0 + (g1 - g0) / 3))
        b = frame(focus_points(G, fo, g1 - (g1 - g0) / 3, g1))
        return cam(a[0], a[1]), cam(b[0], b[1]), (cx, cz)
    return cam(cx, cz), cam(cx, cz), (cx, cz)


AT_SPEED, AT_MIN, AT_MAX = 10, 20, 300       # a "moment" shot plays around that moment: game s per video s, span bounds


def est_dur(lns):
    return 0.6 * make_segment.PACE + sum(make_segment.line_dur(ln) + make_segment.LINE_GAP for ln in lns)


def plan_times(G, st, segs):
    """Game-time span of every shot (v2, 11-07 "no slides"): a `time.at` shot no longer freezes; it plays about AT_SPEED×
    around its moment, continuing from where the previous shot stopped when that is close, and never past the start of
    the next timed shot (so the clock never runs backwards at a cut). `from`/`to` shots keep their span."""
    shots = [(S["seg"], sh) for S in st["segments"] for sh in S["shots"]]
    end = G.g.get("T") or 10 ** 9
    # start_g: the intro film right before this game (C before M1, E0 before F) already played the battle up to there
    s0 = st.get("start_g", 0)
    out, cur = {}, (s0 or None)
    # the plans before the first shot (all "at" the start): the opening minutes play under them, so the viewer watches
    # the plans being carried out; shared out by the lines' length, ending 30 s before the first later moment talked about
    lead = []
    for seg, sh in shots:
        if "at" in sh["time"] and G.gsec(sh["time"]["at"]) == 0:
            lead.append((seg, sh))
        else:
            break
    if lead:
        later = next((G.gsec(sh["time"].get("from", sh["time"].get("at"))) for _, sh in shots[len(lead):]), 600)
        limit = max(s0, min(later - 30, 600))
        durs = [est_dur([segs[seg][i] for i in sh["lines"]]) for seg, sh in lead]
        acc = 0.0
        for (seg, sh), d in zip(lead, durs):
            out[sh["id"]] = (round(s0 + (limit - s0) * acc / sum(durs)), round(s0 + (limit - s0) * (acc + d) / sum(durs)))
            acc += d
        cur = limit
        shots = shots[len(lead):]
    for seg, sh in shots:
        tm = sh["time"]
        if "from" in tm:
            g0, g1 = G.gsec(tm["from"]), G.gsec(tm["to"])
            if cur is not None and g0 < cur < g1 - 30:   # already played into it: go on from there
                g0 = cur
        else:
            at = G.gsec(tm["at"])
            span = min(max(est_dur([segs[seg][i] for i in sh["lines"]]) * AT_SPEED, AT_MIN), AT_MAX)
            # the clock stays near the moment the lines talk about: from at most span/2 before it to 60 s after it
            g0 = cur if cur is not None and at - span / 2 <= cur <= at + 60 else max(0, at - span / 2)
            g1 = max(g0, min(g0 + span, at + 60, end))
        out[sh["id"]] = (round(g0), round(g1))
        cur = g1
    return out


def cue(G, q, beat_lines, shot_lines, g0, g1, timed):
    o = {"type": q["type"]}
    if "on" in q:
        o["line"] = shot_lines.index(q["on"])
    if q.get("units"):
        o["units"] = [G.unit(u) for u in q["units"]]
    for k in ("hexes",):
        if k in q:
            o[k] = [hexnum(h) for h in q[k]]
    for k in ("issued", "effective", "time"):
        if k in q:
            o[k] = G.gsec(q[k])
    if "side" in q:
        o["side"] = SIDE[q["side"]]
    if "view" in q:
        o["view"] = SIDE[q["view"]]
    for k in ("show", "mark", "label", "kind", "text", "zh"):
        if k in q:
            o[k] = q[k]
    if "window" in q:
        o["win"] = [G.gsec(x) for x in q["window"]]
    elif q["type"] == "losses" and timed:
        o["win"] = [g0, g1]
    return o


AIGC = ARENA_DATA.as_posix() + "/video/aigc/anime-v2/"
FL = ARENA_DATA.as_posix() + "/video/fpc-original/06-ui-panels/badges-flags/"
# the two sides' nations come from the manifest entry: "flags": [blue file, red file] under FL (the game's own flag
# bitmaps, e.g. "American__Flags__US Flag.png", "Soviet__Flags__ussr flag.png")


def commander(who, side):
    """Portrait card of a side's commander (the matches' own portraits: NATO uniform for blue, Pact for red)."""
    m = "kimi" if "Kimi" in who else "deepseek" if "DeepSeek" in who else None
    if not m:
        return None
    f = f"{m}_{'nato' if side == 'blue' else 'pact'}.png"
    return {"name": who, "img": AIGC + "hoi4-156x210/" + f, "large": AIGC + "originals/" + f}


def compile_segment(case, man, st, seg, G, lines, out, keys, plan):
    S = next(s for s in st["segments"] if s["seg"] == seg)
    beats = []
    for sh in S["shots"]:
        g0, g1 = plan[sh["id"]]
        timed = g1 > g0
        c0, c1, (cx, cz) = camera(G, sh["focus"], sh["zoom"], g0, g1)
        if seg == S["seg"] == st["segments"][0]["seg"] and sh is S["shots"][0]:
            # the game's first shot: a quick move in from the whole map (the viewer comes from the previous game or the
            # briefing on this map), no lines, then the shot as usual
            cw, _, _ = camera(G, {"view": "whole"}, "wide", g0, g0)
            beats.append({"id": sh["id"] + "-in", "dur": 1.6, "g0": g0, "g": g0, "cam": [cw, c0], "lines": [], "what": "推入第一个焦点",
                          "shot": "wide", "stage": {"zoom": "wide", "names": []}, "cues": []})
        bl = [lines[i] for i in sh["lines"]]
        stage = {"zoom": sh["zoom"], "names": [G.unit(u) for u in sh["focus"].get("units", [])]}
        if sh["zoom"] in NEAR_R:
            stage["near"] = {"x": round(cx, 3), "z": round(cz, 3), "r": NEAR_R[sh["zoom"]]}
        # a shot without lines is a fast-forward bridge (exercises skip quiet stretches, 11-07): its own "dur" seconds
        b = {"id": sh["id"], "dur": "auto" if bl else sh.get("dur", 3), "tail": 0.6 if bl else 0, "g0": g0, "g": g1, "cam": [c0, c1], "ease": "linear",
             "lines": bl, "stage": stage, "what": sh.get("shows", ""), "shot": sh["zoom"],
             "cues": [cue(G, q, bl, sh["lines"], g0, g1, timed) for q in sh.get("cues", [])]}
        if keys and not bl:
            b["keys"] = [round(sh.get("dur", 3) / 2, 2)]
        elif keys:
            rel = 0.6 * make_segment.PACE
            for ln in bl[:-1]:
                rel += make_segment.line_dur(ln) + make_segment.LINE_GAP
            b["keys"] = [round(rel + 1.0, 2)]
        beats.append(b)
    # per map, from the manifest entry: "base" the base shot module (skin, ground, models, unit names of that map),
    # "vp_mul" how the match scores the objectives (e.g. 3 when the objectives count x3), "flags" the sides' flags
    base = pathlib.Path(man["base"]).resolve().as_uri()
    sid = f"{case}-{seg:02d}"
    spec = {
        "segment": sid, "title": f"{man['title']} 第 {seg} 段（演出）", "id": f"staging-{sid}", "base": base,
        "out_shot": str(out / f"shot-{sid}.js"), "mp4": str(out / f"{sid}.mp4"), "timeline": str(out / f"{sid}-timeline.md"),
        "still_prefix": f"{sid}", "g0": beats[0]["g0"], "h0": f"{G.h0 // 60:02d}:{G.h0 % 60:02d}", "duration": "auto", "cams": {}, "beats": beats,
        "note": f"演出规范编译：{st.get('_path', '')} 第 {seg} 段，台本 {man['commentary']}，数据 {man['data']}。",
        "override": {
            "staging": True, "intro": None, "endCard": None, "moments": [], "steps": [], "panels": [], "orders": [], "captions": [],
            "labels": [], "focus": [], "fog": [], "given": [], "roles": [], "match": {"title": man["title"], "blue": man["blue"], "red": man["red"]},
            "commanders": {sd: commander(man[sd], sd) for sd in ("blue", "red") if commander(man[sd], sd)},
            "flags": [FL + f for f in man.get("flags", [])],
            "vpMul": man.get("vp_mul", 1),
            "symbology": {"data": man.get("fpc"), "counters": True, "contacts": False, "orders": False, "fire": False, "panels": False,
                          "markers": {"vp": True, "bridges": False, "craters": True, "wrecks": False},
                          "staging": {"commanders": [man["blue"], man["red"]]}},
        },
    }
    if man.get("clear_run"):                   # the base shot's own run / score fields do not belong to this game
        spec["override"]["run"] = None
    sp = out / f"{sid}.spec.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    make_segment.build(sp, 0)
    return sp


def main(argv):
    keys = "--keys" in argv
    out = pathlib.Path(argv[argv.index("--out") + 1]) if "--out" in argv else P / "out"
    args = [a for k, a in enumerate(argv) if not a.startswith("--") and (k == 0 or argv[k - 1] != "--out")]
    src = pathlib.Path(args[0]) if args[0].endswith(".json") else P / f"{args[0]}.json"
    st = json.loads(src.read_text(encoding="utf-8"))
    st["_path"] = str(src)
    man = {m["case"]: m for m in json.loads((P / "manifest.json").read_text(encoding="utf-8"))}[st["case"]]
    if not man.get("data"):
        raise SystemExit(f"no game data for {st['case']} in the manifest (build_staging_data.py)")
    G = Game(man["data"])
    segs = lines_of(pathlib.Path(man["commentary"]).read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    want = [int(a) for a in args[1:]] or [s["seg"] for s in st["segments"]]
    plan = plan_times(G, st, segs)
    for seg in want:
        compile_segment(st["case"], man, st, seg, G, segs[seg], out, keys, plan)


if __name__ == "__main__":
    main(sys.argv[1:])
