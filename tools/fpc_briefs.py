"""P1 step 1: what each scenario looks like at its start, from a fpc/remote/job-p1-briefs.ps1 run.

python -I tools/fpc_briefs.py <run dir> <Scenarios dir>   writes <run dir>/briefs.json and <run dir>/briefs.md

Per scenario folder of the run: start.sav (the game's 0 % auto-save) and rec.jsonl (recorder load snapshot).
The game writes each side's mission briefing into the auto-save. H hour and duration are always there; scenarios
whose briefing uses the generated sections also carry the victory levels ("A Decisive Victory requires <b>88%</b>
...") and the VP locations held by each side at the start. The .scn.blr next to the .scn gives the module tag
(base game or a DLC). Units per side come from the load snapshot.
"""
import collections
import glob
import json
import os
import re
import sys
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
RUN16 = re.compile(rb"(?:[\x09\x20-\x7e\xa0-\xff]\x00){3,}")  # strings -el: UTF-16LE runs of Latin-1 text


def strings16(data):
    return [m.group().decode("utf-16-le") for m in RUN16.finditer(data)]


def num(s):
    return int(s.replace(",", ""))


def parse_save(path):
    text = "\n".join(strings16(zipfile.ZipFile(path).read("FPC21.$$$")))
    sides = []
    for m in re.finditer(r"H hour is at <b>(\d\d:?\d\d)</b> and you will have <b>(\d+(?:\.\d+)?) hours?</b>", text):
        sides.append({"h_hour": m.group(1), "hours": float(m.group(2)), "pos": m.start()})
    # each side briefing appears once; some saves repeat the whole briefing block, keep the first two distinct
    out = {"hours": sides[0]["hours"] if sides else None, "h_hour": sides[0]["h_hour"] if sides else None}
    levels = re.findall(r"Decisive Victory</b> requires <b>(\d+)%</b>.*?Tactical Victory</b> requires <b>(\d+)%</b>"
                        r".*?Marginal Victory</b> requires <b>(\d+)%</b>", text, re.S)
    if levels:
        out["levels"] = [[int(x) for x in lv] for lv in levels[:2]]  # [decisive, tactical, marginal] per side
    cmd = re.findall(r"You are in command of <b>(.*?)</b> which includes", text)
    if cmd:
        out["commanders"] = cmd[:2]
    area = re.findall(r"starting area is bounded by map columns (\d+) to (\d+) inclusive and map rows (\d+) to (\d+)",
                      text)
    if area:
        out["start_areas"] = [[int(x) for x in a] for a in area[:2]]
    vp = re.search(r"C\. VP Location Overview</h3>(.*?)(?:<h3>|C\. VP Location Overview)", text, re.S)
    if vp:
        groups, cur = collections.OrderedDict(), None
        for line in vp.group(1).splitlines():
            g = re.search(r"<p>(?:<b>)?(.*?) VP Locations:", line)
            if g:
                cur = g.group(1).replace(" Secured", "").strip()
                groups[cur] = []
            loc = re.search(r"location <b>(\d+)</b>, worth ([\d,]+) VPs", line)
            if loc and cur is not None:
                groups[cur].append([int(loc.group(1)), num(loc.group(2))])
        out["vp_locations"] = groups  # first group = side 0's own, then unclaimed, then side 1's
    mp = re.search(r"Map: ([^,.\n]+)[^<]{0,200}?(\d+(?:\.\d+)?) x (\d+(?:\.\d+)?) km area", text)
    if mp:
        out["map"], out["map_km"] = mp.group(1).strip(), [float(mp.group(2)), float(mp.group(3))]
    return out


def parse_snap(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("k") == "snap":
                per = collections.defaultdict(lambda: {"units": 0, "subunits": 0, "types": collections.Counter()})
                for u in r["units"].values():
                    side = u["s"].get("side")
                    per[side]["units"] += 1
                    per[side]["subunits"] += len(u.get("u") or [])
                    per[side]["types"][u["s"].get("icon_type")] += 1
                return {str(k): {"units": v["units"], "subunits": v["subunits"], "types": dict(v["types"])}
                        for k, v in sorted(per.items())}
    return None


def parse_blr(path):
    s = strings16(open(path, "rb").read())
    module = next((x for x in s if x.startswith("Flashpoint Campaigns")), "")
    module = re.sub(r"[^A-Za-z0-9]+$", "", module)  # the length prefix of the next string can trail as one char
    em = next((re.sub(r"<.*?>", "", x) for x in s if "<em>" in x or "<i><p>" in x), "")
    nations = [x for x in s if x.startswith("CW ")][:2]
    return {"module": module, "header": em.strip(), "nations": nations}


def main(run, scen_dir):
    names = {re.sub(r"[^A-Za-z0-9]+", "_", d): d for d in os.listdir(scen_dir)}
    rows = {}
    for folder in sorted(glob.glob(os.path.join(run, "*", ""))):
        safe = os.path.basename(os.path.dirname(folder))
        name = names.get(safe, safe)
        row = {"name": name}
        blr = os.path.join(scen_dir, name, name + ".scn.blr")
        if os.path.exists(blr):
            row.update(parse_blr(blr))
        if os.path.exists(os.path.join(folder, "start.sav")):
            row.update(parse_save(os.path.join(folder, "start.sav")))
        if os.path.exists(os.path.join(folder, "rec.jsonl")):
            row["sides"] = parse_snap(os.path.join(folder, "rec.jsonl"))
        rows[name] = row
    with open(os.path.join(run, "briefs.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    lines = ["| 想定 | 模块 | 时长 h | 单位 蓝/红 | 子单位 蓝/红 | 档线 蓝 D/T/M | 档线 红 D/T/M | VP 格 | 地图 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows.values():
        sd = r.get("sides") or {}
        u = "/".join(str(sd.get(k, {}).get("units", "?")) for k in ("0", "1"))
        su = "/".join(str(sd.get(k, {}).get("subunits", "?")) for k in ("0", "1"))
        lv = r.get("levels") or []
        lvs = ["/".join(map(str, x)) for x in lv] + ["-"] * (2 - len(lv))
        vp = r.get("vp_locations")
        vps = "; ".join(f"{k} {len(v)}" for k, v in vp.items()) if vp else "-"
        mod = "基础" if r.get("module") == "Flashpoint Campaigns Cold War" else (r.get("module") or "?").replace(
            "Flashpoint Campaigns Cold War - ", "")
        lines.append(f"| {r['name']} | {mod} | {r.get('hours', '?')} | {u} | {su} | {lvs[0]} | {lvs[1]} | {vps} | "
                     f"{r.get('map', '?')} |")
    with open(os.path.join(run, "briefs.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
