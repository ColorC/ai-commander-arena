// Copernicus GLO-30 tiles -> height map covering an FPC hex map (default: Marburg, 53 x 33 hexes).
//
//   node dem_to_heightmap.mjs --modules <dir with node_modules/geotiff> --out <dir> [--width 1024]
//        [--cols 53 --rows 33 --lat0 50.9064 --lon0 8.7025] <tile.tif> ...
//   Minden (The Moles of Minden, 46 x 30): --cols 46 --rows 30 --lat0 52.3242 --lon0 8.7710 (README: how it was found)
//
// Writes <out>/heightmap.bin (float32 LE, metres, row-major, row 0 = north edge of the map), <out>/heightmap.json
// (size, extent, min/max, geo parameters) and <out>/heightmap-preview.png (8-bit grey, for a quick look).
// geotiff (npm, MIT, pinned 2.1.3) is installed outside the repo, e.g. $ARENA_DATA/video/node-tools.
//
// Geo alignment (facility-survey.md 2.1): flat-top hexes, column pitch 0.433 km, row pitch 0.5 km, north up, centre of
// hex 0101 at 50.9064 N, 8.7025 E. Map km: x east from the west edge of column 1, z south from the north edge;
// hex 0101 centre = (A, H/2), A = 0.5/sqrt(3) km (circumradius), H = 0.5 km (flat to flat).
// Produced using Copernicus WorldDEM-30 (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH 2014-2018 provided
// under COPERNICUS by the European Union and ESA; all rights reserved.
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { createRequire } from 'node:module';

const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); if (i < 0) return d; const v = args[i + 1]; args.splice(i, 2); return v; };
const modules = opt('--modules');
const outDir = opt('--out');
const width = +opt('--width', 1024);
const COLS = +opt('--cols', 53), ROWS = +opt('--rows', 33), LAT0 = +opt('--lat0', 50.9064), LON0 = +opt('--lon0', 8.7025);
const tifs = args;
if (!modules || !outDir || !tifs.length) { console.error('usage: see header'); process.exit(2); }
const require = createRequire(path.join(modules, 'package.json'));
const { fromArrayBuffer } = require('geotiff');

const A = 0.5 / Math.sqrt(3), H = 0.5;
const MAP_W = A * (2 + 1.5 * (COLS - 1)), MAP_H = H * (ROWS + 0.5);
const KM_LAT = 111.2, KM_LON = 111.32 * Math.cos((LAT0 - MAP_H / 2 / KM_LAT) * Math.PI / 180);   // at the map's mid latitude
const height = Math.round(width * MAP_H / MAP_W);
const lonAt = x => LON0 + (x - A) / KM_LON, latAt = z => LAT0 - (z - H / 2) / KM_LAT;
const north = latAt(0), south = latAt(MAP_H), west = lonAt(0), east = lonAt(MAP_W);

async function readTile(file) {
  const buf = fs.readFileSync(file);
  const tiff = await fromArrayBuffer(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
  const img = await tiff.getImage();
  const [ox, oy] = img.getOrigin(), [sx, sy] = img.getResolution();   // origin = corner of the first pixel
  const W = img.getWidth(), Hh = img.getHeight();
  // only the pixel window that covers the map (+2 px margin)
  const c0 = Math.max(0, Math.floor((west - ox) / sx) - 2), c1 = Math.min(W, Math.ceil((east - ox) / sx) + 2);
  const r0 = Math.max(0, Math.floor((north - oy) / sy) - 2), r1 = Math.min(Hh, Math.ceil((south - oy) / sy) + 2);
  if (c1 <= c0 || r1 <= r0) return null;
  const [data] = await img.readRasters({ window: [c0, r0, c1, r1] });
  // pixel (i, j) of the window has its centre at lon = ox + (c0 + i + 0.5) * sx, lat = oy + (r0 + j + 0.5) * sy (sy < 0)
  return { data, w: c1 - c0, h: r1 - r0, lonC: ox + (c0 + 0.5) * sx, latC: oy + (r0 + 0.5) * sy, dlon: sx, dlat: sy,
           name: path.basename(file) };
}

const tiles = (await Promise.all(tifs.map(readTile))).filter(Boolean);
const hm = new Float32Array(width * height);
let missing = 0, lo = Infinity, hi = -Infinity, sum = 0;
for (let j = 0; j < height; j++) {
  const lat = latAt((j + 0.5) / height * MAP_H);
  for (let i = 0; i < width; i++) {
    const lon = lonAt((i + 0.5) / width * MAP_W);
    let v = NaN;
    for (const t of tiles) {
      let fx = (lon - t.lonC) / t.dlon, fy = (lat - t.latC) / t.dlat;   // half-pixel seam between tiles: clamp
      if (fx < -1 || fy < -1 || fx > t.w || fy > t.h) continue;
      fx = Math.min(Math.max(fx, 0), t.w - 1); fy = Math.min(Math.max(fy, 0), t.h - 1);
      const x0 = Math.min(Math.floor(fx), t.w - 2), y0 = Math.min(Math.floor(fy), t.h - 2), ax = fx - x0, ay = fy - y0;
      const d = t.data, k = y0 * t.w + x0;
      v = d[k] * (1 - ax) * (1 - ay) + d[k + 1] * ax * (1 - ay) + d[k + t.w] * (1 - ax) * ay + d[k + t.w + 1] * ax * ay;
      break;
    }
    if (Number.isNaN(v)) missing++;
    hm[j * width + i] = v;
  }
}
for (const v of hm) if (!Number.isNaN(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); sum += v; }
const mean = sum / (hm.length - missing);
for (let k = 0; k < hm.length; k++) if (Number.isNaN(hm[k])) hm[k] = mean;

fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(path.join(outDir, 'heightmap.bin'), Buffer.from(hm.buffer));
const meta = { width, height, map_km: [MAP_W, MAP_H], min_m: lo, max_m: hi, mean_m: mean, missing_samples: missing,
  format: 'float32 LE, row-major, row 0 = north edge, sample centres at (i+0.5)/width * map width',
  geo: { hex0101_lat: LAT0, hex0101_lon: LON0, km_per_deg_lat: KM_LAT, km_per_deg_lon: KM_LON, north, south, west, east },
  tiles: tiles.map(t => t.name),
  attribution: 'produced using Copernicus WorldDEM-30 (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH ' +
    '2014-2018 provided under COPERNICUS by the European Union and ESA' };
fs.writeFileSync(path.join(outDir, 'heightmap.json'), JSON.stringify(meta, null, 1));

// 8-bit grey preview PNG
const raw = Buffer.alloc((width + 1) * height);
for (let j = 0; j < height; j++) {
  raw[j * (width + 1)] = 0;
  for (let i = 0; i < width; i++) raw[j * (width + 1) + 1 + i] = Math.round((hm[j * width + i] - lo) / (hi - lo) * 255);
}
const crcTable = new Int32Array(256).map((_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c; });
const crc = b => { let c = -1; for (const x of b) c = crcTable[(c ^ x) & 255] ^ (c >>> 8); return (c ^ -1) >>> 0; };
const chunk = (tag, data) => { const t = Buffer.concat([Buffer.from(tag), data]); const len = Buffer.alloc(4); len.writeUInt32BE(data.length); const c = Buffer.alloc(4); c.writeUInt32BE(crc(t)); return Buffer.concat([len, t, c]); };
const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4); ihdr[8] = 8; ihdr[9] = 0;
fs.writeFileSync(path.join(outDir, 'heightmap-preview.png'), Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
  chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw, { level: 9 })), chunk('IEND', Buffer.alloc(0))]));
console.log(JSON.stringify(meta, null, 1));
