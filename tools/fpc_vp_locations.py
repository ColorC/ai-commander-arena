"""Every VP location of a scenario, from the recognised text of its information center's Victory Status pages.

    python -I -X utf8 tools/fpc_vp_locations.py <run dir> [...]   prints one JSON object per run

fpc/remote/job-p0.ps1 -EndScreens scrolls the Victory Status page (end-victory-<k>.txt after tools/fpc_endscreen.py).
Section C names each location with its value and owner: "NATO Objective, 1,500/1,500 VP at 2120: NATO retains
control.", "Unclaimed Objective, 2,500/2,500 VP at 3718: Objective remains unclaimed."; Section B lists them again,
"Hex 1711 (1,000 VP)", under the owners' columns (cut off at the shot's right edge, so only the hex and value are
used). The briefing's own list can be wrong: Eyes Ears and Teeth's names four 500 VP hexes, the game has six other
locations worth 13,500 (11-03). Consecutive pages overlap, so a location read twice counts once; a location the OCR
missed on every page is missing here (formal-2 of 11-03 lost The Armored Fist's 1808), so check "total" against the
end screens' VP Locations.
"""
import glob
import json
import os
import re
import sys

OBJ = re.compile(r"(NATO|Warsaw\s*Pact|Unclaimed)\s*[0O]bjective\s*,\s*([\d,]+)\s*/\s*([\d,]+)\s*VP\s*at\s*(\d{3,4})", re.I)
HEX = re.compile(r"Hex\s*(\d{3,4})\s*\(\s*([\d,]+)\s*VP\s*\)", re.I)
OWNERS = {"nato": "NATO", "warsawpact": "Warsaw Pact", "unclaimed": "Unclaimed"}


def num(s):
    return int(s.replace(",", ""))


def parse(text):
    """{hex: {"owner", "vp", "max"}} from Section C, plus Section B's {hex: vp}."""
    text = text.replace("NAT0", "NATO").replace("NATo", "NATO")
    c = {}
    for m in OBJ.finditer(text):
        c.setdefault(int(m.group(4)), {"owner": OWNERS[re.sub(r"\s", "", m.group(1).lower())], "vp": num(m.group(2)),
                                       "max": num(m.group(3))})
    b = {int(h): num(v) for h, v in HEX.findall(text)}
    return c, b


def read_run(run):
    pages = sorted(glob.glob(os.path.join(run, "end-victory*.txt")) + glob.glob(os.path.join(run, "end-tab-victory.txt")))
    text = "\n".join(open(p, encoding="utf-8", errors="replace").read() for p in pages)
    c, b = parse(text)
    locs = {h: dict(v) for h, v in c.items()}
    for h, vp in b.items():           # in Section B only: value known, owner not
        locs.setdefault(h, {"owner": None, "vp": vp, "max": vp})
    return {"run": run, "locations": dict(sorted(locs.items())), "total": sum(v["max"] for v in locs.values()),
            "by_owner": {o: sum(v["vp"] for v in locs.values() if v["owner"] == o) for o in sorted({v["owner"] or "?" for v in locs.values()})}}


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for run in argv:
        print(json.dumps(read_run(run), ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
