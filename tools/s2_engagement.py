"""Did the two sides fight: per game, from the referee's own logs (11-03 check before a formal on a new scenario).

    python -I -X utf8 tools/s2_engagement.py <game attempt dir> [...]   e.g. .../s2-formal-011738/games/formal-1/a1

Per side: forces lost (100 - the referee's last force_pct), units out, combat events, and the minute of the first
combat; VP cell changes as side 0 saw them. A game "fought" when each side lost at least MIN_LOSS points of its
forces. VP cell changes alone do not count: in the 11-03 formal games East German units walked into the empty US
cells within the first hour and the rest was quiet (forces left 96.0 / 97.7 and 96.3 / 85.8); the training games
against the game's AI all lost 12 points or more on each side (87.9 / 81.3 to 70.7 / 45.8).
"""
import collections
import json
import os
import sys

MIN_LOSS = 10.0


def rows(path):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def measure(game):
    logs = os.path.join(game, "logs")
    ev, sc = rows(os.path.join(logs, "events.jsonl")), rows(os.path.join(logs, "score.jsonl"))
    if not sc:
        return None
    force = sc[-1]["force_pct"]
    out = {"game_min": sc[-1].get("sec", 0) // 60, "share0": sc[-1].get("share0"), "sides": {}}
    for s in (0, 1):
        mine = [e for e in ev if e.get("side") == s]
        combat = [e["t"] for e in mine if e.get("type") == "combat"]
        out["sides"][str(s)] = {"lost_pct": round(100 - force[str(s)], 1),
                                "units_out": sum(1 for e in mine if e.get("type") == "unit_out"),
                                "combat": len(combat), "first_combat_min": min(combat) // 60 if combat else None}
    out["vp_changes"] = [{"min": e["t"] // 60, "hex": e["hex"], "to": "side 0" if e["to"] == "you" else "side 1"}
                         for e in ev if e.get("type") == "vp_change" and e.get("side") == 0]
    out["fought"] = all(v["lost_pct"] >= MIN_LOSS for v in out["sides"].values())
    return out


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for game in argv:
        m = measure(game)
        print(game, json.dumps(m, ensure_ascii=False) if m else "no score log")


if __name__ == "__main__":
    main(sys.argv[1:])
