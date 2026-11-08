"""Map data for the battle report: terrain of Marburg and a per-game replay, as compact JSON for tools/s2_battle_maps.js.

    python -I -X utf8 tools/s2_battle_maps.py <game a1 dir> <out.json>            one game (rec.jsonl + logs/)
    python -I -X utf8 tools/s2_battle_maps.py --terrain <terrain-Marburg.tsv> <out.json>   hex grid, roads, rivers, VP cells
    python -I -X utf8 tools/s2_battle_maps.py --geometry <terrain-Marburg.tsv>   print the hex-side convention test only
    python -I -X utf8 tools/s2_battle_maps.py --scenario <rec.jsonl> <out.json>   the scenario's default start (load snapshot),
        same format as a game JSON with one moment at t = 0, no events / VP changes / score

Hex geometry (found from the data, see geometry_report): flat-top hexes, hex number = column * 100 + row, rows grow
southwards, the EVEN columns (2, 4, ...) sit half a hex lower, hex sides are numbered 1..6 starting at the north edge and
going clockwise (N, NE, SE, S, SW, NW). Of the 24 conventions (2 shifts x 6 first edges x 2 directions) this one makes
99.5 % of the road sides and 100 % of the water sides mirror-consistent across the shared edge; no other convention gets
above 49 % of the road sides or 61 % of the water sides right.

Game JSON (short keys, integers; hex = column * 100 + row, t = game seconds since the scenario's H-hour):
  id, T (last second, at most the scenario length: 14400 for Eyes Ears and Teeth, 18000 for The Moles of Minden)
  scn, h0  scenario title and H-hour as minutes after midnight (EET 07:00 = 420, Minden 03:30 = 210)
  The scenario (VP cells, length, H-hour) comes from the recording's meta record: $ARENA_DATA/scenarios/<title, lower case, spaces as
  dashes>/start.json; Eyes Ears and Teeth when the recording names none.
  vp  [[hex, VP value, start owner]] the scenario's VP cells (same as in the terrain JSON)
  u  [[name, side, icon_type, size, parent index in "pa" or -1, subunits, start hex]] one row per unit, index = unit id
  pa  parent names
  p   per unit [[t, hex, alive subunits, st]] change points only; st 0 = reinforcement not on the map yet, 1 = active, 2 = dead
  o   per unit [[t, order index in "on", destination hex or 0, target unit id or -1]] change points of the first order
  on  order names
  vc  [[t, hex, owner]] VP cell changes, owner 0 = US, 1 = Soviet, 2 = nobody (start owners are in the terrain JSON)
  ev  [[t, hex, side, kind, n, unit id]] kind 0 = in combat (recorder Combat event), 1 = rounds fired (n), 2 = subunits lost (n);
      side is the side of the unit named in the row, the enemy of a kind 2 row is the other side
  sc  [[t, US force %, Soviet force %, US share %, US VP cells, Soviet VP cells]] one row per minute
  chk replay check: snapshots compared with the diff replay
Units whose status is "Reinforcement" (not on the map yet) get st 0 in their rows, with the hex they will enter on; the row
where they arrive carries st 1. The viewer draws st 0 units with a dashed frame, or hides them (opts.reinforcements).
Terrain JSON: see terrain_json.
"""
import collections
import glob
import json
import os
import re
import sys

