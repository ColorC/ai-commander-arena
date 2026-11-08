"""Referee data for a season 2 scenario: start.json and units.json in $ARENA_DATA/scenarios/<slug>/ (the same files as
$ARENA_DATA/scenarios/alamo-at-soest/, read by arena/referee/world.py Scenario), from the 10-30 briefing census.

    python -I -X utf8 tools/s2_scenario_data.py <census dir> <scenario name> <out dir> [official.json] [overrides.json] [<scenario>.scn]

<census dir> is a pulled fpc/remote/job-p1-briefs.ps1 run: briefs.json (one entry per scenario) and one folder per
scenario whose rec.jsonl starts with the load snapshot. start.json is the briefs.json entry; official.json (the
end-screen Victory Status tables, e.g. $ARENA_DATA/scenarios/the-armored-fist/official.json) adds official_levels. A briefing
without starting areas (The Armored Fist) gets each side's box of the opening positions of its units on the map.
overrides.json (keys of start.json, e.g. commanders; "-" for none) is applied last. With the scenario's .scn (the
game's Scenarios folder) vp_locations are the game's own (tools/fpc_objectives.py), not the briefing's: each side's at
the start, then the unclaimed ones; [hex, vp], or [hex, vp to side 0, vp to side 1] where the two differ.
units.json lists every unit of the load snapshot per side. The terrain table is separate: tools/fpc_map.py <map>.fp10.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fpc_objectives  # noqa: E402


def vp_locations(scn, names):
    """start.json's vp_locations, vp_cell_factor and source from the .scn; names: the briefing's three group names
    (side 0's, unclaimed, side 1's)."""
    groups = {0: [], 2: [], 1: []}
    for o in fpc_objectives.read(scn):
        v0, v1 = o["vp"]
        groups[o["owner_at_start"]].append([o["hex"], v0] if v0 == v1 else [o["hex"], v0, v1])
    return {"vp_locations": {names[0]: groups[0], names[1]: groups[2], names[2]: groups[1]}, "vp_cell_factor": 1,
            "vp_locations_source": f"{os.path.basename(scn)} FPC21.$$$ marker records (tools/fpc_objectives.py)"}


def main():
    census, name, out = sys.argv[1:4]
    official = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "-" else None
    overrides = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] != "-" else None
    scn = sys.argv[6] if len(sys.argv) > 6 else None
    briefs = json.load(open(os.path.join(census, "briefs.json"), encoding="utf-8"))
    rows = briefs.values() if isinstance(briefs, dict) else briefs
    start = next(r for r in rows if r.get("name") in (name, name.replace(" ", "_")))
    start = {k: v for k, v in start.items() if k != "text"}
    # the census names some scenarios by their file name (The_Moles_of_Minden); the game wants the title with spaces:
    # -Scenario "The_Moles_of_Minden" loaded another scenario (r5-minden-141702: 9th/12th Royal Lancers, 4 h) and its
    # debug log, which the relay finds by the title, never turned up
    start["name"] = start["name"].replace("_", " ")
    if official:
        o = json.load(open(official, encoding="utf-8"))
        lv = o["levels"]
        # official.json levels: per side the share ranges of the A.1 table; the referee wants per side decisive /
        # tactical / marginal win and the lowest Contested share (alamo-at-soest/start.json official_levels)
        start["official_levels"] = [[lv[s][k][0] for k in ("Decisive Win", "Tactical Win", "Marginal Win", "Contested")]
                                    for s in ("0", "1")]
        start["official_levels_source"] = f"{os.path.basename(official)}: {o.get('source', '')}"[:300]
    folder = os.path.join(census, name.replace(" ", "_"))
    snap = None
    for line in open(os.path.join(folder, "rec.jsonl"), encoding="utf-8"):
        r = json.loads(line)
        if r.get("k") == "snap":
            snap = r
            break
    if "start_areas" not in start:
        areas = []
        for side in (0, 1):
            locs = [u["s"]["location"] for u in snap["units"].values() if (u.get("s") or {}).get("side") == side
                    and u["s"].get("status") == "Active" and isinstance(u["s"].get("location"), int) and u["s"]["location"] > 0]
            cols, rows = [h // 100 for h in locs], [h % 100 for h in locs]
            areas.append([min(cols), max(cols), min(rows), max(rows)])
        start["start_areas"] = areas
        start["start_areas_source"] = "box of each side's opening unit positions (the briefing gives no starting area)"
    if scn:
        start.update(vp_locations(scn, list(start["vp_locations"])))
    if overrides:
        start.update(json.load(open(overrides, encoding="utf-8")))
    sides = {"0": [], "1": []}
    for n, u in sorted(snap["units"].items()):
        s = u.get("s") or {}
        row = {"name": n, "type": s.get("icon_type"), "parent": s.get("parent_name"), "subunits": len(u.get("u") or [])}
        if s.get("status") == "Reinforcement":
            row["arrives_later"] = True
        sides[str(s.get("side"))].append(row)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "start.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(start, f, ensure_ascii=False, indent=1)
        f.write("\n")
    with open(os.path.join(out, "units.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"source": f"load snapshot of the briefing census ({os.path.basename(census)}/{os.path.basename(folder)}"
                             f"/rec.jsonl): every unit of the scenario, per side", "sides": sides}, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"{name}: start.json ({start.get('hours')} h, map {start.get('map')}), units {len(sides['0'])} / {len(sides['1'])}"
          f"{', official levels ' + str(start['official_levels']) if 'official_levels' in start else ''}")


if __name__ == "__main__":
    main()
