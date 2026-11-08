// HOI4 model kit for the sand table (v3). The glbs are converted from the game's own .mesh / .anim / .dds by
// tools/video/hoi4/pdx2glb.py (jobs: hoi4/hoi4_models.json) into $ARENA_DATA/video/hoi4-assets/glb and
// loaded as data (GLTFLoader); nothing in them is executed. Same API as models.js (loadKit / makeVehicle / makeSoldier),
// plus CLASS_MAP / CLASS_ZH so the sandbox picks per-side models (blue = ENG, red = SOV) for every class.
// Every model is skinned with the game's clips: idle / move / attack (vehicles; the attack clip starts with the shot and
// carries its recoil), idle / move / attack / support / charge / death (riflemen, rifle attached to the hand nodes like
// the game entity does). Clips are sampled deterministically with AnimationMixer.setTime. The skeleton roots keep the bind
// pose (the game turns whole units 45 degrees on the root for its map view). Turret aim: the `turret` bone is turned
// after the clip is applied; the muzzle is the `barrel_tip` bone (or the rifle's muzzle locator).
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';

// file: glb name; len: real length (m) the model's z extent is scaled to (soldiers: height); moveClip / fireClip: clips for
// moving / firing (null = keep idle); rotor: bone names spun by the renderer.
export const KIT_DEF = {
  tankB: { file: 'ENG_tank_heavy', what: 'HOI4 ENG heavy tank', len: 7.6 },
  tankR: { file: 'SOV_tank_heavy', what: 'HOI4 SOV heavy tank', len: 7.4 },
  tdB: { file: 'ENG_tank_medium', what: 'HOI4 ENG medium tank', len: 6.6 },
  tdR: { file: 'SOV_tank_medium', what: 'HOI4 SOV medium tank', len: 6.8 },
  recceB: { file: 'ENG_tank_light', what: 'HOI4 ENG light tank', len: 4.8 },
  recceR: { file: 'SOV_tank_light', what: 'HOI4 SOV light tank', len: 5.4 },
  ifvB: { file: 'ENG_mechanized_01', what: 'HOI4 ENG mechanized carrier', len: 5.2 },
  ifvR: { file: 'SOV_mechanized_01', what: 'HOI4 SOV mechanized halftrack', len: 6.6 },
  spgB: { file: 'generic_super_heavy_sp_howitzer', what: 'HOI4 SP howitzer', len: 8.0 },
  spgR: { file: 'SOV_heavy_sp_artillery_brigade_1', what: 'HOI4 SOV heavy SP artillery', len: 7.0 },
  gun: { file: 'generic_super_heavy_artillery', what: 'HOI4 heavy towed howitzer', len: 10.0, moveClip: null },
  hq: { file: 'command_tank', what: 'HOI4 command vehicle', len: 6.8 },
  truckB: { file: 'generic_motorized_01', what: 'HOI4 truck', len: 6.8, fireClip: null },
  truckR: { file: 'SOV_motorized_01', what: 'HOI4 SOV truck', len: 6.8, fireClip: null },
  heli: { file: 'generic_helicopter', what: 'HOI4 helicopter', len: 13.0, moveClip: null, fireClip: null, rotor: ['propeller', 'propeller_front'] },
  infB: { file: 'ENG_infantry', what: 'HOI4 ENG rifleman', height: 1.8, skinned: true },
  infR: { file: 'SOV_infantry', what: 'HOI4 SOV rifleman', height: 1.8, skinned: true },
};
// replay unit types -> kit classes (side suffix B / R is added when the kit has one)
export const CLASS_MAP = { Tank: 'tank', APC: 'ifv', 'SP AT': 'td', SPArty: 'spg', Arty: 'gun', AT: 'inf', Inf: 'inf', Eng: 'inf', Recce: 'recce', HQ: 'hq',
  Utility: 'truck', Depot: 'truck', WLR: 'truck', AD: 'truck', Helo: 'heli' };
export const CLASS_ZH = { tank: '重型坦克', td: '中型坦克', recce: '轻型坦克', ifv: '机械化车辆', spg: '自行火炮', gun: '重型榴弹炮', hq: '指挥车',
  truck: '摩托化卡车', heli: '直升机', inf: '步兵' };
export const SOURCE = 'HOI4';

const loader = new GLTFLoader();
export const KIT = {};

function prep(def, gltf) {
  const root = gltf.scene; root.updateMatrixWorld(true);
  // model extent from the bind-pose geometry of the main meshes (attachments excluded)
  const bb = new THREE.Box3();
  root.traverse(o => { if (o.isMesh && !/^att:/.test(o.name)) { o.geometry.computeBoundingBox(); bb.union(o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld)); } });
  const sz = bb.getSize(new THREE.Vector3());
  const s = def.skinned ? def.height / sz.y : def.len / sz.z;
  const off = [-(bb.min.x + bb.max.x) / 2, -bb.min.y, -(bb.min.z + bb.max.z) / 2];
  const seen = new Set();
  root.traverse(o => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = !def.skinned; o.frustumCulled = false; if (seen.has(o.material)) return; seen.add(o.material);
    const m = o.material; if (m) { m.roughness = 0.82; m.metalness = 0.05; m.color.multiplyScalar(def.bright ?? (def.skinned ? 1.7 : 1.35)); if (def.tint) m.color.multiply(new THREE.Color(...def.tint)); if (m.map) m.map.anisotropy = 4; } } });   // the game's textures are graded for its own lighting: lift them a little
  const clips = Object.fromEntries(gltf.animations.map(a => [a.name, a]));
  return { def, scene: root, scale: s, off, clips, size: [sz.x * s, sz.y * s, sz.z * s] };
}

