"""Scenario data ($ARENA_DATA/scenarios/<scenario>/) and the referee's picture of the game: latest and previous state.json.

state = {"sec", "units": {name: {"s": GetUnitStatus, "o": GetUnitOrders, "u": GetUnitSubunits}}} as written by
arena-bridge. Hex numbers are col * 100 + row; hexes are flat-topped, even columns sit half a hex lower.
The game does not report VP cell owners: World.update reckons them from unit positions (vp_passes).
"""
import json
import os
import re

from . import rules

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# scenario data (README): $ARENA_DATA/scenarios/<scenario>/, ARENA_DATA default <repo>/data
DATA_ROOT = os.path.join(os.environ.get("ARENA_DATA") or os.path.join(REPO, "data"), "scenarios")


class Scenario:
    def __init__(self, slug, data_root=None):
        self.slug = slug
        d = os.path.join(data_root or DATA_ROOT, slug)
        with open(os.path.join(d, "start.json"), encoding="utf-8") as f:
            start = json.load(f)
        self.name = start["name"]
        h, m = start["h_hour"].split(":")
        self.start_clock = int(h) * 3600 + int(m) * 60       # clock-of-day second of game second 0
        self.total_sec = int(start["hours"] * 3600)
        self.commanders = start["commanders"]                 # per side: the command post unit
        # per side: decisive / tactical / marginal VP share the game judges by (its end-of-game Victory Status table);
        # the briefing text quotes other numbers (start["levels"], P4 exam 10-30: 65% for TF Alamo was Contested)
        self.levels = start.get("official_levels") or start["levels"]
        self.start_areas = start["start_areas"]               # per side: col_lo, col_hi, row_lo, row_hi
        self.map_km = start["map_km"]
        groups = list(start["vp_locations"].values())         # own of side 0, unclaimed, own of side 1
        owners = (0, None, 1)
        # "vp" is the game's value, the one its Victory Status page counts; "brief_vp" the briefing's. They differ by
        # scenario: Donau the same (dn-ai1 11-01: briefing 7,000, VP Locations 7,000), Alamo five times (briefing
        # 400 / 400 / 300, opening VP Locations 4,000 + 1,500), so start.json says vp_cell_factor where it is not 1.
        # A location can be worth more to one side than to the other (tools/fpc_objectives.py: High Sticking's 1507 is
        # 500 to side 0, 1,500 to side 1): such an entry is [hex, vp to side 0, vp to side 1], its "vp" the larger.
        factor = start.get("vp_cell_factor", 1)
        self.vp_cells = {}
        for i, g in enumerate(groups[:3]):
            for h, *v in g:
                side_vp = [int(x) * factor for x in (v + v)[:2]]
                self.vp_cells[int(h)] = {"vp": max(side_vp), "side_vp": side_vp, "brief_vp": int(v[0]), "owner": owners[i]}
        # vp-table.json (tools/s2_vp_table.py): the VP value of each subunit type in this scenario, by national era and
        # name; None when the scenario has none (then the referee cannot compute the share)
        self.subunit_vp = None
        if os.path.isfile(os.path.join(d, "vp-table.json")):
            with open(os.path.join(d, "vp-table.json"), encoding="utf-8") as f:
                rows = json.load(f)["table"]
            self.subunit_vp = {(r["era"], r["name"]): r["vp"] for r in rows}
            for r in rows:
                self.subunit_vp.setdefault(("", r["name"]), r["vp"])
        # units.json: every unit of the scenario per side (load snapshot): {"sides": {"0": [{name, type, parent,
        # subunits, arrives_later?}], "1": [...]}}; None when the data has no roster (then no small experiments)
        self.roster = None
        if os.path.isfile(os.path.join(d, "units.json")):
            with open(os.path.join(d, "units.json"), encoding="utf-8") as f:
                self.roster = {int(s): rows for s, rows in json.load(f)["sides"].items()}
        self.terrain = {}
        self.cols = self.rows = 0
        tsv = [f for f in os.listdir(d) if f.startswith("terrain-") and f.endswith(".tsv")]
        if tsv:
            self._load_terrain(os.path.join(d, tsv[0]))
        self.col_km = self.map_km[0] / max(self.cols, 1)
        self.row_km = self.map_km[1] / max(self.rows, 1)

    def cell_vp(self, h, side):
        """What VP cell h counts for side when that side holds it."""
        return self.vp_cells[h]["side_vp"][side]

    def vp_fields(self, h, side):
        """{"vp": what cell h counts for side} and, when the enemy counts it otherwise, its "enemy_vp" too."""
        own, other = self.cell_vp(h, side), self.cell_vp(h, 1 - side)
        return {"vp": own, **({"enemy_vp": other} if other != own else {})}

    def _load_terrain(self, path):
        cols = ("hex", "elev_m", "height_m", "terrain", "road_sides", "water_sides", "slope_sides", "other_sides")
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        for line in lines:
            m = re.match(r"# \S+ (\d+) x (\d+)", line)
            if m:
                self.cols, self.rows = int(m[1]), int(m[2])
            if not line or line.startswith("#") or line.startswith("hex\t"):
                continue
            f = (line.split("\t") + [""] * 8)[:8]
            row = dict(zip(cols, f))
            row["hex"], row["elev_m"], row["height_m"] = int(f[0]), int(f[1]), int(f[2])
            self.terrain[row["hex"]] = row

    def valid_hex(self, h):
        return isinstance(h, int) and not isinstance(h, bool) and (h in self.terrain if self.terrain else h > 0)

    def in_start_area(self, side, h):
        c0, c1, r0, r1 = self.start_areas[side]
        return c0 <= h // 100 <= c1 and r0 <= h % 100 <= r1

    def xy_km(self, h):
        col, row = h // 100, h % 100
        return (col - 1) * self.col_km, (row - 1) * self.row_km + (self.row_km / 2 if col % 2 == 0 else 0)

    def dist_km(self, a, b):
        (ax, ay), (bx, by) = self.xy_km(a), self.xy_km(b)
        return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


