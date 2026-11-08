"""Own artillery fire as the side's ammunition shows it (materials/18 2.1): fire_report and fire_pending events.

A released Barrage is no proof that the guns fired (P5 game 1: three battalions ordered onto VP cells fired nothing and
the side learnt it an hour and a half later, materials/17 3.2). Every minute state the referee compares the rounds of
each subunit of the side's indirect-fire units with the previous state. A unit counts as indirect-fire when its icon
looks like artillery (rules.ARTILLERY_ICON_WORDS: howitzers, mortars, rockets) or it holds or was given a Barrage /
Counter Battery order. A drop is a fire_report for that side only: unit, game second and clock, rounds (by ammunition
type), its current order and the hex that order aims at (when it has one; an enemy unit the order names becomes the
side's contact number, or `target_hidden`). A Barrage released for a unit that has not fired rules.FIRE_PENDING_SEC
game seconds later gives one fire_pending reminder. Nothing of the enemy is read here.
"""
from . import rules
from .world import is_artillery_icon


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _hex(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class FireWatch:
    def __init__(self, side_of, clock_str):
        """side_of: {unit name: side} (World.side_of); clock_str(sec) -> "HH:MM:SS" of a game second."""
        self.side_of, self.clock_str = side_of, clock_str
        self.reports = {0: [], 1: []}          # the fire_report / fire_pending events emitted, per side
        self.given = {0: set(), 1: set()}      # units of a side given an indirect-fire order by the queue
        self.pending = {}                      # unit -> {"side", "sec", "cmd_id", "target"} of a released Barrage
        self.last_fire = {}                    # unit -> game second of its last fire_report
        self.own = {}                          # unit -> {"mins", "until"} of a released Barrage, for called_by

    def indirect(self, name, u):
        s = u.get("s") or {}
        side = self.side_of.get(name)
        return is_artillery_icon(s.get("icon_type")) or name in self.given.get(side, ()) or \
            any(o.get("orders") in rules.INDIRECT_ORDERS for o in u.get("o") or [])

    def on_release(self, side, unit, orders, cmd_id, sec):
        """A released orders command came back ok from the game: note an indirect-fire order and arm the reminder."""
        names = [o.get("orders") for o in orders or []]
        if any(n in rules.INDIRECT_ORDERS for n in names):
            self.given[side].add(unit)
        bar = next((o for o in orders or [] if o.get("orders") == "Barrage"), None)
        if bar is not None:
            self.pending[unit] = {"side": side, "sec": sec, "cmd_id": cmd_id, "target": _hex(bar.get("target_location"))}
            self.own[unit] = {"mins": _int(bar.get("barrage_duration_mins")) or 10, "until": None}

    @staticmethod
    def _aim(orders):
        """(order name, aim hex, contact no, hidden) of a unit's current orders, already cleaned for its side."""
        if not orders:
            return None, None, None, False
        o = next((x for x in orders if x.get("orders") in rules.INDIRECT_ORDERS), orders[0])
        hx = _hex(o.get("target_location"))
        if hx is None and o.get("orders") in rules.INDIRECT_ORDERS:
            hx = _hex(o.get("destination_location"))
        return o.get("orders"), hx, o.get("target_contact"), bool(o.get("target_hidden"))

    def _caller(self, side, name, order, sec):
        """Who made the guns fire: the side's own (an order or a script) when the side's queue released a Barrage for
        the unit (its first fire after the release and the barrage minutes after it, since the unit drops back to On
        Call once the barrage is shot) or gave it Counter Battery; anything else means the game's fire support centre
        called the unit on its own. The order name alone does not tell: the centre puts its On Call units on Barrage
        (round 4 game 1: the Soviet 1/21 RAG fired 36 rounds on a Barrage no command or script had given). Season 2
        EET: DeepSeek blamed its own script for such fire and deleted it."""
        own = self.own.get(name)
        if own is not None and own["until"] is None:
            own["until"] = sec + own["mins"] * 60 + 120
        if own is not None and sec > own["until"]:
            self.own.pop(name)
            own = None
        mine = own is not None or (order == "Counter Battery" and name in self.given.get(side, ()))
        return "own_order" if mine else "fscc"

    def update(self, prev, cur, sec, clean_orders):
        """Two consecutive minute states -> [(side, type, fields)]. clean_orders(side, orders) shows a unit's orders as
        its side may see them (views.Views._clean_orders)."""
        out = []
        if prev is None:
            return out
        for name, u in cur["units"].items():
            side, pu = self.side_of.get(name), prev["units"].get(name)
            if side not in (0, 1) or pu is None or not self.indirect(name, u):
                continue
            by_type, n = {}, 0
            for a, b in zip(pu.get("u") or [], u.get("u") or []):
                old = {x.get("ammo_id"): x for x in a.get("ammo") or []}
                for y in b.get("ammo") or []:
                    x = old.get(y.get("ammo_id"))
                    d = _int(x.get("rounds")) - _int(y.get("rounds")) if x is not None else 0
                    if d > 0:
                        k = y.get("type") or y.get("ammo_id") or "?"
                        by_type[k] = by_type.get(k, 0) + d
                        n += d
            if n:
                order, hx, contact, hidden = self._aim(clean_orders(side, u.get("o")))
                f = {"unit": name, "rounds": n, "by_type": by_type, "clock": self.clock_str(sec), "order": order,
                     "target_hex": hx, "called_by": self._caller(side, name, order, sec)}
                if contact is not None:
                    f["target_contact"] = contact
                if hidden:
                    f["target_hidden"] = True
                out.append((side, "fire_report", f))
                self.last_fire[name] = sec
                self.pending.pop(name, None)
        for name, p in list(self.pending.items()):
            u = cur["units"].get(name)
            if u is None or (u.get("s") or {}).get("status") != rules.GAME_STATUS_ACTIVE:
                self.pending.pop(name)
            elif sec - p["sec"] >= rules.FIRE_PENDING_SEC:
                self.pending.pop(name)
                out.append((p["side"], "fire_pending", {
                    "unit": name, "cmd_id": p["cmd_id"], "target_hex": p["target"], "released_sec": p["sec"],
                    "released_clock": self.clock_str(p["sec"]), "minutes": (sec - p["sec"]) // 60,
                    "text": f"{name} 的炮击命令放行 {(sec - p['sec']) // 60} 游戏分钟了还没开火：目标多半看不到、"
                            "不在射程内，或单位在移动 / 没弹药；查 unit show 和 contact list"}))
        return out

    def note(self, side, ev):
        self.reports[side].append(ev)

    def view(self, side, sec):
        return {"reports": self.reports[side][-200:],
                "pending": [{"unit": n, "cmd_id": p["cmd_id"], "target_hex": p["target"], "released_sec": p["sec"],
                             "released_clock": self.clock_str(p["sec"]), "waiting_min": max(sec - p["sec"], 0) // 60}
                            for n, p in sorted(self.pending.items()) if p["side"] == side],
                "pending_after_min": rules.FIRE_PENDING_SEC // 60,
                "note": "rounds are what the side's own ammunition counts lost between two game minutes"}