// over: per-class overrides from the shot list (SHOT.kitDefs), e.g. { tankR: { file: '../glb-md/MD_SOV_t72', len: 9.5 } };
// file is relative to base; tint: [r, g, b] multiplied into the materials; bright: brightening factor
export async function loadKit(base, over = {}) {
  for (const [k, d] of Object.entries(over || {})) KIT_DEF[k] = { ...(KIT_DEF[k] || {}), ...d };
  for (const [key, def] of Object.entries(KIT_DEF)) KIT[key] = prep(def, await loader.loadAsync(base + def.file + '.glb'));
  return Object.fromEntries(Object.entries(KIT).map(([k, v]) => [k, { size: v.size.map(x => +x.toFixed(2)), file: v.def.file, clips: Object.keys(v.clips) }]));
}

let WRECK = null;
function wreckMat() { if (!WRECK) WRECK = new THREE.MeshStandardMaterial({ color: 0x2b2824, roughness: 1, metalness: 0 }); return WRECK; }

// instance: root (placement) > body (tilt / bob) > norm (scale + centring) > cloned glTF scene; wreck = true (near black) or a tone 0-1
function instance(K, wreck) {
  const root = new THREE.Group(), body = new THREE.Group(), norm = new THREE.Group(); root.add(body); body.add(norm);
  const inner = SkeletonUtils.clone(K.scene); norm.add(inner);
  norm.scale.setScalar(K.scale); norm.position.set(K.off[0] * K.scale, K.off[1] * K.scale, K.off[2] * K.scale);
  const bones = {};
  inner.traverse(o => { if (o.name) bones[o.name] = bones[o.name] || o; if (o.isMesh) { o.castShadow = true; o.frustumCulled = false; if (wreck) { const m = o.material.clone(), k = wreck; if (typeof k === 'number') m.color.setRGB(k, k * 0.94, k * 0.86); else m.color.setRGB(0.16, 0.15, 0.14); m.roughness = 1; o.material = m; } } });
  const mixer = new THREE.AnimationMixer(inner), actions = {};
  for (const [n, c] of Object.entries(K.clips)) actions[n] = mixer.clipAction(c);
  let cur = null;
  const act = (name, t, once = false) => {
    const a = actions[name] || actions.idle; if (!a) return;
    if (cur !== a) { if (cur) cur.stop(); a.reset(); a.play(); if (once) { a.setLoop(THREE.LoopOnce, 1); a.clampWhenFinished = true; } cur = a; }
    const d = a.getClip().duration || 1;
    mixer.setTime(once ? Math.min(Math.max(0, t), d - 0.001) : ((t % d) + d) % d);
  };
  return { root, body, norm, inner, bones, act, mixer, duration: n => (actions[n] || actions.idle)?.getClip().duration || 1 };
}

// a vehicle: { root, body, turret (bone, yaw after act), gun: null (recoil is in the attack clip), muzzle, rotor, pose(state, t) }
export function makeVehicle(key, side, wreck = false) {
  const K = KIT[key], I = instance(K, wreck), D = K.def;
  const tip = I.bones.barrel_tip || I.bones.locturret1 || null;
  const muzzle = new THREE.Object3D();
  if (tip) tip.add(muzzle); else { muzzle.position.set(0, K.size[1] * 0.8, K.size[2] * 0.3); I.body.add(muzzle); }
  const rotors = (D.rotor || []).map(n => I.bones[n]).filter(Boolean);
  const o = { key, hoi4: true, root: I.root, body: I.body, turret: I.bones.turret || null, gun: null, muzzle, rotor: rotors[0] || null, rotors, act: I.act, duration: I.duration };
  // state: 'idle' | 'move' | 'attack'; t: clip time (s). The idle clip of tanks sweeps the turret; when aiming the
  // renderer overrides the turret yaw after this call.
  o.pose = (state, t) => {
    let clip = 'idle';
    if (state === 'attack' && D.fireClip !== null && K.clips.attack) clip = 'attack';
    else if (state === 'move' && D.moveClip !== null && K.clips.move) clip = 'move';
    I.act(clip, t, clip === 'attack');
    for (const r of rotors) r.scale.setScalar(1);        // the move clip hides the blades (game swaps in a blur disc)
  };
  if (!wreck) o.pose('idle', 0);
  return o;
}

// a rifleman: { root, act(name, t) } with the v2 clip names (idle / walk / run / shoot / death) mapped to the game's
const SOLDIER_CLIP = { idle: 'idle', walk: 'move', run: 'move', shoot: 'attack', runShoot: 'charge', death: 'death', hit: 'death', support: 'support' };
export function makeSoldier(side) {
  const K = KIT[side ? 'infR' : 'infB'], I = instance(K, false);
  const act = (name, t) => { const n = SOLDIER_CLIP[name] || 'idle'; I.act(n, t, n === 'death'); };
  act('idle', 0);
  return { root: I.root, inner: I.inner, mixer: I.mixer, act, duration: n => I.duration(SOLDIER_CLIP[n] || 'idle') };
}
