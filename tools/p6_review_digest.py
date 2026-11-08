"""Per-turn digest of a harness transcript: wake (clock, reasons), the agent's messages, the tail of its thinking,
and its tool calls (name + short arguments). Usage: p6_review_digest.py <agents/sideN dir> <out.md> [thought chars] [msg chars]"""
import json
import os
import sys

src, out = sys.argv[1], sys.argv[2]
TH = int(sys.argv[3]) if len(sys.argv) > 3 else 500
MS = int(sys.argv[4]) if len(sys.argv) > 4 else 1200
turns, cur = [], None
for line in open(os.path.join(src, "transcript.jsonl"), encoding="utf-8"):
    x = json.loads(line)
    t = x.get("type")
    if t == "wake":
        clock = ""
        try:
            s = x.get("summary") or ""
            i = s.find('{"game"')
            g = json.JSONDecoder().raw_decode(s[i:])[0]["game"] if i >= 0 else {}
            clock = g.get("clock", "")
        except Exception:
            pass
        cur = {"n": x.get("n"), "agent": x.get("agent"), "clock": clock, "prematch": x.get("prematch"),
               "reasons": [r.get("type") for r in (x.get("reasons") or [])], "msg": [], "th": [], "tools": []}
        turns.append(cur)
        continue
    if t != "acp" or cur is None:
        continue
    m = x["msg"]
    if m.get("method") != "session/update":
        continue
    u = m["params"]["update"]
    k = u.get("sessionUpdate")
    if k == "agent_message_chunk":
        cur["msg"].append((u.get("content") or {}).get("text") or "")
    elif k == "agent_thought_chunk":
        cur["th"].append((u.get("content") or {}).get("text") or "")
    elif k == "tool_call":
        name = (u.get("title") or u.get("name") or "").replace("mcp__arena__", "").replace("arena_", "")
        raw = u.get("rawInput")
        arg = json.dumps(raw, ensure_ascii=False) if raw else ""
        cur["tools"].append(name + (" " + arg[:160] if arg and arg != "{}" else ""))
with open(out, "w", encoding="utf-8", newline="\n") as f:
    for c in turns:
        f.write(f"\n## wake {c['n']} [{c['agent']}] {c['clock']} {'PREMATCH ' if c['prematch'] else ''}{','.join(c['reasons'])}\n")
        th = "".join(c["th"]).strip()
        if th:
            f.write("THINK…" + th[-TH:].replace("\n", " ") + "\n")
        msg = "".join(c["msg"]).strip()
        if msg:
            f.write("SAY: " + msg[:MS].replace("\n", " ") + "\n")
        if c["tools"]:
            f.write("TOOLS(" + str(len(c["tools"])) + "): " + " | ".join(c["tools"])[:1500] + "\n")
print(out, len(turns))
