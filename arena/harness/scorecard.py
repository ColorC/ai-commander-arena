"""A game's scorecard (materials/21 section 6): how well a player did, as facts, never as advice.

The official result (the game's end screen, as the director reads it) is set among the scenario's reference games
($ARENA_DATA/scenarios/<scenario>/benchmarks.json: the side doing nothing, the game's AI commanding the side): where the share
stands, its three parts (forces kept, VP cells held, losses inflicted) against theirs, the exchange ratio (enemy VP
knocked out per own VP lost), how far each side was from sudden death, the force curve from the referee's
logs/score.jsonl (one row a game minute, its own reckoning) and the goal ladder. What to change is the player's work.
"""
import json
import os
import re
import statistics

from arena.referee.world import Scenario

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# scenario data (README): $ARENA_DATA/scenarios/<scenario>/, ARENA_DATA default <repo>/data
DATA = os.path.join(os.environ.get("ARENA_DATA") or os.path.join(ROOT, "data"), "scenarios")
SUDDEN_DEATH_PCT = 30
# the head-to-head goal (materials/19 section 5): two games of the formal scenario, one on each side, sum of shares
HUMAN_PAIR = "正式赛（对另一位选手，两边各打一局）：两局份额之和到人类中位 {m}，再到人类上四分位 {q}"


