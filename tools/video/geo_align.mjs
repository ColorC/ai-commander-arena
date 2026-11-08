// Find where an FPC hex map sits on real terrain: grid-search the centre of hex 0101 (and the scale) for the best
// correlation between the game's per-hex elevation (terrain TSV elev_m) and GLO-30 sampled at the hex centres.
//
//   node geo_align.mjs <dir with node_modules/geotiff> <terrain.tsv> <tile.tif> ... [--box S,N,W,E,lat0lo,lat0hi,lon0lo,lon0hi]
//
// Prints the best anchors (r = Pearson correlation, riv = mean DEM metres of the Major River hexes) and writes nothing.
// Minden (The Moles of Minden): best 0101 = 52.3242 N, 8.7710 E at scale 1.0, r = 0.957 (README).
import fs from 'node:fs'; import path from 'node:path'; import { createRequire } from 'node:module';
const argv = process.argv.slice(2), bi = argv.indexOf('--box');
const box = bi >= 0 ? argv.splice(bi, 2)[1].split(',').map(Number) : [52.05, 52.45, 8.55, 9.3, 52.29, 52.36, 8.72, 8.83];
const [modules, tsv, ...tifs] = argv;
const require = createRequire(path.join(modules, 'package.json')); const { fromArrayBuffer } = require('geotiff');
const [S0, N0, W0, E0, LA0, LA1, LO0, LO1] = box;
const tiles = [];
for (const f of tifs) {
  const buf = fs.readFileSync(f), tiff = await fromArrayBuffer(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
  const img = await tiff.getImage(), [ox, oy] = img.getOrigin(), [sx, sy] = img.getResolution(), w = img.getWidth(), h = img.getHeight();
  const c0 = Math.max(0, Math.floor((W0 - ox) / sx)), c1 = Math.min(w, Math.ceil((E0 - ox) / sx));
  const r0 = Math.max(0, Math.floor((N0 - oy) / sy)), r1 = Math.min(h, Math.ceil((S0 - oy) / sy));
  if (c1 <= c0 || r1 <= r0) continue;
  const [data] = await img.readRasters({ window: [c0, r0, c1, r1] });
  tiles.push({ data, w: c1 - c0, h: r1 - r0, lonC: ox + (c0 + 0.5) * sx, latC: oy + (r0 + 0.5) * sy, dlon: sx, dlat: sy });
}
function dem(lat, lon) {
  for (const t of tiles) {
    const fx = (lon - t.lonC) / t.dlon, fy = (lat - t.latC) / t.dlat;
    if (fx < 0 || fy < 0 || fx > t.w - 1 || fy > t.h - 1) continue;
    const x0 = Math.min(Math.floor(fx), t.w - 2), y0 = Math.min(Math.floor(fy), t.h - 2), ax = fx - x0, ay = fy - y0, k = y0 * t.w + x0, d = t.data;
    return d[k] * (1 - ax) * (1 - ay) + d[k + 1] * ax * (1 - ay) + d[k + t.w] * (1 - ax) * ay + d[k + t.w + 1] * ax * ay;
  }
  return NaN;
}
const A = 0.5 / Math.sqrt(3), KM_LAT = 111.2, KM_LON = 111.32 * Math.cos((LA0 + LA1) / 2 * Math.PI / 180);
const hexes = [];
for (const line of fs.readFileSync(tsv, 'utf8').split('\n').slice(2)) {
  const r = line.split('\t'); if (r.length < 4) continue; const h = +r[0], c = Math.floor(h / 100), rr = h % 100;
  hexes.push({ h, x: A * (1 + 1.5 * (c - 1)), z: 0.5 * (rr - 0.5 + (c % 2 === 0 ? 0.5 : 0)), e: +r[1], river: /Major River/.test(r[3]), urban: /Urban|Highrise|Industrial/.test(r[3]) });
}
const OFFS = [[0, 0], [0.12, 0], [-0.12, 0], [0.06, 0.1], [-0.06, 0.1], [0.06, -0.1], [-0.06, -0.1]];
function score(lat0, lon0, sc) {
  const g = [], d = []; let riv = 0, nr = 0;
  for (const x of hexes) {
    let s = 0, n = 0;
    for (const [ox, oz] of OFFS) { const v = dem(lat0 - ((x.z - 0.25) * sc + oz) / KM_LAT, lon0 + ((x.x - A) * sc + ox) / KM_LON); if (!isNaN(v)) { s += v; n++; } }
    if (!n) return null; const m = s / n; g.push(x.e); d.push(m); if (x.river) { riv += m; nr++; }
  }
  const mg = g.reduce((a, b) => a + b) / g.length, md = d.reduce((a, b) => a + b) / d.length;
  let sxy = 0, sxx = 0, syy = 0; for (let i = 0; i < g.length; i++) { sxy += (g[i] - mg) * (d[i] - md); sxx += (g[i] - mg) ** 2; syy += (d[i] - md) ** 2; }
  return { r: sxy / Math.sqrt(sxx * syy), riv: riv / nr, mean: md };
}
const res = [];
for (const sc of [0.94, 0.97, 1.0, 1.03, 1.06])
  for (let lat0 = LA0; lat0 <= LA1; lat0 += 0.0009) for (let lon0 = LO0; lon0 <= LO1; lon0 += 0.0015) {
    const s = score(lat0, lon0, sc); if (s) res.push({ lat0: +lat0.toFixed(4), lon0: +lon0.toFixed(4), sc, ...s });
  }
res.sort((a, b) => b.r - a.r);
console.log('best by elevation correlation'); for (const r of res.slice(0, 8)) console.log(JSON.stringify(r));
for (const sc of [0.94, 0.97, 1.0, 1.03, 1.06]) console.log('best at scale', sc, JSON.stringify(res.find(r => r.sc === sc)));
