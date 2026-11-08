"""Per-side event feed, built from the change between two minute states plus the log-driven events.

Every event is {"id", "t" (game second), "type", ...}. The feed of a side only ever holds things that side may know:
its own units' changes, public VP changes, contacts by contact number. Event types are the subscribable ones of
materials/10 2.6: orders_phase new_contact combat unit_out strength_drop vp_change order_done script_alarm timer
contact_near.
"""
from . import rules

DONE_BY_REMOVAL = {"Barrage", "Resupply", "Reorganize"}


class EventLog:
    def __init__(self, log):
        self.log = log
        self.items = {0: [], 1: []}
        self._id = 0

    def emit(self, side, etype, t, **fields):
        self._id += 1
        ev = {"id": self._id, "t": t, "type": etype, **fields}
        self.items[side].append(ev)
        self.log.write("events", {"side": side, **ev})
        return ev

    def since(self, side, after_id=0, limit=200):
        return [e for e in self.items[side] if e["id"] > after_id][-limit:]


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def diff(prev, cur, side_of, hq, owners_before, owners_after, vp_value):
    """[(side or None for both, type, fields)] from two consecutive full states (None prev = first state)."""
    out = []
    if prev is not None:
        for name, u in cur["units"].items():
            side, pu = side_of.get(name), prev["units"].get(name)
            if side is None or pu is None:
                continue
            s, ps = u.get("s", {}), pu.get("s", {})
            if ps.get("status") == rules.GAME_STATUS_ACTIVE and s.get("status") == rules.DEAD_STATUS:
                out.append((side, "unit_out", {"unit": name, "hq": name == hq[side]}))
            lost = sum(1 for a, b in zip(pu.get("u") or [], u.get("u") or [])
                       if a.get("status") == rules.SUB_OK and b.get("status") != rules.SUB_OK)
            fired = sum(max(_int(x.get("rounds")) - _int(y.get("rounds")), 0)
                        for a, b in zip(pu.get("u") or [], u.get("u") or [])
                        for x, y in zip(a.get("ammo") or [], b.get("ammo") or []))
            if lost:
                out.append((side, "combat", {"unit": name, "kind": "casualty", "n": lost}))
            elif fired:
                out.append((side, "combat", {"unit": name, "kind": "fire", "n": fired}))
            if s.get("status") == rules.GAME_STATUS_ACTIVE and ps.get("status") == rules.GAME_STATUS_ACTIVE:
                po, o = pu.get("o") or [], u.get("o") or []
                done = None
                if po and po[0].get("destination_location") is not None and \
                        str(s.get("location")) == str(po[0]["destination_location"]) != str(ps.get("location")):
                    done = po[0].get("orders")
                elif po and po[0].get("orders") in DONE_BY_REMOVAL and (not o or o[0].get("orders") != po[0]["orders"]):
                    done = po[0]["orders"]
                if done:
                    out.append((side, "order_done", {"unit": name, "order": done}))
    for h, owner in owners_after.items():
        if owners_before.get(h, owner) != owner:
            out.append((None, "vp_change", {"hex": h, "vp": vp_value.get(h, 0), "from": owners_before[h], "to": owner}))
    return out
