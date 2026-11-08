"""P2 smoke pass/fail summary (materials/10 P2) from one tools/p2_smoke.sh run directory.

    python -I -X utf8 tools/p2_check.py <run dir>

Reads the referee's logs and final.json and the relay folder pulled into the run (relay.log, rec.jsonl) and prints
one JSON object: Orders-phase releases and F9s, real-time gate segments (game seconds per wall second), wake-up lag
(game seconds from a wake to the gate holding the game), bridge step timeouts, audit, fog and energy checks.
"""
import glob
import json
import os
import re
import sys


def jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main(run):
    relay_dirs = [os.path.dirname(f) for f in glob.glob(os.path.join(run, "*", "relay.log"))]
    relay = relay_dirs[0] if relay_dirs else None
    rec = jsonl(os.path.join(relay, "rec.jsonl")) if relay else []
    releases = jsonl(os.path.join(run, "logs", "releases.jsonl"))
    wakes = [w for w in jsonl(os.path.join(run, "logs", "wakes.jsonl")) if w.get("kind") == "wake"]
    final_path = os.path.join(run, "final.json")
    final = json.load(open(final_path, encoding="utf-8")) if os.path.exists(final_path) else None

    issued = [{"batch_id": r["batch_id"], "side": r["side"], "sec": r["sec"], "phase": r["phase"]}
              for r in releases if r.get("event") == "issued"]
    results = {r["batch_id"]: all(c.get("ok") for c in r.get("results") or []) for r in releases
               if r.get("event") == "result"}
    f9 = []
    if relay and os.path.exists(os.path.join(relay, "relay.log")):
        for line in open(os.path.join(relay, "relay.log"), encoding="utf-8-sig"):
            m = re.match(r"(\S+) F9 \((\w+) side (\d*)\)", line)
            if m:
                f9.append({"at": m.group(1), "phase": m.group(2), "side": m.group(3)})

    minutes = [(r["sec"], r["wall"]) for r in rec if r.get("k") == "minute"]
    gate_ev = [r for r in rec if r.get("k") == "gate"]
    segs, cur = [], None
    for g in gate_ev:
        if g["event"] == "mode" and g["mode"] == "sleep":
            cur = {"sec0": g["sec"], "wall0": g["wall_ms"] / 1000.0, "end_sec": None, "end": None}
            segs.append(cur)
        elif cur and (g["event"] == "released" or (g["event"] == "mode" and g["mode"] != "sleep")):
            cur["end_sec"], cur["end"] = g["sec"], g["event"]
            cur = None
    # The game moves in whole minutes and an active spell is often only one to three of them (P3 rehearsal 3: 58
    # spells), so a minute-to-minute ratio says little. The clock ratio is game seconds over wall seconds summed over
    # every gate segment, from the bridge's own anchor (gate on) to its end (released / off), both in ms; an Orders
    # stop inside a segment is caught up by the anchor. late_minutes: held minutes that arrived after their anchored
    # time plus the half-minute lead (the game could not keep up with wall time)
    orders = orders_minutes(run, relay)
    game_sum, wall_sum, late = 0, 0.0, []
    for s in segs:
        if s["end_sec"] is not None:
            end_wall = next(g["wall_ms"] / 1000.0 for g in gate_ev if g["sec"] == s["end_sec"] and g["event"] == s["end"]
                            and g["wall_ms"] / 1000.0 >= s["wall0"])
            s["game_s"], s["wall_s"] = s["end_sec"] - s["sec0"], round(end_wall - s["wall0"], 1)
            game_sum, wall_sum = game_sum + s["game_s"], wall_sum + s["wall_s"]
        for sec, wall in minutes:
            if sec > s["sec0"] and (s["end_sec"] is None or sec <= s["end_sec"]) and wall > s["wall0"] + (sec - s["sec0"]) + 31.5:
                late.append({"sec": sec, "late_s": round(wall - s["wall0"] - (sec - s["sec0"]) - 30, 1)})
        s.pop("wall0")
    pace = {"segments": len(segs), "game_s": game_sum, "wall_s": round(wall_sum, 1),
            "ratio": round(game_sum / wall_sum, 4) if wall_sum else None, "late_minutes": len(late), "late": late[:20]}

    def gate_at(sec):
        for s in segs:
            if s["sec0"] <= sec and (s["end_sec"] is None or sec < s["end_sec"]
                                     or (sec == s["end_sec"] and s["end"] == "released")):
                return s
        return None

    lags = []
    for w in wakes:
        if w.get("prematch"):
            continue
        covering = gate_at(w["sec"])
        nxt = [s["sec0"] for s in segs if s["sec0"] >= w["sec"]]
        lag = 0 if covering else (nxt[0] - w["sec"] if nxt else None)
        lags.append({"side": w["side"], "sec": w["sec"], "lag_game_s": lag})

    out = {
        "relay_dir": os.path.abspath(relay) if relay else None,
        "releases": [dict(r, ok=results.get(r["batch_id"])) for r in issued],
        "orders_releases": sum(1 for r in issued if r["phase"] == "Orders"),
        "f9": f9,
        "gate_segments": segs,
        "gate_pace": pace,
        "orders_minutes": sorted(orders),
        "wake_lags": lags,
        "max_wake_lag": max([x["lag_game_s"] for x in lags if x["lag_game_s"] is not None], default=None),
        "step_timeouts": sum(1 for g in gate_ev if g["event"] == "step_timeout"),
        "release_repeats": sum(1 for r in rec if r.get("k") == "release_repeat"),
        "bridge_errors": [r for r in rec if r.get("k") == "bridge_error"],
        "last_minute": minutes[-1][0] if minutes else None,
        "final": None if final is None else {
            "sec": final.get("sec"), "audit": final.get("audit"), "fog_violations": final.get("fog_violations"),
            "violations": final.get("violations"), "energy_problems": final.get("energy_problems")},
        "transcripts": transcripts(run),
        "end_shots": sorted(os.path.basename(p) for p in glob.glob(os.path.join(relay, "end-*.bmp"))) if relay else [],
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))


