"""The facts of one game for the video program's material pack: no wording, only what happened and who said what.

    python -I -X utf8 tools/program_facts.py <game a1 dir> <replay json (tools/s2_battle_maps.py)> <out.md> [--start HH:MM]
        [--names $ARENA_DATA/scenarios/<scenario>/unit-names-zh.json]

The commentators and the players write their own lines (the commentators in their group chat, the players in their own
sessions); this file gives them the same facts: the official end screen, the referee's per-minute share and force curve,
the losses and fire by half hour, VP cell changes, the heaviest single losses, and every wake of each commander with
its own closing words verbatim (the "voice replay" candidates), each with the transcript it comes from.
Sides are 蓝军 (side 0) and 红军 (side 1) everywhere. Cells are written (col,row) like (17,16) instead of 1716, and with
--names units get their Chinese designation (the original name in brackets); the commanders' own words stay verbatim.
"""
import collections
import json
import os
import sys

SIDE = {0: "蓝军", 1: "红军"}


def clock(sec, start):
    h, m = start
    t = h * 60 + m + sec // 60
    return f"{t // 60 % 24:02d}:{t % 60:02d}"


def cell(hx):
    """'1716' -> '(17,16)'; anything else as it is."""
    h = str(hx)
    return f"({int(h[:2])},{int(h[2:])})" if len(h) == 4 and h.isdigit() else h


def wakes(transcript):
    """Per commander wake: (wake ts, game sec from the summary, reasons, the words it wrote outside thinking)."""
    out, cur = [], None
    for line in open(transcript, encoding="utf-8", errors="replace"):
        try:
            x = json.loads(line)
        except ValueError:
            continue
        if x.get("type") == "wake" and x.get("agent") == "commander":
            sec = None
            s = x.get("summary") or ""
            i = s.find('"sec":')
            if i >= 0:
                digits = "".join(ch for ch in s[i + 6:i + 14] if ch.isdigit())
                sec = int(digits) if digits else None
            cur = {"n": x.get("n"), "sec": sec, "prematch": bool(x.get("prematch")),
                   "reasons": sorted({r.get("type") for r in x.get("reasons") or [] if isinstance(r, dict)}), "said": []}
            out.append(cur)
            continue
        if cur is None:
            continue
        msg = x.get("msg") if isinstance(x.get("msg"), dict) else {}
        u = (msg.get("params") or {}).get("update") or {}
        if u.get("sessionUpdate") == "agent_message_chunk":
            c = u.get("content")
            if isinstance(c, dict) and c.get("text"):
                cur["said"].append(c["text"])
    return out


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    a1, replay, out = argv[:3]
    start = (3, 30)
    if "--start" in argv:
        h, m = argv[argv.index("--start") + 1].split(":")
        start = (int(h), int(m))
    zh = {}
    if "--names" in argv:
        zh = json.load(open(argv[argv.index("--names") + 1], encoding="utf-8"))["units"]

    def name(n):
        return f"{zh[n]['zh']}（{n}）" if n in zh else n

    d = json.load(open(replay, encoding="utf-8"))
    units = d["u"]
    rows = [json.loads(x) for x in open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(a1))), "games.jsonl"),
                                         encoding="utf-8") if x.strip()]
    official = next((r.get("official") for r in reversed(rows) if os.path.normcase(str(r.get("dir"))) == os.path.normcase(a1)), {}) or {}
    L = [f"# 对局事实：{os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(a1))))}", "",
         f"来源：{a1}；回放数据 {replay}。0 方 = 蓝军，1 方 = 红军。时间为游戏时钟（开局 {start[0]:02d}:{start[1]:02d}）。", ""]
    L += ["## 终局页（游戏官方，蓝军指挥部视角）", ""]
    for k in ("result", "vp_ratio_pct", "starting_ratio_pct", "survived_vp", "locations_vp", "inflicted_vp", "stars"):
        if k in official:
            L.append(f"- {k}: {official[k]}")
    score = [json.loads(x) for x in open(os.path.join(a1, "logs", "score.jsonl"), encoding="utf-8") if x.strip()]
    L += ["", "## 裁判曲线（每 30 游戏分钟：蓝军份额为裁判计分，双方剩余兵力按 VP 价值占开局的百分比）", "",
          "| 时间 | 蓝军份额 | 蓝军兵力 | 红军兵力 | 蓝军持有目标格 | 红军持有目标格 |", "|---|---|---|---|---|---|"]
    for r in score:
        if r["sec"] % 1800 == 0 or r is score[-1]:
            L.append(f"| {clock(r['sec'], start)} | {r['share0']}% | {r['force_pct']['0']}% | {r['force_pct']['1']}% | "
                     f"{r['cells']['0']} | {r['cells']['1']} |")
    loss, fire = collections.Counter(), collections.Counter()
    big = []
    for t, hx, side, kind, n, uid in d["ev"]:
        if kind == 2:
            loss[(t // 1800, side)] += n
            big.append((n, t, side, units[uid][0], hx))
        elif kind == 1:
            fire[(t // 1800, side)] += n
    L += ["", "## 每半小时：损失子单位与发射弹药", "", "| 时段 | 蓝军损失 | 红军损失 | 蓝军发射 | 红军发射 |", "|---|---|---|---|---|"]
    for h in range((max(e[0] for e in d["ev"]) // 1800) + 1 if d["ev"] else 0):
        L.append(f"| {clock(h * 1800, start)}–{clock(h * 1800 + 1800, start)} | {loss[(h, 0)]} | {loss[(h, 1)]} | "
                 f"{fire[(h, 0)]} | {fire[(h, 1)]} |")
    L += ["", "## 目标格易手", ""]
    vp = {h: v for h, v, _ in d.get("vp") or []}
    for t, hx, owner in d.get("vc") or []:
        L.append(f"- {clock(t, start)} 格 {cell(hx)} 归 {SIDE.get(owner, '无人')}")
    L += ["", "## 单次损失最多的 12 次（单位、格、子单位数）", ""]
    for n, t, side, u, hx in sorted(big, reverse=True)[:12]:
        L.append(f"- {clock(t, start)} {SIDE[side]} {name(u)} 在 {cell(hx)} 损失 {n}")
    for side in (0, 1):
        tr = os.path.join(a1, "agents", f"side{side}", "transcript.jsonl")
        L += ["", f"## {SIDE[side]}指挥官每次唤醒说的话（原文，语音回放候选；思考过程见转录）", "", f"转录：{tr}", ""]
        for w in wakes(tr):
            said = "".join(w["said"]).strip()
            when = "赛前部署" if w["prematch"] else (clock(w["sec"], start) if w["sec"] is not None else "?")
            L.append(f"- 唤醒 {w['n']}｜{when}｜{','.join(w['reasons'])}：{said if said else '（没有对外的话）'}")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1:])
