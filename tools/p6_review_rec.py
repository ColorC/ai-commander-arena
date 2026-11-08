"""Game-side facts from a game's rec.jsonl for the post-match review: rounds actually fired per artillery unit per game
hour (per-tube ammo seeded from snap records, every decrease counted; diff keys are u.<i>.ammo.<j>.rounds, 1-based),
and subunits knocked out (Runner -> Fallen Out / Dead) per side per half hour.
Usage: p6_review_rec.py <rec.jsonl> <artillery unit names, comma separated>"""
import collections
import json
import sys

path, names = sys.argv[1], set(sys.argv[2].split(","))
ammo = collections.defaultdict(dict)
fired = collections.defaultdict(collections.Counter)
first = {}
side = {}
status = {}
lost = collections.Counter()


def clock(sec, step=3600):
    sec -= sec % step
    return f"{7 + sec // 3600:02d}:{sec % 3600 // 60:02d}"


def apply(unit, kv, sec):
    for k, v in kv.items():
        p = k.split(".")
        if len(p) == 3 and p[0] == "u" and p[2] == "status":
            key = (unit, int(p[1]))
            if status.get(key) == "Runner" and v != "Runner":
                lost[(side.get(unit), clock(sec, 1800))] += 1
            status[key] = v
        if unit not in names or not k.endswith("rounds"):
            continue
        try:
            v = int(v)
        except (TypeError, ValueError):
            continue
        old = ammo[unit].get(k)
        ammo[unit][k] = v
        if old is not None and v < old:
            fired[unit][clock(sec)] += old - v
            first.setdefault(unit, clock(sec, 60))


def flat(u):
    out = {}
    for i, sub in enumerate(u.get("u") or []):
        out[f"u.{i + 1}.status"] = sub.get("status")
        for j, a in enumerate(sub.get("ammo") or []):
            out[f"u.{i + 1}.ammo.{j + 1}.rounds"] = a.get("rounds")
    return out


sec = 0
for line in open(path, encoding="utf-8"):
    x = json.loads(line)
    sec = int(x.get("sec") or sec)
    if x.get("k") == "snap":
        for n, u in (x.get("units") or {}).items():
            side[n] = (u.get("s") or {}).get("side")
            apply(n, flat(u), sec)
    elif x.get("k") == "diff" and x.get("unit"):
        apply(x["unit"], x.get("set") or {}, sec)
print("rounds fired:")
for u in sorted(names):
    print(f"  {u}: first {first.get(u)}, by hour {dict(sorted(fired[u].items()))}, total {sum(fired[u].values())}")
print("subunits knocked out per half hour (side 0 = US, 1 = Pact):")
for s in (0, 1):
    rows = {t: n for (sd, t), n in sorted(lost.items(), key=lambda kv: kv[0][1]) if sd == s}
    print(f"  side {s}: {rows}, total {sum(rows.values())}")
