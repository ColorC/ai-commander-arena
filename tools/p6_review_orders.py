"""Compact order timeline of a game run: submitted commands per side (clock, source, unit, kind, order, target),
plus vp_change / unit_out / strength_drop events and per-hour combat counts. Usage: p6_review_orders.py <run> <out.md>"""
import collections
import json
import os
import sys

run, out = sys.argv[1], sys.argv[2]


def rows(p):
    return [json.loads(x) for x in open(os.path.join(run, p), encoding="utf-8") if x.strip()]


def clock(sec):
    sec = int(sec or 0)
    return f"{7 + sec // 3600:02d}:{sec % 3600 // 60:02d}"


def brief(a):
    a = a or {}
    if isinstance(a.get("orders"), list):
        parts = []
        for o in a["orders"]:
            s = o.get("orders", "?")
            for k in ("destination_location", "target_location", "static_target", "location"):
                if o.get(k):
                    s += f"@{o[k]}"
            for k in ("rounds", "ammo", "duration"):
                if o.get(k):
                    s += f" {k}={o[k]}"
            parts.append(s)
        return " > ".join(parts)
    keep = {k: v for k, v in a.items() if k not in ("unit",)}
    return json.dumps(keep, ensure_ascii=False)[:160]


lines = []
for q in rows("logs/queue.jsonl"):
    if q.get("event") != "submitted":
        continue
    src = q.get("source", "")
    src = "AG" if src == "agent" else src.replace("script:", "S:")
    lines.append((q.get("sec", 0), q.get("side"), f"{clock(q.get('sec'))} s{q.get('side')} {src:<18} {q.get('kind'):<12} {q.get('unit') or ''} :: {brief(q.get('args'))}"))
ev = rows("logs/events.jsonl")
for e in ev:
    if e.get("type") in ("vp_change", "unit_out", "strength_drop", "command_failed"):
        d = {k: v for k, v in e.items() if k not in ("side", "id", "t", "type")}
        lines.append((e.get("t", 0), e.get("side"), f"{clock(e.get('t'))} s{e.get('side')} EVENT {e['type']} {json.dumps(d, ensure_ascii=False)[:160]}"))
lines.sort(key=lambda x: (x[0], x[1] if x[1] is not None else -1))
combat = collections.Counter((e.get("side"), int(e.get("t", 0)) // 3600) for e in ev if e.get("type") == "combat")
contacts = collections.Counter((e.get("side"), int(e.get("t", 0)) // 3600) for e in ev if e.get("type") == "new_contact")
with open(out, "w", encoding="utf-8", newline="\n") as f:
    f.write("combat events per hour (side, hour07+): " + json.dumps(sorted(combat.items())) + "\n")
    f.write("new contacts per hour: " + json.dumps(sorted(contacts.items())) + "\n")
    for _, _, s in lines:
        f.write(s + "\n")
print(out, len(lines))