def orders_minutes(run, relay):
    """Game seconds of the Orders phase lines in the collected debug log (start clock = its first clock)."""
    paths = glob.glob(os.path.join(relay or run, "collected", "*DEBUG*")) + glob.glob(os.path.join(run, "collected", "*DEBUG*"))
    if not paths:
        return set()
    start, out = None, set()
    for line in open(paths[0], encoding="utf-8", errors="replace"):
        m = re.search(r" - \((\d\d):(\d\d):(\d\d)\)", line)
        if not m:
            continue
        c = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        start = c if start is None else start
        if "Orders Phase" in line and re.search(r"Player \d Orders Phase", line):
            out.add((c - start) % 86400)
    return out


def transcripts(run):
    """Per side and agent: model requests answered, tool calls asked for vs tool results logged."""
    out = {}
    for path in sorted(glob.glob(os.path.join(run, "agents", "side*", "transcript.jsonl"))):
        per = {}
        for r in jsonl(path):
            a = per.setdefault(r.get("agent", "-"), {"requests": 0, "responses": 0, "retries": 0, "tool_calls": 0,
                                                     "tool_results": 0, "turns": 0})
            t = r.get("type")
            if t == "request":
                a["requests"] += 1
            elif t == "response":
                a["responses"] += 1
                a["tool_calls"] += len(r.get("tool_calls") or [])
            elif t == "retry":
                a["retries"] += 1
            elif t == "tool":
                a["tool_results"] += 1
            elif t == "turn_end":
                a["turns"] += 1
        out[os.path.basename(os.path.dirname(path))] = per
    return out


if __name__ == "__main__":
    main(sys.argv[1])