# --- hex geometry: flat-topped, hex number = column * 100 + row, both counted from 1, even columns half a hex lower ---
# (the "odd-q" offset layout of 0-based columns); cube coordinates (x, y, z) for distances and straight lines.
def hex_cube(h):
    c, r = h // 100 - 1, h % 100 - 1
    z = r - (c - (c & 1)) // 2
    return c, -c - z, z


def cube_hex(x, z):
    return (x + 1) * 100 + z + (x - (x & 1)) // 2 + 1


def _round(fx, fy, fz):
    x, y, z = round(fx), round(fy), round(fz)
    dx, dy, dz = abs(x - fx), abs(y - fy), abs(z - fz)
    if dx > dy and dx > dz:
        x = -y - z
    elif dy > dz:
        y = -x - z
    else:
        z = -x - y
    return x, z


def hex_dist(a, b):
    """Hexes between two hex numbers (0 = the same hex, 1 = neighbours)."""
    (ax, ay, az), (bx, by, bz) = hex_cube(a), hex_cube(b)
    return max(abs(ax - bx), abs(ay - by), abs(az - bz))


def is_artillery_icon(icon):
    low = str(icon or "").lower()
    return any(w in low for w in rules.ARTILLERY_ICON_WORDS)


def hex_line(a, b):
    """Hex numbers a straight line from a to b passes, both ends included (cell by cell)."""
    (ax, ay, az), (bx, by, bz) = hex_cube(a), hex_cube(b)
    n = max(abs(ax - bx), abs(ay - by), abs(az - bz))
    if n == 0:
        return [a]
    # a tiny nudge keeps lines that run exactly along a cell edge from flipping between the two cells
    fa, fb = (ax + 1e-6, ay + 2e-6, az - 3e-6), (bx + 1e-6, by + 2e-6, bz - 3e-6)
    out = []
    for i in range(n + 1):
        t = i / n
        x, z = _round(*(p + (q - p) * t for p, q in zip(fa, fb)))
        out.append(cube_hex(x, z))
    return out


