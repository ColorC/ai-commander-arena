"""Subscriptions and the wake decision (materials/10 2.6).

A subscription is {"type": <event type>, ...filters}; an idle side is woken by the first matching event, events
within WAKE_MERGE_SEC of it join the same wake. `merge_sec` on a subscription (default 0, new_contact default
600, script_alarm always at least 300) is the quiet time after one of its events woke the side. Matching events that
arrive while the side is active only go to the feed.
"""
from . import rules

_ALLOWED = {
    "orders_phase": {"merge_sec"}, "unit_out": {"merge_sec"}, "vp_change": {"merge_sec"}, "combat": {"merge_sec"},
    "order_done": {"merge_sec"}, "script_alarm": {"merge_sec"},
    "new_contact": {"merge_sec", "area", "min_size"},
    "strength_drop": {"merge_sec", "points"},
    "timer": {"merge_sec", "at", "every_min"},
    "contact_near": {"merge_sec", "center", "radius_km"},
}


def _num(v, lo, hi, name):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ValueError(f"{name} must be a number in [{lo}, {hi}]")
    return v


def normalize(raw, valid_hex):
    """Validate a subscription list from an agent; returns clean copies or raises ValueError."""
    if not isinstance(raw, list) or len(raw) > rules.MAX_SUBSCRIPTIONS:
        raise ValueError(f"subscriptions must be a list of at most {rules.MAX_SUBSCRIPTIONS}")
    out = []
    for s in raw:
        if not isinstance(s, dict) or s.get("type") not in rules.SUB_TYPES:
            raise ValueError(f"unknown subscription type: {s!r}")
        extra = set(s) - _ALLOWED[s["type"]] - {"type"}
        if extra:
            raise ValueError(f"{s['type']}: unknown fields {sorted(extra)}")
        c = dict(s)
        if "merge_sec" in c:
            _num(c["merge_sec"], 0, 24 * 3600, "merge_sec")
        t = c["type"]
        if t == "timer":
            if ("at" in c) == ("every_min" in c):
                raise ValueError("timer needs exactly one of at / every_min")
            if "at" in c:
                _num(c["at"], 0, 24 * 3600, "at")
            else:
                _num(c["every_min"], rules.TIMER_MIN_MINUTES, 24 * 60, "every_min")
        elif t == "strength_drop":
            c["points"] = _num(c.get("points", rules.DEFAULT_DROP_POINTS), 1, 100, "points")
        elif t == "contact_near":
            if not valid_hex(c.get("center")):
                raise ValueError("contact_near: center is not a hex of the map")
            _num(c.get("radius_km"), 0.1, 50, "radius_km")
        elif t == "new_contact":
            if "min_size" in c:
                _num(c["min_size"], 1, 1000, "min_size")
            a = c.get("area")
            if a is not None:
                ok = isinstance(a, dict) and (
                    (set(a) == {"box"} and isinstance(a["box"], list) and len(a["box"]) == 4
                     and all(isinstance(x, int) and not isinstance(x, bool) for x in a["box"]))
                    or (set(a) == {"center", "radius_km"} and valid_hex(a["center"])
                        and _num(a["radius_km"], 0.1, 50, "radius_km")))
                if not ok:
                    raise ValueError("new_contact.area must be {box:[col0,col1,row0,row1]} or {center, radius_km}")
        out.append(c)
    return out


class WakeManager:
    def __init__(self, scenario, log):
        self.sc, self.log = scenario, log
        self.subs = {0: [], 1: []}
        self.pending = {0: None, 1: None}
        self._n = 0

    def set_subs(self, side, raw, sec):
        subs = normalize(raw, self.sc.valid_hex)
        for s in subs:
            self._n += 1
            s["id"], s["last_hit"], s["inside"] = f"sub{self._n}", None, set()
            if s["type"] == "timer":
                s["next"] = s["at"] if "at" in s else sec + int(s["every_min"] * 60)
        self.subs[side] = subs
        return [{k: v for k, v in s.items() if k not in ("last_hit", "inside", "next")} for s in subs]

    def timers_due(self, side, sec):
        """Timer events due at game second `sec` -> [(sub id, scheduled second)]; repeating timers move on."""
        out = []
        for s in self.subs[side]:
            if s["type"] == "timer" and s.get("next") is not None and sec >= s["next"]:
                out.append((s["id"], s["next"]))
                if "every_min" in s:
                    step = int(s["every_min"] * 60)
                    s["next"] += step * ((sec - s["next"]) // step + 1)
                else:
                    s["next"] = None
        return out

    def _matches(self, s, ev):
        if s["type"] != ev["type"]:
            return False
        t = s["type"]
        if t == "strength_drop":
            return ev["drop"] >= s["points"]
        if t in ("timer", "contact_near"):
            return ev.get("sub") == s["id"]
        if t == "new_contact":
            if ev["size"] < s.get("min_size", 1):
                return False
            a, pos = s.get("area"), ev.get("pos")
            if a and pos is not None:
                if "box" in a:
                    c0, c1, r0, r1 = a["box"]
                    return c0 <= pos // 100 <= c1 and r0 <= pos % 100 <= r1
                return self.sc.dist_km(a["center"], pos) <= a["radius_km"]
        return True

    def _enqueue(self, side, ev, sub):
        p = self.pending[side]
        if p is None:
            p = self.pending[side] = {"first": ev["t"], "due": ev["t"] + rules.WAKE_MERGE_SEC, "events": []}
        p["events"].append({"id": ev["id"], "type": ev["type"], "sub": sub})

    def offer(self, side, ev, active):
        """Feed one event of `side`. Returns the sub id it matched (None = no wake candidate)."""
        if ev.get("forced"):                       # a script switched off for going over its limits
            if not active:
                self._enqueue(side, ev, "forced")
            return "forced"
        for s in self.subs[side]:
            if not self._matches(s, ev):
                continue
            gap = s.get("merge_sec", 0)
            if ev["type"] == "script_alarm":
                gap = max(gap, rules.SCRIPT_ALARM_GAP_SEC)
            if s["last_hit"] is not None and ev["t"] - s["last_hit"] < gap:
                self.log.write("wakes", {"side": side, "kind": "merged", "sub": s["id"], "event": ev["id"]})
                return None
            if active:
                return None
            s["last_hit"] = ev["t"]
            self._enqueue(side, ev, s["id"])
            return s["id"]
        return None

    def due(self, side, sec):
        p = self.pending[side]
        return p if p and sec >= p["due"] else None

    def next_due(self):
        d = [p["due"] for p in self.pending.values() if p]
        for side in (0, 1):
            d += [s["next"] for s in self.subs[side] if s["type"] == "timer" and s.get("next") is not None]
        return min(d) if d else None

    def clear(self, side):
        self.pending[side] = None
