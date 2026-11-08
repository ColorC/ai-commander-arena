"""Fire and loss events per hex per game minute in a window (scratch: pick the busiest cold-open close-up).

    python -I -X utf8 hotspot.py <game.json> <t0> <t1> [top]
ev rows: [t, hex, side, kind (1 fire, 2 loss), n, unit]. Score = fire events + 3 x subunits lost.
"""
import json
import sys
from collections import defaultdict

G = json.load(open(sys.argv[1], encoding='utf-8'))
t0, t1 = int(sys.argv[2]), int(sys.argv[3])
top = int(sys.argv[4]) if len(sys.argv) > 4 else 15
h0 = G.get('h0', 210)
cell = defaultdict(lambda: [0, 0, 0, set()])      # (hex, minute) -> fire events, rounds, losses, sides
hexsum = defaultdict(lambda: [0, 0, 0])
for t, h, side, kind, n, uid in G['ev']:
    if not (t0 <= t < t1) or kind not in (1, 2):
        continue
    m = t // 60
    c = cell[(h, m)]
    if kind == 1:
        c[0] += 1
        c[1] += n
        hexsum[h][0] += 1
    else:
        c[2] += n
        hexsum[h][2] += n
    c[3].add(side)
fmt = lambda m: f"{(m + h0) // 60:02d}:{(m + h0) % 60:02d}"
print('hex-minute (fire events / rounds / subunits lost / sides):')
for (h, m), c in sorted(cell.items(), key=lambda kv: -(kv[1][0] + 3 * kv[1][2]))[:top]:
    print(f"  ({h // 100},{h % 100}) {fmt(m)}  fire {c[0]:3d}  rounds {c[1]:4d}  lost {c[2]:2d}  sides {sorted(c[3])}")
print('hex totals over the window:')
for h, s in sorted(hexsum.items(), key=lambda kv: -(kv[1][0] + 3 * kv[1][2]))[:top]:
    print(f"  ({h // 100},{h % 100})  fire {s[0]:3d}  lost {s[2]:2d}")
