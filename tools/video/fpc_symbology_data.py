"""Side data for the FPC symbology layer (fpc-symbology.js): what the original counters, paths and panels show that the
compact replay (data-*.js from build_data.py) leaves out, read from the game's own recording.

    python -I -X utf8 fpc_symbology_data.py --rec <game dir>/<out dir>/rec.jsonl --wakes <game dir>/logs/wakes.jsonl
                                            --data <data-*.js> --out <fpc-*.json>

rec.jsonl (the arena's Lua recorder): a `snap` (load / keyframes) with every unit's state `s`, order queue `o` and subunits
`u` (platform name, status, ammo), then `diff` rows that set / delete dotted paths ("o.1.orders", "u.3.ammo.1.rounds",
1-based), `release` rows (the commanders' order batches with the game's readback) and `event` rows.
Output (unit index = index into ARENA.game.u, times = game seconds since H-hour):
  base     scenario start as "YYYY-MM-DD HH:MM:SS" (game second 0)
  units    [{ plat: main platform (most subunits), plats: [[name, n]], nat: national_era, tr: training, n: subunits }]
  mo / rd  per unit [[t, morale]] / [[t, readiness]] change points
  am       per unit [[t, key ammo %]] (first ammo line of every subunit still running: the main weapon)
  oq       per unit [[t, [order]]] the whole order queue at every change; order = { o: name, s, e (game s), d: dest hex,
           tu: target unit index, sl: start hex, b: [type, rounds, first round s, minutes] for barrages }
  rel      [[t, side, units]] order releases (the game accepted a commander's batch)
  wake     [[t, side]] commander wakes (decision points: the arena's command cycle)
  combat   [[t, unit]] recorder Combat events
"""
import argparse
import collections
import datetime as dt
import json
import glob
import os
import re
import pathlib

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[2] / "data")

FPC = ARENA_DATA.as_posix() + "/video/fpc-original/raw/FCCW/Data"


def load_platforms():
    """SUTag -> {name, pic (Sils file), nato (NATO symbol file), mob, sut} from every nation's .xls Units sheet"""
    import xlrd
    out = {}
    for f in glob.glob(os.path.join(FPC, "*", "*.xls")):
        wb = xlrd.open_workbook(f)
        if "Units" not in wb.sheet_names():
            continue
        sh = wb.sheet_by_name("Units")
        head = [str(x) for x in sh.row_values(1)]
        col = {h: k for k, h in enumerate(head)}
        for r in range(2, sh.nrows):
            row = sh.row_values(r)
            tag = str(row[col["SUTag"]]).strip()
            if tag and tag not in out:
                out[tag] = {"name": row[col["Name"]], "pic": row[col["PICID"]], "nato": row[col["NATOPIC"]],
                            "mob": row[col["Mobility"]], "sut": row[col["SU Type"]]}
    return out


def load_units(data_path):
    txt = open(data_path, encoding="utf-8").read()
    j = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
    return [u[0] for u in j["game"]["u"]]


def setp(obj, path, val):
    """dotted 1-based path into nested dict/list state ('o.2.orders')"""
    keys = path.split(".")
    cur = obj
    for k, nxt in zip(keys[:-1], keys[1:]):
        if isinstance(cur, list):
            i = int(k) - 1
            while len(cur) <= i:
                cur.append({})
            if not isinstance(cur[i], (dict, list)):
                cur[i] = {}
            cur = cur[i]
        else:
            if k not in cur or not isinstance(cur[k], (dict, list)):
                cur[k] = [] if nxt.isdigit() else {}
            cur = cur[k]
    last = keys[-1]
    if isinstance(cur, list):
        i = int(last) - 1
        while len(cur) <= i:
            cur.append({})
        cur[i] = val
    else:
        cur[last] = val


