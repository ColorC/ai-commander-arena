"""Season 2: the formal match result (the rule in arena/agent/prompts/training.md) from a formal run's games.jsonl.

    python -I -X utf8 tools/s2_match_score.py <formal run dir> [--json <out.json>]

Per game (the last record of each formal id: corrections are appended) each side's scorecard: its own official VP
share and the losses it inflicted (its own end-screen page's Enemy Losses Inflicted, or the other side's start forces
minus what the other kept: arena/harness/scorecard.py). Main score of a player = the sum of its own side's shares
over the two games. A difference under 2 points is a tie, broken by more losses inflicted over both games. The old
tie-break (less attention energy spent, tools/p6_score.py) rewarded doing nothing: the official levels are drawn
around the starting share, so both sides idle is a tie on any scenario (11-03, materials/19).
"""
import argparse
import json
import os
import sys

TIE_POINTS = 2.0


def formal_games(run):
    last = {}
    with open(os.path.join(run, "games.jsonl"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                if r.get("kind") == "formal":
                    last[r["id"]] = r
    return [last[k] for k in sorted(last)]


def score(games):
    if len(games) != 2:
        raise SystemExit(f"{len(games)} formal games, not 2")
    main, inflicted, per_game = {}, {}, []
    for g in games:
        row = {"id": g["id"]}
        for s in ("0", "1"):
            p = g["seats"][s].rsplit("/", 1)[-1]
            card = (g.get("scorecard") or {}).get(s) or {}
            if card.get("share") is None:
                raise SystemExit(f"{g['id']}: no share for side {s}")
            main[p] = main.get(p, 0.0) + card["share"]
            inflicted[p] = inflicted.get(p, 0.0) + ((card.get("you") or {}).get("inflicted_vp") or 0.0)
            row[s] = {"player": p, "share": card["share"], "result": card.get("result"),
                      "inflicted_vp": (card.get("you") or {}).get("inflicted_vp")}
        per_game.append(row)
    if games[0]["seats"]["0"] == games[1]["seats"]["0"] or len(main) != 2:
        raise SystemExit("the two games have the same player on side 0: no side swap")
    a, b = sorted(main)
    diff = main[a] - main[b]
    if abs(diff) >= TIE_POINTS:
        winner, how = (a if diff > 0 else b), "main score"
    elif inflicted[a] != inflicted[b]:
        winner, how = (a if inflicted[a] > inflicted[b] else b), f"tie under {TIE_POINTS:g} points: more losses inflicted"
    else:
        winner, how = None, "tie on main score and losses inflicted"
    return {"games": per_game, "main_score": main, f"difference_{a}_minus_{b}": round(diff, 2),
            "inflicted_vp": inflicted, "winner": winner, "decided_by": how}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    out = score(formal_games(a.run))
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(text)


if __name__ == "__main__":
    main()
