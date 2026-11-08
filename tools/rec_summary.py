"""Summarise an FPC recorder file (rec.jsonl) and check that replaying the diffs reproduces each snapshot.

    python tools/rec_summary.py <rec.jsonl> [--md <out.md>]

Record kinds come from fpc/lua/recorder.lua: meta, snap, diff, unit_add, unit_gone, minute, event,
order_start, order_finish, f9, end.
"""
import argparse
import collections
import json
import sys


def flatten(v, prefix="", out=None):
    out = {} if out is None else out
    if isinstance(v, dict):
        for k, x in v.items():
            flatten(x, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(v, list):
        for i, x in enumerate(v, 1):
            flatten(x, f"{prefix}.{i}" if prefix else str(i), out)
    else:
        out[prefix] = v
    return out


def hhmm(sec):
    if sec is None:
        return "--:--"
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rec")
    ap.add_argument("--md")
    a = ap.parse_args()

    recs = [json.loads(line) for line in open(a.rec, encoding="utf-8") if line.strip()]
    kinds = collections.Counter(r["k"] for r in recs)
    meta = next((r for r in recs if r["k"] == "meta"), {})
    snaps = [r for r in recs if r["k"] == "snap"]
    minutes = [r for r in recs if r["k"] == "minute" and r.get("sec") is not None]

    side_of = {}
    state = {}          # unit -> flat state, rebuilt from the first snapshot + diffs
    replay_checks = []  # (snapshot reason, sec, mismatching units)
    moves = collections.Counter()
    status_changes = collections.Counter()
    sub_status_changes = collections.Counter()
    order_changes = collections.Counter()
    ammo_used = collections.Counter()
    per_hour = collections.defaultdict(collections.Counter)

    def side(unit):
        return side_of.get(unit, "?")

    for r in recs:
        k = r["k"]
        if k == "snap":
            flat = {u: flatten(c) for u, c in r["units"].items()}
            for u, f in flat.items():
                if "s.side" in f:
                    side_of[u] = f["s.side"]
            if state:
                bad = sorted(u for u in set(flat) | set(state) if flat.get(u) != state.get(u))
                replay_checks.append((r.get("reason"), r.get("sec"), bad))
            state = flat
        elif k == "unit_add":
            state[r["unit"]] = flatten(r["state"])
            side_of.setdefault(r["unit"], state[r["unit"]].get("s.side"))
        elif k == "unit_gone":
            state.pop(r["unit"], None)
        elif k == "diff":
            u, cur = r["unit"], state.setdefault(r["unit"], {})
            hour = (r.get("sec") or 0) // 3600
            for p, v in r.get("set", {}).items():
                old = cur.get(p)
                if p == "s.location":
                    moves[side(u)] += 1
                    per_hour[hour][f"moves_side{side(u)}"] += 1
                elif p == "s.status":
                    status_changes[(side(u), f"{old} -> {v}")] += 1
                elif p.startswith("u.") and p.endswith(".status"):
                    sub_status_changes[(side(u), f"{old} -> {v}")] += 1
                    per_hour[hour][f"subunit_status_side{side(u)}"] += 1
                elif p.startswith("o.") and p.endswith(".orders"):
                    order_changes[(side(u), v)] += 1
                elif p.endswith(".rounds"):
                    try:
                        d = int(old) - int(v)
                        if d > 0:
                            ammo_used[side(u)] += d
                            per_hour[hour][f"rounds_side{side(u)}"] += d
                    except (TypeError, ValueError):
                        pass
                cur[p] = v
            for p in r.get("del", []):
                cur.pop(p, None)

    first = snaps[0] if snaps else {"units": {}}
    last = snaps[-1] if snaps else {"units": {}}

    def roster(snap):
        c = collections.Counter()
        for u, cs in snap["units"].items():
            s = cs.get("s", {})
            c[(s.get("side"), s.get("status"))] += 1
        return c

    def subroster(snap):
        c = collections.Counter()
        for u, cs in snap["units"].items():
            for sub in cs.get("u", []) if isinstance(cs.get("u"), list) else []:
                c[(cs.get("s", {}).get("side"), sub.get("status"))] += 1
        return c

    L = []
    L.append(f"# FPC 录制汇总：{(meta.get('scenario') or {}).get('title', '?')}")
    L.append("")
    L.append(f"- 文件：`{a.rec}`")
    L.append(f"- 记录：{len(recs)} 行；按类型 " + "、".join(f"{k} {n}" for k, n in sorted(kinds.items())))
    if minutes:
        w0, w1 = minutes[0]["wall"], minutes[-1]["wall"]
        span = max(w1 - w0, 1)
        gsec = (minutes[-1]["sec"] or 0) - (minutes[0]["sec"] or 0)
        L.append(f"- 游戏钟：{hhmm(minutes[0]['sec'])} → {hhmm(minutes[-1]['sec'])}，墙钟 {span} 秒，"
                 f"约 {gsec / span:.1f} 游戏秒/墙钟秒")
        L.append(f"- 每分钟采样耗时：最大 {max(m.get('cpu_ms', 0) for m in minutes)} ms，"
                 f"平均 {sum(m.get('cpu_ms', 0) for m in minutes) / len(minutes):.1f} ms")
    L.append(f"- 结束：{'有 OnScenarioEnd' if kinds.get('end') else '没有 OnScenarioEnd（未打完或被作业收尾）'}")
    L.append("")
    L.append("## 回放校验（首个快照 + 差分 重建后与后续快照比对）")
    L.append("")
    if not replay_checks:
        L.append("- 只有一个快照，无法比对")
    for reason, sec, bad in replay_checks:
        L.append(f"- {reason} @ {hhmm(sec)}：" + ("一致" if not bad else f"{len(bad)} 个单位不一致：{', '.join(bad[:8])}"))
    L.append("")
    L.append("## 兵力（单位状态，开局快照 → 最后快照）")
    L.append("")
    r0, r1 = roster(first), roster(last)
    for key in sorted(set(r0) | set(r1), key=str):
        L.append(f"- side {key[0]} {key[1]}：{r0.get(key, 0)} → {r1.get(key, 0)}")
    L.append("")
    L.append("## 车辆 / 班组（子单位状态，开局 → 最后）")
    L.append("")
    s0, s1 = subroster(first), subroster(last)
    for key in sorted(set(s0) | set(s1), key=str):
        L.append(f"- side {key[0]} {key[1]}：{s0.get(key, 0)} → {s1.get(key, 0)}")
    L.append("")
    L.append("## 变化统计")
    L.append("")
    L.append(f"- 位置变化（格）：" + "，".join(f"side {s} {n}" for s, n in sorted(moves.items(), key=str)))
    L.append(f"- 弹药消耗（发）：" + "，".join(f"side {s} {n}" for s, n in sorted(ammo_used.items(), key=str)))
    L.append("- 单位状态变化：")
    for (s, t), n in sorted(status_changes.items(), key=str):
        L.append(f"  - side {s} {t}：{n}")
    L.append("- 子单位状态变化：")
    for (s, t), n in sorted(sub_status_changes.items(), key=str):
        L.append(f"  - side {s} {t}：{n}")
    L.append("- 新命令：")
    for (s, o), n in sorted(order_changes.items(), key=str):
        L.append(f"  - side {s} {o}：{n}")
    if per_hour:
        L.append("")
        L.append("## 按游戏小时")
        L.append("")
        cols = sorted({c for h in per_hour.values() for c in h})
        L.append("| 小时 | " + " | ".join(cols) + " |")
        L.append("|---|" + "---|" * len(cols))
        for h in sorted(per_hour):
            L.append(f"| {h} | " + " | ".join(str(per_hour[h].get(c, 0)) for c in cols) + " |")
    events = [r for r in recs if r["k"] == "event"]
    if events:
        L.append("")
        L.append("## 事件样例（前 5 条）")
        L.append("")
        for e in events[:5]:
            L.append(f"- {hhmm(e.get('sec'))} `{json.dumps(e.get('event'), ensure_ascii=False)[:300]}`")
    text = "\n".join(L) + "\n"
    if a.md:
        open(a.md, "w", encoding="utf-8").write(text)
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