def benchmarks(key):
    path = os.path.join(DATA, key or "", "benchmarks.json")
    if not key or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def scenario_key(name):
    """The $ARENA_DATA/scenarios folder of a scenario by its display name (exercise-report.json keeps the name only)."""
    for key in sorted(os.listdir(DATA)):
        path = os.path.join(DATA, key, "start.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                if json.load(f).get("name") == name:
                    return key
    return None


def sides(key):
    """{side: its end-screen commander} from $ARENA_DATA/scenarios/<key>/official.json "sides" ("1st Bde (US …)" gives "1st Bde");
    {} when unknown."""
    path = os.path.join(DATA, key or "", "official.json")
    if not key or not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return {int(s): n.split(" (")[0].strip() for s, n in (json.load(f).get("sides") or {}).items()}


def view_side(official, key):
    """Whose end screen the official result (or one of its views) is, by its commander: Battle Over is side 0's in a
    game with two seats whoever played it, the human side's against the game's AI. The name is compared without spaces
    and punctuation and may be cut off at the shot's edge ("1st" of training p1-3, 11-02). None when no side, or both,
    fit it."""
    key_of = lambda s: re.sub(r"[^0-9a-z]", "", str(s or "").lower())  # noqa: E731
    name = key_of((official or {}).get("commander"))
    hits = [s for s, n in sides(key).items()
            if name and key_of(n) and (name.startswith(key_of(n)) or (len(name) >= 3 and key_of(n).startswith(name)))]
    return hits[0] if len(hits) == 1 else None


def _turn(result):
    w = str(result or "").split()
    return " ".join(w[:-1] + [{"win": "Loss", "loss": "Win"}[w[-1].lower()]]) if w and w[-1].lower() in ("win", "loss") else result


def _turned(v, key, side):
    """Side's numbers from the other side's end screen v: the shares and the result exactly (one side's Tactical Win is
    the other's Tactical Loss), the three parts from the starting forces (kept = own start - what the other inflicted;
    inflicted = other start - what the other kept; VP cells = all cells - the other's). Only what v has."""
    out = {"result": _turn(v.get("result"))}
    for k in ("vp_ratio_pct", "starting_ratio_pct", "awarded_vp_pct", "vp_share_pct"):
        if isinstance(v.get(k), (int, float)):
            out[k] = round(100 - v[k], 1)
    out["force_points"], out["enemy_points"] = v.get("enemy_points"), v.get("force_points")
    b = benchmarks(key)
    if b:
        start, cells = b["start_forces_vp"], b.get("vp_cells_total")
        if v.get("inflicted_vp") is not None:
            out["survived_vp"] = max(start[str(side)] - v["inflicted_vp"], 0)
        if v.get("survived_vp") is not None:
            out["inflicted_vp"] = max(start[str(1 - side)] - v["survived_vp"], 0)
        if cells is not None and v.get("locations_vp") is not None:
            out["locations_vp"] = max(cells - v["locations_vp"], 0)
    return {k: x for k, x in out.items() if x is not None}


FIELD_NAMES = {"vp_ratio_pct": "份额", "starting_ratio_pct": "开局份额", "result": "结果", "survived_vp": "保住部队",
               "locations_vp": "守住 VP 格", "inflicted_vp": "造成损失"}
# fields only the information center's pages give (tools/fpc_endscreen.py)
INFO_CENTER = ("vp_share_pct", "awarded_vp_pct", "force_points", "enemy_points", "adjusted_net_score_pct")


def own_view(official, key, side):
    """The official result as side `side` sees it (a new dict). The end screens shot are Battle Over and the
    information center's pages, each one side's (tools/fpc_endscreen.py keeps them under "views"): the side's own
    is taken; a number its page lost at the cut-off right edge comes from the other side's, turned round (_turned),
    listed in "filled". With only the other side's screen, all of it is turned round, marked "derived" (stars and
    combat readiness do not turn and are left out). As is when the view is not known. vp_ratio_pct falls back on
    vp_share_pct (the same share, from the Victory Status page)."""
    if not official:
        return official
    views = [v for v in (official.get("views") or {}).values()]
    at = view_side(official, key)
    mine = next((v for v in views if view_side(v, key) == side), None)
    theirs = next((v for v in views if view_side(v, key) == 1 - side), None)
    if at == side or (at is None and mine is None):
        out = dict(official)
    elif mine is not None:
        out = dict(mine)
    else:
        # records before views (11-02) merged the information center's fields into Battle Over's; the one such record
        # of side 1 (training p1-1) had side 1's information center, the player's own here
        src = official if views else {k: x for k, x in official.items() if k not in INFO_CENTER}
        out = dict(_turned(src, key, side), derived=True, end_screen_of=1 - side, commander=sides(key).get(side))
        if not views:
            out.update({k: official[k] for k in INFO_CENTER if k in official})
        theirs = None
    out.pop("views", None)
    # the share of the side's own page first (a record before views can hold the other side's vp_share_pct with its
    # own Battle Over: not then)
    if out.get("vp_ratio_pct") is None and out.get("vp_share_pct") is not None and (views or out.get("derived")):
        out["vp_ratio_pct"] = out["vp_share_pct"]
    filled = []
    for k, x in (_turned(theirs, key, side) if theirs else {}).items():
        if out.get(k) is None:
            out[k] = x
            filled.append(k)
    if filled:
        out["filled"] = filled
    # both pages' result sentences cut off (two seats, 11-02 p0-4, p1-4, p1-5): the level of the share in the
    # scenario's table for this side
    if out.get("result") is None and isinstance(out.get("vp_ratio_pct"), (int, float)):
        out["result"] = level_of(key, side, out["vp_ratio_pct"])
        if out["result"]:
            out["result_from_share"] = True
    return out


def level_of(key, side, share):
    """The level a share is for a side, from $ARENA_DATA/scenarios/<key>/official.json "levels" ({side: {level: [lo, hi]}}), as the
    game writes it ("Contested Result"); None when unknown."""
    path = os.path.join(DATA, key or "", "official.json")
    if not key or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        table = (json.load(f).get("levels") or {}).get(str(side)) or {}
    s = round(share)
    name = next((n for n, (lo, hi) in table.items() if lo <= s <= hi), None)
    return "Contested Result" if name == "Contested" else name


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def _parts(survived, cells, inflicted, start_own, start_enemy):
    lost = max(start_own - survived, 0)
    return {"kept_pct": _pct(survived, start_own), "cells_vp": cells, "inflicted_vp": inflicted, "lost_vp": lost,
            "exchange": round(inflicted / lost, 2) if lost > 0 else None,
            "enemy_left_pct": _pct(max(start_enemy - inflicted, 0), start_enemy)}


def goals(refs, levels_decisive):
    """The ladder against the game's AI, low to high: thresholds from the reference games, then Decisive Win."""
    out = []
    passive = [r["share"] for r in refs if r["kind"] == "passive"]
    comp = [r["share"] for r in refs if r["kind"] == "computer"]
    if passive:
        out.append({"goal": f"不输给原地不动（份额 > {passive[0]}%）", "above": passive[0]})
    if comp:
        mean = round(statistics.mean(comp), 1)
        out.append({"goal": f"超过电脑指挥同一方的平均（份额 > {mean:g}%）", "above": mean})
        out.append({"goal": f"超过电脑的最好成绩（份额 > {max(comp)}%）", "above": max(comp)})
    out.sort(key=lambda g: g["above"])
    out.append({"goal": f"Decisive Win（份额 ≥ {levels_decisive}%，或把敌方打到突然死亡）", "decisive": True})
    # the top rung: the share itself in the Decisive band, not raised there by the enemy's sudden death
    out.append({"goal": f"份额本身 ≥ {levels_decisive}%（不靠突然死亡升档）", "at_least": levels_decisive})
    return out


def _curve(rows, side, start_clock):
    """Hourly samples of both sides' force (VP value %) and the own steepest hour, from logs/score.jsonl rows."""
    if not rows:
        return None
    own, enemy = str(side), str(1 - side)
    clock = lambda sec: (start_clock + int(sec)) % 86400  # noqa: E731
    hhmm = lambda sec: "%02d:%02d" % (clock(sec) // 3600, clock(sec) % 3600 // 60)  # noqa: E731
    samples, hour = [], None
    for r in rows:                               # the first row of each clock hour, and the last row
        if clock(r["sec"]) // 3600 != hour or r is rows[-1]:
            samples.append({"clock": hhmm(r["sec"]), "sec": r["sec"], "you": r["force_pct"][own], "enemy": r["force_pct"][enemy]})
            hour = clock(r["sec"]) // 3600
    worst, j = None, 0
    for i, r in enumerate(rows):                 # steepest own drop over any 60 game minutes
        while rows[j]["sec"] < r["sec"] - 3600:
            j += 1
        drop = rows[j]["force_pct"][own] - r["force_pct"][own]
        if worst is None or drop > worst["drop"]:
            worst = {"from": hhmm(rows[j]["sec"]), "to": hhmm(r["sec"]), "drop": round(drop, 1)}
    return {"hourly": samples, "steepest_own_hour": worst if worst and worst["drop"] > 0 else None}


def card(official, key, side, vs_ai=True, timeline=None, curve=None):
    """The scorecard of one side of one finished game: a dict with a "text" (several lines, Chinese) for the player.
    The official result is taken as the side sees it (own_view); "view" says whether it was turned round ("derived")
    or read as is ("own"). curve: the force curve of a card made earlier, when the timeline is not at hand."""
    b = benchmarks(key)
    official = own_view(official, key, side)
    if not official or b is None or official.get("vp_ratio_pct") is None:
        return None
    sc = Scenario(key)
    start_own, start_enemy = b["start_forces_vp"][str(side)], b["start_forces_vp"][str(1 - side)]
    you = _parts(official.get("survived_vp") or 0, official.get("locations_vp") or 0, official.get("inflicted_vp") or 0,
                 start_own, start_enemy)
    share = official["vp_ratio_pct"]
    refs = [dict(r, **_parts(r["survived_vp"], r["cells_vp"], r["inflicted_vp"], start_own, start_enemy))
            for r in (b.get("vs_ai") or {}).get(str(side), [])] if vs_ai else []
    derived = bool(official.get("derived"))
    out = {"scenario": b["scenario"], "side": side, "vs_ai": vs_ai, "share": share, "result": official.get("result"),
           "view": "derived" if derived else "own",
           "you": you, "references": [{k: r[k] for k in ("id", "kind", "share", "kept_pct", "cells_vp", "inflicted_vp", "exchange")} for r in refs]}
    lines = [f"成绩单：{b['scenario']}，执 {side} 方，{'对内置 AI' if vs_ai else '对另一位指挥官'}",
             f"- 官方结果 {official.get('result')}，份额 {share:g}%"
             + (f"（开局 {official['starting_ratio_pct']:g}%）" if isinstance(official.get("starting_ratio_pct"), (int, float)) else "")
             + (f"。只读到 {official['end_screen_of']} 方视角的结算页，这里换成本方：份额和结果是对方的反面，"
                "保住部队、守住 VP 格、造成损失三项由开局兵力推出" if derived else "")
             + ("。" + "、".join(FIELD_NAMES[k] for k in official["filled"] if k in FIELD_NAMES) + "在本方结算页上被截掉，由对方那页换算"
                if any(k in FIELD_NAMES for k in official.get("filled") or ()) else "")
             + ("。结算页上写结果的那句被截掉，结果按份额对照本图本方的等级表得出" if official.get("result_from_share") else "")]
    sd_own, sd_enemy = you["kept_pct"] is not None and you["kept_pct"] < SUDDEN_DEATH_PCT, \
        you["enemy_left_pct"] is not None and you["enemy_left_pct"] < SUDDEN_DEATH_PCT
    lines.append(f"- 兵力终局（按 VP 价值，低于 {SUDDEN_DEATH_PCT}% 触发突然死亡）：本方 {you['kept_pct']}%"
                 + ("（触发）" if sd_own else "") + f"，敌方 {you['enemy_left_pct']}%" + ("（触发）" if sd_enemy else ""))
    if refs:
        above = sum(1 for r in refs if share > r["share"])
        below = sum(1 for r in refs if share < r["share"])
        kinds = {}
        for r in refs:
            kinds.setdefault(r["what"], []).append(f"{r['share']}%")
        names = "；".join(f"{w} {' / '.join(v)}" for w, v in kinds.items())
        lines.append(f"- 份额在 {len(refs)} 局参照里：高于 {above} 局，低于 {below} 局（{names}）")

        def rng(k, fmt="{}"):
            vals = [r[k] for r in refs if r[k] is not None]
            lo, hi = fmt.format(min(vals)), fmt.format(max(vals))
            return f"参照 {lo}" if lo == hi else f"参照 {lo}–{hi}"
        lines.append(f"- 保住部队 {you['kept_pct']}%（{rng('kept_pct')}%）")
        lines.append(f"- 守住 VP 格 {you['cells_vp']:,.0f}（{rng('cells_vp', '{:,}')}，共 {b['vp_cells_total']:,}）")
        lines.append(f"- 给敌方造成损失 {you['inflicted_vp']:,.0f} VP（{rng('inflicted_vp', '{:,}')}）")
        lines.append(f"- 交换比（打掉敌方 VP ÷ 本方损失 VP）{you['exchange'] if you['exchange'] is not None else '本方无损失'}（{rng('exchange')}）")
        ladder = goals(refs, sc.levels[side][0])
        for g in ladder:
            g["reached"] = (str(official.get("result") or "").lower() == "decisive win") if g.get("decisive") else \
                share >= g["at_least"] if "at_least" in g else share > g["above"]
        out["goals"] = ladder
        lines.append("- 目标阶梯：" + "　".join(("■ " if g["reached"] else "□ ") + g["goal"] for g in ladder))
    else:
        lines.append(f"- 保住部队 {you['kept_pct']}%，守住 VP 格 {you['cells_vp']:,.0f}，给敌方造成损失 {you['inflicted_vp']:,.0f} VP，"
                     f"交换比 {you['exchange']}")
    h = b.get("human")
    if h:
        lines.append(f"- 人类对照（{h['source']}）：这张图份额中位 {h['share_median']}，四分位 {h['share_quartiles'][0]}/{h['share_quartiles'][1]}；"
                     + HUMAN_PAIR.format(m=h["pair_sum_median"], q=h["pair_sum_quartiles"][1]))
    curve = _curve(timeline or [], side, sc.start_clock) or curve
    if curve:
        out["curve"] = curve
        lines.append("- 兵力走势（裁判估算，本方/敌方 %）：" + " · ".join(f"{s['clock']} {s['you']:g}/{s['enemy']:g}" for s in curve["hourly"]))
        w = curve["steepest_own_hour"]
        if w:
            lines.append(f"- 本方掉得最快的一小时：{w['from']}–{w['to']}，掉了 {w['drop']:g} 个百分点")
    out["text"] = "\n".join(lines)
    return out


def recard(stored, official, key, side, vs_ai=True, timeline=None):
    """A stored scorecard as is when it was made with views (it has "view"); else made again (cards made before took
    the end screen as the player's even when it was the other side's), keeping the stored force curve."""
    if stored and stored.get("view"):
        return stored
    return card(official, key, side, vs_ai=vs_ai, timeline=timeline, curve=(stored or {}).get("curve"))


def read_timeline(game_dir):
    """logs/score.jsonl of a game's referee folder (rows the referee wrote each game minute); [] when absent."""
    path = os.path.join(game_dir, "logs", "score.jsonl")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]
