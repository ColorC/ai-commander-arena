"""A side's score (materials/18 2.1 and 3.3): the game's Victory Status page as the referee can reckon it.

FM01 30: VP come from keeping one's own subunits, knocking out or destroying enemy subunits (each subunit is worth its
own VP, shown in the game's Subunit Inspector, the same whether damaged or destroyed) and holding VP cells; the share
of all VP decides the result, judged against the scenario's Victory Status table; a side whose force strength falls
below 30% triggers Sudden Death (and its result is rated one level lower).
The formula, checked 11-01 on The Armored Fist end screens (materials/18 3.3): a side's total = VP of its subunits
still Runner + the game value of the VP cells it holds; share = own total / both totals. Knocking out an enemy subunit
scores by taking its VP off the enemy's total; the page's "Enemy Losses Inflicted" row is not added. World.vp() does
the sums from the scenario's subunit VP table ($ARENA_DATA/scenarios/<scenario>/vp-table.json); without one, `formula_known` is
False and `share` None. The cell owners are the referee's own reckoning (the game does not report them).
Enemy losses are counts and VP sums only, as the game's Butcher's Bill and Victory Status show them to a player; no
enemy unit is named.
"""
from .views import VP_SOURCE
from .world import _loc, hex_dist
from . import rules

SUDDEN_DEATH_PCT = 30          # FM01 30.1 / 30.5: force strength below this ends the game
# the game measures that strength by VP value ("Current % of Starting Forces" on the Victory Status page: surviving
# forces / starting forces), not by subunit count. Start task 11-01, Kimi's second game: the referee showed the
# Czech side at 29.1 % of its subunits and said sudden death; the game had it at 31 % by value and played on
# a VP cell with units of both sides on it or next to it at the end: the game gives it by the units near it (FM01 30,
# Section C of the Victory Status page). Same game: 3923, a West German and a Czech unit on it from 11:26 to the end,
# was counted Czech while the referee still had it West German
CONTESTED_HEXES = 1
PENDING = "这个想定没有子单位分值表，算不出份额；只给能拿到的分项"


def share(own, enemy):
    """Own share of all VP in percent, one decimal (the game rounds to whole percent)."""
    both = own + enemy
    return round(100.0 * own / both, 1) if both > 0 else None


def timeline_row(world):
    """One row of logs/score.jsonl (a game minute): each side's surviving forces by VP value in % of its start (the
    game's sudden-death measure) and the VP cells it holds by the referee's reckoning; the scorecard's force curve
    (arena/harness/scorecard.py). None before the first state or without a subunit VP table."""
    st = world.start_vp
    if world.state is None or not st:
        return None
    v = {s: world.vp(s) for s in (0, 1)}
    return {"force_pct": {str(s): round(100.0 * v[s]["forces"] / max(st[s]["forces"], 1), 1) for s in (0, 1)},
            "cells": {str(s): v[s]["cells"] for s in (0, 1)}, "share0": share(v[0]["total"], v[1]["total"])}


def _losses(world, side):
    if world.state is None:
        return {"subunits_start": 0, "subunits_now": 0, "subunits_lost": 0, "strength_pct": None}
    ok, tot = world.strength(side)
    return {"subunits_start": tot, "subunits_now": ok, "subunits_lost": max(tot - ok, 0),
            "strength_pct": world.strength_pct(side)}


def contested(ref, side):
    """VP cells with an own ground unit and an enemy contact in view on them or next to them (CONTESTED_HEXES)."""
    own = [_loc((u.get("s") or {}).get("location")) for u in ref.world.own(side).values()
           if (u.get("s") or {}).get("status") == rules.GAME_STATUS_ACTIVE and (u.get("s") or {}).get("icon_type") not in rules.AIR_ICONS]
    own = [h for h in own if h]
    enemy = [_loc(c.last_pos) for c in ref.tables[side].all() if c.visible]
    enemy = [h for h in enemy if h]
    return {h for h in ref.sc.vp_cells
            if any(hex_dist(h, x) <= CONTESTED_HEXES for x in own) and any(hex_dist(h, x) <= CONTESTED_HEXES for x in enemy)}


def score(ref, side):
    w, sc = ref.world, ref.sc
    cells, held = [], {"you": 0, "enemy": 0, "none": 0}
    hot = contested(ref, side) if w.state is not None else set()
    for h, c in sorted(sc.vp_cells.items()):
        o = w.owner.get(h)
        who = "none" if o is None else ("you" if o == side else "enemy")
        held[who] += sc.cell_vp(h, 1 - side if who == "enemy" else side)
        cells.append({"hex": h, **sc.vp_fields(h, side), "owner": who,
                      **({"contested": True} if h in hot else {})})
    lv = sc.levels[side]
    known = sc.subunit_vp is not None and w.state is not None
    parts = {"vp_cells": cells, "vp_cells_held": held,
             "losses": {"you": _losses(w, side), "enemy": _losses(w, 1 - side)}}
    out = {"formula_known": known, "share": None, **parts}
    if known:
        you, enemy = w.vp(side), w.vp(1 - side)
        st = w.start_vp or {}
        out["vp"] = {"you": you, "enemy": enemy}
        out["share"] = share(you["total"], enemy["total"])
        if st:
            out["starting_share"] = share(st[side]["total"], st[1 - side]["total"])
            # what each side has lost of its own forces' VP: the "Enemy Losses Inflicted" row of the other side
            out["vp_lost"] = {"you": max(st[side]["forces"] - you["forces"], 0),
                              "enemy": max(st[1 - side]["forces"] - enemy["forces"], 0)}
            # the game's sudden-death measure: surviving forces' VP over the starting forces' VP
            out["force_value_pct"] = {"you": round(100.0 * you["forces"] / max(st[side]["forces"], 1), 1),
                                      "enemy": round(100.0 * enemy["forces"] / max(st[1 - side]["forces"], 1), 1)}
        # contested cells may go either way at the end: the share if all of them go to the enemy, and if all come to you
        mine = sum(c["vp"] for c in cells if c.get("contested") and c["owner"] == "you")
        theirs = sum(c["vp"] for c in cells if c.get("contested") and c["owner"] == "enemy")
        if mine or theirs:
            out["share_range"] = [share(you["total"] - mine, enemy["total"] + mine),
                                  share(you["total"] + theirs, enemy["total"] - theirs)]
    out.update({"levels": {"decisive": lv[0], "tactical": lv[1], "marginal": lv[2],
                           **({"contested_from": lv[3]} if len(lv) > 3 else {})},
                "sudden_death_below_pct": SUDDEN_DEATH_PCT, "vp_source": VP_SOURCE})
    if not known:
        out["note"] = PENDING
    return out
