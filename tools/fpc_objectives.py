"""The VP locations of a scenario and who holds them, straight from the game's own files (no end-screen OCR).

    python -I -X utf8 tools/fpc_objectives.py <file.scn | file.sav> [...]   prints one JSON object per file

A .scn (the scenario) and a .sav (the game's autosave, written every few percent of the game) are zips whose member
FPC21.$$$ holds the map markers as 300-byte records of int32 (read 11-03 against the end screens of The Armored Fist,
Quietly Flows the Donau, High Sticking and Eyes Ears and Teeth):
  [0] hex   [2] owner now   [3] owner at the start (0 side 0, 1 side 1, 2 nobody)   [4] marker type
  [18] marker type * 65537 (the type again in both halves: what tells a record from other bytes)
  [33] the location's VP to side 0   [34] its VP to side 1 (they can differ: High Sticking's 1507 is worth 500 to the
       Canadians, 1,500 to the Soviets; held at the start 1,000 / 3,500 and the Soviets' 9,000 at the end add up so)
Marker types 14, 15, 16 are the VP locations (side 0's, side 1's, unclaimed at the start); 19 are line markers (two
hexes, no VP). The briefing's list can be wrong (Eyes Ears and Teeth names four 500 VP hexes, the game has six
others worth 13,500), the game's Lua GetMarkers returns nothing (materials/08, 10), so this is the source.
"owner" is the owner during the game. At the end the game settles some locations again (unclaimed ones with units near
them: ee-fr 11-03 9,500 VP to side 0 at the 100 % save, 13,500 on Battle Over; fd-fr 3,000 and 16,000); those final
owners are only on the end screens (Victory Status Section C, tools/fpc_vp_locations.py). 27 other calibration games
of 11-01..03 agree with the end screens' VP Locations (af-ai2c's last save is at 64 %: not comparable).
"""
import json
import struct
import sys
import zipfile

RECORD = 300
VP_TYPES = (14, 15, 16)


def objectives(data):
    """[{"hex", "type", "owner", "owner_at_start", "vp": [to side 0, to side 1]}] of the VP location records."""
    out = []
    for p in range(0, len(data) - RECORD + 1):
        t = struct.unpack_from("<i", data, p + 16)[0]
        if t not in VP_TYPES or struct.unpack_from("<i", data, p + 72)[0] != t * 65537:
            continue
        f = struct.unpack_from("<35i", data, p)
        if 101 <= f[0] <= 9999 and f[2] in (0, 1, 2) and f[3] in (0, 1, 2) and f[33] >= 0 and f[34] >= 0:
            out.append({"hex": f[0], "type": t, "owner": f[2], "owner_at_start": f[3], "vp": [f[33], f[34]]})
    return out


def read(path):
    with zipfile.ZipFile(path) as z:
        return objectives(z.read("FPC21.$$$"))


def held(objs, key="owner"):
    """VP each side holds in locations: a location counts its value to the side holding it."""
    out = {0: 0, 1: 0}
    for o in objs:
        if o[key] in (0, 1):
            out[o[key]] += o["vp"][o[key]]
    return out


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for path in argv:
        objs = read(path)
        print(json.dumps({"file": path, "objectives": objs, "held": held(objs), "held_at_start": held(objs, "owner_at_start")},
                         ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
