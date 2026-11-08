// FPC symbology layer: the sand table drawn with Flashpoint Campaigns: Cold War's own map symbols (shot.symbology).
// The 3D board stays; on top of it this layer draws, per category, what the original game draws on its map:
//   counters  the game's counters built from its own parts (national background, silhouette / NATO symbol, size pips,
//             status icon, movement-direction triangle) + the lettering re-drawn after the manual (FM01 §17.1)
//   contacts  fog of war seen by one side (`view`): Detected (? counter), Classified (UNK_* class symbol), Identified,
//             lost contacts at the last known hex
//   orders    movement paths (overlays.ini [MovementPath]), arrowheads, numbered waypoints, waypoint hint, fire-mission
//             target hexes ([FireSupportArtillery] stripes, "HE, 30 rnds / 09:27-09:32") and lines, battle-plan graphics
//             (the scenario's own mission-graphics bitmaps draped on the ground + [Battleplans] arrows from shot data)
//   markers   VP markers (VP_B/R/Y with value), bridges, craters, brew / kill markers, and any other Map Markers bitmap
//             through shot.symbology.mapMarkers (minefields, obstacles, IP, forts, smoke, TRP, chem ...)
//   fire      the four fire-line styles (Line-of-Fire-Animations.ini), Hit / Miss / artillery / mortar / MRLS sequences,
//             shot.symbology.effects for any other sequence (smoke, airstrike, illumination, surrender ...)
//   panels    Game Control panel (clock, Command Cycle Chronology), Commander panel (orders-in countdown, readiness,
//             force VPs, victory estimate) and Unit Dashboard (key ammo, readiness, morale, range to HQ, orders delay,
//             orders list), drawn after the game's Delphi panels with Roboto
// Each category is on for the whole shot (`true`) or for video windows ([[from, to], ...]); with no shot.symbology the
// renderer never loads this module. Data: the replay (ARENA.game) + the side file from fpc_symbology_data.py
// (`symbology.data`: platforms, morale / readiness / ammo, order queues, commander wakes).
// asset root: ?video=<file URL> on sandbox.html (render.py passes $ARENA_DATA/video/), default <repo>/data/video/
const VIDEO = globalThis.ARENA_VIDEO || new URL('../../data/video/', import.meta.url).href;
const FPC = VIDEO + 'fpc-original/';
const ASSETS = VIDEO + 'fpc-symbology/assets/';
const CP = FPC + '01-unit-counters/';
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const lerp = (a, b, f) => a + (b - a) * f;
const hexA = (c, a) => { const n = parseInt(c.slice(1), 16); return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`; };

// overlays.ini (the game's "Recommended" setting) and Line-of-Fire-Animations.ini values
const INI = {
  path: { c: '#80FFFF', a: 0xE8 / 255, w: 7 }, fire: [{ c: '#085AFF', a: 0x9F / 255 }, { c: '#FF0000', a: 0x9F / 255 }], fireW: 7,
  arty: { c: '#FF6A00', a: 0xB6 / 255 }, cas: { c: '#D000FF', a: 0xB6 / 255 }, illum: { c: '#FFFF00', a: 0xB6 / 255 },
  plan: { pending: ['#5CA1FF', '#FF5C86'], active: ['#67FF5C', '#EEFF5C'], done: ['#7F7F7F', '#7F7F7F'], a: 0xE0 / 255, arrowW: 64, lineW: 8 },
  cursor: { c: '#C0C0C0', a: 0xE8 / 255 },
  lof: {
    Gun: { pw: 32, pl: 300, speed: 300, vw: 10, vl: 1200, smoke: [20, 25, 128, 64], bg: [0x40, 36], n: 1 },
    AutoCannon: { pw: 32, pl: 60, speed: 150, gap: 450, vw: 10, vl: 400, smoke: [20, 30, 128, 64], bg: [0x40, 36], n: 3 },
    ATGM: { pw: 32, pl: 300, speed: 100, burnW: 50, burnL: 50, burn: ['rgba(240,224,96,.63)', 'rgba(255,0,0,.63)', 'rgba(255,255,255,.63)'], smoke: [16, 6, 128, 32], launch: [20, 24, 128, 64], bg: [0x30, 36], n: 1 },
    Classic: { w: 32 },
  },
};
const ORDER_LETTER = { Assault: 'A', 'Move Deliberate': 'M', 'Move Hasty': 'M', Screen: 'S', Hold: 'H', Barrage: 'B', Gas: 'G', 'Counter Battery': 'C',
  Engineer: 'E', Resupply: 'R', 'Rest and Resupply': 'R', 'On Call': 'O', Scoot: 'F', Withdraw: 'W', Hunt: 'Z', Scan: 'S' };
const ORDER_ZH = { Assault: '突击', 'Move Deliberate': '开进', 'Move Hasty': '急进', Withdraw: '撤退', Hold: '坚守', Screen: '警戒', Barrage: '炮击', Gas: '毒气',
  'Counter Battery': '反炮兵', Engineer: '工兵作业', Resupply: '补给', 'Rest and Resupply': '休整补给', 'On Call': '待命射击', Scoot: '转移阵地', Hunt: '搜索攻击', Scan: '侦察' };
const MOVE = new Set(['Move Deliberate', 'Move Hasty', 'Assault', 'Withdraw']);
const DIR_KIND = { Assault: 'assault', 'Move Deliberate': 'deliberate', 'Move Hasty': 'hasty', Withdraw: 'hasty', Scoot: 'hasty' };
const PIP = { '.': 'pip_o', '..': 'pip_oo', '...': 'pip_ooo', I: 'pip_i', II: 'pip_ii', III: 'pip_iii', X: 'pip_x', XX: 'pip_xx', XXX: 'pip_xxx' };
const MOB = { Track: 'T', Wheel: 'W', Leg: 'L', Foot: 'L', Rotary: 'R', Helicopter: 'R', Prop: 'P', Jet: 'J', None: 'S', '': 'S' };
const NATION = [   // national_era / scenario side -> counter background, flag
  { re: /United Kingdom|British/i, bg: 'British__UK-BGD', flag: 'British__Flags__uk flag', badge: 'British__Badges__Royal Regiment Fusiliers', name: 'United Kingdom' },
  { re: /East-German|DDR/i, bg: 'East-German__DDR-BGD', flag: 'East-German__Flags__nva flag', badge: 'East-German__Badges__NVA', name: 'East Germany' },
  { re: /United States|American|US Army/i, bg: 'American__US-BGD', flag: 'American__Flags__US Flag', badge: 'American__Badges__11th Armored Cavalry Reg', name: 'United States' },
  { re: /Soviet|USSR/i, bg: 'Soviet__USSR-BGD', flag: 'Soviet__Flags__ussr flag', badge: 'Soviet__Badges__National USSR', name: 'Soviet Union' },
  { re: /West-German|FRG/i, bg: 'West-German__FRG-BGD', flag: 'West-German__Flags__frg flag', badge: '', name: 'West Germany' },
  { re: /Czech/i, bg: 'Czechoslovak__CSSR-BGD', flag: 'Czechoslovak__Flags__cz flag', badge: '', name: 'Czechoslovakia' },
  { re: /Polish|Poland/i, bg: 'Polish__PL-BGD', flag: '', badge: '', name: 'Poland' },
  { re: /Dutch|Netherlands/i, bg: 'Dutch__NL-BGD', flag: '', badge: '', name: 'Netherlands' },
  { re: /Belgian/i, bg: 'Belgian__BG-BGD', flag: 'Belgian__Flags__be flag', badge: '', name: 'Belgium' },
  { re: /Canad/i, bg: 'Canadian__CN-BGD', flag: 'Canadian__Flags__ca flag', badge: '', name: 'Canada' },
  { re: /French|France/i, bg: 'French__FR-BGD', flag: '', badge: '', name: 'France' },
];
const nationOf = s => NATION.find(n => n.re.test(s || '')) || NATION[0];
// fallbacks when no side file is given: icon type -> NATO symbol (raw/Common/Data/NATO) and movement class
const TYPE_NATO = { Tank: ['NS-TANK', 'Track'], APC: ['NS-APCT', 'Track'], Inf: ['NS-INFLG', 'Leg'], AT: ['NS-INFAM', 'Leg'], 'SP AT': ['NS-SPAT', 'Wheel'],
  Arty: ['NS-ARTY', 'None'], SPArty: ['NS-SPARTY', 'Track'], Recce: ['NS-RECCE', 'Wheel'], HQ: ['NS-HQ', 'Wheel'], Eng: ['NS-ENGR', 'Leg'],
  AD: ['NS-SPSAM', 'Track'], Helo: ['NS-HELOAT', 'Rotary'], Utility: ['NS-TRANS', 'Wheel'], WLR: ['NS-SPWLR', 'Track'], Depot: ['NS-TRANS', 'Wheel'] };
const UNK_CLASS = u => /^TANK/.test(u.sut || '') || u.type === 'Tank' ? 'UNK_Tank' : /^(APC|IFV|RCN|RECCE|SPAT)/.test(u.sut || '') || ['APC', 'SP AT', 'Recce'].includes(u.type) ? 'UNK_Mech'
  : /^INF|^ENGR/.test(u.sut || '') || ['Inf', 'AT', 'Eng'].includes(u.type) ? 'UNK_Inf' : 'UNK_Utility';

export async function initFPC(api) {
  const { SHOT, G, THREE } = api, S = SHOT.symbology === true || SHOT.symbology === 'fpc' ? {} : { ...SHOT.symbology };
  const Wd = SHOT.width, Ht = SHOT.height, NU = G.u.length;
  const cats = ['counters', 'contacts', 'orders', 'markers', 'fire', 'panels'];
  for (const c of cats) if (S[c] === undefined) S[c] = c === 'contacts' ? false : true;
  const winOf = x => x === true ? null : Array.isArray(x) ? x : x && x.at ? x.at : null;
  const on = (c, v) => { const x = S[c]; if (!x) return false; if (x === true) return true; const w = winOf(x); return !w || w.some(([a, b]) => v >= a && v <= b); };
  const opt = c => (S[c] && typeof S[c] === 'object' && !Array.isArray(S[c])) ? S[c] : {};
  // zh: viewer-friendly counters: the counter keeps only its pictures (nation colour, silhouette, status, direction); the
  // name, strength and order go to a Chinese tag under the counter (stacks: one tag under the stack)
  // staging (tools/video/staging/STANDARD.md, compiled by compile_staging.py): `staging: { shots, cues, commanders }`.
  // The board keeps only counters (pictures), objective badges (owner colour) and the board markers; names, values, paths,
  // fire, losses, score, fog and quote cards come and go with the cues (video-second windows). Counters sit on the replay's
  // own positions (the video time map may jump back between shots).
  const STG = S.staging ? { ...S.staging, ...(SHOT.stagingCues || {}) } : null;   // stagingCues: make_segment.py
  const ZH = !!(S.zh ?? opt('counters').zh) || !!STG;

  // ---------------------------------------------------------------- assets
  const imgCache = new Map();
  const img = async p => { if (imgCache.has(p)) return imgCache.get(p); const im = new Image(); im.src = await api.dataURL(p); await im.decode().catch(() => {}); imgCache.set(p, im); return im; };
  const tryImg = async p => { try { return await img(p); } catch (e) { return null; } };
  for (const [fam, file, w] of [['FPC Roboto', 'Roboto-Regular.ttf', '400'], ['FPC Roboto', 'Roboto-Bold.ttf', '700'], ['FPC RobotoC', 'RobotoCondensed-Bold.ttf', '700'],
    ['FPC RobotoC', 'RobotoCondensed-Regular.ttf', '400'], ['FPC Marker', 'PermanentMarker-Regular.ttf', '400']]) {
    const buf = await (await fetch(FPC + '06-ui-panels/fonts/' + file)).arrayBuffer();
    const ff = new FontFace(fam, buf, { weight: w }); await ff.load(); document.fonts.add(ff);
  }
  const FD = S.data ? await (await fetch(S.data.startsWith('file:') ? S.data : 'file:///' + S.data)).json() : null;
  const uinfo = G.u.map((u, i) => { const x = FD && FD.units[i] || {}; const tn = TYPE_NATO[u[2]] || ['NS-INFLG', 'Leg'];
    return { ...x, type: u[2], size: u[3], name: u[0], side: u[1], sutType: x.sut, nat: x.nat || '', pic: x.pic || '', nato: x.nato || tn[0] + '.png', mob: x.mob || tn[1] }; });
  // nations: side file national_era, else shot.symbology.nations, else by scenario
  const scnNat = /Minden/i.test(G.scn || '') ? ['United Kingdom', 'East-German'] : ['United States', 'Soviet'];
  const sideNat = [0, 1].map(sd => nationOf((S.nations || [])[sd] || (uinfo.find(u => u.side === sd && u.nat) || {}).nat || scnNat[sd]));
  const P = {};
  for (const n of ['pip_o', 'pip_oo', 'pip_ooo', 'pip_i', 'pip_ii', 'pip_iii', 'pip_x', 'pip_xx', 'pip_xxx', 'highlight_selected', 'pip_spotted'])
    P[n] = await img(CP + 'counter-parts/' + n + '.png');
  for (const d of ['n', 'ne', 'nw', 's', 'se', 'sw']) for (const k of ['hasty', 'deliberate', 'assault']) P[`dir_${d}_${k}`] = await img(CP + `counter-parts/dir_${d}_${k}.png`);
  const STATUS = { green: await img(FPC + '06-ui-panels/exe-embedded/png_030abeab_100x100.png'), yellow: await img(FPC + '06-ui-panels/exe-embedded/png_030ad8b4_100x100.png'),
    red: await img(FPC + '06-ui-panels/exe-embedded/png_030ad13a_100x100.png'), black: await img(FPC + '06-ui-panels/exe-embedded/png_030abcb7_100x100.png') };
  const BG = [], NATIONS = new Map();
  const bgFor = async nat => { const n = nationOf(nat); if (!NATIONS.has(n.bg)) NATIONS.set(n.bg, await img(CP + 'nation-backgrounds/' + n.bg + '.png')); return NATIONS.get(n.bg); };
  for (const sd of [0, 1]) BG[sd] = await bgFor(sideNat[sd].name);
  const SIL = {};
  for (const u of uinfo) {
    if (u.pic && !SIL[u.pic]) SIL[u.pic] = await tryImg(FPC + 'raw/FCCW/Sils/' + u.pic);
    if (!SIL[u.nato]) SIL[u.nato] = await tryImg(FPC + 'raw/Common/Data/NATO/' + u.nato);
    u.bg = u.nat ? await bgFor(u.nat) : BG[u.side];
  }
  const FOG = {}; for (const n of ['_Unknown', 'UNK_Tank', 'UNK_Mech', 'UNK_Inf', 'UNK_Utility']) FOG[n] = await img(CP + 'contact-and-fog-symbols/' + n + '.png');
  const MK = {}; const mk = async n => MK[n] || (MK[n] = await tryImg(FPC + '03-map-markers/markers/' + n + '.png'));
  for (const n of ['VP_B', 'VP_R', 'VP_Y', 'bridgeNS', 'bridgeNE', 'bridgeSE', 'blownbridgeNS', 'blownbridgeNE', 'blownbridgeSE', 'Crater1', 'Crater2', 'Crater3', 'Crater4', 'Crater5',
    'Brew_B', 'Brew_R', 'Kill_B', 'Kill_R', 'HeloWreck_B', 'HeloWreck_R']) await mk(n);
  for (const m of S.mapMarkers || []) await mk(m.file || markerFile(m));
  const ATLAS = await (await fetch(ASSETS + 'fx/atlas.json')).json(), FX = {};
  const fxImg = async n => FX[n] || (FX[n] = { im: await img(ASSETS + 'fx/' + n + '.png'), ...ATLAS[n] });
  for (const n of ['Hit 1', 'Hit 2', 'Hit 3', 'Hit 4', 'Hit 5', 'Hit 6', 'Miss 1', 'Miss 2', 'Miss 3', 'Miss 4', 'Miss 5', 'Miss 6', 'Artillery Stonk Short 1', 'Artillery Stonk Short 2',
    'Artillery Stonk Short 3', 'Artillery Stonk Short 4', 'Mortar 1', 'Mortar 2', 'Mortar 3', 'Mortar 4', 'MRLS Barrage 1', 'MRLS Barrage 2']) await fxImg(n);
  for (const e of S.effects || []) await fxImg(e.kind);
  const PANEL_IMG = { popout: await img(FPC + '06-ui-panels/exe-embedded/png_030b0d41_24x24.png') };
  // HOI4 interface sprites for the staging look (counter plate, strength bar, selection frame, mini-dialog 9-slice, order icons)
  const HUI = VIDEO + 'hoi4-assets/ui/', HST = VIDEO + 'assets-staging/hoi4/';
  const H4 = { plate: await tryImg(HUI + 'counter/onmap_unit_counter.png'), str: await tryImg(HUI + 'counter/mapicon_bar_str_sprite.png'),
    sel: await tryImg(HUI + 'counter/onmap_unit_counter_selected.png'), mini: await tryImg(HUI + 'panel/tiled_mini_dialog.png'),
    art: await tryImg(HUI + 'army_icons/onmap_unit_art_icon.png'), offence: await tryImg(HST + 'order_draw_offence_btn.png'),
    blitz: await tryImg(HST + 'order_draw_blitz_btn.png'), defence: await tryImg(HST + 'order_draw_defence_btn.png'),
    fallback: await tryImg(HST + 'order_draw_fallback_btn.png'), area: await tryImg(HST + 'order_area_defense_btn.png') };
  const ORDER_ICON = { Assault: 'blitz', 'Move Deliberate': 'offence', 'Move Hasty': 'offence', Withdraw: 'fallback', Hold: 'defence', Screen: 'area', Barrage: 'art' };
  for (const sd of [0, 1]) { const n = sideNat[sd]; PANEL_IMG['flag' + sd] = n.flag ? await tryImg(FPC + '06-ui-panels/badges-flags/' + n.flag + '.png') : null;
    const bd = (opt('panels').badges || [])[sd] || n.badge; PANEL_IMG['badge' + sd] = bd ? await tryImg(FPC + '06-ui-panels/badges-flags/' + bd + '.png') : null; }

  // battle-plan graphics: the scenario's own mission-graphics bitmap (briefing / threat overlay of a side) draped on the board
  const planMeshes = [];
  for (const bp of opt('orders').planImages || []) {
    const im = await img(bp.src), tex = new THREE.Texture(im); tex.needsUpdate = true; tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 8;
    const nx = 160, nz = 120, geo = new THREE.PlaneGeometry(1, 1, nx, nz), pos = geo.attributes.position, uv = geo.attributes.uv;
    for (let k = 0; k < pos.count; k++) { const u = uv.getX(k), w = 1 - uv.getY(k), mx = u * api.MAP_W, mz = w * api.MAP_H; const p = api.W(mx, mz, 0.006); pos.setXYZ(k, p.x, p.y, p.z); }
    geo.computeBoundingSphere();
    // the bitmap's own grey-green paper (#6D7863) is the editor background: keyed out so only the drawing stays
    const mat = new THREE.ShaderMaterial({ transparent: true, depthWrite: false, depthTest: false, uniforms: { map: { value: tex }, op: { value: 1 } },
      vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
      fragmentShader: 'uniform sampler2D map; uniform float op; varying vec2 vUv; void main(){ vec4 c = texture2D(map, vUv); vec3 k = vec3(0.427,0.471,0.388); float d = distance(c.rgb, k); c.a *= smoothstep(0.03, 0.09, d) * op; gl_FragColor = c; }' });
    const m = new THREE.Mesh(geo, mat); m.renderOrder = 6; m.frustumCulled = false; m.visible = false; api.scene.add(m); planMeshes.push({ bp, m });
  }

  // ---------------------------------------------------------------- side-file lookups
  const series = (key, i, g) => { const s = FD && FD[key] && FD[key][i]; if (!s || !s.length) return null; const k = api.rowAt(s, g); return s[Math.max(0, k)][1]; };
  const queueAt = (i, g) => { if (FD) { const s = FD.oq[i]; const k = api.rowAt(s, g); return k >= 0 ? s[k][1] : []; }
    const os = G.o[i] || [], k = api.rowAt(os, g); if (k < 0) return []; const r = os[k]; return [{ o: G.on[r[1]], s: r[0], d: r[2] || undefined, tu: r[3] >= 0 ? r[3] : undefined }]; };
  const curOrder = (i, g) => { const q = queueAt(i, g); return q.find(o => o.s == null || o.s <= g) || q[0] || null; };
  const hexAt = (i, t) => { const s = api.unitState(i, t); return s && s.hex; };
  const unitXZ = (i, t) => { const s = api.unitState(i, t); return s && !isNaN(s.x) ? [s.x, s.z] : null; };
  // orders delay per unit: game minutes between a release that touched the unit and its first order's start
  const delays = Array.from({ length: NU }, () => []);
  if (FD) for (const [t, side] of FD.rel) for (let i = 0; i < NU; i++) { if (G.u[i][1] !== side) continue; const q = FD.oq[i], k = api.rowAt(q, t + 1);
    if (k >= 0 && q[k][0] === t) { const o = q[k][1][0]; if (o && o.s != null) delays[i].push([t, Math.max(0, Math.round((o.s - t) / 60))]); } }

  // ---------------------------------------------------------------- projection
  let cam = null, frameV = 0, frameG = 0;
  const tmp = new THREE.Vector3();
  const scr = (x, z, dy = 0.004) => { tmp.copy(api.W(x, z, dy)).project(cam); return [(tmp.x + 1) / 2 * Wd, (1 - tmp.y) / 2 * Ht, tmp.z]; };
  const hexPxAt = (x, z) => { const a = scr(x - api.HX / 2, z), b = scr(x + api.HX / 2, z); return Math.hypot(b[0] - a[0], b[1] - a[1]); };
  const hexPoly = (h, k = 1) => { const [x, z] = api.hexXZ(h); return api.CORNER.map(([cx, cz]) => scr(x + cx * k, z + cz * k)); };
  const pathPoly = (c, pts) => { c.beginPath(); pts.forEach((p, k) => k ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1])); };
  const offscreen = (p, m = 120) => p[2] > 1 || p[0] < -m || p[0] > Wd + m || p[1] < -m || p[1] > Ht + m;
  // the game's 9-slice drawing (corners / edges at their own size, scaled by d / b; the centre stretched)
  const nine = (c, im, x, y, w, h, b = 12, d = 8) => { d = Math.min(d, w / 2, h / 2);
    if (!im) { c.fillStyle = 'rgba(16,18,14,.88)'; c.fillRect(x, y, w, h); return; }
    const W = im.width, H = im.height, sx = [0, b, W - b, W], sy = [0, b, H - b, H], dx = [x, x + d, x + w - d, x + w], dy = [y, y + d, y + h - d, y + h];
    for (let r = 0; r < 3; r++) for (let q = 0; q < 3; q++) { const dw = dx[q + 1] - dx[q], dh = dy[r + 1] - dy[r]; if (dw > 0.2 && dh > 0.2) c.drawImage(im, sx[q], sy[r], sx[q + 1] - sx[q], sy[r + 1] - sy[r], dx[q], dy[r], dw, dh); } };
  const text = (c, s, x, y, o) => { c.font = o.font; c.textAlign = o.align || 'center'; c.textBaseline = o.base || 'alphabetic';
    if (o.halo) { c.lineJoin = 'round'; c.lineWidth = o.halo; c.strokeStyle = o.haloC || '#000'; c.strokeText(s, x, y); } c.fillStyle = o.color || '#fff'; c.fillText(s, x, y); };

  // ---------------------------------------------------------------- counters
  const CS = 128, counterCache = new Map();
  const statusOf = f => f <= 0 ? 'black' : f < 0.34 ? 'red' : f < 0.67 ? 'yellow' : 'green';
  function drawSil(c, im, side, x, y, w, h, flip) {
    if (!im) return;
    c.save(); if (flip) { c.translate(x + w, y); c.scale(-1, 1); c.drawImage(im, 0, 0, w, h); } else c.drawImage(im, x, y, w, h); c.restore();
  }
  // mode: 'own' (all details), 'ident' / 'class' / 'detect' (enemy contact states, FM01 §24.1)
  function counterCanvas(i, mode, alive, dirKey, letter, sel, full = false) {
    const plain = ZH && !full;
    const key = [i, mode, alive, dirKey, letter, sel ? 1 : 0, plain ? 1 : 0].join('|'); if (counterCache.has(key)) return counterCache.get(key);
    const u = uinfo[i], side = u.side, cv = document.createElement('canvas'); cv.width = cv.height = CS + 24; const c = cv.getContext('2d');
    c.translate(12, 12);
    c.drawImage(u.bg || BG[side], 0, 0, CS, CS);
    const W8 = '700 ', F = n => `${W8}${n}px "FPC Roboto"`, FC = n => `${W8}${n}px "FPC RobotoC"`;
    const total = G.u[i][5], f = alive / Math.max(1, total);
    const flip = side === 1;
    // main picture: silhouette for vehicles / guns, the game's own '_' symbols (never flipped) or the NATO symbol otherwise
    let pic = null, picName = '';
    if (mode === 'detect') pic = FOG._Unknown; else if (mode === 'class') pic = FOG[UNK_CLASS(u)];
    else { picName = u.pic && SIL[u.pic] ? u.pic : u.nato; pic = SIL[picName]; }
    const silY = plain ? 24 : mode === 'own' ? 20 : 26, silH = plain ? 62 : mode === 'own' ? 56 : 60;
    drawSil(c, pic, side, (CS - silH * 2) / 2, silY, silH * 2, silH, flip && mode !== 'detect' && mode !== 'class' && !picName.startsWith('_') && !picName.startsWith('NS-'));
    if (mode !== 'detect' && !plain) text(c, MOB[u.mob] ?? 'S', 6, 26, { font: F(26), align: 'left', halo: 4 });
    if (mode === 'own' && !plain) {
      const pip = P[PIP[u.size]]; if (pip) c.drawImage(pip, CS / 2 - 33, -6, 66, 34);
      if (u.type === 'HQ') text(c, 'H', CS - 6, 26, { font: F(26), align: 'right', halo: 4 });
      let nm = u.name, fs = 17; c.font = FC(fs); while (c.measureText(nm).width > CS - 10 && fs > 12) { fs--; c.font = FC(fs); }
      while (c.measureText(nm).width > CS - 8 && nm.length > 3) nm = nm.slice(0, -1);
      text(c, nm, CS / 2, 92, { font: FC(fs), halo: 3.5 });
    }
    const num = mode === 'detect' ? '?' : String(alive);
    if (!plain) text(c, num, 7, CS - 6, { font: F(38), align: 'left', halo: 5 });
    if (!plain) text(c, mode === 'own' ? letter : '\u2014', CS - 7, CS - 8, { font: F(mode === 'own' ? 32 : 30), align: 'right', halo: 4.5 });
    if (mode === 'own') c.drawImage(STATUS[statusOf(f)], CS / 2 - 8, CS - (plain ? 30 : 36), 16, 16);
    // direction of travel inside the counter on the edge / corner the unit moves towards, as in FM01 §17 (the counter
    // diagram: corner triangles in the corners, the south one at the bottom centre under the status icon)
    if (dirKey) { const im = P[dirKey], d = dirKey.split('_')[1], s = d.length === 1 ? 0.46 : 0.42, w = im.width * s, h = im.height * s;
      const at = { n: [CS / 2 - w / 2, 0], s: [CS / 2 - w / 2, CS - h], ne: [CS - w, 0], nw: [0, 0], se: [CS - w, CS - h], sw: [0, CS - h] }[d];
      c.drawImage(im, at[0], at[1], w, h); if (d === 's' && mode === 'own') c.drawImage(STATUS[statusOf(f)], CS / 2 - 8, CS - h - 14, 16, 16); }
    if (sel) c.drawImage(P.highlight_selected, -10, -10, CS + 20, CS + 20);
    counterCache.set(key, cv); return cv;
  }
  function zhTag(c, L, g) {                       // "28团1营3连 30/31 · 突击" under the counter; a stack: "28团1营 等 5 个 · 突击"
    const i = L.i, cur = curOrder(i, g), ord = cur ? ORDER_ZH[cur.o] || cur.o : '坚守', n = L.n || 1;
    let name = api.unitZh(i), str = `${L.alive}/${G.u[i][5]}`;
    if (n > 1) { const ns = L.stack.map(it => api.unitZh(it.i)); let p = ns[0];
      for (const q of ns) { let k = 0; while (k < p.length && k < q.length && p[k] === q[k]) k++; p = p.slice(0, k); }
      const cut = Math.max(...[...'团营连排班队'].map(ch => p.lastIndexOf(ch))); name = `${cut >= 1 ? p.slice(0, cut + 1) : name} 等 ${n} 个`; str = ''; }
    const fs = Math.round(clamp(L.s * 0.2, 13, 19)), f1 = `700 ${fs}px "Noto Sans SC",sans-serif`, f2 = `500 ${Math.round(fs * 0.9)}px "Noto Sans SC",sans-serif`;
    c.save(); c.font = f1; const w1 = c.measureText(name).width; c.font = f2; const tail = (str ? str + ' · ' : '') + ord, w2 = c.measureText(tail).width;
    const pad = fs * 0.4, gap = fs * 0.45, bar = Math.max(3, fs * 0.22), w = bar + pad + w1 + gap + w2 + pad, h = fs * 1.45;
    const top = L.y0 + L.s * (CS + 24) / CS / 2 - L.s * 0.06, x = L.x0 - w / 2;
    c.fillStyle = 'rgba(18,20,16,.8)'; c.beginPath(); c.roundRect(x, top, w, h, h * 0.18); c.fill();
    c.fillStyle = L.o.side === 0 ? '#4f93ea' : '#e0473b'; c.fillRect(x, top, bar, h);
    const y = top + h * 0.72; text(c, name, x + bar + pad, y, { font: f1, align: 'left', color: '#fff' });
    text(c, tail, x + bar + pad + w1 + gap, y, { font: f2, align: 'left', color: '#d9d4c3' }); c.restore();
  }
  // six-way direction of travel in screen space (FPC map is north-up; the triangles name the hex sides)
  function dirName(ax, ay, bx, by) { const a = (Math.atan2(bx - ax, -(by - ay)) * 180 / Math.PI + 360) % 360; return ['n', 'ne', 'se', 's', 'sw', 'nw'][Math.floor(((a + 30) % 360) / 60)]; }

  // ---------------------------------------------------------------- contacts (one side's view)
  const COMBAT = new Map(); for (const [t, i] of (FD && FD.combat) || []) { if (!COMBAT.has(i)) COMBAT.set(i, []); COMBAT.get(i).push(t); }
  const ownNear = (side, x, z, t) => { let best = 1e9; for (let j = 0; j < NU; j++) { if (G.u[j][1] !== side) continue; const s = api.unitState(j, t); if (!s || s.st !== 1 || !s.alive || isNaN(s.x)) continue; best = Math.min(best, Math.hypot(s.x - x, s.z - z)); } return best; };
  const C = opt('contacts'), RNG = C.ranges || [1.5, 2.5, 3.5];          // km: identify / classify / detect (approximation, see README)
  function contactState(i, view, t) {
    const s = api.unitState(i, t); if (!s || s.st !== 1 || isNaN(s.x)) return null;
    const d = ownNear(view, s.x, s.z, t), fired = (COMBAT.get(i) || []).some(tc => tc <= t && t - tc < 300);
    if (d <= RNG[0]) return 'ident'; if (d <= RNG[1] || (fired && d <= RNG[2] + 1)) return 'class'; if (d <= RNG[2]) return 'detect'; return null;
  }
  function lostAt(i, view, g) {                     // seen in the last 10 game minutes, not now: last known hex
    for (let dt = 60; dt <= 600; dt += 60) { const st = contactState(i, view, g - dt); if (st) { const s = api.unitState(i, g - dt); return { x: s.x, z: s.z, age: dt }; } }
    return null;
  }

  // ---------------------------------------------------------------- paths
  const chaikin = (pts, it = 2) => { let Pp = pts; for (let r = 0; r < it; r++) { const Q = [Pp[0]]; for (let k = 0; k + 1 < Pp.length; k++) { const a = Pp[k], b = Pp[k + 1];
    Q.push([0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]], [0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]]); } Q.push(Pp[Pp.length - 1]); Pp = Q; } return Pp; };
  function planOf(i, g, start) {                  // [{ wp: hex, o: order, pts: [[x, z]] }] for the move orders at the head of the queue
    const q = queueAt(i, g), out = []; let at = start, t = g;
    for (const o of q) {
      if (!MOVE.has(o.o) || !o.d) break; if (o.e != null && o.e < g) continue;
      const rows = G.p[i] || [], hx = []; let arrive = null;
      for (const r of rows) { if (r[0] <= t) continue; if (o.e != null && r[0] > o.e + 300) break; if (!r[1]) continue;
        const k = hx.indexOf(r[1]); if (k >= 0) { hx.length = k + 1; continue; }      // came back to a hex: drop the loop
        hx.push(r[1]); if (r[1] === o.d) { arrive = r[0]; break; } }
      const dxz = api.hexXZ(o.d), pts = arrive ? [at, ...hx.map(api.hexXZ)] : [at, dxz];
      out.push({ wp: o.d, o, pts, arrive: arrive ?? o.e }); at = dxz; t = arrive ?? o.e ?? t;
      if (out.length >= 6) break;
    }
    return out;
  }

  // ---------------------------------------------------------------- per-frame state shared by the layers
  let layout = new Map();                          // unit -> { x, y, s (counter px), hexPx }
  const SMOOTH = new Map(), STICK = new Map();    // per unit: smoothed map position; per stack top: last strip spot
  function placeCounters(v, g, live) {
    layout = new Map();
    const view = STG ? FR.fog : on('contacts', v) ? (C.view ?? 0) : null, items = [];
    for (const l of live) {
      const o = l.o, i = o.i; if (l.dead) continue;
      const P0 = o.P || {}, x = P0.bx ?? l.s.x, z = P0.bz ?? l.s.z;
      let mode = 'own';
      if (view != null && o.side !== view) { mode = contactState(i, view, g); if (!mode) continue; }
      const p = scr(x, z, 0.01); if (offscreen(p)) continue;
      items.push({ i, o, x, z, p, mode, moving: !!(P0.moving && !P0.turning), hex: l.s.hex, alive: l.alive, hd: P0.hd });
    }
    // stacks: same side, same hex, standing: offset up-left like the game's stack (later counters on top)
    const byHex = new Map();
    for (const it of items) { if (it.moving) continue; const k = it.o.side + ':' + it.hex; if (!byHex.has(k)) byHex.set(k, []); byHex.get(k).push(it); }
    const top = it => STG && FR.named.has(it.i) ? 1 + (FR.hl.has(it.i) ? 1 : 0) : 0;      // staging: named / highlighted on top of the stack
    for (const list of byHex.values()) list.forEach(it => { it.n = list.length; it.stack = list; }), list.sort((a, b) => top(a) - top(b) || G.u[b.i][5] - G.u[a.i][5] || a.i - b.i).forEach((it, k) => { it.k = k; const a = api.hexXZ(it.hex); it.wx = a[0] + (it.o.side ? 1 : -1) * 0.07; it.wz = a[1] + 0.05; });
    // a counter glides (in map space) between its path position while moving and its stack anchor while standing, instead
    // of snapping at every stop / start (the periodic jumps); a cut (non-consecutive frame) or a long jump resets it
    for (const it of items) { const tx = it.wx ?? it.x, tz = it.wz ?? it.z, m = SMOOTH.get(it.i), dv = m ? v - m.v : -1;
      let sx = tx, sz = tz;
      if (m && dv === 0) { sx = m.x; sz = m.z; }   // the same frame placed again
      else if (m && dv > 0 && dv < 0.2 && Math.hypot(tx - m.x, tz - m.z) < 1.2) { const a = 1 - Math.exp(-dv / 0.14); sx = m.x + (tx - m.x) * a; sz = m.z + (tz - m.z) * a; }
      SMOOTH.set(it.i, { x: sx, z: sz, v }); it.p = scr(sx, sz, 0.01); }
    // zh / staging: HOI4-style chips of a fixed screen size (by the shot's zoom), one per stack (the top one), no fan-out
    // 11-08 phone pass: chips 30 / 25 / 21 -> 36 / 32 / 28 px; their name strips get their own (larger) type, STRIP_PX
    const chipH = STG ? ({ close: 36, mid: 32, wide: 28 }[(FR.shot || {}).zoom] || 32) : null;
    for (const it of items) { const hp = hexPxAt(it.x, it.z), k = it.k || 0;
      if (ZH) { const s = chipH || clamp(hp * 0.3, 18, 28); layout.set(it.i, { ...it, x0: it.p[0], y0: it.p[1], s, hp, top: !it.stack || k === it.stack.length - 1 }); continue; }
      const s = clamp(hp * (S.counterScale ?? 0.74), S.minPx ?? 26, S.maxPx ?? 96);
      layout.set(it.i, { ...it, x0: it.p[0] - k * s * 0.09, y0: it.p[1] - k * s * 0.09, s, hp }); }
    return view;
  }

  // ---------------------------------------------------------------- HOI4-style chip (zh / staging)
  // [icon box: the nation background + the game's silhouette / NATO symbol] with the strength bar under it (green / yellow
  // / red as the game's status dot) and a stack count; the Chinese name strip is attached on the right by chipStrips
  // HOI4 on-map counter (onmap_unit_counter, 66 x 27): plate tinted with the side colour, the icon zone widened for the
  // game's silhouette / NATO symbol (drawn light, as HOI4 draws its unit icons), the dark box with the stack count; the
  // strength bar (mapicon_bar_str) under the plate; the selection frame (onmap_unit_counter_selected) for a highlight
  const iconCache = new Map(), ZW = 1.5, BW = 24 / 27, RW = 11 / 27, CHW = ZW + BW + RW, TINT = ['#5f8fe0', '#d6503e'];
  function iconCanvas(i, mode) {
    const key = i + '|' + mode; if (iconCache.has(key)) return iconCache.get(key);
    const u = uinfo[i], H = 54, k = H / 27, W = Math.round(H * CHW), cv = document.createElement('canvas'); cv.width = W; cv.height = H; const c = cv.getContext('2d');
    const P = H4.plate, zw = Math.round(H * ZW), bw = Math.round(H * BW);
    if (P) { const pl = document.createElement('canvas'); pl.width = W; pl.height = H; const q = pl.getContext('2d');      // the plate, widened
      q.drawImage(P, 0, 0, 31, 27, 0, 0, zw, H); q.drawImage(P, 31, 0, 24, 27, zw, 0, bw, H); q.drawImage(P, 55, 0, 11, 27, zw + bw, 0, W - zw - bw, H);
      c.drawImage(pl, 0, 0); c.globalCompositeOperation = 'multiply'; c.fillStyle = TINT[u.side]; c.fillRect(0, 0, W, H);
      c.globalCompositeOperation = 'destination-in'; c.drawImage(pl, 0, 0);
      c.globalCompositeOperation = 'source-over'; c.fillStyle = 'rgba(28,26,24,.96)'; c.fillRect(zw + 2 * k, 4 * k, bw - 4 * k, H - 9 * k); }      // the count box, over the tint
    else { c.fillStyle = TINT[u.side]; c.fillRect(0, 0, W, H); }
    let pic = null, picName = '';
    if (mode === 'detect') pic = FOG._Unknown; else if (mode === 'class') pic = FOG[UNK_CLASS(u)]; else { picName = u.pic && SIL[u.pic] ? u.pic : u.nato; pic = SIL[picName]; }
    if (pic) { const ph = H * 0.6, pw = ph * 2, t = document.createElement('canvas'); t.width = Math.ceil(pw); t.height = Math.ceil(ph); const x = t.getContext('2d');
      drawSil(x, pic, u.side, 0, 0, pw, ph, u.side === 1 && mode !== 'detect' && mode !== 'class' && !picName.startsWith('_') && !picName.startsWith('NS-'));
      // light icon from the dark strokes: the darker a pixel, the more opaque its light colour (NATO symbols carry a white
      // box behind their lines; silhouettes are dark on transparent)
      const D = x.getImageData(0, 0, t.width, t.height), px = D.data;
      for (let n = 0; n < px.length; n += 4) { const lum = (0.3 * px[n] + 0.59 * px[n + 1] + 0.11 * px[n + 2]) / 255; px[n + 3] = Math.round(px[n + 3] * clamp((0.82 - lum) / 0.62, 0, 1)); px[n] = 244; px[n + 1] = 239; px[n + 2] = 224; }
      x.putImageData(D, 0, 0);
      c.save(); c.shadowColor = 'rgba(0,0,0,.85)'; c.shadowBlur = 2 * k; c.shadowOffsetY = k; c.drawImage(t, (zw - pw) / 2 + k, (H - ph) / 2 - k * 0.5); c.restore(); }
    iconCache.set(key, cv); return cv;
  }
  function drawChip(c, L, mode, n, sel, dim) {
    const H = L.s, W = H * CHW, x = L.x0 - W / 2, y = L.y0 - H / 2, f = L.alive / Math.max(1, G.u[L.i][5]), bh = Math.max(3, H * 0.16), k = H / 27;
    c.save(); if (dim) c.filter = 'brightness(45%) saturate(55%)';
    c.shadowColor = 'rgba(0,0,0,.55)'; c.shadowBlur = 3 * k; c.shadowOffsetY = 1.5 * k;
    c.drawImage(iconCanvas(L.i, mode), x, y, W, H); c.shadowColor = 'transparent';
    const bx = x + H * ZW, bw = H * BW;
    if (mode !== 'detect') text(c, String(n), bx + bw / 2, y + H * 0.7, { font: `600 ${Math.round(H * 0.56)}px "Oswald","FPC Roboto",sans-serif`, color: '#f3efe2' });
    if (mode !== 'detect') { c.fillStyle = 'rgba(10,10,9,.9)'; c.fillRect(x + 2 * k, y + H - k, W - 4 * k, bh + 2);
      const fw = Math.max(f > 0 ? 2 : 0, (W - 6 * k) * f); if (H4.str && fw > 0) c.drawImage(H4.str, 0, 0, H4.str.width, H4.str.height, x + 3 * k, y + H, fw, bh); else if (fw > 0) { c.fillStyle = '#c9874a'; c.fillRect(x + 3 * k, y + H, fw, bh); } }
    if (sel) { if (H4.sel) c.drawImage(H4.sel, x - 2 * k, y - 1.5 * k, W + 4 * k, H + 3 * k + bh); else { c.strokeStyle = YEL; c.lineWidth = 2; c.strokeRect(x - 2, y - 2, W + 4, H + bh + 4); } }
    c.restore();
    L.box = { x: x - 2, y: y - 2, w: W + 4, h: H + bh + 4 };
  }

  function drawCounters(c, v, g, view) {
    if (ZH) { const list = [...layout.values()].filter(L => L.top).sort((a, b) => (STG && FR.hl.has(a.i) ? 1 : 0) - (STG && FR.hl.has(b.i) ? 1 : 0) || a.y0 - b.y0);
      for (const L of list) { const mem = (L.stack || [L]).map(m => m.i), hl = STG && mem.some(i => FR.hl.has(i));
        drawChip(c, L, L.mode === 'own' || view == null ? 'own' : L.mode, L.n || 1, hl || mem.some(i => (S.selected || []).includes(i)), STG && FR.hl.size && !hl); }
      if (!STG) chipStrips(c, g, list, view);
      return; }
    const sel = new Set(S.selected || []); if (on('orders', v)) for (const q of opt('orders').selected || []) sel.add(q.unit ?? q);
    const hlTop = L => STG && FR.hl.has(L.i) ? 1 : 0;
    const list = [...layout.values()].sort((a, b) => hlTop(a) - hlTop(b) || (a.k || 0) - (b.k || 0) || a.y0 - b.y0);
    for (const L of list) {
      const i = L.i, cur = curOrder(i, g), letter = cur ? ORDER_LETTER[cur.o] || cur.o[0] : 'H';
      let dirKey = null;
      if (L.moving && L.hd != null) { const k = DIR_KIND[cur && cur.o] || 'deliberate', a = scr(L.x, L.z), b = scr(L.x + Math.sin(L.hd) * 0.2, L.z + Math.cos(L.hd) * 0.2); dirKey = `dir_${dirName(a[0], a[1], b[0], b[1])}_${k}`; }
      const cv = counterCanvas(i, L.mode === 'own' || view == null ? 'own' : L.mode, L.mode === 'detect' ? 0 : L.alive, dirKey, letter, sel.has(i) || (STG && FR.hl.has(i)));
      const s = L.s * (CS + 24) / CS;
      c.save(); if (STG && FR.hl.size && !FR.hl.has(i)) c.filter = 'brightness(42%) saturate(55%)';   // staging: the rest dims under a highlight
      c.shadowColor = 'rgba(0,0,0,.55)'; c.shadowBlur = L.s * 0.08; c.shadowOffsetX = L.s * 0.03; c.shadowOffsetY = L.s * 0.04;
      c.drawImage(cv, L.x0 - s / 2, L.y0 - s / 2, s, s); c.restore();
    }
    if (ZH && !STG) for (const L of list) { if ((L.k || 0) !== 0 || (L.mode !== 'own' && view != null)) continue; zhTag(c, L, g); }
    if (view != null && C.lost !== false) for (let i = 0; i < NU; i++) {
      if (G.u[i][1] === view || layout.has(i)) continue; const s0 = api.unitState(i, g); if (!s0 || s0.st !== 1) continue;
      const L = lostAt(i, view, g); if (!L) continue; const p = scr(L.x, L.z, 0.01); if (offscreen(p)) continue;
      const s = clamp(hexPxAt(L.x, L.z) * (S.counterScale ?? 0.74), S.minPx ?? 26, S.maxPx ?? 96), cv = counterCanvas(i, 'detect', 0, null, '', false), sz = s * (CS + 24) / CS;
      c.save(); c.globalAlpha = 0.42 * (1 - L.age / 700); c.drawImage(cv, p[0] - sz / 2, p[1] - sz / 2, sz, sz);
      c.globalAlpha = 0.8; c.setLineDash([s * 0.08, s * 0.06]); c.strokeStyle = '#fff'; c.lineWidth = Math.max(1, s * 0.03); c.strokeRect(p[0] - s / 2, p[1] - s / 2, s, s); c.restore();
    }
  }

  // ---------------------------------------------------------------- orders
  function hatchHex(c, h, col, a, dirn = 'h') {
    const poly = hexPoly(h); if (poly.some(p => p[2] > 1)) return;
    const xs = poly.map(p => p[0]), ys = poly.map(p => p[1]), x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys), step = Math.max(4, (y1 - y0) / 8);
    c.save(); pathPoly(c, poly); c.closePath(); c.clip(); c.strokeStyle = hexA(col, a); c.lineWidth = step * 0.55; c.beginPath();
    if (dirn === 'v') for (let x = x0; x <= x1; x += step) { c.moveTo(x, y0); c.lineTo(x, y1); }
    else for (let y = y0 + step / 2; y <= y1; y += step) { c.moveTo(x0, y); c.lineTo(x1, y); }
    c.stroke(); c.restore();
  }
  function arrowHead(c, p, q, len, wid, fill) {
    const a = Math.atan2(q[1] - p[1], q[0] - p[0]); c.save(); c.translate(q[0], q[1]); c.rotate(a); c.beginPath(); c.moveTo(len * 0.35, 0); c.lineTo(-len * 0.65, wid / 2); c.lineTo(-len * 0.65, -wid / 2); c.closePath(); c.fillStyle = fill; c.fill(); c.restore();
  }
  function screenPath(pts) { return chaikin(pts, 3).map(([x, z]) => scr(x, z, 0.012)); }
  function drawPaths(c, v, g) {
    const O = opt('orders'), selIds = (O.selected || []).map(q => q.unit ?? q), sel = new Set(selIds), who = O.paths ?? 'all';
    const ids = who === 'all' ? [...layout.keys()] : who === 'selected' ? selIds : who;
    const hints = [];
    for (const i of ids) {
      const L = layout.get(i); if (!L || L.mode !== 'own' && L.mode != null) continue;
      if (O.sides && !O.sides.includes(G.u[i][1])) continue;
      const plan = planOf(i, g, [L.x, L.z]); if (!plan.length) continue;
      const isSel = sel.has(i), hp = L.hp, w = INI.path.w * hp / 64 * (O.pathScale ?? 1.2), alpha = isSel ? INI.path.a : INI.path.a - (O.dim ?? 0.38);
      const all = []; for (const s of plan) all.push(...(all.length ? s.pts.slice(1) : s.pts));
      const sp = screenPath(all); if (sp.length < 2) continue;
      // wp markers, last hex highlighted grey (as after Commit)
      if (isSel) { const last = plan[plan.length - 1].wp, poly = hexPoly(last); c.save(); pathPoly(c, poly); c.closePath(); c.fillStyle = 'rgba(176,176,176,.55)'; c.fill(); c.restore(); }
      c.save(); c.lineCap = 'round'; c.lineJoin = 'round'; c.strokeStyle = hexA(INI.path.c, alpha); c.lineWidth = w; pathPoly(c, sp); c.stroke();
      // arrowheads along the path and a big one at the end
      let acc = 0; const every = hp * 1.6;
      for (let k = 1; k < sp.length - 2; k++) { acc += Math.hypot(sp[k][0] - sp[k - 1][0], sp[k][1] - sp[k - 1][1]); if (acc > every) { acc = 0; arrowHead(c, sp[k - 1], sp[k], w * 2.6, w * 2.4, hexA(INI.path.c, alpha)); } }
      arrowHead(c, sp[sp.length - 3] || sp[0], sp[sp.length - 1], w * 4, w * 3.6, hexA(INI.path.c, alpha));
      c.restore();
      if (isSel) {
        plan.forEach((s, k) => { const [x, z] = api.hexXZ(s.wp), p = scr(x, z, 0.012), b = Math.max(16, hp * 0.32);
          c.save(); c.fillStyle = '#b3c7a7'; c.strokeStyle = '#f4f4f4'; c.lineWidth = 1.5; c.fillRect(p[0] - b / 2, p[1] - b / 2, b, b); c.strokeRect(p[0] - b / 2, p[1] - b / 2, b, b);
          c.strokeStyle = '#26301f'; c.lineWidth = 1; c.strokeRect(p[0] - b / 2 - 1.5, p[1] - b / 2 - 1.5, b + 3, b + 3);
          text(c, String(k + 1), p[0], p[1] + b * 0.36, { font: `700 ${Math.round(b * 0.86)}px "FPC Roboto"`, color: '#111' }); c.restore();
          const qq = (O.selected || []).find(q => (q.unit ?? q) === i); if (qq && (qq.hint ?? true) && k === plan.length - 1) hints.push({ p, b, i, k, s }); });
      }
    }
    for (const h of hints) waypointHint(c, h, g);
  }
  function waypointHint(c, { p, b, i, k, s }, g) {
    const nm = G.u[i][0], l2 = `WP ${k + 1}: ${s.o.o}`, t0 = api.hhmm(s.o.s ?? g), t1 = api.hhmm(s.arrive ?? g);
    c.save(); c.font = '700 17px "FPC Roboto"'; const w = Math.max(c.measureText(nm).width, (c.font = '400 17px "FPC Roboto"', c.measureText(l2).width), 130) + 22, h = 74;
    const x = p[0] + b * 0.6, y = p[1] + b * 0.7;
    c.fillStyle = 'rgba(204,226,190,.94)'; c.strokeStyle = '#6f8a62'; c.lineWidth = 1; c.fillRect(x, y, w, h); c.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
    text(c, nm, x + 10, y + 21, { font: '700 17px "FPC Roboto"', align: 'left', color: '#1f2a1b' }); c.fillStyle = '#1f2a1b'; c.fillRect(x + 10, y + 24, c.measureText(nm).width, 1.4);
    text(c, l2, x + 10, y + 44, { font: '400 17px "FPC Roboto"', align: 'left', color: '#1f2a1b' });
    c.font = '400 17px "FPC Roboto"'; const a = `${t0} - `; text(c, a, x + 10, y + 66, { font: '400 17px "FPC Roboto"', align: 'left', color: '#1f2a1b' });
    text(c, t1, x + 10 + c.measureText(a).width, y + 66, { font: '700 17px "FPC Roboto"', align: 'left', color: '#1f2a1b' }); c.restore();
  }
  function fireMissions(g) {                       // active / pending barrages: [{ i, o, hex, first, end }]
    const out = [];
    for (let i = 0; i < NU; i++) { const s0 = api.unitState(i, g); if (!s0 || s0.st !== 1) continue;
      for (const o of queueAt(i, g)) { if (o.o !== 'Barrage' && !o.b) continue; const first = o.b ? o.b[2] : o.s, end = o.b ? first + o.b[3] * 60 : o.e;
        if (first == null || g > end + 60) continue; const hex = o.d || (o.tu != null ? hexAt(o.tu, Math.min(g, first)) : null); if (!hex) continue; out.push({ i, o, hex, first, end }); } }
    return out;
  }
  function drawFireMissions(c, v, g) {
    const O = opt('orders'); if (O.fireMissions === false) return;
    const list = fireMissions(g);
    for (const F of list) hatchHex(c, F.hex, INI.arty.c, INI.arty.a);
    for (const F of list) {
      const L = layout.get(F.i), [tx, tz] = api.hexXZ(F.hex), t = scr(tx, tz, 0.012); if (t[2] > 1) continue;
      const from = L ? [L.x0, L.y0] : (() => { const q = unitXZ(F.i, g); return q ? scr(q[0], q[1], 0.012) : null; })(); if (!from) continue;
      const hp = hexPxAt(tx, tz);
      c.save(); c.strokeStyle = hexA(INI.arty.c, INI.arty.a); c.lineWidth = Math.max(2, INI.fireW * hp / 64); c.beginPath(); c.moveTo(from[0], from[1]); c.lineTo(t[0], t[1]); c.stroke();
      // shooter designation along the line, near the target (orange italic, as in the game)
      const a = Math.atan2(t[1] - from[1], t[0] - from[0]), flip = Math.abs(a) > Math.PI / 2, d = Math.hypot(t[0] - from[0], t[1] - from[1]);
      c.translate(lerp(t[0], from[0], Math.min(0.5, hp * 1.4 / d)), lerp(t[1], from[1], Math.min(0.5, hp * 1.4 / d))); c.rotate(flip ? a + Math.PI : a);
      text(c, G.u[F.i][0], 0, -hp * 0.12, { font: `italic 700 ${Math.round(clamp(hp * 0.3, 14, 26))}px "FPC Roboto"`, color: hexA(INI.arty.c, 0.85) }); c.restore();
      const lb = F.o.b ? `${F.o.b[0]}, ${F.o.b[1]} rnds` : F.o.o, tm = `${api.hhmm(F.first)}-${api.hhmm(F.end)}`, fs = Math.round(clamp(hp * 0.27, 14, 24));
      text(c, lb, t[0], t[1] - fs * 0.15, { font: `700 ${fs}px "FPC Roboto"`, halo: 4 }); text(c, tm, t[0], t[1] + fs * 1.0, { font: `700 ${fs}px "FPC Roboto"`, halo: 4 });
    }
  }
  function drawBattlePlans(c, v, g) {
    for (const B of opt('orders').battleplans || []) {
      if (B.from != null && (v < B.from || v > B.to)) continue;
      const pts = (B.path || []).map(h => Array.isArray(h) ? h : api.hexXZ(h)); if (pts.length < 2) continue;
      const sp = chaikin(pts, 3).map(([x, z]) => scr(x, z, 0.012)); const hp = hexPxAt(...pts[0]);
      const col = (INI.plan[B.state || 'pending'] || INI.plan.pending)[B.side ?? 0], half = INI.plan.arrowW / 2 * hp / 64 * (B.scale ?? 1);
      const L = [], R = [];
      const n = sp.length, headLen = half * 2.6;
      let tot = 0; const cum = [0]; for (let k = 1; k < n; k++) { tot += Math.hypot(sp[k][0] - sp[k - 1][0], sp[k][1] - sp[k - 1][1]); cum.push(tot); }
      let kEnd = n - 1; while (kEnd > 1 && tot - cum[kEnd] < headLen) kEnd--;
      for (let k = 0; k <= kEnd; k++) { const a = sp[Math.max(0, k - 1)], b = sp[Math.min(n - 1, k + 1)], dx = b[0] - a[0], dy = b[1] - a[1], m = Math.hypot(dx, dy) || 1;
        L.push([sp[k][0] - dy / m * half, sp[k][1] + dx / m * half]); R.push([sp[k][0] + dy / m * half, sp[k][1] - dx / m * half]); }
      const e = sp[n - 1], b0 = sp[kEnd], dx = e[0] - b0[0], dy = e[1] - b0[1], m = Math.hypot(dx, dy) || 1;
      const hl = [b0[0] - dy / m * half * 2, b0[1] + dx / m * half * 2], hr = [b0[0] + dy / m * half * 2, b0[1] - dx / m * half * 2];
      c.save(); c.beginPath(); L.forEach((p, k) => k ? c.lineTo(...p) : c.moveTo(...p)); c.lineTo(...hl); c.lineTo(e[0], e[1]); c.lineTo(...hr); for (let k = R.length - 1; k >= 0; k--) c.lineTo(...R[k]); c.closePath();
      c.fillStyle = hexA(col, (B.fill ?? 0.55) * INI.plan.a); c.fill(); if (B.outline) { c.strokeStyle = hexA(col, INI.plan.a); c.lineWidth = INI.plan.lineW * hp / 64; c.stroke(); }
      if (B.label) { const q = sp[Math.floor(n * 0.45)]; text(c, B.label, q[0], q[1], { font: `400 ${Math.round(hp * 0.55)}px "FPC Marker"`, color: hexA(col, 0.95), halo: 3, haloC: 'rgba(0,0,0,.6)' }); }
      c.restore();
    }
    for (const A of opt('orders').objectives || []) {      // objective areas: filled hexes + marker-pen name (Obj Lion)
      if (A.from != null && (v < A.from || v > A.to)) continue; const col = (INI.plan[A.state || 'pending'] || INI.plan.pending)[A.side ?? 0];
      for (const h of A.hexes) { const poly = hexPoly(h); c.save(); pathPoly(c, poly); c.closePath(); c.fillStyle = hexA(col, 0.45); c.fill(); c.restore(); }
      if (A.label) { const [x, z] = api.hexXZ(A.hexes[0]), p = scr(x, z), hp = hexPxAt(x, z); text(c, A.label, p[0], p[1], { font: `400 ${Math.round(hp * 0.6)}px "FPC Marker"`, color: hexA(col, 0.95), halo: 3, haloC: 'rgba(0,0,0,.6)' }); }
    }
  }

  // ---------------------------------------------------------------- markers
  function markerFile(m) {
    const own = m.owner == null ? 'Y' : ['B', 'R', 'Y'][m.owner] || m.owner, lo = own.toLowerCase();
    const full = { Minefield: `Minefield_${own}`, LightMinefield: 'Light_Minefield_y', ReducedMinefield: `ReducedMinefield_${lo}`, Obstacle: `Obstacle_${own}`, ReducedObstacle: `ReducedObstacle_${lo}`,
      IP: `IP_${own}`, ReducedIP: `Reduced_IP_${lo}`, Fort: `Fort_${own}`, ReducedFort: `reduced_fort_${lo}`, Smoke: 'Smoke', SmokeN: 'smoke_n', SmokeT: 'smoke_t', SmokeMS: 'smoke_ms',
      Chem: m.owner == null ? 'Chem' : `Chem_${lo}`, Gas: 'Gas', Bio: m.owner == null ? 'Bio' : `Bio_${lo}`, Radiation: m.owner == null ? 'Radiation' : `Radiation_${lo}`, Rubble: 'Rubble',
      TRP: `TRP_${own === 'Y' ? 'B' : own}`, PPF: `PPF_${own}`, Exit: `Exit_${own === 'Y' ? 'B' : own}`, Civ: 'Civ_Refugees', Crater: 'Crater1', Brew: `Brew_${own === 'Y' ? 'B' : own}`,
      Kill: `Kill_${own === 'Y' ? 'B' : own}`, HeloWreck: `HeloWreck_${own === 'Y' ? 'B' : own}` };
    if (m.edge) return `${{ Minefield: 'minefield', Obstacle: 'obstacle', IP: 'ip', Fort: 'fort' }[m.kind] || m.kind.toLowerCase()}_side_${m.edge}_${lo}`;
    if (m.kind === 'Bridge' || m.kind === 'BlownBridge' || m.kind === 'ConstructBridge') { const base = { Bridge: 'bridge', BlownBridge: 'blownbridge', ConstructBridge: 'constructbridge' }[m.kind] + (m.dir || 'NS');
      return m.owner == null && m.kind !== 'ConstructBridge' ? base : `${base}_${lo === 'y' ? 'b' : lo}`; }
    return full[m.kind] || m.kind;
  }
  // bridges: a road spoke crossing a hex side that carries water (river / stream edge of this hex or of its neighbour)
  const T = api.T, edgeSet = new Set();
  for (const L of [T.rv || [], T.st || []]) for (const [h, m] of L) for (let k = 0; k < 6; k++) if (m >> k & 1) edgeSet.add(h + ':' + k);
  const roads = new Map(); for (const L of [T.pr || [], T.sr || [], T.hw || []]) for (const [h, m] of L) roads.set(h, (roads.get(h) || 0) | m);
  const BRIDGES = [], bseen = new Set();
  for (const [h, m] of roads) for (let k = 0; k < 6; k++) { if (!(m >> k & 1)) continue; const nb = api.neighbours(h)[k];
    const water = edgeSet.has(h + ':' + k) || (nb && edgeSet.has(nb + ':' + (k + 3) % 6)); if (!water) continue;
    const key = [Math.min(h, nb || 0), Math.max(h, nb || 0)].join('-'); if (bseen.has(key)) continue; bseen.add(key);
    BRIDGES.push({ h, k, dir: ['NS', 'NE', 'SE'][k % 3] }); }
  for (const b of S.mapMarkers || []) if (b.kind === 'BlownBridge' && b.hex) for (const B of BRIDGES) if (B.h === b.hex && (b.k == null || b.k === B.k)) B.blown = b.from ?? 0;
  const wrecksAt = []; {
    const R = (s => () => (s = (s * 16807) % 2147483647) / 2147483647)(12345);
    for (const l of api.lossSchedule) { const xz = l.ix != null ? [l.ix, l.iz] : unitXZ(l.unit, l.g); if (!xz) continue; const u = uinfo[l.unit], soft = /^INF|^ENGR|^UTY|HQ/.test(u.sut || '') || ['Inf', 'AT', 'Eng'].includes(u.type);
      for (let k = 0; k < Math.min(3, l.n); k++) wrecksAt.push({ g: l.g, x: xz[0] + (R() - 0.5) * 0.12, z: xz[1] + (R() - 0.5) * 0.1, f: (u.type === 'Helo' ? 'HeloWreck_' : soft ? 'Kill_' : 'Brew_') + (l.side ? 'R' : 'B') }); }
  }
  function drawMarkers(c, v, g) {
    const M = opt('markers');
    // bridges
    if (M.bridges !== false) for (const B of BRIDGES) { const [x, z] = api.hexXZ(B.h), mid = api.MID[B.k], p = scr(x + mid[0], z + mid[1], 0.006); if (offscreen(p)) continue;
      const hp = hexPxAt(x, z), blown = B.blown != null && v >= B.blown, im = MK[(blown ? 'blownbridge' : 'bridge') + B.dir], s = hp * 0.62;
      if (im) c.drawImage(im, p[0] - s / 2, p[1] - s / 2, s, s); }
    // craters where shells landed, wreck / casualty markers where subunits died (they stay, as in the game)
    if (M.craters !== false) for (const sh of api.shotSchedule) { if (sh.ix == null || g < sh.g) continue; const p = scr(sh.ix, sh.iz, 0.004); if (offscreen(p)) continue;
      const s = hexPxAt(sh.ix, sh.iz) * 0.16, im = MK['Crater' + (1 + (sh.seed % 5))]; if (im) c.drawImage(im, p[0] - s / 2, p[1] - s / 2, s, s); }
    if (M.wrecks !== false) for (const w of wrecksAt) { if (g < w.g) continue; const p = scr(w.x, w.z, 0.004); if (offscreen(p)) continue; const s = hexPxAt(w.x, w.z) * 0.3, im = MK[w.f]; if (im) c.drawImage(im, p[0] - s / 2, p[1] - s / 2, s, s); }
    // extra map markers from the shot (no such data in the replay): minefields, obstacles, IP, smoke, TRP ...
    for (const m of S.mapMarkers || []) { if (m.kind === 'BlownBridge') continue; if (m.from != null && (v < m.from || v > (m.to ?? 1e9))) continue;
      const im = MK[m.file || markerFile(m)]; if (!im) continue; const [x, z] = api.hexXZ(m.hex), p = scr(x, z, 0.006); if (offscreen(p)) continue; const hp = hexPxAt(x, z);
      const s = hp * (m.scale ?? (im.width > im.height ? 1.16 : im.width <= 100 ? 0.4 : 0.9)); c.drawImage(im, p[0] - s / 2, p[1] - s * im.height / im.width / 2, s, s * im.height / im.width);
      if (m.label) text(c, m.label, p[0], p[1] + s * 0.5, { font: `700 ${Math.round(hp * 0.22)}px "FPC Roboto"`, halo: 3 }); }
    if (!vpOnTop(v)) drawVP(c, v, g);
  }
  // VP markers (objective value written on the badge); under the counters as in the game, or over them while
  // markers.vpOnTop (a [from, to] list or true) asks for it, so a demo can show badges sitting under a stack
  const vpOnTop = v => { const t = opt('markers').vpOnTop; return t === true || (Array.isArray(t) && t.some(([a, b]) => v >= a && v <= b)); };
  function drawVP(c, v, g, cueOnly = false) {
    const M = opt('markers');
    if (M.vp !== false) for (const [h, val, o0] of G.vp || []) { if (cueOnly && !FR.vp.has(h)) continue; const own = api.vpOwner(h, o0, g), [x, z] = api.hexXZ(h), p = scr(x, z, 0.006); if (offscreen(p)) continue;
      const hp = hexPxAt(x, z), s = STG ? clamp(hp * 0.45, 24, 46) : hp * (M.vpScale ?? 0.86), im = MK[['VP_B', 'VP_R', 'VP_Y'][own] || 'VP_Y'];
      if (cueOnly) {                                   // staging: the value on a plate at the hex's top edge, over the counters
        // 11-08 phone pass: 22 -> 32 px
        const Q = FR.vp.get(h), t = Q.label || String(val); c.save(); c.globalAlpha = Q.a; c.font = `600 32px "Oswald","Noto Sans SC",sans-serif`; const w = c.measureText(t).width + 58, y = Math.max(TOP_Y + 4, p[1] - hp * 0.62 - 48);
        nine(c, H4.mini, p[0] - w / 2, y, w, 48, 12, 9);
        c.drawImage(im, p[0] - w / 2 + 9, y + 8, 32, 32); text(c, t, p[0] + 17, y + 37, { font: `600 32px "Oswald","Noto Sans SC",sans-serif`, color: '#e9c35a' }); c.restore(); continue; }
      c.drawImage(im, p[0] - s / 2, p[1] - s / 2, s, s);
      if (STG && !FR.vp.has(h)) continue;                 // staging: owner colour only; the value comes with a hex cue
      if (STG) c.globalAlpha = FR.vp.get(h).a;
      const lbl = STG && FR.vp.get(h).label ? FR.vp.get(h).label : M.vpValues ? M.vpValues(h, val) : String(val), fs = Math.round(s * (lbl.length > 3 ? 0.27 : 0.32));
      text(c, lbl, p[0], p[1] + fs * 0.36, { font: `700 ${fs}px "FPC Roboto"`, color: own === 1 ? '#fff' : '#fff', halo: Math.max(2, fs * 0.16) }); c.globalAlpha = 1; }
  }

  // ---------------------------------------------------------------- fire lines and effect sequences
  const FXFPS = S.fxFps ?? 30;
  const styleOf = k => ({ Tank: 'Gun', AT: 'ATGM', 'SP AT': 'ATGM', Helo: 'ATGM' })[k] || 'AutoCannon';
  function drawSeq(c, name, age, x, y, size, fps = FXFPS) {
    const A = FX[name]; if (!A || !A.im) return false; const f = Math.floor(age * fps); if (f < 0 || f >= A.n) return false;
    const cx = f % A.cols, cy = Math.floor(f / A.cols); c.drawImage(A.im, cx * A.w, cy * A.h, A.w, A.h, x - size / 2, y - size / 2, size, size); return true;
  }
  function smokePuff(c, x, y, r, a) { const g2 = c.createRadialGradient(x, y, 0, x, y, r); g2.addColorStop(0, `rgba(255,255,255,${a})`); g2.addColorStop(1, 'rgba(255,255,255,0)'); c.fillStyle = g2; c.beginPath(); c.arc(x, y, r, 0, 7); c.fill(); }
  function posOf(i, g) { const L = layout.get(i); if (L) return [L.x0, L.y0, L.hp]; const q = unitXZ(i, g); if (!q) return null; const p = scr(q[0], q[1], 0.01); return offscreen(p) ? null : [p[0], p[1], hexPxAt(q[0], q[1])]; }
  // the shot's time map may jump between segments (one per category) while the sandbox's fire schedule assumes a
  // monotonic map: fire is timed inside the segment playing now (v of game second gx in that segment)
  function segV(gx, v) {
    const k = api.SHOT.time;
    for (let i = 1; i < k.length; i++) { const [v0, g0] = k[i - 1], [v1, g1] = k[i]; if (v1 <= v0 || v < v0 || v > v1) continue; return g1 === g0 ? null : v0 + (gx - g0) / (g1 - g0) * (v1 - v0); }
    return api.vOf(gx);
  }
  const foeCache = new Map();
  function nearestFoe(i, g) {                         // the replay names targets only for shots that caused a loss
    const key = i + ':' + Math.round(g / 30); if (foeCache.has(key)) return foeCache.get(key);
    const a = unitXZ(i, g); let best = null, bd = opt('fire').range ?? 3.5;
    if (a) for (let j = 0; j < NU; j++) { if (G.u[j][1] === G.u[i][1]) continue; const s = api.unitState(j, g); if (!s || s.st !== 1 || !s.alive || isNaN(s.x)) continue;
      const d = Math.hypot(s.x - a[0], s.z - a[1]); if (d < bd) { bd = d; best = j; } }
    foeCache.set(key, best); return best;
  }
  function drawFire(c, v, g, filt = null) {
    const Fo = opt('fire'), classic = Fo.style === 'classic', FR = api.FPS;
    for (const sh of api.shotSchedule) {
      if (api.BARRAGE(sh.kind) || (filt && !filt(sh))) continue;
      const vL = segV(sh.g, v); if (vL == null) continue; const age = v - (vL - 0.55 - sh.k * 0.16); if (age < 0 || age > 2.4) continue;
      const gL = sh.g, a = posOf(sh.from, gL); if (!a) continue;
      let tgt = sh.target >= 0 ? sh.target : nearestFoe(sh.from, gL); const b0 = tgt != null ? posOf(tgt, gL) : null; if (!b0) continue;
      const R = (s => () => (s = (s * 16807) % 2147483647) / 2147483647)(sh.seed % 2147483646 + 1);
      const ppm = a[2] / 500, side = sh.side, col = INI.fire[side];
      const miss = !sh.hitLoss, off = miss ? [(R() - 0.5) * a[2] * 0.7, (R() - 0.5) * a[2] * 0.5] : [0, 0], b = [b0[0] + off[0], b0[1] + off[1]];
      const dx = b[0] - a[0], dy = b[1] - a[1], D = Math.hypot(dx, dy) || 1, ux = dx / D, uy = dy / D, Dm = D / ppm;
      const st = classic ? 'Classic' : styleOf(sh.kind), Pm = INI.lof[st];
      // flight time from the ini speed (metres per animation frame at the game's 30 fps-ish tick), capped for the video
      const flight = st === 'Classic' ? 0.6 : clamp(Dm / (Pm.speed * 30) * 6, 0.35, st === 'ATGM' ? 1.4 : 0.9);
      c.save(); c.lineCap = 'butt';
      if (st === 'Classic') { if (age < flight) { c.strokeStyle = hexA(col.c, col.a); c.lineWidth = Pm.w * ppm * 1.4; c.beginPath(); c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); c.stroke(); } }
      else {
        const life = flight + 0.35;
        if (age < life) { c.strokeStyle = `rgba(255,255,255,${(Pm.bg[0] / 255) * (age < flight ? 1 : 1 - (age - flight) / 0.35)})`; c.lineWidth = Math.max(2, Pm.bg[1] * ppm); c.beginPath(); c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); c.stroke(); }
        const wig = s => st === 'ATGM' ? Math.sin(s * 9 + sh.seed) * 0.05 * D * Math.sin(Math.PI * s) * (1 - s * 0.6) : 0;
        const at = s => [a[0] + dx * s - uy * wig(s), a[1] + dy * s + ux * wig(s)];
        const shots = Pm.n || 1;
        for (let n = 0; n < shots; n++) {
          const sAge = age - n * (Pm.gap ? Pm.gap / (Pm.speed * 30) * 6 * 0.5 : 0); if (sAge < 0) continue;
          const f = sAge / flight;
          // vapour trail: thin, semi-transparent, fades behind the projectile
          if (st !== 'ATGM' && Pm.vw && f < 1.3) { const tail = clamp(f - Pm.vl / Dm, 0, 1), head = clamp(f, 0, 1); const p0 = at(tail), p1 = at(head);
            const gr = c.createLinearGradient(p0[0], p0[1], p1[0], p1[1]); gr.addColorStop(0, hexA(col.c, 0)); gr.addColorStop(1, hexA(col.c, col.a * 0.6 * (f < 1 ? 1 : 1 - (f - 1) / 0.3)));
            c.strokeStyle = gr; c.lineWidth = Math.max(1, Pm.vw * ppm); c.beginPath(); c.moveTo(p0[0], p0[1]); c.lineTo(p1[0], p1[1]); c.stroke(); }
          if (st === 'ATGM') {                       // wiggly smoke trail that vanishes, rocket burn behind a fat projectile
            for (let s = 0; s < Math.min(1, f); s += 0.025) { const life2 = (f - s) * flight; const q = at(s), aa = (Pm.smoke[2] / 255) * Math.max(0, 1 - life2 / (Pm.smoke[1] / 30 * 4)); if (aa > 0.01) smokePuff(c, q[0], q[1], Math.max(2, (Pm.smoke[0] + life2 * 30) * ppm), aa * 0.6); }
          }
          if (f >= 0 && f <= 1) {
            const len = Math.min(Pm.pl / Dm, 0.5), p0 = at(Math.max(0, f - len)), p1 = at(f);
            if (st === 'ATGM') { const bl = Pm.burnL / Dm * 2.4; Pm.burn.forEach((bc, k) => { const q0 = at(Math.max(0, f - len - bl * (k + 1) * 0.5)), q1 = at(Math.max(0, f - len)); c.strokeStyle = bc; c.lineWidth = Math.max(2, Pm.burnW * ppm * (1 - k * 0.25)); c.beginPath(); c.moveTo(q0[0], q0[1]); c.lineTo(q1[0], q1[1]); c.stroke(); }); }
            c.strokeStyle = hexA(col.c, Math.min(1, col.a + 0.25)); c.lineWidth = Math.max(2, Pm.pw * ppm * (st === 'ATGM' ? 1.3 : 1)); c.lineCap = 'round'; c.beginPath(); c.moveTo(p0[0], p0[1]); c.lineTo(p1[0], p1[1]); c.stroke(); c.lineCap = 'butt';
          }
          // muzzle / launcher smoke at the shooter
          const ms = st === 'ATGM' ? Pm.launch : Pm.smoke, mAge = sAge * FR; if (mAge < ms[1] * 2) { const t = mAge / (ms[1] * 2), r = (ms[0] + 18 * t * (st === 'Gun' ? 2 : 1)) * ppm * (st === 'Gun' ? 2.2 : 1.2);
            smokePuff(c, a[0] + ux * r * 0.6, a[1] + uy * r * 0.6, Math.max(4, r), (lerp(ms[2], ms[3], t) / 255) * (1 - t * 0.6)); }
        }
      }
      c.restore();
      // impact: Hit sequence on the target counter, Miss sequence where the round lands
      const ia = age - flight; if (ia >= 0) drawSeq(c, (miss ? 'Miss ' : 'Hit ') + (1 + sh.seed % 6), ia, b[0], b[1], (miss ? 0.85 : 1.15) * b0[2]);
      if (Fo.hints && st === 'ATGM' && age < 1.2 && (Fo.hintUnits ? Fo.hintUnits.includes(sh.from) : true)) combatHint(c, a[0], a[1] - a[2] * 0.6, 'ATGM Launch!!');
    }
    // artillery: the game's bitmap sequences on every shell
    for (const sh of api.shotSchedule) {
      if (!api.BARRAGE(sh.kind) || sh.ix == null || (filt && !filt(sh))) continue; const vI = segV(sh.g, v); if (vI == null) continue; const age = v - vI - sh.k * 0.12; if (age < 0 || age > 6) continue;
      const p = scr(sh.ix, sh.iz, 0.01); if (offscreen(p)) continue; const hp = hexPxAt(sh.ix, sh.iz), u = uinfo[sh.from];
      const mortar = /mortar|mtr/i.test(u.plat + ' ' + u.name), rkt = /RM-70|BM|MLRS|Rkt|rocket/i.test(u.plat + ' ' + u.name);
      const name = mortar ? `Mortar ${1 + sh.seed % 4}` : rkt ? `MRLS Barrage ${1 + sh.seed % 2}` : `Artillery Stonk Short ${1 + sh.seed % 4}`;
      drawSeq(c, name, age, p[0], p[1], hp * (mortar ? 0.6 : 1.25));
    }
    // effects from the shot table (any sequence: Smoke 1, Airstrike 1, Illumination, Unit Surrender, FP Nuke ...)
    for (const e of S.effects || []) { const age = v - e.v; if (age < 0) continue; const xz = e.unit != null ? unitXZ(e.unit, g) : api.hexXZ(e.hex); if (!xz) continue;
      const p = scr(xz[0], xz[1], 0.01); if (offscreen(p)) continue; const hp = hexPxAt(xz[0], xz[1]); const A = FX[e.kind]; if (!A) continue;
      const loopAge = e.loop ? age % (A.n / (e.fps || FXFPS)) : age; if (e.to != null && v > e.to) continue;
      drawSeq(c, e.kind, loopAge, p[0], p[1], hp * (e.scale ?? A.w / 64)); if (e.label && age < (e.labelFor ?? 3)) combatHint(c, p[0], p[1] - hp * 0.8, e.label); }
  }
  function combatHint(c, x, y, s) {
    c.save(); c.font = '700 17px "FPC Roboto"'; const w = c.measureText(s).width + 20, h = 28; const r = 7;
    c.fillStyle = 'rgba(214,236,200,.95)'; c.strokeStyle = '#5d7a52'; c.lineWidth = 1.2; c.beginPath(); c.roundRect(x - w / 2, y - h, w, h, r); c.fill(); c.stroke();
    text(c, s, x, y - 8, { font: '700 17px "FPC Roboto"', color: '#24301f' }); c.restore();
  }

  // ---------------------------------------------------------------- panels (after the game's Delphi windows)
  const PN = { title: '#5b6b52', titleTx: '#ffffff', body: '#484848', inner: '#434a43', line: '#2c2c2c', text: '#f2f2f2', dim: '#c9c9c9', sel: '#2aa0f7', yellow: '#ffe066', green: '#18c03a' };
  function panel(c, x, y, w, h, title) {
    c.save(); c.fillStyle = 'rgba(0,0,0,.45)'; c.fillRect(x + 3, y + 4, w, h); c.fillStyle = PN.body; c.fillRect(x, y, w, h); c.fillStyle = PN.title; c.fillRect(x, y, w, 30);
    c.strokeStyle = PN.line; c.lineWidth = 1; c.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
    let fs = 17, tt = title; c.font = `400 ${fs}px "FPC Roboto"`; while (c.measureText(tt).width > w - 48 && fs > 13) { fs--; c.font = `400 ${fs}px "FPC Roboto"`; }
    while (c.measureText(tt).width > w - 48 && tt.length > 4) tt = tt.slice(0, -2) + '…';
    text(c, tt, x + 10, y + 21, { font: `400 ${fs}px "FPC Roboto"`, align: 'left', color: PN.titleTx });
    c.drawImage(PANEL_IMG.popout, x + w - 27, y + 4, 22, 22); c.restore();
  }
  function dot(c, x, y, kind, s = 14) { c.drawImage(STATUS[kind], x - s / 2, y - s / 2, s, s); }
  const lvl = (p, good) => p >= good ? 'green' : p >= good - 25 ? 'yellow' : 'red';
  const avg = (side, key, g) => { let s = 0, n = 0; for (let i = 0; i < NU; i++) { if (G.u[i][1] !== side) continue; const st = api.unitState(i, g); if (!st || st.st !== 1 || !st.alive) continue; const x = series(key, i, g); if (x == null) continue; s += x; n++; } return n ? s / n : null; };
  const nextWake = (side, g) => { const w = ((FD && FD.wake) || []).filter(x => x[1] === side).map(x => x[0]).sort((a, b) => a - b); return w.find(t => t > g) ?? null; };
  function resultName(sh) { const s = Math.round(sh); if (s >= 80) return 'Decisive Win'; if (s >= 65) return 'Tactical Win'; if (s >= 55) return 'Marginal Win'; if (s > 45) return 'Contested'; if (s > 35) return 'Marginal Loss'; if (s > 20) return 'Tactical Loss'; return 'Decisive Loss'; }
  const DATE = FD ? new Date(FD.base.replace(' ', 'T')) : null;
  function clockLong(g) { const m = Math.floor(g / 60) + api.H0; const hh = String(Math.floor(m / 60) % 24).padStart(2, '0'), mm = String(m % 60).padStart(2, '0');
    if (!DATE) return `${hh}:${mm} hrs`; const mon = DATE.toLocaleString('en-US', { month: 'short' }); return `${hh}:${mm} hrs, ${mon} ${DATE.getDate()} ${DATE.getFullYear()}`; }
  function gameControl(c, x, y, v, g) {
    const w = 400, h = 214; panel(c, x, y, w, h, 'Game Resolution .');
    text(c, clockLong(g), x + 18, y + 64, { font: '400 25px "FPC Roboto"', align: 'left', color: PN.text });
    const el = Math.max(0, Math.floor(g / 60)), tot = Math.round(G.T / 60);
    text(c, `${Math.floor(el / 60)}:${String(el % 60).padStart(2, '0')} of ${Math.floor(tot / 60)}:${String(tot % 60).padStart(2, '0')} Elapsed`, x + 40, y + 90, { font: '400 18px "FPC Roboto"', align: 'left', color: PN.dim });
    // Pause button
    c.save(); c.fillStyle = '#3c3c3c'; c.strokeStyle = '#9a9a9a'; c.lineWidth = 1.5; c.beginPath(); c.roundRect(x + w - 128, y + 42, 112, 62, 6); c.fill(); c.stroke(); c.restore();
    text(c, 'Pause', x + w - 72, y + 82, { font: '700 22px "FPC Roboto"', color: PN.text });
    // Command Cycle Chronology: -1 hr .. Now .. +1 hr, own side above (green/blue), enemy below (red; italic = estimate)
    const tx = x + 14, tw = 270, ty = y + 128, side = opt('panels').side ?? 0;
    text(c, '-1 hr', tx, ty - 6, { font: '400 13px "FPC Roboto"', align: 'left', color: PN.dim }); text(c, 'Now', tx + tw / 2, ty - 6, { font: '400 13px "FPC Roboto"', color: PN.dim }); text(c, '+1 hr', tx + tw, ty - 6, { font: '400 13px "FPC Roboto"', align: 'right', color: PN.dim });
    c.save(); c.strokeStyle = '#cfcfcf'; c.lineWidth = 1; c.beginPath(); for (let k = 0; k <= 12; k++) { const xx = tx + tw * k / 12; c.moveTo(xx, ty); c.lineTo(xx, ty + (k % 6 ? 4 : 8)); } c.stroke(); c.restore();
    const rows = [side, 1 - side].map(sd => ((FD && FD.wake) || []).filter(q => q[1] === sd).map(q => q[0]).sort((a, b) => a - b));
    rows.forEach((ws, r) => {
      const yy = ty + 8 + r * 26, base = r === 0 ? ['#2e7d4f', '#56a06f'] : ['#8c2a2a', '#c96a6a'];
      for (let k = 0; k < ws.length; k++) { const t0 = ws[k], t1 = ws[k + 1] ?? G.T; const a0 = (t0 - g) / 3600, a1 = (t1 - g) / 3600; if (a1 < -0.5 || a0 > 0.5) continue;
        const x0 = tx + tw * (clamp(a0, -0.5, 0.5) + 0.5), x1 = tx + tw * (clamp(a1, -0.5, 0.5) + 0.5); if (x1 - x0 < 2) continue;
        const future = t0 > g; c.fillStyle = future ? base[1] : base[0]; c.fillRect(x0 + 1, yy, x1 - x0 - 2, 22);
        const mins = Math.round((t1 - t0) / 60); if (x1 - x0 > 22) text(c, String(mins), (x0 + x1) / 2, yy + 17, { font: `${r === 1 && future ? 'italic ' : ''}700 15px "FPC Roboto"`, color: '#fff' }); }
    });
    c.save(); c.strokeStyle = '#ff2020'; c.lineWidth = 2; c.beginPath(); c.moveTo(tx + tw / 2, ty + 6); c.lineTo(tx + tw / 2, ty + 62); c.stroke(); c.restore();
    text(c, 'Command Cycle', tx + tw + 12, ty + 22, { font: '400 14px "FPC Roboto"', align: 'left', color: PN.dim }); text(c, 'Chronology', tx + tw + 12, ty + 40, { font: '400 14px "FPC Roboto"', align: 'left', color: PN.dim });
  }
  function commander(c, x, y, v, g, side) {
    const Pn = opt('panels'), w = 330, h = 262, cm = (Pn.commanders || [])[side] || {}, form = cm.formation || (side === 0 ? 'Blue' : 'Red');
    panel(c, x, y, w, h, `${form} Command HQ`);
    text(c, cm.name || '', x + 12, y + 56, { font: '700 19px "FPC Roboto"', align: 'left', color: PN.text });
    const bd = PANEL_IMG['badge' + side]; if (bd) { const s = 92 / Math.max(bd.width, bd.height); c.drawImage(bd, x + 16, y + 66, bd.width * s, bd.height * s); }
    const fl = PANEL_IMG['flag' + side]; if (fl) { const s = 40 / fl.height; c.drawImage(fl, x + 128, y + 44, fl.width * s, 40); }
    const nw = nextWake(side, g);
    text(c, 'Orders in:', x + 128, y + 104, { font: '400 16px "FPC Roboto"', align: 'left', color: PN.text });
    text(c, nw == null ? 'Now' : `${Math.max(0, Math.ceil((nw - g) / 60))} mins`, x + 128, y + 136, { font: '700 30px "FPC Roboto"', align: 'left', color: PN.text });
    text(c, nw == null ? '' : `At ${api.hhmm(nw)} hrs`, x + 128, y + 158, { font: '400 15px "FPC Roboto"', align: 'left', color: PN.text });
    const sc = api.scoreAt(g), force = side === 0 ? sc.fb : sc.fr, rd = avg(side, 'rd', g), share = side === 0 ? sc.share : 100 - sc.share;
    const row = (yy, a, b) => { text(c, a, x + 92, yy, { font: '700 16px "FPC Roboto"', align: 'right', color: PN.text }); text(c, b, x + 108, yy, { font: '400 16px "FPC Roboto"', align: 'left', color: PN.text }); };
    row(y + 188, `${Math.round(force)} %`, 'of Starting Force VPs');
    row(y + 212, rd == null ? '—' : `${Math.round(rd)} %`, 'Average Readiness');
    const res = Pn.result ? Pn.result(g, side) : resultName(share);
    text(c, res, x + 92, y + 240, { font: '700 16px "FPC Roboto"', align: 'right', color: PN.text });
    const col = /Win/.test(res) ? PN.green : /Loss/.test(res) ? '#d33' : '#e6d23c'; c.fillStyle = col; c.fillRect(x + 108, y + 228, Math.round(170 * clamp(share / 100, 0.08, 1)), 14);
    // right-side buttons
    ['HQ', 'Ops', 'Int', 'PL', 'FS', 'OMA', 'SI'].forEach((b, k) => { const bx = x + w - 46, by = y + 40 + k * 31; c.save(); c.fillStyle = '#3d3d3d'; c.strokeStyle = '#a8a8a8'; c.beginPath(); c.roundRect(bx, by, 36, 22, 4); c.fill(); c.stroke(); c.restore();
      text(c, b, bx + 18, by + 16, { font: '400 13px "FPC Roboto"', color: PN.text }); });
  }
  function dashboard(c, x, y, v, g, i) {
    const w = 660, h = 300, u = G.u[i], s0 = api.unitState(i, g); if (!s0) return;
    const L = layout.get(i), alive = L ? L.alive : s0.alive, cur = curOrder(i, g);
    panel(c, x, y, w, h, `${u[0]} - ${uinfo[i].plat ? uinfo[i].plat.replace(/\[.*?\]/g, '') : u[2]} ${{ '.': 'Section', '..': 'Squad', '...': 'Platoon', I: 'Company', II: 'Battalion', III: 'Regiment' }[u[3]] || ''}   [F4]`);
    const cv = counterCanvas(i, 'own', alive, null, cur ? ORDER_LETTER[cur.o] || cur.o[0] : 'H', false, true); c.drawImage(cv, x + 6, y + 36, 100, 100);
    const n = alive, pl = (uinfo[i].plat || u[2]).replace(/\[.*?\]/g, '');
    text(c, `${n} x ${pl} (${u[5]} x ${u[2]}), in hex ${String(s0.hex).padStart(4, '0')}`, x + 118, y + 58, { font: '400 16px "FPC Roboto"', align: 'left', color: PN.text });
    const tr = uinfo[i].tr; text(c, `Training ${tr ?? '—'}${cur ? `, ${cur.o} order` : ''}.`, x + 118, y + 80, { font: '400 16px "FPC Roboto"', align: 'left', color: PN.text });
    const fired = (COMBAT.get(i) || []).some(t => t <= g && g - t < 600);
    text(c, fired ? `In combat as of ${api.hhmm(g)} hrs.` : `Not under fire as of ${api.hhmm(g)} hrs.`, x + 118, y + 102, { font: '400 16px "FPC Roboto"', align: 'left', color: PN.text });
    const am = series('am', i, g), rd = series('rd', i, g), mo = series('mo', i, g), dl = (() => { const d = delays[i]; const k = api.rowAt(d, g); return k >= 0 ? d[k][1] : null; })();
    // range to HQ: hexes to the parent HQ unit
    let hq = '—'; const par = u[4] >= 0 ? G.pa[u[4]] : null, pi = par ? G.u.findIndex(q => q[0] === par) : -1;
    if (pi >= 0) { const a = api.unitState(pi, g); if (a && a.st === 1 && !isNaN(a.x)) hq = String(Math.round(Math.hypot(a.x - s0.x, a.z - s0.z) / 0.5)); }
    const rows = [['Key Ammo:', am, 50], ['Readiness:', rd, 60], ['Morale:', mo, 60]];
    rows.forEach(([k, val, good], r) => { const yy = y + 54 + r * 24; text(c, k, x + w - 120, yy, { font: '400 16px "FPC Roboto"', align: 'right', color: PN.text });
      text(c, val == null ? '—' : `${val}%`, x + w - 46, yy, { font: '700 16px "FPC Roboto"', align: 'right', color: PN.text }); if (val != null) dot(c, x + w - 26, yy - 6, lvl(val, good)); });
    text(c, 'Range to HQ:', x + w - 120, y + 126, { font: '400 16px "FPC Roboto"', align: 'right', color: PN.text }); text(c, hq, x + w - 46, y + 126, { font: '700 16px "FPC Roboto"', align: 'right', color: PN.text });
    text(c, 'Orders Delay:', x + w - 120, y + 150, { font: '400 16px "FPC Roboto"', align: 'right', color: PN.text }); text(c, dl == null ? '—' : `${dl} mins.`, x + w - 18, y + 150, { font: '700 16px "FPC Roboto"', align: 'right', color: PN.text });
    // tabs + orders list
    ['Orders', 'Subunits', 'Staff', 'Log'].forEach((t, k) => { const bx = x + 14 + k * 92, by = y + 168; c.save(); c.fillStyle = k ? '#3f3f3f' : '#5b5b5b'; c.strokeStyle = '#8a8a8a'; c.beginPath(); c.roundRect(bx, by, 86, 26, [6, 6, 0, 0]); c.fill(); c.stroke(); c.restore();
      text(c, t, bx + 43, by + 18, { font: `${k ? 400 : 700} 15px "FPC Roboto"`, color: PN.text }); });
    c.fillStyle = PN.inner; c.fillRect(x + 12, y + 194, w - 24, h - 204);
    const q = queueAt(i, g).slice(0, 4);
    q.forEach((o, k) => { const yy = y + 214 + k * 22, sel = k === 0, dest = o.d ? ` to hex ${String(o.d).padStart(4, '0')}` : o.tu != null ? ` on ${G.u[o.tu][0]}` : '';
      if (sel) { c.fillStyle = PN.sel; c.fillRect(x + 16, yy - 16, w - 32, 21); }
      const when = o.s == null ? '' : o.s <= g ? ` started at ${api.hhmm(o.s)}` : ` starting at ${api.hhmm(o.s)}`;
      const extra = o.b ? `, ${o.b[0]} ${o.b[1]} rnds` : '';
      text(c, `${k + 1}  ${o.o}${dest}${extra}${when}`, x + 22, yy, { font: '400 15px "FPC Roboto"', align: 'left', color: '#fff' }); });
  }
  function drawPanels(c, v, g) {
    const Pn = opt('panels');
    if (Pn.gameControl !== false) gameControl(c, Pn.gcX ?? 24, Pn.gcY ?? 24, v, g);
    for (const sd of Pn.commanderSides ?? [0, 1]) commander(c, sd === 0 ? (Pn.cmX0 ?? 24) : (Pn.cmX1 ?? Wd - 354), Pn.cmY ?? (Ht - 290), v, g, sd);
    const du = (Pn.dashboard || []).find(d => v >= (d.from ?? 0) && v <= (d.to ?? 1e9)); if (du) dashboard(c, du.x ?? Wd - 690, du.y ?? 24, v, g, du.unit);
  }

  // ---------------------------------------------------------------- staging cues (STANDARD.md §3)
  // cue = { type, from, to (video s), units: [unit index], ... } as compile_staging.py writes them; one look for every cue
  const CUES = STG ? STG.cues || [] : [], SHOTS = STG ? STG.shots || [] : [];
  const NOTO = (w, n) => `${w} ${Math.round(n)}px "Noto Sans SC",sans-serif`;
  // 11-08 phone pass ($ARENA_DATA/video/research/ui-scale-benchmark): map text sized for a phone held sideways
  // (the 1920 frame ~410 pt wide). Name strips by the shot's zoom (were 0.56 x chip = 17 / 14 / 12 px); order / fire label
  // boxes; at most NEAR_MAX strips for units that are only near the focus (named, highlighted, tagged or cued ones always);
  // nothing above TOP_Y (the 1.5x top bar) or within SUB_H of the bottom (the subtitle)
  const STRIP_PX = { close: 28, mid: 26, wide: 24 }, LABEL_PX = S.labelPx ?? 26, LABEL_PX2 = Math.round(LABEL_PX * 0.85);
  const NEAR_MAX = S.nearMax ?? 6, TOP_Y = 136, SUB_H = 128;
  const SIDEC = ['#4f93ea', '#e0473b'], SIDEL = ['#9cc3f2', '#f2a497'], YEL = '#ffd84a', ARROWC = ['#6fb0ff', '#ff6b55'];
  const wakes = side => ((FD && FD.wake) || []).filter(w => w[1] === side).map(w => w[0]);
  let FR = { hl: new Set(), named: new Set(), tag: new Map(), vp: new Map(), fog: null, act: [], shot: null };
  function stageFrame(v) {
    const act = CUES.filter(q => v >= q.from && v < q.to), shot = SHOTS.find(s => v >= s.from && v < s.to) || null;
    const F = { hl: new Set(), named: new Set(shot ? shot.names || [] : []), tag: new Map(), vp: new Map(), fog: null, act, shot, late: [] };
    for (const q of act) {
      q.a = clamp((v - q.from) / 0.3, 0, 1);
      for (const i of q.units || []) if (q.type !== 'losses' || q.units) F.named.add(i);
      if (q.type === 'highlight') for (const i of q.units) F.hl.add(i);
      if (q.type === 'tag') for (const i of q.units) F.tag.set(i, [...new Set([...(F.tag.get(i) || []), ...q.show])]);
      if (q.type === 'fog') F.fog = q.view;
      if (q.type === 'hex' && q.mark === 'objective') for (const h of q.hexes) F.vp.set(h, { a: q.a, label: q.label });
    }
    return F;
  }
  function stagingLive(g) {                         // the replay's own positions (no video-time motion plan)
    const out = [];
    for (let i = 0; i < NU; i++) { const s = api.unitState(i, g); if (!s || s.st !== 1 || isNaN(s.x) || s.alive <= 0) continue;
      out.push({ o: { i, side: G.u[i][1], P: { moving: s.moving, hd: s.moving ? Math.atan2(s.nx, s.nz) : null } }, s, alive: s.alive, dead: false }); }
    return out;
  }
  // near units of a close shot get their names too
  function nearNames(g) { const sh = FR.shot; if (!sh || !sh.near) return;
    for (const L of layout.values()) if (Math.hypot(L.x - sh.near.x, L.z - sh.near.z) <= sh.near.r) FR.named.add(L.i); }
  function rectHit(a, b) { const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x), h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y); return w > 0 && h > 0 ? w * h : 0; }
  function attr(i, k, g, L) {                       // tag fields: [label, value]
    const tot = G.u[i][5], alive = L ? L.alive : (api.unitState(i, g) || {}).alive;
    if (k === 'strength') return ['兵力', `${alive}/${tot}`];
    if (k === 'subunits') return ['子单位', String(alive)];
    if (k === 'losses') { const g0 = FR.shot ? FR.shot.g0 : g, s0 = api.unitState(i, g0); return ['损失', String(Math.max(0, (s0 ? s0.alive : tot) - alive))]; }
    if (k === 'order') { const o = curOrder(i, g); return ['命令', o ? ORDER_ZH[o.o] || o.o : '坚守']; }
    if (k === 'morale') { const x = series('mo', i, g); return ['士气', x == null ? '—' : String(x)]; }
    if (k === 'ammo') { const x = series('am', i, g); return ['弹药', x == null ? '—' : x + '%']; }
    if (k === 'readiness') { const x = series('rd', i, g); return ['准备度', x == null ? '—' : String(x)]; }
    if (k === 'delay') { const d = delays[i], r = api.rowAt(d, g); return ['命令延迟', r >= 0 ? d[r][1] + ' 分' : '—']; }
    if (k === 'equipment') { const pl = (uinfo[i].plats || [])[0]; return ['装备', pl ? `${String(pl[0]).replace(/\[.*?\]/g, '').trim()} ×${pl[1]}` : uinfo[i].plat || '—']; }
    return [k, ''];
  }
  const prefixName = ids => { const ns = ids.map(i => api.unitZh(i)); let p = ns[0];
    for (const q of ns) { let k = 0; while (k < p.length && k < q.length && p[k] === q[k]) k++; p = p.slice(0, k); }
    const cut = Math.max(...[...'团营连排班队'].map(ch => p.lastIndexOf(ch))); return cut >= 1 ? p.slice(0, cut + 1) : ns[0]; };
  // name strips (HOI4 counter name tab, in Chinese): attached to the chip, right side first, else left, else below; one per
  // stack; the fields of tag cues follow the name as small chips; a near unit's strip that finds no room is left out
  function chipStrips(c, g, list, view) {
    const want = [];
    for (const L of list) {
      if (!L.top || (L.mode !== 'own' && view != null)) continue;
      const mem = (L.stack || [L]).map(m => m.i), nm = STG ? mem.filter(i => FR.named.has(i)) : mem; if (!nm.length) continue;
      const tagged = STG ? nm.filter(i => FR.tag.has(i)) : [];
      const pri = !STG ? 0 : nm.some(i => FR.hl.has(i)) ? 3 : tagged.length ? 2 : ((FR.shot && (FR.shot.names || []).some(i => nm.includes(i))) || FR.act.some(q => (q.units || []).some(i => nm.includes(i)))) ? 1 : 0;
      const head = mem.length === 1 ? api.unitZh(mem[0]) : prefixName(mem) + ` 等 ${mem.length} 个`;
      const one = tagged.length === 1 && mem.length === 1;
      const rows = [{ name: head, chips: one ? FR.tag.get(tagged[0]).map(k => attr(tagged[0], k, g, layout.get(tagged[0]))) : [] }];
      if (!one) for (const i of tagged) rows.push({ name: api.unitZh(i), chips: FR.tag.get(i).map(k => attr(i, k, g, layout.get(i))), sub: true });
      const acts = STG ? FR.act.filter(q => (q.units || []).some(i => nm.includes(i))).map(q => q.a) : [];
      want.push({ L, rows, pri, side: L.o.side, a: STG ? Math.max(...acts, FR.shot ? clamp((frameV - FR.shot.from) / 0.3, 0, 1) : 1) : 1 });
    }
    // the HTML panels up now (sandbox.js api.panels: kill feed, commander card) count as strips already placed
    const placed = STG && api.panels ? api.panels(frameV, frameG).map(r => ({ ...r, hard: true })) : [], obst = list.map(L => L.box).filter(Boolean);
    want.sort((a, b) => b.pri - a.pri || a.L.y0 - b.L.y0);
    for (const T of want) {
      const H = T.L.s, fs = STG ? STRIP_PX[(FR.shot || {}).zoom] || STRIP_PX.mid : Math.round(H * 0.56), fc = Math.round(fs * (STG ? 0.86 : 0.86)),
        rowH = Math.max(H, Math.round(fs * 1.5)), padX = Math.max(H * 0.25, fs * 0.4), bx = T.L.box;
      if (!bx) continue;
      const widths = T.rows.map(r => { c.font = NOTO(700, r.sub ? fc : fs); let w = c.measureText(r.name).width; c.font = NOTO(500, fc); for (const [k, x] of r.chips) w += 6 + c.measureText(k + ' ' + x).width + 10; return w; });
      const w = padX * 2 + Math.max(3, H * 0.11) + Math.max(...widths), h = T.rows.length * rowH;
      const y1 = T.L.y0 - H / 2 - (rowH - H) / 2;   // a strip taller than the chip sits centred on it
      const cand = [[bx.x + bx.w - 1, y1], [bx.x - w + 1, y1], [T.L.x0 - w / 2, bx.y + bx.h + 1]];
      // sticky: the side it had last frame wins unless clearly worse; a strip that was showing stays unless badly covered
      const was = STICK.get(T.L.i), fresh = !was || frameV - was.v > 0.2 || frameV < was.v;
      let best = null;
      cand.forEach(([x, y], ci) => { const r = { x, y, w, h, ci };
        let sc = placed.reduce((t, q) => t + rectHit(r, q) * 10, 0) + obst.filter(o => o !== bx).reduce((t, q) => t + rectHit(r, q), 0) + (x < 0 || x + w > Wd || y < TOP_Y || y + h > Ht - SUB_H ? 1e6 : 0);
        if (!fresh && ci !== was.ci && sc < 1e6) sc += w * h * 0.25 + 40;
        if (!best || sc < best.sc) best = { ...r, sc }; });
      const keep = !fresh && was.shown && best.sc < w * h * 0.6;
      // a near unit's strip (pri 0) also stays out once NEAR_MAX of them are up (the focus units' strips always come first)
      const near = placed.filter(q => q.pri < 1).length;
      // 11-08: a strip only named by the shot (pri 1, not tagged / highlighted) that would sit more than 8 % on strips already up
      // (or on an HTML panel) stays out too, so a dense cluster shows its tagged units' strips cleanly instead of a pile
      const onStrips = placed.reduce((t, q) => t + rectHit(best, q), 0);
      if (best.sc >= 1e6 || (best.sc > 0 && T.pri < 1 && !keep) || (STG && T.pri < 1 && near >= NEAR_MAX) || (STG && T.pri === 1 && !keep && onStrips > w * h * 0.08)) { STICK.set(T.L.i, { ci: best.ci, v: frameV, shown: false }); continue; }
      STICK.set(T.L.i, { ci: best.ci, v: frameV, shown: true });
      best.pri = T.pri; placed.push(best);
      c.save(); c.globalAlpha = T.a; if (STG && FR.hl.size && T.pri < 3) c.globalAlpha *= 0.75;
      nine(c, H4.mini, best.x, best.y, w, h, 12, Math.max(5, H * 0.3)); c.fillStyle = SIDEC[T.side]; c.fillRect(best.x + 4, best.y + 4, Math.max(3, H * 0.11), h - 8);
      T.rows.forEach((r, k) => { let x = best.x + padX + Math.max(3, H * 0.11); const yb = best.y + k * rowH + rowH * 0.72;
        text(c, r.name, x, yb, { font: NOTO(700, r.sub ? fc : fs), align: 'left', color: r.sub ? '#e6e1d0' : '#fff' }); c.font = NOTO(700, r.sub ? fc : fs); x += c.measureText(r.name).width + 6;
        for (const [kk, val] of r.chips) { c.font = NOTO(500, fc); const t1 = kk + ' ', w1 = c.measureText(t1).width; c.font = NOTO(700, fc); const w2 = c.measureText(val).width;
          c.fillStyle = 'rgba(0,0,0,.35)'; c.fillRect(x, yb - fc * 1.0, w1 + w2 + 8, fc * 1.32); c.strokeStyle = 'rgba(233,195,90,.35)'; c.lineWidth = 1; c.strokeRect(x + 0.5, yb - fc * 1.0 + 0.5, w1 + w2 + 7, fc * 1.32 - 1);
          text(c, t1, x + 4, yb, { font: NOTO(500, fc), align: 'left', color: '#cfc9b6' }); text(c, val, x + 4 + w1, yb, { font: NOTO(700, fc), align: 'left', color: YEL }); x += w1 + w2 + 8 + 4; } });
      c.restore();
    }
    FR.occ = [...obst, ...placed];                    // order labels (FR.late) keep clear of chips, strips and each other
  }
  // order cue: the unit's order at that time (or the next one released within 20 game minutes)
  function orderOf(i, g, q) {
    const g1 = q.effective != null ? Math.max(g, q.effective) : g, live = o => o.e == null || o.e >= g;
    let o = queueAt(i, g1).find(live);
    if ((!o || !(MOVE.has(o.o) || o.o === 'Barrage' || o.b)) && FD) { const s = FD.oq[i] || []; for (const [t, qq] of s) if (t > g && t <= g + 1200) { const x = qq.find(y => MOVE.has(y.o) || y.o === 'Barrage' || y.b); if (x) { o = x; break; } } }
    if (!o) return null;
    let rel = null; if (FD) { const s = FD.oq[i] || [], same = r => r[1].some(y => y.o === o.o && y.d === o.d && y.s === o.s);
      let k = s.findIndex(same); if (k >= 0) rel = s[k][0]; }
    let issued = q.issued ?? null;
    if (issued == null && rel != null) { const w = wakes(G.u[i][1]).filter(t => t <= rel && rel - t <= 1200); issued = w.length ? w[w.length - 1] : rel; }
    const eff = q.effective ?? (o.s != null && (issued == null || o.s > issued + 30) ? o.s : null);
    return { o, issued, eff };
  }
  // the first fraction f of a screen polyline (an order arrow drawing itself out from the unit)
  function growPath(sp, f) {
    if (f >= 1 || sp.length < 2) return sp;
    let tot = 0; for (let k = 1; k < sp.length; k++) tot += Math.hypot(sp[k][0] - sp[k - 1][0], sp[k][1] - sp[k - 1][1]);
    let left = tot * f; const out = [sp[0]];
    for (let k = 1; k < sp.length; k++) { const s = Math.hypot(sp[k][0] - sp[k - 1][0], sp[k][1] - sp[k - 1][1]);
      if (s >= left) { const t = s ? left / s : 0; out.push([sp[k - 1][0] + (sp[k][0] - sp[k - 1][0]) * t, sp[k - 1][1] + (sp[k][1] - sp[k - 1][1]) * t]); break; }
      out.push(sp[k]); left -= s; }
    return out.length >= 2 ? out : [sp[0], sp[0]];
  }
  function labelBox(c, x, y, lines, col, a, icon) {
    // 11-08 phone pass: 17 / 15 px -> LABEL_PX / LABEL_PX2, rows 22 -> 34 px, the order icon 35 -> 47 px
    c.save(); c.globalAlpha = a; const fs = LABEL_PX, f2 = LABEL_PX2, LH = Math.round(fs * 1.3); let w = 0; lines.forEach((t, k) => { c.font = NOTO(k ? 500 : 700, k ? f2 : fs); w = Math.max(w, c.measureText(t).width); });
    const im = icon && H4[icon], iw = im ? 52 : 0, h = Math.max(18 + lines.length * LH, im ? 58 : 0), W2 = w + 32 + iw, gap = 8;
    // where it was asked for, then lower, higher, right, left: the first spot clear of chips / strips / earlier labels
    const occ = FR.occ || (FR.occ = []), at = (px, py) => ({ x: clamp(px, 4, Wd - W2 - 4), y: clamp(py, TOP_Y + 4, Ht - SUB_H - 20 - h), w: W2, h });
    const cand = [at(x - W2 / 2, y), at(x - W2 / 2, y + h + gap), at(x - W2 / 2, y - h - 2 * gap - 40), at(x + 30, y - h / 2), at(x - W2 - 30, y - h / 2), at(x - W2 / 2, y + 2 * (h + gap)),
      at(x + 30, y + h / 2 + gap), at(x - W2 - 30, y + h / 2 + gap), at(x + 30, y - 1.5 * h - gap), at(x - W2 - 30, y - 1.5 * h - gap),
      at(x + W2 / 2 + 60, y - h / 2), at(x - 1.5 * W2 - 60, y - h / 2), at(x + W2 / 2 + 60, y + h / 2 + gap), at(x - 1.5 * W2 - 60, y + h / 2 + gap)];
    // 11-08: another label or an HTML panel (hard) under it counts 10x a chip, a name strip 4x; still more than a third covered -> not drawn
    const hit = r => occ.reduce((t, q) => t + rectHit(r, q) * (q.hard ? 10 : q.pri != null ? 4 : 1), 0), hardHit = r => occ.reduce((t, q) => t + (q.hard ? rectHit(r, q) : 0), 0);
    const key = lines.join('|'), was = STICK.get(key), fresh = !was || frameV - was.v > 0.2 || frameV < was.v;   // sticky, as the strips
    let best = null; cand.forEach((r, ci) => { let sc = hit(r); if (!fresh && ci !== was.ci) sc += W2 * h * 0.25 + 40; if (!best || sc < best.sc) best = { ...r, sc, ci }; });
    STICK.set(key, { ci: best.ci, v: frameV });
    if (STG && hardHit(best) > W2 * h / 3) { c.restore(); return; }
    const bx = best.x, by = best.y; occ.push({ x: bx - 2, y: by - 2, w: W2 + 4, h: h + 4, hard: true });
    nine(c, H4.mini, bx, by, W2, h, 12, 10);
    if (im) c.drawImage(im, bx + 10, by + (h - 46) / 2, 47, 46);
    lines.forEach((t, k) => text(c, t, bx + 16 + iw, by + (h - lines.length * LH) / 2 + LH * 0.76 + k * LH, { font: NOTO(k ? 500 : 700, k ? f2 : fs), align: 'left', color: k ? '#d9d4c3' : '#f3efe2' })); c.restore();
  }
  function stagingOrders(c, v, g) {
    const seen = new Set();
    for (const q of FR.act) { if (q.type !== 'order') continue;
      for (const i of q.units) {
        const R = orderOf(i, g, q); if (!R) continue; const { o } = R, L = layout.get(i), st = unitXZ(i, g); if (!st) continue;
        const from = L ? [L.x, L.z] : st, when = [R.issued != null ? `${api.hhmm(R.issued)} 下令` : '', R.eff != null ? `${api.hhmm(R.eff)} 生效` : ''].filter(Boolean).join(' → ');
        const name = q.label || ORDER_ZH[o.o] || o.o, key = [o.o, o.d, o.tu, R.issued].join('|');
        if (o.o === 'Barrage' || o.b) {
          let hex = o.d || (o.tu != null ? hexAt(o.tu, Math.min(g, o.b ? o.b[2] : g)) : null);
          if (!hex && o.b) {                           // fired at a contact: the hex where its shells came down
            const t0 = o.b[2] - 60, t1 = o.b[2] + o.b[3] * 60 + 60, ss = api.shotSchedule.filter(sh => sh.from === i && sh.ix != null && sh.g >= t0 && sh.g <= t1);
            if (ss.length) { const x = ss.reduce((t, sh) => t + sh.ix, 0) / ss.length, z = ss.reduce((t, sh) => t + sh.iz, 0) / ss.length, cc = Math.round((x / api.A - 1) / 1.5) + 1;
              hex = cc * 100 + Math.round(z / api.HX + 0.5 - (cc % 2 === 0 ? 0.5 : 0)); } }
          if (!hex) continue;
          hatchHex(c, hex, INI.arty.c, INI.arty.a * q.a); const [tx, tz] = api.hexXZ(hex), t = scr(tx, tz, 0.012), f = scr(from[0], from[1], 0.012), hp = hexPxAt(tx, tz);
          c.save(); c.globalAlpha = q.a; c.strokeStyle = hexA(INI.arty.c, INI.arty.a); c.lineWidth = Math.max(3, INI.fireW * hp / 64); c.beginPath(); c.moveTo(f[0], f[1]); c.lineTo(t[0], t[1]); c.stroke(); c.restore();
          if (!seen.has(key)) { seen.add(key); FR.late.push(() => labelBox(c, t[0], t[1] + hp * 0.45, [o.b ? `${name} · ${o.b[1]} 发` : name, when].filter(Boolean), INI.arty.c, q.a, 'art')); }
          continue;
        }
        if (MOVE.has(o.o) && o.d) {
          const rows = G.p[i] || [], hx = []; let arrive = null;
          for (const r of rows) { if (r[0] <= g) continue; if (o.e != null && r[0] > o.e + 300) break; if (!r[1]) continue; const k = hx.indexOf(r[1]); if (k >= 0) { hx.length = k + 1; continue; } hx.push(r[1]); if (r[1] === o.d) { arrive = r[0]; break; } }
          const pts = arrive ? [from, ...hx.map(api.hexXZ)] : [from, api.hexXZ(o.d)];
          const [dx, dz] = api.hexXZ(o.d), hp = hexPxAt(dx, dz), w = INI.path.w * hp / 64 * 1.3, sp = growPath(screenPath(pts), clamp((frameV - q.from) / 1.4, 0, 1));
          c.save(); c.globalAlpha = q.a; pathPoly(c, hexPoly(o.d)); c.closePath(); c.fillStyle = 'rgba(176,176,176,.5)'; c.fill();
          const ac = ARROWC[G.u[i][1]] || INI.path.c;   // the ordering side's colour
          if (sp.length >= 2) { c.lineCap = 'round'; c.lineJoin = 'round'; c.strokeStyle = hexA(ac, INI.path.a); c.lineWidth = w; pathPoly(c, sp); c.stroke();
            arrowHead(c, sp[sp.length - 3] || sp[0], sp[sp.length - 1], w * 4, w * 3.6, hexA(ac, INI.path.a)); }
          c.restore();
          if (!seen.has(key)) { seen.add(key); const p = scr(dx, dz, 0.012); FR.late.push(() => labelBox(c, p[0], p[1] + hp * 0.55, [`${name} ${'(' + Math.floor(o.d / 100) + ',' + o.d % 100 + ')'}`, when].filter(Boolean), INI.path.c, q.a, ORDER_ICON[o.o])); }
          continue;
        }
        const p = L ? [L.x0, L.y0 - L.s * 0.9] : scr(st[0], st[1]); FR.late.push(() => labelBox(c, p[0], p[1] - 40, [name, when].filter(Boolean), INI.path.c, q.a, ORDER_ICON[o.o]));
      }
    }
  }
  function hexEdges(set) { const E = []; for (const h of set) { const nb = api.neighbours(h), [x, z] = api.hexXZ(h);
    for (let k = 0; k < 6; k++) if (!set.has(nb[k])) { const a = api.CORNER[(k + 4) % 6], b = api.CORNER[(k + 5) % 6]; E.push([scr(x + a[0], z + a[1], 0.008), scr(x + b[0], z + b[1], 0.008)]); } } return E; }
  function stagingHexes(c, v, g) {
    for (const q of FR.act) { if (q.type !== 'hex') continue;
      const set = new Set(q.hexes);
      if (q.mark === 'objective') { for (const h of q.hexes) if (!(G.vp || []).some(r => r[0] === h)) { const [x, z] = api.hexXZ(h), p = scr(x, z, 0.006), s = hexPxAt(x, z) * 0.86;
        c.save(); c.globalAlpha = q.a; c.drawImage(MK.VP_Y, p[0] - s / 2, p[1] - s / 2, s, s); if (q.label) text(c, q.label, p[0], p[1] + s * 0.11, { font: `700 ${Math.round(s * 0.3)}px "FPC Roboto"`, halo: 3 }); c.restore(); } continue; }
      const E = hexEdges(set);
      c.save(); c.globalAlpha = q.a; c.lineCap = 'round';
      if (q.mark === 'area') { for (const h of set) { pathPoly(c, hexPoly(h)); c.closePath(); c.fillStyle = 'rgba(255,216,74,.10)'; c.fill(); } }
      for (const [w, col] of [[7, 'rgba(0,0,0,.55)'], [3.5, YEL]]) { c.strokeStyle = col; c.lineWidth = w; c.beginPath(); for (const [a, b] of E) { c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); } c.stroke(); }
      if (q.label) { const ys = E.flatMap(e => [e[0][1], e[1][1]]), xs = E.flatMap(e => [e[0][0], e[1][0]]), a = q.a;
        FR.late.push(() => { c.save(); c.globalAlpha = a; text(c, q.label, (Math.min(...xs) + Math.max(...xs)) / 2, Math.min(...ys) - 12, { font: NOTO(700, LABEL_PX + 2), color: YEL, halo: 6, haloC: 'rgba(0,0,0,.85)' }); c.restore(); }); }
      c.restore();
    }
  }
  const lossFilt = q => l => (q.units ? q.units.includes(l.unit) : q.side == null || l.side === q.side) && (!q.win || (l.rowT >= q.win[0] && l.rowT <= q.win[1]));   // the minute the game logged it (l.g is the drawn impact, up to 48 s earlier)
  function stagingLosses(c, v, g, hud) {
    for (const q of FR.act) { if (q.type !== 'losses') continue; const f = lossFilt(q), pop = new Map();
      for (const l of api.lossSchedule) { if (!f(l)) continue; const vl = segV(l.g, v); if (vl == null) continue; const age = v - vl; if (age < 0 || age > 1.8) continue;
        const p = pop.get(l.unit) || { n: 0, age: 9 }; p.n += l.n; p.age = Math.min(p.age, age); pop.set(l.unit, p); }
      for (const [i, p] of pop) { const P = posOf(i, g); if (!P) continue; const s = layout.get(i) ? layout.get(i).s : 40;
        c.save(); c.globalAlpha = q.a * clamp(1.6 - p.age, 0, 1); text(c, `−${p.n}`, P[0] + s * 0.42, P[1] - s * 0.45 - p.age * 18, { font: NOTO(900, 38), align: 'left', color: G.u[i][1] ? '#ff9a8a' : '#9cc8ff', halo: 5, haloC: 'rgba(0,0,0,.85)' }); c.restore(); }
      const sides = q.units ? [...new Set(q.units.map(i => G.u[i][1]))] : q.side != null ? [q.side] : [0, 1];
      for (const sd of sides) { const n = api.lossSchedule.filter(l => f(l) && l.side === sd && l.g <= g).reduce((t, l) => t + l.n, 0); hud.push({ a: q.a, side: sd, txt: `${sd ? '红军' : '蓝军'} −${n}` }); }
    }
  }
  function odometer(c, x, y, val, fs, col, align) {  // one decimal; the tenths column rolls, higher columns carry
    const s = (Math.round(val * 10) / 10).toFixed(1), frac = val * 10 - Math.floor(val * 10);
    c.font = NOTO(700, fs); const w = c.measureText(s + '%').width; let cx = align === 'right' ? x - w : x;
    for (let k = 0; k < s.length; k++) { const ch = s[k], cw = c.measureText(ch).width;
      if (k === s.length - 1 && frac > 0.02 && frac < 0.98) { const nx = String((+ch + 1) % 10); c.save(); c.beginPath(); c.rect(cx - 1, y - fs * 1.05, cw + 2, fs * 1.3); c.clip();
        text(c, ch, cx, y - frac * fs * 1.1, { font: NOTO(700, fs), align: 'left', color: col }); text(c, nx, cx, y + (1 - frac) * fs * 1.1, { font: NOTO(700, fs), align: 'left', color: col }); c.restore(); }
      else text(c, ch, cx, y, { font: NOTO(700, fs), align: 'left', color: col }); cx += cw; }
    text(c, '%', cx, y, { font: NOTO(700, fs), align: 'left', color: col }); return w;
  }
  function stagingHud(c, v, g, hud) {            // under the programme's top bar (1.5 x 87 px since 11-08)
    let y = TOP_Y + 12;
    for (const q of FR.act) { if (q.type !== 'score') continue; const sc = api.scoreAt(g);
      const rows = (q.show === 'both' ? ['share', 'strength'] : [q.show]).map(k => k === 'share' ? ['份额', sc.share, 100 - sc.share] : ['兵力', sc.fb, sc.fr]);
      const w = 600, RH = 56, h = 18 + rows.length * RH, x = Wd / 2 - w / 2, y0 = TOP_Y + 8;   // 11-08: x1.4
      c.save(); c.globalAlpha = q.a; nine(c, H4.mini, x, y0, w, h, 12, 10);
      rows.forEach(([k, b, r], n) => { const yy = y0 + 54 + n * RH; text(c, k, Wd / 2, yy - 6, { font: NOTO(500, 24), color: '#cfc9b6' });
        text(c, '蓝军', x + 20, yy - 6, { font: NOTO(700, 23), align: 'left', color: SIDEL[0] }); odometer(c, x + 86, yy, b, 39, SIDEL[0], 'left');
        text(c, '红军', x + w - 20, yy - 6, { font: NOTO(700, 23), align: 'right', color: SIDEL[1] }); odometer(c, x + w - 86, yy, r, 39, SIDEL[1], 'right'); });
      c.restore(); }
    for (const H of hud) { c.save(); c.globalAlpha = H.a; c.font = NOTO(800, 38); const w = c.measureText(H.txt).width + 40;   // 11-08: 26 -> 38 px
      nine(c, H4.mini, Wd - 20 - w, y, w, 58, 12, 10); c.fillStyle = SIDEC[H.side]; c.fillRect(Wd - 20 - w + 8, y + 8, 5, 42);
      text(c, H.txt, Wd - 38, y + 43, { font: NOTO(800, 38), align: 'right', color: SIDEL[H.side] }); c.restore(); y += 68; }
    // card cues: the programme's commander cards with portraits (sandbox.js ORDERS)
  }
  function stagingFire(c, v, g) {
    const fq = FR.act.filter(q => q.type === 'fire'); if (!fq.length) return;
    drawFire(c, v, g, sh => fq.some(q => (!q.units || q.units.includes(sh.from)) && (q.side == null || sh.side === q.side) && (!q.win || (sh.g >= q.win[0] && sh.g <= q.win[1]))));
  }
  async function drawStaging(c, v, g) {
    FR = stageFrame(v);
    const view = placeCounters(v, g, stagingLive(g)); nearNames(g);
    drawMarkers(c, v, g); stagingHexes(c, v, g); stagingOrders(c, v, g);
    drawCounters(c, v, g, view);
    if (FR.vp.size) drawVP(c, v, g, true);         // objective badges a cue talks about: over the counters, with the value
    chipStrips(c, g, [...layout.values()].filter(L => L.top), view);
    for (const f of FR.late) f();
    stagingFire(c, v, g);
    const hud = []; stagingLosses(c, v, g, hud); stagingHud(c, v, g, hud);
  }

  // ---------------------------------------------------------------- hooks called by sandbox.js
  const hidden = [];
  const hide = o => { if (o && o.visible) { hidden.push(o); o.visible = false; } };
  return {
    on,
    // just before the 3D render: hide the HOI4-style board items the active categories replace (restored after the render)
    beforeRender(v, g) {
      const Ly = api.layers();
      if (on('counters', v)) for (const o of Ly.units) { if (opt('counters').pieces) { hide(o.flag); hide(o.chip); } else { hide(o.root); hide(o.chip); } }
      if (on('orders', v) || on('counters', v) || on('markers', v)) { hide(Ly.arrowMesh); hide(Ly.ovlMesh); }
      if (on('markers', v)) { for (const o of Ly.vpObjs) { for (const k of ['star', 'disc', 'pole', 'label', 'ring']) hide(o[k]); for (const k of [0, 1, 2]) { hide(o.ribbon && o.ribbon[k]); hide(o.fill && o.fill[k]); } for (const f of o.flags || []) hide(f); }
        for (const m of Ly.marks) hide(m.r); for (const L of Ly.labelObjs) if (S.hideLabels) hide(L.sp); }
      if ((on('fire', v) || STG) && Ly.FX5) { for (const k of ['impact', 'kill', 'smoke', 'puff', 'flash', 'muzzle', 'fire', 'tracers']) for (const o of Ly.FX5[k].a) hide(o); for (const M of Ly.FX5.craterMeshes) hide(M); if (Ly.fx) for (const s of Ly.fx.text) hide(s); }
      if (on('fire', v) || on('markers', v) || STG) for (const w of Ly.wreckPool || []) hide(w.vv && w.vv.root);
      for (const { bp, m } of planMeshes) { m.visible = on('orders', v) && v >= (bp.from ?? 0) && v <= (bp.to ?? 1e9); if (m.visible) m.material.uniforms.op.value = clamp(Math.min((v - (bp.from ?? -9)) / 0.6, ((bp.to ?? 1e9) - v) / 0.6), 0, 1) * (bp.alpha ?? 1); }
    },
    afterRender() { while (hidden.length) hidden.pop().visible = true; },
    // the map-anchored HTML of the HOI4 skin (unit / objective labels, order tags) is left out while the layer is on
    hidesMapHtml: v => on('counters', v) || on('markers', v) || on('orders', v),
    async draw(c, v, g, camera, live) {
      cam = camera; frameV = v; frameG = g; camera.updateMatrixWorld(); camera.updateProjectionMatrix();
      if (STG) return drawStaging(c, v, g);
      const view = placeCounters(v, g, live);
      if (on('orders', v)) drawBattlePlans(c, v, g);
      if (on('orders', v)) drawFireMissions(c, v, g);
      if (on('markers', v)) drawMarkers(c, v, g);
      if (on('orders', v)) drawPaths(c, v, g);
      if (on('counters', v)) drawCounters(c, v, g, view);
      if (on('markers', v) && vpOnTop(v)) drawVP(c, v, g);
      if (on('fire', v)) drawFire(c, v, g);
      if (on('panels', v)) drawPanels(c, v, g);
    },
  };
}