CLASSES = ["open / fields", "light woods / mixed cover", "heavy woods", "urban / town", "water / marsh"]
URBAN = ("Urban", "Industrial/Commercial", "Highrise")
SIDE_RE = re.compile(r"(\d):(\d+) \(([^)]*)\)")
SIDE_NAMES = ["N", "NE", "SE", "S", "SW", "NW"]
# scenario data (README): $ARENA_DATA/scenarios/<scenario>/, ARENA_DATA default <repo>/data
DATA_DIR = os.path.join(os.environ.get("ARENA_DATA") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"), "scenarios")
SCENARIO_START = os.path.join(DATA_DIR, "eyes-ears-and-teeth", "start.json")
# VP-cell owner names in start.json -> side (0 = blue / NATO, 1 = red / Soviet, 2 = nobody)
VP_OWNERS = {"11th Armored Cavalry Reg": 0, "8th Recon Bn": 1, "6 Bn Royal Regiment of Fusiliers": 0, "28 MRR": 1,
             "Unclaimed": 2}


# ---------------------------------------------------------------- hex geometry

def neighbour(col, row, d, even_down=True):
    """Hex across edge d (0 = N, 1 = NE, 2 = SE, 3 = S, 4 = SW, 5 = NW) of a flat-top hex; rows grow southwards."""
    down = (col % 2 == 0) == even_down
    if d == 0:
        return col, row - 1
    if d == 3:
        return col, row + 1
    if down:
        return {1: (col + 1, row), 2: (col + 1, row + 1), 4: (col - 1, row + 1), 5: (col - 1, row)}[d]
    return {1: (col + 1, row - 1), 2: (col + 1, row), 4: (col - 1, row), 5: (col - 1, row - 1)}[d]


def side_dir(k, first=0, step=1):
    """Compass edge (0..5) of the hex side number k (1..6) when side 1 is edge `first` and numbers run by `step`."""
    return (first + step * (k - 1)) % 6


def parse_sides(text):
    """'3:90 (Primary road,Rail road),4:80 (Secondary Road)' -> {3: ['Primary road', 'Rail road'], 4: ['Secondary Road']}"""
    return {int(m.group(1)): [x.strip() for x in m.group(3).split(",")] for m in SIDE_RE.finditer(text or "")}


def read_terrain(path):
    rows = [line.rstrip("\n").split("\t") for line in open(path, encoding="utf-8")]
    head = re.match(r"# (\S+) (\d+) x (\d+)", rows[0][0])
    hexes = {}
    for r in rows[2:]:
        r += [""] * (8 - len(r))
        hexes[int(r[0])] = {"elev": int(r[1]), "height": int(r[2]), "terrain": r[3], "road": r[4], "water": r[5],
                            "slope": r[6]}
    return head.group(1), int(head.group(2)), int(head.group(3)), hexes


def consistency(hexes, field, even_down, first, step):
    """Count sides whose neighbour across the side lists the opposite side: (mirrored, one-sided, off the map)."""
    ok = bad = off = 0
    for h, v in hexes.items():
        col, row = divmod(h, 100)
        for k in parse_sides(v[field]):
            c2, r2 = neighbour(col, row, side_dir(k, first, step), even_down)
            other = hexes.get(c2 * 100 + r2)
            if other is None:
                off += 1
            elif (k - 1 + 3) % 6 + 1 in parse_sides(other[field]):
                ok += 1
            else:
                bad += 1
    return ok, bad, off


def geometry_report(hexes):
    """Score all 24 conventions on roads and water; returns (printable lines, the chosen convention's numbers)."""
    lines, table = [], []
    for even_down in (True, False):
        for first in range(6):
            for step in (1, -1):
                row = {"even_down": even_down, "first": SIDE_NAMES[first], "step": step}
                for field in ("road", "water"):
                    ok, bad, off = consistency(hexes, field, even_down, first, step)
                    row[field] = (ok, bad, off, ok / (ok + bad) if ok + bad else 0)
                table.append(row)
    table.sort(key=lambda r: -(r["road"][3] + r["water"][3]))
    lines.append("convention: even columns down?  side 1 = ; direction   road mirrored/one-sided/off-map (share)   "
                 "water mirrored/one-sided/off-map (share)")
    for r in table[:5] + table[-1:]:
        lines.append(f"  even_down={r['even_down']!s:5} side1={r['first']:2} {'cw ' if r['step'] == 1 else 'ccw'}   "
                     f"road {r['road'][0]}/{r['road'][1]}/{r['road'][2]} ({r['road'][3]:.3f})   "
                     f"water {r['water'][0]}/{r['water'][1]}/{r['water'][2]} ({r['water'][3]:.3f})")
    return lines, table[0], table[1]


def classify(terrain):
    parts = {p.strip() for p in terrain.split(",")}
    if "Marsh" in parts or "Major Lake [water]" in parts:
        return 4
    if parts & set(URBAN):
        return 3
    if "Heavy Woods" in parts:
        return 1 if parts & {"Farm Fields", "Mixed"} else 2
    return 1 if "Mixed" in parts else 0


def side_masks(hexes, field, kinds):
    """{hex: bitmask of sides (bit k-1 = side k)} for the sides that carry one of `kinds`, made symmetric across edges."""
    masks = collections.defaultdict(int)
    for h, v in hexes.items():
        col, row = divmod(h, 100)
        for k, types in parse_sides(v[field]).items():
            if not any(t in kinds for t in types):
                continue
            masks[h] |= 1 << (k - 1)
            c2, r2 = neighbour(col, row, side_dir(k))
            if c2 * 100 + r2 in hexes:
                masks[c2 * 100 + r2] |= 1 << ((k - 1 + 3) % 6)
    return masks


def once_per_edge(masks):
    """Keep each shared edge on the lower-numbered hex only (rivers and slopes lie on the edge itself)."""
    out = {}
    for h, m in masks.items():
        col, row = divmod(h, 100)
        for k in range(1, 7):
            if not m >> (k - 1) & 1:
                continue
            c2, r2 = neighbour(col, row, side_dir(k))
            if c2 * 100 + r2 in masks and c2 * 100 + r2 < h and masks[c2 * 100 + r2] >> ((k - 1 + 3) % 6) & 1:
                m &= ~(1 << (k - 1))
        if m:
            out[h] = m
    return out


def terrain_json(tsv):
    name, cols, rows, hexes = read_terrain(tsv)
    lines, best, second = geometry_report(hexes)
    order = [c * 100 + r for c in range(1, cols + 1) for r in range(1, rows + 1)]
    pairs = lambda m: sorted([h, v] for h, v in m.items())
    start = json.load(open(os.path.join(os.path.dirname(tsv), "start.json"), encoding="utf-8"))
    vp = vp_cells(os.path.join(os.path.dirname(tsv), "start.json"))
    sec =side_masks(hexes, "road", ("Secondary Road",))
    prim = side_masks(hexes, "road", ("Primary road",))
    rail = side_masks(hexes, "road", ("Rail road",))
    out = {
        "name": name, "cols": cols, "rows": rows, "km": start["map_km"], "cls": CLASSES,
        "t": "".join(str(classify(hexes[h]["terrain"])) for h in order),
        "e": [round(hexes[h]["elev"] / 10) for h in order],
        "emin": min(v["elev"] for v in hexes.values()), "emax": max(v["elev"] for v in hexes.values()),
        "pr": pairs(prim), "sr": pairs(sec), "rl": pairs(rail),
        "st": pairs(once_per_edge(side_masks(hexes, "water", ("Stream [water]",)))),
        "rv": pairs(once_per_edge(side_masks(hexes, "water", ("Minor River [water]",)))),
        "sl": pairs(once_per_edge(side_masks(hexes, "slope", ("Steep slope",)))),
        "hw": pairs(side_masks(hexes, "road", ("Highway",))),
        "wf": [h for h in order if {p.strip() for p in hexes[h]["terrain"].split(",")}
               & {"Major River [water]", "Major Lake [water]", "Minor Lake [water]"}],
        "vp": vp, "start": start["start_areas"],
        "geom": {"flat_top": True, "even_cols_down": True, "side1": "N", "direction": "clockwise", "row1": "north",
                 "hex_flat_to_flat_m": 500,
                 "road_sides": {"mirrored": best["road"][0], "one_sided": best["road"][1], "off_map": best["road"][2],
                                "share": round(best["road"][3], 4)},
                 "water_sides": {"mirrored": best["water"][0], "one_sided": best["water"][1],
                                 "off_map": best["water"][2], "share": round(best["water"][3], 4)},
                 "runner_up_share": {"road": round(second["road"][3], 4), "water": round(second["water"][3], 4)}},
    }
    return out, lines


# ---------------------------------------------------------------- game replay

def flatten(v, prefix="", out=None):
    out = {} if out is None else out
    if isinstance(v, dict):
        for k, x in v.items():
            flatten(x, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(v, list):
        for i, x in enumerate(v, 1):
            flatten(x, f"{prefix}.{i}" if prefix else str(i), out)
    else:
        out[prefix] = v
    return out


def to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def new_unit(name, cs):
    """Replay state of one unit from a snapshot / unit_add record."""
    s = cs.get("s") or {}
    subs = cs.get("u") or []
    return {"name": name, "side": s.get("side"), "icon": s.get("icon_type") or "?", "size": s.get("size") or "",
            "parent": s.get("parent_name") or "", "loc": to_int(s.get("location")), "ustat": s.get("status"),
            "subs": [x.get("status") for x in subs],
            "ammo": {f"{i}.{j}": to_int(a.get("rounds"), None) for i, x in enumerate(subs, 1)
                     for j, a in enumerate(x.get("ammo") or [], 1)},
            "ord": {k: v for k, v in flatten(cs.get("o") or [], "o").items()}}


def unit_row(u):
    """(hex, alive subunits, st) as drawn."""
    st = 0 if u["ustat"] == "Reinforcement" else (1 if u["ustat"] == "Active" else 2)
    return u["loc"], sum(1 for x in u["subs"] if x == "Runner"), st


def order_row(u, index, order_ids):
    o = u["ord"]
    name = o.get("o.1.orders")
    if name is None:
        return None
    dest = to_int(o.get("o.1.destination_location"))
    target = index.get(o.get("o.1.target_unit"), -1)
    return order_ids.setdefault(name, len(order_ids)), dest, target


def vp_changes(path):
    """Owner changes of the VP cells: [[t, hex, owner]] from the side-0 view of logs/events.jsonl (you = US, enemy = Soviet)."""
    if not os.path.exists(path):
        return []
    seen, out = set(), []
    names = {"you": 0, "enemy": 1, "none": 2}
    for line in open(path, encoding="utf-8"):
        e = json.loads(line)
        if e.get("type") != "vp_change":
            continue
        mine = names.get(e["to"], 2)
        owner = mine if e["side"] == 0 else ({0: 1, 1: 0}.get(mine, 2))
        key = (e["t"], e["hex"])
        if key not in seen:
            seen.add(key)
            out.append([e["t"], e["hex"], owner])
    return sorted(out)


def score_series(path):
    if not os.path.exists(path):
        return []
    by_sec = {}
    for line in open(path, encoding="utf-8"):
        s = json.loads(line)
        by_sec[s["sec"]] = [s["sec"], s["force_pct"]["0"], s["force_pct"]["1"], s["share0"], s["cells"]["0"],
                            s["cells"]["1"]]
    return [by_sec[t] for t in sorted(by_sec)]


def vp_cells(start_json):
    start = json.load(open(start_json, encoding="utf-8"))
    return [[hx, value, VP_OWNERS[who]] for who, cells in start["vp_locations"].items() for hx, value in cells]


def scenario_start(rec):
    """start.json of the scenario named in the recording's meta record (EET when there is none or it is unknown)."""
    with open(rec, encoding="utf-8") as f:
        first = json.loads(f.readline() or "{}")
    title = ((first.get("scenario") or {}).get("title") or "") if first.get("k") == "meta" else ""
    path = os.path.join(DATA_DIR, title.lower().replace(" ", "-"), "start.json")
    return path if title and os.path.exists(path) else SCENARIO_START


def scenario_clock(start_json):
    start = json.load(open(start_json, encoding="utf-8"))
    hh, mm = (int(x) for x in (start.get("h_hour") or "07:00").split(":"))
    return start.get("name") or "", hh * 60 + mm, int(round(float(start.get("hours") or 4.0) * 3600))


def game_json(a1, game_id=None):
    rec = glob.glob(os.path.join(a1, "s2-*", "rec.jsonl"))
    if len(rec) != 1:
        raise SystemExit(f"{a1}: expected one s2-*/rec.jsonl, found {len(rec)}")
    units, order, index = {}, [], {}
    track, orders, order_ids, events = {}, {}, {}, []
    last_row, last_ord = {}, {}
    check = {"snapshots": 0, "units_compared": 0, "mismatches": 0, "first": []}
    parents = {}
    end_sec = 0

    def put(name, sec):
        u = units[name]
        row = unit_row(u)
        i = index[name]
        if last_row.get(name) != row:
            if track[i] and track[i][-1][0] == sec:
                track[i][-1] = [sec, *row]
            else:
                track[i].append([sec, *row])
            last_row[name] = row
        orow = order_row(u, index, order_ids)
        if orow is not None and last_ord.get(name) != orow:
            if orders[i] and orders[i][-1][0] == sec:
                orders[i][-1] = [sec, *orow]
            else:
                orders[i].append([sec, *orow])
            last_ord[name] = orow

    def add_unit(name, cs, sec):
        if name in units:
            return
        units[name] = new_unit(name, cs)
        index[name] = len(order)
        order.append(name)
        track[index[name]] = []
        orders[index[name]] = []
        put(name, sec)

    for line in open(rec[0], encoding="utf-8"):
        x = json.loads(line)
        k = x["k"]
        sec = to_int(x.get("sec"))
        end_sec = max(end_sec, sec)
        if k == "snap":
            check["snapshots"] += 1
            for name, cs in x["units"].items():
                if name not in units:
                    add_unit(name, cs, sec)
                    continue
                fresh = new_unit(name, cs)
                check["units_compared"] += 1
                if unit_row(fresh) != unit_row(units[name]):
                    check["mismatches"] += 1
                    if len(check["first"]) < 5:
                        check["first"].append([x.get("reason"), sec, name, list(unit_row(units[name])),
                                               list(unit_row(fresh))])
                fresh["ammo"] = units[name]["ammo"]
                units[name] = fresh
                put(name, sec)
        elif k == "unit_add":
            add_unit(x["unit"], x["state"], sec)
        elif k == "diff" and x.get("unit") in units:
            u = units[x["unit"]]
            fired = 0
            before = unit_row(u)[1]
            for key, v in (x.get("set") or {}).items():
                p = key.split(".")
                if key == "s.location":
                    u["loc"] = to_int(v)
                elif key == "s.status":
                    u["ustat"] = v
                elif p[0] == "u" and len(p) == 3 and p[2] == "status":
                    while len(u["subs"]) < int(p[1]):
                        u["subs"].append(None)
                    u["subs"][int(p[1]) - 1] = v
                elif p[0] == "u" and len(p) == 5 and p[2] == "ammo" and p[4] == "rounds":
                    now, old = to_int(v, None), u["ammo"].get(f"{p[1]}.{p[3]}")
                    u["ammo"][f"{p[1]}.{p[3]}"] = now
                    if now is not None and old is not None and now < old:
                        fired += old - now
                elif p[0] == "o":
                    u["ord"][key] = v
            for key in x.get("del") or []:
                u["ord"].pop(key, None)
            put(x["unit"], sec)
            i, (hx, alive, _) = index[x["unit"]], unit_row(u)
            if fired:
                events.append([sec, hx, u["side"], 1, fired, i])
            if alive < before:
                events.append([sec, hx, u["side"], 2, before - alive, i])
        elif k == "event" and x["event"].get("type") == "Combat" and x["event"].get("unit_name") in units:
            u = units[x["event"]["unit_name"]]
            events.append([sec, u["loc"], u["side"], 0, 1, index[u["name"]]])
    events.sort(key=lambda e: (e[0], e[3]))
    start_json = scenario_start(rec[0])
    scn, h0, length = scenario_clock(start_json)
    pa = sorted({units[n]["parent"] for n in order if units[n]["parent"]})
    check["by_hour_events"] = dict(sorted(collections.Counter(e[0] // 3600 for e in events).items()))
    return {
        "id": game_id or os.path.basename(os.path.dirname(os.path.abspath(a1))), "T": min(end_sec, length),
        "scn": scn, "h0": h0,
        "vp": vp_cells(start_json),
        "u":[[n, units[n]["side"], units[n]["icon"], units[n]["size"],
               pa.index(units[n]["parent"]) if units[n]["parent"] else -1, len(units[n]["subs"]), track[index[n]][0][1]]
              for n in order],
        "pa": pa,
        "p": [track[i] for i in range(len(order))],
        "o": [orders[i] for i in range(len(order))],
        "on": [n for n, _ in sorted(order_ids.items(), key=lambda kv: kv[1])],
        "vc": vp_changes(os.path.join(a1, "logs", "events.jsonl")),
        "ev": events,
        "sc": score_series(os.path.join(a1, "logs", "score.jsonl")),
        "chk": check,
    }


def scenario_json(rec):
    """The scenario's default start: the first snapshot of a briefing recording, one row per unit at t = 0."""
    snap = next(x for x in (json.loads(line) for line in open(rec, encoding="utf-8")) if x["k"] == "snap")
    units = {n: new_unit(n, cs) for n, cs in snap["units"].items()}
    start_json = scenario_start(rec)
    scn, h0, _ = scenario_clock(start_json)
    names = sorted(units, key=lambda n: (units[n]["side"], n))
    index = {n: i for i, n in enumerate(names)}
    order_ids, pa = {}, sorted({units[n]["parent"] for n in names if units[n]["parent"]})
    orders = [order_row(units[n], index, order_ids) for n in names]
    return {
        "id": "scenario-start", "T": 0, "scn": scn, "h0": h0, "vp": vp_cells(start_json),
        "u": [[n, units[n]["side"], units[n]["icon"], units[n]["size"],
               pa.index(units[n]["parent"]) if units[n]["parent"] else -1, len(units[n]["subs"]), unit_row(units[n])[0]]
              for n in names],
        "pa": pa,
        "p": [[[0, *unit_row(units[n])]] for n in names],
        "o": [[[0, *o]] if o else [] for o in orders],
        "on": [n for n, _ in sorted(order_ids.items(), key=lambda kv: kv[1])],
        "vc": [], "ev": [], "sc": [],
        "chk": {"source": os.path.abspath(rec), "snapshot_reason": snap.get("reason")},
    }


def dump(obj, path):
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return len(text.encode("utf-8"))


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) >= 2 and argv[0] == "--geometry":
        for line in geometry_report(read_terrain(argv[1])[3])[0]:
            print(line)
    elif len(argv) == 3 and argv[0] == "--terrain":
        out, lines = terrain_json(argv[1])
        print("\n".join(lines))
        print(f"wrote {os.path.abspath(argv[2])} ({dump(out, argv[2])} bytes)")
    elif len(argv) == 3 and argv[0] == "--scenario":
        out = scenario_json(argv[1])
        reserve = sum(1 for p in out["p"] if p[0][3] == 0)
        print(f"scenario start: {len(out['u'])} units, {reserve} reinforcements (st 0), off-map hex-0 units: "
              f"{[u[0] for u in out['u'] if u[6] == 0]}")
        print(f"wrote {os.path.abspath(argv[2])} ({dump(out, argv[2])} bytes)")
    elif len(argv) == 2:
        out = game_json(argv[0])
        c = out["chk"]
        print(f"{out['id']}: {len(out['u'])} units, {sum(len(p) for p in out['p'])} position rows, {len(out['ev'])} events, "
              f"{len(out['vc'])} VP changes, {len(out['sc'])} score rows, T={out['T']}; replay check {c['snapshots']} "
              f"snapshots, {c['mismatches']}/{c['units_compared']} unit mismatches {c['first']}")
        print(f"wrote {os.path.abspath(argv[1])} ({dump(out, argv[1])} bytes)")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
