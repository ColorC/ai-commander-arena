// Sand-table renderer for the AI wargame programme (v1). three.js r170, milsymbol 2.2.0, mp4-muxer 5.1.3 (vendor/).
// Deterministic: every frame is a pure function of the frame index (plus smoothing state that only depends on
// rendering the frames in order). No requestAnimationFrame, no wall clock.
//
//   await SANDBOX.init({shot})       build the scene (data: window.ARENA from build_data.py)
//   SANDBOX.renderFrame(i)          draw frame i into the composite canvas (3D + HTML/CSS overlay)
//   await SANDBOX.encodeFrame(i)    VideoFrame of the composite -> VideoEncoder (avc1, hardware preferred)
//   await SANDBOX.finish()          flush, mux (mp4-muxer), hand the bytes to window.arenaWrite(b64 chunk, last)
//   await SANDBOX.snapshot(name)    PNG of the composite -> window.arenaSave(name, b64)
//
// Map space: km, x east from the west edge of column 1, z south from the north edge (flat-top hexes, hex = col*100+row,
// even columns half a hex lower, as tools/s2_battle_maps.py). World space: x = mapX - W/2, z = mapZ - H/2, y up (km).
//
// v1 (Minden): scenario clock (G.h0), water hexes / highways, miniature trees and houses, movement trails and order
// arrows, VP capture rings, close-up mode (camera key `models` -> generic white-model vehicles instead of counters,
// wrecks, sky + haze), HUD with force curves, VP chips, attack result cards, kill feed, moment banners, captions.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { SavePoint } from './vendor/savepoint/savepoint.js';
// asset root: ?video=<file URL> on sandbox.html (render.py passes $ARENA_DATA/video/), default <repo>/data/video/
const VIDEO = globalThis.ARENA_VIDEO || new URL('../../data/video/', import.meta.url).href;
// model kit: models.js (v2, open-licensed) or models-hoi4.js (v3, converted HOI4 models); SHOT.kit / init({kit}) picks it
let loadKit, makeVehicle, makeSoldier, KITMOD = {};

const ARENA = window.ARENA;
const T = ARENA.terrain, G = ARENA.game;
const A = 0.5 / Math.sqrt(3), HX = 0.5;
const MAP_W = A * (2 + 1.5 * (T.cols - 1)), MAP_H = HX * (T.rows + 0.5);
let EXAG = 3.0, BASE_M = 150;            // vertical exaggeration; metres that sit at y = 0 (shot.exag / shot.baseM)
const H0 = G.h0 ?? 420;                  // minutes after midnight at game second 0 (EET 07:00, Minden 03:30)
const BLUE = '#3f74c4', RED = '#c0453b', BLUE_DK = '#24477d', RED_DK = '#7d2a24';

// ---------------------------------------------------------------- small helpers
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const lerp = (a, b, f) => a + (b - a) * f;
const smooth = f => f * f * (3 - 2 * f);
function rng(seed) { let s = seed >>> 0; return () => { s = (s + 0x6D2B79F5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
const colX = c => A * (1 + 1.5 * (c - 1));
const rowZ = (c, r) => HX * (r - 0.5 + (c % 2 === 0 ? 0.5 : 0));
const hexXZ = h => { const c = Math.floor(h / 100); return [colX(c), rowZ(c, h % 100)]; };
const CORNER = [...Array(6)].map((_, i) => [A * Math.cos(i * Math.PI / 3), A * Math.sin(i * Math.PI / 3)]);
const MID = [...Array(6)].map((_, k) => { const a = (-90 + 60 * k) * Math.PI / 180; return [HX / 2 * Math.cos(a), HX / 2 * Math.sin(a)]; });
const hhmm = g => { const m = Math.floor(g / 60) + H0; return String(Math.floor(m / 60) % 24).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0'); };
const hexName = h => V5 ? `(${Math.floor(h / 100)},${h % 100})` : String(h).padStart(4, '0');   // v5: (col,row)
function rowAt(rows, t) { let lo = 0, hi = rows.length - 1, ans = -1; while (lo <= hi) { const m = (lo + hi) >> 1; if (rows[m][0] <= t) { ans = m; lo = m + 1; } else hi = m - 1; } return ans; }
function neighbours(h) {                           // flat-top, even columns down
  const c = Math.floor(h / 100), r = h % 100, dn = c % 2 === 0;
  const d = dn ? [[0, -1], [1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0]] : [[0, -1], [1, -1], [1, 0], [0, 1], [-1, 0], [-1, -1]];
  return d.map(([dc, dr]) => (c + dc) * 100 + r + dr);
}

// ---------------------------------------------------------------- height map
const HM = (() => {
  const m = ARENA.hm, bin = atob(m.b64), buf = new ArrayBuffer(bin.length), u8 = new Uint8Array(buf);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  let d = new Float32Array(buf);
  const w = m.width, h = m.height;
  for (let pass = 0; pass < 2; pass++) {            // soften the DSM (tree tops, buildings) for a sand-table look
    const o = new Float32Array(d.length);
    for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) {
      let s = 0, n = 0;
      for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) {
        const x = i + di, y = j + dj; if (x < 0 || y < 0 || x >= w || y >= h) continue; s += d[y * w + x]; n++;
      }
      o[j * w + i] = s / n;
    }
    d = o;
  }
  return { w, h, d, min: m.min_m, max: m.max_m };
})();
function heightM(mx, mz) {
  const fx = clamp(mx / MAP_W * HM.w - 0.5, 0, HM.w - 1.001), fz = clamp(mz / MAP_H * HM.h - 0.5, 0, HM.h - 1.001);
  const x0 = Math.floor(fx), z0 = Math.floor(fz), ax = fx - x0, az = fz - z0, w = HM.w, d = HM.d, k = z0 * w + x0;
  return d[k] * (1 - ax) * (1 - az) + d[k + 1] * ax * (1 - az) + d[k + w] * (1 - ax) * az + d[k + w + 1] * ax * az;
}
const yAt = (mx, mz) => (heightM(mx, mz) - BASE_M) / 1000 * EXAG;
const W = (mx, mz, dy = 0) => new THREE.Vector3(mx - MAP_W / 2, yAt(mx, mz) + dy, mz - MAP_H / 2);
function normalAt(mx, mz) { const e = 0.02; return new THREE.Vector3(yAt(mx - e, mz) - yAt(mx + e, mz), 2 * e, yAt(mx, mz - e) - yAt(mx, mz + e)).normalize(); }

// ---------------------------------------------------------------- per-game lookups
const NU = G.u.length;
const isHelo = i => G.u[i][2] === 'Helo';
const TERR = {};                                    // hex -> class index
for (let c = 1; c <= T.cols; c++) for (let r = 1; r <= T.rows; r++) TERR[c * 100 + r] = +T.t[(c - 1) * T.rows + (r - 1)];
const WATER = new Set(T.wf || []);
function unitState(i, g) {                         // {x, z, alive, st, hex, moving, nx, nz}
  const rows = G.p[i], k = rowAt(rows, g);
  if (k < 0) return null;
  const cur = rows[k], nxt = rows[k + 1];
  let [x, z] = cur[1] ? hexXZ(cur[1]) : [NaN, NaN];
  let hexNow = cur[1], moving = false, nx = 0, nz = 0;
  if (nxt && nxt[1] && cur[1] && nxt[1] !== cur[1] && cur[3] === 1) {
    const dur = Math.min(nxt[0] - cur[0], 75), start = nxt[0] - dur, [x2, z2] = hexXZ(nxt[1]);
    nx = x2 - x; nz = z2 - z;
    if (g > start) {
      moving = true;
      const f = V4 ? clamp((g - start) / dur, 0, 1) : smooth((g - start) / dur);
      x = lerp(x, x2, f); z = lerp(z, z2, f);
      if (f > 0.5) hexNow = nxt[1];
    }
  }
  return { x, z, alive: cur[2], st: cur[3], hex: hexNow, moving, nx, nz };
}

// ---------------------------------------------------------------- time mapping
let SHOT, FPS;
function gOf(v) {
  const k = SHOT.time; if (v <= k[0][0]) return k[0][1];
  for (let i = 1; i < k.length; i++) if (v <= k[i][0]) return lerp(k[i - 1][1], k[i][1], (v - k[i - 1][0]) / (k[i][0] - k[i - 1][0]));
  return k[k.length - 1][1];
}
function vOf(g) {
  const k = SHOT.time; if (g <= k[0][1]) return k[0][0] - (k[0][1] - g) / 60;
  for (let i = 1; i < k.length; i++) if (g <= k[i][1]) return lerp(k[i - 1][0], k[i][0], (g - k[i - 1][1]) / (k[i][1] - k[i - 1][1]));
  return k[k.length - 1][0] + (g - k[k.length - 1][1]) / 60;
}

// ---------------------------------------------------------------- canvas textures
function canvas(w, h) { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; }
function hexPath(ctx, x, z, s, r = A) {
  ctx.beginPath();
  for (let i = 0; i < 6; i++) { const px = (x + r / A * CORNER[i][0]) * s, pz = (z + r / A * CORNER[i][1]) * s; i ? ctx.lineTo(px, pz) : ctx.moveTo(px, pz); }
  ctx.closePath();
}
function eachHex(fn) { for (let c = 1; c <= T.cols; c++) for (let r = 1; r <= T.rows; r++) fn(c, r, (c - 1) * T.rows + (r - 1)); }

function paintGround(TW) {
  const s = TW / MAP_W, TH = Math.round(MAP_H * s), cv = canvas(TW, TH), ctx = cv.getContext('2d');
  const R = rng(7);
  const CLS = ['#cbbd8c', '#a3a874', '#6f8453', '#b8aa95', '#93aca4'];
  // 1 soft class colours (blurred hex fills)
  const base = canvas(TW, TH), b = base.getContext('2d');
  if (!V6) { b.fillStyle = CLS[0]; b.fillRect(0, 0, TW, TH);
  eachHex((c, r, i) => { const t = +T.t[i]; b.fillStyle = CLS[t]; hexPath(b, colX(c), rowZ(c, r), s, A * 1.04); b.fill(); });
  ctx.filter = `blur(${Math.round(s * 0.05)}px)`; ctx.drawImage(base, 0, 0); ctx.filter = 'none'; }
  // 2 hand-painted detail per class
  if (!V6) eachHex((c, r, i) => {
    const t = +T.t[i], cx = colX(c), cz = rowZ(c, r);
    if (WATER.has(c * 100 + r)) return;
    const pt = () => { for (;;) { const x = (R() * 2 - 1) * A, z = (R() * 2 - 1) * HX / 2; if (Math.abs(z) <= HX / 2 && Math.abs(x) * HX / 2 + Math.abs(z) * A / 2 <= A * HX / 2) return [(cx + x) * s, (cz + z) * s]; } };
    if (t === 0) {                                   // patchwork fields
      for (let k = 0; k < 9; k++) {
        const [x, z] = pt(), w = (0.05 + R() * 0.09) * s, h = (0.03 + R() * 0.06) * s;
        ctx.save(); ctx.translate(x, z); ctx.rotate((R() - 0.5) * 0.9);
        ctx.fillStyle = ['#d4c592', '#c1b67f', '#b7b07a', '#d8c99f', '#c9b680', '#a9b27a'][Math.floor(R() * 6)]; ctx.globalAlpha = 0.55;
        ctx.fillRect(-w / 2, -h / 2, w, h);
        ctx.globalAlpha = 0.18; ctx.strokeStyle = '#7c6d47'; ctx.lineWidth = 1; ctx.strokeRect(-w / 2, -h / 2, w, h);
        ctx.restore();
      }
    } else if (t === 1 || t === 2) {                 // tree crowns (under the 3D trees)
      const n = t === 2 ? 70 : 26;
      for (let k = 0; k < n; k++) {
        const [x, z] = pt(), rr = (t === 2 ? 0.012 + R() * 0.012 : 0.01 + R() * 0.01) * s;
        ctx.globalAlpha = 0.75; ctx.fillStyle = t === 2 ? (R() < 0.5 ? '#566d40' : '#4b6238') : (R() < 0.5 ? '#7d8f57' : '#6f824e');
        ctx.beginPath(); ctx.arc(x, z, rr, 0, 7); ctx.fill();
      }
    } else if (t === 3) {                            // town blocks
      for (let k = 0; k < 34; k++) {
        const [x, z] = pt(), w = (0.012 + R() * 0.02) * s, h = (0.01 + R() * 0.016) * s;
        ctx.globalAlpha = 0.8; ctx.fillStyle = R() < 0.6 ? '#8d7c69' : '#a5604a';
        ctx.fillRect(x - w / 2, z - h / 2, w, h);
      }
    } else if (t === 4) {                            // marsh hatching
      ctx.globalAlpha = 0.5; ctx.strokeStyle = '#5f8790'; ctx.lineWidth = Math.max(1, s * 0.004);
      for (let k = 0; k < 16; k++) { const [x, z] = pt(), w = (0.02 + R() * 0.03) * s; ctx.beginPath(); ctx.moveTo(x - w, z); ctx.lineTo(x + w, z); ctx.stroke(); }
    }
    ctx.globalAlpha = 1;
  });
  // 3 hill shade (baked, mild) from the height map
  if (!V6) {
    const sh = canvas(HM.w, HM.h), sc = sh.getContext('2d'), img = sc.createImageData(HM.w, HM.h);
    const cell = MAP_W / HM.w * 1000, L = new THREE.Vector3(-0.6, 0.65, -0.45).normalize();
    for (let j = 0; j < HM.h; j++) for (let i = 0; i < HM.w; i++) {
      const g = (x, y) => HM.d[clamp(y, 0, HM.h - 1) * HM.w + clamp(x, 0, HM.w - 1)];
      const dx = (g(i + 1, j) - g(i - 1, j)) / (2 * cell) * EXAG, dz = (g(i, j + 1) - g(i, j - 1)) / (2 * cell) * EXAG;
      const n = new THREE.Vector3(-dx, 1, -dz).normalize(), v = clamp(n.dot(L) * 1.25, 0, 1);
      const k = (j * HM.w + i) * 4, c = Math.round(150 + 105 * v);
      img.data[k] = c; img.data[k + 1] = c; img.data[k + 2] = Math.round(c * 0.97); img.data[k + 3] = 255;
    }
    sc.putImageData(img, 0, 0);
    ctx.globalCompositeOperation = 'multiply'; ctx.globalAlpha = 0.55; ctx.drawImage(sh, 0, 0, TW, TH);
    ctx.globalCompositeOperation = 'source-over'; ctx.globalAlpha = 1;
  }
  // 4 contour lines (marching squares), 20 m, every 100 m darker
  {
    const sx = TW / HM.w, sz = TH / HM.h, d = HM.d, w = HM.w;
    for (let lv = Math.ceil(HM.min / 20) * 20; lv < HM.max; lv += 20) {
      const major = lv % 100 === 0; if (V6 && !major) continue;
      ctx.strokeStyle = V6 ? 'rgba(70,52,30,.28)' : major ? 'rgba(92,62,32,.55)' : 'rgba(110,80,45,.30)'; ctx.lineWidth = major ? 2.2 : 1.1;
      ctx.beginPath();
      for (let j = 0; j < HM.h - 1; j++) for (let i = 0; i < w - 1; i++) {
        const a = d[j * w + i] - lv, bb = d[j * w + i + 1] - lv, c = d[(j + 1) * w + i + 1] - lv, e = d[(j + 1) * w + i] - lv;
        const code = (a > 0) | (bb > 0) << 1 | (c > 0) << 2 | (e > 0) << 3;
        if (code === 0 || code === 15) continue;
        const P = [];
        const edge = (v1, v2, x1, z1, x2, z2) => { const f = v1 / (v1 - v2); P.push([(x1 + (x2 - x1) * f + 0.5) * sx, (z1 + (z2 - z1) * f + 0.5) * sz]); };
        if ((a > 0) !== (bb > 0)) edge(a, bb, i, j, i + 1, j);
        if ((bb > 0) !== (c > 0)) edge(bb, c, i + 1, j, i + 1, j + 1);
        if ((c > 0) !== (e > 0)) edge(c, e, i + 1, j + 1, i, j + 1);
        if ((e > 0) !== (a > 0)) edge(e, a, i, j + 1, i, j);
        for (let k = 0; k + 1 < P.length; k += 2) { ctx.moveTo(P[k][0], P[k][1]); ctx.lineTo(P[k + 1][0], P[k + 1][1]); }
      }
      ctx.stroke();
    }
  }
  // 5 open water (major river / lake hexes): soft-edged fill along the connected water hexes
  if (WATER.size) {
    const wm = canvas(TW, TH), w = wm.getContext('2d');
    w.fillStyle = '#fff'; w.strokeStyle = '#fff'; w.lineCap = 'round'; w.lineWidth = s * 0.26;
    for (const h of WATER) {
      const [x, z] = hexXZ(h);
      w.beginPath(); w.arc(x * s, z * s, s * 0.17, 0, 7); w.fill();
      for (const n of neighbours(h)) if (WATER.has(n) && n > h) { const [x2, z2] = hexXZ(n); w.beginPath(); w.moveTo(x * s, z * s); w.lineTo(x2 * s, z2 * s); w.stroke(); }
    }
    const wb = canvas(TW, TH), wc = wb.getContext('2d');
    wc.filter = `blur(${Math.round(s * 0.03)}px)`; wc.drawImage(wm, 0, 0); wc.filter = 'none';
    // bank (light) then water (blue), both masked by the blurred shape
    const paint = (col, grow) => {
      const t = canvas(TW, TH), tc = t.getContext('2d');
      if (grow) { tc.filter = `blur(${Math.round(s * 0.05)}px)`; tc.drawImage(wm, 0, 0); tc.filter = 'none'; tc.drawImage(wm, 0, 0); } else tc.drawImage(wb, 0, 0);
      tc.globalCompositeOperation = 'source-in'; tc.fillStyle = col; tc.fillRect(0, 0, TW, TH);
      ctx.drawImage(t, 0, 0);
    };
    paint('rgba(222,214,180,.85)', true);
    paint('#4f7f9c', false);
    // ripple strokes
    ctx.save(); ctx.globalCompositeOperation = 'source-atop';
    const rr = rng(21); ctx.strokeStyle = 'rgba(200,225,235,.25)'; ctx.lineWidth = Math.max(1, s * 0.003);
    for (const h of WATER) { const [x, z] = hexXZ(h); for (let k = 0; k < 5; k++) { const px = (x + (rr() - 0.5) * 0.3) * s, pz = (z + (rr() - 0.5) * 0.3) * s, l = (0.02 + rr() * 0.03) * s; ctx.beginPath(); ctx.moveTo(px - l, pz); ctx.lineTo(px + l, pz); ctx.stroke(); } }
    ctx.restore();
  }
  // 6 water lines, roads, rail
  const edges = (list, style, width) => {
    ctx.strokeStyle = style; ctx.lineWidth = width; ctx.lineCap = 'round'; ctx.beginPath();
    for (const [h, m] of list) { const [x, z] = hexXZ(h); for (let k = 0; k < 6; k++) if (m >> k & 1) { const p = CORNER[(4 + k) % 6], q = CORNER[(5 + k) % 6]; ctx.moveTo((x + p[0]) * s, (z + p[1]) * s); ctx.lineTo((x + q[0]) * s, (z + q[1]) * s); } }
    ctx.stroke();
  };
  const spokes = (list, style, width, dash) => {
    ctx.strokeStyle = style; ctx.lineWidth = width; ctx.lineCap = dash ? 'butt' : 'round'; ctx.setLineDash(dash || []); ctx.beginPath();
    for (const [h, m] of list) { const [x, z] = hexXZ(h); for (let k = 0; k < 6; k++) if (m >> k & 1) { ctx.moveTo(x * s, z * s); ctx.lineTo((x + MID[k][0]) * s, (z + MID[k][1]) * s); } }
    ctx.stroke(); ctx.setLineDash([]);
  };
  edges(T.st || [], 'rgba(74,120,150,.85)', s * 0.012);
  edges(T.rv || [], 'rgba(214,226,222,.7)', s * 0.05);
  edges(T.rv || [], '#4b7c99', s * 0.032);
  if (!V6) {                                          // v6: roads are surface textures (groundWeightsV6)
  spokes(T.sr || [], 'rgba(120,96,68,.85)', s * 0.012);
  spokes(T.pr || [], 'rgba(238,226,196,.9)', s * 0.03);
  spokes(T.pr || [], '#8a6440', s * 0.018);
  spokes(T.hw || [], 'rgba(245,236,210,.95)', s * 0.046);
  spokes(T.hw || [], '#5e5850', s * 0.03);
  spokes(T.hw || [], 'rgba(240,220,150,.9)', s * 0.004, [s * 0.02, s * 0.02]); }
  spokes(T.rl || [], '#3b3732', s * 0.012);
  spokes(T.rl || [], 'rgba(230,220,200,.9)', s * 0.006, [s * 0.025, s * 0.025]);
  // 7 hex grid, faint
  ctx.strokeStyle = V6 ? 'rgba(40,32,20,.16)' : 'rgba(58,44,24,.22)'; ctx.lineWidth = Math.max(1, s * 0.0035); ctx.beginPath();
  eachHex((c, r) => { const x = colX(c), z = rowZ(c, r); for (let i = 0; i < 6; i++) { const px = (x + CORNER[i][0]) * s, pz = (z + CORNER[i][1]) * s; i ? ctx.lineTo(px, pz) : ctx.moveTo(px, pz); } ctx.closePath(); });
  ctx.stroke();
  // 8 paper grain
  if (V6) return cv;
  const gr = canvas(256, 256), gc = gr.getContext('2d'), gi = gc.createImageData(256, 256), R2 = rng(11);
  for (let k = 0; k < gi.data.length; k += 4) { const v = 110 + R2() * 145; gi.data[k] = v; gi.data[k + 1] = v; gi.data[k + 2] = v * 0.95; gi.data[k + 3] = 255; }
  gc.putImageData(gi, 0, 0);
  ctx.globalCompositeOperation = 'overlay'; ctx.globalAlpha = 0.16; ctx.fillStyle = ctx.createPattern(gr, 'repeat'); ctx.fillRect(0, 0, TW, TH);
  ctx.globalCompositeOperation = 'source-over'; ctx.globalAlpha = 1;
  return cv;
}

// v6 ground: real surface textures (Poly Haven CC0, one DataArrayTexture) blended per terrain class in the shader.
// W1 = open-ground parcels (r meadow, g pasture, b ploughed, rest stubble), W2 = class (r woods, g town, b marsh),
// W3 = roads (r gravel, g asphalt) and a baked hill shade (b). Map z runs down the canvas rows (flipY off).
let GROUND6 = null;
async function loadGroundV6(img) {
  const G6 = SHOT.ground, N = G6.layers.length, S = 1024, data = new Uint8Array(S * S * 4 * N), c = canvas(S, S), x = c.getContext('2d', { willReadFrequently: true });
  for (let k = 0; k < N; k++) { const im = await img(G6.base + G6.layers[k] + '/' + (G6.file || 'diffuse_1k.jpg')); x.drawImage(im, 0, 0, S, S); data.set(x.getImageData(0, 0, S, S).data, k * S * S * 4); }
  const t = new THREE.DataArrayTexture(data, S, S, N);
  t.format = THREE.RGBAFormat; t.type = THREE.UnsignedByteType; t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping; t.minFilter = THREE.LinearMipmapLinearFilter; t.magFilter = THREE.LinearFilter; t.generateMipmaps = true; t.anisotropy = 8; t.needsUpdate = true;
  GROUND6 = { tex: t };
}
function groundWeightsV6() {
  const R = rng(77), mk = (w) => { const h = Math.round(w * MAP_H / MAP_W), cv = canvas(w, h); return [cv, cv.getContext('2d'), w / MAP_W, h]; };
  // W1: field parcels over the whole board (masked to open ground in the shader)
  const [c1, x1, s1, h1] = mk(V7 ? 4096 : 2048), tracks = [];
  x1.fillStyle = '#ff0000'; x1.fillRect(0, 0, c1.width, h1);
  const TYPES = ['#ff0000', '#ff0000', '#ff0000', '#00ff00', '#00ff00', '#00ff00', '#0000ff', '#000000'];
  // v7: West German open-field farmland: blocks of 0.4-0.8 km, each cut into long strips 40-140 m wide (meadow, pasture,
  // ploughed, grain / stubble), farm tracks along the block edges
  if (V8) {                                     // v8: no fields; soft blobs 1-3 km across, +-8 % brightness at most
    x1.fillStyle = 'rgb(128,0,0)'; x1.fillRect(0, 0, c1.width, h1);
    for (let k = 0; k < 140; k++) { const x = R() * MAP_W * s1, z = R() * MAP_H * s1, r = (0.5 + R() * 1.0) * s1, lt = R() < 0.5;
      const gr = x1.createRadialGradient(x, z, 0, x, z, r); gr.addColorStop(0, lt ? 'rgba(255,0,0,0.16)' : 'rgba(0,0,0,0.16)'); gr.addColorStop(1, lt ? 'rgba(255,0,0,0)' : 'rgba(0,0,0,0)');
      x1.fillStyle = gr; x1.fillRect(x - r, z - r, 2 * r, 2 * r); }
  }
  else if (V7) for (let z = -0.3; z < MAP_H + 0.3; z += 0.42) for (let xx = -0.3; xx < MAP_W + 0.3; xx += 0.55) {
    const bw = 0.45 + R() * 0.35, bh = 0.32 + R() * 0.25, rot = (R() - 0.5) * 0.9, cx = (xx + R() * 0.12) * s1, cz = (z + R() * 0.1) * s1;
    x1.save(); x1.translate(cx, cz); x1.rotate(rot);
    const alongX = R() < 0.5, L = alongX ? bh : bw; let o = -L / 2;
    while (o < L / 2) { const sw = 0.04 + R() * 0.1, u = R(); x1.fillStyle = u < 0.3 ? '#ff0000' : u < 0.48 ? '#00ff00' : u < 0.68 ? '#0000ff' : '#000000';
      if (alongX) x1.fillRect(-bw / 2 * s1, o * s1, bw * s1, Math.min(sw, L / 2 - o) * s1); else x1.fillRect(o * s1, -bh / 2 * s1, Math.min(sw, L / 2 - o) * s1, bh * s1); o += sw; }
    x1.restore();
    if (R() < 0.7) tracks.push([cx / s1, cz / s1, rot, bw, bh, R() < 0.5]);
  }
  else for (let z = -0.2; z < MAP_H + 0.2; z += 0.22) for (let xx = -0.2; xx < MAP_W + 0.2; xx += 0.3) {
    const w = 0.16 + R() * 0.3, h = 0.1 + R() * 0.2, rot = (R() - 0.5) * 0.7 + (Math.floor(xx / 2.5 + z / 3) % 2) * 0.5;
    x1.save(); x1.translate((xx + R() * 0.2) * s1, (z + R() * 0.15) * s1); x1.rotate(rot); x1.fillStyle = TYPES[Math.floor(R() * TYPES.length)];
    x1.fillRect(-w / 2 * s1, -h / 2 * s1, w * s1, h * s1); x1.restore();
  }
  // W2: terrain class (light woods half weight), soft edges
  const [c2, x2, s2, h2] = mk(2048), b2 = canvas(2048, h2), y2 = b2.getContext('2d');
  y2.fillStyle = '#000'; y2.fillRect(0, 0, 2048, h2);
  eachHex((c, r, i) => { const t = +T.t[i]; if (!t) return; y2.fillStyle = t === 1 ? '#a00000' : t === 2 ? '#ff0000' : t === 3 ? '#00ff00' : '#0000ff'; hexPath(y2, colX(c), rowZ(c, r), s2, A * 1.02); y2.fill(); });
  x2.filter = `blur(${Math.round(s2 * 0.06)}px)`; x2.drawImage(b2, 0, 0); x2.filter = 'none';
  // W3: roads (r gravel: primary / secondary, g asphalt: highways), b hill shade
  const [c3, x3, s3, h3] = mk(4096);
  { const sh = canvas(HM.w, HM.h), sc = sh.getContext('2d'), im = sc.createImageData(HM.w, HM.h);
    const cell = MAP_W / HM.w * 1000, L = new THREE.Vector3(-0.6, 0.65, -0.45).normalize();
    for (let j = 0; j < HM.h; j++) for (let i = 0; i < HM.w; i++) {
      const g = (a, b) => HM.d[clamp(b, 0, HM.h - 1) * HM.w + clamp(a, 0, HM.w - 1)];
      const dx = (g(i + 1, j) - g(i - 1, j)) / (2 * cell) * EXAG, dz = (g(i, j + 1) - g(i, j - 1)) / (2 * cell) * EXAG;
      const v = clamp(new THREE.Vector3(-dx, 1, -dz).normalize().dot(L) * 1.25, 0, 1), k = (j * HM.w + i) * 4;
      im.data[k] = 0; im.data[k + 1] = 0; im.data[k + 2] = Math.round(150 + 105 * v); im.data[k + 3] = 255;
    }
    sc.putImageData(im, 0, 0); x3.imageSmoothingEnabled = true; x3.drawImage(sh, 0, 0, 4096, h3); }
  x3.globalCompositeOperation = 'lighter'; x3.lineCap = 'round';
  const spoke = (list, col, w) => { x3.strokeStyle = col; x3.lineWidth = w * s3; x3.beginPath();
    for (const [h, m] of list) { const [x, z] = hexXZ(h); for (let k = 0; k < 6; k++) if (m >> k & 1) { x3.moveTo(x * s3, z * s3); x3.lineTo((x + MID[k][0]) * s3, (z + MID[k][1]) * s3); } } x3.stroke(); };
  spoke(T.sr || [], '#b40000', 0.012); spoke(T.pr || [], '#ff0000', 0.022); spoke(T.hw || [], '#00ff00', 0.03);
  if (V7 && !V8) { x3.strokeStyle = '#a00000'; x3.lineWidth = Math.max(1.2, 0.006 * s3); x3.beginPath();       // farm tracks (dirt, ~6 m)
    for (const [cx, cz, rot, bw, bh, side] of tracks) { const c = Math.cos(rot), sn = Math.sin(rot), P = (a, b) => [(cx + a * c - b * sn) * s3, (cz + a * sn + b * c) * s3];
      const [a0, b0] = side ? P(-bw / 2, -bh / 2) : P(-bw / 2, -bh / 2), [a1, b1] = side ? P(bw / 2, -bh / 2) : P(-bw / 2, bh / 2);
      x3.moveTo(a0, b0); x3.lineTo(a1, b1); }
    x3.stroke(); }
  x3.globalCompositeOperation = 'source-over';
  const tex = cv => { const t = new THREE.CanvasTexture(cv); t.flipY = false; t.colorSpace = THREE.NoColorSpace; t.minFilter = THREE.LinearMipmapLinearFilter; t.magFilter = THREE.LinearFilter; t.anisotropy = 8; return t; };
  return [tex(c1), tex(c2), tex(c3)];
}

function woodTexture(w, h, seed, tone = [86, 58, 36]) {
  const cv = canvas(w, h), c = cv.getContext('2d'), R = rng(seed);
  c.fillStyle = `rgb(${tone})`; c.fillRect(0, 0, w, h);
  for (let k = 0; k < 220; k++) {
    const y = R() * h, a = 0.05 + R() * 0.12, dk = R() < 0.5;
    c.strokeStyle = dk ? `rgba(30,18,10,${a})` : `rgba(200,150,100,${a * 0.6})`; c.lineWidth = 1 + R() * 3;
    c.beginPath(); c.moveTo(0, y);
    for (let x = 0; x <= w; x += w / 16) c.lineTo(x, y + Math.sin(x / w * 6 + k) * (2 + R() * 4));
    c.stroke();
  }
  const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; t.wrapS = t.wrapT = THREE.RepeatWrapping; t.anisotropy = 8;
  return t;
}
function detailTexture() {                          // tileable value noise: r = fine, g = coarse (close-up ground detail)
  const N = 256, cv = canvas(N, N), c = cv.getContext('2d'), img = c.createImageData(N, N), R = rng(99);
  const oct = [8, 16, 32, 64].map(n => ({ n, v: Float32Array.from({ length: n * n }, () => R()) }));
  const val = (o, x, y) => { const n = o.n, fx = x / N * n, fy = y / N * n, x0 = Math.floor(fx), y0 = Math.floor(fy), ax = smooth(fx - x0), ay = smooth(fy - y0), g = (i, j) => o.v[((j % n + n) % n) * n + ((i % n + n) % n)]; return lerp(lerp(g(x0, y0), g(x0 + 1, y0), ax), lerp(g(x0, y0 + 1), g(x0 + 1, y0 + 1), ax), ay); };
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    const fine = 0.5 * val(oct[3], x, y) + 0.3 * val(oct[2], x, y) + 0.2 * R(), coarse = 0.6 * val(oct[0], x, y) + 0.4 * val(oct[1], x, y), k = (y * N + x) * 4;
    img.data[k] = fine * 255; img.data[k + 1] = coarse * 255; img.data[k + 2] = 128; img.data[k + 3] = 255;
  }
  c.putImageData(img, 0, 0);
  const t = new THREE.CanvasTexture(cv); t.wrapS = t.wrapT = THREE.RepeatWrapping; t.anisotropy = 8; return t;
}
function radialTexture(stops, size = 128) {
  const cv = canvas(size, size), c = cv.getContext('2d'), g = c.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  for (const [o, col] of stops) g.addColorStop(o, col);
  c.fillStyle = g; c.fillRect(0, 0, size, size);
  const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; return t;
}

function textSprite(text, o = {}) {
  const font = o.font || '700 44px "Noto Sans SC", "Oswald", sans-serif', pad = o.pad ?? 14;
  const m = canvas(8, 8).getContext('2d'); m.font = font;
  const tw = Math.ceil(m.measureText(text).width), th = o.lineH || 64;
  const cv = canvas(tw + pad * 2 + (o.edge ? 8 : 0), th), c = cv.getContext('2d');
  if (o.bg) {
    c.fillStyle = o.bg; c.globalAlpha = o.bgAlpha ?? 0.85;
    c.beginPath(); c.roundRect(1, 4, cv.width - 2, th - 8, 6); c.fill(); c.globalAlpha = 1;
    if (o.edge) { c.fillStyle = o.edge; c.fillRect(1, 4, 8, th - 8); }
  }
  c.font = font; c.textBaseline = 'middle'; c.textAlign = 'center';
  const cx = cv.width / 2 + (o.edge ? 4 : 0);
  if (o.stroke) { c.lineWidth = o.strokeW || 8; c.strokeStyle = o.stroke; c.lineJoin = 'round'; c.strokeText(text, cx, th / 2 + 2); }
  c.fillStyle = o.color || '#fff'; c.fillText(text, cx, th / 2 + 2);
  const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4;
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, depthTest: o.depthTest ?? true, toneMapped: false, sizeAttenuation: o.attenuate ?? true, fog: false });
  const sp = new THREE.Sprite(mat), hgt = o.height || 0.16;
  sp.scale.set(hgt * cv.width / th, hgt, 1); sp.center.set(0.5, o.anchorY ?? 0);
  sp.renderOrder = o.renderOrder ?? 10;
  return sp;
}

// ---------------------------------------------------------------- unit symbols (milsymbol)
const FUNC = { Tank: 'UCA', Recce: 'UCR', Inf: 'UCI', APC: 'UCIZ', SPArty: 'UCFHE', Arty: 'UCF', AD: 'UCD', Depot: 'USS', Utility: 'USS',
  Eng: 'UCE', HQ: 'UC', AT: 'UCAA', 'SP AT': 'UCAAAT', WLR: 'UUMRG' };
const ECH = { '.': 'B', '..': 'C', '...': 'D', 'I': 'E', 'II': 'F', 'III': 'G' };
function sidc(u) {
  const [, side, icon, size] = u, aff = side === 0 ? 'F' : 'H';
  if (icon === 'Helo') return 'S' + aff + 'APMHA-----';
  return ('S' + aff + 'GP' + (FUNC[icon] || 'U').padEnd(6, '-') + (icon === 'HQ' ? 'A' : '-') + (ECH[size] || '-')).padEnd(15, '-');
}
async function symbolImage(u, size = 70) {
  const sym = new window.ms.Symbol(sidc(u), { size, fill: true, frame: true, strokeWidth: 4, outlineWidth: 0 });
  const img = new Image(); img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(sym.asSVG()); await img.decode();
  return img;
}
function symbolTexture(u, img) {
  const cv = canvas(256, 188), c = cv.getContext('2d');
  const g = c.createLinearGradient(0, 0, 0, 188); g.addColorStop(0, '#f3ead3'); g.addColorStop(1, '#e2d5b4');
  c.fillStyle = g; c.fillRect(0, 0, 256, 188);
  c.strokeStyle = u[1] === 0 ? BLUE_DK : RED_DK; c.lineWidth = 10; c.strokeRect(5, 5, 246, 178);
  const sc = Math.min(200 / img.width, 150 / img.height);
  c.drawImage(img, 128 - img.width * sc / 2, 96 - img.height * sc / 2, img.width * sc, img.height * sc);
  const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8; return t;
}
function strengthCanvas(cv, alive, total, side) {
  const c = cv.getContext('2d'), w = cv.width, h = cv.height;
  c.fillStyle = '#1d1a16'; c.fillRect(0, 0, w, h);
  const n = Math.max(1, total), gap = n > 16 ? 2 : 4, cw = (w - 12 - gap * (n - 1)) / n;
  for (let k = 0; k < n; k++) {
    c.fillStyle = k < alive ? (side === 0 ? '#9cc3f2' : '#f2b0a4') : '#4a3d35';
    c.fillRect(6 + k * (cw + gap), 10, cw, h - 20);
  }
}
const TYPE_ZH = { Tank: '坦克', Recce: '侦察', Inf: '步兵', APC: '机步', HQ: '指挥', SPArty: '自行火炮', Arty: '炮兵', AD: '防空', Helo: '直升机',
  Depot: '补给', Eng: '工兵', AT: '反坦克', 'SP AT': '自行反坦克', WLR: '炮位雷达', Utility: '保障' };
const SIZE_ZH = { '.': '组', '..': '班', '...': '排', 'I': '连', 'II': '营', 'III': '团' };
// v4: Chinese unit names from SHOT.names ($ARENA_DATA/scenarios/<scenario>/unit-names-zh.json: original name -> { zh, short }); the
// screen shows the short form with a 蓝 / 红 prefix
let V4 = false, V5 = false, V6 = false, V7 = false, V8 = false, V9 = false, NAMES = null;
const unitZh = i => { const u = G.u[i], n = NAMES && NAMES[u[0]]; return n ? n.short : `${u[0]} ${TYPE_ZH[u[2]] || ''}${SIZE_ZH[u[3]] || ''}`; };
const sideZh = i => (G.u[i][1] === 0 ? '蓝 ' : '红 ') + unitZh(i);

// ---------------------------------------------------------------- state
let renderer, scene, camera, sun, rtA, rtB, postScene, postCam, matH, matV, composite, cctx, skyDome, ovlMesh, groundMat;
let ORDERS = [], NARR = [], SLIDES = [], themeCss = '', TOPFLAGS = null, LOGO_NAMES = null;
const LOGO_DIR = VIDEO + 'assets-staging/logos/';
let CAMD = 0, CAMF = 0.55, CAMB = 0, ZOOM = 1, MODELS = 0, SKY = 0, FXS = 1, units = [], vpObjs = [], labelObjs = [], fx = null, fontCss = '', overlayCache = { key: '', img: null };
let lossSchedule = [], shotSchedule = [], encoder = null, muxer = null, stats = { frames: 0, render_ms: 0, overlay_ms: 0, encode_wait_ms: 0 };
let camTarget = new THREE.Vector3(), detailU = { value: 0 };
let SYM = null;                                    // FPC symbology layer (shot.symbology, fpc-symbology.js); null = the layer is off
const BG_TABLE = new THREE.Color(0x15110d), BG_SKY = new THREE.Color(0xb9c6cf);

function buildBoard() {
  // terrain
  const SEG_X = 900, SEG_Z = Math.round(SEG_X * MAP_H / MAP_W);
  const geo = new THREE.PlaneGeometry(MAP_W, MAP_H, SEG_X, SEG_Z); geo.rotateX(-Math.PI / 2);
  const pos = geo.attributes.position;
  for (let k = 0; k < pos.count; k++) pos.setY(k, yAt(pos.getX(k) + MAP_W / 2, pos.getZ(k) + MAP_H / 2));
  geo.computeVertexNormals();
  const tex = new THREE.CanvasTexture(paintGround(8192)); tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = renderer.capabilities.getMaxAnisotropy(); tex.generateMipmaps = true; tex.minFilter = THREE.LinearMipmapLinearFilter;
  groundMat = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.93, metalness: 0 });
  const dtex = detailTexture();
  groundMat.onBeforeCompile = sh => {           // close-up detail: two octaves of tiled noise in world space
    sh.uniforms.tDetail = { value: dtex }; sh.uniforms.detailK = detailU; sh.uniforms.tCtrl = { value: ctrlTex }; sh.uniforms.ctrlK = ctrlU; sh.uniforms.mapWH = { value: new THREE.Vector2(MAP_W, MAP_H) };
    sh.vertexShader = sh.vertexShader.replace('#include <common>', '#include <common>\nvarying vec2 vWXZ;')
      .replace('#include <project_vertex>', '#include <project_vertex>\nvWXZ = (modelMatrix * vec4(transformed, 1.0)).xz;');
    sh.fragmentShader = sh.fragmentShader.replace('#include <common>', '#include <common>\nvarying vec2 vWXZ; uniform sampler2D tDetail; uniform float detailK; uniform sampler2D tCtrl; uniform float ctrlK; uniform vec2 mapWH;')
      .replace('#include <map_fragment>', '#include <map_fragment>\n{ float d1 = texture2D(tDetail, vWXZ * 31.0).r; float d2 = texture2D(tDetail, vWXZ * 4.3).g; float d3 = texture2D(tDetail, vWXZ * 160.0).r;\n  diffuseColor.rgb *= mix(1.0, (0.74 + 0.5 * d1) * (0.86 + 0.28 * d2) * (0.9 + 0.2 * d3), detailK);\n  vec4 ctl = texture2D(tCtrl, vWXZ / mapWH + 0.5); diffuseColor.rgb = mix(diffuseColor.rgb, ctl.rgb * 1.1, ctl.a * 0.4 * ctrlK); }');
  };
  if (V6) {                                       // real surface textures per terrain class + the painted map layer on top
    const [w1, w2, w3] = groundWeightsV6(), K = SHOT.ground.tile || {};
    groundMat.onBeforeCompile = sh => {
      Object.assign(sh.uniforms, { tGround: { value: GROUND6.tex }, tW1: { value: w1 }, tW2: { value: w2 }, tW3: { value: w3 }, tCtrl: { value: ctrlTex }, ctrlK: ctrlU, mapWH: { value: new THREE.Vector2(MAP_W, MAP_H) } });
      sh.vertexShader = sh.vertexShader.replace('#include <common>', '#include <common>\nvarying vec2 vWXZ;')
        .replace('#include <project_vertex>', '#include <project_vertex>\nvWXZ = (modelMatrix * vec4(transformed, 1.0)).xz;');
      const f = n => (+n).toFixed(3), TN = SHOT.ground.tint || {}, v3 = a => a ? `vec3(${a.map(f).join(', ')})` : 'vec3(1.0)';
      sh.fragmentShader = sh.fragmentShader.replace('#include <common>', `#include <common>
varying vec2 vWXZ; uniform highp sampler2DArray tGround; uniform sampler2D tW1, tW2, tW3, tCtrl; uniform float ctrlK; uniform vec2 mapWH;
vec3 gs(float layer, float sc) { vec2 uv = vWXZ * sc; return mix(texture(tGround, vec3(uv, layer)).rgb, texture(tGround, vec3(uv * 0.29 + vec2(0.37, 0.71), layer)).rgb, 0.45); }`)
        .replace('#include <map_fragment>', `{ vec4 ov = texture2D(map, vMapUv); vec2 wuv = vWXZ / mapWH + 0.5;
  vec4 w1 = texture2D(tW1, wuv), w2 = texture2D(tW2, wuv), w3 = texture2D(tW3, wuv);
  float woods = w2.r, town = w2.g, marsh = w2.b, open = clamp(1.0 - woods - town - marsh, 0.0, 1.0), stub = clamp(1.0 - w1.r - w1.g - w1.b, 0.0, 1.0);
  vec3 fld = ${V8 ? `gs(0.0, ${f(K.meadow ?? 16)}) * ${v3(TN.meadow)} * (1.0 + 0.16 * (w1.r - 0.5))` : `gs(0.0, ${f(K.meadow ?? 16)}) * ${v3(TN.meadow)} * w1.r + gs(1.0, ${f(K.pasture ?? 16)}) * ${v3(TN.pasture)} * w1.g + gs(2.0, ${f(K.ploughed ?? 12)}) * ${v3(TN.ploughed)} * w1.b + gs(3.0, ${f(K.stubble ?? 14)}) * ${v3(TN.stubble)} * stub`};
  vec3 col = fld * open + ${V7
    // v7: the field pattern runs on under the woods (clearings, meadow between the tree clusters) and the villages
    // (gardens and paddocks around gravel yards), so a close-up where the near trees / houses are cut away is not bare brown
    ? `mix(gs(4.0, ${f(K.woods ?? 18)}), gs(0.0, ${f(K.meadow ?? 16)}) * ${v3(TN.meadow)}, 0.7) * woods + mix(gs(7.0, ${f(K.town ?? 10)}) * 1.05, fld, 0.7) * town`
    : `mix(gs(8.0, 16.0), gs(4.0, ${f(K.woods ?? 18)}), 0.75) * woods + mix(gs(5.0, ${f(K.town ?? 10)}), gs(8.0, 16.0), 0.35) * town`} + gs(6.0, ${f(K.marsh ?? 16)}) * marsh;
  col = mix(col, gs(7.0, ${f(K.road ?? 40)}), clamp(w3.r, 0.0, 1.0)); col = mix(col, gs(5.0, ${f(K.road ?? 40)}) * 0.85, clamp(w3.g, 0.0, 1.0));
  col *= ${f(SHOT.ground.gain ?? 1.15)} * (1.0 - 0.45 * (1.0 - w3.b));
  col = mix(col, ov.rgb, ov.a);
  diffuseColor.rgb *= col;
  vec4 ctl = texture2D(tCtrl, wuv); diffuseColor.rgb = mix(diffuseColor.rgb, ctl.rgb * 1.1, ctl.a * 0.4 * ctrlK); }`);
    };
  }
  const ground = new THREE.Mesh(geo, groundMat);
  ground.receiveShadow = true; ground.castShadow = true; scene.add(ground);
  // cut sides of the terrain block (earth cross-section) down to the table
  const BOTTOM = -0.32, sideMat = new THREE.MeshStandardMaterial({ color: 0x5b4330, roughness: 1 });
  const wall = (pts) => {
    const g = new THREE.BufferGeometry(), v = [], idx = [];
    pts.forEach(([mx, mz], k) => { v.push(mx - MAP_W / 2, yAt(mx, mz), mz - MAP_H / 2, mx - MAP_W / 2, BOTTOM, mz - MAP_H / 2); if (k) { const a = 2 * k - 2; idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); } });
    g.setAttribute('position', new THREE.Float32BufferAttribute(v, 3)); g.setIndex(idx); g.computeVertexNormals();
    const m = new THREE.Mesh(g, sideMat); m.material.side = THREE.DoubleSide; m.receiveShadow = true; scene.add(m);
  };
  const N = 400;
  wall([...Array(N + 1)].map((_, k) => [k / N * MAP_W, 0]));
  wall([...Array(N + 1)].map((_, k) => [k / N * MAP_W, MAP_H]));
  wall([...Array(N + 1)].map((_, k) => [0, k / N * MAP_H]));
  wall([...Array(N + 1)].map((_, k) => [MAP_W, k / N * MAP_H]));
  // wooden frame
  const FW = 0.75, FT = 0.30, FB = -0.42, wood = woodTexture(1024, 128, 3);
  const frameMat = new THREE.MeshStandardMaterial({ map: wood, roughness: 0.55, metalness: 0.05, color: 0xb08a66 });
  const bar = (w, d, x, z, rot) => {
    const g = new THREE.BoxGeometry(w, FT - FB, d), m = new THREE.Mesh(g, frameMat);
    m.position.set(x, (FT + FB) / 2, z); m.rotation.y = rot || 0; m.castShadow = m.receiveShadow = true; scene.add(m);
    const lip = new THREE.Mesh(new THREE.BoxGeometry(w + 0.04, 0.05, 0.12), new THREE.MeshStandardMaterial({ color: 0xc9a46a, roughness: 0.35, metalness: 0.6 }));
    lip.position.set(x, FT + 0.025, z); lip.rotation.y = rot || 0; scene.add(lip);
  };
  bar(MAP_W + 2 * FW, FW, 0, -MAP_H / 2 - FW / 2);
  bar(MAP_W + 2 * FW, FW, 0, MAP_H / 2 + FW / 2);
  bar(MAP_H, FW, -MAP_W / 2 - FW / 2, 0, Math.PI / 2);
  bar(MAP_H, FW, MAP_W / 2 + FW / 2, 0, Math.PI / 2);
  // table
  const tt = woodTexture(1024, 1024, 9, [48, 33, 22]); tt.repeat.set(6, 6);
  const table = new THREE.Mesh(new THREE.PlaneGeometry(160, 120), new THREE.MeshStandardMaterial({ map: tt, roughness: 0.7, color: 0x9a8270 }));
  table.rotation.x = -Math.PI / 2; table.position.y = FB; table.receiveShadow = true; scene.add(table);
}

// miniature trees (woods hexes) and houses (town hexes): instanced, deterministic placement
function buildScenery() {
  const R = rng(4242), trees = [], houses = [];
  const inHex = (cx, cz) => { for (;;) { const x = (R() * 2 - 1) * A, z = (R() * 2 - 1) * HX / 2; if (Math.abs(x) * HX / 2 + Math.abs(z) * A / 2 <= A * HX / 2) return [cx + x, cz + z]; } };
  eachHex((c, r, i) => {
    const h = c * 100 + r, t = TERR[h]; if (WATER.has(h)) return;
    const [cx, cz] = [colX(c), rowZ(c, r)];
    const nt = t === 2 ? 60 : t === 1 ? 22 : t === 0 ? 2 : t === 3 ? 3 : 0;
    for (let k = 0; k < nt; k++) trees.push([...inHex(cx, cz), 0.7 + R() * 0.6, R(), t]);
    const hi = (T.hr || []).includes(h);
    const nh = t === 3 ? 26 : t === 0 ? (R() < 0.25 ? 3 : 0) : 0;
    for (let k = 0; k < nh; k++) houses.push([...inHex(cx, cz), R() * Math.PI, 0.7 + R() * 0.7, R(), hi]);
  });
  // tree: cone crown + short trunk, merged by hand into one geometry with vertex colours
  const crown = new THREE.ConeGeometry(0.011, 0.034, 7, 1); crown.translate(0, 0.024, 0);
  const trunk = new THREE.CylinderGeometry(0.0018, 0.0022, 0.008, 5); trunk.translate(0, 0.004, 0);
  const treeGeo = mergeColoured([[crown, 0x5d7a45], [trunk, 0x5a4330]]);
  const treeMat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.9 }); nearCull(treeMat);
  const tm = new THREE.InstancedMesh(treeGeo, treeMat, trees.length), m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), col = new THREE.Color();
  trees.forEach(([x, z, s, v, t], k) => {
    m4.compose(new THREE.Vector3(x - MAP_W / 2, yAt(x, z) - 0.002, z - MAP_H / 2), q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), v * 6.28), new THREE.Vector3(s, s * (0.85 + v * 0.4), s));
    tm.setMatrixAt(k, m4); col.setHSL(0.2 + v * 0.08, 0.3 + v * 0.15, t === 2 ? 0.42 + v * 0.1 : 0.5 + v * 0.1); tm.setColorAt(k, col);
  });
  tm.castShadow = true; tm.receiveShadow = true; scene.add(tm);
  // house: box body + gable roof
  const body = new THREE.BoxGeometry(0.014, 0.009, 0.01); body.translate(0, 0.0045, 0);
  const roof = new THREE.ConeGeometry(0.0072, 0.006, 4, 1); roof.rotateY(Math.PI / 4); roof.scale(0.014 / (0.0072 * Math.SQRT2) * 1.08, 1, 0.01 / (0.0072 * Math.SQRT2) * 1.1); roof.translate(0, 0.012, 0);
  const houseGeo = mergeColoured([[body, 0xe6ddc8], [roof, 0xa35540]]);
  const houseMat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.8 }); nearCull(houseMat);
  const hm = new THREE.InstancedMesh(houseGeo, houseMat, houses.length);
  houses.forEach(([x, z, rot, s, v, hi], k) => {
    const tall = hi ? 2.6 + v * 2 : 1;
    m4.compose(new THREE.Vector3(x - MAP_W / 2, yAt(x, z) - 0.002, z - MAP_H / 2), q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), rot), new THREE.Vector3(s, s * tall, s));
    hm.setMatrixAt(k, m4); col.setHSL(0.08, 0.15, 0.82 + v * 0.18); hm.setColorAt(k, col);
  });
  hm.castShadow = true; hm.receiveShadow = true; scene.add(hm);
  return { trees: trees.length, houses: houses.length };
}
// v5 scenery: the game's own map items instead of cones and boxes (HOI4 base game, hoi4/scenery_models.json -> glb-scenery):
// tree clusters (pines, beeches) in woods hexes, western town blocks in town hexes, the odd copse / farm block in open
// country; instanced, deterministic placement, same near / sight-line culling as before. SHOT.scenery: { base, items:
// { key: { file, extent (km, footprint of one cluster), bright } }, woods: [keys], town: [keys] }
const loadGlb = url => new GLTFLoader().loadAsync(url);
function glbParts(gltf, bright = 1) {                // [{ g: geometry with the node transform baked, m: material }], size
  const root = gltf.scene; root.updateMatrixWorld(true); const parts = [], bb = new THREE.Box3();
  // v7: a HOI4 tree cluster carries all its LODs (lodd*Shape) and a stray 3-vertex helper below the ground; keep only the
  // most detailed LOD and drop helpers (else the LODs overlap and the helper lifts the whole cluster off the ground)
  let keep = null;
  if (V7) { const ms = []; root.traverse(o => { if (o.isMesh) ms.push(o); });
    const lods = ms.filter(o => /lod/i.test(o.name)), best = lods.reduce((a, b) => (!a || b.geometry.attributes.position.count > a.geometry.attributes.position.count ? b : a), null);
    keep = new Set(ms.filter(o => o.geometry.attributes.position.count >= 12 && (!/lod/i.test(o.name) || o === best))); }
  root.traverse(o => { if (!o.isMesh || (keep && !keep.has(o))) return; const g = o.geometry.clone(); g.applyMatrix4(o.matrixWorld); g.computeBoundingBox(); bb.union(g.boundingBox);
    const m = o.material.clone(); if (m.color) m.color.multiplyScalar(bright); m.roughness = 0.9; m.metalness = 0; if (m.map) m.map.anisotropy = 4; parts.push({ g, m }); });
  const sz = bb.getSize(new THREE.Vector3()), c = bb.getCenter(new THREE.Vector3());
  for (const p of parts) p.g.translate(-c.x, -bb.min.y, -c.z);
  return { parts, sz };
}
const SCEN_IMS = [];
async function buildSceneryV5() {
  const S = SHOT.scenery, R = rng(4242), kinds = {};
  for (const [k, d] of Object.entries(S.items)) kinds[k] = { ...glbParts(await loadGlb(S.base + d.file + '.glb'), d.bright ?? 1.2), d, list: [] };
  const inHex = (cx, cz, f) => { for (;;) { const x = (R() * 2 - 1) * A * f, z = (R() * 2 - 1) * HX / 2 * f; if (Math.abs(x) * HX / 2 + Math.abs(z) * A / 2 <= A * HX / 2 * f) return [cx + x, cz + z]; } };
  const pick = a => a[Math.floor(R() * a.length)];
  eachHex((c, r) => {
    const h = c * 100 + r, t = TERR[h]; if (WATER.has(h)) return;
    const cx = colX(c), cz = rowZ(c, r);
    const add = (keys, n, f, s0 = 1) => { for (let j = 0; j < n; j++) { const [x, z] = inHex(cx, cz, f); kinds[pick(keys)].list.push([x, z, R() * 6.283, s0 * (0.85 + R() * 0.3)]); } };
    if (t === 2) add(S.woods, 4, 0.75);
    else if (t === 1) add(S.woods, 2, 0.7, 0.85);
    else if (t === 3) add(S.town, 2, 0.55);
    else if (t === 0) { if (R() < 0.12) add(S.woods, 1, 0.7, 0.6); if (R() < 0.06) add(S.town, 1, 0.6, 0.55); }
  });
  const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), out = {};
  for (const [k, K] of Object.entries(kinds)) {
    if (!K.list.length) continue;
    const s0 = K.d.extent / Math.max(K.sz.x, K.sz.z);
    for (const p of K.parts) {
      nearCull(p.m);
      const im = new THREE.InstancedMesh(p.g, p.m, K.list.length);
      const dm = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking, map: p.m.map || null, alphaTest: p.m.alphaTest || 0 }); nearCull(dm); im.customDepthMaterial = dm;
      K.list.forEach(([x, z, rot, s], j) => {
        m4.compose(new THREE.Vector3(x - MAP_W / 2, yAt(x, z) - 0.003, z - MAP_H / 2), q.setFromAxisAngle(UP, rot), new THREE.Vector3(s0 * s, s0 * s * (K.d.yScale ?? 1), s0 * s));
        im.setMatrixAt(j, m4);
      });
      im.castShadow = true; im.receiveShadow = true; scene.add(im); SCEN_IMS.push(im);
    }
    out[k] = K.list.length;
  }
  return out;
}
const nearU = { value: 0 }, sightA = { value: new THREE.Vector3() }, sightB = { value: new THREE.Vector3() }, sightR = { value: 0 };
// instances closer than nearU km to the camera collapse (no foreground walls); close-ups (SHOT.clearR) also clear a
// corridor of sightR km along camera -> target, widening towards the target (trees no longer hide the fight)
function nearCull(mat) {
  mat.onBeforeCompile = sh => {
    Object.assign(sh.uniforms, { uNear: nearU, uSA: sightA, uSB: sightB, uSR: sightR });
    sh.vertexShader = sh.vertexShader.replace('#include <common>', '#include <common>\nuniform float uNear; uniform vec3 uSA; uniform vec3 uSB; uniform float uSR;')
      .replace('#include <begin_vertex>', `#include <begin_vertex>
#ifdef USE_INSTANCING
{ vec4 ip = modelMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0); if (distance(ip.xyz, ${V7 ? 'uSA' : 'cameraPosition'}) < uNear) transformed *= 0.0;
  if (uSR > 0.0) { vec2 a = uSA.xz, b = uSB.xz, d = b - a; float t = clamp(dot(ip.xz - a, d) / max(dot(d, d), 1e-8), 0.0, 1.08);
    if (distance(ip.xz, a + d * t) < uSR * (0.45 + 0.55 * t)) transformed *= 0.0; } }
#endif`);
  };
}
function mergeColoured(parts) {                     // [[geometry, hex colour]] -> one non-indexed geometry with a colour attribute
  const pos = [], nor = [], colr = [], c = new THREE.Color();
  for (const [g0, hex] of parts) {
    const g = g0.index ? g0.toNonIndexed() : g0; c.set(hex);
    pos.push(...g.attributes.position.array); nor.push(...g.attributes.normal.array);
    for (let k = 0; k < g.attributes.position.count; k++) colr.push(c.r, c.g, c.b);
  }
  const out = new THREE.BufferGeometry();
  out.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3)); out.setAttribute('normal', new THREE.Float32BufferAttribute(nor, 3));
  out.setAttribute('color', new THREE.Float32BufferAttribute(colr, 3));
  return out;
}

function hexRibbon(h, color, lift = 0.012, wdt = 0.03) {
  const [cx, cz] = hexXZ(h), g = new THREE.BufferGeometry(), v = [], idx = [];
  const STEPS = 6;
  for (let i = 0; i <= 6 * STEPS; i++) {
    const k = Math.floor(i / STEPS) % 6, f = (i % STEPS) / STEPS, p = CORNER[k], q = CORNER[(k + 1) % 6];
    const x = p[0] + (q[0] - p[0]) * f, z = p[1] + (q[1] - p[1]) * f, r = Math.hypot(x, z), ix = x * (1 - wdt / r), iz = z * (1 - wdt / r);
    v.push(cx + x - MAP_W / 2, yAt(cx + x, cz + z) + lift, cz + z - MAP_H / 2, cx + ix - MAP_W / 2, yAt(cx + ix, cz + iz) + lift, cz + iz - MAP_H / 2);
    if (i) { const a = 2 * i - 2; idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); }
  }
  g.setAttribute('position', new THREE.Float32BufferAttribute(v, 3)); g.setIndex(idx);
  const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.9, side: THREE.DoubleSide, depthWrite: false, toneMapped: false }));
  m.renderOrder = 2; return m;
}
function hexFill(h, color, lift = 0.01) {
  const [cx, cz] = hexXZ(h), v = [], idx = [], STEPS = 6, n = 6 * STEPS;
  v.push(cx - MAP_W / 2, yAt(cx, cz) + lift, cz - MAP_H / 2);
  for (let i = 0; i < n; i++) {
    const k = Math.floor(i / STEPS), f = (i % STEPS) / STEPS, p = CORNER[k], q = CORNER[(k + 1) % 6], x = cx + p[0] + (q[0] - p[0]) * f, z = cz + p[1] + (q[1] - p[1]) * f;
    v.push(x - MAP_W / 2, yAt(x, z) + lift, z - MAP_H / 2); idx.push(0, 1 + i, 1 + (i + 1) % n);
  }
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(v, 3)); g.setIndex(idx);
  const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.25, side: THREE.DoubleSide, depthWrite: false, toneMapped: false }));
  m.renderOrder = 1; return m;
}

// v5 objective markers: a flag pin stuck in the hex (Kenney Minigolf Kit flag, CC0; blue / red / green = neutral) and a
// numbered sign "(c,r) · value" above it; the hex outline and fill in the owner's colour stay
let FLAGS = null, VPSTAR = null;
function buildVPV5() {
  for (const [h, val, owner0] of (G.vp || T.vp)) {
    const [mx, mz] = hexXZ(h), base = W(mx, mz), grp = new THREE.Group();
    const ribbon = { 0: hexRibbon(h, BLUE), 1: hexRibbon(h, RED), 2: hexRibbon(h, '#d8d2c4') };
    const fill = { 0: hexFill(h, BLUE), 1: hexFill(h, RED), 2: hexFill(h, '#d8d2c4') };
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.85, 1, 48), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, depthWrite: false, side: THREE.DoubleSide, toneMapped: false }));
    ring.rotation.x = -Math.PI / 2; ring.position.set(base.x, base.y + 0.06, base.z); ring.renderOrder = 22; ring.visible = false;
    let star = null;
    if (V7) { star = new THREE.Sprite(new THREE.SpriteMaterial({ map: VPSTAR[2], transparent: true, depthWrite: false, depthTest: false, toneMapped: false, fog: false }));
      star.center.set(0.5, 0.15); star.renderOrder = 25; star.userData.mxz = [mx, mz]; star.position.copy(W(mx - 0.1, mz - 0.1)); star.position.y += 0.004; scene.add(star); }   // off the hex centre (units' slots lie on the other diagonal)
    const flags = V7 ? [] : [0, 1, 2].map(k => {
      const F = FLAGS[k], f = F.scene.clone(true), holder = new THREE.Group();
      f.position.set(-F.c.x, -F.min.y, -F.c.z); holder.add(f); holder.userData.s = F.s;
      f.traverse(o => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
      holder.position.copy(W(mx - 0.11, mz - 0.11)); holder.rotation.y = 0.6; return holder;
    });
    grp.add(ribbon[0], ribbon[1], ribbon[2], fill[0], fill[1], fill[2], ring, ...flags); scene.add(grp);
    const label = textSprite(`${hexName(h)} · ${val} 分`, { font: '600 46px "Oswald", "Noto Sans SC", sans-serif', color: '#fff5d6', bg: '#2a241c', bgAlpha: 0.82, height: 0.13, pad: 12, lineH: 62 });
    label.position.set(base.x, base.y + 0.42, base.z); scene.add(label);
    vpObjs.push({ h, val, owner0, flags, ribbon, fill, label, base, ring, star });
  }
}
function updateVPV5(v, g) {
  const closeUp = MODELS >= 0.5, z = ZOOM * (closeUp ? 0.07 : 1);
  for (const o of vpObjs) {
    const own = vpOwner(o.h, o.owner0, g), b = o.base;
    for (const k of [0, 1, 2]) { o.ribbon[k].visible = own === k && !closeUp; o.fill[k].visible = own === k && !closeUp;
      const f = o.flags[k]; if (f) { f.visible = own === k; f.scale.setScalar(f.userData.s * z); } }
    if (o.star) { const [sx, sz] = o.star.userData.mxz; o.star.position.copy(W(sx - 0.1, sz - 0.1)); o.star.position.y += 0.004;
      o.star.material.map = VPSTAR[own] || VPSTAR[2]; const px = (SHOT.vpPx || [34, 46])[closeUp ? 1 : 0], th = Math.tan(camera.fov * Math.PI / 360),
      d = camera.position.distanceTo(o.star.position), hh = px / SHOT.height * 2 * d * th; o.star.scale.set(hh * 116 / 108, hh, 1); }
    o.label.position.y = b.y + 0.4 * z;
    const im = o.label.material.map.image; let lz = closeUp ? 0.12 : z;   // SHOT.vpLabelPx: close-up sign at most that many pixels tall
    if (closeUp && SHOT.vpLabelPx) { const th = Math.tan(camera.fov * Math.PI / 360), d = camera.position.distanceTo(o.label.position);
      lz = Math.min(lz, SHOT.vpLabelPx / SHOT.height * 2 * d * th / 0.13); }
    o.label.scale.set(0.13 * lz * im.width / im.height, 0.13 * lz, 1);
    if (V6) o.label.visible = closeUp;
    o.ring.visible = false;
    if (CAPTURES.length) continue;               // captures play the save-point effect (buildCaptures), no ring
    if (SHOT.vpRing === false) continue;         // as in the game: a capture only turns the objective to the new owner's colour
    for (const [t, hh, ow] of G.vc || []) {
      if (hh !== o.h) continue; const age = v - vOf(t);
      if (age >= 0 && age < 3) { o.ring.visible = true; o.ring.scale.setScalar(0.25 + age * 0.35); o.ring.material.color.set(ow === 0 ? '#8fc0ff' : ow === 1 ? '#ff8a76' : '#ffffff'); o.ring.material.opacity = 1 - age / 3; }
    }
  }
}
// objective captures (SHOT.vpCapture { base, size, light }): every change of owner inside the cut plays the RPG
// save-point effect of ics-creative/160304_threejs_save_point (vendor/savepoint/savepoint.js, MIT) on the hex, in the new
// owner's colour, and flashes the objective's label once. Both run on GSAP's global timeline, which the renderer drives
// with the video clock (gsap.updateRoot(v) in renderFrame), so every frame is a function of the video time.
let CAPTURES = [];
const gsap = window.gsap;
if (gsap) { gsap.ticker.remove(gsap.updateRoot); gsap.ticker.lagSmoothing(0); }
async function buildCaptures() {
  if (!SHOT.vpCapture || !V5) return;
  const C = SHOT.vpCapture, R = Math.hypot(CORNER[0][0], CORNER[0][1]);
  for (const [t, h, ow] of G.vc || []) {
    const o = vpObjs.find(x => x.h === h); if (!o) continue;
    const at = vOf(t); if (at < -0.05 || at > SHOT.duration) continue;
    const flash = { v: 0 };
    gsap.globalTimeline.add(gsap.timeline().to(flash, { v: 1, duration: 0.14, ease: 'power2.out' }).to(flash, { v: 0, duration: 0.65, ease: 'power2.out' }), Math.max(0, at));
    const fx = new SavePoint({ base: C.base, tint: ow === 1 ? 'red' : 'blue', rng: rng(4099 + t * 7 + h), light: C.light !== false });
    const P = W(...hexXZ(h)); fx.position.copy(P); fx.scale.setScalar(R / 4.25 * (C.size || 1)); fx.renderOrder = 21;
    scene.add(fx); fx.play(Math.max(0, at));
    CAPTURES.push({ t, h, ow, o, fx, flash, at });
  }
}
function updateCaptures(v) {
  if (!CAPTURES.length) return;
  gsap.updateRoot(v);
  for (const c of CAPTURES) if (c.fx.visible) c.fx.update(1 / FPS);
}
function captureAt(h, v) {                          // the latest capture of objective h that is still flashing
  let best = null;
  for (const c of CAPTURES) { if (c.h !== h) continue; const age = v - c.at; if (age >= 0 && age <= 1 && (!best || c.t > best.t)) best = c; }
  return best;
}
function buildVP() {
  if (V5) return buildVPV5();
  const shape = new THREE.Shape();
  for (let i = 0; i < 10; i++) { const r = i % 2 ? 0.07 : 0.16, a = Math.PI / 2 + i * Math.PI / 5; i ? shape.lineTo(r * Math.cos(a), r * Math.sin(a)) : shape.moveTo(r * Math.cos(a), r * Math.sin(a)); }
  const starGeo = new THREE.ExtrudeGeometry(shape, { depth: 0.035, bevelEnabled: true, bevelThickness: 0.01, bevelSize: 0.01, bevelSegments: 2 });
  starGeo.center();
  for (const [h, val, owner0] of (G.vp || T.vp)) {
    const [mx, mz] = hexXZ(h), grp = new THREE.Group(), base = W(mx, mz);
    const disc = new THREE.Mesh(new THREE.CylinderGeometry(0.13, 0.15, 0.04, 32), new THREE.MeshStandardMaterial({ color: 0x777777, roughness: 0.5 }));
    disc.position.set(base.x, base.y + 0.02, base.z); disc.castShadow = true;
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.008, 0.22, 8), new THREE.MeshStandardMaterial({ color: 0x3a3127 }));
    pole.position.set(base.x, base.y + 0.13, base.z);
    const star = new THREE.Mesh(starGeo, new THREE.MeshStandardMaterial({ color: 0xf0c75a, roughness: 0.35, metalness: 0.15, emissive: 0x6a4a08 }));
    star.position.set(base.x, base.y + 0.32, base.z); star.castShadow = true;
    const ribbon = { 0: hexRibbon(h, BLUE), 1: hexRibbon(h, RED), 2: hexRibbon(h, '#d8d2c4') };
    const fill = { 0: hexFill(h, BLUE), 1: hexFill(h, RED), 2: hexFill(h, '#d8d2c4') };
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.85, 1, 48), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, depthWrite: false, side: THREE.DoubleSide, toneMapped: false }));
    ring.rotation.x = -Math.PI / 2; ring.position.set(base.x, base.y + 0.06, base.z); ring.renderOrder = 22; ring.visible = false;
    grp.add(disc, pole, star, ribbon[0], ribbon[1], ribbon[2], fill[0], fill[1], fill[2], ring); scene.add(grp);
    const label = textSprite(`${hexName(h)} · ${val} 分`, { font: '600 46px "Oswald", sans-serif', color: '#fff5d6', bg: '#2a241c', bgAlpha: 0.82, height: 0.13, pad: 12, lineH: 62 });
    label.position.set(base.x, base.y + 0.45, base.z); scene.add(label);
    vpObjs.push({ h, val, owner0, disc, star, ribbon, fill, pole, label, base, ring });
  }
}
let marks = [];                                    // gold hex outlines on the target hexes of the current moment
function buildMarks() { for (const m of SHOT.moments || []) for (const h of m.targets || [m.target]) { const r = hexRibbon(h, '#f3c84b', 0.03, 0.05); r.visible = false; scene.add(r); marks.push({ m, r }); } }
function updateMarks(v, g) { for (const { m, r } of marks) { const on = !V5 && g >= (m.show ?? m.from) && g <= m.to + (m.hold ?? 600) && MODELS < 0.5; r.visible = on; if (on) r.material.opacity = V5 ? 0.75 : 0.55 + 0.4 * Math.sin(v * 4.5) ** 2; } }
function vpOwner(h, owner0, g) { let o = owner0; for (const [t, hh, ow] of G.vc || []) if (hh === h && t <= g) o = ow; return o; }

// ---------------------------------------------------------------- unit classes -> open-licensed models (models.js, credits.md)
// Board ("global") view: every unit is a diorama stand: side-coloured base, 1-4 figures of its class at figurine scale
// (figure count = surviving share), and a flag above it (NATO symbol, name, type, strength pips; "×n" when stands merge).
// Close-ups: the same models at x5 scale (1 m = 5 m) on the terrain, one vehicle or a 3-man team per slot.
const CLS = { Tank: 'tank', APC: 'ifv', 'SP AT': 'spg', SPArty: 'spg', Arty: 'gun', AT: 'inf', Inf: 'inf', Eng: 'inf', Recce: 'jeep', HQ: 'jeep',
  Utility: 'truck', Depot: 'truck', WLR: 'truck', AD: 'truck', Helo: 'heli' };
const classOf = u => (SHOT.classMap && SHOT.classMap[u[2]]) || (KITMOD.CLASS_MAP || CLS)[u[2]] || 'truck';
const kitKey = (cls, side) => KITMOD.KIT && KITMOD.KIT[cls + (side ? 'R' : 'B')] ? cls + (side ? 'R' : 'B') : cls === 'tank' ? (side ? 'tankR' : 'tankB') : cls;
const CLASS_ZH_V2 = { tank: '坦克', ifv: '步兵战车', spg: '自行火炮', gun: '牵引火炮', inf: '步兵', jeep: '轻型越野车', truck: '军用卡车', heli: '直升机' };
const FIGV = 0.021, FIGI = 0.034, FIGH = 0.012;   // figurine scale on the board, km per metre (vehicles / soldiers / helicopters)
const MS = 0.005;                                   // close-up scale: km per metre (x5; soldiers x9 so a fire team reads next to the houses)
const LAYOUT = {
  v: [[[0, 0]], [[-0.065, 0.04], [0.065, -0.04]], [[0, 0.08], [-0.085, -0.055], [0.085, -0.055]]],
  i: [[[0, 0]], [[-0.04, 0.012], [0.04, -0.012]], [[0, 0.05], [-0.055, -0.03], [0.055, -0.03]], [[0, 0.06], [-0.065, 0.005], [0.065, 0.005], [0, -0.06]]],
};
const standCap = o => o.cls === 'heli' ? 1 : o.cls === 'inf' ? (o.u[5] >= 6 ? 4 : 3) : (o.u[5] >= 6 ? 3 : 2);
const figsFor = (o, alive) => alive <= 0 ? 0 : Math.max(1, Math.min(o.cap, Math.round(o.cap * alive / Math.max(1, o.u[5]))));
const FLAG_W = 512, FLAG_H = 172, FLAG_HGT = 0.12;
// HOI4-style map counter (theme.counter: plate / icons per unit type, loaded as images in init): side-tinted plate with
// the unit-type icon, name, type and a strength bar in pips; "×n" shows like the game's division count
let COUNTER = null, ARROW = null, arrowMesh = null;
function drawCounter(o, alive, badge) {
  const c = o.flagCv.getContext('2d'), K = COUNTER, sc = K.side[o.side];
  c.clearRect(0, 0, FLAG_W, FLAG_H);
  const ph = 122;                                          // plate height; bars below
  if (!K.tinted[o.side]) {                                 // plate tinted with the side colour once (multiply, alpha kept)
    const t = canvas(FLAG_W, ph), x = t.getContext('2d'), P = K.plate, fw = P.width / (K.plateFrames || 1);
    x.drawImage(P, 0, 0, fw, P.height, 0, 0, FLAG_W, ph);
    x.globalCompositeOperation = 'multiply'; x.fillStyle = sc.plate; x.fillRect(0, 0, FLAG_W, ph);
    x.globalCompositeOperation = 'destination-in'; x.drawImage(P, 0, 0, fw, P.height, 0, 0, FLAG_W, ph);
    K.tinted[o.side] = t;
  }
  c.drawImage(K.tinted[o.side], 0, 0);
  c.fillStyle = 'rgba(12,12,10,.78)'; c.fillRect(14, 12, 150, ph - 24);             // icon slot
  c.strokeStyle = 'rgba(0,0,0,.9)'; c.lineWidth = 3; c.strokeRect(14, 12, 150, ph - 24);
  const ic = K.icons[o.u[2]] || K.icons.Inf;
  if (ic) { const fw = ic.width / (K.iconFrames || 1), s = Math.min(138 / fw, (ph - 34) / ic.height); c.drawImage(ic, K.iconFrame * fw, 0, fw, ic.height, 89 - fw * s / 2, ph / 2 - ic.height * s / 2, fw * s, ic.height * s); }
  let fs = 46; c.font = `700 ${fs}px "Oswald","Noto Sans SC",sans-serif`;
  const room = FLAG_W - 196 - (badge > 1 ? 78 : 12);
  while (c.measureText(o.u[0]).width > room && fs > 24) { fs -= 2; c.font = `700 ${fs}px "Oswald","Noto Sans SC",sans-serif`; }
  c.textBaseline = 'middle'; c.lineWidth = 6; c.strokeStyle = 'rgba(0,0,0,.85)'; c.strokeText(o.u[0], 180, 40); c.fillStyle = '#ffffff'; c.fillText(o.u[0], 180, 40);
  c.font = '700 30px "Noto Sans SC",sans-serif'; c.strokeText(`${TYPE_ZH[o.u[2]] || ''}${SIZE_ZH[o.u[3]] || ''}`, 180, 88); c.fillStyle = '#f2e7c4'; c.fillText(`${TYPE_ZH[o.u[2]] || ''}${SIZE_ZH[o.u[3]] || ''}`, 180, 88);
  if (badge > 1) { c.fillStyle = 'rgba(10,10,8,.85)'; c.fillRect(FLAG_W - 78, 14, 64, 56); c.strokeStyle = '#000'; c.strokeRect(FLAG_W - 78, 14, 64, 56);
    c.fillStyle = '#ffffff'; c.font = '700 40px "Oswald",sans-serif'; c.textAlign = 'center'; c.fillText(String(badge), FLAG_W - 46, 43); c.textAlign = 'left'; }
  // strength bar (HOI4 green) on a dark track
  c.fillStyle = 'rgba(8,8,6,.92)'; c.fillRect(4, ph + 6, FLAG_W - 8, FLAG_H - ph - 12);
  const n = Math.max(1, o.u[5]), gap = n > 16 ? 2 : 3, x0 = 9, w = FLAG_W - 18, cw = (w - gap * (n - 1)) / n;
  for (let k = 0; k < n; k++) { c.fillStyle = k < alive ? K.bar : K.barLost; c.fillRect(x0 + k * (cw + gap), ph + 11, cw, FLAG_H - ph - 22); }
  o.flagTex.needsUpdate = true;
}
// v4 counter: drawn as vectors on the canvas (no bitmap is enlarged): plate in the HOI4 colours, side-coloured symbol
// block with the NATO symbol (milsymbol SVG at 2x), Chinese short name, type, one continuous strength bar with the
// numbers; a stack of n units in one hex shows two shadow plates behind it and an "n" box like the game's division count
function drawCounterV4(o, alive, total, badge) {
  const c = o.flagCv.getContext('2d'), W2 = FLAG_W, side = o.side;
  c.clearRect(0, 0, W2, FLAG_H);
  const y0 = 16, ph = 112, x1 = W2 - 18;
  const plate = (dx, dy, a) => { c.globalAlpha = a; c.fillStyle = '#121311'; c.beginPath(); c.roundRect(dx, dy, x1 - 2, ph, 7); c.fill(); c.globalAlpha = 1; };
  if (badge > 1) { plate(16, 0, 0.75); plate(8, 8, 0.9); }
  const g = c.createLinearGradient(0, y0, 0, y0 + ph); g.addColorStop(0, '#3d3e39'); g.addColorStop(0.5, '#2a2b27'); g.addColorStop(1, '#1b1c19');
  c.fillStyle = g; c.beginPath(); c.roundRect(0, y0, x1, ph, 7); c.fill();
  c.lineWidth = 4; c.strokeStyle = '#0a0a09'; c.stroke();
  c.lineWidth = 1.5; c.strokeStyle = 'rgba(214,206,170,.35)'; c.beginPath(); c.roundRect(4, y0 + 4, x1 - 8, ph - 8, 5); c.stroke();
  // side block with the symbol
  const sg = c.createLinearGradient(0, y0, 0, y0 + ph); sg.addColorStop(0, side ? '#d0594b' : '#5b8fd9'); sg.addColorStop(1, side ? '#7e241c' : '#22488a');
  c.fillStyle = sg; c.beginPath(); c.roundRect(6, y0 + 6, 150, ph - 12, 4); c.fill();
  const im = o.symHi || o.img, s2 = Math.min(132 / im.width, (ph - 22) / im.height);
  c.drawImage(im, 81 - im.width * s2 / 2, y0 + ph / 2 - im.height * s2 / 2, im.width * s2, im.height * s2);
  // name + type
  const name = unitZh(o.i), room = x1 - 172 - (badge > 1 ? 70 : 10);
  let fs = 46; c.font = `900 ${fs}px "Noto Sans SC",sans-serif`;
  while (c.measureText(name).width > room && fs > 24) { fs -= 2; c.font = `900 ${fs}px "Noto Sans SC",sans-serif`; }
  c.textBaseline = 'middle'; c.lineJoin = 'round'; c.lineWidth = 6; c.strokeStyle = '#000';
  c.strokeText(name, 168, y0 + 38); c.fillStyle = '#ffffff'; c.fillText(name, 168, y0 + 38);
  c.font = '700 28px "Noto Sans SC",sans-serif'; const ty = `${TYPE_ZH[o.u[2]] || ''}${SIZE_ZH[o.u[3]] || ''}` + (badge > 1 ? ` 等 ${badge} 个单位` : '');
  c.lineWidth = 5; c.strokeText(ty, 168, y0 + 84); c.fillStyle = '#f2c84b'; c.fillText(ty, 168, y0 + 84);
  if (badge > 1) {
    c.fillStyle = '#0d0d0c'; c.fillRect(x1 - 64, y0 + 8, 56, 52); c.strokeStyle = '#f2c84b'; c.lineWidth = 3; c.strokeRect(x1 - 64, y0 + 8, 56, 52);
    c.fillStyle = '#ffffff'; c.font = '700 40px "Oswald",sans-serif'; c.textAlign = 'center'; c.fillText(String(badge), x1 - 36, y0 + 36); c.textAlign = 'left';
  }
  // strength bar: continuous, HOI4 green, with the numbers
  const by = y0 + ph + 6, bh = FLAG_H - by - 4, f = clamp(alive / Math.max(1, total), 0, 1);
  c.fillStyle = '#0b0b0a'; c.fillRect(0, by, x1, bh);
  const bg = c.createLinearGradient(0, by, 0, by + bh); bg.addColorStop(0, f > 0.5 ? '#7fcf4e' : f > 0.25 ? '#e0b43c' : '#e0533c'); bg.addColorStop(1, f > 0.5 ? '#3f8a25' : f > 0.25 ? '#9a7417' : '#8f2a1c');
  c.fillStyle = bg; c.fillRect(4, by + 4, (x1 - 8) * f, bh - 8);
  c.font = '700 30px "Oswald",sans-serif'; c.textAlign = 'right'; c.lineWidth = 5; c.strokeStyle = '#000'; const nt = `${alive} / ${total}`;
  c.strokeText(nt, x1 - 10, by + bh / 2 + 1); c.fillStyle = '#fff'; c.fillText(nt, x1 - 10, by + bh / 2 + 1); c.textAlign = 'left';
  o.flagTex.needsUpdate = true;
}
// v6: the full counter is the game's on-map unit counter (plate tinted with the side colour, unit icon, dark count box),
// a name tab cut from the counter overlay sprite and the game's strength bar; the close-up plate uses the same parts
let CT6 = null;
function tintSprite(im, sx, sw, col) { const t = canvas(sw, im.height), x = t.getContext('2d'); x.drawImage(im, sx, 0, sw, im.height, 0, 0, sw, im.height);
  x.globalCompositeOperation = 'multiply'; x.fillStyle = col; x.fillRect(0, 0, sw, im.height); x.globalCompositeOperation = 'destination-in'; x.drawImage(im, sx, 0, sw, im.height, 0, 0, sw, im.height); return t; }
function counterParts6(c, o, x0, y0, k, n) {           // plate (66 x 27 sprite) at scale k with icon and count / strength number
  const P = CT6.plate[o.side]; c.imageSmoothingEnabled = true; c.imageSmoothingQuality = 'high';
  c.drawImage(P, x0, y0, 66 * k, 27 * k);
  const ic = TINY.icons[o.u[2]] || TINY.icons.Inf; if (ic) c.drawImage(ic, ic.width / 2, 0, ic.width / 2, ic.height, x0 + 5 * k, y0 + 7 * k, ic.width / 2 * k, ic.height * k);
  c.font = `700 ${Math.round(11 * k)}px "Oswald",sans-serif`; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillStyle = '#f2ead2';
  c.fillText(String(n), x0 + 44.5 * k, y0 + 13.5 * k); c.textAlign = 'left';
}
function barParts6(c, x, y, w, h, f) { c.imageSmoothingEnabled = false; c.drawImage(CT6.barBg, x, y, w, h); c.drawImage(CT6.barStr, 0, 0, Math.max(1, Math.round(21 * f)), 7, x, y, w * f, h); c.imageSmoothingEnabled = true; }
function nameTab6(c, x, y, w, h, text, fs0) {
  c.imageSmoothingEnabled = true; c.drawImage(CT6.tab, x, y, w, h);
  let fs = fs0; c.font = `900 ${fs}px "Noto Sans SC",sans-serif`; while (c.measureText(text).width > w - 24 && fs > 18) { fs -= 2; c.font = `900 ${fs}px "Noto Sans SC",sans-serif`; }
  c.textBaseline = 'middle'; c.lineJoin = 'round'; c.lineWidth = 5; c.strokeStyle = '#000'; c.strokeText(text, x + 12, y + h / 2 + 1); c.fillStyle = '#fff'; c.fillText(text, x + 12, y + h / 2 + 1);
}
function drawCounterV6(o, alive, total, badge) {
  const c = o.flagCv.getContext('2d'); c.clearRect(0, 0, FLAG_W, FLAG_H);
  counterParts6(c, o, 0, 14, 4, badge > 1 ? badge : alive);
  nameTab6(c, 270, 30, FLAG_W - 272, 76, unitZh(o.i), 44);
  barParts6(c, 4, 132, 256, 26, clamp(alive / Math.max(1, total), 0, 1));
  o.flagTex.needsUpdate = true;
}
function drawPlateV6(o, alive) {
  const c = o.npCv.getContext('2d'), w = o.npCv.width, h = o.npCv.height; c.clearRect(0, 0, w, h);
  counterParts6(c, o, 0, 0, 2.4, alive);
  nameTab6(c, 166, 4, w - 168, 56, o.npText, 36);
  barParts6(c, 2, 70, 154, 16, clamp(alive / Math.max(1, o.u[5]), 0, 1));
  o.npTex.needsUpdate = true;
}
function drawFlag(o, alive, badge) {
  if (V6 && CT6) return drawCounterV6(o, o.stackA ?? alive, o.stackT ?? o.u[5], badge);
  if (V4) return drawCounterV4(o, o.stackA ?? alive, o.stackT ?? o.u[5], badge);
  if (COUNTER) return drawCounter(o, alive, badge);
  const c = o.flagCv.getContext('2d'), col = o.side === 0 ? BLUE : RED;
  c.clearRect(0, 0, FLAG_W, FLAG_H);
  c.fillStyle = 'rgba(22,19,15,.9)'; c.beginPath(); c.roundRect(2, 2, FLAG_W - 4, FLAG_H - 4, 12); c.fill();
  c.fillStyle = col; c.beginPath(); c.roundRect(2, 2, 18, FLAG_H - 4, [12, 0, 0, 12]); c.fill();
  c.fillStyle = '#efe6cf'; c.fillRect(30, 12, 150, 112);
  const img = o.img, sc = Math.min(140 / img.width, 102 / img.height); c.drawImage(img, 105 - img.width * sc / 2, 68 - img.height * sc / 2, img.width * sc, img.height * sc);
  let fs = 48; c.font = `700 ${fs}px "Oswald","Noto Sans SC",sans-serif`;
  while (c.measureText(o.u[0]).width > FLAG_W - 205 - (badge > 1 ? 70 : 0) && fs > 26) { fs -= 2; c.font = `700 ${fs}px "Oswald","Noto Sans SC",sans-serif`; }
  c.fillStyle = '#fff6dc'; c.textBaseline = 'middle'; c.fillText(o.u[0], 194, 42);
  c.font = '700 34px "Noto Sans SC",sans-serif'; c.fillStyle = '#ecc75a';
  c.fillText(`${TYPE_ZH[o.u[2]] || ''}${SIZE_ZH[o.u[3]] || ''}`, 194, 96);
  const n = Math.max(1, o.u[5]), gap = n > 16 ? 2 : 4, x0 = 30, w = FLAG_W - 44, cw = (w - gap * (n - 1)) / n;
  for (let k = 0; k < n; k++) { c.fillStyle = k < alive ? (o.side === 0 ? '#9cc3f2' : '#f2b0a4') : '#4a3d35'; c.fillRect(x0 + k * (cw + gap), 136, cw, 22); }
  if (badge > 1) {
    c.fillStyle = '#ecc75a'; c.beginPath(); c.arc(FLAG_W - 44, 44, 34, 0, 7); c.fill();
    c.fillStyle = '#1b160f'; c.font = '700 36px "Oswald",sans-serif'; c.textAlign = 'center'; c.fillText('×' + badge, FLAG_W - 44, 46); c.textAlign = 'left';
  }
  o.flagTex.needsUpdate = true;
}
function makeFig(o, close) {
  const ph = (o.i * 0.37) % 1;
  if (o.cls === 'inf') {
    const s = makeSoldier(o.side); s.root.scale.setScalar(close ? MS * 1.8 : FIGI);
    return { inf: true, s, root: s.root, ph, ty: 0, deadV: null };
  }
  const vv = makeVehicle(o.key, o.side); vv.root.scale.setScalar(close ? MS : o.cls === 'heli' ? FIGH : FIGV);
  return { inf: false, vv, root: vv.root, ph, ty: 0, deadV: null };
}
async function buildUnits() {
  const baseGeo = new THREE.CylinderGeometry(0.12, 0.128, 0.014, 40); baseGeo.scale(1.15, 1, 0.95); baseGeo.translate(0, 0.007, 0);
  const feltGeo = new THREE.CylinderGeometry(0.111, 0.111, 0.002, 40); feltGeo.scale(1.15, 1, 0.95); feltGeo.translate(0, 0.0145, 0);
  const feltMat = new THREE.MeshStandardMaterial({ color: 0x55603a, roughness: 0.95 });
  for (let i = 0; i < NU; i++) {
    const u = G.u[i], side = u[1], cls = classOf(u);
    const img = await symbolImage(u);
    const root = new THREE.Group(), pivot = new THREE.Group(); root.add(pivot); root.visible = false; scene.add(root);
    const baseMat = new THREE.MeshStandardMaterial({ color: side === 0 ? BLUE : RED, roughness: 0.5 });
    const base = new THREE.Mesh(baseGeo, baseMat), felt = new THREE.Mesh(feltGeo, feltMat.clone()); base.castShadow = true; base.receiveShadow = felt.receiveShadow = true;
    pivot.add(base, felt);
    const o = { i, u, side, cls, key: kitKey(cls, side), root, pivot, base, felt, baseMat, img, figs: [], cap: 0, lastAlive: -1, lastBadge: -1, off: null,
      helo: cls === 'heli', models: null, prevM: -1, hd: null, prevFigs: -1 };
    o.cap = V4 ? (o.cls === 'inf' ? 3 : 1) : standCap(o);
    if (V4) o.symHi = await symbolImage(u, 140);
    if (V5 && TINY) {                                   // far-zoom chip: the game's tiny map counter + its on-map unit icon
      o.chipCv = canvas(66, CHIP_H); o.chipTex = new THREE.CanvasTexture(o.chipCv); o.chipTex.colorSpace = THREE.SRGBColorSpace;
      o.chip = new THREE.Sprite(new THREE.SpriteMaterial({ map: o.chipTex, transparent: true, depthWrite: false, depthTest: false, toneMapped: false, fog: false }));
      o.chip.center.set(0.5, 0); o.chip.renderOrder = 23; o.chip.visible = false; scene.add(o.chip); o.chipKey = '';
    }
    for (let k = 0; k < o.cap; k++) { const f = makeFig(o, false); pivot.add(f.root); o.figs.push(f); }
    o.flagCv = canvas(FLAG_W, FLAG_H); o.flagTex = new THREE.CanvasTexture(o.flagCv); o.flagTex.colorSpace = THREE.SRGBColorSpace; o.flagTex.anisotropy = 4;
    o.flag = new THREE.Sprite(new THREE.SpriteMaterial({ map: o.flagTex, transparent: true, depthWrite: false, depthTest: false, toneMapped: false, fog: false }));
    o.flag.center.set(0.5, 0); o.flag.renderOrder = 24; const fh = FLAG_HGT * (V4 ? 1.5 : 1); o.flag.scale.set(fh * FLAG_W / FLAG_H, fh, 1); o.flag.position.y = V4 ? 0.22 : 0.2; root.add(o.flag);
    if (o.helo) {
      base.visible = felt.visible = false;
      const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.004, 0.004, 1, 6), new THREE.MeshBasicMaterial({ color: 0x1a1a1a, transparent: true, opacity: 0.35 }));
      root.add(stem); o.stem = stem;
      const sh = new THREE.Mesh(new THREE.CircleGeometry(0.09, 24), new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.25, depthWrite: false })); sh.rotation.x = -Math.PI / 2; root.add(sh); o.shadowBlob = sh;
    }
    units.push(o);
  }
}

const CAP = 5;
const SLOTS = [[0, 0], [-1, -0.9], [1, -0.9], [-2, -1.8], [2, -1.8]];
const TEAM = [[0, 0.004], [-0.0052, -0.003], [0.0052, -0.003]];      // close-up fire team offsets (km)
const modelsFor = (alive, total) => alive <= 0 ? 0 : Math.max(1, Math.min(CAP, Math.round(CAP * alive / Math.max(1, total))));
function nameplate(o) {
  const nm = (o.side === 0 ? '蓝 ' : '红 ') + unitZh(o.i) + ' · ' + (KITMOD.CLASS_ZH || CLASS_ZH_V2)[o.cls];
  const sp = textSprite(nm, { font: '700 36px "Noto Sans SC", sans-serif', color: '#fff6dc', bg: '#1f1b15', bgAlpha: 0.82, edge: o.side === 0 ? BLUE : RED, pad: 14, lineH: 52, height: 0.016, attenuate: false, depthTest: false, renderOrder: 26 });
  scene.add(sp); return sp;
}
function headingAt(i, g, s) {                       // radians, 0 = +z (south); facing movement, else the nearest enemy, else the enemy side
  if (s.moving) return Math.atan2(s.nx, s.nz);
  const j = nearestEnemy(i, g, 4);
  if (j != null) { const e = unitState(j, g); return Math.atan2(e.x - s.x, e.z - s.z); }
  return G.u[i][1] === 0 ? Math.atan2(1, -1) : Math.atan2(-1, 1);
}
const wrap = a => { while (a > Math.PI) a -= 2 * Math.PI; while (a < -Math.PI) a += 2 * Math.PI; return a; };
function turnFig(f, want, rate = 0.09) { if (f.yaw == null) f.yaw = want; else f.yaw += clamp(wrap(want - f.yaw), -rate, rate); f.root.rotation.y = f.yaw; }
const recoil = dt => dt < 0 || dt > 0.6 ? 0 : dt < 0.04 ? dt / 0.04 : 1 - smooth((dt - 0.04) / 0.56);
// pose one figure: vehicles aim the turret at the target, recoil, bob when moving; soldiers play idle / run / shoot
function poseFig(f, o, v, s, fire, aimTo) {
  if (f.inf) {
    let clip = 'idle', t = v + f.ph * 3;
    if (V5 && fire && fire.dt >= 0 && fire.dt < fire.len) { clip = 'shoot'; t = fire.dt; }
    else if (!V5 && fire && fire.dt > -0.3 && fire.dt < 1.0) { clip = 'shoot'; t = fire.dt + 0.3 + f.ph * 0.2; }
    else if (s.moving) { clip = 'run'; t = v * 1.1 + f.ph; }
    if (aimTo != null && !V4) f.root.rotation.y = aimTo;
    f.s.act(clip, t);
    return;
  }
  const vv = f.vv;
  if (vv.pose) {                                    // HOI4: idle / move / attack clips (the attack clip starts with the shot)
    const st = V5 ? (fire && fire.dt >= 0 && fire.dt < fire.len ? 'attack' : s.moving && !o.helo ? 'move' : 'idle')
      : fire && fire.dt > -0.05 && fire.dt < vv.duration('attack') ? 'attack' : s.moving && !o.helo ? 'move' : 'idle';
    vv.pose(st, st === 'attack' ? fire.dt + (V5 ? 0 : 0.05) : v * (st === 'move' ? 1 : 0.6) + f.ph * 7);
    if (vv.turret && st !== 'move') { const want = aimTo != null ? wrap(aimTo - f.root.rotation.y) : null;
      if (want != null) { f.ty += wrap(want - f.ty) * 0.25; vv.turret.rotation.set(0, f.ty, 0); } else f.ty = vv.turret.rotation.y; }
    vv.body.position.y = s.moving && !o.helo && !V5 ? 0.04 * Math.sin(v * 17 + f.ph * 6) : 0;
    for (const r of vv.rotors || []) r.rotation.y = v * 25 + f.ph;
    return;
  }
  if (vv.turret) {
    const want = aimTo != null ? wrap(aimTo - f.root.rotation.y) : 0;
    f.ty += wrap(want - f.ty) * 0.25; vv.turret.rotation.y = f.ty;
  }
  const r = fire ? recoil(fire.dt) : 0;
  if (vv.gun) vv.gun.position.z = -0.9 * r * (vv.turret ? 1 : 0.6);
  if (o.cls === 'gun' || o.cls === 'spg') vv.body.rotation.x = -0.05 * r;
  vv.body.position.y = s.moving && !o.helo ? 0.09 * Math.sin(v * 17 + f.ph * 6) : 0;
  if (!fire || o.cls !== 'gun') vv.body.rotation.x = s.moving && !o.helo ? 0.02 * Math.sin(v * 8.5 + f.ph * 6) : vv.body.rotation.x * 0.5;
  if (vv.rotor) vv.rotor.rotation.y = v * 25 + f.ph;
}

// ---------------------------------------------------------------- effects
function buildFx() {
  const flashTex = radialTexture([[0, 'rgba(255,255,235,1)'], [0.25, 'rgba(255,214,120,.95)'], [0.6, 'rgba(255,120,30,.35)'], [1, 'rgba(255,80,0,0)']]);
  const smokeTex = radialTexture([[0, 'rgba(70,64,58,.85)'], [0.55, 'rgba(90,84,76,.45)'], [1, 'rgba(110,104,96,0)']]);
  const glowTex = radialTexture([[0, 'rgba(255,250,220,1)'], [0.4, 'rgba(255,200,120,.6)'], [1, 'rgba(255,160,60,0)']]);
  const pool = (n, make) => { const a = []; for (let k = 0; k < n; k++) { const o = make(); o.visible = false; scene.add(o); a.push(o); } return { a, used: 0 }; };
  fx = {
    flash: pool(160, () => { const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: flashTex, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, toneMapped: false, fog: false })); s.renderOrder = 20; return s; }),
    smoke: pool(360, () => { const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: smokeTex, transparent: true, depthWrite: false })); s.renderOrder = 15; return s; }),
    head: pool(80, () => { const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, toneMapped: false, fog: false })); s.renderOrder = 21; return s; }),
    trail: pool(160, () => { const m = new THREE.Mesh(new THREE.CylinderGeometry(1, 1, 1, 6, 1, true), new THREE.MeshBasicMaterial({ color: 0xffe2b0, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false, fog: false })); m.renderOrder = 19; return m; }),
    text: [], textTex: {},
  };
}
function take(p) { const o = p.a[p.used++]; if (!o) return null; o.visible = true; return o; }
function popupSprite(n, side) {
  const key = side + ':' + n;
  if (!fx.textTex[key]) {
    const sp = textSprite('−' + n, { font: '700 64px "Oswald", sans-serif', color: '#ffffff', stroke: side === 1 ? '#8a1d14' : '#163a73', strokeW: 12, height: 0.26, pad: 18, lineH: 84, renderOrder: 30, depthTest: false });
    fx.textTex[key] = sp.material;
  }
  let s = fx.text.find(x => !x.visible);
  if (!s) { s = new THREE.Sprite(); s.center.set(0.5, 0); s.renderOrder = 30; scene.add(s); fx.text.push(s); }
  s.material = fx.textTex[key]; const img = s.material.map.image; s.scale.set(0.26 * img.width / img.height, 0.26, 1); s.visible = true;
  return s;
}

// ---------------------------------------------------------------- fire / loss schedule (pure data, built once)
const BARRAGE = k => k === 'SPArty' || k === 'Arty';
function buildSchedule() {
  const R = rng(1234);
  const lossByT = new Map();
  for (const e of G.ev) if (e[3] === 2) { const k = e[0]; if (!lossByT.has(k)) lossByT.set(k, []); lossByT.get(k).push({ e, shooter: null }); }
  const fires = G.ev.filter(e => e[3] === 1);
  // pair losses with shooters of the other side firing in the same minute (nearest, within 6 km)
  for (const [t, ls] of lossByT) for (const L of ls) {
    const lp = unitState(L.e[5], t); if (!lp) continue;
    let best = null, bd = 6;
    for (const f of fires) if (f[0] === t && f[2] !== L.e[2]) { const sp = unitState(f[5], t); if (!sp || isNaN(sp.x)) continue; const d = Math.hypot(sp.x - lp.x, sp.z - lp.z); if (d < bd) { bd = d; best = f; } }
    L.shooter = best;
  }
  for (const [t, ls] of lossByT) for (const L of ls) {
    const impact = t - 8 - R() * 40;                 // the recorder sees the loss at the minute mark; show it during that minute
    lossSchedule.push({ g: impact, unit: L.e[5], side: L.e[2], n: L.e[4], shooter: L.shooter ? L.shooter[5] : -1, rowT: t, hex: L.e[1] });
  }
  for (const f of fires) {
    const t = f[0], mine = lossSchedule.filter(l => l.rowT === t && l.shooter === f[5]);
    const shots = Math.min(f[4], f[2] === 0 ? 6 : 8);
    for (let k = 0; k < shots; k++) {
      const L = mine[k % Math.max(1, mine.length)];
      const target = L ? L.unit : -1;
      const g = L ? L.g - 0 : t - 10 - R() * 45;
      const kind = G.u[f[5]][2];
      let tgt = target, hexT = 0;
      if (BARRAGE(kind) && !L) { const os = G.o[f[5]] || [], oi = rowAt(os, t); if (oi >= 0) { tgt = os[oi][3]; hexT = os[oi][2]; } }
      shotSchedule.push({ g, k, from: f[5], side: f[2], target: tgt, hexT, hitLoss: !!L && k < mine.length, kind, seed: Math.floor(R() * 1e9), t });
    }
  }
  lossSchedule.sort((a, b) => a.g - b.g);
  for (const sh of shotSchedule) sh.vL = launchV(sh);
  if (V5) buildCycles();
}
function nearestEnemy(i, g, maxD = 5) {
  const me = unitState(i, g); if (!me) return null; let best = null, bd = maxD;
  for (let j = 0; j < NU; j++) { if (G.u[j][1] === G.u[i][1]) continue; const s = unitState(j, g); if (!s || s.st !== 1 || !s.alive || isNaN(s.x)) continue; const d = Math.hypot(s.x - me.x, s.z - me.z); if (d < bd) { bd = d; best = j; } }
  return best;
}
// alive count as shown on screen: losses already shown by an effect are taken off before the recorder's row
function shownAlive(i, g, st) {
  let a = st.alive;
  for (const l of lossSchedule) if (l.unit === i && l.g <= g && l.rowT > g) a -= l.n;
  return Math.max(0, a);
}

// ---------------------------------------------------------------- per-frame update
const pieceWorld = new Map();   // unit id -> THREE.Vector3 (flag foot / top of the stand) for this frame
const modelWorld = new Map();   // unit id -> [THREE.Vector3 muzzle of each shown figure] for this frame
const centerWorld = new Map();  // unit id -> THREE.Vector3 (hit point: middle of the figures) for this frame
const firing = new Map();       // unit id -> { dt (video s since launch), sh } for shots launched around now
const FLIGHT = 0.55;
function launchV(sh) { return BARRAGE(sh.kind) ? vOf(sh.g) + sh.k * 0.12 - 1.3 : vOf(sh.g) - FLIGHT - sh.k * 0.16; }
function computeFiring(v) {
  firing.clear();
  for (const sh of shotSchedule) {
    const dt = v - sh.vL; if (dt < -0.35 || dt > 1.0) continue;
    const cur = firing.get(sh.from); if (!cur || Math.abs(dt - 0.15) < Math.abs(cur.dt - 0.15)) firing.set(sh.from, { dt, sh });
  }
}
function aimHeading(o, s, fire, g) {               // map-space heading towards the target of the current shot
  if (!fire) return null;
  if (V5) { const t = fire.sh.target, close = MODELS >= 0.5, me = o.P; if (!me) return null; let tx, tz;
    if (t >= 0) { const e = units[t].P; if (!e) return null; tx = close ? e.cx : e.bx; tz = close ? e.cz : e.bz; }
    else if (fire.sh.hexT) [tx, tz] = hexXZ(fire.sh.hexT); else return null;
    const x = close ? me.cx : me.bx, z = close ? me.cz : me.bz; if (Math.hypot(tx - x, tz - z) < 1e-4) return null; return Math.atan2(tx - x, tz - z); }
  const t = fire.sh.target; let tx, tz;
  if (t >= 0) { const e = unitState(t, g); if (!e || isNaN(e.x)) return null; tx = e.x; tz = e.z; }
  else if (fire.sh.hexT) [tx, tz] = hexXZ(fire.sh.hexT); else return null;
  return Math.atan2(tx - s.x, tz - s.z);
}
let liveUnits = [], boardWrecks = [], corpses = [];
const tmpV = new THREE.Vector3(), UP = new THREE.Vector3(0, 1, 0);
// fog of war, per shot (SHOT.fog: [{ from, to, side }]): approximate, an enemy is shown only within FOG_R km of a
// surviving friendly unit (the replay has no per-side spotting data)
const FOG_R = 2.0;
let FOG = null;
const fogAt = v => { const f = (SHOT.fog || []).find(x => v >= x.from && v <= x.to); return f ? f.side : null; };
function mergeOn(n) { return n >= 4 || (n >= 3 && ZOOM > 1.25) || (n >= 2 && ZOOM > 1.7); }
// v4 placement. Display position: follows the replay position, but a unit that has to turn first turns on the spot
// (finite turn rate) and only then drives, catching up at a fixed speed; uniform speed along the path.
// Separation: pieces (board) and formations (close-up) are pushed apart so they never overlap; two enemies in one hex
// end up on the two sides of it (each towards its own rear) and face each other.
const HOME = [[-0.7071, 0.7071], [0.7071, -0.7071]];    // towards each side's rear (blue: west / south, red: east / north)
const TURN = 0.06, FTURN = 0.02;                       // rad per frame: unit heading, formation heading
function relax(items, need, maxOff, iters = 10) {
  for (let it = 0; it < iters; it++) for (let a = 0; a < items.length; a++) for (let b = a + 1; b < items.length; b++) {
    const A = items[a], B = items[b], d0 = need(A, B); let dx = B.x - A.x, dz = B.z - A.z, d = Math.hypot(dx, dz);
    if (d >= d0) continue;
    if (d < 1e-4) {
      if (A.side !== B.side) { dx = HOME[B.side][0] - HOME[A.side][0]; dz = HOME[B.side][1] - HOME[A.side][1]; }
      else { const an = (A.k * 2.39996 + B.k * 0.7) % (2 * Math.PI); dx = Math.cos(an); dz = Math.sin(an); }
      d = Math.hypot(dx, dz); dx /= d; dz /= d; d = 0;
    } else { dx /= d; dz /= d; }
    const m = (d0 - d) / 2; A.x -= dx * m; A.z -= dz * m; B.x += dx * m; B.z += dz * m;
  }
  for (const it of items) { const ox = it.x - it.x0, oz = it.z - it.z0, L = Math.hypot(ox, oz); if (L > maxOff) { it.x = it.x0 + ox / L * maxOff; it.z = it.z0 + oz / L * maxOff; } }
}
function followV4(o, s) {
  if (o.bx == null || Math.hypot(s.x - o.bx, s.z - o.bz) > 1.5) { o.bx = s.x; o.bz = s.z; return; }
  const dx = s.x - o.bx, dz = s.z - o.bz, d = Math.hypot(dx, dz); if (d < 1e-6) return;
  const aligned = o.hd == null || d < 0.012 || Math.abs(wrap(Math.atan2(dx, dz) - o.hd)) < 0.6;
  if (!aligned) return;
  const st = Math.min(d, Math.max(0.0028, d * 0.2)); o.bx += dx / d * st; o.bz += dz / d * st;
}
const bump = (dt, len) => dt < 0 || dt > len ? 0 : Math.sin(Math.PI * dt / len);
function updateUnits(v, g) {
  if (V5) return updateUnitsV5(v, g);
  pieceWorld.clear(); modelWorld.clear(); centerWorld.clear();
  computeFiring(v);
  const byHex = new Map(), live = [];
  for (const o of units) {
    const s = unitState(o.i, g);
    if (!s || s.st === 0 || isNaN(s.x)) { o.root.visible = false; continue; }
    const alive = shownAlive(o.i, g, s), dead = s.st === 2 || alive === 0;
    let deathV = null;
    if (dead) {
      const ls = lossSchedule.filter(l => l.unit === o.i && l.g <= g);
      const rows = G.p[o.i], k = rows.findIndex(r => r[3] === 2 || r[2] === 0);
      const gd = ls.length ? ls[ls.length - 1].g : (k >= 0 ? rows[k][0] : 0);
      deathV = vOf(gd);
      if (v - deathV > 4.2) { o.root.visible = false; if (o.models) { o.prevM = 0; } o.prevFigs = 0; continue; }
    }
    o.root.visible = true;
    live.push({ o, s, alive, dead, deathV });
  }
  if (FOG != null) {                                  // drop enemies nobody on the viewing side is close to
    const own = live.filter(l => l.o.side === FOG && !l.dead).map(l => l.s);
    for (let k = live.length - 1; k >= 0; k--) { const l = live[k]; if (l.o.side === FOG) continue;
      if (!own.some(s => Math.hypot(s.x - l.s.x, s.z - l.s.z) < FOG_R)) { l.o.root.visible = false; live.splice(k, 1); } }
  }
  // stacking key: side + hex; when the camera is far out, side + 2x2 block of hexes (pieces that would overlap merge)
  const far = ZOOM > 1.6;
  for (const { o, s } of live) {
    if (o.helo) continue;
    const h = s.hex | 0, key = o.side + ':' + (far ? Math.floor(h / 200) * 100 + Math.floor((h % 100) / 2) : h);
    if (!byHex.has(key)) byHex.set(key, []); byHex.get(key).push(o);
  }
  // stands in one hex: merge into the strongest one (badge ×n) when the camera is far, else share the hex
  const aliveOf = new Map(live.map(l => [l.o, l.alive]));
  for (const list of byHex.values()) {
    const n = list.length, merge = (V4 ? n >= 2 : mergeOn(n)) && MODELS < 0.5;
    const lead = list.reduce((a, b) => (shownAliveCached(b) > shownAliveCached(a) ? b : a), list[0]);
    for (const o of list) { o.mergedInto = merge && o !== lead ? lead : null; o.badge = merge && o === lead ? n : 1; o.hexN = merge ? 1 : n; o.hexK = merge ? 0 : list.indexOf(o); o.stackA = o.stackT = undefined; }
    if (V4 && merge) { lead.stackA = list.reduce((a, o) => a + (aliveOf.get(o) || 0), 0); lead.stackT = list.reduce((a, o) => a + o.u[5], 0); lead.members = list.map(o => o.i); }
    else if (V4) for (const o of list) o.members = [o.i];
  }
  if (V4) {                                            // display positions + board separation (stands of both sides)
    for (const { o, s } of live) followV4(o, s);
    const items = live.filter(l => !l.o.helo && !l.o.mergedInto).map(l => ({ o: l.o, side: l.o.side, k: l.o.i, x: l.o.bx, z: l.o.bz, x0: l.o.bx, z0: l.o.bz }));
    relax(items, (a, b) => (a.side === b.side ? 0.3 : 0.42) * ZOOM, 0.34 * ZOOM);
    for (const it of items) { it.o.rx = it.x - it.x0; it.o.rz = it.z - it.z0; }
  }
  const counterVis = MODELS < 0.5;
  for (const { o, s, alive, dead, deathV } of live) {
    let tx = 0, tz = 0, ty = 0, sc = 1;
    if (o.helo) { tx = 0.09; tz = -0.06; }
    else {
      const n = o.hexN, k = o.hexK;
      if (n === 2) { tx = k ? 0.13 : -0.13; tz = k ? 0.05 : -0.05; sc = 0.84; }
      else if (n > 2) {
        const slots = n <= 4 ? [[-0.13, -0.09], [0.13, -0.09], [-0.13, 0.1], [0.13, 0.1]] : [[-0.16, -0.1], [0, -0.1], [0.16, -0.1], [-0.16, 0.11], [0, 0.11], [0.16, 0.11]];
        const cap = slots.length, sl = slots[k % cap]; tx = sl[0]; tz = sl[1]; ty = Math.floor(k / cap) * 0.07; sc = n <= 4 ? 0.74 : 0.6;
      }
    }
    if (V4 && !o.helo) { tx = o.rx || 0; tz = o.rz || 0; ty = 0; sc = 1; }
    if (!o.off) o.off = { x: tx, z: tz, y: ty, s: sc };
    o.stackK = o.helo ? 0 : o.hexK;
    const f = 0.18; o.off.x = lerp(o.off.x, tx, f); o.off.z = lerp(o.off.z, tz, f); o.off.y = lerp(o.off.y, ty, f); o.off.s = lerp(o.off.s, sc, f);
    let lx = 0, lz = 0;
    if (V4) { const fr = firing.get(o.i), am = aimHeading(o, s, fr, g); if (fr && am != null) { const b = bump(fr.dt, 0.9) * 0.05 * ZOOM; lx = Math.sin(am) * b; lz = Math.cos(am) * b; } }
    const mx = (V4 ? o.bx : s.x) + o.off.x + lx, mz = (V4 ? o.bz : s.z) + o.off.z + lz, gy = yAt(mx, mz);
    let hover = 0;
    if (o.helo && !dead) hover = (0.26 + 0.02 * Math.sin(v * 2.1 + o.i)) * Math.sqrt(ZOOM);
    o.root.position.set(mx - MAP_W / 2, gy + o.off.y + hover, mz - MAP_H / 2);
    o.root.scale.setScalar(o.off.s * ZOOM);
    o.root.visible = counterVis && !o.mergedInto;
    if (o.stem) { const hs = hover / o.root.scale.y; o.stem.visible = hover > 0.05; o.stem.scale.set(1, hs, 1); o.stem.position.set(0, -hs / 2, 0); o.shadowBlob.position.y = -hs + 0.003; o.shadowBlob.visible = hover > 0.05; }
    const fkey = alive + ':' + o.badge + ':' + (o.stackA ?? '');
    if (fkey !== o.lastKey) { drawFlag(o, alive, o.badge); o.lastAlive = alive; o.lastBadge = o.badge; o.lastKey = fkey; }
    // heading (smoothed; order-dependent like the slot offsets). v4: finite turn rate, formation turns slower
    const hd = headingAt(o.i, g, s);
    if (o.hd == null) o.hd = hd; else o.hd += V4 ? clamp(wrap(hd - o.hd), -TURN, TURN) : wrap(hd - o.hd) * 0.12;
    if (o.fd == null) o.fd = o.hd; else o.fd += clamp(wrap(o.hd - o.fd), -FTURN, FTURN);
    // stand tilts with the ground
    if (!o.helo) o.pivot.quaternion.setFromUnitVectors(UP, normalAt(mx, mz));
    let glow = 0;
    for (const l of lossSchedule) if (l.unit === o.i) { const dv = v - vOf(l.g); if (dv >= 0 && dv < 1.2) glow = Math.max(glow, 1 - dv / 1.2); }
    o.baseMat.emissive.setRGB(glow * 0.9, glow * 0.35, 0);
    // figures: m shown, the ones lost since the last frame fall (soldiers) or become burning wrecks (vehicles)
    const m = dead ? 0 : V4 ? o.cap : figsFor(o, alive), fire = firing.get(o.i), aim = aimHeading(o, s, fire, g);
    const lay = LAYOUT[o.cls === 'inf' ? 'i' : 'v'][Math.max(1, Math.min(m || 1, o.cap)) - 1];
    const fdir = V4 ? o.fd : o.hd, cs = Math.cos(fdir), sn = Math.sin(fdir);
    const bp = [];
    for (let k = 0; k < o.cap; k++) {
      const fg = o.figs[k];
      if (k >= m) {
        if (fg.deadV == null && o.prevFigs > k) {
          fg.deadV = v;
          if (!fg.inf && counterVis && o.root.visible) {
            fg.root.getWorldPosition(tmpV);
            boardWrecks.push({ key: o.key, side: o.side, p: tmpV.clone(), hd: o.hd + (k % 2 ? 0.5 : -0.4), sc: fg.root.scale.x * o.root.scale.x / ZOOM, born: v, k, unit: o.i });
          }
        }
        const dying = fg.inf && fg.deadV != null && v - fg.deadV < 3.2;
        fg.root.visible = dying;
        if (dying) fg.s.act('death', v - fg.deadV);
        continue;
      }
      fg.deadV = null; fg.root.visible = true;
      const [fx_, fz_] = o.helo ? [0, 0] : lay[Math.min(k, lay.length - 1)];
      fg.root.position.set(fx_ * cs + fz_ * sn, 0.0145, -fx_ * sn + fz_ * cs);
      if (V4) turnFig(fg, fg.inf && aim != null ? aim : o.hd); else fg.root.rotation.y = o.hd;
      poseFig(fg, o, v, s, fire, aim);
    }
    o.prevFigs = m;
    o.root.updateMatrixWorld(true);
    for (let k = 0; k < Math.max(1, m); k++) { const fg = o.figs[k]; if (!fg || !fg.root.visible) continue; bp.push(fg.inf ? fg.root.localToWorld(new THREE.Vector3(0, 1.3, 0.5)) : fg.vv.muzzle.getWorldPosition(new THREE.Vector3())); }
    if (dead) {                                       // the base and flag fade out after the last figure fell
      const fade = clamp((v - deathV - 2.4) / 1.6, 0, 1);
      for (const mt of [o.baseMat, o.felt.material, o.flag.material]) { mt.transparent = true; mt.opacity = 1 - fade; }
    } else for (const mt of [o.baseMat, o.felt.material]) { mt.transparent = false; mt.opacity = 1; }
    if (!dead) o.flag.material.opacity = 1;
    if (V4 && !dead) { const st = stepAt(v); if (st) { const inStep = (o.members || [o.i]).some(i => st.units.includes(i)); o.flag.material.opacity = inStep ? 1 : 0.32; } }
    const top = o.root.localToWorld(new THREE.Vector3(0, 0.2, 0));
    pieceWorld.set(o.i, top);
    if (counterVis) {
      if (bp.length) modelWorld.set(o.i, bp);
      centerWorld.set(o.i, o.root.localToWorld(new THREE.Vector3(0, o.helo ? 0 : 0.03, 0)));
    }
  }
  // merged stands: effects use the leader's stand
  for (const { o } of live) if (o.mergedInto && counterVis) { const L = o.mergedInto; if (modelWorld.has(L.i)) modelWorld.set(o.i, modelWorld.get(L.i)); if (centerWorld.has(L.i)) centerWorld.set(o.i, centerWorld.get(L.i)); if (pieceWorld.has(L.i)) pieceWorld.set(o.i, pieceWorld.get(L.i)); }
  liveUnits = live;
  updateModels(v, g, live);
}
function shownAliveCached(o) { return o.lastAlive >= 0 ? o.lastAlive : o.u[5]; }

// ---------------------------------------------------------------- v5 movement: a precomputed plan per unit (video time)
// Root cause of the v4 drift: the replay holds one hex per unit per sampled minute (60-300 s apart), v4 slid the piece
// over the last 75 game seconds before the next row (a fraction of a video second at x150-x550), then relaxed all pieces
// apart every frame, reshuffled stack slots and turned idle pieces towards the nearest enemy. v5 plans every move once,
// the way RTS engines move units (0 A.D. UnitMotion, OpenRA Mobile, Spring GroundMoveType): along the hex path (via the
// shared neighbour for two-hex jumps), turn on the spot first when the turn is large (finite turn rate per class), then
// accelerate / cruise / brake with a per-class top speed; A-B-A excursions shorter than 2 game minutes are dropped; a
// unit keeps a fixed slot in its hex (assigned on arrival, kept until it leaves); units of one order set off together
// with a small stagger by rank; in fast-forward segments only the units the shot is about move at that speed, the rest
// slide quickly to where the replay has them. Nothing moves except along a leg, so nothing drifts or spins.
const KIN = {                // video seconds: top speed km/s, turn rate rad/s, turn-on-the-spot above (rad), accel / brake s
  inf: { vmax: 0.22, w: 9.4, thr: 1.05, ta: 0.2, td: 0.15 },
  veh: { vmax: 0.45, w: 3.14, thr: 1.05, ta: 0.4, td: 0.25 },
  heavy: { vmax: 0.38, w: 1.57, thr: 0.79, ta: 0.8, td: 0.5 },
  heli: { vmax: 0.8, w: 3.14, thr: 9, ta: 0.5, td: 0.5 },
};
const kinOf = o => KIN[o.helo ? 'heli' : o.cls === 'inf' ? 'inf' : o.cls === 'tank' || o.cls === 'td' || o.cls === 'spg' ? 'heavy' : 'veh'];
const DEF_HD = [Math.atan2(1, -1), Math.atan2(-1, 1)];   // facing the enemy side (blue looks north-east, red south-west)
const ANCHOR = 0.12, SLOT_W = 0.14, SLOT_D = 0.1;
const SLOT_GRID = [[0, 0], [1, 0], [-1, 0], [0.5, 1], [-0.5, 1], [1.5, 1], [-1.5, 1], [0.5, -1], [-0.5, -1], [0, 2], [1, 2], [-1, 2], [1.5, -1], [-1.5, -1]];
function anchorXZ(h, side) { const [x, z] = hexXZ(h); return [x + HOME[side][0] * ANCHOR, z + HOME[side][1] * ANCHOR]; }
function slotXZ(h, side, k) {                       // slot k of a side in a hex: rows across the hex, rear rows towards home
  const [x, z] = anchorXZ(h, side), bx = HOME[side][0], bz = HOME[side][1], px = -bz, pz = bx, n = SLOT_GRID.length;
  const [a, b] = SLOT_GRID[k % n], sq = k >= n ? 0.5 : 1;
  return [x + (px * a * SLOT_W + bx * b * SLOT_D) * sq, z + (pz * a * SLOT_W + bz * b * SLOT_D) * sq];
}
function sharedNeighbour(a, b) { const nb = new Set(neighbours(b)); return neighbours(a).find(h => nb.has(h)) || 0; }
let PLAN = [], DEATHV = [], CYCLES = [];
function focusSet(v, g, motion = false) {           // units the shot is about right now: current order steps, moment, SHOT.focus
  const F = new Set(), seq = stepSeq(v);           // (focus entries with counters: false only steer the movement plan)
  if (seq) for (const st of seq) if (v >= st.from) for (const i of st.units) F.add(i);
  const m = momentAt(g); if (m) for (const i of [...(m.att || []), ...(m.def || [])]) F.add(i);
  for (const f of SHOT.focus || []) if (v >= f.from && v <= f.to && (motion || f.counters !== false)) for (const i of f.units) F.add(i);
  return F;
}
let ENGOFF = new Map();                               // engage stance: SHOT.engageKm (default 0.17) km towards the enemy hex (500 m hexes: shared edge at 0.25)
function buildEngage() {                              // per unit: [game s, unit vector towards the enemy's hex] of each direct-fire exchange
  ENGOFF = new Map(); const add = (i, t, d) => { if (!ENGOFF.has(i)) ENGOFF.set(i, []); ENGOFF.get(i).push([t, d[0], d[1]]); };
  const ED = SHOT.engageKm ?? 0.17, SD = SHOT.engageSameKm ?? 0.035;
  for (const sh of shotSchedule) {
    if (sh.target == null || sh.target < 0 || BARRAGE(sh.kind)) continue;
    const a = unitState(sh.from, sh.g), b = unitState(sh.target, sh.g); if (!a || !b || !a.hex || !b.hex) continue;
    if (a.hex === b.hex) {                            // v7: same hex: each side moves from its rear slot (ANCHOR) to SD km on its own side of the centre
      if (!V7) continue; const sa = G.u[sh.from][1], sb = G.u[sh.target][1]; if (sa === sb) continue;
      add(sh.from, sh.g, [HOME[sa][0] * SD, HOME[sa][1] * SD, 1]); add(sh.target, sh.g, [HOME[sb][0] * SD, HOME[sb][1] * SD, 1]); continue; }
    const [ax, az] = hexXZ(a.hex), [bx, bz] = hexXZ(b.hex), L = Math.hypot(bx - ax, bz - az); if (L > 0.55) continue;      // adjacent hexes only
    if (V7) { add(sh.from, sh.g, [(bx - ax) / L * ED, (bz - az) / L * ED]); add(sh.target, sh.g, [(ax - bx) / L * ED, (az - bz) / L * ED]); continue; }
    add(sh.from, sh.g, [(bx - ax) / L, (bz - az) / L]); add(sh.target, sh.g, [(ax - bx) / L, (az - bz) / L]);
  }
}
function engageOffset(i, side, g) {                  // smooth in game time (triangle kernel, 150 s): from the rear-biased slot to ENG_D km towards the enemy
  const L = ENGOFF.get(i), ENG_D = SHOT.engageKm ?? 0.17; if (!L) return [0, 0];
  let sx = 0, sz = 0, sw = 0; for (const [t, dx, dz] of L) { const k = 1 - Math.abs(g - t) / 150; if (k <= 0) continue; sx += dx * k; sz += dz * k; sw += k; }
  if (!sw) return [0, 0]; const w = smooth(Math.min(1, sw)), n = Math.hypot(sx, sz) || 1;
  if (V7) return [(sx / sw - HOME[side][0] * ANCHOR) * w, (sz / sw - HOME[side][1] * ANCHOR) * w];   // entries carry their own distance
  return [(sx / n * ENG_D - HOME[side][0] * ANCHOR) * w, (sz / n * ENG_D - HOME[side][1] * ANCHOR) * w];
}
function deathVOf(i) {
  const rows = G.p[i], k = rows.findIndex(r => r[3] === 2 || r[2] === 0); if (k < 0) return null;
  const ls = lossSchedule.filter(l => l.unit === i && l.g <= rows[k][0]);
  return vOf(ls.length ? ls[ls.length - 1].g : rows[k][0]);
}
function trapezoid(t, D, ta, td) {                   // share of the path covered after t of D seconds (accelerate, cruise, brake)
  if (ta + td > D) { const k = D / (ta + td); ta *= k; td *= k; }
  const vp = 1 / (D - ta / 2 - td / 2);
  if (t <= 0) return 0; if (t >= D) return 1;
  if (t < ta) return 0.5 * vp * t * t / ta;
  if (t < D - td) return vp * (ta / 2 + (t - ta));
  return 1 - 0.5 * vp * (D - t) ** 2 / td;
}
function buildMotion() {
  const legsAll = [];
  PLAN = []; DEATHV = [];
  for (const o of units) {
    const i = o.i, stays = [];
    for (const r of G.p[i]) { if (!r[1]) continue; const L = stays[stays.length - 1]; if (!L || L.h !== r[1]) stays.push({ h: r[1], tIn: r[0], tLast: r[0], st: r[3] }); else { L.tLast = r[0]; L.st = r[3]; } }
    for (let ch = true; ch;) { ch = false;               // A-B-A excursions under 120 game s: stay in A
      for (let k = 2; k < stays.length; k++) if (stays[k].h === stays[k - 2].h && stays[k].tIn - stays[k - 1].tIn < 120) { stays[k - 2].tLast = stays[k].tLast; stays[k - 2].st = stays[k].st; stays.splice(k - 1, 2); ch = true; break; } }
    const legs = [];
    for (let k = 1; k < stays.length; k++) {
      const a = stays[k - 1], b = stays[k]; if (a.st !== 1) continue;
      const tB = b.tIn, dep = tB - Math.min(tB - a.tLast, 75), os = G.o[i] || [], oi = rowAt(os, tB - 1);
      const mid = neighbours(a.h).includes(b.h) ? 0 : sharedNeighbour(a.h, b.h);
      const L = { i, side: o.side, hA: a.h, hB: b.h, mid, tB, vDep: vOf(dep), vArr: vOf(tB), gk: oi >= 0 ? `${o.side}:${os[oi][0]}:${os[oi][1]}:${os[oi][2]}:${tB}` : `u${i}:${tB}`, stag: 0 };
      legs.push(L); legsAll.push(L);
    }
    PLAN[i] = { h0: stays.length ? stays[0].h : 0, legs, k0: 0 };
    DEATHV[i] = deathVOf(i);
  }
  // one order, one departure: rank within (order, arrival row) -> small stagger
  const byG = new Map(); for (const L of legsAll) { if (!byG.has(L.gk)) byG.set(L.gk, []); byG.get(L.gk).push(L); }
  for (const list of byG.values()) list.sort((a, b) => a.i - b.i).forEach((L, r) => { L.stag = Math.min(0.4, 0.07 * r); });
  // timing per unit: chained legs, turn first, trapezoid speed; fast-forward legs of units off focus are quick slides
  for (const o of units) {
    const P = PLAN[o.i], K = kinOf(o); let prevEnd = -1e9, hd = DEF_HD[o.side];
    P.hd0 = hd;
    for (const L of P.legs) {
      const vS = Math.max(L.vDep + L.stag, prevEnd + 0.05), [ax, az] = hexXZ(L.hA), [bx, bz] = hexXZ(L.hB);
      const [mx, mz] = L.mid ? hexXZ(L.mid) : [(ax + bx) / 2, (az + bz) / 2];
      const len = Math.hypot(mx - ax, mz - az) + Math.hypot(bx - mx, bz - mz), dir = Math.atan2(mx - ax, mz - az), dirEnd = Math.atan2(bx - mx, bz - mz);
      const rate = gOf(vS + 0.5) - gOf(vS - 0.5), focus = focusSet(vS, gOf(vS), true).has(o.i);
      L.quick = (rate > 120 && !focus) || vS - L.vArr > 1.5;
      const dA = wrap(dir - hd);
      L.turnT = Math.abs(dA) / (K.w * (L.quick ? 4 : 1)); L.inPlace = !L.quick && Math.abs(dA) > K.thr;
      L.D = L.quick ? clamp(L.vArr - vS, 0.22, 0.4) : Math.max(L.vArr - L.vDep, len / K.vmax + (K.ta + K.td) / 2);
      L.vS = vS; L.vM = vS + (L.inPlace ? L.turnT : 0); L.vE = L.vM + L.D; L.h0 = hd; L.dir = dir; L.K = K;
      hd = dirEnd; L.hEnd = dirEnd;
      // after a move back towards the own rear, face the enemy side again
      L.faceBack = Math.cos(dirEnd - Math.atan2(HOME[o.side][0], HOME[o.side][1])) > 0.5;
      L.vFace = L.vE + 0.4; L.faceT = L.faceBack ? Math.abs(wrap(DEF_HD[o.side] - dirEnd)) / K.w : 0;
      if (L.faceBack) hd = DEF_HD[o.side];
      prevEnd = L.vE + (L.faceBack ? 0.4 + L.faceT : 0);
    }
  }
  // sticky slots: event sweep in video time (leave frees the slot at the start of the leg, arrive takes the lowest free one)
  const ev = [], occ = new Map(), take = (h, s, i) => { const key = h + ':' + s; if (!occ.has(key)) occ.set(key, new Set()); const S = occ.get(key); let k = 0; while ([...S].some(x => x[0] === k)) k++; S.add([k, i]); return k; };
  const free = (h, s, i) => { const S = occ.get(h + ':' + s); if (S) for (const x of S) if (x[1] === i) S.delete(x); };
  for (const o of units) { const P = PLAN[o.i]; if (P.h0) P.k0 = take(P.h0, o.side, o.i);
    for (const L of P.legs) { ev.push([L.vS, 0, () => free(L.hA, L.side, L.i)]); ev.push([L.vE, 1, () => { L.kB = take(L.hB, L.side, L.i); }]); }
    if (DEATHV[o.i] != null) { const last = P.legs.filter(L => L.vE <= DEATHV[o.i] + 4.3).pop(); ev.push([DEATHV[o.i] + 4.3, 0, () => free(last ? last.hB : P.h0, o.side, o.i)]); } }
  ev.sort((a, b) => a[0] - b[0] || a[1] - b[1]); for (const e of ev) e[2]();
  for (const o of units) { const P = PLAN[o.i]; let k = P.k0; for (const L of P.legs) { L.kA = k; k = L.kB ?? 0; } }
}
// self-check over the whole cut (reported by init): per-frame displacement and turn of every unit's board position
function motionStats() {
  const n = Math.round(SHOT.duration * FPS), st = { legs: 0, quick: 0, turnFirst: 0, maxLag: 0, maxStepKm: 0, maxTurnDegPerFrame: 0, jumpsOver50m: 0, units: 0 };
  for (const o of units) { const P = PLAN[o.i]; if (!P || !P.h0) continue; st.units++; st.legs += P.legs.length;
    for (const L of P.legs) { if (L.quick) st.quick++; if (L.inPlace) st.turnFirst++; st.maxLag = Math.max(st.maxLag, L.vE - L.vArr); }
    let prev = null;
    for (let i = 0; i < n; i++) { const q = planAt(o.i, i / FPS);
      if (prev) { const d = Math.hypot(q.bx - prev.bx, q.bz - prev.bz), a = Math.abs(wrap(q.hd - prev.hd)) * 180 / Math.PI;
        st.maxStepKm = Math.max(st.maxStepKm, d); if (d > 0.05) { st.jumpsOver50m++; if (!q.quick) st.slowJumps = (st.slowJumps || 0) + 1; }
        if (a > 20) { st.turnSnaps = (st.turnSnaps || 0) + 1; const ph = (q.turning ? 'turn' : q.moving ? (q.quick ? 'quick' : 'move') : 'stay') + '<' + (prev.turning ? 'turn' : prev.moving ? (prev.quick ? 'quick' : 'move') : 'stay');
          st.snapPh = st.snapPh || {}; st.snapPh[ph] = (st.snapPh[ph] || 0) + 1; if (a > (st.worst?.a || 0)) st.worst = { a: +a.toFixed(1), i: o.i, f: i, ph }; }
        st.maxTurnDegPerFrame = Math.max(st.maxTurnDegPerFrame, a); }
      prev = q; } }
  for (const k of ['maxLag', 'maxStepKm', 'maxTurnDegPerFrame']) st[k] = +st[k].toFixed(3);
  return st;
}
function legPath(L, board) {                         // sampled path [[x, z, cumulative km]]
  const key = board ? '_pb' : '_pc'; if (L[key]) return L[key];
  const a = board ? anchorXZ(L.hA, L.side) : slotXZ(L.hA, L.side, L.kA), b = board ? anchorXZ(L.hB, L.side) : slotXZ(L.hB, L.side, L.kB);
  const c = L.mid ? hexXZ(L.mid) : [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], pts = [];
  for (let k = 0; k <= 16; k++) { const t = k / 16; pts.push([(1 - t) ** 2 * a[0] + 2 * (1 - t) * t * c[0] + t * t * b[0], (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * c[1] + t * t * b[1], 0]); }
  for (let k = 1; k < pts.length; k++) pts[k][2] = pts[k - 1][2] + Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]);
  for (let k = 1; k < pts.length; k++) { const a = Math.atan2(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]); pts[k][3] = k > 1 ? pts[k - 1][3] + wrap(a - pts[k - 1][3]) : a; }
  pts[0][3] = pts[1][3];                             // [3]: unwrapped tangent of the segment ending here
  return (L[key] = pts);
}
function alongPath(pts, f) {                         // point and tangent heading at share f of the length
  const d = f * pts[pts.length - 1][2]; let k = 1; while (k < pts.length - 1 && pts[k][2] < d) k++;
  const p = pts[k - 1], q = pts[k], u = (d - p[2]) / ((q[2] - p[2]) || 1);
  return [lerp(p[0], q[0], u), lerp(p[1], q[1], u), Math.atan2(q[0] - p[0], q[1] - p[1])];
}
const turnTo = (a, b, maxStep) => a + clamp(wrap(b - a), -maxStep, maxStep);
// heading is rate-limited everywhere (turn rate per class, a little faster on quick slides): it turns from where the
// previous leg left it towards the path tangent while driving, settles after arrival, then turns back to face the enemy
const wOf = L => Math.min(L.K.w * (L.quick ? 2 : 1), 9.4);
function headStart(P, k) { return k > 0 ? headAfter(P.legs[k - 1], P.legs[k].vS) : P.hd0; }
function headMoving(L, h0, v) {                     // target = unwrapped path tangent, so it never flips side at 180 degrees
  const pts = legPath(L, false), t0 = h0 + wrap(pts[0][3] - h0);
  if (v < L.vM) return turnTo(h0, t0, (v - L.vS) * L.K.w);
  const t = v - L.vM, f = L.quick ? smooth(clamp(t / L.D, 0, 1)) : trapezoid(t, L.D, L.K.ta, L.K.td);
  const d = f * pts[pts.length - 1][2]; let k = 1; while (k < pts.length - 1 && pts[k][2] < d) k++;
  const step = (v - L.vS) * wOf(L);
  return h0 + clamp(t0 + (pts[k][3] - pts[0][3]) - h0, -step, step);
}
function headAfter(L, v) {                           // v >= L.vE
  if (L._hE == null) { const P = PLAN[L.i]; L._hE = headMoving(L, headStart(P, P.legs.indexOf(L)), L.vE); }
  const settle = (u) => turnTo(L._hE, L.hEnd, (u - L.vE) * L.K.w);
  if (L.faceBack && v > L.vFace) return turnTo(settle(L.vFace), DEF_HD[L.side], (v - L.vFace) * L.K.w);
  return settle(v);
}
function planAt(i, v) {                              // { hex, cx, cz (close-up slot), bx, bz (board anchor), hd, moving }
  const P = PLAN[i], legs = P.legs; let k = -1;
  for (let j = 0; j < legs.length && legs[j].vS <= v; j++) k = j;
  const stay = (h, s, kk, hd) => { const [cx, cz] = slotXZ(h, s, kk), [bx, bz] = anchorXZ(h, s); return { hex: h, cx, cz, bx, bz, hd, moving: false }; };
  const side = G.u[i][1];
  if (k < 0) return stay(P.h0, side, P.k0, P.hd0);
  const L = legs[k];
  if (v >= L.vE) return stay(L.hB, side, L.kB ?? 0, headAfter(L, v));
  const hd = headMoving(L, headStart(P, k), v);
  if (v < L.vM) { const s = stay(L.hA, side, L.kA ?? 0, hd); s.moving = true; s.turning = true; return s; }
  const t = v - L.vM, f = L.quick ? smooth(clamp(t / L.D, 0, 1)) : trapezoid(t, L.D, L.K.ta, L.K.td);
  const [cx, cz] = alongPath(legPath(L, false), f), [bx, bz] = alongPath(legPath(L, true), f);
  return { hex: f < 0.5 ? L.hA : L.hB, cx, cz, bx, bz, hd, moving: true, quick: L.quick };
}
// fire cycles: one attack animation per cycle, played once from its start; a unit starts a new cycle only after the clip
// has finished (+0.2 s), so nothing restarts mid-clip. Shots that would start inside a running cycle are dropped, unless
// they carry a loss (those keep their tracer and land inside the running cycle).
function buildCycles() {
  const byU = new Map(); for (const sh of shotSchedule) { if (!byU.has(sh.from)) byU.set(sh.from, []); byU.get(sh.from).push(sh); }
  const keep = new Set(); CYCLES = [];
  for (const [i, list] of byU) {
    const o = units[i], f = o.figs[0];
    const len = clamp(f ? (f.inf ? f.s.duration('shoot') : f.vv.duration('attack')) : 1.2, 0.8, 2.4), gap = Math.max(1.5, len + 0.2);
    const starts = [];
    const ok = vL => starts.every(s => Math.abs(vL - s.v0) >= gap);
    for (const sh of [...list].filter(s => s.hitLoss).sort((a, b) => a.vL - b.vL)) { if (ok(sh.vL)) starts.push({ unit: i, v0: sh.vL, sh, len }); keep.add(sh); }
    for (const sh of [...list].filter(s => !s.hitLoss).sort((a, b) => a.vL - b.vL)) if (ok(sh.vL)) { starts.push({ unit: i, v0: sh.vL, sh, len }); keep.add(sh); }
    CYCLES.push(...starts);
  }
  shotSchedule = shotSchedule.filter(sh => keep.has(sh));
  CYCLES.sort((a, b) => a.v0 - b.v0);
}
function computeFiringV5(v) {
  firing.clear();
  for (const c of CYCLES) {
    if (c.v0 > v + 0.05) break; const dt = v - c.v0; if (dt > c.len + 0.3) continue;
    const cur = firing.get(c.unit); if (!cur || c.v0 > cur.v0) firing.set(c.unit, { dt, sh: c.sh, len: c.len, v0: c.v0 });
  }
}
function figYaw(o, f, fire, aim) {                   // soldiers turn to the target for the cycle, then back to the unit heading
  if (!f.inf || !fire || aim == null) return o.hd;
  const a = fire.dt < 0.15 ? smooth(clamp(fire.dt / 0.15, 0, 1)) : fire.dt > fire.len ? 1 - smooth(clamp((fire.dt - fire.len) / 0.3, 0, 1)) : 1;
  return o.hd + wrap(aim - o.hd) * a;
}
const TINY_ICON = { Tank: 'medium_tank', Recce: 'recon', Inf: 'infantry', APC: 'mechanized', SPArty: 'medium_spart', Arty: 'art', AD: 'anti_air', HQ: 'hq', AT: 'at', 'SP AT': 'at',
  Eng: 'engineer', Helo: 'helicopter_brigade', Utility: 'motorized', Depot: 'motorized', WLR: 'motorized' };
let TINY = null, CHIP_H = 20;
function drawChip(o, n, f) {                            // tiny counter tinted with the side colour, NATO frame of the on-map icon, stack count
  const c = o.chipCv.getContext('2d'), P = TINY.plate;
  c.clearRect(0, 0, 66, CHIP_H);
  if (!TINY.tinted[o.side]) { const t = canvas(66, 20), x = t.getContext('2d'); x.drawImage(P, 0, 0); x.globalCompositeOperation = 'multiply'; x.fillStyle = o.side ? '#e0675a' : '#78a3e6'; x.fillRect(0, 0, 66, 20);
    x.globalCompositeOperation = 'destination-in'; x.drawImage(P, 0, 0); TINY.tinted[o.side] = t; }
  c.drawImage(TINY.tinted[o.side], 0, 0);
  const ic = TINY.icons[o.u[2]] || TINY.icons.Inf; if (ic) c.drawImage(ic, ic.width / 2, 0, ic.width / 2, ic.height, 3, 4, ic.width / 2, ic.height);
  if (n > 1) { c.font = '700 13px "Oswald",sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle'; c.lineJoin = 'round'; c.lineWidth = 3; c.strokeStyle = '#000';
    c.strokeText(String(n), 49, 11); c.fillStyle = '#f6efd8'; c.fillText(String(n), 49, 11); }
  if (CHIP_H > 20 && f != null) {                   // strength bar under the plate (the game's counter bar bitmaps when loaded)
    c.fillStyle = 'rgba(8,8,7,.92)'; c.fillRect(3, 20, 60, 6);
    const fw = Math.max(f > 0 ? 1 : 0, 58 * f), S = CT6 && CT6.strSprite;
    if (S && fw > 0) { c.save(); c.imageSmoothingEnabled = true; c.translate(4, 25); c.rotate(-Math.PI / 2); c.drawImage(S, 0, 0, S.width, S.height, 0, 0, 4, fw); c.restore(); }
    else if (fw > 0) { c.fillStyle = '#c9874a'; c.fillRect(4, 21, fw, 4); } }
  o.chipTex.needsUpdate = true;
}
const SCALE_V5 = 0.85;
function updateUnitsV5(v, g) {
  pieceWorld.clear(); modelWorld.clear(); centerWorld.clear();
  computeFiringV5(v);
  for (const o of units) o.aliveNow = 0;
  const live = [];
  for (const o of units) {
    const s = unitState(o.i, g);
    if (!s || s.st === 0 || isNaN(s.x) || !PLAN[o.i].h0) { o.root.visible = false; if (o.chip) o.chip.visible = false; continue; }
    const alive = shownAlive(o.i, g, s), dead = s.st === 2 || alive === 0;
    let deathV = null;
    if (dead) { deathV = DEATHV[o.i] ?? v; if (v - deathV > 4.2) { o.root.visible = false; if (o.chip) o.chip.visible = false; o.prevM = 0; o.prevFigs = 0; continue; } }
    const P = planAt(o.i, v); o.P = P;
    const [hx, hz] = hexXZ(P.hex);
    live.push({ o, s: { ...s, x: P.bx, z: P.bz, hx, hz, hex: P.hex, moving: P.moving && !P.turning, turning: !!P.turning }, alive, dead, deathV });
  }
  if (FOG != null) {
    const own = live.filter(l => l.o.side === FOG && !l.dead).map(l => l.s);
    for (let k = live.length - 1; k >= 0; k--) { const l = live[k]; if (l.o.side === FOG) continue;
      if (!own.some(s => Math.hypot(s.x - l.s.x, s.z - l.s.z) < FOG_R)) { l.o.root.visible = false; if (l.o.chip) l.o.chip.visible = false; live.splice(k, 1); } }
  }
  const close = MODELS >= 0.5, counterVis = !close;
  // stacks: units of one side that have arrived in one hex (moving units are never merged); the lead is fixed per stack
  const byHex = new Map(), aliveOf = new Map(live.map(l => [l.o, l.alive]));
  for (const { o, s } of live) {
    o.mergedInto = null; o.badge = 1; o.members = [o.i]; o.stackA = o.stackT = undefined;
    if (o.helo || s.moving || s.turning) continue;
    const key = o.side + ':' + s.hex; if (!byHex.has(key)) byHex.set(key, []); byHex.get(key).push(o);
  }
  for (const list of byHex.values()) {
    if (list.length < 2 || close) continue;
    const lead = list.reduce((a, b) => (b.u[5] > a.u[5] || (b.u[5] === a.u[5] && b.i < a.i) ? b : a), list[0]);
    for (const o of list) o.mergedInto = o === lead ? null : lead;
    lead.badge = list.length; lead.members = list.map(o => o.i);
    lead.stackA = list.reduce((a, o) => a + (aliveOf.get(o) || 0), 0); lead.stackT = list.reduce((a, o) => a + o.u[5], 0);
  }
  const F = focusSet(v, g); FOCUS = F;
  for (const { o } of live) o.aliveNow = 0;
  for (const l of live) l.o.aliveNow = l.dead ? 0 : l.alive;
  for (const { o, s, alive, dead, deathV } of live) {
    let lx = 0, lz = 0; const fr = firing.get(o.i), am = aimHeading(o, s, fr, g);
    if (fr && am != null) { const b = bump(fr.dt, 1.4) * 0.012 * ZOOM; lx = Math.sin(am) * b; lz = Math.cos(am) * b; }
    o.bx = s.x; o.bz = s.z; o.off = { x: 0, z: 0, y: 0, s: 1 }; o.stackK = 0;
    const mx = s.x + lx, mz = s.z + lz, gy = yAt(mx, mz);
    const hover = o.helo && !dead ? (0.26 + 0.02 * Math.sin(v * 2.1 + o.i)) * Math.sqrt(ZOOM) : 0;
    o.root.position.set(mx - MAP_W / 2, gy + hover, mz - MAP_H / 2);
    o.root.scale.setScalar(SCALE_V5 * ZOOM);
    o.root.visible = counterVis && !o.mergedInto;
    if (o.stem) { const hs = hover / o.root.scale.y; o.stem.visible = hover > 0.05; o.stem.scale.set(1, hs, 1); o.stem.position.set(0, -hs / 2, 0); o.shadowBlob.position.y = -hs + 0.003; o.shadowBlob.visible = hover > 0.05; }
    // full counter for the units the shot is about, the tiny counter for everyone else (HOI4: zoomed-out map counters)
    const full = (o.members || [o.i]).some(i => F.has(i)) || !o.chip;
    o.flag.visible = full;
    if (o.chip) { o.chip.visible = counterVis && !o.mergedInto && !full; if (o.chip.visible) { const mem = o.members || [o.i], ck = chipKeyOf(o.badge, mem); if (ck !== o.chipKey) { const [a, t] = chipStrength(mem); drawChip(o, o.badge, a / Math.max(1, t)); o.chipKey = ck; } } }
    const fkey = alive + ':' + o.badge + ':' + (o.stackA ?? '');
    if (full && fkey !== o.lastKey) { drawFlag(o, alive, o.badge); o.lastAlive = alive; o.lastBadge = o.badge; o.lastKey = fkey; }
    o.hd = o.fd = o.P.hd;
    if (!o.helo) o.pivot.quaternion.setFromUnitVectors(UP, normalAt(mx, mz));
    let glow = 0;
    for (const l of lossSchedule) if (l.unit === o.i) { const dv = v - vOf(l.g); if (dv >= 0 && dv < 1.2) glow = Math.max(glow, 1 - dv / 1.2); }
    o.baseMat.emissive.setRGB(glow * 0.9, glow * 0.35, 0);
    const m = dead ? 0 : o.cap, fire = fr, aim = am;
    const lay = LAYOUT[o.cls === 'inf' ? 'i' : 'v'][Math.max(1, Math.min(m || 1, o.cap)) - 1];
    const cs = Math.cos(o.hd), sn = Math.sin(o.hd), bp = [];
    for (let k = 0; k < o.cap; k++) {
      const fg = o.figs[k];
      if (k >= m) {
        if (fg.deadV == null && o.prevFigs > k) {
          fg.deadV = v;
          if (!fg.inf && counterVis && o.root.visible) { fg.root.getWorldPosition(tmpV); boardWrecks.push({ key: o.key, side: o.side, p: tmpV.clone(), hd: o.hd + (k % 2 ? 0.5 : -0.4), sc: fg.root.scale.x * o.root.scale.x / ZOOM, born: v, k, unit: o.i }); }
        }
        const dying = fg.inf && fg.deadV != null && v - fg.deadV < 3.2;
        fg.root.visible = dying; if (dying) fg.s.act('death', v - fg.deadV);
        continue;
      }
      fg.deadV = null; fg.root.visible = true;
      const [fx_, fz_] = o.helo ? [0, 0] : lay[Math.min(k, lay.length - 1)];
      fg.root.position.set(fx_ * cs + fz_ * sn, 0.0145, -fx_ * sn + fz_ * cs);
      fg.root.rotation.y = figYaw(o, fg, fire, aim);
      poseFig(fg, o, v, s, fire, aim);
    }
    o.prevFigs = m;
    o.root.updateMatrixWorld(true);
    for (let k = 0; k < Math.max(1, m); k++) { const fg = o.figs[k]; if (!fg || !fg.root.visible) continue; bp.push(fg.inf ? fg.root.localToWorld(new THREE.Vector3(0, 1.3, 0.5)) : fg.vv.muzzle.getWorldPosition(new THREE.Vector3())); }
    if (dead) { const fade = clamp((v - deathV - 2.4) / 1.6, 0, 1); for (const mt of [o.baseMat, o.felt.material, o.flag.material]) { mt.transparent = true; mt.opacity = 1 - fade; } }
    else { for (const mt of [o.baseMat, o.felt.material]) { mt.transparent = false; mt.opacity = 1; } o.flag.material.opacity = 1; }
    if (!dead) { const st = stepAt(v); if (st) { const inStep = (o.members || [o.i]).some(i => st.units.includes(i)); o.flag.material.opacity = inStep ? 1 : 0.32; } }
    pieceWorld.set(o.i, o.root.localToWorld(new THREE.Vector3(0, 0.2, 0)));
    if (o.chip) o.chip.userData.p = o.root.localToWorld(new THREE.Vector3(0, 0.07, 0));
    if (counterVis) { if (bp.length) modelWorld.set(o.i, bp); centerWorld.set(o.i, o.root.localToWorld(new THREE.Vector3(0, o.helo ? 0 : 0.03, 0))); }
  }
  for (const { o } of live) if (o.mergedInto && counterVis) { const L = o.mergedInto; for (const M of [modelWorld, centerWorld, pieceWorld]) if (M.has(L.i)) M.set(o.i, M.get(L.i)); }
  liveUnits = live;
  updateModels(v, g, live);
}
let FOCUS = new Set();
// chips keep a fixed size on screen (the game's tiny counter at about its own pixel size)
function declutterChips(px, th) {
  camera.updateMatrixWorld(); const H = SHOT.height, Wd = SHOT.width, up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);
  const scr = p => { const q = p.clone().project(camera); return [(q.x + 1) / 2 * Wd, (1 - q.y) / 2 * H]; };
  const cw = px[0] * 66 / 20, ch = px[0] * CHIP_H / 20, fixed = [], placed = [];
  const ov = (r, q) => Math.max(0, Math.min(r[2], q[2]) - Math.max(r[0], q[0])) * Math.max(0, Math.min(r[3], q[3]) - Math.max(r[1], q[1]));
  if (V9) {                                       // v9: full counters first (lit ones, then nearest the camera keep their place), the others step up / down
    const fl = units.filter(o => o.root.visible && o.flag.visible).map(o => { o.flag.position.set(0, V4 ? 0.22 : 0.2, 0); o.flag.getWorldPosition(tmpV); return { o, q: scr(tmpV), w: tmpV.clone() }; })
      .sort((a, b) => b.o.flag.material.opacity - a.o.flag.material.opacity || b.q[1] - a.q[1] || a.o.i - b.o.i);   // lit (current order step) first
    const fw = px[1] * FLAG_W / FLAG_H, fh = px[1];
    for (const f of fl) { const r0 = [f.q[0] - fw / 2, f.q[1] - fh, f.q[0] + fw / 2, f.q[1]]; let pick = null;
      for (const d of [0, -(fh + 3), fh + 3, -2 * (fh + 3), 2 * (fh + 3), -3 * (fh + 3)]) { const r = [r0[0], r0[1] + d, r0[2], r0[3] + d];
        if (!fixed.some(q => ov(r, q) > 0.04 * fw * fh)) { pick = { d, r }; break; } }
      if (!pick) pick = { d: 0, r: r0 };
      if (pick.d) { const dist = camera.position.distanceTo(f.w); f.o.flag.position.copy(f.o.root.worldToLocal(f.w.clone().addScaledVector(up, -pick.d / H * 2 * dist * th))); }
      fixed.push(pick.r); }
  } else
  for (const o of units) if (o.root.visible && o.flag.visible) { o.flag.getWorldPosition(tmpV); const q = scr(tmpV), w = px[1] * FLAG_W / FLAG_H; fixed.push([q[0] - w / 2, q[1] - px[1], q[0] + w / 2, q[1]]); }
  // objective stars: the first spot around the hex centre that keeps clear of the full counters (they stay put)
  const sp = (SHOT.vpPx || [34])[0];
  for (const vo of vpObjs) if (vo.star) { const [mx, mz] = vo.star.userData.mxz; let best = null;
    for (const [dx, dz] of [[-0.1, -0.1], [0.12, 0.12], [-0.15, 0.08], [0.08, -0.15], [0, 0.17], [0, -0.17], [0.17, 0], [-0.17, 0]]) {
      const P = W(mx + dx, mz + dz); P.y += 0.004; const q = scr(P), r = [q[0] - sp / 2, q[1] - sp * 0.85, q[0] + sp / 2, q[1] + sp * 0.15];
      const sc = fixed.reduce((a, f) => a + ov(r, f), 0); if (!best || sc < best.sc) best = { P, r, sc }; if (sc === 0) break; }
    vo.star.position.copy(best.P); const hh = sp / H * 2 * camera.position.distanceTo(best.P) * th; vo.star.scale.set(hh * 116 / 108, hh, 1); fixed.push(best.r); }
  const chips = units.filter(o => o.chip && o.chip.visible).map(o => ({ o, q: scr(o.chip.position), n: o.badge || 1, mem: (o.members || [o.i]).slice() }))
    .sort((a, b) => b.n - a.n || b.o.u[5] - a.o.u[5] || a.o.i - b.o.i);
  for (const c of chips) {
    const r0 = [c.q[0] - cw / 2, c.q[1] - ch, c.q[0] + cw / 2, c.q[1]];
    const host = placed.find(p => p.o.side === c.o.side && ov(r0, p.r) > 0.2 * cw * ch);
    if (host) { host.n += c.n; host.mem.push(...c.mem); c.o.chip.visible = false; continue; }
    let pick = null;
    for (const d of [0, -(ch + 2), ch + 2, -2 * (ch + 2), 2 * (ch + 2)]) { const r = [r0[0], r0[1] + d, r0[2], r0[3] + d];
      if (!placed.some(p => ov(r, p.r) > 0.03 * cw * ch) && !fixed.some(q => ov(r, q) > 0.03 * cw * ch)) { pick = { d, r }; break; } }
    if (!pick) { const h2 = placed.find(p => p.o.side === c.o.side && ov(r0, p.r) > 0); if (h2) { h2.n += c.n; h2.mem.push(...c.mem); c.o.chip.visible = false; continue; } pick = { d: 0, r: r0 }; }   // nowhere free: join a touching stack of its side
    placed.push({ o: c.o, r: pick.r, n: c.n, d: pick.d, mem: c.mem });
  }
  for (const p of placed) {
    p.o.chipMem = p.mem;
    if (p.d) { const dist = camera.position.distanceTo(p.o.chip.position); p.o.chip.position.addScaledVector(up, -p.d / H * 2 * dist * th); }
    const ck = chipKeyOf(p.n, p.mem); if (ck !== p.o.chipKey) { const [a, t] = chipStrength(p.mem); drawChip(p.o, p.n, a / Math.max(1, t)); p.o.chipKey = ck; }
  }
}
function sizeChips() {
  const th = Math.tan(camera.fov * Math.PI / 360), px = SHOT.counterPx || [26, 54];
  for (const o of units) {
    const c = o.chip;
    if (c && c.visible && c.userData.p) { c.position.copy(c.userData.p); const d = camera.position.distanceTo(c.position), h = px[0] * CHIP_H / 20 / SHOT.height * 2 * d * th; c.scale.set(h * 66 / CHIP_H, h, 1); }
    if (o.root.visible && o.flag.visible) { o.flag.getWorldPosition(tmpV); const d = camera.position.distanceTo(tmpV), h = px[1] / SHOT.height * 2 * d * th / o.root.scale.y; o.flag.scale.set(h * FLAG_W / FLAG_H, h, 1); }
  }
  if (V7 && MODELS < 0.5) declutterChips(px, th);
}

// close-up models (camera key `models: 1`): up to 5 vehicles / fire teams per unit at x5 scale, on the terrain
function makeCloseEl(o) {
  const grp = new THREE.Group(), figs = [];
  if (o.cls === 'inf') for (let k = 0; k < 3; k++) { const f = makeFig(o, true); f.ph = (o.i * 0.37 + k * 0.29) % 1; f.root.position.set(TEAM[k][0], 0, TEAM[k][1]); grp.add(f.root); figs.push(f); }
  else { const f = makeFig(o, true); grp.add(f.root); figs.push(f); }
  scene.add(grp); return { grp, figs };
}
// v4 close-up name plate: Chinese name with the side prefix, type, and a strength bar with the numbers
function drawPlateV4(o, alive) {
  const c = o.npCv.getContext('2d'), w = o.npCv.width, h = o.npCv.height;
  c.clearRect(0, 0, w, h);
  const g = c.createLinearGradient(0, 0, 0, h); g.addColorStop(0, 'rgba(46,47,42,.94)'); g.addColorStop(1, 'rgba(22,23,20,.94)');
  c.fillStyle = g; c.beginPath(); c.roundRect(2, 2, w - 4, h - 4, 6); c.fill(); c.lineWidth = 3; c.strokeStyle = '#0a0a09'; c.stroke();
  c.fillStyle = o.side ? '#c4493b' : '#4a7fce'; c.fillRect(6, 6, 12, h - 12);
  c.textBaseline = 'middle'; c.font = '900 38px "Noto Sans SC",sans-serif'; c.fillStyle = '#fff';
  c.fillText(o.npText, 30, 32);
  const tw = c.measureText(o.npText).width; c.font = '700 26px "Noto Sans SC",sans-serif'; c.fillStyle = '#f2c84b'; c.fillText(o.npType, 30 + tw + 14, 34);
  const f = clamp(alive / Math.max(1, o.u[5]), 0, 1), by = 60, bw = w - 140;
  c.fillStyle = '#0b0b0a'; c.fillRect(30, by, bw, 20);
  c.fillStyle = f > 0.5 ? '#62b43c' : f > 0.25 ? '#d9a832' : '#d9503a'; c.fillRect(33, by + 3, (bw - 6) * f, 14);
  c.font = '700 26px "Oswald",sans-serif'; c.fillStyle = '#fff'; c.fillText(`${alive}/${o.u[5]}`, 30 + bw + 12, by + 11);
  o.npTex.needsUpdate = true;
}
function nameplateV4(o) {
  o.npText = sideZh(o.i); o.npType = `${TYPE_ZH[o.u[2]] || ''}${SIZE_ZH[o.u[3]] || ''}`;
  const m = canvas(8, 8).getContext('2d'); m.font = '900 38px "Noto Sans SC",sans-serif'; const w1 = m.measureText(o.npText).width; m.font = '700 26px "Noto Sans SC",sans-serif';
  o.npCv = V6 ? canvas(Math.max(420, Math.ceil(166 + 24 + w1 * 36 / 38 + 16)), 92) : canvas(Math.max(360, Math.ceil(30 + w1 + 14 + m.measureText(o.npType).width + 20)), 92);
  o.npTex = new THREE.CanvasTexture(o.npCv); o.npTex.colorSpace = THREE.SRGBColorSpace; o.npTex.anisotropy = 4;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: o.npTex, transparent: true, depthWrite: false, depthTest: false, toneMapped: false, fog: false }));
  const hgt = 0.02; sp.scale.set(hgt * o.npCv.width / o.npCv.height, hgt, 1); sp.center.set(0.5, 0); sp.renderOrder = 26; o.npAlive = -1;
  scene.add(sp); return sp;
}
function updateModels(v, g, live) {
  const near = 2.6, close = MODELS >= 0.5;
  if (V4 && close && !V5) {                            // separate formations: no two units overlap, enemies face each other
    const items = live.filter(l => !l.o.helo && Math.hypot(l.o.bx - MAP_W / 2 - camTarget.x, l.o.bz - MAP_H / 2 - camTarget.z) < near + 0.6)
      .map(l => ({ o: l.o, side: l.o.side, k: l.o.i, x: l.o.bx, z: l.o.bz, x0: l.o.bx, z0: l.o.bz }));
    relax(items, (a, b) => (a.side === b.side ? 0.15 : 0.2), 0.5, 24);
    for (const it of items) { const o = it.o, snap = o.cAt == null || v - o.cAt > 0.1;
      o.cx = snap ? it.x : lerp(o.cx, it.x, 0.12); o.cz = snap ? it.z : lerp(o.cz, it.z, 0.12); o.cAt = v; }
  }
  for (const { o, s, alive, dead } of live) {
    if (!o.models) o.models = { list: [], plate: null };
    const M = o.models, total = o.u[5], m = dead ? 0 : V4 ? Math.max(1, Math.min(3, Math.round(3 * alive / Math.max(1, total)))) : modelsFor(alive, total);
    const fire0 = firing.get(o.i), aim0 = aimHeading(o, s, fire0, g);
    let cx = s.x + o.off.x * 0.8, cz = s.z + o.off.z * 0.8;
    if (V5) { cx = o.P.cx; cz = o.P.cz; if (V6) { const e = engageOffset(o.i, o.side, g); cx += e[0]; cz += e[1]; } if (fire0 && aim0 != null) { const b = bump(fire0.dt, 1.4) * (o.cls === 'inf' ? 0.008 : 0.005); cx += Math.sin(aim0) * b; cz += Math.cos(aim0) * b; } }
    else if (V4 && o.cx != null && o.cAt === v) { cx = o.cx; cz = o.cz; if (fire0 && aim0 != null) { const b = bump(fire0.dt, 1.0) * (o.cls === 'inf' ? 0.028 : 0.012); cx += Math.sin(aim0) * b; cz += Math.cos(aim0) * b; } }
    const dCam = Math.hypot(cx - MAP_W / 2 - camTarget.x, cz - MAP_H / 2 - camTarget.z);
    const show = close && dCam < near && !o.helo;
    const fdir = V4 ? (o.fd ?? o.hd) : o.hd, cs = Math.cos(fdir), sn = Math.sin(fdir), sp = o.cls === "inf" ? 0.032 : 0.05;
    const pos = [];
    for (let k = 0; k < CAP; k++) { const [lx, lz] = SLOTS[k]; pos.push([cx + (lx * cs + lz * sn) * sp, cz + (-lx * sn + lz * cs) * sp]); }
    // models lost since the previous frame: vehicles become wrecks, soldiers fall where they stood
    if (o.prevM > m && M.list.length) {
      for (let k = m; k < o.prevM; k++) if (M.list[k]) {
        if (o.cls === 'inf') for (let j = 0; j < 3; j++) corpses.push({ side: o.side, x: pos[k][0] + TEAM[j][0] * cs, z: pos[k][1] + TEAM[j][1], hd: o.hd + j, born: v, unit: o.i, k: k * 3 + j });
        else wrecks.push({ unit: o.i, key: o.key, side: o.side, x: pos[k][0], z: pos[k][1], hd: o.hd + (k % 2 ? 0.5 : -0.4), born: v, k });
      }
    }
    o.prevM = m;
    const fire = firing.get(o.i), aim = aimHeading(o, s, fire, g), bp = [];
    for (let k = 0; k < CAP; k++) {
      let el = M.list[k];
      const vis = show && k < m;
      if (!vis) { if (el) el.grp.visible = false; continue; }
      if (!el) { el = makeCloseEl(o); M.list[k] = el; }
      const [wx, wz] = pos[k], y = yAt(wx, wz), n = normalAt(wx, wz);
      el.grp.visible = true; el.grp.position.set(wx - MAP_W / 2, y, wz - MAP_H / 2);
      el.grp.quaternion.setFromUnitVectors(UP, n);
      for (const f of el.figs) { const fk = fire && { ...fire, dt: fire.dt - k * 0.09 };
        if (V5) f.root.rotation.y = figYaw(o, f, fk, aim); else if (V4) turnFig(f, f.inf && aim != null ? aim : o.hd); else f.root.rotation.y = o.hd;
        poseFig(f, o, v + k * 0.21, s, fk, aim); }
      el.grp.updateMatrixWorld(true);
      for (const f of el.figs) bp.push(f.inf ? f.root.localToWorld(new THREE.Vector3(0, 1.3, 0.6)) : f.vv.muzzle.getWorldPosition(new THREE.Vector3()));
      if (k === 0) centerWorld.set(o.i, new THREE.Vector3(wx - MAP_W / 2, y + 0.004, wz - MAP_H / 2));
    }
    if (close && bp.length) modelWorld.set(o.i, bp);
    if (show && m > 0 && (V4 || dCam < 0.9)) {
      if (!M.plate) M.plate = V4 ? nameplateV4(o) : nameplate(o);
      if (V4 && o.npAlive !== alive) { if (V6 && CT6) drawPlateV6(o, alive); else drawPlateV4(o, alive); o.npAlive = alive; }
      const [wx, wz] = V4 ? [cx, cz] : pos[0]; M.plate.visible = true; M.plate.position.set(wx - MAP_W / 2, yAt(wx, wz) + (V4 ? 0.03 : 0.024), wz - MAP_H / 2);
    } else if (M.plate) M.plate.visible = false;
  }
  for (const u of units) if (u.models && !live.some(l => l.o === u)) { for (const el of u.models.list) if (el) el.grp.visible = false; if (u.models.plate) u.models.plate.visible = false; }
}
// close-up name plates: nearest plate keeps its place, the others step up until they no longer overlap on screen
function declutterPlates() {
  const H = SHOT.height, Wd = SHOT.width, th = Math.tan(camera.fov * Math.PI / 360);
  camera.updateMatrixWorld();
  const items = [];
  for (const u of units) { const p = u.models && u.models.plate; if (!p || !p.visible) continue;
    const q = p.position.clone().project(camera), h = p.scale.y / (2 * th) * H;
    items.push({ p, x: (q.x + 1) / 2 * Wd, y: (1 - q.y) / 2 * H, w: h * p.scale.x / p.scale.y, h, k: 0 }); }
  items.sort((a, b) => b.y - a.y);
  const placed = [];
  for (const it of items) {
    const hit = k => placed.some(o => Math.abs(o.x - it.x) < (o.w + it.w) / 2 + 8 && Math.abs((o.y - o.k * o.h * 1.15) - (it.y - k * it.h * 1.15)) < (o.h + it.h) / 2 + 3);
    let k = 0; while (k < 6 && hit(k)) k++; it.k = k; placed.push(it);
    const want = 0.5 - k * 1.15, cy = it.p.userData.cy ?? want; it.p.userData.cy = Math.abs(want - cy) > 3 ? want : lerp(cy, want, 0.3);
    it.p.center.y = it.p.userData.cy;
  }
}
// v7 close-ups: an objective's "(c,r) · value" sign steps below / above the unit name plates it would cover
function declutterVpSigns() {
  const H = SHOT.height, Wd = SHOT.width, up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion), rt = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
  camera.updateMatrixWorld();
  const scr = v => { const q = v.clone().project(camera); return [(q.x + 1) / 2 * Wd, (1 - q.y) / 2 * H]; };
  const rect = sp => { const a = scr(sp.position), b = scr(sp.position.clone().addScaledVector(up, sp.scale.y)), c = scr(sp.position.clone().addScaledVector(rt, sp.scale.x));
    const h = Math.hypot(b[0] - a[0], b[1] - a[1]), w = Math.hypot(c[0] - a[0], c[1] - a[1]);
    return [a[0] - sp.center.x * w, a[1] - (1 - sp.center.y) * h, a[0] + (1 - sp.center.x) * w, a[1] + sp.center.y * h, h]; };
  const plates = [];
  for (const u of units) { const p = u.models && u.models.plate; if (p && p.visible) plates.push(rect(p)); }
  const ov = (r, q) => r[0] < q[2] + 6 && r[2] > q[0] - 6 && r[1] < q[3] + 4 && r[3] > q[1] - 4;
  for (const o of vpObjs) { const L = o.label; if (!L.visible) continue; L.center.y = 0.5;
    for (const cy of [0.5, -0.75, 1.75, -2.0, 3.0]) { L.center.y = cy; const r = rect(L); if (!plates.some(q => ov(r, q))) break; }
    if (plates.some(q => ov(rect(L), q))) L.center.y = 0.5;
  }
}
// wrecks (close-up: at x5 on the terrain; board: at figurine scale where the figure stood) and fallen soldiers
let wrecks = [], wreckPool = [], corpsePool = [];
function wreckModel(key, side) {
  let md = wreckPool.find(x => !x.used && x.key === key);
  if (!md) { const vv = makeVehicle(key, side, V9 ? 0.42 : true); md = { key, vv, used: false }; scene.add(vv.root); wreckPool.push(md); }
  md.used = true; md.vv.root.visible = true; return md.vv;
}
function burn(p, age, seed, k, size) {               // fire + smoke column on a wreck
  const R = rng(seed);
  if (age < 30) { const f = take(fx.flash); if (f) { f.position.set(p.x, p.y + 0.3 * size, p.z); f.scale.setScalar(size * (0.9 + 0.4 * Math.sin(age * 17 + k))); f.material.opacity = 0.8 * (1 - age / 30); } }
  for (let j = 0; j < 4; j++) {
    const sm = take(fx.smoke); if (!sm) break;
    const ph = ((age * 0.35 + j / 4 + R()) % 1);
    sm.position.set(p.x + ph * 1.4 * size, p.y + size * (0.5 + ph * 5), p.z + ph * 0.8 * size); sm.scale.setScalar(size * (0.9 + ph * 3.6));
    sm.material.opacity = 0.55 * (1 - ph) * clamp(1 - (age - 40) / 50, 0.25, 1);
  }
}
function updateWrecks(v) {
  for (const w of wreckPool) { w.used = false; w.vv.root.visible = false; }
  for (const c of corpsePool) c.used = false;
  const close = MODELS >= 0.5;
  if (close) for (const w of wrecks) {
    if (v - w.born > 120) continue;
    const vv = wreckModel(w.key, w.side), y = yAt(w.x, w.z), n = normalAt(w.x, w.z);
    vv.root.scale.setScalar(MS); vv.root.position.set(w.x - MAP_W / 2, y - 0.0012, w.z - MAP_H / 2);
    vv.root.quaternion.setFromUnitVectors(UP, n).multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), 0.1)).multiply(new THREE.Quaternion().setFromAxisAngle(UP, w.hd));
    if (vv.turret) { vv.turret.rotation.y = 0.9 + w.k; vv.turret.position.y = vv.turret.userData.y0 ?? (vv.turret.userData.y0 = vv.turret.position.y); vv.turret.rotation.z = 0.12; }
    burn(new THREE.Vector3(w.x - MAP_W / 2, y, w.z - MAP_H / 2), v - w.born, w.unit * 31 + w.k * 7, w.k, 0.012);
  } else for (const w of boardWrecks) {
    if (v - w.born > 50) continue;
    const vv = wreckModel(w.key, w.side);
    vv.root.scale.setScalar(w.sc * ZOOM);
    vv.root.position.copy(w.p); vv.root.rotation.set(0, w.hd, 0.12);
    if (vv.turret) vv.turret.rotation.y = 1.1 + w.k;
    burn(w.p, v - w.born, w.unit * 31 + w.k * 7, w.k, 0.03 * Math.sqrt(ZOOM));
  }
  if (close) for (const c of corpses) {
    if (v - c.born > 120) continue;
    let cp = corpsePool.find(x => !x.used && x.side === c.side);
    if (!cp) { const s = makeSoldier(c.side); s.root.scale.setScalar(MS * 1.8); scene.add(s.root); cp = { s, side: c.side, used: false }; corpsePool.push(cp); }
    cp.used = true; const y = yAt(c.x, c.z);
    cp.s.root.position.set(c.x - MAP_W / 2, y, c.z - MAP_H / 2); cp.s.root.rotation.y = c.hd; cp.s.act('death', v - c.born);
  }
  for (const c of corpsePool) c.s.root.visible = c.used;
}

function unitWorld(i, g, k = 0) {                  // muzzle of figure k (this frame), else the stand / piece
  if (modelWorld.has(i)) { const a = modelWorld.get(i); return a[k % a.length].clone(); }
  if (pieceWorld.has(i)) return pieceWorld.get(i).clone();
  const s = unitState(i, g); if (!s || isNaN(s.x)) return null;
  return W(s.x, s.z, 0.1 + (isHelo(i) ? 0.24 : 0));
}
function targetWorld(i, g) {                       // where hits land: middle of the target's figures
  if (centerWorld.has(i)) return centerWorld.get(i).clone();
  return unitWorld(i, g);
}

// ---------------------------------------------------------------- v5 effects: ready-made sprites, no drawn gradients
// Sinestesia "Free Explosion Animations" 1 / 2 (CC0, 8x8 flipbooks): shell impacts and kills; Kenney Smoke Particles
// (CC0): black smoke / white puff / explosion / flash frames; Kenney Particle Pack (CC0): muzzle flame, fire, tracer
// streak, scorch decals (the craters, tinted dark and left on the ground). Frames and offsets are seeded per event, so
// every frame is still a pure function of time.
let FX5 = null, CRATERS = [];
async function buildFxV5() {
  const ld = async n => { const t = await new THREE.TextureLoader().loadAsync(SHOT.fxBase + n); t.colorSpace = THREE.SRGBColorSpace; return t; };
  const T5 = { impact: await ld('impact_8x8.png'), kill: await ld('kill_8x8.png'), smoke: await ld('blacksmoke_5x5.png'), puff: await ld('whitepuff_5x5.png'),
    flash: await ld('flash_3x3.png'), muzzle: await ld('muzzle_02.png'), fire: await ld('fire_01.png'), trace: await ld('trace_01.png'),
    scorch: [await ld('scorch_01.png'), await ld('scorch_02.png'), await ld('scorch_03.png')] };
  const pool = (n, tex, add, ro, grid) => { const a = [];
    for (let k = 0; k < n; k++) { const t = grid ? tex.clone() : tex; if (grid) t.repeat.set(1 / grid, 1 / grid);
      const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, blending: add ? THREE.AdditiveBlending : THREE.NormalBlending, transparent: true, depthWrite: false, toneMapped: false, fog: false }));
      s.renderOrder = ro; s.visible = false; scene.add(s); a.push(s); }
    return { a, used: 0, grid }; };
  const tg = new THREE.PlaneGeometry(1, 1), tg2 = tg.clone().rotateY(Math.PI / 2), trGeo = new THREE.BufferGeometry();
  { const a = tg.toNonIndexed(), b = tg2.toNonIndexed(); trGeo.setAttribute('position', new THREE.Float32BufferAttribute([...a.attributes.position.array, ...b.attributes.position.array], 3));
    trGeo.setAttribute('uv', new THREE.Float32BufferAttribute([...a.attributes.uv.array, ...b.attributes.uv.array], 2)); }
  const tracers = { a: [], used: 0 };
  for (let k = 0; k < 160; k++) { const m = new THREE.Mesh(trGeo, new THREE.MeshBasicMaterial({ map: T5.trace, color: 0xffd9a0, blending: THREE.AdditiveBlending, transparent: true, depthWrite: false, side: THREE.DoubleSide, toneMapped: false, fog: false }));
    m.renderOrder = 19; m.visible = false; m.frustumCulled = false; scene.add(m); tracers.a.push(m); }
  const craterMeshes = T5.scorch.map(t => { const g = new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2);
    const m = new THREE.InstancedMesh(g, new THREE.MeshBasicMaterial({ map: t, color: V7 ? 0x4a3c2c : 0x2a2018, transparent: true, opacity: V7 ? 0.6 : 0.85, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2, toneMapped: false }), 1500);
    m.count = 0; m.renderOrder = 2; m.frustumCulled = false; scene.add(m); return m; });
  FX5 = { impact: pool(80, T5.impact, false, 20, 8), kill: pool(40, T5.kill, false, 20, 8), smoke: pool(500, T5.smoke, false, 15, 5), puff: pool(240, T5.puff, false, 15, 5),
    flash: pool(140, T5.flash, true, 21, 3), muzzle: pool(140, T5.muzzle, true, 21, 0), fire: pool(140, T5.fire, true, 19, 0), tracers, craterMeshes };
}
function take5(p) { const o = p.a[p.used++]; if (!o) return null; o.visible = true; return o; }
function frame5(sp, grid, f) { const t = sp.material.map, c = f % grid, r = Math.floor(f / grid) % grid; t.offset.set(c / grid, 1 - (r + 1) / grid); }
// deterministic crater / impact points (map km): barrage shells around the target, kills under the unit
function buildCraters() {
  CRATERS = [];
  const at = (i, vI) => { const P = planAt(i, vI); return [P.cx, P.cz]; };
  for (const sh of shotSchedule) {
    if (!BARRAGE(sh.kind)) continue;
    const vI = vOf(sh.g) + sh.k * 0.12, R = rng(sh.seed);
    let c = sh.target >= 0 && PLAN[sh.target] && PLAN[sh.target].h0 ? at(sh.target, vI) : sh.hexT ? hexXZ(sh.hexT) : null; if (!c) continue;
    const a = R() * 6.283, d = 0.03 + R() * 0.16;
    sh.ix = c[0] + Math.cos(a) * d; sh.iz = c[1] + Math.sin(a) * d; sh.vI = vI;
    CRATERS.push({ x: sh.ix, z: sh.iz, v: vI + 0.15, s: 0.03 + R() * 0.018, t: Math.floor(R() * 3), r: R() * 6.283 });
  }
  for (const l of lossSchedule) {
    if (!PLAN[l.unit] || !PLAN[l.unit].h0) continue; const vI = vOf(l.g), R = rng(l.unit * 7919 + Math.round(l.g)), [x, z] = at(l.unit, vI);
    l.ix = x + (R() - 0.5) * 0.03; l.iz = z + (R() - 0.5) * 0.03;
    CRATERS.push({ x: l.ix, z: l.iz, v: vI + 0.2, s: 0.035 + R() * 0.015, t: Math.floor(R() * 3), r: R() * 6.283 });
  }
  CRATERS.sort((a, b) => a.v - b.v);
}
function updateCraters(v) {
  const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), q2 = new THREE.Quaternion(), n = [0, 0, 0];
  for (const c of CRATERS) { if (c.v > v) break; if (v - c.v > 100) continue; const M = FX5.craterMeshes[c.t]; if (M.count >= 1500) continue;
    q.setFromUnitVectors(UP, normalAt(c.x, c.z)).multiply(q2.setFromAxisAngle(UP, c.r)); const s = c.s * clamp((v - c.v) / 0.3, 0.3, 1);
    m4.compose(new THREE.Vector3(c.x - MAP_W / 2, yAt(c.x, c.z) + 0.0012, c.z - MAP_H / 2), q, new THREE.Vector3(s, s, s)); M.setMatrixAt(M.count++, m4); n[c.t]++; }
  for (const M of FX5.craterMeshes) M.instanceMatrix.needsUpdate = true;
}
const W5 = (x, z, dy) => new THREE.Vector3(x - MAP_W / 2, yAt(x, z) + dy, z - MAP_H / 2);
function smokeColumn(p, age, seed, size, n, dur, dark = true) {   // puffs rising and spreading, each from a random frame
  const R = rng(seed);
  for (let j = 0; j < n; j++) {
    const fr = R(), dx = (R() - 0.5) * 0.6, dz = (R() - 0.5) * 0.6, rot = R() * 6.283;
    const a = (age - j * dur / n * 0.35) / dur; if (a < 0 || a > 1) continue;
    const sp = take5(dark ? FX5.smoke : FX5.puff); if (!sp) return;
    frame5(sp, 5, Math.floor(fr * 25));
    sp.position.set(p.x + (dx + a * 0.8) * size, p.y + size * (0.2 + a * 2.6), p.z + (dz + a * 0.4) * size);
    sp.scale.setScalar(size * (0.6 + a * 1.8)); sp.material.rotation = rot + a * 0.6;
    sp.material.opacity = (dark ? 0.7 : 0.55) * Math.min(1, a * 6) * (1 - a);
    sp.material.color.setScalar(dark ? 0.55 + 0.25 * a : 1);
  }
}
function flipbook(pool, p, age, dur, size) { if (age < 0 || age > dur) return; const sp = take5(pool); if (!sp) return; frame5(sp, 8, Math.min(63, Math.floor(age / dur * 64))); sp.position.copy(p); sp.scale.setScalar(size); sp.material.opacity = 1; }
function burnV5(p, age, seed, size) {                // wreck: flickering flame for half a minute, then a thinning smoke column
  const R = rng(seed);
  if (age < 30) for (let j = 0; j < 2; j++) { const f = take5(FX5.fire); if (!f) break; const fl = 0.75 + 0.25 * Math.sin(age * (13 + j * 4) + seed);
    f.position.set(p.x + (R() - 0.5) * size * 0.3, p.y + size * (0.25 + j * 0.15), p.z + (R() - 0.5) * size * 0.3); f.scale.setScalar(size * (0.8 + 0.3 * fl) * (1 - age / 45));
    f.material.color.setRGB(1, 0.55 + 0.15 * fl, 0.2); f.material.opacity = 0.9 * (1 - age / 30); f.material.rotation = R() * 0.6 - 0.3; }
  const loop = 3.2, k0 = Math.floor(age / loop * 4);
  for (let j = 0; j < 4; j++) { const k = k0 - j, a = (age - k * loop / 4) / loop; if (k < 0 || a < 0 || a > 1) continue;
    smokeColumn(p, a * loop, seed * 13 + k, size * clamp(1 - (age - 40) / 60, 0.35, 1), 1, loop, true); }
}
function updateWrecksV5(v) {
  for (const w of wreckPool) { w.used = false; w.vv.root.visible = false; }
  for (const c of corpsePool) c.used = false;
  const close = MODELS >= 0.5;
  if (close) for (const w of wrecks) {
    if (v - w.born > 120) continue;
    const vv = wreckModel(w.key, w.side), y = yAt(w.x, w.z), n = normalAt(w.x, w.z);
    vv.root.scale.setScalar(MS); vv.root.position.set(w.x - MAP_W / 2, y - 0.0012, w.z - MAP_H / 2);
    vv.root.quaternion.setFromUnitVectors(UP, n).multiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), 0.1)).multiply(new THREE.Quaternion().setFromAxisAngle(UP, w.hd));
    if (vv.turret) vv.turret.rotation.y = 0.9 + w.k;
    burnV5(new THREE.Vector3(w.x - MAP_W / 2, y, w.z - MAP_H / 2), v - w.born, w.unit * 31 + w.k * 7, 0.016);
  } else for (const w of boardWrecks) {
    if (v - w.born > 50) continue;
    const vv = wreckModel(w.key, w.side);
    vv.root.scale.setScalar(w.sc * ZOOM); vv.root.position.copy(w.p); vv.root.rotation.set(0, w.hd, 0.12);
    if (vv.turret) vv.turret.rotation.y = 1.1 + w.k;
    burnV5(w.p, v - w.born, w.unit * 31 + w.k * 7, 0.05 * Math.sqrt(ZOOM));
  }
  if (close) for (const c of corpses) {
    if (v - c.born > 120) continue;
    let cp = corpsePool.find(x => !x.used && x.side === c.side);
    if (!cp) { const s = makeSoldier(c.side); s.root.scale.setScalar(MS * 1.8); scene.add(s.root); cp = { s, side: c.side, used: false }; corpsePool.push(cp); }
    cp.used = true; cp.s.root.position.set(c.x - MAP_W / 2, yAt(c.x, c.z), c.z - MAP_H / 2); cp.s.root.rotation.y = c.hd; cp.s.act('death', v - c.born);
  }
  for (const c of corpsePool) c.s.root.visible = c.used;
}
function updateFxV5(v, g) {
  for (const k of ['impact', 'kill', 'smoke', 'puff', 'flash', 'muzzle', 'fire', 'tracers']) { for (const o of FX5[k].a) o.visible = false; FX5[k].used = 0; }
  for (const M of FX5.craterMeshes) M.count = 0;
  fx.text.forEach(s => { s.visible = false; });
  updateWrecksV5(v); updateCraters(v);
  const close = MODELS >= 0.5, Z = Math.sqrt(ZOOM), K = close ? 0.14 : Z;
  // muzzle flash on the firing figures, once per cycle
  for (const [i, f] of firing) {
    if (f.dt < 0 || f.dt > 0.14) continue; const pts = modelWorld.get(i); if (!pts) continue; const R = rng(i * 131 + Math.round(f.v0 * 30));
    for (const p of pts.slice(0, 3)) { const s = take5(FX5.flash); if (!s) break; frame5(s, 3, Math.floor(R() * 9)); s.position.copy(p);
      s.scale.setScalar((close ? 0.012 : 0.09 * Z) * (BARRAGE(f.sh.kind) ? 1.8 : 1)); s.material.opacity = 1 - f.dt / 0.14; s.material.rotation = R() * 6.283; }
  }
  for (const sh of shotSchedule) {
    if (BARRAGE(sh.kind)) {                          // shell lands: explosion flipbook, dust and smoke, crater left behind
      if (sh.ix == null) continue; const age = v - sh.vI; if (age < 0 || age > 3.2) continue;
      const p = W5(sh.ix, sh.iz, 0.002), sz = close ? 0.07 : 0.32 * Z;
      flipbook(FX5.impact, p.clone().setY(p.y + sz * 0.3), age, 1.3, sz);
      smokeColumn(p, age, sh.seed, close ? 0.012 : 0.05 * Z, 2, 3.2, false);
      continue;
    }
    const age = v - sh.vL; if (age < 0 || age > FLIGHT + 1.2) continue;
    const gL = gOf(sh.vL), from = unitWorld(sh.from, gL, sh.k); if (!from) continue;
    let tgt = sh.target >= 0 ? sh.target : nearestEnemy(sh.from, gL, 5), to = tgt != null ? targetWorld(tgt, gL) : null;
    const R = rng(sh.seed);
    if (!to) to = from.clone().add(new THREE.Vector3((R() - 0.5) * 2, -0.4, (R() - 0.5) * 2)); to = to.clone(); if (!close) to.y -= 0.04;
    const miss = (sh.hitLoss ? 0.02 : 0.1 + R() * 0.12) * (close ? 0.2 : 1); to.x += (R() - 0.5) * miss * 2; to.z += (R() - 0.5) * miss * 2;
    to.y = Math.max(to.y, yAt(to.x + MAP_W / 2, to.z + MAP_H / 2) + 0.002);
    const missile = sh.kind === 'Helo' || sh.kind === 'Tank' || sh.kind === 'AT' || sh.kind === 'SP AT';
    if (age <= FLIGHT) {
      const f = age / FLIGHT, head = from.clone().lerp(to, f); if (missile) head.y += Math.sin(f * Math.PI) * 0.05 * (close ? 0.15 : 1);
      let tail = from.clone().lerp(to, Math.max(0, f - (missile ? 0.35 : 0.15))); const maxL = close ? 0.05 : 0.6 * Z;
      if (head.distanceTo(tail) > maxL) tail = head.clone().addScaledVector(tail.clone().sub(head).normalize(), maxL);
      const tr = take5(FX5.tracers);
      if (tr) { const d = head.clone().sub(tail), len = d.length(); tr.position.copy(tail).addScaledVector(d, 0.5); tr.quaternion.setFromUnitVectors(UP, d.normalize());
        const w = close ? 0.004 : 0.05 * Z; tr.scale.set(w, Math.max(0.001, len), w); tr.material.color.set(missile ? 0xffe0b0 : 0xffc070); tr.material.opacity = 0.95; }
    } else if (!sh.hitLoss) {                       // miss: puff of dust where it lands
      const a = age - FLIGHT; const s = a < 0.12 ? take5(FX5.flash) : null;
      if (s) { frame5(s, 3, Math.floor(R() * 9)); s.position.copy(to); s.scale.setScalar(close ? 0.01 : 0.08 * Z); s.material.opacity = 1 - a / 0.12; }
      smokeColumn(to, a, sh.seed + 7, close ? 0.006 : 0.035 * Z, 1, 1.2, false);
    }
  }
  // losses: kill explosion, black smoke column, "-n"
  for (const l of lossSchedule) {
    const vI = vOf(l.g), age = v - vI; if (age < 0 || age > 4) continue;
    let p = null;
    if (close) { const w = wrecks.filter(x => x.unit === l.unit && Math.abs(x.born - vI) < 0.2).pop(); if (w) p = W5(w.x, w.z, 0.004); }
    if (!p) p = targetWorld(l.unit, l.g) || (l.ix != null ? W5(l.ix, l.iz, 0.01) : null); if (!p) continue;
    const sz = close ? 0.09 : 0.5 * Z * (1 + Math.min(l.n, 8) * 0.04);
    flipbook(FX5.kill, p.clone().setY(p.y + sz * 0.25), age, 1.6, sz);
    smokeColumn(p, age, l.unit * 7919 + Math.round(l.g), close ? 0.016 : 0.07 * Z, 3, 4, true);
    if (age < 2.2) {
      const s = popupSprite(l.n, l.side), a = age / 2.2;
      s.position.set(p.x, p.y + (0.18 + 0.35 * smooth(clamp(a * 1.6, 0, 1))) * (close ? 0.12 : 1), p.z);
      const pop = 1 + 0.35 * Math.max(0, 1 - age / 0.25), img = s.material.map.image, hgt = 0.24 * pop * (close ? 0.1 : 1); s.scale.set(hgt * img.width / img.height, hgt, 1);
      s.material.opacity = a < 0.75 ? 1 : 1 - (a - 0.75) / 0.25;
    }
  }
}
function updateFx(v, g) {
  if (V5) return updateFxV5(v, g);
  for (const k of ['flash', 'smoke', 'head', 'trail']) { fx[k].a.forEach(o => { o.visible = false; }); fx[k].used = 0; }
  fx.text.forEach(s => { s.visible = false; });
  updateWrecks(v);
  const S = FXS, close = MODELS >= 0.5;
  for (const [i, f] of firing) {                    // muzzle flash + smoke on the firing figures
    if (f.dt < 0 || f.dt > 0.9) continue; const pts = modelWorld.get(i); if (!pts) continue; const k = close ? 0.1 : 0.45 * Math.sqrt(ZOOM);
    for (const p of pts.slice(0, 3)) {
      if (f.dt < 0.12) { const mf = take(fx.flash); if (mf) { mf.position.copy(p); mf.scale.setScalar(0.1 * k * (BARRAGE(f.sh.kind) ? 1.6 : 1)); mf.material.opacity = 1 - f.dt / 0.12; } }
      const sm = take(fx.smoke); if (sm) { const a = f.dt / 0.9; sm.position.set(p.x, p.y + a * 0.05 * k, p.z); sm.scale.setScalar((0.04 + a * 0.12) * k); sm.material.opacity = 0.45 * (1 - a); }
    }
  }
  for (const sh of shotSchedule) {
    if (BARRAGE(sh.kind)) {                          // barrage: shells land scattered around the target, no trajectory drawn
      const a = (v - vOf(sh.g) - sh.k * 0.12) / 0.9; if (a < 0 || a > 1) continue;
      const to = sh.target >= 0 ? targetWorld(sh.target, sh.g) : sh.hexT ? W(...hexXZ(sh.hexT), 0) : null; if (!to) continue;
      const R = rng(sh.seed), spread = close ? 0.22 : 0.7, px = to.x + (R() - 0.5) * spread, pz = to.z + (R() - 0.5) * spread, py = yAt(px + MAP_W / 2, pz + MAP_H / 2) + 0.04 * S;
      const f = a < 0.4 ? take(fx.flash) : null; if (f) { f.position.set(px, py, pz); f.scale.setScalar((0.2 * (1 - a / 0.4) + 0.06) * Math.sqrt(ZOOM) * (close ? 0.3 : 1)); f.material.opacity = 1 - a / 0.4; }
      const sm = take(fx.smoke); if (sm) { sm.position.set(px, py + a * 0.12 * (close ? 0.3 : 1), pz); sm.scale.setScalar((0.1 + a * 0.25) * Math.sqrt(ZOOM) * (close ? 0.3 : 1)); sm.material.opacity = 0.6 * (1 - a); }
      continue;
    }
    const vL = sh.vL;
    const age = v - vL; if (age < 0 || age > FLIGHT + 1.0) continue;
    const gL = gOf(vL), from = unitWorld(sh.from, gL, sh.k); if (!from) continue;
    let tgt = sh.target >= 0 ? sh.target : nearestEnemy(sh.from, gL, 5);
    let to = tgt != null ? targetWorld(tgt, gL) : null;
    const R = rng(sh.seed);
    if (!to) { to = from.clone().add(new THREE.Vector3((R() - 0.5) * 2 * S, -0.4 * S, (R() - 0.5) * 2 * S)); }
    to = to.clone(); if (!close) to.y -= 0.06;
    const miss = (sh.hitLoss ? 0.03 : 0.12 + R() * 0.15) * (close ? 0.25 : 1); to.x += (R() - 0.5) * miss * 2; to.z += (R() - 0.5) * miss * 2;
    if (close) to.y = Math.max(to.y, yAt(to.x + MAP_W / 2, to.z + MAP_H / 2) + 0.003);
    const isMissile = sh.kind === 'Helo' || sh.kind === 'Tank' || sh.kind === 'AT' || sh.kind === 'SP AT';
    if (age <= FLIGHT) {
      const f = age / FLIGHT, head = from.clone().lerp(to, f);
      if (isMissile) head.y += Math.sin(f * Math.PI) * 0.06 * S;
      let tail = from.clone().lerp(to, Math.max(0, f - (isMissile ? 0.45 : 0.12)));
      if (close && head.distanceTo(tail) > 0.04) tail = head.clone().addScaledVector(tail.clone().sub(head).normalize(), 0.04);   // short tracer streaks in close-ups
      const tr = take(fx.trail);
      if (tr) {
        const d = head.clone().sub(tail), len = d.length();
        tr.position.copy(tail).addScaledVector(d, 0.5); tr.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), d.normalize());
        const w = close ? (isMissile ? 0.0016 : 0.0008) : (isMissile ? 0.011 : 0.006) * Math.sqrt(ZOOM); tr.scale.set(w, Math.max(0.001, len), w);
        tr.material.color.set(sh.side === 0 ? 0xffe6b8 : 0xffc66e); tr.material.opacity = isMissile ? 0.75 : 0.9;
      }
      const hd = take(fx.head); if (hd) { hd.position.copy(head); hd.scale.setScalar(close ? (isMissile ? 0.012 : 0.006) : (isMissile ? 0.16 : 0.08) * Math.sqrt(ZOOM)); hd.material.opacity = 1; }
      if (age < 0.12) { const mf = take(fx.flash); if (mf) { mf.position.copy(from); mf.scale.setScalar((isMissile ? 0.22 : 0.12) * (close ? 0.15 : 1)); mf.material.opacity = 1 - age / 0.12; } }
    } else if (!sh.hitLoss) {                       // miss / suppression puff
      const a = (age - FLIGHT) / 1.0, f = a < 0.35 ? take(fx.flash) : null, k = close ? 0.15 : 1;
      if (f) { f.position.copy(to); f.scale.setScalar((isMissile ? 0.28 : 0.12) * k); f.material.opacity = 1 - a / 0.35; }
      const sm = take(fx.smoke); if (sm) { sm.position.set(to.x, to.y + (0.05 + a * 0.1) * k, to.z); sm.scale.setScalar((0.12 + a * 0.2) * k); sm.material.opacity = 0.5 * (1 - a); }
    }
  }
  // losses: explosion, smoke column, floating "-n"
  for (const l of lossSchedule) {
    const vI = vOf(l.g), age = v - vI; if (age < 0 || age > 2.6) continue;
    let p = null;
    if (close) { const w = wrecks.filter(x => x.unit === l.unit && Math.abs(x.born - vI) < 0.2).pop(); if (w) p = new THREE.Vector3(w.x - MAP_W / 2, yAt(w.x, w.z) + 0.006, w.z - MAP_H / 2); }
    if (!p) p = targetWorld(l.unit, l.g); if (!p) continue;
    const k = close ? 0.14 : 1;
    const R = rng(l.unit * 7919 + Math.round(l.g));
    if (l.shooter < 0) {                            // no shooter found: artillery-like scatter before the hit
      for (let j = 0; j < 4; j++) { const ak = age + 0.5 - j * 0.12; if (ak < 0 || ak > 0.4) continue; const f = take(fx.flash); if (f) { f.position.set(p.x + (R() - 0.5) * 0.6 * k * 2, p.y, p.z + (R() - 0.5) * 0.6 * k * 2); f.scale.setScalar(0.3 * k); f.material.opacity = 1 - ak / 0.4; } }
    }
    if (age < 0.5) { const f = take(fx.flash); if (f) { f.position.set(p.x, p.y + 0.05 * k, p.z); f.scale.setScalar((0.14 + 0.24 * smooth(age / 0.5) * (1 + Math.min(l.n, 10) * 0.06)) * k); f.material.opacity = 1 - age / 0.5; } }
    for (let j = 0; j < 3; j++) {
      const sm = take(fx.smoke); if (!sm) break;
      const a = clamp((age - j * 0.15) / 2.4, 0, 1);
      sm.position.set(p.x + (R() - 0.5) * 0.08 * k, p.y + (0.05 + a * 0.45) * k, p.z + (R() - 0.5) * 0.08 * k); sm.scale.setScalar((0.1 + a * 0.32) * k); sm.material.opacity = 0.75 * (1 - a) * (a > 0 ? 1 : 0);
    }
    if (age < 2.2) {
      const s = popupSprite(l.n, l.side), a = age / 2.2;
      s.position.set(p.x, p.y + (0.18 + 0.35 * smooth(clamp(a * 1.6, 0, 1))) * (close ? 0.12 : 1), p.z);
      const pop = 1 + 0.35 * Math.max(0, 1 - age / 0.25);
      const img = s.material.map.image, hgt = 0.24 * pop * (close ? 0.1 : 1); s.scale.set(hgt * img.width / img.height, hgt, 1);
      s.material.opacity = a < 0.75 ? 1 : 1 - (a - 0.75) / 0.25;
    }
  }
}

// ---------------------------------------------------------------- trails (where units came from) and order arrows (where they are going)
// ---------------------------------------------------------------- control field, front line, HOI4-style arrows
// Control: every surviving unit spreads influence sqrt(alive) * gaussian(sigma 1.1 km) on a 92x60 grid; blue minus red
// tints the board (ground shader, global view only). Front line: the zero contour of blue - red where both sides are
// present (min(B, R) above a threshold), drawn as a dark core with a blue edge on the blue side and a red edge on the red side.
const CF = { nx: 92, nz: 60 }, ctrlU = { value: 0 };
let ctrlTex = null, ctrlData = null, FIELD = null;
function buildControl() {
  ctrlData = new Uint8Array(CF.nx * CF.nz * 4);
  ctrlTex = new THREE.DataTexture(ctrlData, CF.nx, CF.nz, THREE.RGBAFormat);
  ctrlTex.magFilter = ctrlTex.minFilter = THREE.LinearFilter; ctrlTex.needsUpdate = true;
}
function updateControl() {
  const { nx, nz } = CF, B = new Float32Array(nx * nz), R = new Float32Array(nx * nz), dx = MAP_W / nx, dz = MAP_H / nz, sig = 1.0, rad = 4.0;
  for (const { o, s, alive, dead } of liveUnits) {
    if (o.helo || dead || !alive) continue;
    const w = (o.cls === 'truck' || o.cls === 'jeep' || o.cls === 'gun' ? 0.7 : 1) * (0.85 + 0.15 * Math.min(1, alive / 12)), F = o.side ? R : B;
    const i0 = Math.max(0, Math.floor((s.x - rad) / dx)), i1 = Math.min(nx - 1, Math.ceil((s.x + rad) / dx));
    const j0 = Math.max(0, Math.floor((s.z - rad) / dz)), j1 = Math.min(nz - 1, Math.ceil((s.z + rad) / dz));
    for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) {
      const ddx = (i + 0.5) * dx - s.x, ddz = (j + 0.5) * dz - s.z;
      const val = w * Math.exp(-(ddx * ddx + ddz * ddz) / (2 * sig * sig)); if (val > F[j * nx + i]) F[j * nx + i] = val;   // nearest unit wins (Voronoi-like)
    }
  }
  const own = FOG == null ? null : liveUnits.filter(l => l.o.side === FOG && !l.dead && !l.o.helo).map(l => l.s);
  for (let k = 0; k < nx * nz; k++) {
    const b = B[k], r = R[k], c = (b - r) / (b + r + 0.02);
    let a = smooth(clamp((Math.max(b, r) - 0.03) / 0.35, 0, 1)) * Math.min(1, Math.abs(c) * 2.5);
    if (V8) a = 0;
    let col = V8 ? [18, 18, 18] : c > 0 ? [63, 116, 196] : [192, 69, 59];
    if (own) {                                        // fog view: darken what the viewing side cannot see
      const x = (k % nx + 0.5) * dx, z = (Math.floor(k / nx) + 0.5) * dz;
      let d = 1e9; for (const p of own) d = Math.min(d, Math.hypot(p.x - x, p.z - z));
      const f = smooth(clamp((d - FOG_R + 0.4) / 0.8, 0, 1));
      if (f > 0) { col = col.map(q => Math.round(lerp(q, 18, f))); a = lerp(a, 1, f); }
    }
    ctrlData[k * 4] = col[0]; ctrlData[k * 4 + 1] = col[1]; ctrlData[k * 4 + 2] = col[2]; ctrlData[k * 4 + 3] = Math.round(a * 255);
  }
  ctrlTex.needsUpdate = true;
  FIELD = { B, R, nx, nz, dx, dz };
}
function frontSegments(thr = SHOT.frontThr ?? 0.13, minLen = 1.4, bridge = SHOT.frontBridge ?? 0) {               // marching squares on B - R (grid nodes = cell centres)
  if (!FIELD) return [];
  const { B, R, nx, nz, dx, dz } = FIELD, out = [];
  const f = (i, j) => B[j * nx + i] - R[j * nx + i] + 1e-4, m = (i, j) => Math.min(B[j * nx + i], R[j * nx + i]);
  for (let j = 0; j < nz - 1; j++) for (let i = 0; i < nx - 1; i++) {
    if (Math.max(m(i, j), m(i + 1, j), m(i, j + 1), m(i + 1, j + 1)) < thr) continue;
    const a = f(i, j), b = f(i + 1, j), c = f(i + 1, j + 1), d = f(i, j + 1), P = [];
    const X = ii => (ii + 0.5) * dx, Z = jj => (jj + 0.5) * dz;
    const bb = (ii, jj) => B[jj * nx + ii];
    let ok = true;                                  // both sides must be strong where the contour crosses each edge
    const e = (v1, v2, x1, z1, x2, z2, b1, b2) => { const t = v1 / (v1 - v2); P.push([x1 + (x2 - x1) * t, z1 + (z2 - z1) * t]); if (lerp(b1, b2, t) < thr) ok = false; };
    if ((a > 0) !== (b > 0)) e(a, b, X(i), Z(j), X(i + 1), Z(j), bb(i, j), bb(i + 1, j));
    if ((b > 0) !== (c > 0)) e(b, c, X(i + 1), Z(j), X(i + 1), Z(j + 1), bb(i + 1, j), bb(i + 1, j + 1));
    if ((c > 0) !== (d > 0)) e(c, d, X(i + 1), Z(j + 1), X(i), Z(j + 1), bb(i + 1, j + 1), bb(i, j + 1));
    if ((d > 0) !== (a > 0)) e(d, a, X(i), Z(j + 1), X(i), Z(j), bb(i, j + 1), bb(i, j));
    if (!ok) continue;
    // which side is blue: gradient of B - R
    const gx = (b + c - a - d) / 2, gz = (c + d - a - b) / 2;
    for (let k = 0; k + 1 < P.length; k += 2) out.push([P[k], P[k + 1], gx, gz]);
  }
  // drop short fragments: join segments that share an end point, keep chains at least minLen km long
  const par = out.map((_, k) => k), find = k => { while (par[k] !== k) k = par[k] = par[par[k]]; return k; }, ends = new Map();
  const key = p => Math.round(p[0] * 1000) + ',' + Math.round(p[1] * 1000);
  out.forEach((sg, k) => { for (const p of [sg[0], sg[1]]) { const kk = key(p); if (ends.has(kk)) par[find(k)] = find(ends.get(kk)); else ends.set(kk, k); } });
  const len = new Map(); out.forEach((sg, k) => { const r = find(k); len.set(r, (len.get(r) || 0) + Math.hypot(sg[1][0] - sg[0][0], sg[1][1] - sg[0][1])); });
  const kept = out.filter((_, k) => len.get(find(k)) >= minLen);
  if (!bridge) return kept;
  // bridge gaps: chain ends (points used once) of different chains closer than `bridge` km are joined (nearest first)
  const deg = new Map(), own = new Map();
  out.forEach((sg, k) => { if (len.get(find(k)) < minLen) return; for (const pt of [sg[0], sg[1]]) { const kk = key(pt); deg.set(kk, (deg.get(kk) || 0) + 1); own.set(kk, [pt, find(k), sg[2], sg[3]]); } });
  const endsL = [...deg].filter(([, d]) => d === 1).map(([kk]) => own.get(kk)), used = new Set(), pairs = [];
  for (let a = 0; a < endsL.length; a++) for (let b = a + 1; b < endsL.length; b++) {
    const A1 = endsL[a], B1 = endsL[b]; if (A1[1] === B1[1]) continue;
    const d = Math.hypot(A1[0][0] - B1[0][0], A1[0][1] - B1[0][1]); if (d < bridge) pairs.push([d, a, b]);
  }
  pairs.sort((x, y) => x[0] - y[0]);
  for (const [, a, b] of pairs) { if (used.has(a) || used.has(b)) continue; used.add(a); used.add(b);
    const A1 = endsL[a], B1 = endsL[b]; kept.push([A1[0], B1[0], (A1[2] + B1[2]) / 2, (A1[3] + B1[3]) / 2]); }
  return kept;
}

const ORDER_ARROW = new Set(['Move Deliberate', 'Move Hasty', 'Assault', 'Withdraw']);
const VERB = { 'Move Deliberate': '开进', 'Move Hasty': '急进', Assault: '突击', Withdraw: '撤往' };
// v4 screen tags (projected in the HTML overlay after the camera is placed): arrow labels, engagement links, highlight rings
let TAGS = [];
// v5 order groups: one group = one order of one commander wake (side, wake time, order type, target hex); a group never
// changes until one of its units gets a new order. Arrow labels, step cards and the order list all use these groups.
let ORDER_GROUPS = [];
function buildOrderGroups() {
  const m = new Map();
  for (let i = 0; i < NU; i++) (G.o[i] || []).forEach((r, k) => {
    const name = G.on[r[1]], key = `${G.u[i][1]}:${r[0]}:${name}:${r[2]}`;
    if (!m.has(key)) m.set(key, { key, side: G.u[i][1], t: r[0], name, dest: r[2], ids: [], k: {} });
    const g2 = m.get(key); g2.ids.push(i); g2.k[i] = k;
  });
  const all = [...m.values()].sort((a, b) => a.t - b.t);
  ORDER_GROUPS = all.filter(g2 => ORDER_ARROW.has(g2.name) && g2.dest);
  return all;
}
const ORDER_ZH = { Hold: '就地坚守', Screen: '警戒', 'On Call': '待命射击', 'Counter Battery': '反炮兵', Scan: '侦察', Barrage: '炮击', Resupply: '补给', Scoot: '转移阵地' };
function tiltBlurCss(y, extra = '') {
  if (!SHOT.labelBlur) return extra ? `;filter:${extra}` : '';
  const band = 0.16, d = Math.max(0, Math.abs(y / SHOT.height - CAMF) - band) / (1 - band), t = clamp(d * 1.7, 0, 1);
  const sig = CAMB * 9 * t * t * (3 - 2 * t) * 0.42;
  const parts = [extra, sig >= 0.35 ? `blur(${sig.toFixed(2)}px)` : ''].filter(Boolean);
  return parts.length ? `;filter:${parts.join(' ')}` : '';
}
function chipStrength(ids) {
  let a = 0, t = 0; for (const i of ids) { a += (units[i] && units[i].aliveNow) || 0; t += G.u[i][5]; } return [a, t];
}
const chipKeyOf = (n, ids) => { const [a, t] = chipStrength(ids); return `${n}:${CHIP_H > 20 ? Math.round(40 * a / Math.max(1, t)) : ''}`; };
function chipName(ids) {                            // one unit: its short name; several (the chip shows how many): their common
  if (ids.length === 1) return unitZh(ids[0]);       // formation ("28团1营"), else the strongest one + "等"
  const ns = ids.map(unitZh); let p = ns[0];
  for (const s of ns) { let k = 0; while (k < p.length && k < s.length && p[k] === s[k]) k++; p = p.slice(0, k); }
  const cut = Math.max(...[...'团营连排班队'].map(ch => p.lastIndexOf(ch)));
  if (cut >= 1) return p.slice(0, cut + 1);
  const lead = ids.reduce((a, b) => (G.u[b][5] > G.u[a][5] || (G.u[b][5] === G.u[a][5] && b < a) ? b : a), ids[0]);
  return `${unitZh(lead)}等`;
}
function groupText(g2, ids) {                        // "红 28团1营2连 等 3 个单位 · 突击 (17,16)"; the strongest unit names the group
  const lead = ids.reduce((a, b) => (G.u[b][5] > G.u[a][5] || (G.u[b][5] === G.u[a][5] && b < a) ? b : a), ids[0]), n = ids.length;
  return `${sideZh(lead)}${n > 1 ? ` 等 ${n} 个单位` : ''} · ${VERB[g2.name] ? VERB[g2.name] + ' ' + hexName(g2.dest) : ORDER_ZH[g2.name] || g2.name}`;
}
function resolveSteps(all) {                         // SHOT.steps[].group: { side, t, order, dest } -> the group's units and label
  for (const st of SHOT.steps || []) {
    if (!st.group) continue; const q = st.group;
    const g2 = all.find(x => x.side === q.side && x.t === q.t && x.name === q.order && x.dest === (q.dest || 0));
    if (!g2) { console.warn('step group not found', JSON.stringify(q)); continue; }
    st.units = g2.ids.slice(); st.side = g2.side; st.order = g2.name; st.dest = ORDER_ARROW.has(g2.name) ? g2.dest : null; st.label = groupText(g2, g2.ids); st.title = st.title || st.label; st.g2key = g2.key;
  }
}
// v6 operation axes. Every commander order (group) is drawn from where its units really went while the order stood: each
// member's legs from the order until its next order, cut at the last hex it reached; the group path is the strength-
// weighted mean of its members' paths. Orders the game gave by itself (automatic withdrawals, outside a wake) and groups
// of only small units (recce, HQ, observation posts, trucks) get no arrow. Groups of one side going the same way at the
// same time merge into one axis (HOI4 battle plan); an axis shows from the order until its last unit has arrived, then
// fades. Its label is its role in the commander's plan (SHOT.roles, from the wake's own words; of several directions given
// in one wake the clearly strongest is the main attack), otherwise the order and its target.
let GROUPS6 = new Map(), AXES = [];
const SMALL6 = new Set(['Recce', 'HQ', 'Utility', 'Depot', 'WLR', 'AD', 'Helo', 'Arty', 'SPArty']);
function resamplePath(pts, n = 24) {
  const acc = [0]; for (let k = 1; k < pts.length; k++) acc.push(acc[k - 1] + Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]));
  const L = acc[acc.length - 1], out = [];
  for (let j = 0; j <= n; j++) { const d = L * j / n; let k = 1; while (k < pts.length - 1 && acc[k] < d) k++; const f = pts.length < 2 ? 0 : (d - acc[k - 1]) / ((acc[k] - acc[k - 1]) || 1);
    out.push(pts.length < 2 ? [...pts[0]] : [lerp(pts[k - 1][0], pts[k][0], f), lerp(pts[k - 1][1], pts[k][1], f)]); }
  return out;
}
function chaikin(pts, it = 2) { let P = pts; for (let r = 0; r < it; r++) { const Q = [P[0]]; for (let k = 0; k + 1 < P.length; k++) { const a = P[k], b = P[k + 1]; Q.push([0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]], [0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]]); } Q.push(P[P.length - 1]); P = Q; } return P; }
const roleOf = r => ((SHOT.roles || []).find(q => q.side === r.side && q.t === r.t && q.dest === r.dest) || {}).role || null;
function wakeOf(side, t) { const W = ((SHOT.wakes || {})[side]) || []; let k = -1; W.forEach((w, j) => { if (t >= w && t <= w + 240) k = j; }); return SHOT.wakes ? k : 0; }
let GIVEN = new Set();
function buildAxes() {
  GROUPS6 = new Map(); AXES = []; GIVEN = new Set((SHOT.given || []).map(q => q.join(':')));
  for (const g2 of ORDER_GROUPS) {
    const mem = [];
    for (const i of g2.ids) {
      const os = G.o[i], k = g2.k[i], t0 = os[k][0], t1 = k + 1 < os.length ? os[k + 1][0] : Infinity, P = PLAN[i]; if (!P) continue;
      const legs = P.legs.filter(L => L.tB > t0 && L.tB <= t1); if (!legs.length) continue;
      const hexes = [legs[0].hA]; for (const L of legs) { if (L.mid) hexes.push(L.mid); hexes.push(L.hB); }
      mem.push({ i, pts: hexes.map(h => anchorXZ(h, g2.side)), vS: legs[0].vS, vE: legs[legs.length - 1].vE, subs: G.u[i][5], small: SMALL6.has(G.u[i][2]) });
    }
    if (!mem.length) continue;
    const R2 = mem.map(m => resamplePath(m.pts)), ws = mem.reduce((a, m) => a + m.subs, 0) || 1;
    const pts = R2[0].map((_, j) => [R2.reduce((a, r, q) => a + r[j][0] * mem[q].subs, 0) / ws, R2.reduce((a, r, q) => a + r[j][1] * mem[q].subs, 0) / ws]);
    const n = pts.length - 1;
    GROUPS6.set(g2.key, { key: g2.key, side: g2.side, t: g2.t, name: g2.name, dest: g2.dest, ids: mem.map(m => m.i), subs: ws, pts, v0: vOf(g2.t),
      vE: Math.max(...mem.map(m => m.vE)), eligible: (SHOT.given ? GIVEN.has(g2.side + ':' + g2.t + ':' + g2.dest) : wakeOf(g2.side, g2.t) >= 0) && mem.some(m => !m.small) && ws >= (SHOT.axisMinSubs ?? 6),
      ang: Math.atan2(pts[n][0] - pts[0][0], pts[n][1] - pts[0][1]), len: Math.hypot(pts[n][0] - pts[0][0], pts[n][1] - pts[0][1]) });
  }
  const end = p => p[p.length - 1];
  for (const r of [...GROUPS6.values()].filter(r => r.eligible && r.len > 0.3).sort((a, b) => b.subs - a.subs)) {
    let A = AXES.find(A => A.side === r.side && (A.name === 'Withdraw') === (r.name === 'Withdraw') && r.v0 <= A.vE + 0.5 && A.v0 <= r.vE + 0.5
      && Math.abs(wrap(A.ang - r.ang)) < 0.8 && Math.hypot(A.pts[0][0] - r.pts[0][0], A.pts[0][1] - r.pts[0][1]) < 3.0 && Math.hypot(end(A.pts)[0] - end(r.pts)[0], end(A.pts)[1] - end(r.pts)[1]) < 1.5
      && A.groups.some(q => { const [a, b] = hexXZ(q.dest), [c, d] = hexXZ(r.dest); return Math.hypot(a - c, b - d) < 1.2; }));
    if (!A) { A = { side: r.side, name: r.name, ang: r.ang, groups: [] }; AXES.push(A); }
    A.groups.push(r);
    const ws = A.groups.reduce((a, q) => a + q.subs, 0);
    A.subs = ws; A.v0 = Math.min(...A.groups.map(q => q.v0)); A.vE = Math.max(...A.groups.map(q => q.vE));
    A.pts = A.groups[0].pts.map((_, j) => [A.groups.reduce((a, q) => a + q.pts[j][0] * q.subs, 0) / ws, A.groups.reduce((a, q) => a + q.pts[j][1] * q.subs, 0) / ws]);
    A.ids = [...new Set(A.groups.flatMap(q => q.ids))];
  }
  const byWake = new Map();
  for (const A of AXES) {
    A.role = A.groups.map(roleOf).find(Boolean) || null;
    A.dests = [...new Set(A.groups.map(q => q.dest))].slice(0, 2);
    A.draw = chaikin(A.pts);
    if (A.name === 'Assault') { const k = A.side + ':' + A.groups[0].t; if (!byWake.has(k)) byWake.set(k, []); byWake.get(k).push(A); }
  }
  for (const L of byWake.values()) { if (L.length < 2) continue; L.sort((a, b) => b.subs - a.subs); if (!L[0].role && L[0].subs > 1.15 * L[1].subs) L[0].role = '主攻'; }
  for (const A of AXES) A.label = `${A.role || VERB[A.name] || ''} ${A.dests.map(hexName).join('·')}`;
  return { groups: GROUPS6.size, axes: AXES.map(A => ({ side: A.side, t: A.groups[0].t, label: A.label, subs: A.subs, groups: A.groups.length, v: [+A.v0.toFixed(1), +A.vE.toFixed(1)] })) };
}
const stepAt = v => (SHOT.steps || []).find(st => v >= st.from && v <= st.to) || null;
const stepSeq = v => { const S = SHOT.steps || []; if (!S.length) return null; const seq = S.filter(st => (st.seq ?? 0) === ((S.find(x => v >= x.from - 0.01 && v <= x.to + 0.01) || {}).seq ?? -1)); return seq.length && v >= seq[0].from && v <= seq[seq.length - 1].to ? seq : null; };
// where a unit stands on screen this frame (merged members: their stack), map km
function unitMapPos(i, g) {
  const o = units[i], L = o && o.mergedInto ? o.mergedInto : o;
  if (L && L.bx != null && L.root.visible) return [L.bx + (L.off ? L.off.x : 0), L.bz + (L.off ? L.off.z : 0)];
  const s = unitState(i, g); return s && !isNaN(s.x) ? [s.x, s.z] : null;
}
function updateOverlayLines(v, g) {
  const P = [], C = [], I = [], PT = [], CT = [], UT = [], IT = [];
  const vtxT = (x, z, rgb, a, u, w, l) => { PT.push(x - MAP_W / 2, yAt(x, z) + l, z - MAP_H / 2); CT.push(rgb[0], rgb[1], rgb[2], a); UT.push(u, w); return PT.length / 3 - 1; };
  const TR = SHOT.trail ?? 1800, close = MODELS >= 0.5, Z = Math.sqrt(ZOOM), lift = 0.016 * (close ? 0.25 : Z);
  const vtx = (x, z, rgb, a, l = lift) => { P.push(x - MAP_W / 2, yAt(x, z) + l, z - MAP_H / 2); C.push(rgb[0], rgb[1], rgb[2], a); return P.length / 3 - 1; };
  const ribbon = (pts, wdt, rgb, alphaAt, l = lift) => {   // pts [[x, z], ...] map km; draped on the terrain
    const dense = [];
    for (let k = 0; k + 1 < pts.length; k++) {
      const [x1, z1] = pts[k], [x2, z2] = pts[k + 1], L = Math.hypot(x2 - x1, z2 - z1), n = Math.max(1, Math.ceil(L / 0.06));
      for (let j = 0; j < n; j++) dense.push([lerp(x1, x2, j / n), lerp(z1, z2, j / n), (k + j / n) / (pts.length - 1)]);
    }
    if (pts.length) dense.push([...pts[pts.length - 1], 1]);
    if (dense.length < 2) return;
    for (let k = 0; k < dense.length; k++) {
      const [x, z, f] = dense[k], [xa, za] = dense[Math.max(0, k - 1)], [xb, zb] = dense[Math.min(dense.length - 1, k + 1)];
      let dx = xb - xa, dz = zb - za; const L = Math.hypot(dx, dz) || 1; dx /= L; dz /= L;
      const w = typeof wdt === 'function' ? wdt(f) : wdt, nx = -dz * w / 2, nz = dx * w / 2, a = alphaAt(f, k);
      const i0 = vtx(x - nx, z - nz, rgb, a, l), i1 = vtx(x + nx, z + nz, rgb, a, l);
      if (k) I.push(i0 - 2, i0 - 1, i0, i0 - 1, i1, i0);
    }
  };
  // tapered arrow along a polyline: wide tail -> narrow neck, then a head; drawn twice (dark outline, then fill)
  const curve = (a, b, bend) => {                  // quadratic bezier a -> b bulging by `bend` (fraction of the length)
    const mx = (a[0] + b[0]) / 2, mz = (a[1] + b[1]) / 2, L = Math.hypot(b[0] - a[0], b[1] - a[1]), nx = -(b[1] - a[1]) / L, nz = (b[0] - a[0]) / L;
    const c = [mx + nx * L * bend, mz + nz * L * bend], out = [];
    for (let k = 0; k <= 24; k++) { const t = k / 24; out.push([(1 - t) ** 2 * a[0] + 2 * (1 - t) * t * c[0] + t * t * b[0], (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * c[1] + t * t * b[1]]); }
    return out;
  };
  const taper = (pts, o) => {                      // o: {w0, w1, hw, hl, fill, edge, a, grow}
    let L = 0; const acc = [0]; for (let k = 1; k < pts.length; k++) { L += Math.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]); acc.push(L); }
    const Lg = L * (o.grow ?? 1); if (Lg < o.hl * 0.6) return;
    const at = d => { let k = 1; while (k < acc.length - 1 && acc[k] < d) k++; const f = (d - acc[k - 1]) / (acc[k] - acc[k - 1] || 1); return [lerp(pts[k - 1][0], pts[k][0], f), lerp(pts[k - 1][1], pts[k][1], f)]; };
    const bodyEnd = Lg - o.hl, body = []; for (let d = 0; d <= bodyEnd; d += Math.max(0.05, bodyEnd / 30)) body.push(at(d)); body.push(at(Math.max(0, bodyEnd)));
    const tip = at(Lg), neck = at(Math.max(0, bodyEnd)), dir = [tip[0] - neck[0], tip[1] - neck[1]], dl = Math.hypot(dir[0], dir[1]) || 1, ux = dir[0] / dl, uz = dir[1] / dl;
    if (ARROW && o.tex) {                          // textured: body strip samples the texture's body profile, head = textured quad
      const A2 = ARROW, l = lift * 1.12, rgb = A2.fillLin && o.side != null ? A2.fillLin[o.side] : o.fill, H = o.hw, hl = H * A2.headAspect;
      const bEnd = Math.max(0, Lg - hl * (1 - A2.overlap)), pts2 = []; for (let d = 0; d <= bEnd; d += Math.max(0.04, bEnd / 40)) pts2.push(at(d)); pts2.push(at(bEnd));
      for (let k = 0; k < pts2.length; k++) {
        const [x, z] = pts2[k], [xa, za] = pts2[Math.max(0, k - 1)], [xb, zb] = pts2[Math.min(pts2.length - 1, k + 1)];
        let dx = xb - xa, dz = zb - za; const L2 = Math.hypot(dx, dz) || 1; dx /= L2; dz /= L2;
        const w = lerp(o.w0, H * A2.bodyRatio, k / Math.max(1, pts2.length - 1)), nx = -dz * w / 2, nz = dx * w / 2;
        const i0 = vtxT(x - nx, z - nz, rgb, o.a, A2.bodyU, A2.bodyV[0], l), i1 = vtxT(x + nx, z + nz, rgb, o.a, A2.bodyU, A2.bodyV[1], l);
        if (k) IT.push(i0 - 2, i0 - 1, i0, i0 - 1, i1, i0);
      }
      const hb = at(Math.max(0, Lg - hl)), hx = -uz * H / 2, hz = ux * H / 2;
      const q0 = vtxT(hb[0] - hx, hb[1] - hz, rgb, o.a, A2.headU[0], A2.headV[0], l * 1.01), q1 = vtxT(hb[0] + hx, hb[1] + hz, rgb, o.a, A2.headU[0], A2.headV[1], l * 1.01);
      const q2 = vtxT(tip[0] - hx, tip[1] - hz, rgb, o.a, A2.headU[1], A2.headV[0], l * 1.01), q3 = vtxT(tip[0] + hx, tip[1] + hz, rgb, o.a, A2.headU[1], A2.headV[1], l * 1.01);
      IT.push(q0, q1, q2, q1, q3, q2);
      return;
    }
    for (const [rgb, grow, a, l] of [[o.edge, o.ol, o.a * 0.9, lift * 1.05], [o.fill, 0, o.a, lift * 1.12]]) {
      ribbon(body, f => lerp(o.w0, o.w1, f) + 2 * grow, rgb, () => a, l);
      const hw = o.hw / 2 + grow * 1.6, i0 = vtx(tip[0] + ux * grow * 1.8, tip[1] + uz * grow * 1.8, rgb, a, l);
      const i1 = vtx(neck[0] - uz * hw - ux * grow, neck[1] + ux * hw - uz * grow, rgb, a, l), i2 = vtx(neck[0] + uz * hw - ux * grow, neck[1] - ux * hw - uz * grow, rgb, a, l);
      I.push(i0, i1, i2);
    }
  };
  const BL = [0.3, 0.55, 1.0], RD = [1.0, 0.25, 0.18], DARK = [0.08, 0.06, 0.04];
  if (!close) {
    // front line
    const segs = (SHOT.quiet || []).some(q => v >= q[0] && v <= q[1]) ? [] : frontSegments(), wc = 0.03 * Z;   // no front in quiet windows either
    for (const [p, q, gx, gz] of segs) {
      let dx = q[0] - p[0], dz = q[1] - p[1]; const L = Math.hypot(dx, dz) || 1; dx /= L; dz /= L;
      let nx = -dz, nz = dx; if (nx * gx + nz * gz < 0) { nx = -nx; nz = -nz; }   // n points to blue
      const ext = wc * 0.6, a = [p[0] - dx * ext, p[1] - dz * ext], b = [q[0] + dx * ext, q[1] + dz * ext];
      const quad = (off, w, rgb, al, l) => { const i0 = vtx(a[0] + nx * (off - w / 2), a[1] + nz * (off - w / 2), rgb, al, l), i1 = vtx(a[0] + nx * (off + w / 2), a[1] + nz * (off + w / 2), rgb, al, l), i2 = vtx(b[0] + nx * (off - w / 2), b[1] + nz * (off - w / 2), rgb, al, l), i3 = vtx(b[0] + nx * (off + w / 2), b[1] + nz * (off + w / 2), rgb, al, l); I.push(i0, i1, i2, i1, i3, i2); };
      quad(wc * 0.85, wc * 0.7, [0.35, 0.6, 1.0], 0.9, lift * 0.9); quad(-wc * 0.85, wc * 0.7, [1.0, 0.33, 0.25], 0.9, lift * 0.9);
      quad(0, wc, DARK, 0.92, lift * 0.95);
    }
    // key-moment push arrows (HOI4 offensive arrows): from the attackers' starting area to the target
    for (const mm of V4 ? [] : SHOT.moments || []) {
      const v0 = vOf(mm.show ?? mm.from), v1 = vOf(mm.to + (mm.hold ?? 600)); if (v < v0 || v > v1 + 0.8) continue;
      if (!mm._arrow) {
        const tg = (mm.targets || [mm.target]).map(hexXZ), to = [tg.reduce((s, p) => s + p[0], 0) / tg.length, tg.reduce((s, p) => s + p[1], 0) / tg.length];
        let sx = 0, sz = 0, n = 0; for (const id of mm.att || []) { const st = unitState(id, mm.from); if (st && !isNaN(st.x)) { sx += st.x; sz += st.z; n++; } }
        let from = n ? [sx / n, sz / n] : [to[0] - 2, to[1] - 2];
        let L = Math.hypot(from[0] - to[0], from[1] - to[1]);
        if (L < 1.6) { const ux = (from[0] - to[0]) / (L || 1), uz = (from[1] - to[1]) / (L || 1); from = [to[0] + (L ? ux : -0.7) * 1.8, to[1] + (L ? uz : -0.7) * 1.8]; L = 1.8; }
        if (L > 4.5) from = [to[0] + (from[0] - to[0]) / L * 4.5, to[1] + (from[1] - to[1]) / L * 4.5];
        mm._arrow = curve(from, to, 0.12 * (mm.n % 2 ? 1 : -1));
      }
      const grow = smooth(clamp((v - v0) / 1.8, 0, 1)), a = clamp((v1 + 0.8 - v) / 0.8, 0, 1) * 0.92;
      const side = mm.attacker, fill = side === 0 ? [0.36, 0.62, 1.0] : [1.0, 0.36, 0.27];
      taper(mm._arrow, { w0: 0.36 * Z, w1: 0.16 * Z, hw: 0.54 * Z, hl: 0.46 * Z, fill, edge: DARK, ol: 0.035 * Z, a, grow, tex: true, side });
    }
  }
  for (const { o, s, dead } of liveUnits) {
    if (o.helo || dead || close || o.mergedInto || V6) continue;
    const rows = G.p[o.i], rgb = o.side === 0 ? BL : RD;
    // trail: hex centres visited in the last TR game seconds, then the current position
    const pts = []; let k = rowAt(rows, g - TR);
    if (k >= 0 && rows[k][1]) { const [x, z] = hexXZ(rows[k][1]); pts.push([x, z, g - TR]); }
    for (let j = Math.max(0, k + 1); j < rows.length && rows[j][0] <= g; j++) if (rows[j][1] && rows[j][3] === 1) { const [x, z] = hexXZ(rows[j][1]); const last = pts[pts.length - 1]; if (!last || last[0] !== x || last[1] !== z) pts.push([x, z, rows[j][0]]); }
    pts.push([s.x, s.z, g]);
    if (pts.length >= 2 && !(pts.length === 2 && Math.hypot(pts[0][0] - pts[1][0], pts[0][1] - pts[1][1]) < 0.05)) {
      const t0 = pts[0][2], ages = pts.map(p => p[2]);
      ribbon(pts.map(p => [p[0], p[1]]), 0.022 * Z, rgb, f => {
        const idx = f * (ages.length - 1), j = Math.min(ages.length - 2, Math.floor(idx)), tt = lerp(ages[j], ages[j + 1], idx - j);
        return 0.4 * clamp(1 - (g - tt) / TR, 0, 1) * clamp((g - t0) / 60, 0.2, 1);
      });
    }
    // order arrow: thick tapered arrow towards the order's destination hex (move / assault / withdraw), dark outline
    const os = G.o[o.i] || [], oi = V4 ? -1 : rowAt(os, g);
    if (oi >= 0) {
      const [t, on, dest] = os[oi], name = G.on[on];
      if (ORDER_ARROW.has(name) && dest && dest !== s.hex) {
        const [dx, dz] = hexXZ(dest), L = Math.hypot(dx - s.x, dz - s.z);
        if (L > 0.35 && L < 9) {
          const fadeIn = clamp((g - t) / 90, 0, 1), assault = name === 'Assault';
          const ux = (dx - s.x) / L, uz = (dz - s.z) / L, st = [s.x + ux * 0.22, s.z + uz * 0.22], en = [dx - ux * 0.1, dz - uz * 0.1];
          const fill = assault ? (o.side === 0 ? [0.55, 0.78, 1.0] : [1.0, 0.5, 0.36]) : (o.side === 0 ? [0.42, 0.62, 0.95] : [0.95, 0.42, 0.34]);
          taper(curve(st, en, 0.06), { w0: (assault ? 0.1 : 0.065) * Z, w1: (assault ? 0.055 : 0.04) * Z, hw: (assault ? 0.2 : 0.14) * Z, hl: (assault ? 0.19 : 0.14) * Z,
            fill, edge: DARK, ol: 0.016 * Z, a: (assault ? 0.92 : 0.62) * fadeIn, grow: 1 });
        }
      }
    }
  }
  // SHOT.quiet: video-second windows [[from, to], ...] with no order arrows, arrow labels, engagement links or front line (counters,
  // objective stars and their labels stay): the programme opening's sand-table panoramas
  if (V4 && (SHOT.quiet || []).some(q => v >= q[0] && v <= q[1])) TAGS = [];
  else if (V4) {
    TAGS = [];
    const seq = stepSeq(v), cur = stepAt(v);
    // grouped order arrows: units of one side with an active move / assault order to the same hex share one arrow;
    // its tail sits at their centre and thin lines tie every unit to it; the label names the units and the order
    const groups = new Map();
    const pushGroup = (key, side, dest, name, ids, t, a0, label) => { if (!groups.has(key)) groups.set(key, { side, dest, name, ids: [], t, a0, label }); const G2 = groups.get(key); for (const i of ids) if (!G2.ids.includes(i)) G2.ids.push(i); G2.t = Math.max(G2.t, t); };
    if (seq && !V6) {                                 // order-by-order mode: only the steps shown so far
      for (const st of seq) { if (v < st.from) continue; const k = 'st' + st.from;
        if (!st.dest) { if (st === cur) for (const i of st.units) { const pp = unitMapPos(i, g); if (pp) TAGS.push({ k: 'ring', p: W(pp[0], pp[1], 0.02), side: st.side }); } continue; } pushGroup(k, st.side, st.dest, st.order || 'Assault', st.units, 0, st === cur ? 1 : 0.5, st.label); groups.get(k).grow = smooth(clamp((v - st.from) / 1.2, 0, 1)); groups.get(k).cur = st === cur; }
    } else if (!close && V5 && !V6) {                 // v5: one arrow per commander order (wake, order type, target), fixed
      const liveSet = new Set(liveUnits.filter(l => !l.dead).map(l => l.o.i));
      for (const g2 of ORDER_GROUPS) {
        if (g2.t > g) break;
        const cnt = [], act = [];
        for (const i of g2.ids) { if (!liveSet.has(i) || rowAt(G.o[i] || [], g) !== g2.k[i]) continue; cnt.push(i); const P = units[i].P; if (P && !units[i].helo && (P.hex !== g2.dest || P.moving)) act.push(i); }
        if (!act.length) continue;
        pushGroup(g2.key, g2.side, g2.dest, g2.name, act, g2.t, 1, groupText(g2, cnt));
      }
    } else if (!close) {
      for (const { o, s, dead } of liveUnits) {
        if (o.helo || dead) continue;
        const os = G.o[o.i] || [], oi = rowAt(os, g); if (oi < 0) continue;
        const [t, on, dest] = os[oi], name = G.on[on];
        if (!ORDER_ARROW.has(name) || !dest || dest === s.hex) continue;
        const [dx, dz] = hexXZ(dest), L = Math.hypot(dx - s.x, dz - s.z); if (L < 0.35 || L > 9) continue;
        pushGroup(o.side + ':' + dest + ':' + (name === 'Assault' ? 'a' : name === 'Withdraw' ? 'w' : 'm'), o.side, dest, name, [o.i], t, 1);
      }
    }
    const labels = [];
    if (V6) {                                         // operation axes / step groups drawn along the real paths
      const draws = [];
      if (seq) { for (const st of seq) { if (v < st.from) continue; const r = GROUPS6.get(st.g2key);
          if (!r || !st.dest) { if (st === cur) for (const i of st.units) { const pp = unitMapPos(i, g); if (pp) TAGS.push({ k: 'ring', p: W(pp[0], pp[1], 0.02), side: st.side }); } continue; }
          const ax = AXES.find(A => A.groups.includes(r));
          draws.push({ side: r.side, pts: chaikin(r.pts), subs: r.subs, a: st === cur ? 1 : 0.55, grow: smooth(clamp((v - st.from) / 1.2, 0, 1)), cur: st === cur,
            label: `${roleOf(r) || (ax && ax.groups[0] === r && ax.role) || VERB[r.name] || ''} ${hexName(r.dest)}`, ids: r.ids });
          st.title6 = draws[draws.length - 1].label; } }
      else if (!close) {
        for (const sd of [0, 1]) {
          const on = AXES.filter(A => A.side === sd && v >= A.v0 && v <= A.vE + 1.4).sort((a, b) => b.subs - a.subs).slice(0, SHOT.axesMax ?? 3);
          for (const A of on) draws.push({ side: sd, pts: A.draw, subs: A.subs, a: 0.92 * clamp(1 - (v - A.vE - 0.6) / 0.8, 0, 1), grow: smooth(clamp((v - A.v0) / 1.0, 0, 1)), label: A.label, ids: A.ids });
        }
      }
      const sc = Z;
      for (const D of draws) {
        if (D.a <= 0.01) continue;
        const P2 = D.pts, n = P2.length - 1, u0 = [P2[1][0] - P2[0][0], P2[1][1] - P2[0][1]], l0 = Math.hypot(u0[0], u0[1]) || 1;
        const pts = [[P2[0][0] + u0[0] / l0 * 0.08, P2[0][1] + u0[1] / l0 * 0.08], ...P2.slice(1)];
        const w = (0.12 + 0.05 * clamp(D.subs / 40, 0, 2)) * (seq ? 1.25 : 1);
        taper(pts, { w0: w * 0.9 * sc, w1: w * 0.45 * sc, hw: w * 1.55 * sc, hl: w * 1.3 * sc, fill: D.side ? [1.0, 0.36, 0.27] : [0.36, 0.62, 1.0], edge: DARK, ol: 0.02 * sc, a: D.a, grow: D.grow, tex: true, side: D.side });
        const cands = [0.5, 0.4, 0.6, 0.3, 0.7, 0.22, 0.8].map(f => { const q = P2[Math.round(f * n)]; return W(q[0], q[1], 0.03 * sc); });
        labels.push({ k: 'label', p: cands[0], cands, text: D.label, side: D.side, n: D.subs, L: 0, a: D.a, cur: !!D.cur });
        if (D.cur) for (const i of D.ids) { const pp = unitMapPos(i, g); if (pp) TAGS.push({ k: 'ring', p: W(pp[0], pp[1], 0.02), side: D.side }); }
      }
      groups.clear();
    }
    for (const G2 of groups.values()) {
      const pts = G2.ids.map(i => unitMapPos(i, g)).filter(Boolean); if (!pts.length) continue;
      const tail = [pts.reduce((a, p) => a + p[0], 0) / pts.length, pts.reduce((a, p) => a + p[1], 0) / pts.length];
      const [dx, dz] = hexXZ(G2.dest), L = Math.hypot(dx - tail[0], dz - tail[1]); if (L < 0.3) continue;
      const ux = (dx - tail[0]) / L, uz = (dz - tail[1]) / L, n = G2.ids.length, assault = G2.name === 'Assault', withdraw = G2.name === 'Withdraw';
      const st = [tail[0] + ux * 0.16 * Z, tail[1] + uz * 0.16 * Z], en = [dx - ux * 0.12, dz - uz * 0.12];
      const fade = seq ? G2.a0 : clamp((g - G2.t) / 90, 0.25, 1) * (assault ? 0.95 : withdraw ? 0.6 : 0.78);
      const w = (seq ? 0.3 : assault ? 0.2 : 0.14) + 0.025 * Math.min(n - 1, 4), arrowPts = curve(st, en, 0.08 * (G2.side ? 1 : -1));
      const sc = close ? 0.12 : Z;
      taper(arrowPts, { w0: w * 0.9 * sc, w1: w * 0.45 * sc, hw: w * 1.5 * sc, hl: w * 1.3 * sc, fill: G2.side ? [1.0, 0.36, 0.27] : [0.36, 0.62, 1.0], edge: DARK, ol: 0.02 * sc, a: fade, grow: G2.grow ?? 1, tex: true, side: G2.side });
      const rgb = G2.side ? RD : BL;
      if (n > 1 || seq) for (const p of pts) if (Math.hypot(p[0] - tail[0], p[1] - tail[1]) > 0.08) ribbon([p, tail], 0.016 * sc, rgb, () => 0.85 * fade, lift * 1.2);
      // label: names + order; one unit = its name, several = the strongest one "等 n 个单位"
      const lead = G2.ids.reduce((a, b) => (G.u[b][5] > G.u[a][5] ? b : a), G2.ids[0]);
      const text = G2.label || `${sideZh(lead)}${n > 1 ? ` 等 ${n} 个单位` : ''} · ${VERB[G2.name] || '前往'} ${hexName(G2.dest)}`;
      const mid = arrowPts[Math.round(arrowPts.length * 0.45)];
      if (!V5 || seq || G2.ids.some(i => FOCUS.has(i)) || !FOCUS.size) labels.push({ k: 'label', p: W(mid[0], mid[1], 0.03 * sc), text, side: G2.side, n, L, a: fade, cur: !!G2.cur });
      if (G2.cur) for (const i of G2.ids) { const pp = unitMapPos(i, g); if (pp) TAGS.push({ k: 'ring', p: W(pp[0], pp[1], 0.02), side: G2.side }); }
    }
    labels.sort((a, b) => (b.cur - a.cur) || (b.n - a.n) || (b.L - a.L));
    TAGS.push(...labels.slice(0, seq ? 8 : 6));
    // engagements: pairs exchanging fire right now (direct fire with a unit target), linked on screen with the combat icon
    if (!seq) {
      const pairs = new Map();
      for (const sh of shotSchedule) {
        if (sh.target < 0 || BARRAGE(sh.kind)) continue; const dv = v - sh.vL; if (dv < -1.5 || dv > 2.5) continue;
        const ra = units[sh.from].mergedInto || units[sh.from], rb = units[sh.target].mergedInto || units[sh.target];
        const key = Math.min(ra.i, rb.i) + '-' + Math.max(ra.i, rb.i); if (!pairs.has(key)) pairs.set(key, [ra.i, rb.i, sh.side]);
      }
      for (const [a, b] of pairs.values()) {
        const pa = centerWorld.get(a), pb = centerWorld.get(b); if (!pa || !pb) continue;
        if (pa.distanceTo(pb) > (close ? 1.2 : 6) || pa.distanceTo(pb) < 1e-3) continue;
        TAGS.push({ k: 'engage', a: pa.clone(), b: pb.clone(), sa: G.u[a][1] });
      }
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(P, 3)); geo.setAttribute('color', new THREE.Float32BufferAttribute(C, 4)); geo.setIndex(I);
  if (ovlMesh.geometry) ovlMesh.geometry.dispose();
  ovlMesh.geometry = geo;
  if (arrowMesh) {
    const g2 = new THREE.BufferGeometry();
    g2.setAttribute('position', new THREE.Float32BufferAttribute(PT, 3)); g2.setAttribute('color', new THREE.Float32BufferAttribute(CT, 4)); g2.setAttribute('uv', new THREE.Float32BufferAttribute(UT, 2)); g2.setIndex(IT);
    if (arrowMesh.geometry) arrowMesh.geometry.dispose();
    arrowMesh.geometry = g2;
  }
}

function updateVP(v, g, cam) {
  if (V5) return updateVPV5(v, g);
  for (const o of vpObjs) {
    const own = vpOwner(o.h, o.owner0, g);
    const closeUp = MODELS >= 0.5;
    for (const k of [0, 1, 2]) { o.ribbon[k].visible = own === k && !closeUp; o.fill[k].visible = own === k && !closeUp; }
    o.disc.material.color.set(own === 0 ? BLUE : own === 1 ? RED : '#8a857a');
    const z = ZOOM * (closeUp ? 0.07 : 1), b = o.base;
    o.disc.scale.set(z, closeUp ? 0.1 : 1, z); o.disc.position.y = b.y + (closeUp ? 0.002 : 0.02); o.pole.scale.set(1, z, 1); o.pole.position.y = b.y + 0.13 * z;
    o.star.scale.setScalar(z); o.star.position.y = b.y + 0.32 * z; o.label.position.y = b.y + 0.45 * z;
    const im = o.label.material.map.image, lz = closeUp ? 0.12 : z; o.label.scale.set(0.13 * lz * im.width / im.height, 0.13 * lz, 1);
    o.label.position.y = b.y + 0.45 * z;
    o.star.rotation.y = Math.atan2(cam.position.x - o.star.position.x, cam.position.z - o.star.position.z);
    // capture ring: expands for 3 s of video after an owner change
    o.ring.visible = false;
    for (const [t, hh, ow] of G.vc || []) {
      if (hh !== o.h) continue; const age = v - vOf(t);
      if (age >= 0 && age < 3) { o.ring.visible = true; const r = 0.25 + age * 0.35; o.ring.scale.setScalar(r); o.ring.material.color.set(ow === 0 ? '#8fc0ff' : ow === 1 ? '#ff8a76' : '#ffffff'); o.ring.material.opacity = 1 - age / 3; }
    }
  }
}

function updateLabels(v, g) {
  for (const L of labelObjs) {
    const d = L.def, vis = v >= d.from && v <= d.to;
    let a = clamp(Math.min((v - d.from) / 0.6, (d.to - v) / 0.6), 0, 1);
    if (d.unit != null) {
      const s = unitState(d.unit, g);
      if (!s || s.st !== 1 || isNaN(s.x)) { L.sp.visible = false; continue; }
      const p = pieceWorld.get(d.unit) || W(s.x, s.z, 0.5); L.sp.position.set(p.x, p.y + 0.12 * ZOOM, p.z);
    }
    if (!L.base) L.base = L.sp.scale.clone();
    const zs = Math.pow(ZOOM, 0.8) * (MODELS >= 0.5 ? 0.25 : 1); L.sp.scale.set(L.base.x * zs, L.base.y * zs, 1);
    L.sp.visible = vis && a > 0; L.sp.material.opacity = a;
  }
}

// ---------------------------------------------------------------- camera
function resolveAt(at, g) {
  if (typeof at === 'number') return hexXZ(at);
  if (Array.isArray(at)) return at;
  const [bx, bz] = hexXZ(at.base); let sx = 0, sz = 0, n = 0;
  for (const id of at.follow) for (let k = -3; k <= 3; k++) { const s = unitState(id, g + k * 40); if (s && !isNaN(s.x)) { sx += s.x; sz += s.z; n++; } }
  if (!n) return [bx, bz];
  return [lerp(bx, sx / n, at.w), lerp(bz, sz / n, at.w)];
}
function cameraAt(v, g) {
  const K = SHOT.camera; let i = 0; while (i < K.length - 2 && v >= K[i + 1].t) i++;
  const a = K[i], b = K[i + 1], f0 = b.t > a.t ? clamp((v - a.t) / (b.t - a.t), 0, 1) : 1, f = a.ease === 'linear' ? f0 : smooth(f0);
  const pa = resolveAt(a.at, g), pb = resolveAt(b.at, g);
  const P = (k, d) => lerp(a[k] ?? d, b[k] ?? d, f);
  return { x: lerp(pa[0], pb[0], f), z: lerp(pa[1], pb[1], f), dist: P('dist'), pitch: P('pitch'), yaw: P('yaw'), focus: P('focus', 0.55), blur: P('blur', 0.8),
    fov: P('fov', 30), models: f < 0.5 ? (a.models ?? 0) : (b.models ?? 0), sky: P('sky', 0), lift: P('lift', 0) };
}
function blocked(from, to) {                      // terrain above the sight line (first 90 % of it)?
  for (let k = 1; k < 28; k++) { const f = k / 30, x = lerp(from.x, to.x, f), z = lerp(from.z, to.z, f), y = lerp(from.y, to.y, f); if (yAt(x + MAP_W / 2, z + MAP_H / 2) + 0.004 > y) return true; }
  return false;
}
function placeCamera(c) {
  const tgt = W(c.x, c.z, 0); tgt.y = yAt(c.x, c.z) + c.lift;
  const p = c.pitch * Math.PI / 180, y = c.yaw * Math.PI / 180;
  camera.position.set(tgt.x + c.dist * Math.cos(p) * Math.sin(y), tgt.y + c.dist * Math.sin(p), tgt.z + c.dist * Math.cos(p) * Math.cos(y));
  for (let tries = 0, pp = p; tries < 30 && blocked(camera.position, tgt); tries++) { pp += 1.5 * Math.PI / 180; camera.position.set(tgt.x + c.dist * Math.cos(pp) * Math.sin(y), tgt.y + c.dist * Math.sin(pp), tgt.z + c.dist * Math.cos(pp) * Math.cos(y)); }
  const ground = yAt(camera.position.x + MAP_W / 2, camera.position.z + MAP_H / 2) + 0.006;
  if (camera.position.y < ground) camera.position.y = ground;
  camera.fov = c.fov; camera.near = clamp(c.dist * 0.02, 0.002, 0.05); camera.updateProjectionMatrix();
  camera.lookAt(tgt); camTarget.copy(tgt);
  sightA.value.copy(camera.position); sightB.value.copy(tgt); sightR.value = MODELS >= 0.5 ? (SHOT.clearR ?? 0) : 0;
  // shadows: tight frustum around the target in close-ups, the whole board otherwise
  const half = lerp(15, 1.6, MODELS >= 0.5 ? 1 : 0);
  sun.position.set(tgt.x - 14 * half / 15 - (MODELS >= 0.5 ? 1.5 : 0), 22 * Math.max(0.15, half / 15) + 2, tgt.z + 9 * half / 15 + (MODELS >= 0.5 ? 0.9 : 0));
  // v7: in close-ups the sun stands on the camera's side (40 deg off to the left, 56 deg up). With the old fixed sun the
  // camera often looked into the light: slopes facing the camera faced away from the sun and the tree line above them
  // threw long dark tree-shaped blots down the open slope
  if (V7 && MODELS >= 0.5) { const a = Math.atan2(camera.position.x - tgt.x, camera.position.z - tgt.z) + 0.7; sun.position.set(tgt.x + Math.sin(a) * 3.5, tgt.y + 5.2, tgt.z + Math.cos(a) * 3.5); }
  if (MODELS < 0.5) sun.position.set(-14, 22, 9);
  sun.target.position.copy(MODELS >= 0.5 ? tgt : new THREE.Vector3(0, 0, 0)); sun.target.updateMatrixWorld();
  Object.assign(sun.shadow.camera, { left: -half, right: half, top: half * 0.8, bottom: -half * 0.8, near: 0.1, far: 70 }); sun.shadow.camera.updateProjectionMatrix();
  sun.shadow.bias = MODELS >= 0.5 ? -0.00015 : -0.0004; sun.shadow.normalBias = MODELS >= 0.5 ? 0.002 : 0.02;
  // background: table room -> sky with haze in close-ups
  scene.background.copy(BG_TABLE).lerp(BG_SKY, SKY);
  scene.fog.color.copy(scene.background);
  scene.fog.near = lerp(45, 1.2, SKY); scene.fog.far = lerp(110, 7.5, SKY);
  skyDome.visible = SKY > 0.01; skyDome.material.opacity = SKY; skyDome.position.copy(camera.position);
}

// ---------------------------------------------------------------- post (tilt-shift, vignette, grade, grain)
const POST_VS = `varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }`;
function postMaterial(dir, final) {
  return new THREE.ShaderMaterial({
    uniforms: { tDiffuse: { value: null }, res: { value: new THREE.Vector2(1920, 1080) }, focus: { value: 0.55 }, band: { value: 0.16 }, blur: { value: 1 }, seed: { value: 0 }, grain: { value: 0.012 } },
    vertexShader: POST_VS, depthTest: false, depthWrite: false, toneMapped: final,
    fragmentShader: `
      uniform sampler2D tDiffuse; uniform vec2 res; uniform float focus, band, blur, seed, grain; varying vec2 vUv;
      float h(vec2 p){ return fract(sin(dot(p, vec2(12.9898, 78.233)) + seed) * 43758.5453); }
      void main(){
        float d = max(0.0, abs((1.0 - vUv.y) - focus) - band) / (1.0 - band);
        float r = blur * 9.0 * smoothstep(0.0, 1.0, d * 1.7);
        vec2 stepv = ${dir === 'h' ? 'vec2(1.0, 0.0)' : 'vec2(0.0, 1.0)'} / res * r / 3.0;
        vec4 c = texture2D(tDiffuse, vUv) * 0.2270270270;
        c += texture2D(tDiffuse, vUv + stepv * 1.3846153846) * 0.3162162162; c += texture2D(tDiffuse, vUv - stepv * 1.3846153846) * 0.3162162162;
        c += texture2D(tDiffuse, vUv + stepv * 3.2307692308) * 0.0702702703; c += texture2D(tDiffuse, vUv - stepv * 3.2307692308) * 0.0702702703;
        ${final ? `
        vec2 q = vUv - 0.5; float vig = smoothstep(0.95, 0.25, length(q * vec2(1.0, 0.85)));
        c.rgb *= mix(0.55, 1.0, vig);
        c.rgb = mix(c.rgb, c.rgb * vec3(1.05, 0.98, 0.88), 0.6);
        gl_FragColor = vec4(c.rgb, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
        gl_FragColor.rgb += (h(gl_FragCoord.xy) - 0.5) * grain;
        ` : `gl_FragColor = c;`}
      }`,
  });
}

// ---------------------------------------------------------------- HTML/CSS overlay (rasterised through SVG foreignObject)
const UI_CSS = `
.ui{position:relative;width:1920px;height:1080px;font-family:var(--font-ui);color:var(--text);}
.num{font-family:var(--font-num);font-variant-numeric:tabular-nums;}
.panel{background:linear-gradient(180deg,var(--panel-top),var(--panel-bot));border:2px solid var(--edge);box-shadow:0 0 0 1px rgba(0,0,0,.6),inset 0 1px 0 rgba(255,236,190,.18),0 6px 18px rgba(0,0,0,.45);}
.top{position:absolute;left:50%;top:22px;width:900px;margin-left:-450px;height:112px;border-radius:4px;}
.top .title{position:absolute;left:0;right:0;top:10px;text-align:center;font-size:24px;font-weight:700;letter-spacing:2px;}
.top .title .b{color:#8fb7ef}.top .title .r{color:#f09a8c}.top .title .dot{color:#c8a560;margin:0 12px}
.top .clock{position:absolute;left:50%;top:42px;width:200px;margin-left:-100px;text-align:center;font-size:54px;font-weight:600;line-height:60px;color:#fff3d4;letter-spacing:2px;}
.top .lbl{position:absolute;top:64px;font-size:15px;color:#bfae86;letter-spacing:3px;}
.top .lbl.l{left:34px}.top .lbl.r{right:34px}
.top .chip{position:absolute;top:54px;width:140px;height:38px;line-height:38px;text-align:center;font-weight:900;font-size:21px;letter-spacing:4px;}
.top .chip.b{left:140px;background:linear-gradient(180deg,#4f86d6,#2b5598);border:1px solid #9cc2f5;color:#fff}
.top .chip.r{right:140px;background:linear-gradient(180deg,#d65a4c,#8f2f27);border:1px solid #f5aa9c;color:#fff}
.share{position:absolute;left:50%;top:142px;width:900px;margin-left:-450px;height:44px;}
.share .bar{position:absolute;left:0;right:0;top:0;height:18px;background:#a8392f;border:2px solid #6d5a36;box-shadow:0 2px 8px rgba(0,0,0,.5);}
.share .bar .bf{position:absolute;left:0;top:0;bottom:0;background:linear-gradient(180deg,#5d93e0,#2d5ea6);}
.share .bar .rf{position:absolute;right:0;top:0;bottom:0;background:linear-gradient(180deg,#dd6556,#9b3329);}
.share .tick{position:absolute;top:-6px;width:2px;height:30px;background:#f6e7bd;}
.share .t{position:absolute;top:22px;font-size:16px;font-weight:700;color:#efe6cf;text-shadow:0 1px 3px #000;}
.share .t.l{left:4px}.share .t.r{right:4px}.share .t.c{left:0;right:0;text-align:center;font-weight:500;font-size:14px;color:#cdbb92}
.side{position:absolute;left:24px;top:22px;width:440px;border-radius:4px;padding:12px 16px 14px;}
.side .ref{display:flex;align-items:center;gap:10px;font-size:15px;color:#bfae86;letter-spacing:2px;border-bottom:1px solid #4d4130;padding-bottom:8px;}
.side .ref b{font-size:19px;font-weight:900;letter-spacing:4px;color:#f3e6c2;}
.side .eye{width:26px;height:16px;border:3px solid #e8c14d;border-radius:50%;position:relative;}
.side .eye i{position:absolute;left:7px;top:2px;width:7px;height:7px;border-radius:50%;background:#e8c14d;}
.side .hd{font-size:17px;font-weight:900;letter-spacing:3px;color:#e8c14d;margin-top:10px;}
.force{display:flex;justify-content:space-between;align-items:flex-end;margin-top:4px;}
.force .f{width:48%;}
.force .f .n{font-size:44px;line-height:48px;font-weight:600;}
.force .f.b .n{color:#9cc3f2}.force .f.r{text-align:right}.force .f.r .n{color:#f2a497}
.force .f .s{font-size:14px;color:#cdbb92;}
.chart{margin-top:6px;}
.vps{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap;}
.vp{width:56px;height:46px;border-radius:3px;text-align:center;font-size:14px;line-height:18px;padding-top:4px;border:1px solid rgba(0,0,0,.6);box-shadow:inset 0 1px 0 rgba(255,255,255,.25);}
.vp .h{font-family:"Oswald",sans-serif;font-size:17px;font-weight:600;color:#fff}
.vp .p{font-family:"Oswald",sans-serif;font-size:13px;color:rgba(255,255,255,.85)}
.vp.o0{background:linear-gradient(180deg,#4f86d6,#2b5598)}.vp.o1{background:linear-gradient(180deg,#d65a4c,#8f2f27)}.vp.o2{background:#6b665c}
.vp.hot{outline:3px solid #ffe08a;outline-offset:1px}
.card{position:absolute;right:24px;top:22px;width:430px;border-radius:4px;padding:12px 18px 14px;}
.card .k{font-size:15px;color:#e8c14d;letter-spacing:3px;font-weight:700;}
.card .ti{font-size:26px;font-weight:900;letter-spacing:2px;margin-top:2px;}
.card .tm{font-size:15px;color:#bfae86;margin-top:2px;}
.card .row{display:flex;justify-content:space-between;align-items:baseline;font-size:16px;margin-top:8px;color:#d8c9a6;border-top:1px solid #4d4130;padding-top:7px;}
.card .row .v{color:#f3e6c2;text-align:right;max-width:290px;}
.card .loss{display:flex;gap:10px;margin-top:10px;}
.card .loss div{flex:1;padding:6px 10px;border-radius:3px;}
.card .loss .lb{background:rgba(63,116,196,.25);border:1px solid #3f74c4}.card .loss .lr{background:rgba(192,69,59,.25);border:1px solid #c0453b}
.card .loss .n{font-size:40px;line-height:44px;font-weight:600;color:#fff}
.card .loss .u{font-size:13px;color:#d8c9a6}
.card .units{font-size:14px;line-height:21px;color:#d8c9a6;margin-top:6px;}
.card .units .ub{color:#9cc3f2}.card .units .ur{color:#f2a497}
.card .res{margin-top:10px;padding:8px 12px;font-size:20px;font-weight:900;letter-spacing:2px;text-align:center;border-radius:3px;}
.card .res.b{background:linear-gradient(180deg,#4f86d6,#2b5598)}.card .res.r{background:linear-gradient(180deg,#d65a4c,#8f2f27)}.card .res.n{background:#5a5246}
.feed{position:absolute;right:24px;bottom:96px;width:430px;}
.feed div{display:flex;justify-content:space-between;font-size:16px;line-height:28px;padding:0 10px;margin-top:3px;background:rgba(20,18,15,.72);border-left:5px solid #888;}
.feed div.s0{border-left-color:#3f74c4}.feed div.s1{border-left-color:#c0453b}
.feed div .t{font-family:"Oswald",sans-serif;color:#bfae86;width:52px}.feed div .nm{flex:1}.feed div .d{font-family:"Oswald",sans-serif;font-size:19px;color:#ff8a78;font-weight:600;}
.banner{position:absolute;left:50%;top:232px;width:1000px;margin-left:-500px;text-align:center;}
.banner .k{display:inline-block;font-size:20px;letter-spacing:8px;color:#1c170f;background:#e8c14d;padding:3px 18px;font-weight:900;}
.banner .ti{font-size:54px;font-weight:900;letter-spacing:6px;color:#fff3d4;text-shadow:0 3px 14px rgba(0,0,0,.85),0 0 2px #000;margin-top:8px;}
.banner .su{font-size:22px;color:#efe0bb;text-shadow:0 2px 8px #000;margin-top:4px;}
.cap{position:absolute;left:50%;bottom:86px;width:1100px;margin-left:-550px;text-align:center;}
.cap span{display:inline-block;padding:8px 20px;font-size:21px;background:rgba(18,16,13,.8);border:1px solid #6d5a36;color:#f3e6c2;border-radius:3px;}
.legend{position:absolute;left:24px;bottom:26px;padding:8px 14px;border-radius:4px;font-size:15px;color:#e4d6b4;display:flex;gap:18px;align-items:center;}
.legend .sw{display:inline-block;width:20px;height:13px;vertical-align:-1px;margin-right:6px;border:1px solid #000}
.legend .ln{display:inline-block;width:26px;height:5px;vertical-align:3px;margin-right:6px;}
.legend .star{color:#e0b94e;font-size:17px;margin-right:4px}
.attr{position:absolute;right:24px;bottom:22px;font-size:13px;color:rgba(239,230,207,.72);text-shadow:0 1px 2px #000;text-align:right;line-height:18px;}
.end{position:absolute;left:50%;top:250px;width:980px;margin-left:-490px;border-radius:6px;padding:22px 30px 26px;}
.end .k{font-size:18px;letter-spacing:6px;color:#e8c14d;font-weight:900;}
.end .ti{font-size:42px;font-weight:900;letter-spacing:4px;margin-top:4px;}
.end .grid{display:flex;gap:16px;margin-top:16px;}
.end .grid .c{flex:1;padding:12px 14px;border-radius:4px;background:rgba(0,0,0,.25);border:1px solid #4d4130;}
.end .grid .c .l{font-size:15px;color:#bfae86;letter-spacing:2px}
.end .grid .c .v{font-size:40px;font-weight:600;line-height:48px;}
.end .grid .c .s{font-size:14px;color:#cdbb92}
.end.e7{width:760px;margin-left:-380px;top:330px;text-align:center}.end.e7 .ti{font-size:52px;letter-spacing:2px;margin-top:8px}
.end.el{top:auto;bottom:70px}
.banner,.vs,.end{z-index:5}
.vs{position:absolute;left:50%;top:150px;width:640px;margin-left:-320px;box-sizing:border-box;padding:16px 30px 22px;text-align:center;}
.vs .k{font-size:18px;letter-spacing:6px;color:#e8c14d;font-weight:900;margin-bottom:6px;}
.vs .row{display:flex;align-items:center;justify-content:space-between;}
.vs .sd{width:200px;}
.vs .pt{position:relative;width:156px;height:210px;margin:12px auto 34px;background:#000 0 0/156px 210px no-repeat;}
.vs .nm{font-size:22px;font-weight:900;letter-spacing:2px;}
.vs .s0 .nm{color:#9cc3f2}.vs .s1 .nm{color:#f2a497}
.vs .x{font-size:60px;font-weight:700;color:#e8c14d;text-shadow:0 2px 6px #000;}
.end .note{margin-top:14px;font-size:16px;color:#d8c9a6;line-height:26px;}
.order{position:absolute;left:24px;top:506px;width:476px;box-sizing:border-box;border-radius:4px;padding:12px 14px;display:flex;gap:14px;}
.order.s0{border-left:6px solid var(--blue)}.order.s1{border-left:6px solid var(--red)}
.order .pt{width:117px;height:158px;flex:none;border:2px solid var(--edge);background:#000 center/cover no-repeat;box-shadow:0 2px 8px rgba(0,0,0,.6);}
.order .bd{flex:1;min-width:0;}
.order .who{font-size:15px;letter-spacing:2px;color:var(--muted);}
.order .who b{font-size:19px;letter-spacing:1px;margin-right:8px;}
.order.s0 .who b{color:var(--blue-lt)}.order.s1 .who b{color:var(--red-lt)}
.order .when{font-family:var(--font-num);color:var(--accent);margin-left:6px;}
.order .q{font-size:18px;line-height:28px;color:var(--text-hi);margin-top:6px;}
.order .q:before{content:"“";color:var(--accent);font-weight:900;margin-right:2px}.order .q:after{content:"”";color:var(--accent);font-weight:900;margin-left:2px}
.order .src{font-size:12px;color:var(--muted);margin-top:6px;}
.narr{position:absolute;left:50%;bottom:150px;width:1240px;margin-left:-620px;text-align:center;}
.narr span{display:inline-block;padding:8px 22px;font-size:26px;line-height:38px;font-weight:700;color:#fff8e6;background:rgba(10,9,7,.78);border-radius:3px;text-shadow:0 1px 2px #000;}
.narr .sp{color:var(--accent);margin-right:10px;}

.tags{position:absolute;left:0;top:0;}
.eng{position:absolute;width:38px;height:37px;background-size:38px 37px;}
.alab{position:absolute;transform:translate(-50%,-50%);white-space:nowrap;font-size:26px;font-weight:700;padding:3px 10px 4px;color:#fff;background:rgba(14,14,12,.84);border:1px solid #000;border-left:5px solid #888;box-shadow:0 2px 6px rgba(0,0,0,.6);}
.vlab{position:absolute;transform:translate(-50%,-50%);white-space:nowrap;font:600 28px/34px "Oswald","Noto Sans SC",sans-serif;padding:2px 10px 4px;color:#fff5d6;background:rgba(42,36,28,.84);border-radius:3px;box-shadow:0 2px 5px rgba(0,0,0,.5);}
.alab.s0{border-left-color:var(--blue)}.alab.s1{border-left-color:var(--red)}.alab.cur{font-size:30px;outline:2px solid var(--accent);}
.step{position:absolute;left:24px;top:150px;width:520px;box-sizing:border-box;padding:12px 18px 14px;}
.step.s0{border-left:7px solid var(--blue)}.step.s1{border-left:7px solid var(--red)}
.step .k{font-size:15px;letter-spacing:3px;color:var(--accent);font-weight:700}
.step .ti{font-size:28px;font-weight:900;margin-top:4px;color:var(--text-hi)}
.step ul{margin:8px 0 0;padding:0;list-style:none}.step li{font-size:19px;line-height:30px;color:var(--text-hi);font-weight:700}
.step li .ty{font-size:14px;font-weight:400;color:var(--muted);margin-left:10px}
.card.c6 .ti{font-size:26px}.card.c6 .loss .n{font-size:34px;line-height:38px}
.step ul.two{columns:2;column-gap:14px}.step ul.two li{font-size:18px;line-height:28px;white-space:nowrap}
.step ul.many{columns:3;column-gap:14px}.step ul.many li{font-size:15px;line-height:23px;font-weight:700;white-space:nowrap}
.vp.w5{width:70px}.end .cred{margin-top:12px;padding-top:10px;border-top:1px solid var(--rule,#4d4130);font-size:13px;line-height:20px;color:var(--muted,#bfae86)}
.step .q{margin-top:10px;font-size:17px;line-height:27px;color:var(--text);border-top:1px solid var(--rule);padding-top:8px}
.step .q:before{content:"“";color:var(--accent)}.step .q:after{content:"”";color:var(--accent)}
.step .src{font-size:12px;color:var(--muted);margin-top:4px}
.explain{position:absolute;left:24px;top:150px;width:500px;box-sizing:border-box;padding:12px 18px 16px;}
.explain .k{font-size:15px;letter-spacing:3px;color:var(--accent);font-weight:700}
.explain .ti{font-size:26px;font-weight:900;margin-top:4px;color:var(--text-hi)}
.explain .tx{font-size:18px;line-height:30px;margin-top:8px;color:var(--text)}
.explain .tx b{color:var(--text-hi)}
`;
// force-% / share curves (SVG polyline), drawn up to the current game second
function chartSVG(g) {
  const sc = G.sc, Tn = G.T || sc[sc.length - 1][0], w = 408, h = 132, y0 = 40, y1 = 100;
  const X = t => (t / Tn * (w - 52) + 30).toFixed(1), Y = p => (h - 16 - (clamp(p, y0, y1) - y0) / (y1 - y0) * (h - 26)).toFixed(1);
  const line = (k) => { const pts = []; for (const r of sc) { if (r[0] > g) break; pts.push(X(r[0]) + ',' + Y(r[k])); } const k0 = rowAt(sc, g); if (k0 >= 0) pts.push(X(g) + ',' + Y(sc[k0][k])); return pts.join(' '); };
  let grid = '';
  for (const p of [40, 60, 80, 100]) grid += `<line x1="30" x2="${w - 4}" y1="${Y(p)}" y2="${Y(p)}" stroke="rgba(239,230,207,.14)" stroke-width="1"/><text x="24" y="${+Y(p) + 4}" font-size="11" fill="#9d8f70" text-anchor="end" font-family="Oswald">${p}</text>`;
  for (let hr = 0; hr * 3600 <= Tn; hr++) { const x = X(hr * 3600); grid += `<line x1="${x}" x2="${x}" y1="6" y2="${h - 16}" stroke="rgba(239,230,207,.08)"/><text x="${x}" y="${h - 3}" font-size="11" fill="#9d8f70" text-anchor="middle" font-family="Oswald">${hhmm(hr * 3600)}</text>`; }
  for (const m of SHOT.moments || []) grid += `<rect x="${X(m.from)}" y="6" width="${(+X(m.to) - +X(m.from)).toFixed(1)}" height="${h - 22}" fill="rgba(232,193,77,${g >= m.from ? 0.13 : 0.05})"/><text x="${((+X(m.from) + +X(m.to)) / 2).toFixed(1)}" y="16" font-size="11" fill="#e8c14d" text-anchor="middle" font-family="Oswald">${m.n}</text>`;
  return `<svg class="chart" width="${w}" height="${h}" xmlns="http://www.w3.org/2000/svg">${grid}
    <polyline points="${line(3)}" fill="none" stroke="#e8c14d" stroke-width="2" stroke-dasharray="5 3"/>
    <polyline points="${line(2)}" fill="none" stroke="#f08070" stroke-width="2.6"/>
    <polyline points="${line(1)}" fill="none" stroke="#7fb0f0" stroke-width="2.6"/>
    <line x1="${X(g)}" x2="${X(g)}" y1="6" y2="${h - 16}" stroke="#fff3d4" stroke-width="1.2"/></svg>`;
}
function scoreAt(g) {
  const sc = G.sc, k = Math.max(0, rowAt(sc, g)), r0 = sc[k], r1 = sc[Math.min(sc.length - 1, k + 1)];
  const f = r1[0] > r0[0] ? clamp((g - r0[0]) / (r1[0] - r0[0]), 0, 1) : 0;
  return { share: lerp(r0[3], r1[3], f), fb: lerp(r0[1], r1[1], f), fr: lerp(r0[2], r1[2], f) };
}
const TOTAL = [0, 1].map(sd => G.u.reduce((s, u) => s + (u[1] === sd ? u[5] : 0), 0));
function subsLeft(side, g) {                         // subunits on screen (alive) of a side
  let n = 0;
  for (let i = 0; i < NU; i++) { if (G.u[i][1] !== side) continue; const s = unitState(i, g); n += s ? shownAlive(i, g, s) : G.u[i][5]; }
  return n;
}
function momentAt(g) { for (const m of SHOT.moments || []) if (g >= (m.show ?? m.from) && g <= m.to + (m.hold ?? 600)) return m; return null; }
function cardHTML(m, g, style = '') {
  const tx = (m.targets || [m.target]).map(hexXZ), rad = m.radius ?? 1.1;
  const inArea = h => { if (!h) return false; const [x, z] = hexXZ(h); return tx.some(([a, b]) => Math.hypot(x - a, z - b) <= rad); };
  const shown = lossSchedule.filter(l => l.rowT >= m.from && l.rowT <= m.to && l.g <= g && inArea(l.hex));
  const lb = shown.filter(l => l.side === 0).reduce((s, l) => s + l.n, 0), lr = shown.filter(l => l.side === 1).reduce((s, l) => s + l.n, 0);
  const strength = (ids, side) => ids.map(i => { const s0 = unitState(i, m.from), s = unitState(i, Math.min(g, m.to)); const a0 = s0 ? s0.alive : G.u[i][5], a = s ? shownAlive(i, Math.min(g, m.to), s) : a0; return `<span class="${side ? 'ur' : 'ub'}">${V4 ? unitZh(i) : G.u[i][0]}</span> ${a0}→${a}`; }).join('　');
  const changes = (G.vc || []).filter(([t, h]) => (m.targets || [m.target]).includes(h) && t >= m.from && t <= m.to && t <= g);
  let res = `<div class="res n">${m.pending || '交战中…'}</div>`;   // m.pending: status text while the moment runs
  if (changes.length) { const [t, h, ow] = changes[changes.length - 1]; res = `<div class="res ${ow === 0 ? 'b' : 'r'}">${ow === 0 ? '蓝军' : '红军'}${ow === m.attacker ? (m.retake ? '夺回' : '攻占') : '守住'} ${hexName(h)} · ${hhmm(t)}</div>`; }
  else if (g >= m.to && m.result) res = `<div class="res ${m.result[0] === 0 ? 'b' : m.result[0] === 1 ? 'r' : 'n'}">${m.result[1]}</div>`;
  const att = m.attacker === 0 ? '蓝军' : '红军';
  if (V6) return `<div class="card panel c6" style="${style}"><div class="k">关键时刻 ${m.n}</div><div class="ti">${m.title}</div>
    <div class="loss"><div class="lb"><div class="u">蓝军损失</div><div class="n num">${lb}</div></div><div class="lr"><div class="u">红军损失</div><div class="n num">${lr}</div></div></div>
    ${res}</div>`;
  return `<div class="card panel" style="${style}"><div class="k">战报 · 关键时刻 ${m.n}</div><div class="ti">${m.title}</div>
    <div class="tm num">${hhmm(m.from)}–${hhmm(m.to)} · 目标格 ${(m.targets || [m.target]).map(hexName).join(' / ')}${m.vp ? ' · ' + m.vp + ' 分' : ''}</div>
    <div class="row"><span>进攻方</span><span class="v">${att} ${m.attackerText}</span></div>
    <div class="row"><span>防守方</span><span class="v">${m.attacker === 0 ? '红军' : '蓝军'} ${m.defenderText}</span></div>
    <div class="loss"><div class="lb"><div class="u">蓝军损失（子单位）</div><div class="n num">${lb}</div></div><div class="lr"><div class="u">红军损失（子单位）</div><div class="n num">${lr}</div></div></div>
    <div class="units">${strength(m.att || [], m.attacker)}<br/>${strength(m.def || [], 1 - m.attacker)}</div>
    ${res}</div>`;
}
// v4 broadcast layout: the board gets the screen; panels pop in only when the shot list asks (SHOT.panels: [{ from, to,
// kind: 'force' | 'card' | 'explain', m (moment n for 'card'), title / text for 'explain' }]) and slide back out
function panelOn(v, kind, pick) {
  let a = 0, hit = null;
  for (const p of SHOT.panels || []) { if (p.kind !== kind || (typeof pick === "function" && !pick(p))) continue; const x = clamp(Math.min((v - p.from) / 0.4, (p.to - v) / 0.4), 0, 1); if (x > a) { a = x; hit = p; } }
  return pick === true ? hit : a;
}
const slide = (a, dir) => `opacity:${a.toFixed(2)};transform:translateX(${((1 - a) * 46 * dir).toFixed(1)}px)`;
function cardV4(v, g, end) {
  if (end) return '';
  let best = null, a = 0;
  for (const p of SHOT.panels || []) { if (p.kind !== 'card') continue; const x = clamp(Math.min((v - p.from) / 0.4, (p.to - v) / 0.4), 0, 1); if (x > a) { a = x; best = p; } }
  if (!best) return '';
  const m = (SHOT.moments || []).find(mm => mm.n === best.m) || momentAt(g); if (!m) return '';
  return cardHTML(m, g, slide(a, 1));
}
const SWORDS = `<svg width="38" height="37" viewBox="0 0 38 37" xmlns="http://www.w3.org/2000/svg"><g stroke="#1b1206" stroke-width="1.2" stroke-linejoin="round"><path d="M10 9 L27 26 L25 28 L8 11 Z" fill="#efe6cf"/><path d="M28 9 L11 26 L13 28 L30 11 Z" fill="#efe6cf"/><path d="M22 25 L27 30 M16 25 L11 30" stroke="#e8c14d" stroke-width="3"/></g></svg>`;
let ENGAGE_BG = '';
function v4Extras(v, g, end) {
  if (end) return '';
  const Wd = SHOT.width, H = SHOT.height, out = [];
  camera.updateMatrixWorld();
  const scr = p => { const q = p.clone().project(camera); return q.z > 1 || q.z < -1 ? null : [(q.x + 1) / 2 * Wd, (1 - q.y) / 2 * H]; };
  const cur = stepAt(v), seq = stepSeq(v);
  // spotlight on the units of the current order step
  const rings = TAGS.filter(t => t.k === 'ring').map(t => scr(t.p)).filter(Boolean);
  let svg = '';
  if (cur && rings.length && V5) {                   // v5: soft halo only (dim the rest, light the units of the step)
    const a = clamp(Math.min((v - cur.from) / 0.5, 1), 0, 1);
    svg += `<defs><radialGradient id="hk"><stop offset="0.45" stop-color="#000" stop-opacity="1"/><stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>`
      + `<radialGradient id="hl"><stop offset="0" stop-color="#ffe9b0" stop-opacity=".30"/><stop offset="1" stop-color="#ffe9b0" stop-opacity="0"/></radialGradient>`
      + `<mask id="sp"><rect width="${Wd}" height="${H}" fill="#fff"/>${rings.map(([x, y]) => `<circle cx="${x.toFixed(0)}" cy="${y.toFixed(0)}" r="96" fill="url(#hk)"/>`).join('')}</mask></defs>`
      + `<rect width="${Wd}" height="${H}" fill="rgba(0,0,0,${(a * 0.45).toFixed(2)})" mask="url(#sp)"/>`
      + rings.map(([x, y]) => `<circle cx="${x.toFixed(0)}" cy="${y.toFixed(0)}" r="70" fill="url(#hl)" opacity="${a.toFixed(2)}"/>`).join('');
  } else if (cur && rings.length) {
    const a = clamp(Math.min((v - cur.from) / 0.5, 1), 0, 1) * 0.42;
    svg += `<defs><mask id="sp"><rect width="${Wd}" height="${H}" fill="#fff"/>${rings.map(([x, y]) => `<circle cx="${x.toFixed(0)}" cy="${y.toFixed(0)}" r="64" fill="#000"/>`).join('')}</mask></defs>`
      + `<rect width="${Wd}" height="${H}" fill="rgba(0,0,0,${a.toFixed(2)})" mask="url(#sp)"/>`;
    const pulse = 1 + 0.08 * Math.sin(v * 6);
    for (const [x, y] of rings) svg += `<circle cx="${x.toFixed(0)}" cy="${y.toFixed(0)}" r="${(40 * pulse).toFixed(1)}" fill="none" stroke="#f2c84b" stroke-width="4"/><circle cx="${x.toFixed(0)}" cy="${y.toFixed(0)}" r="${(40 * pulse).toFixed(1)}" fill="none" stroke="#000" stroke-opacity=".6" stroke-width="1.5"/>`;
  }
  // engagement links: dashed line between the two units, the combat icon in the middle
  const icons = [];
  for (const t of TAGS) if (t.k === 'engage') {
    const A1 = scr(t.a), B1 = scr(t.b); if (!A1 || !B1) continue;
    const inView = q => q[0] > 20 && q[0] < Wd - 20 && q[1] > 80 && q[1] < H - 20; if (!inView(A1) || !inView(B1)) continue;
    const L = Math.hypot(B1[0] - A1[0], B1[1] - A1[1]); if (L < 30) continue;
    svg += `<line x1="${A1[0].toFixed(0)}" y1="${A1[1].toFixed(0)}" x2="${B1[0].toFixed(0)}" y2="${B1[1].toFixed(0)}" stroke="#000" stroke-opacity=".55" stroke-width="6"/>`
      + `<line x1="${A1[0].toFixed(0)}" y1="${A1[1].toFixed(0)}" x2="${B1[0].toFixed(0)}" y2="${B1[1].toFixed(0)}" stroke="#f6e7bd" stroke-width="2.5" stroke-dasharray="10 7"/>`;
    const mx = (A1[0] + B1[0]) / 2, my = (A1[1] + B1[1]) / 2;
    if (!icons.some(([x, y]) => Math.hypot(x - mx, y - my) < 44)) icons.push([mx, my]);
  }
  if (svg) out.push(`<svg class="tags" width="${Wd}" height="${H}" xmlns="http://www.w3.org/2000/svg">${svg}</svg>`);
  for (const [x, y] of icons) out.push(`<div class="eng" style="left:${(x - 19).toFixed(0)}px;top:${(y - 18).toFixed(0)}px;${ENGAGE_BG ? `background-image:url(${ENGAGE_BG})` : ''}">${SWORDS}</div>`);
  // arrow labels (no overlap: later ones step down)
  const placed = [];
  if (V6) {                                          // try points along the arrow and small vertical shifts; skip if nothing is free
    const block = [[0, 0, Wd, 104]];
    if (cur) block.push([0, 90, 570, 470]);
    if (panelOn(v, 'card') > 0) block.push([Wd - 470, 90, Wd, 560]);
    if (panelOn(v, 'force') > 0) block.push([0, 90, 480, 520]);
    if (ORDERS.some(o => v >= o.v0 && v <= o.v1)) block.push([0, 470, 520, 960]);
    block.push([Wd - 450, 830, Wd, 1030], [120, 930, Wd - 120, 1080]);   // feed; subtitles (11-08: 1-row ~42 px text)
    for (const q of SHOT.people || []) if (v >= q.from && v <= q.to) {   // person cards (width estimated: portrait + scope column)
      const k = q.small ? 0.8 : 1, dy = q.small ? 58 : 0, w = 560 * k, top = (q.y ?? 300) + 298 * (1 - k) + dy; block.push([q.x - w / 2, top, q.x + w / 2, (q.y ?? 300) + 298 + dy]); }   // .pc.sm: 0.8, 58 px lower (theme)
    const hit = (r, L) => L.some(q => r[0] < q[2] && r[2] > q[0] && r[1] < q[3] && r[3] > q[1]);
    // counters on screen (sprites anchored bottom-centre, fixed pixel size): labels keep off them when they can
    const ctr = [], px = SHOT.counterPx || [26, 54];
    if (V7 && MODELS < 0.5) for (const o of vpObjs) if (o.star) { const q = scr(o.star.position), sp = (SHOT.vpPx || [34])[0]; if (q) ctr.push([q[0] - sp / 2, q[1] - sp * 0.85, q[0] + sp / 2, q[1] + sp * 0.15]); }
    if (MODELS < 0.5) for (const o of units) {
      if (o.chip && o.chip.visible) { const q = scr(o.chip.position); if (q) { const w = px[0] * 66 / 20, hh = px[0] * CHIP_H / 20; ctr.push([q[0] - w / 2, q[1] - hh, q[0] + w / 2, q[1]]); } }
      if (o.root.visible && o.flag.visible) { o.flag.getWorldPosition(tmpV); const q = scr(tmpV); if (q) { const w = px[1] * FLAG_W / FLAG_H; ctr.push([q[0] - w / 2, q[1] - px[1], q[0] + w / 2, q[1]]); } }
    }
    const place = (pts, w, h, dxs, dys) => {          // first free spot: off labels, panels and counters; else off labels and panels only
      for (const strict of [true, false]) for (const p of pts) for (const dy of dys) for (const dx of dxs) {
        const x = p[0] + dx, y = p[1] + dy, r = [x - w / 2, y - h / 2, x + w / 2, y + h / 2];
        if (r[0] < 10 || r[2] > Wd - 10 || r[1] < 0 || r[3] > H) continue;
        if (!hit(r, placed) && !hit(r, block) && (!strict || !hit(r, ctr))) return [x, y, r];
      }
      return null;
    };
    const tw = (text, fs, ascii) => [...text].reduce((a, c) => a + (c.charCodeAt(0) > 255 ? fs * 1.02 : fs * ascii), 0);
    for (const t of TAGS) if (t.k === 'label') {
      const fs = t.cur ? 30 : 26, w = tw(t.text, fs, 0.55) + 34, h = fs + 14;   // .alab / .alab.cur font sizes (11-08: 17 / 21)
      const best = place((t.cands || [t.p]).map(scr).filter(Boolean), w, h, [0], [0, -h - 4, h + 4, -2 * h - 8, 2 * h + 8]);
      if (!best) continue;
      placed.push(best[2]);
      out.push(`<div class="alab s${t.side}${t.cur ? ' cur' : ''}" style="left:${best[0].toFixed(0)}px;top:${best[1].toFixed(0)}px;opacity:${Math.max(0.55, Math.min(1, t.a + 0.2)).toFixed(2)}${tiltBlurCss(best[1])}">${t.text}</div>`);
    }
    if (MODELS < 0.5) for (const o of vpObjs) {      // objective labels: near the flag, the nearest free spot around it
      let q = scr(o.label.position);
      if (o.star) { const sq = scr(o.star.position); q = sq && [sq[0], sq[1] - (SHOT.vpPx || [34])[0] - 14]; }
      if (!q || q[0] < 0 || q[0] > Wd || q[1] < 0 || q[1] > H) continue;
      let tag = '';
      if (SHOT.vpChangeTag && !SHOT.vpCapture) for (const [t, hh, ow] of G.vc || []) { if (hh !== o.h) continue; const age = v - vOf(t);
        if (age >= -0.05 && age < 3) tag = ` · <span class="vc s${ow}">${ow === 0 ? '蓝军' : ow === 1 ? '红军' : '无人'}占领</span>`; }
      const plain = `${hexName(o.h)} · ${o.val} 分${tag ? ' · 蓝军占领' : ''}`, text = `${hexName(o.h)} · ${o.val} 分${tag}`, w = tw(plain, 28, 0.5) + 26, h = 40;   // .vlab 28 px (11-08: 20)
      const best = place([q], w, h, [0, -w / 2 - 30, w / 2 + 30], [0, -44, 44, -88, 88]);
      if (!best) continue;
      placed.push(best[2]);
      const own = vpOwner(o.h, o.owner0, g), cap = CAPTURES.length ? captureAt(o.h, v) : null, fl = cap ? cap.flash.v : 0;
      const flash = fl > 0.01 ? `;--fl:${fl.toFixed(3)}` : '';
      out.push(`<div class="vlab${CAPTURES.length ? ` o${own}` : ''}${fl > 0.01 ? ' fl' : ''}" style="left:${best[0].toFixed(0)}px;top:${best[1].toFixed(0)}px${flash}${tiltBlurCss(best[1], fl > 0.01 ? `brightness(${(1 + 1.3 * fl).toFixed(3)})` : '')}">${text}</div>`);
    }
    // unit names beside the tiny counters once the camera is close (SHOT.chipNames { dist, fade, px }): a chip that stands
    // for several units names them together (chipName); the units in action, then the bigger stacks, then those nearest
    // the screen centre pick first; a name that finds no free spot (off counters, labels and panels) is left out
    const CN = SHOT.chipNames;
    if (CN && MODELS < 0.5 && CAMD < CN.dist) {
      // SHOT.mapTextScale (default 1.6, 11-08 phone pass) enlarges the names over the shot's own px
      const fs = Math.round((CN.px || 15) * (SHOT.mapTextScale ?? 1.6)), a = clamp((CN.dist - CAMD) / (CN.fade || 1.5), 0, 1), cw = px[0] * 66 / 20;
      const list = units.filter(o => o.chip && o.chip.visible).map(o => { const q = scr(o.chip.position), mem = o.chipMem || o.members || [o.i];
        return q && { o, q, mem, hot: mem.some(i => firing.has(i)) ? 1 : 0, dc: Math.hypot(q[0] - Wd / 2, q[1] - H / 2) }; }).filter(Boolean)
        .sort((x, y) => y.hot - x.hot || y.mem.length - x.mem.length || x.dc - y.dc);
      for (const it of list) {
        const [sa, st] = chipStrength(it.mem), hh = px[0] * CHIP_H / 20, nm = chipName(it.mem);
        const text = CN.strength === false ? nm : `${nm} <span class="st">${sa}/${st}</span>`, w = tw(`${nm} ${sa}/${st}`, fs, 0.55) + 20, h = fs + 10, [x0, y0] = it.q, ym = y0 - hh + px[0] / 2;
        let best = null;
        for (const [x, y] of [[x0 + cw / 2 + 3 + w / 2, ym], [x0 - cw / 2 - 3 - w / 2, ym], [x0, y0 - hh - 3 - h / 2], [x0, y0 + 3 + h / 2]]) {
          const r = [x - w / 2, y - h / 2, x + w / 2, y + h / 2];
          if (r[0] < 10 || r[2] > Wd - 10 || r[1] < 0 || r[3] > H) continue;
          if (!hit(r, placed) && !hit(r, block) && !hit(r, ctr)) { best = [x, y, r]; break; }
        }
        if (!best) continue;
        placed.push(best[2]);
        out.push(`<div class="ulab s${it.o.side}" style="left:${best[0].toFixed(0)}px;top:${best[1].toFixed(0)}px;font-size:${fs}px;opacity:${a.toFixed(2)}${tiltBlurCss(best[1])}">${text}</div>`);
      }
    }
  }
  for (const t of TAGS) if (t.k === 'label' && !V6) {
    const p = scr(t.p); if (!p) continue; let [x, y] = p;
    const w = 14 + t.text.length * 17;
    for (let k = 0; k < 6 && placed.some(q => Math.abs(q[0] - x) < (q[2] + w) / 2 && Math.abs(q[1] - y) < 30); k++) y += 32;
    if (x < 40 || x > Wd - 40 || y < 120 || y > H - 60) continue;
    placed.push([x, y, w]);
    out.push(`<div class="alab s${t.side}${t.cur ? ' cur' : ''}" style="left:${x.toFixed(0)}px;top:${y.toFixed(0)}px;opacity:${Math.max(0.55, Math.min(1, t.a + 0.2)).toFixed(2)}">${t.text}</div>`);
  }
  // order-by-order card
  if (cur) {
    const k = seq.indexOf(cur) + 1, a = clamp(Math.min((v - cur.from) / 0.35, (cur.to - v) / 0.35), 0, 1);
    const many = cur.units.length > 6;
    const names = cur.units.map(i => many || V6 ? `<li>${unitZh(i)}</li>` : `<li>${sideZh(i)}<span class="ty">${TYPE_ZH[G.u[i][2]] || ''}${SIZE_ZH[G.u[i][3]] || ''} · 原名 ${G.u[i][0]}</span></li>`).join('');
    out.push(`<div class="step panel s${cur.side}" style="${slide(a, -1)}"><div class="k">${cur.kicker || '逐条展示命令'} · 第 ${k} / ${seq.length} 条</div>
      <div class="ti">${V6 ? `${cur.side ? '红军' : '蓝军'} ${cur.title6 || (cur.dest ? cur.title : '就地坚守')} · ${cur.units.length} 个单位` : cur.title}</div><ul class="${many ? 'many' : V6 ? 'two' : ''}">${names}</ul>${cur.quote ? `<div class="q">${cur.quote}</div>${V5 ? '' : `<div class="src">${cur.src || ''}</div>`}` : ''}</div>`);
  }
  // explanation cards
  const ex = V6 ? null : panelOn(v, 'explain', true);
  if (ex) { const a = panelOn(v, 'explain'); out.push(`<div class="explain panel" style="${slide(a, -1)}"><div class="k">${ex.k || '看图说明'}</div><div class="ti">${ex.title}</div><div class="tx">${ex.text}</div></div>`); }
  return out.join('');
}
// boards are drawn SHOT.boardScale (default 1.25, 11-08 phone pass) larger around their top centre: the same layout, every
// text, picture and frame scaled together; a board that would then be wider than 1840 px lays out narrower (it reflows)
// and one that would cross the screen edge moves in. Highlight frames (cls "mark") keep their size and place (their
// label is enlarged in the theme and turns to the frame's right edge near the screen's right edge).
function boardsHTML(v) {
  const S0 = SHOT.boardScale ?? 1.25;
  // left edges of the small person cards up now (the judge, 0.8 x ~560 px wide): a board that was laid out clear of one
  // stays clear of it when enlarged (moves left, and lays out narrower if it must)
  const cards = (SHOT.people || []).filter(q => q.small && v >= q.from && v <= q.to).map(q => q.x - 560 * 0.8 / 2);
  return (SHOT.boards || []).filter(b => v >= b.from && v <= b.to).map(b => {
    const a = clamp(Math.min((v - b.from) / 0.45, (b.to - v) / 0.45), 0, 1), mark = /\bmark\b/.test(b.cls || '');
    const S = mark ? 1 : (b.scale ?? S0), x0 = b.x ?? 960, w0 = b.w ?? 900;
    let w = Math.min(w0, Math.floor(1840 / S)), x = mark ? x0 : clamp(x0, 20 + w * S / 2, SHOT.width - 20 - w * S / 2);
    if (!mark) for (const cl of cards) if (x0 + w0 / 2 <= cl + 10 && x + w * S / 2 > cl - 20) {
      w = Math.min(w, Math.floor((cl - 40) / S)); x = clamp(Math.min(x, cl - 20 - w * S / 2), 20 + w * S / 2, SHOT.width); }
    const r = mark && x0 + w / 2 > SHOT.width - 260 ? ' r' : '';
    return `<div class="board panel${b.cls ? ' ' + b.cls : ''}${r}" style="left:${x}px;top:${b.y ?? 140}px;width:${w}px;opacity:${a.toFixed(2)};transform-origin:50% 0;transform:translate(-50%,${((1 - a) * 18).toFixed(1)}px)${S !== 1 ? ` scale(${S})` : ''}">${b.htmlR || ''}</div>`;
  }).join('');
}
// staging (tools/video/staging/STANDARD.md): the programme frame stays (top bar, clock, share bar, commander cards with
// portraits); the map-side information comes and goes with the cues in fpc-symbology.js. stagingHTML (clock and subtitles
// only) is the v0 look, kept for comparison
function stagingHTML(v, g) {
  const nr = NARR.find(n => v >= n.from && v <= n.to);
  return `<div class="ui stg"><style>.ui.stg .top{background:none;left:auto;right:0;width:260px}.ui.stg .top:before{display:none}</style>
  <div class="top"><div class="clock num">${hhmm(g)}</div></div>
  ${nr ? `<div class="narr"><span>${nr.speaker ? `<span class="sp">${nr.speaker}</span>` : ''}${nr.text}</span></div>` : ''}</div>`;
}
function overlayHTML(v, g) {
  const { fb, fr } = scoreAt(g), M = SHOT.match, end = SHOT.endCard && v >= SHOT.endCard.from;
  // v7: once the end card is up the top bar shows the official result too (same numbers as the card)
  // endCard.mode 'losses' (programme segments): the end shows the front and the losses only, the score is left to the
  // results segment, so the share bar goes away too
  const lossEnd = end && SHOT.endCard.mode === 'losses';
  const fin = V7 && end && !lossEnd && (SHOT.finalScores || {})[SHOT.run], share = fin ? fin[0] : scoreAt(g).share;
  const shareTxt = (Math.round(share * 10) / 10).toFixed(1), red = (Math.round((100 - share) * 10) / 10).toFixed(1);
  const speed = Math.round(gOf(v + 0.5) - gOf(v - 0.5));
  // VP chips
  const chips = (G.vp || []).map(([h, val, o0]) => {
    const own = vpOwner(h, o0, g), hot = (G.vc || []).some(([t, hh]) => hh === h && v - vOf(t) >= 0 && v - vOf(t) < 4);
    return `<div class="vp o${own}${hot ? ' hot' : ''}${V5 ? ' w5' : ''}"><div class="h">${hexName(h)}</div><div class="p">${val}</div></div>`;
  }).join('');
  // kill feed: the last losses shown on screen
  const feed = lossSchedule.filter(l => l.g <= g && v - vOf(l.g) < 9).slice(V6 ? -3 : -5).reverse().map(l =>
    `<div class="s${l.side}"><span class="t">${hhmm(l.g)}</span><span class="nm">${l.side === 0 ? '蓝' : '红'} ${unitZh(l.unit)}</span><span class="d num">−${l.n}</span></div>`).join('');
  const m = momentAt(g);
  // moment banner: first seconds of a moment (video time)
  let banner = '';
  for (const mm of V7 ? [] : SHOT.moments || []) { const a = v - vOf(mm.show ?? mm.from); if (a >= 0 && a < (mm.banner ?? 3.2)) banner = `<div class="banner" style="opacity:${clamp(Math.min(a / 0.4, ((mm.banner ?? 3.2) - a) / 0.6), 0, 1).toFixed(2)}"><div class="k">关键时刻 ${mm.n}</div><div class="ti">${mm.title}</div><div class="su">${mm.sub || ''}</div></div>`; }
  const intro = SHOT.intro && v < SHOT.intro.to ? `<div class="banner" style="opacity:${clamp(Math.min(v / 0.5, (SHOT.intro.to - v) / 0.8), 0, 1).toFixed(2)}"><div class="k">${SHOT.intro.k}</div><div class="ti">${SHOT.intro.title}</div><div class="su">${SHOT.intro.sub}</div></div>` : '';
  const cap = (SHOT.captions || []).find(c => v >= c.from && v <= c.to);
  // matchup card (SHOT.versus: [{ from, to, k }]): both commanders' portraits and names, blue left, red right
  const vsq = (SHOT.versus || []).find(q => v >= q.from && v <= q.to);
  const versus = vsq ? (() => {
    const a = clamp(Math.min((v - vsq.from) / 0.4, (vsq.to - v) / 0.5), 0, 1), CM = SHOT.commanders || {};
    const sd = (k, side) => { const C = CM[k] || {}; return `<div class="sd s${side}"><div class="pt" style="background-image:url(${C.data || ''})"><div class="pn">${C.name || ''}</div></div><div class="nm">${side === 0 ? '蓝军' : '红军'} · ${C.name || ''}</div></div>`; };
    return `<div class="vs panel" style="opacity:${a.toFixed(2)}">${vsq.k ? `<div class="k">${vsq.k}</div>` : ''}<div class="row">${sd('blue', 0)}<div class="x num">VS</div>${sd('red', 1)}</div></div>`;
  })() : '';
  const oc = ORDERS.find(o => v >= o.v0 && v <= o.v1), nr = NARR.find(n => v >= n.from && v <= n.to);
  const orderHTML = oc ? (() => {
    const C = (SHOT.commanders || {})[oc.side === 0 ? 'blue' : 'red'] || {}, a = clamp(Math.min((v - oc.v0) / 0.35, (oc.v1 - v) / 0.35), 0, 1);
    return `<div class="order panel s${oc.side}${oc.kind === 'order' ? ' ord' : ''}" style="opacity:${a.toFixed(2)}"><div class="pt" style="background-image:url(${C.data || ''})">${V9 ? `<div class="pn">${C.name || ''}</div>` : ''}</div><div class="bd">${oc.kind === 'order' ? '<div class="oh"><i></i>作战命令</div>' : ''}
      <div class="who"><b>${oc.side === 0 ? '蓝军' : '红军'}指挥官 · ${C.name || ''}</b>${oc.verb || '下令'}<span class="when">${oc.when}</span></div>
      <div class="q">${oc.text}</div>${V5 ? '' : `<div class="src">${C.src || '指挥官原话（节选）'}</div>`}</div></div>`;
  })() : '';
  // programme opening (shot-opening.js): person cards (SHOT.people [{ from, to, img, name, role, x, y, on: [[from, to]] }],
  // portrait in the leader frame + name and role plates), a title card (SHOT.titleCard { from, to, k, title, sub }) and a
  // screen dim (SHOT.dim [{ from, to, a }]); fades are 0.5 s
  const fade = (q, d = 0.5) => clamp(Math.min((v - q.from) / d, (q.to - v) / d), 0, 1);
  const dimQ = (SHOT.dim || []).find(q => v >= q.from && v <= q.to);
  const dimHTML = dimQ ? `<div class="dim" style="opacity:${(fade(dimQ, 0.6) * (dimQ.a ?? 0.55)).toFixed(3)}"></div>` : '';
  const peopleHTML = (SHOT.people || []).filter(q => v >= q.from && v <= q.to).map(q => {
    const on = (q.on || []).some(([a, b]) => v >= a && v <= b);
    return `<div class="pc panel${on ? ' on' : ''}${q.small ? ' sm' : ''}" style="left:${q.x}px;top:${q.y ?? 300}px;opacity:${fade(q).toFixed(2)}"><div class="pt" style="background-image:url(${q.data || ''})"><div class="pn">${q.tag || q.name}</div></div>
      <div class="pb"><div class="nm">${q.name}</div><div class="rl">${q.role || ''}</div>${q.note ? `<div class="nt">${q.note}</div>` : ''}${SHOT.scope ? scopeHTML(q, v) : ''}</div></div>`;
  }).join('');
  const tq = SHOT.titleCard && v >= SHOT.titleCard.from && v <= SHOT.titleCard.to ? SHOT.titleCard : null;
  const titleHTML = tq ? `<div class="tc panel" style="opacity:${fade(tq, 0.7).toFixed(2)}">${tq.k ? `<div class="k">${tq.k}</div>` : ''}<div class="ti">${tq.title}</div>${tq.sub ? `<div class="su">${tq.sub}</div>` : ''}</div>` : '';
  const endHTML = end ? (() => {
    const E = SHOT.endCard, a = clamp((v - E.from) / 0.8, 0, 1), sb = subsLeft(0, g), sr = subsLeft(1, g);
    const cells = (G.vp || []).filter(([h, , o0]) => vpOwner(h, o0, g) === 0).length;
    // v7: the official result (the game's end page items, objectives x3), written per game in the shot table
    // (`finalScores[run]`); the referee's live share (score.jsonl share0) can count a contested objective differently
    if (lossEnd) return `<div class="end panel e7 el" style="opacity:${a.toFixed(2)}"><div class="k">${E.kicker || `战斗结束 · ${hhmm(G.T)}`}</div>
      <div class="ti">${E.title || '双方损失'}</div>
      <div class="grid"><div class="c"><div class="l">蓝军损失子单位</div><div class="v num" style="color:#9cc3f2">${TOTAL[0] - sb}</div><div class="s">开局 ${TOTAL[0]} · 兵力剩 ${fb.toFixed(1)}%</div></div>
      <div class="c"><div class="l">红军损失子单位</div><div class="v num" style="color:#f2a497">${TOTAL[1] - sr}</div><div class="s">开局 ${TOTAL[1]} · 兵力剩 ${fr.toFixed(1)}%</div></div></div>${E.note ? `<div class="note">${E.note}</div>` : ''}</div>`;
    if (V7) { const fs = (SHOT.finalScores || {})[SHOT.run]; if (!fs) throw new Error(`shot.finalScores has no entry for run ${SHOT.run}`);
      return `<div class="end panel e7${E.low ? ' el' : ''}" style="opacity:${a.toFixed(2)}"><div class="k">${E.kicker || '终局'}</div>
      <div class="ti num"><span style="color:#9cc3f2">蓝军 ${fs[0].toFixed(1)}%</span> : <span style="color:#f2a497">红军 ${fs[1].toFixed(1)}%</span></div>
      <div class="grid"><div class="c"><div class="l">蓝军兵力</div><div class="v num" style="color:#9cc3f2">${fb.toFixed(1)}%</div></div>
      <div class="c"><div class="l">红军兵力</div><div class="v num" style="color:#f2a497">${fr.toFixed(1)}%</div></div></div>${E.note ? `<div class="note">${E.note}</div>` : ''}</div>`; }
    return `<div class="end panel" style="opacity:${a.toFixed(2)}"><div class="k">终局 · ${hhmm(G.T)}</div><div class="ti">${E.title}</div>
      <div class="grid"><div class="c"><div class="l">蓝军兵力</div><div class="v num" style="color:#9cc3f2">${fb.toFixed(1)}%</div><div class="s">子单位 ${sb} / ${TOTAL[0]}</div></div>
      <div class="c"><div class="l">红军兵力</div><div class="v num" style="color:#f2a497">${fr.toFixed(1)}%</div><div class="s">子单位 ${sr} / ${TOTAL[1]}</div></div>
      <div class="c"><div class="l">目标格</div><div class="v num">${cells} / ${(G.vp || []).length}</div><div class="s">蓝军控制</div></div>
      <div class="c"><div class="l">蓝军份额</div><div class="v num" style="color:#e8c14d">${shareTxt}%</div><div class="s">${V6 ? '裁判计分' : '裁判计分（目标格 ×3）'}</div></div></div>
      <div class="note">${E.note}</div>${V5 && SHOT.credits ? `<div class="cred">${SHOT.credits}</div>` : ''}</div>`;
  })() : '';
  return `<div class="ui${SHOT.hud === false ? ' nohud' : ''}${SHOT.staging ? ' stg2' : ''}">
  <div class="top panel${TOPFLAGS ? ' fl' : ''}">${TOPFLAGS ? `<div class="flags">${TOPFLAGS.map(d => `<i style="background-image:url(${d})"></i>`).join('')}</div>` : ''}
    <div class="title">${M.title}<span class="dot">·</span><span class="b">蓝军 ${M.blue}</span> / <span class="r">红军 ${M.red}</span></div>
    ${SHOT.staging ? '' : '<div class="chip b">蓝军</div><div class="chip r">红军</div>'}
    <div class="lbl l">游戏时间</div><div class="lbl r">${speed > 0 ? `回放 <span class="num">×${speed}</span>` : v < ((SHOT.endCard && SHOT.endCard.from) || 1e9) - 1 ? '定格' : '终局'}</div>
    <div class="clock num">${hhmm(g)}</div>
  </div>
  <div class="share"${lossEnd ? ' style="visibility:hidden"' : ''}>
    <div class="bar"><div class="bf" style="width:${shareTxt}%"></div><div class="rf" style="width:${red}%"></div><div class="tick" style="left:50%;margin-left:-1px"></div></div>
    <div class="t l">蓝军份额 <span class="num">${shareTxt}%</span></div>${V6 ? '' : '<div class="t c">裁判计分：兵力 + 目标格（×3）</div>'}<div class="t r"><span class="num">${red}%</span> 红军份额</div>
  </div>
  <div class="side panel" style="${V4 ? (panelOn(v, 'force') > 0 ? slide(panelOn(v, 'force'), -1) : 'display:none') : ''}">
    ${V6 && FOG == null ? '' : `<div class="ref"><div class="eye"><i></i></div><b>${FOG == null ? '裁判视角' : FOG === 0 ? '蓝军视角' : '红军视角'}</b>`}${V6 && FOG == null ? '' : `<span>${FOG == null ? '全图 · 无迷雾' : V6 ? '' : V5 ? `迷雾：己方 ${FOG_R} km 内可见` : `迷雾（近似：己方 ${FOG_R} km 内可见）`}</span></div>`}
    <div class="hd">兵力对比</div>
    <div class="force"><div class="f b"><div class="n num">${fb.toFixed(1)}%</div><div class="s">蓝军${V6 ? '' : ` · 子单位 ${subsLeft(0, g)}/${TOTAL[0]}`}</div></div>
      <div class="f r"><div class="n num">${fr.toFixed(1)}%</div><div class="s">红军${V6 ? '' : ` · 子单位 ${subsLeft(1, g)}/${TOTAL[1]}`}</div></div></div>
    ${V6 ? '' : chartSVG(g)}
    <div class="hd">目标格（分值）</div>
    <div class="vps">${chips}</div>
  </div>
  ${V4 ? cardV4(v, g, end) : m && !end ? cardHTML(m, g) : ''}
  ${feed && !end ? `<div class="feed">${feed}</div>` : ''}
  ${dimHTML}${boardsHTML(v)}${peopleHTML}${titleHTML}${intro}${banner}${versus}${endHTML}
  ${orderHTML && !end ? orderHTML : ''}
  ${nr && !end ? (SHOT.staging ? `<div class="narr2">${nr.speaker ? `<div class="sp">${nr.speaker}</div>` : ''}<div class="tx">${nr.text}</div></div>`
    : `<div class="narr"><span>${nr.speaker ? `<span class="sp">${nr.speaker}</span>` : ''}${nr.text}</span></div>`) : ''}
  ${cap && !end ? `<div class="cap"><span>${cap.text}</span></div>` : ''}
  ${V4 ? (slideAlpha(v) >= 0.99 || (SYM && SYM.hidesMapHtml(v)) ? '' : `<div style="opacity:${(1 - slideAlpha(v)).toFixed(2)}">${v4Extras(v, g, end)}</div>`) : `<div class="legend panel"><span><span class="star">★</span>目标格</span><span><span class="sw" style="background:${BLUE}"></span>蓝军</span><span><span class="sw" style="background:${RED}"></span>红军</span><span><span class="ln" style="background:#7fb0f0"></span>来路</span><span><span class="ln" style="background:repeating-linear-gradient(90deg,#f08070 0 6px,transparent 6px 10px)"></span>命令去向</span><span>−n 子单位损失</span></div>`}
  ${V5 ? '' : `<div class="attr">${SHOT.attribution || ''}</div>`}
</div>`;
}
// 11-08 phone pass (research/ui-scale-benchmark): the subtitles are drawn about 2x larger (52 px, was 25 / 30), so a long line is shown as
// consecutive one-row chunks the way high-view videos do: at most `max` characters (CJK 1, Latin / digits 0.55), cut after a
// sentence end first, then after a comma / colon; the line's [from, to] is shared out by characters. Same words, same timing.
function subChunks(n, max) {
  const text = n.text || '', wt = s => [...s].reduce((a, c) => a + (c.charCodeAt(0) > 255 ? 1 : 0.55), 0);
  if (!max || wt(text) <= max) return [n];
  // clauses: up to and with a 。！？；… or ，、： (closing quotes / brackets stay with it); an ASCII comma ("(17,16)") never cuts
  const clauses = text.match(/[^。！？；…，、：]+(?:[。！？；…，、：]+[”’」』）]*)?|[。！？；…，、：]+[”’」』）]*/g) || [text];
  const hard = s => {                                // a clause longer than max: cut near max, not inside a Latin / digit run
    const out = []; let rest = [...s];
    while (wt(rest.join('')) > max) {
      let k = 0, w = 0; while (k < rest.length && w + (rest[k].charCodeAt(0) > 255 ? 1 : 0.55) <= max) { w += rest[k].charCodeAt(0) > 255 ? 1 : 0.55; k++; }
      const asc = c => c && /[\x21-\x7e]/.test(c);
      let j = k; while (j > k / 2 && asc(rest[j - 1]) && asc(rest[j])) j--;
      if (j <= k / 2) j = k;
      out.push(rest.slice(0, j).join('')); rest = rest.slice(j);
    }
    if (rest.length) out.push(rest.join(''));
    return out;
  };
  const pieces = clauses.flatMap(c => wt(c) > max ? hard(c) : [c]), chunks = [];
  for (const p of pieces) {
    const last = chunks[chunks.length - 1];
    // join the next clause while the chunk stays within max, unless the chunk already ends a sentence and is half full
    if (last != null && wt(last + p) <= max && !(/[。！？；…][”’」』）]*$/.test(last) && wt(last) >= max / 2)) chunks[chunks.length - 1] = last + p;
    else chunks.push(p);
  }
  // a short tail (a few characters) rides with the chunk before it
  // (only while the row still fits: max + 3 characters is the width the subtitle box has room for)
  if (chunks.length > 1 && wt(chunks[chunks.length - 1]) < 6 && wt(chunks[chunks.length - 2] + chunks[chunks.length - 1]) <= max + 3) { const t = chunks.pop(); chunks[chunks.length - 1] += t; }
  const tot = chunks.reduce((a, c) => a + wt(c), 0), span = n.to - n.from;
  let acc = 0;
  return chunks.map(c => { const f = n.from + span * acc / tot; acc += wt(c); return { ...n, text: c.trim(), from: +f.toFixed(3), to: +(n.from + span * acc / tot).toFixed(3) }; });
}
// person-card oscilloscope (SHOT.scope): a small phosphor screen under the role plate. While the card's voice speaks
// (q.on: its lines, or `talk` spans without subtitles) the trace swings with a syllable-rate envelope; otherwise a flat,
// dim line. Each voice has its own phosphor colour and wave (q.voice, default the name; SHOT.scope.voices overrides).
// Everything is a function of the video time v, so frames stay deterministic.
const SCOPE_VOICES = {
  Claude:   { c: '#ffb54a', wave: 'sine',   f: 2.2, rate: 4.1, seed: 0.13 },   // amber P3
  GPT:      { c: '#79ff9c', wave: 'square', f: 3.0, rate: 4.6, seed: 0.41 },   // green P1
  Kimi:     { c: '#ff8fb1', wave: 'saw',    f: 3.6, rate: 5.0, seed: 0.67 },
  DeepSeek: { c: '#8fd8ff', wave: 'tri',    f: 2.7, rate: 4.4, seed: 0.89 },
};
function scopeWave(kind, x) {                     // x in turns; output -1..1
  const fr = x - Math.floor(x);
  if (kind === 'square') return Math.tanh(3.2 * Math.sin(2 * Math.PI * x)) * 0.9 + 0.1 * Math.sin(6 * Math.PI * x);
  if (kind === 'saw') return (2 * fr - 1) * 0.8 + 0.2 * Math.sin(4 * Math.PI * x);
  if (kind === 'tri') return (1 - 4 * Math.abs(fr - 0.5)) * 0.85 + 0.15 * Math.sin(10 * Math.PI * x);
  return Math.sin(2 * Math.PI * x) * 0.75 + 0.25 * Math.sin(6 * Math.PI * x + 0.7);
}
function scopeHTML(q, v) {
  const S = { ...SCOPE_VOICES[q.voice || q.name], ...((SHOT.scope.voices || {})[q.voice || q.name] || {}) }, c = S.c || '#79ff9c';
  const W = 236, H = 54, n = 118, talk = (q.on || []).some(([a, b]) => v >= a && v <= b);
  const sd = S.seed || 0, env = talk ? (0.35 + 0.65 * Math.pow(Math.abs(Math.sin(Math.PI * (v * (S.rate || 4.4) + sd))), 0.7))
    * (0.7 + 0.3 * Math.sin(2 * Math.PI * (v * 0.63 + sd * 3))) : 0;
  const pts = [];
  for (let k = 0; k <= n; k++) {
    const t = k / n, taper = Math.sin(Math.PI * t);
    let y = env * taper * scopeWave(S.wave, t * (S.f || 2.5) * (1 + 0.25 * Math.sin(v * 1.3 + sd * 9)) - v * 1.7);
    y += 0.035 * Math.sin(t * 61 + v * 23 + sd * 40) * (talk ? 1 : 0.5);   // trace jitter
    pts.push(`${(t * W).toFixed(1)},${(H / 2 - y * H * 0.4).toFixed(1)}`);
  }
  const P = pts.join(' '), op = talk ? 1 : 0.45;
  let grid = '';
  for (let k = 1; k < 8; k++) grid += `<line x1="${(k * W / 8).toFixed(1)}" y1="0" x2="${(k * W / 8).toFixed(1)}" y2="${H}"/>`;
  for (let k = 1; k < 4; k++) grid += `<line x1="0" y1="${(k * H / 4).toFixed(1)}" x2="${W}" y2="${(k * H / 4).toFixed(1)}"/>`;
  return `<div class="scope${talk ? ' live' : ''}"><svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">`
    + `<g stroke="${c}" stroke-opacity=".16" stroke-width="1">${grid}</g><line x1="0" y1="${H / 2}" x2="${W}" y2="${H / 2}" stroke="${c}" stroke-opacity=".3" stroke-width="1"/>`
    + `<polyline points="${P}" fill="none" stroke="${c}" stroke-opacity="${(0.28 * op).toFixed(2)}" stroke-width="6" stroke-linejoin="round"/>`
    + `<polyline points="${P}" fill="none" stroke="${c}" stroke-opacity="${(0.55 * op).toFixed(2)}" stroke-width="3" stroke-linejoin="round"/>`
    + `<polyline points="${P}" fill="none" stroke="#fffbe8" stroke-opacity="${(0.85 * op).toFixed(2)}" stroke-width="1.1" stroke-linejoin="round"/></svg></div>`;
}
async function overlayImage(v, g) {
  const html = overlayHTML(v, g);
  if (html === overlayCache.key) return overlayCache.img;
  const img = await rasterHTML(html);
  overlayCache = { key: html, img };
  return img;
}
// a logo in front of every commander name in the overlay's text nodes (not in tags, attributes or inline svg)
function brand(html) {
  if (!LOGO_NAMES) return html;
  let depth = 0;
  return html.replace(/(<[^>]*>)|([^<]+)/g, (m, tag, txt) => {
    if (tag) { if (/^<svg\b/i.test(tag)) depth++; else if (/^<\/svg>/i.test(tag)) depth = Math.max(0, depth - 1); return tag; }
    return depth ? txt : txt.replace(LOGO_NAMES, n => `<i class="lg lg-${n.toLowerCase()}"></i>${n}`);
  });
}
async function rasterHTML(html) {
  html = brand(html);
  const svg =`<svg xmlns="http://www.w3.org/2000/svg" width="${SHOT.width}" height="${SHOT.height}"><foreignObject x="0" y="0" width="100%" height="100%"><div xmlns="http://www.w3.org/1999/xhtml"><style>${fontCss}${UI_CSS}${themeCss}</style>${html}</div></foreignObject></svg>`;
  const img = new Image(); img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
  await img.decode();
  return img;
}
// warm-up: every @font-face in the embedded CSS (the fonts come in unicode-range chunks that load lazily) drawn once with
// all its characters, so an SVG rasterization never finishes before the faces it needs are decoded (text was missing in
// the first frame of a run after the 11-06 chunked fonts)
async function warmFonts() {
  const spans = [];
  for (const m of fontCss.matchAll(/@font-face\s*{([^}]*)}/g)) {
    const b = m[1], fam = (b.match(/font-family:\s*([^;]+);/) || [])[1], wt = (b.match(/font-weight:\s*(\d+)/) || [])[1] || '400';
    const ur = (b.match(/unicode-range:\s*([^;]+);/) || [])[1]; if (!fam || !ur) continue;
    let txt = '';
    for (const part of ur.split(',')) { const r = part.trim().replace(/^U\+/i, '').split('-');
      const a = parseInt(r[0], 16), z = r[1] ? parseInt(r[1], 16) : a; if (!(a >= 0)) continue;
      for (let c = a; c <= Math.min(z, a + 400); c++) if (c > 32 && (c < 0xD800 || c > 0xDFFF)) txt += String.fromCodePoint(c); }
    spans.push(`<span style="font-family:${fam};font-weight:${wt}">${txt.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}</span>`);
  }
  const html = `<div style="position:absolute;left:0;top:0;width:${SHOT.width}px;font-size:12px;line-height:1;color:#000;opacity:0.01;word-break:break-all">${spans.join('')}</div>`;
  for (let k = 0; k < 2; k++) await rasterHTML(html);
}

// ---------------------------------------------------------------- theme (skin) and inlined images
async function dataURL(src) {
  const url = /^(file|https?|data):/.test(src) || src.startsWith('./') ? src : 'file:///' + src.split(String.fromCharCode(92)).join('/');
  let b = await (await fetch(url)).blob();
  if (!b.type) {                                    // file:// fetches carry no type; image decoders refuse octet-stream JPEGs
    const ext = (url.split('?')[0].match(/\.([a-z0-9]+)$/i) || [])[1]?.toLowerCase();
    const T = { jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp', svg: 'image/svg+xml', gif: 'image/gif' }[ext];
    if (T) b = new Blob([b], { type: T });
  }
  return await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(b); });
}
async function buildThemeCss(t) {
  let css = '.ui{' + Object.entries(t.vars || {}).map(([k, v]) => k + ':' + v).join(';') + '}';
  for (const [cls, f] of Object.entries(t.frames || {})) if (f && f.src)
    css += `.${cls}{border-style:solid;border-width:${f.width}px;border-image:url(${await dataURL(f.src)}) ${f.slice}${f.fill ? ' fill' : ''} / ${f.width}px ${f.repeat || 'stretch'};}`;
  for (const [cls, x] of Object.entries(t.textures || {})) if (x && x.src)
    css += `.${cls}{background-image:url(${await dataURL(x.src)});background-size:${x.size || 'auto'};background-repeat:${x.repeat || 'repeat'};${x.blend ? 'background-blend-mode:' + x.blend + ';' : ''}}`;
  let extra = t.css || '';                            // {{img:<path>}} in theme css -> inlined data: URL
  const toks = [...new Set(extra.match(/\{\{img:[^}]+\}\}/g) || [])];
  for (const tk of toks) extra = extra.split(tk).join('url(' + await dataURL(tk.slice(6, -2)) + ')');
  return css + extra;
}

// v5: every hex in shot-list text is written (col,row): 4-digit / 3-digit hex numbers inside prose are rewritten
function fixCoords(str) {
  return str.replace(/(^|[^0-9A-Za-z:.\/\-(,])(\d{3,4})(?![0-9:.%,)])/g, (all, pre, num) => { const n = +num, c = Math.floor(n / 100), r = n % 100;
    return c >= 1 && c <= T.cols && r >= 1 && r <= T.rows ? pre + `(${c},${r})` : all; });
}
function fixShotCoords(o) {
  const KEYS = new Set(['title', 'sub', 'text', 'quote', 'attackerText', 'defenderText', 'note', 'kicker']);
  const walk = x => { if (Array.isArray(x)) { x.forEach((y, k) => { if (typeof y === 'string' && x.length === 2 && k === 1) x[k] = fixCoords(y); else walk(y); }); return; }
    if (x && typeof x === 'object') for (const [k, y] of Object.entries(x)) { if (typeof y === 'string' && KEYS.has(k)) x[k] = fixCoords(y); else if (typeof y === 'object') walk(y); } };
  for (const k of ['moments', 'panels', 'captions', 'orders', 'steps', 'endCard', 'intro']) walk(o[k]);
}

// ---------------------------------------------------------------- public API
async function init(opts) {
  SHOT = (await import(opts.shot || './examples/shot-example.js')).default; FPS = SHOT.fps;
  // the match scores some maps' objectives at a multiple of the scenario's value (Minden x3): show what the referee counts
  if (SHOT.vpMul && G.vp) for (const r of G.vp) r[1] *= SHOT.vpMul;
  V9 = !!SHOT.v9; V8 = !!SHOT.v8 || V9; V7 = !!SHOT.v7 || V8; V6 = !!SHOT.v6 || V7; V5 = !!SHOT.v5 || V6; V4 = !!SHOT.v4 || V5;
  if (V5) fixShotCoords(SHOT);
  if (SHOT.names) { const j = await (await fetch(new URL(SHOT.names, location.href))).json(); NAMES = j.units || j; }
  EXAG = SHOT.exag ?? 3.0; BASE_M = SHOT.baseM ?? 150;
  if (!SHOT.attribution && !V5) SHOT.attribution = `地形高程：Copernicus GLO-30 © DLR e.V. / Airbus（垂直 ×${EXAG}） · 示意画面`;
  const theme = (await import(opts.theme || SHOT.theme || './theme-default.js')).default;
  fontCss = await (await fetch(opts.fonts || theme.fonts || './vendor/fonts.css')).text();
  themeCss = await buildThemeCss(theme);
  const img = async src => { const im = new Image(); im.src = await dataURL(src); await im.decode(); return im; };
  if (theme.counter) {                                // HOI4-style map counters instead of the v2 flags
    const K = theme.counter;
    COUNTER = { ...K, plate: await img(K.plate), icons: {}, tinted: [null, null], iconFrame: K.iconFrame ?? 0 };
    for (const [t, src] of Object.entries(K.icons || {})) COUNTER.icons[t] = await img(src);
  }
  if (theme.engageIcon) ENGAGE_BG = await dataURL(theme.engageIcon);
  if (theme.arrow) {                                  // textured push arrows (HOI4 battle-plan arrow: body profile + double head)
    const tx = await new THREE.TextureLoader().loadAsync(await dataURL(theme.arrow.src)); tx.colorSpace = THREE.SRGBColorSpace; tx.anisotropy = 8;
    ARROW = { ...theme.arrow, tex: tx };              // fill: sRGB side colours; vertex colours are linear and get multiplied by the texture's grey
    if (theme.arrow.fill) ARROW.fillLin = theme.arrow.fill.map(h => { const c = new THREE.Color(h), k = 1 / (theme.arrow.grey ?? 0.55); return [c.r * k, c.g * k, c.b * k]; });
  }
  for (const k of ['blue', 'red']) { const C = (SHOT.commanders || {})[k]; if (C && C.img) C.data = await dataURL(C.img); }
  for (const q of SHOT.people || []) if (q.img) q.data = await dataURL(q.img);
  // the two sides' flags in the top bar's flag box (staging: compile_staging.py sets SHOT.flags from the scenario)
  TOPFLAGS = SHOT.flags ? await Promise.all(SHOT.flags.map(f => dataURL(f))) : null;
  // the two commanders' flat logos in front of their names in every overlay text (brand(); SHOT.logos false = none)
  if (SHOT.logos !== false) { const L = Object.assign({ Kimi: LOGO_DIR + 'kimi-flat.svg', DeepSeek: LOGO_DIR + 'deepseek-flat.svg', Claude: LOGO_DIR + 'claude-flat.svg', GPT: LOGO_DIR + 'gpt-flat.svg' }, SHOT.logos || {});
    themeCss += '.lg{display:inline-block;width:1.04em;height:1.04em;vertical-align:-0.16em;margin-right:0.2em;background:center/contain no-repeat}';
    for (const [n, f] of Object.entries(L)) themeCss += `.lg-${n.toLowerCase()}{background-image:url(${await dataURL(f)})}`;
    LOGO_NAMES = new RegExp(`(?<![\\w-])(${Object.keys(L).join('|')})(?![\\w-])`, 'g'); }
  // slides: full-screen stills (SHOT.slides [{ from, to, src, zoom: [z0, z1], pan: [[x0, y0], [x1, y1]] }], pan in
  // fractions of the spare width / height); boards: HTML panels (SHOT.boards [{ from, to, html, x, y, w, cls }]),
  // `{{src:<file>}}` inside the HTML becomes a data URL (images on the boards)
  SLIDES = [];
  for (const sl of SHOT.slides || []) { const im = new Image(); im.src = await dataURL(sl.src); await im.decode(); SLIDES.push({ ...sl, im }); }
  for (const b of SHOT.boards || []) {
    let h = b.html || ''; for (const m of [...h.matchAll(/\{\{src:([^}]+)\}\}/g)]) h = h.replace(m[0], await dataURL(m[1].trim()));
    b.htmlR = h;
  }
  ORDERS = []; let busy = 0;                         // commander order cards: queued so they never overlap, min 'dur' s each
  for (const o of [...(SHOT.orders || [])].sort((a, b) => vOf(a.g) - vOf(b.g))) {
    const v0 = Math.max(o.v ?? vOf(o.g), busy + 0.25), v1 = v0 + (o.dur ?? 6);   // o.v: explicit video second (segment cuts)
    if (SHOT.endCard && v1 > SHOT.endCard.from) continue;
    ORDERS.push({ ...o, v0, v1, when: o.when || hhmm(o.g) }); busy = v1;
  }
  // staging: the card cues (fpc-symbology staging) become the commander cards with portraits; the text is the commander's
  // own Chinese line (voice_cards.py `zh`) when there is one
  if (SHOT.staging) for (const q of (SHOT.stagingCues || {}).cues || []) if (q.type === 'card')
    ORDERS.push({ side: q.side, text: q.zh || q.text, v0: q.from, v1: q.to, when: q.time != null ? hhmm(q.time) : '', verb: q.kind === 'order' ? '下令' : '', kind: q.kind });
  ORDERS.sort((a, b) => a.v0 - b.v0);
  NARR = (SHOT.lines || []).map(n => ({ ...n }));     // inline subtitles (speaker + text, video seconds); a narration file overrides
  if (opts.narration) { try { const j = await (await fetch(opts.narration)).json(); NARR = (j.lines || j).map(n => ({ ...n, from: n.from ?? vOf(n.g), to: n.to ?? (n.from ?? vOf(n.g)) + (n.dur ?? 4) })); } catch (e) { console.warn('narration', e); } }
  NARR = NARR.flatMap(n => subChunks(n, SHOT.subChars ?? 26));   // 26 characters at 52 px fit one row next to the speaker tag
  for (const f of ['500 20px "Noto Sans SC"', '700 20px "Noto Sans SC"', '900 20px "Noto Sans SC"', '400 20px "Oswald"', '600 20px "Oswald"', '700 20px "Oswald"']) await document.fonts.load(f, '第二局蓝军0123');
  const cv = document.getElementById('gl');
  renderer = new THREE.WebGLRenderer({ canvas: cv, antialias: false, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(1); renderer.setSize(SHOT.width, SHOT.height, false);
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  scene = new THREE.Scene(); scene.background = BG_TABLE.clone(); scene.fog = new THREE.Fog(0x15110d, 45, 110);
  camera = new THREE.PerspectiveCamera(30, SHOT.width / SHOT.height, 0.05, 300);
  sun = new THREE.DirectionalLight(0xffdcb0, 2.6); sun.position.set(-14, 22, 9); sun.target.position.set(0, 0, 0);
  sun.castShadow = true; sun.shadow.mapSize.set(4096, 4096);
  Object.assign(sun.shadow.camera, { left: -15, right: 15, top: 12, bottom: -12, near: 1, far: 70 }); sun.shadow.bias = -0.0004; sun.shadow.normalBias = 0.02;
  scene.add(sun, sun.target);
  scene.add(new THREE.HemisphereLight(0xcfd9ee, 0x5a4630, 0.75));
  const fill = new THREE.DirectionalLight(0x9fb4d8, 0.35); fill.position.set(12, 8, -10); scene.add(fill);
  // sky dome for close-ups (gradient, no fog)
  { const c = canvas(4, 256), x = c.getContext('2d'), gr = x.createLinearGradient(0, 0, 0, 256);
    gr.addColorStop(0, '#5d7f9f'); gr.addColorStop(0.42, '#a9bccb'); gr.addColorStop(0.5, '#c9d1d3'); gr.addColorStop(1, '#c9d1d3'); x.fillStyle = gr; x.fillRect(0, 0, 4, 256);
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
    skyDome = new THREE.Mesh(new THREE.SphereGeometry(60, 32, 16), new THREE.MeshBasicMaterial({ map: t, side: THREE.BackSide, fog: false, depthWrite: false, transparent: true, toneMapped: false }));
    skyDome.renderOrder = -1; skyDome.visible = false; scene.add(skyDome); }
  KITMOD = await import(opts.kit || SHOT.kit || './models.js'); ({ loadKit, makeVehicle, makeSoldier } = KITMOD);
  const kit = await loadKit(opts.models || SHOT.models || VIDEO + 'models/', SHOT.kitDefs);
  if (V6) {                                           // counter sprites for the full counters / close-up plates
    const dir = SHOT.tiny.plate.replace(/[^/]*$/, ''), on = await img(dir + 'onmap_unit_counter.png');
    CT6 = { plate: [tintSprite(on, 0, 66, '#8fb2ec'), tintSprite(on, 0, 66, '#ec8a7c')], barBg: await img(dir + 'mapicon_bar_bg.png'), barStr: await img(dir + 'mapicon_bar_str.png'),
      tab: await img(dir + 'onmap_unit_counter_overlay.png'), strSprite: await img(dir + 'mapicon_bar_str_sprite.png').catch(() => null) };
  }
  if (V5) {                                           // tiny counters (HOI4 interface sprites), objective flags (Kenney)
    TINY = { plate: await img(SHOT.tiny.plate), icons: {}, tinted: [null, null] }; CHIP_H = SHOT.chipBar ? 26 : 20;
    for (const [t, n] of Object.entries(TINY_ICON)) TINY.icons[t] = await img(SHOT.tiny.icons + 'onmap_unit_' + n + '_icon.png').catch(() => null);
    if (!TINY.icons.Helo) TINY.icons.Helo = await img(SHOT.tiny.icons + 'onmap_helicopter_brigade_icon.png').catch(() => null);
    FLAGS = [];
    if (V7) {                                         // HOI4 on-map victory point strip (20 frames of 29 x 27): blue / red / grey star
      const st = await img(SHOT.vpStrip), fw = st.width / 20, mkS = k => { const c = canvas(116, 108), x = c.getContext('2d'); x.imageSmoothingEnabled = true; x.imageSmoothingQuality = 'high';
        x.drawImage(st, k * fw, 0, fw, st.height, 0, 0, 116, 108); const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t; };
      VPSTAR = [mkS(1), mkS(0), mkS(4)];
    }
    for (const [k, n] of V7 ? [] : [[0, 'blue'], [1, 'red'], [2, 'green']]) {
      const gl = await loadGlb(SHOT.flagBase + (SHOT.flagFile || 'flag-') + n + '.glb'); gl.scene.updateMatrixWorld(true);
      const bb = new THREE.Box3().setFromObject(gl.scene), sz = bb.getSize(new THREE.Vector3());
      FLAGS[k] = { scene: gl.scene, c: bb.getCenter(new THREE.Vector3()), min: bb.min.clone(), s: (SHOT.flagH ?? 0.36) / sz.y };
    }
  }
  if (V6) await loadGroundV6(img);
  buildControl(); buildBoard(); const scen = V5 ? await buildSceneryV5() : buildScenery(); buildVP(); await buildCaptures(); await buildUnits(); buildFx(); if (V5) await buildFxV5(); buildSchedule();
  let axesInfo = null;
  if (V5) { const all = buildOrderGroups(); resolveSteps(all); buildMotion(); buildCraters(); if (V6) { axesInfo = buildAxes(); buildEngage(); } }
  buildMarks();
  ovlMesh = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, depthWrite: false, side: THREE.DoubleSide, toneMapped: false }));
  ovlMesh.renderOrder = 3; ovlMesh.frustumCulled = false; scene.add(ovlMesh);
  if (ARROW) { arrowMesh = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial({ map: ARROW.tex, vertexColors: true, transparent: true, depthWrite: false, side: THREE.DoubleSide, toneMapped: false }));
    arrowMesh.renderOrder = 4; arrowMesh.frustumCulled = false; scene.add(arrowMesh); }
  for (const d of SHOT.labels || []) {
    const sp = textSprite(d.text, { font: '700 40px "Noto Sans SC", sans-serif', color: '#fff6dc', bg: '#1f1b15', bgAlpha: 0.86, edge: d.side === 0 ? BLUE : d.side === 1 ? RED : '#c8a560', height: (d.unit != null ? 0.15 : 0.24) * (SHOT.mapTextScale ?? 1.6) / 1.15, pad: 18, lineH: 60, renderOrder: 25, depthTest: false });   // 11-08: x1.4
    if (d.hex) { const [x, z] = hexXZ(d.hex); sp.position.copy(W(x, z, 0.55)); }
    scene.add(sp); labelObjs.push({ def: d, sp });
  }
  rtA = new THREE.WebGLRenderTarget(SHOT.width, SHOT.height, { type: THREE.HalfFloatType, samples: 4 });
  rtB = new THREE.WebGLRenderTarget(SHOT.width, SHOT.height, { type: THREE.HalfFloatType });
  postScene = new THREE.Scene(); postCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const tri = new THREE.BufferGeometry(); tri.setAttribute('position', new THREE.Float32BufferAttribute([-1, -1, 0, 3, -1, 0, -1, 3, 0], 3)); tri.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 2, 0, 0, 2], 2));
  matH = postMaterial('h', false); matV = postMaterial('v', true);
  postScene.add(new THREE.Mesh(tri, matH)); postScene.userData.quad = postScene.children[0];
  composite = document.createElement('canvas'); composite.width = SHOT.width; composite.height = SHOT.height; cctx = composite.getContext('2d');
  // warm-up: the first SVG rasterization of the overlay can run before its embedded fonts are decoded (text missing in
  // the first frame of a run, seen 11-06); rasterize twice and drop the cache so frame 0 is drawn with fonts ready
  try { await document.fonts.ready; await warmFonts(); await overlayImage(0, gOf(0)); } catch (e) { console.warn('overlay warm-up', e); }
  overlayCache = { key: '', img: null };
  if (SHOT.symbology) {                               // the original game's symbols over the board (see fpc-symbology.js)
    const { initFPC } = await import('./fpc-symbology.js');
    SYM = await initFPC({ THREE, SHOT, G, T, FPS, W, hexXZ, neighbours, MAP_W, MAP_H, A, HX, CORNER, MID, H0, hhmm, gOf, vOf, rowAt, unitState, vpOwner, scoreAt, dataURL,
      lossSchedule, shotSchedule, BARRAGE, scene, unitZh, layers: () => ({ units, arrowMesh, ovlMesh, vpObjs, FX5, fx, labelObjs, marks, wreckPool }),
      // screen boxes of the HTML panels up at (v, g) that map labels keep off (staging, 11-08): the kill feed (3 rows at most,
      // 1.5 x 42 px each, 540 px wide, bottom 196) and the commander card (1.3 x ~540 x 340 px, bottom 196); sizes as in
      // theme-hoi4-v9.js .stg2 .feed / .order
      panels: (v, g) => { const out = [], Wd = SHOT.width, H = SHOT.height;
        const n = Math.min(3, lossSchedule.filter(l => l.g <= g && v - vOf(l.g) < 9).length);
        if (n) out.push({ x: Wd - 18 - 540, y: H - 196 - n * 63, w: 540, h: n * 63 });
        if (ORDERS.some(o => v >= o.v0 && v <= o.v1)) out.push({ x: 18, y: H - 196 - 440, w: 702, h: 440 });
        return out; } });
  }
  const gl = renderer.getContext(), dbg = gl.getExtension('WEBGL_debug_renderer_info');
  return { axes: axesInfo, engage: V6 ? ENGOFF.size : null, motion: V5 ? motionStats() : null, groups: V5 ? ORDER_GROUPS.length : null, cycles: V5 ? CYCLES.length : null, gpu: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : '?', frames: Math.round(SHOT.duration * FPS), fps: FPS, width: SHOT.width, height: SHOT.height, keyframes: SHOT.keyframes, units: NU, losses: lossSchedule.length, shots: shotSchedule.length, scenery: scen, h0: H0, T: G.T, kit };
}

function slideAlpha(v) {                         // how much a full-screen slide covers the map (map labels fade with it)
  let a = 0; for (const sl of SLIDES) if (v >= sl.from && v <= sl.to) a = Math.max(a, clamp(Math.min((v - sl.from) / (sl.fade ?? 0.5), (sl.to - v) / (sl.fade ?? 0.5)), 0, 1));
  return a;
}
function drawSlides(v) {
  for (const sl of SLIDES) {
    if (v < sl.from || v > sl.to) continue;
    const a = clamp(Math.min((v - sl.from) / (sl.fade ?? 0.5), (sl.to - v) / (sl.fade ?? 0.5)), 0, 1), t = smooth(clamp((v - sl.from) / (sl.to - sl.from), 0, 1));
    const Wd = SHOT.width, H = SHOT.height, im = sl.im, z = lerp((sl.zoom || [1.02, 1.1])[0], (sl.zoom || [1.02, 1.1])[1], t);
    const k = Math.max(Wd / im.width, H / im.height) * z, w = im.width * k, h = im.height * k;
    const P = sl.pan || [[0.5, 0.5], [0.5, 0.5]], px = lerp(P[0][0], P[1][0], t), py = lerp(P[0][1], P[1][1], t);
    cctx.save(); cctx.globalAlpha = a; cctx.fillStyle = '#000'; cctx.fillRect(0, 0, Wd, H);
    cctx.drawImage(im, -(w - Wd) * px, -(h - H) * py, w, h); cctx.restore();
  }
}
async function renderFrame(i) {
  const t0 = performance.now(), v = i / FPS, g = gOf(v);
  const c = cameraAt(v, g); CAMD = c.dist; CAMF = c.focus; CAMB = c.blur; ZOOM = clamp(1 + (c.dist - 8) / 15, 1, 2.6); MODELS = c.models; SKY = c.sky; FOG = fogAt(v); FXS = MODELS >= 0.5 ? 0.1 : 1;
  detailU.value = clamp(1.4 - c.dist / 2.5, 0, 1); nearU.value = MODELS >= 0.5 ? (V5 ? 0.3 : 0.16) : 0;
  camTarget.copy(W(c.x, c.z, 0));
  updateUnits(v, g); if (MODELS >= 0.5 && !V4) declutterPlates(); updateControl(); ctrlU.value = MODELS < 0.5 ? 1 : 0; updateFx(v, g); updateLabels(v, g); updateOverlayLines(v, g);
  if (V7) for (const im of SCEN_IMS) im.castShadow = MODELS < 0.5;   // v7: no tree shadows in close-ups (see README: black blots)
  placeCamera(c); updateVP(v, g, camera); updateCaptures(v); updateMarks(v, g); if (V5) sizeChips();
  if (V4 && MODELS >= 0.5) { camera.updateMatrixWorld(); const th = Math.tan(camera.fov * Math.PI / 360);
    for (const u of units) { const pl = u.models && u.models.plate; if (!pl || !pl.visible) continue;
      const d = camera.position.distanceTo(pl.position), h = 40 / SHOT.height * 2 * d * th; pl.scale.set(h * pl.material.map.image.width / pl.material.map.image.height, h, 1); }
    declutterPlates(); }
  if (V7 && MODELS >= 0.5) declutterVpSigns();
  if (SYM) SYM.beforeRender(v, g);
  renderer.setRenderTarget(rtA); renderer.render(scene, camera);
  const quad = postScene.userData.quad;
  for (const m of [matH, matV]) { m.uniforms.focus.value = c.focus; m.uniforms.blur.value = c.blur; m.uniforms.seed.value = (i % 97) * 0.37; }
  matH.uniforms.tDiffuse.value = rtA.texture; quad.material = matH; renderer.setRenderTarget(rtB); renderer.render(postScene, postCam);
  matV.uniforms.tDiffuse.value = rtB.texture; quad.material = matV; renderer.setRenderTarget(null); renderer.render(postScene, postCam);
  if (SYM) SYM.afterRender();
  const t1 = performance.now();
  cctx.drawImage(renderer.domElement, 0, 0);
  if (SYM) { const sa = slideAlpha(v); if (sa < 0.99) { cctx.save(); cctx.globalAlpha = 1 - sa; await SYM.draw(cctx, v, g, camera, liveUnits); cctx.restore(); } }
  drawSlides(v);
  const ov = await overlayImage(v, g); cctx.drawImage(ov, 0, 0);
  const t2 = performance.now();
  stats.render_ms += t1 - t0; stats.overlay_ms += t2 - t1; stats.frames++;
  return { v, g, clock: hhmm(g), render_ms: +(t1 - t0).toFixed(1), overlay_ms: +(t2 - t1).toFixed(1), fx: [fx.trail.used, fx.flash.used, fx.text.filter(x => x.visible).length, wrecks.length] };
}

async function startEncoder(o = {}) {
  const cfg = { codec: o.codec || 'avc1.640028', width: SHOT.width, height: SHOT.height, bitrate: o.bitrate || SHOT.bitrate || 16e6, framerate: FPS,
    hardwareAcceleration: o.hw || 'prefer-hardware', avc: { format: 'avc' }, latencyMode: 'quality' };
  const hwOnly = await VideoEncoder.isConfigSupported({ ...cfg, hardwareAcceleration: 'prefer-hardware' });
  const sup = await VideoEncoder.isConfigSupported(cfg);
  if (!sup.supported) throw new Error('encoder config not supported ' + JSON.stringify(cfg));
  muxer = new window.Mp4Muxer.Muxer({ target: new window.Mp4Muxer.ArrayBufferTarget(), video: { codec: 'avc', width: SHOT.width, height: SHOT.height, frameRate: FPS }, fastStart: 'in-memory' });
  let err = null;
  encoder = new VideoEncoder({ output: (chunk, meta) => muxer.addVideoChunk(chunk, meta), error: e => { err = e; console.error('encoder', e); } });
  encoder.configure(cfg); encoder._err = () => err;
  return { config: cfg, hardware_supported: hwOnly.supported };
}
async function encodeFrame(i, base = 0) {        // base: first encoded frame (a clip of a range starts at t = 0)
  const t0 = performance.now();
  // an encoder that errors or stalls never fires 'dequeue' again: poll with a short race, fail loudly instead of hanging
  // (11-06: a 4 h silent hang in segment C was this wait)
  while (encoder.encodeQueueSize > 6) {
    if (encoder._err()) throw encoder._err();
    if (encoder.state !== 'configured') throw new Error('encoder ' + encoder.state + ' at frame ' + i);
    if (performance.now() - t0 > 30000) throw new Error('encoder stalled 30 s at frame ' + i + ', queue ' + encoder.encodeQueueSize);
    await Promise.race([new Promise(r => encoder.addEventListener('dequeue', r, { once: true })), new Promise(r => setTimeout(r, 250))]);
  }
  const vf = new VideoFrame(composite, { timestamp: Math.round((i - base) * 1e6 / FPS), duration: Math.round(1e6 / FPS) });
  encoder.encode(vf, { keyFrame: (i - base) % (FPS * 2) === 0 }); vf.close();
  stats.encode_wait_ms += performance.now() - t0;
  if (encoder._err()) throw encoder._err();
}
async function finish() {
  await Promise.race([encoder.flush(), new Promise((_, rej) => setTimeout(() => rej(new Error('encoder flush stalled 120 s')), 120000))]);
  if (encoder._err()) throw encoder._err();
  encoder.close(); muxer.finalize();
  const buf = new Uint8Array(muxer.target.buffer), CH = 4 << 20;
  for (let o = 0; o < buf.length; o += CH) {
    const part = buf.subarray(o, Math.min(buf.length, o + CH)); let s = '';
    for (let k = 0; k < part.length; k += 0x8000) s += String.fromCharCode.apply(null, part.subarray(k, k + 0x8000));
    await window.arenaWrite(btoa(s), o + CH >= buf.length);
  }
  return { bytes: buf.length, stats };
}
async function snapshot(name) {
  const blob = await new Promise(r => composite.toBlob(r, 'image/png'));
  const u8 = new Uint8Array(await blob.arrayBuffer()); let s = '';
  for (let k = 0; k < u8.length; k += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(k, k + 0x8000));
  await window.arenaSave(name, btoa(s));
  return u8.length;
}

window.SANDBOX = { init, renderFrame, startEncoder, encodeFrame, finish, snapshot, stats: () => stats };
window.SANDBOX_READY = true;
