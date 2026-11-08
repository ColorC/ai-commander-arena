// Open-licensed model kit for the sand table (v2). One glTF per class, loaded as data from the models folder
// ($ARENA_DATA/video/models, authors / sources / licences in credits.md there; nothing in it is executed).
// Each model is normalised once at load: forward = +z, metres, ground at y = 0, centred on x/z. Static vehicles are baked
// into vertex-coloured geometry (one draw call per part), split into hull / turret (turret pivots about its own centre,
// barrel included) and recoloured per side (paint colours pulled towards a side camouflage). The soldier is a skinned
// glTF with its own clips (idle / walk / run / shoot / death), cloned per figure; a rifle is attached to the right hand.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';

// yaw: rotation (rad, about +y) that turns the model's nose to +z; len: real length (m) the model's z extent is scaled to;
// turret: triangles whose centroid (normalised metres) has y > y0 and |x| < hx and z0 < z < z1 rotate with the turret;
// pivot: turret pivot [x, y, z]; muzzle: barrel tip in normalised metres (turret at rest); rotor: helicopter blades (y > y0).
export const KIT_DEF = {
  tankB: { file: 'polypizza/7GG1xDtc8l.glb', what: 'MBT (Leopard / Chieftain-like)', yaw: 0, len: 9.7,
    turret: { y0: 1.8, hx: 2.0, z0: -4.3, z1: 9 }, pivot: [0, 1.8, -0.9], muzzle: [0, 2.25, 4.85] },
  tankR: { file: 'polypizza/egcLMSGiuA.glb', what: 'MBT (T-72-like)', yaw: Math.PI / 2, len: 9.5,
    turret: { y0: 1.65, hx: 1.6, z0: -4.6, z1: 9 }, pivot: [0, 1.7, -2.3], muzzle: [0, 1.95, 4.75] },
  ifv: { file: 'polypizza/1jJ50vLGCk.glb', what: 'IFV / APC (BMP / Warrior-like)', yaw: -Math.PI / 2, len: 6.8,
    turret: { y0: 2.2, hx: 1.1, z0: -2.3, z1: 9 }, pivot: [0, 2.25, -0.75], muzzle: [0, 2.65, 3.4] },
  spg: { file: 'polypizza/S1jUTRmAjD.glb', what: 'self-propelled gun', yaw: 0, len: 6.6, muzzle: [0, 2.15, 3.15] },
  jeep: { file: 'polypizza/AcSdGGrgYP.glb', what: 'light utility vehicle (Land Rover / UAZ-like)', yaw: 0, len: 4.4 },
  truck: { file: 'polypizza/3XAD5t0Djqv.glb', what: 'military truck', yaw: 0, len: 7.2 },
  gun: { file: 'polypizza/b4woGhCexLm.glb', what: 'towed gun (D-30-like stand-in)', yaw: Math.PI / 2, len: 6.0, muzzle: [0, 2.2, 3.0], repaint: true },
  heli: { file: 'polypizza/hG2Qr0A3zR.glb', what: 'helicopter', yaw: 0, len: 17.0, rotor: { y0: 4.3 } },
  rifle: { file: 'polypizza/K2lXTYFSLC.glb', what: 'assault rifle', yaw: -Math.PI / 2, len: 0.95, keep: true },
  soldier: { file: 'polypizza/Btfn3G5Xv4.glb', what: 'soldier (animated)', skinned: true, height: 1.8,
    clips: { idle: 'Idle_Gun', walk: 'Walk', run: 'Run', shoot: 'Idle_Gun_Shoot', runShoot: 'Run_Shoot', death: 'Death', hit: 'HitRecieve' } },
};
// side camouflage (blue = NATO, red = Pact) and identification colours
const CAMO = [new THREE.Color('#56603f'), new THREE.Color('#6b6a43')];
const SIDE = [new THREE.Color('#3f74c4'), new THREE.Color('#c0453b')];
const UNIFORM = [new THREE.Color('#5f6b45'), new THREE.Color('#857a52')];

const loader = new GLTFLoader();
export const KIT = {};

function lum(c) { return 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b; }
function paint(c, side, force) {                   // pull painted (mid-luminance, coloured) surfaces towards the side camo
  const hsl = {}; c.getHSL(hsl);
  const l = lum(c);
  if (force) return c.clone().multiplyScalar(0.35).lerp(CAMO[side], 0.75);
  if (l < 0.03 || (hsl.s < 0.12 && l > 0.5)) return c.clone();            // black parts, glass / chrome
  const out = c.clone().lerp(CAMO[side], hsl.s > 0.25 ? 0.72 : 0.45);
  return out;
}