def _loc(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class World:
    def __init__(self, scenario):
        self.sc = scenario
        self.state = None
        self.prev = None
        self.sec = 0
        self.side_of = {}                                       # every unit name ever seen -> side
        self.total_subs = {0: 0, 1: 0}                          # subunits at the first state: strength denominator
        self.start_subs = {}                                    # unit -> subunits when first seen (loss lists)
        self.owner = {h: c["owner"] for h, c in scenario.vp_cells.items()}
        self.start_vp = None                                    # side -> vp() at the first state

    def update(self, state):
        first = self.state is None
        self.prev, self.state = self.state, state
        self.sec = self.sec if state.get("sec") is None else int(state["sec"])
        for n, u in state["units"].items():
            side = u.get("s", {}).get("side")
            if side in (0, 1):
                self.side_of[n] = side
                self.start_subs.setdefault(n, len(u.get("u") or []))
                if first:
                    self.total_subs[side] += len(u.get("u") or [])
        for h, sides in self.vp_passes(self.prev, state).items():
            if len(sides) == 1:                                 # both sides in the same minute: stays as it was
                self.owner[h] = next(iter(sides))
        if first and self.sc.subunit_vp is not None:
            self.start_vp = {s: self.vp(s) for s in (0, 1)}

    def vp(self, side):
        """The side's Victory Status parts as the game counts them (checked 11-01 on The Armored Fist end screens:
        af-pb4 1st Bde 33,305 surviving + 500 locations = 33,805 total, 7. PD 28,655 + 6,000 = 34,655, share 49 %;
        af-ai1b 36 %): surviving forces = VP of every subunit still a Runner (damaged, Fallen Out, counts as lost, as
        destroyed does; units not yet arrived count), plus the VP cells the side holds at the game's value. "Enemy
        losses inflicted" on that page is shown but not added. None without a subunit VP table."""
        table = self.sc.subunit_vp
        if table is None:
            return None
        forces = 0.0
        for u in self.own(side).values():
            era = (u.get("s") or {}).get("national_era") or ""
            for x in u.get("u") or []:
                if x.get("status") == rules.SUB_OK:
                    forces += table.get((era, x.get("name")), table.get(("", x.get("name")), 0.0))
        cells = sum(self.sc.cell_vp(h, side) for h in self.sc.vp_cells if self.owner.get(h) == side)
        return {"forces": round(forces), "cells": cells, "total": round(forces) + cells}

    def vp_passes(self, prev, state):
        """{vp hex: {sides}} whose ground units stood on the cell, or crossed it on the straight line from last
        minute's position to this minute's (rules FM01 section 30: ground units passing take the cell; helicopters
        and aircraft do not). The game does not report cell owners, so this is the referee's own reckoning."""
        vp = self.sc.vp_cells
        out = {h: set() for h in vp}
        for name, u in state["units"].items():
            s = u.get("s") or {}
            if s.get("side") not in (0, 1) or s.get("status") != rules.GAME_STATUS_ACTIVE \
                    or s.get("icon_type") in rules.AIR_ICONS:
                continue
            here = _loc(s.get("location"))
            if here is None:
                continue
            path, pu = [here], prev and prev["units"].get(name)
            there = _loc(((pu or {}).get("s") or {}).get("location")) \
                if pu and (pu.get("s") or {}).get("status") == rules.GAME_STATUS_ACTIVE else None
            if there is not None and there != here:
                path = hex_line(there, here)
            for h in path:
                if h in out:
                    out[h].add(s["side"])
        return {h: sides for h, sides in out.items() if sides}

    @property
    def units(self):
        return self.state["units"] if self.state else {}

    def own(self, side):
        """{name: unit} of one side, any status."""
        return {n: u for n, u in self.units.items() if self.side_of.get(n) == side}

    def present(self, side, name):
        """The unit exists, belongs to side and is on the map and alive (status Active)."""
        u = self.units.get(name)
        return bool(u) and self.side_of.get(name) == side and u.get("s", {}).get("status") == rules.GAME_STATUS_ACTIVE

    def location(self, name):
        u = self.units.get(name)
        return u["s"].get("location") if u else None

    def names(self, side):
        return [n for n, s in self.side_of.items() if s == side]

    def strength(self, side):
        """(runners, initial subunits): subunits still able to fight over the subunits at the start."""
        ok = sum(1 for u in self.own(side).values() for s in (u.get("u") or []) if s.get("status") == rules.SUB_OK)
        return ok, max(self.total_subs[side], 1)

    def strength_pct(self, side):
        ok, tot = self.strength(side)
        return round(100.0 * ok / tot, 1)

    def losses(self, side):
        """Butcher's-bill rows of one side from the latest state: subunits when the unit was first seen, subunits still
        able to fight, status; units seen before but missing from the latest state are listed as such."""
        rows, units = [], self.units
        for n in sorted(self.names(side)):
            u = units.get(n)
            s, subs = ((u or {}).get("s") or {}), ((u or {}).get("u") or [])
            rows.append({"unit": n, "type": s.get("icon_type"), "status": s.get("status") if u else "not in last state",
                         "subunits_start": self.start_subs.get(n, 0),
                         "subunits_now": sum(1 for x in subs if x.get("status") == rules.SUB_OK)})
        return rows
