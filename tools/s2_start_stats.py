"""What a player did in one game (materials/18 section 4, start-task measures), from the game's own logs.

    python -I -X utf8 tools/s2_start_stats.py <game attempt dir> [side]

<game attempt dir> is a director game, e.g. $ARENA_DATA/runs/s2-start-065501/games/start-B/a1.
Reads agents/side<N>/mcp.jsonl (every `arena` command and its result), agents/side<N>/usage.jsonl (tokens per model
call), logs/wakes.jsonl (turns) and exercise-report.json (the official result, once the game is over). Each command is
put in one class with the CLI's own noun and verb resolution (arena/agent/cli.py):
  look   brief, score, map, unit list/show, contact, event, fire list/report, order list/show, turn status/summary
  act    order add/withdraw, fire add, unit <name> order/sop, script put/delete, agent spawn
  check  order lint, script lint/test, calc run
  docs   help, doc, any --help
  note   note
  turn   turn end
Refused: the result starts with 错误 (a refusal by the CLI or the referee; nothing was queued). Warned: queued with a lint
warning. Prints one JSON object.
"""
import collections
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arena.agent.cli import NOUNS, resolve_noun, resolve_verb, split_opts, tokenize  # noqa: E402

LOOK = {"brief", "score", "map", "contact", "event", "exercise"}
ACT = {("order", "add"), ("order", "withdraw"), ("fire", "add"), ("script", "put"), ("script", "delete"), ("agent", "spawn")}
CHECK = {("order", "lint"), ("script", "lint"), ("script", "test"), ("calc", "run")}


def classify(cmd):
    toks = tokenize(cmd)
    pos, opts = split_opts(toks)
    if not pos or opts.get("help"):
        return "docs", "help"
    noun = resolve_noun(pos[0])
    if noun is None and len(pos) > 1 and resolve_noun(pos[1]) and resolve_verb(resolve_noun(pos[1]), pos[0]):
        noun, pos = resolve_noun(pos[1]), [pos[1], pos[0]] + pos[2:]
    if noun is None:
        return "other", pos[0]
    verb = resolve_verb(noun, pos[1]) if len(pos) > 1 else NOUNS[noun]["default"]
    if noun == "unit" and verb is None:      # unit <name> [order|sop ...]: the unit-first form
        words = [w.lower() for w in pos[2:]]
        verb = "sop" if "sop" in words or "emission" in words else "order" if words else "show"
    key = f"{noun} {verb or ''}".strip()
    if noun in ("help", "doc"):
        return "docs", key
    if noun == "note":
        return "note", key
    if noun == "turn":
        return ("turn" if verb == "end" else "look"), key
    if (noun, verb) in ACT or (noun == "unit" and verb in ("order", "sop")):
        return "act", key
    if (noun, verb) in CHECK:
        return "check", key
    if noun in LOOK or noun in ("unit", "fire", "order", "script", "agent"):
        return "look", key
    return "other", key


def main():
    run = sys.argv[1]
    side = sys.argv[2] if len(sys.argv) > 2 else "0"
    agents = os.path.join(run, "agents", f"side{side}")
    classes, keys, refused, warned = collections.Counter(), collections.Counter(), [], 0
    for line in open(os.path.join(agents, "mcp.jsonl"), encoding="utf-8"):
        r = json.loads(line)
        if r.get("type") != "call":
            continue
        try:
            cmd = json.loads(r.get("arguments") or "{}").get("cmd", "")
        except ValueError:                   # the log cuts long arguments (a script's source in input): cmd comes first
            m = re.match(r'\s*\{\s*"cmd"\s*:\s*"((?:[^"\\]|\\.)*)"', r.get("arguments") or "")
            cmd = json.loads(f'"{m.group(1)}"') if m else ""
        cls, key = classify(cmd)
        classes[cls] += 1
        keys[key] += 1
        res = str(r.get("result") or "")
        if r.get("is_error") or res.startswith("错误"):
            refused.append({"cmd": cmd[:160], "result": res[:240]})
        elif "警告" in res[:400]:
            warned += 1
    turns = [json.loads(x) for x in open(os.path.join(run, "logs", "wakes.jsonl"), encoding="utf-8")]
    ends = [t for t in turns if t.get("kind") == "agent_end" and str(t.get("side")) == side]
    usage = collections.Counter()
    path = os.path.join(agents, "usage.jsonl")
    for line in open(path, encoding="utf-8") if os.path.exists(path) else []:
        u = json.loads(line)
        usage["calls"] += 1
        for k in ("input", "output", "reasoning", "cache_read", "cache_write"):
            usage[k] += u.get(k) or 0
    official = None
    rep = os.path.join(run, "exercise-report.json")
    if os.path.exists(rep):
        official = json.load(open(rep, encoding="utf-8")).get("official")
    total = sum(classes.values())
    print(json.dumps({
        "commands": total,
        "classes": dict(classes.most_common()),
        "look_per_act": round(classes["look"] / classes["act"], 2) if classes["act"] else None,
        "docs_share_pct": round(100 * classes["docs"] / total, 1) if total else None,
        "refused": len(refused), "warned": warned, "refused_detail": refused[:20],
        "turns": len(ends), "turn_end_reasons": dict(collections.Counter(t.get("reason") for t in ends)),
        "turn_seconds": [round(t.get("seconds") or 0) for t in ends],
        "usage": dict(usage), "by_command": dict(keys.most_common()), "official": official,
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
