"""P0.4: each side's picture of the enemy, from the game's debug log, checked against the autosaves.

python -I tools/fog_check.py <run dir with rec.jsonl and collected/*.sav, collected/*DEBUG*.txt>

Spotted state of a unit = how its enemies currently see it (one value per unit; FPC enum uGameGlobals.TSpottedType):
0 Undetected, 1 Detected, 2 Classified, 3 Identified, 4 Auto (not currently spotted), 5 RadarOnly, 6 SigInt,
7 EmissionsOnly. Side A's enemy picture = side B units whose state is 1-3 or 5-7, with the game's report text
("Detected: 2x vehicle(s)", "Classified: 2 x Tank", "Identified: 2 x FV107 Scimitar ...").

Live source: the debug log line written at every change,
  UpdateSpottedStepsAndReport() <unit>: Contact <100 + save index>, <state>: <report>, spotted steps N, steps M
replayed from Auto at load. Reference: the autosave replay log (Unit Spotted / EnemySpottingReport).
At three autosave times, every unit's live state is compared with the save; agreement is reported per viewing side.
"""
import collections
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fpc_save  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
STATES = {"Undetected": 0, "Detected": 1, "Classified": 2, "Identified": 3, "AutoSpot": 4, "Auto": 4,
          "RadarOnly": 5, "Radar": 5, "SigInt": 6, "EmissionsOnly": 7, "Emissions": 7}
SEEN = {1, 2, 3, 5, 6, 7}
LINE = re.compile(r"\((\d+):(\d\d):(\d\d)\) UpdateSpottedStepsAndReport\(\) (.+?): Contact (\d+), (\w+): (.*?), spotted steps")
CLOCK = re.compile(r"\((\d+):(\d\d):(\d\d)\)")


def log_changes(path):
    """[(game second, save index, unit name, state, report)] from the debug log, game second 0 = first log line."""
    out, t0 = [], None
    for line in open(path, encoding="utf-8-sig", errors="replace"):
        c = CLOCK.search(line)
        if c and t0 is None:
            t0 = int(c[1]) * 3600 + int(c[2]) * 60 + int(c[3])
        m = LINE.search(line)
        if m:
            t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) - t0
            out.append((t, int(m[5]) - 100, m[4], STATES.get(m[6], -1), m[7]))
    return out


def unit_sides(path):
    side = {}
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        if r["k"] == "snap":
            for n, u in r["units"].items():
                side[n] = u.get("s", {}).get("side")
        elif r["k"] == "unit_add":
            side[r["unit"]] = r["state"].get("s", {}).get("side")
    return side


def main(run):
    debug = glob.glob(os.path.join(run, "collected", "*DEBUG*.txt"))[0]
    changes = log_changes(debug)
    side_of = unit_sides(os.path.join(run, "rec.jsonl"))
    saves = sorted(glob.glob(os.path.join(run, "collected", "*.sav")), key=os.path.getmtime)
    parsed = [ch for ch in (fpc_save.replay(p) for p in saves) if ch]
    ch_last = max(parsed, key=fpc_save.end_time)
    name_of = {idx: n for _, idx, n, _, _ in changes}
    print(f"log: {len(changes)} state changes for {len(name_of)} units; states "
          f"{dict(collections.Counter(s for _, _, _, s, _ in changes))}")

    times = sorted({fpc_save.end_time(ch) for ch in parsed} - {0})
    picks = sorted({times[len(times) // 4], times[len(times) // 2], times[-1]}) if len(times) >= 3 else times
    total = collections.Counter()
    for T in picks:
        st = fpc_save.state_at(ch_last, T)
        live = collections.defaultdict(lambda: 4)
        for t, idx, _, s, _ in changes:
            if t <= T:
                live[idx] = s
        per = collections.Counter()
        for idx, f in st.items():
            if "Spotted" not in f:
                continue
            owner = side_of.get(name_of.get(idx))
            viewer = None if owner is None else 1 - owner
            ok = int(f["Spotted"]) == live[idx]
            per[(viewer, "n")] += 1
            per[(viewer, "ok")] += ok
            per[(viewer, "seen")] += int(f["Spotted"]) in SEEN
            per[(viewer, "vis")] += (int(f["Spotted"]) in SEEN) == (live[idx] in SEEN)
            if not ok:
                print(f"   t={T} index {idx} {name_of.get(idx)}: save {f['Spotted']} log {live[idx]}")
        for v in sorted({k[0] for k in per}, key=str):
            print(f"t={T}s viewer side {v}: {per[(v, 'ok')]}/{per[(v, 'n')]} units agree, "
                  f"{per[(v, 'seen')]} enemy units spotted")
            total[(v, "n")] += per[(v, "n")]
            total[(v, "ok")] += per[(v, "ok")]
            total[(v, "vis")] += per[(v, "vis")]
    for v in sorted({k[0] for k in total}, key=str):
        print(f"viewer side {v}: agreement {total[(v, 'ok')]}/{total[(v, 'n')]} = "
              f"{total[(v, 'ok')] / max(total[(v, 'n')], 1):.3f}; seen / not seen agreement "
              f"{total[(v, 'vis')]}/{total[(v, 'n')]} = {total[(v, 'vis')] / max(total[(v, 'n')], 1):.3f}")

    # what a side is shown about one spotted enemy unit: the game's own report text at that state
    T = picks[-1] if picks else 0
    for viewer in (0, 1):
        last = {}
        for t, idx, n, s, rep in changes:
            if t <= T and side_of.get(n) == 1 - viewer:
                last[n] = (s, rep)
        shown = [(n, s, rep[:70]) for n, (s, rep) in sorted(last.items()) if s in SEEN]
        print(f"t={T}s side {viewer} sees {len(shown)} enemy units, e.g. {shown[:3]}")


if __name__ == "__main__":
    main(sys.argv[1])
