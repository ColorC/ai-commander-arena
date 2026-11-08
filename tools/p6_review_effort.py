"""Where each player's effort went, for the post-match review: per phase (commander turns between exercises, inside
exercises, notes round, game 1, game 2) the wall time from the transcript and arena tool calls by category; plus
each exercise's game length and wall time. Players are told apart by the model in the transcript start record.
Usage: p6_review_effort.py <out.txt>"""
import collections
import glob
import json
import os
import sys
import pathlib

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[1] / "data")

ROOT = ARENA_DATA.as_posix() + "/runs"
PHASES = [("training", "p4-training-053735"), ("training", "p4-supplement-p1-134811"), ("notes", "p5-notes-154449"),
          ("game1", "p5-game1-145700"), ("game2", "p5-game2-155820")]
CAT = {
    "orders": {"submit_command", "withdraw_command", "end_turn", "get_queue"},
    "battlefield": {"get_unit", "list_units", "get_contacts", "get_vp", "get_summary", "get_events", "get_energy"},
    "manual+terrain": {"library_search", "library_read", "get_terrain", "get_briefing"},
    "notes": {"notes_get", "notes_put"},
    "scripts": {"put_script", "delete_script", "list_scripts"},
    "subagents": {"spawn_subagent", "check_subagents"},
    "exercises": {"request_exercise", "list_exercises"},
    "compute": {"run_compute"},
}


def who(d):
    try:
        with open(os.path.join(d, "transcript.jsonl"), encoding="utf-8") as f:
            m = json.loads(f.readline()).get("model", "")
    except (OSError, ValueError):
        return None
    return "A" if "kimi" in m else "B" if "deepseek" in m else None


def wall(d):
    ts = []
    with open(os.path.join(d, "transcript.jsonl"), encoding="utf-8") as f:
        for line in f:
            try:
                t = json.loads(line).get("ts")
            except ValueError:
                continue
            if t:
                ts.append(t)
    return (max(ts) - min(ts)) if ts else 0.0


tools = collections.defaultdict(collections.Counter)
secs = collections.Counter()
exercises = []
for phase, run in PHASES:
    for d in glob.glob(f"{ROOT}/{run}/agents/side*") + glob.glob(f"{ROOT}/{run}/ex/*/agents/side*"):
        d = d.replace("\\", "/")
        p = who(d)
        if not p:
            continue
        key = (p, "exercise" if "/ex/" in d else ("commander" if phase == "training" else phase))
        secs[key] += wall(d)
        mcp = os.path.join(d, "mcp.jsonl")
        if os.path.isfile(mcp):
            for line in open(mcp, encoding="utf-8"):
                x = json.loads(line)
                if x.get("type") == "call":
                    t = x.get("tool")
                    tools[key][next((c for c, s in CAT.items() if t in s), "other")] += 1
    for rep in glob.glob(f"{ROOT}/{run}/ex/*/exercise-report.json"):
        r = json.load(open(rep, encoding="utf-8"))
        name = os.path.basename(os.path.dirname(rep))
        exercises.append((run, name, r.get("pacing"), r.get("players"), r.get("game_clock"), round(r.get("wall_sec") or 0),
                          r.get("ended_by")))
with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as f:
    f.write("player phase: wall minutes | tool calls by category\n")
    for key in sorted(secs):
        n = sum(tools[key].values())
        f.write(f"{key[0]} {key[1]}: {secs[key] / 60:.0f} min | {n} calls {dict(tools[key].most_common())}\n")
    f.write("\nexercises (run, id, pacing, players, end clock, wall s, ended_by):\n")
    for e in sorted(exercises):
        f.write("  " + " ".join(str(v) for v in e) + "\n")
print(open(sys.argv[1], encoding="utf-8").read())