// bake one static glTF into normalised, split, vertex-coloured geometries: { parts: {hull, turret, rotor}, size }
function bake(def, gltf) {
  const root = gltf.scene; root.updateMatrixWorld(true);
  const rot = new THREE.Matrix4().makeRotationY(def.yaw || 0);
  const tris = [];                                  // [ax, ay, az, bx, ..., colour]
  root.traverse(o => {
    if (!o.isMesh) return;
    const g = o.geometry.index ? o.geometry.toNonIndexed() : o.geometry.clone();
    g.applyMatrix4(new THREE.Matrix4().multiplyMatrices(rot, o.matrixWorld));
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    const groups = g.groups.length ? g.groups : [{ start: 0, count: g.attributes.position.count, materialIndex: 0 }];
    const p = g.attributes.position.array;
    for (const gr of groups) {
      const m = mats[gr.materialIndex] || mats[0], col = (m.color || new THREE.Color(1, 1, 1)).clone();
      for (let v = gr.start; v < gr.start + gr.count; v += 3) tris.push([p[v * 3], p[v * 3 + 1], p[v * 3 + 2], p[v * 3 + 3], p[v * 3 + 4], p[v * 3 + 5], p[v * 3 + 6], p[v * 3 + 7], p[v * 3 + 8], col]);
    }
  });
  const bb = new THREE.Box3();
  for (const t of tris) for (let k = 0; k < 9; k += 3) bb.expandByPoint(new THREE.Vector3(t[k], t[k + 1], t[k + 2]));
  const sz = bb.getSize(new THREE.Vector3()), s = def.len / sz.z, cx = (bb.min.x + bb.max.x) / 2, cz = (bb.min.z + bb.max.z) / 2;
  for (const t of tris) for (let k = 0; k < 9; k += 3) { t[k] = (t[k] - cx) * s; t[k + 1] = (t[k + 1] - bb.min.y) * s; t[k + 2] = (t[k + 2] - cz) * s; }
  const dbg = {}; for (const t of tris) { const y = (t[1] + t[4] + t[7]) / 3, z = (t[2] + t[5] + t[8]) / 3, x = (t[0] + t[3] + t[6]) / 3, b = Math.floor(y / 0.25) * 0.25; const d = dbg[b] || (dbg[b] = [0, 99, -99, 0]); d[0]++; d[1] = Math.min(d[1], z); d[2] = Math.max(d[2], z); d[3] = Math.max(d[3], Math.abs(x)); }
  const T = def.turret, P = def.pivot || [0, 0, 0], R = def.rotor;
  const part = t => {
    const x = (t[0] + t[3] + t[6]) / 3, y = (t[1] + t[4] + t[7]) / 3, z = (t[2] + t[5] + t[8]) / 3;
    if (T && y > T.y0 && Math.abs(x) < T.hx && z > T.z0 && z < T.z1) return 'turret';
    if (R && y > R.y0) return 'rotor';
    return 'hull';
  };
  const parts = {};
  for (const name of ['hull', 'turret', 'rotor']) {
    const sel = tris.filter(t => part(t) === name); if (!sel.length) continue;
    const off = name === 'turret' ? P : [0, 0, 0];
    const pos = new Float32Array(sel.length * 9);
    sel.forEach((t, i) => { for (let k = 0; k < 9; k += 3) { pos[i * 9 + k] = t[k] - off[0]; pos[i * 9 + k + 1] = t[k + 1] - off[1]; pos[i * 9 + k + 2] = t[k + 2] - off[2]; } });
    const cols = [0, 1].map(side => { const c = new Float32Array(sel.length * 9); sel.forEach((t, i) => { const pc = def.keep ? t[9] : paint(t[9], side, def.repaint); for (let k = 0; k < 9; k += 3) { c[i * 9 + k] = pc.r; c[i * 9 + k + 1] = pc.g; c[i * 9 + k + 2] = pc.b; } }); return c; });
    const geos = cols.map(c => { const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos, 3)); g.setAttribute('color', new THREE.BufferAttribute(c, 3)); g.computeVertexNormals(); g.computeBoundingSphere(); return g; });
    parts[name] = { geos, off };
  }
  return { parts, size: [sz.x * s, sz.y * s, def.len], scale: s, dbg };
}

export async function loadKit(base) {
  for (const [key, def] of Object.entries(KIT_DEF)) {
    const gltf = await loader.loadAsync(base + def.file);
    if (def.skinned) {
      const root = gltf.scene; root.updateMatrixWorld(true);
      const bb = new THREE.Box3().setFromObject(root), s = def.height / (bb.max.y - bb.min.y);
      const clips = {};
      for (const [k, n] of Object.entries(def.clips)) clips[k] = gltf.animations.find(a => a.name.endsWith('|' + n) || a.name === n);
      // materials per side: main fabric -> uniform colour, black kit (vest, helmet) -> side colour
      const mats = [0, 1].map(side => {
        const map = new Map();
        root.traverse(o => { if (!o.isMesh) return; const m = o.material; if (map.has(m.name)) return; const c = m.clone(); c.skinning = true;
          if (/^Swat$/.test(m.name)) c.color.copy(UNIFORM[side]);
          else if (/Black/.test(m.name)) c.color.copy(SIDE[side]).multiplyScalar(0.72);
          c.roughness = 0.8; c.metalness = 0; map.set(m.name, c); });
        return map;
      });
      KIT[key] = { def, scene: root, scale: s, clips, mats };
    } else {
      KIT[key] = { def, ...bake(def, gltf) };
    }
  }
  return Object.fromEntries(Object.entries(KIT).map(([k, v]) => [k, { size: v.size, file: v.def.file }]));
}