def delp(obj, path):
    keys = path.split(".")
    cur = obj
    for k in keys[:-1]:
        cur = cur[int(k) - 1] if isinstance(cur, list) else cur.get(k)
        if cur is None:
            return
    last = keys[-1]
    if isinstance(cur, list):
        i = int(last) - 1
        if 0 <= i < len(cur):
            cur[i] = None
    else:
        cur.pop(last, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rec", required=True)
    ap.add_argument("--wakes", default="")
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    global PLAT
    PLAT = load_platforms()
    names = load_units(a.data)
    idx = {n: i for i, n in enumerate(names)}
    rows = [json.loads(l) for l in open(a.rec, encoding="utf-8")]
    state = {}
    # base time: the start_time of the orders set at scenario start (commanders' first orders start "now")
    starts = collections.Counter()
    for r in rows:
        if r.get("k") == "diff" and r.get("sec") == 0:
            for k, v in (r.get("set") or {}).items():
                if k.endswith("start_time") and isinstance(v, str) and v[:4] < "2000":
                    starts[v] += 1
    base_s = starts.most_common(1)[0][0]
    base = dt.datetime.strptime(base_s, "%Y-%m-%d %H:%M:%S")
    gs = lambda s: int((dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S") - base).total_seconds()) if s else None

    N = len(names)
    out = {"base": base_s, "units": [None] * N, "mo": [[] for _ in range(N)], "rd": [[] for _ in range(N)],
           "am": [[] for _ in range(N)], "oq": [[] for _ in range(N)], "rel": [], "wake": [], "combat": []}

    def push(lst, t, v):
        if not lst or lst[-1][1] != v:
            if lst and lst[-1][0] == t:
                lst[-1][1] = v
            else:
                lst.append([t, v])

    def key_ammo(st):
        r = m = 0
        for su in st.get("u") or []:
            if not su or su.get("status") in ("Dead", "Fallen Out"):
                continue
            am = [x for x in (su.get("ammo") or []) if x]
            if not am:
                continue
            try:
                r += float(am[0].get("rounds") or 0)
                m += float(am[0].get("max_rounds") or 0)
            except ValueError:
                pass
        return round(100 * r / m) if m else None

    def orders(st):
        q = []
        for o in st.get("o") or []:
            if not o or not o.get("orders"):
                continue
            e = {"o": o["orders"], "s": gs(o.get("start_time")), "e": gs(o.get("end_time"))}
            if o.get("destination_location"):
                e["d"] = int(o["destination_location"])
            if o.get("start_location") and o["start_location"] != "0":
                e["sl"] = int(o["start_location"])
            if o.get("target_unit") in idx:
                e["tu"] = idx[o["target_unit"]]
            if o.get("barrage_type"):
                e["b"] = [o["barrage_type"], int(o.get("barrage_rounds") or 0), gs(o.get("barrage_first_round_time")),
                          int(o.get("barrage_duration_mins") or 0)]
            q.append(e)
        return q

    def sample(name, t):
        i = idx.get(name)
        if i is None:
            return
        st = state[name]
        s = st.get("s") or {}
        if s.get("morale") is not None:
            push(out["mo"][i], t, int(s["morale"]))
        if s.get("readiness") is not None:
            push(out["rd"][i], t, int(s["readiness"]))
        ka = key_ammo(st)
        if ka is not None:
            push(out["am"][i], t, ka)
        q = orders(st)
        if not out["oq"][i] or out["oq"][i][-1][1] != q:
            if out["oq"][i] and out["oq"][i][-1][0] == t:
                out["oq"][i][-1][1] = q
            else:
                out["oq"][i].append([t, q])
        if out["units"][i] is None:
            subs = [su for su in st.get("u") or [] if su and su.get("name")]
            plats = collections.Counter(su.get("name") for su in subs)
            first = subs[0] if subs else {}
            info = PLAT.get(first.get("tag", ""), {})
            out["units"][i] = {"plat": first.get("name", ""), "tag": first.get("tag", ""), "pic": info.get("pic", ""),
                               "nato": info.get("nato", ""), "mob": info.get("mob", ""), "sut": first.get("type", ""),
                               "plats": plats.most_common(), "nat": s.get("national_era", ""), "tr": s.get("training"),
                               "n": len(subs)}

    for r in rows:
        k = r.get("k")
        t = r.get("sec", 0)
        if k == "snap":
            for name, st in r["units"].items():
                state[name] = json.loads(json.dumps(st))
                sample(name, t)
        elif k == "diff":
            name = r.get("unit")
            if name not in state:
                state[name] = {}
            for p in r.get("del") or []:
                delp(state[name], p)
            for p, v in (r.get("set") or {}).items():
                setp(state[name], p, v)
            sample(name, t)
        elif k == "release":
            side = r.get("side")
            us = [x.get("unit") for x in r.get("results") or [] if x.get("kind") == "orders"]
            out["rel"].append([t, side, len(us)])
        elif k == "event":
            e = r["event"]
            if e.get("type") == "Combat" and e.get("unit_name") in idx:
                out["combat"].append([t, idx[e["unit_name"]]])
    if a.wakes:
        for l in open(a.wakes, encoding="utf-8"):
            w = json.loads(l)
            if w.get("kind") == "wake" and not w.get("prematch"):
                out["wake"].append([w["sec"], w["side"]])
    for i, u in enumerate(out["units"]):
        if u is None:
            out["units"][i] = {"plat": "", "tag": "", "pic": "", "nato": "", "mob": "", "sut": "", "plats": [], "nat": "", "tr": None, "n": 0}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(a.out, "base", base_s, "units", N, "orders rows", sum(len(x) for x in out["oq"]), "releases", len(out["rel"]),
          "wakes", len(out["wake"]))
    for i in range(min(N, 200)):
        u = out["units"][i]
        print(i, names[i], "|", u["plat"], "|", u.get("pic"), u.get("nato"), u.get("mob"), "|", re.sub(r"CW 80s ", "", u["nat"]))


if __name__ == "__main__":
    main()
