"""P6: the match result (materials/10 2.3) from the two P5 game runs.

python -I -X utf8 tools/p6_score.py <game 1 run dir> <game 2 run dir> [--json <out.json>]

Per game: who played side 0 (<run>/match.json), the official VP shares (the game's Battle Over page, TF Alamo's
VP ratio, read by tools/fpc_endscreen.py into <run>/<relay folder>/end-result.json; the Polish share is 100 minus
it, the same split as the information center's "VP distribution" row), the attention energy each side spent
(<run>/final.json). Main score of a player = the sum of its own side's shares over the two games; B's = 200 - A's.
A difference under 2 points is a tie, broken by less energy spent over both games.
Efficiency figures (published, not scored): share per active agent-hour (the energy ledger's active rows: 1 point
per second per active agent), tokens (<run>/agents/side<N>/usage.jsonl), commands submitted (by the agent and by
its scripts, logs/queue.jsonl), script runs (logs/scripts.jsonl).
"""
import argparse
import glob
import json
import os
import sys

TIE_POINTS = 2.0


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def rows(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except OSError:
        return []


def game(run):
    match = load(os.path.join(run, "match.json"))
    found = glob.glob(os.path.join(run, "*", "end-result.json"))
    if len(found) != 1:
        raise SystemExit(f"need one end-result.json under {run}, found {len(found)}")
    off = load(found[0])
    bo = off["pages"]["end-battle-over"]["parsed"]
    if "Alamo" not in bo.get("commander", ""):
        raise SystemExit(f"{run}: the Battle Over page is not TF Alamo's: {bo.get('commander')}")
    us = float(bo["vp_ratio_pct"])
    final = load(os.path.join(run, "final.json"))
    p0 = match["side0_player"]
    players = {0: p0, 1: "B" if p0 == "A" else "A"}
    out = {"run": run, "players": players, "share": {0: us, 1: 100.0 - us}, "us_result": bo.get("result"),
           "end_result": found[0], "fog_violations": final.get("fog_violations"),
           "energy_problems": final.get("energy_problems"), "sides": {}}
    queue = rows(os.path.join(run, "logs", "queue.jsonl"))
    scripts = rows(os.path.join(run, "logs", "scripts.jsonl"))
    for s in (0, 1):
        spent = final["energy"][str(s)]["spent"]
        usage = [u for u in rows(os.path.join(run, "agents", f"side{s}", "usage.jsonl")) if "input" in u]
        sub = [q for q in queue if q.get("event") == "submitted" and q.get("side") == s]
        runs = [r for r in scripts if r.get("side") == s and r.get("script")]
        active_h = spent.get("active", 0.0) / 3600
        out["sides"][s] = {
            "player": players[s], "share": out["share"][s],
            "energy_spent": round(sum(spent.values()), 1), "energy_spent_by_type": spent,
            "active_agent_hours": round(active_h, 3),
            "share_per_active_hour": round(out["share"][s] / active_h, 1) if active_h else None,
            "tokens": {k: sum(u.get(k) or 0 for u in usage) for k in ("input", "cache_read", "output", "reasoning")},
            "model_calls": len(usage),
            "commands": {"agent": sum(1 for q in sub if q.get("source") == "agent"),
                         "script": sum(1 for q in sub if str(q.get("source", "")).startswith("script:"))},
            "script_runs": len(runs), "script_runs_failed": sum(1 for r in runs if not r.get("ok")),
        }
    return out


def score(g1, g2):
    main = {"A": 0.0, "B": 0.0}
    energy = {"A": 0.0, "B": 0.0}
    for g in (g1, g2):
        for s in (0, 1):
            p = g["players"][s]
            main[p] += g["share"][s]
            energy[p] += g["sides"][s]["energy_spent"]
    if g1["players"][0] == g2["players"][0]:
        raise SystemExit("the two games have the same player on side 0: no side swap")
    diff = main["A"] - main["B"]
    if abs(diff) >= TIE_POINTS:
        winner, how = ("A" if diff > 0 else "B"), "main score"
    elif energy["A"] != energy["B"]:
        winner, how = ("A" if energy["A"] < energy["B"] else "B"), f"tie under {TIE_POINTS:g} points: less energy spent"
    else:
        winner, how = None, "tie on main score and energy"
    return {"main_score": main, "difference_A_minus_B": round(diff, 2), "energy_spent": energy,
            "winner": winner, "decided_by": how}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("game1")
    ap.add_argument("game2")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    g1, g2 = game(a.game1), game(a.game2)
    res = {"games": [g1, g2], "result": score(g1, g2)}
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if a.json:
        with open(a.json, "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