// materials shared by all static vehicles
let VMAT = null;
function vmat() {
  if (!VMAT) VMAT = {
    paint: new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.75, metalness: 0.08 }),
    wreck: new THREE.MeshStandardMaterial({ color: 0x2a2724, roughness: 1, metalness: 0 }),
    side: [0, 1].map(s => new THREE.MeshStandardMaterial({ color: SIDE[s], roughness: 0.5, emissive: SIDE[s], emissiveIntensity: 0.25 })),
  };
  return VMAT;
}
// a static vehicle: { root, body (tilt / bob), turret (yaw), gun (recoil), muzzle (Object3D at the barrel tip), rotor }
export function makeVehicle(key, side, wreck = false) {
  const K = KIT[key], M = vmat(), root = new THREE.Group(), body = new THREE.Group(); root.add(body);
  const mesh = (part) => { const m = new THREE.Mesh(K.parts[part].geos[side], wreck ? M.wreck : M.paint); m.castShadow = true; m.receiveShadow = true; return m; };
  body.add(mesh('hull'));
  const o = { key, root, body, turret: null, gun: null, muzzle: new THREE.Object3D(), rotor: null };
  if (K.parts.turret) {
    const t = new THREE.Group(), gun = new THREE.Group(); t.position.fromArray(K.parts.turret.off); t.add(gun); gun.add(mesh('turret')); body.add(t);
    o.turret = t; o.gun = gun;
    if (K.def.muzzle) { o.muzzle.position.set(K.def.muzzle[0] - K.parts.turret.off[0], K.def.muzzle[1] - K.parts.turret.off[1], K.def.muzzle[2] - K.parts.turret.off[2]); gun.add(o.muzzle); }
  } else {
    o.gun = body;
    if (K.def.muzzle) { o.muzzle.position.fromArray(K.def.muzzle); body.add(o.muzzle); }
    else { o.muzzle.position.set(0, K.size[1] * 0.8, K.size[2] * 0.3); body.add(o.muzzle); }
  }
  if (K.parts.rotor) { const r = mesh('rotor'); body.add(r); o.rotor = r; }
  if (!wreck && key !== 'heli') {                   // side identification panel on the roof
    const pn = new THREE.Mesh(new THREE.BoxGeometry(K.size[0] * 0.42, 0.12, Math.min(1.4, K.size[2] * 0.18)), M.side[side]);
    const top = (o.turret ? o.turret : body); pn.position.set(0, o.turret ? K.size[1] - K.parts.turret.off[1] + 0.02 : K.size[1] + 0.02, o.turret ? -0.6 : -K.size[2] * 0.15);
    if (key === 'gun') pn.position.set(0, 0.9, -1.8);
    top.add(pn);
  }
  return o;
}

// an animated soldier: { root, mixer, act(name, t) }
export const RIFLE = { pos: [0, 0.08, 0.02], rot: [1.57, 0, 0] };   // grip offset (m) and rotation in the right-hand bone
export function makeSoldier(side) {
  const K = KIT.soldier, inner = SkeletonUtils.clone(K.scene), root = new THREE.Group();
  inner.scale.multiplyScalar(K.scale); root.add(inner);
  inner.traverse(o => { if (o.isMesh) { o.material = K.mats[side].get(o.material.name) || o.material; o.castShadow = true; o.receiveShadow = false; o.frustumCulled = false; } });
  let hand = null; inner.traverse(o => { if (o.name === 'WristR' || o.name === 'Wrist.R') hand = o; });
  if (hand && KIT.rifle) {                          // rifle in the right hand (bone space: undo the figure's scale)
    const r = new THREE.Mesh(KIT.rifle.parts.hull.geos[0], vmat().paint); r.castShadow = true;
    const wsc = new THREE.Vector3(); inner.updateMatrixWorld(true); hand.getWorldScale(wsc);
    r.scale.setScalar(1 / wsc.x); r.position.fromArray(RIFLE.pos).multiplyScalar(1 / wsc.x);
    r.rotation.fromArray(RIFLE.rot); hand.add(r); root.userData.rifle = r;
  }
  const mixer = new THREE.AnimationMixer(inner), actions = {};
  for (const [k, clip] of Object.entries(K.clips)) if (clip) actions[k] = mixer.clipAction(clip);
  let cur = null;
  // deterministic: clip `name` at local time t (s); `once` clips clamp at their end
  const act = (name, t) => {
    const a = actions[name] || actions.idle;
    if (cur !== a) { if (cur) cur.stop(); a.reset(); a.play(); if (name === 'death') { a.setLoop(THREE.LoopOnce, 1); a.clampWhenFinished = true; } cur = a; }
    const d = a.getClip().duration;
    mixer.setTime(name === 'death' ? Math.min(t, d - 0.001) : t % d);
  };
  return { root, inner, mixer, act, duration: n => (actions[n] || actions.idle).getClip().duration };
}
