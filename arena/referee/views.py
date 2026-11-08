"""What an agent (or a script) may see of the game, and the fog assertion every view must pass.

A side sees: all of its own units, the public VP cells, the map, and the enemy only as contacts from its own
contact table (contact number, state, the game's report text, last position and time) - never an enemy unit name.
Enemy entries are recognised by the key "contact". check() verifies, on the finished view and before it leaves the
referee, that each such entry matches the table (known number, only allowed keys, report / position / state that the
table really recorded) and that no enemy unit name occurs anywhere else in the view. A failing view is withheld and
logged as fog_violation.
"""
import json
import re

from . import rules

CONTACT_KEYS = {"contact", "state", "state_name", "report", "size", "visible", "first_seen", "last_seen", "last_pos"}
EVENT_KEYS = {"id", "t", "type", "pos", "regained", "dist_km", "sub"}
# views.build() also answers: queue/<cmd_id> (one own command as sent), score (scoring.py), fires (fires.py)
SIDE_NAMES = {0: "Blue (Player 1)", 1: "Red (Player 2)"}
VP_SOURCE = "referee's reckoning from ground units passing (the game does not report cell owners)"
VP_RULE = ("a VP cell belongs to the side whose ground units stood on it or crossed it since the last game minute; "
           "helicopters and aircraft do not count; both sides in the same minute: owner unchanged. " + VP_SOURCE)


class FogViolation(Exception):
    def __init__(self, problems):
        super().__init__("; ".join(problems[:3]))
        self.problems = problems


def clock_str(sc, sec):
    c = (sc.start_clock + int(sec)) % 86400
    return f"{c // 3600:02d}:{c % 3600 // 60:02d}:{c % 60:02d}"


def est_tokens(obj):
    return len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False)) // rules.CHARS_PER_TOKEN


class Views:
    def __init__(self, ref):
        self.ref = ref
        self._rx = {}

    # --- own units ---
    def _clean_orders(self, side, orders):
        """Scenario orders may name the enemy ("Screen" target_unit = an enemy unit): show a contact number if the
        side holds that contact, else only that the target is hidden."""
        out = []
        for o in orders or []:
            t = o.get("target_unit")
            if t is not None and self.ref.world.side_of.get(t) != side:
                o = {k: v for k, v in o.items() if k != "target_unit"}
                c = self.ref.tables[side].by_unit.get(t)
                if c is not None and c.first_seen is not None:
                    o["target_contact"] = c.no
                else:
                    o["target_hidden"] = True
            out.append(o)
        return out

    def _unit_row(self, side, name, u):
        s, o = u.get("s", {}), self._clean_orders(side, u.get("o"))
        subs = u.get("u") or []
        first = {k: o[0][k] for k in ("orders", "destination_location", "target_unit", "target_contact", "target_hidden")
                 if k in o[0]} if o else None
        return {"name": name, "parent": s.get("parent_name"), "hex": s.get("location"), "status": s.get("status"),
                "morale": s.get("morale"), "readiness": s.get("readiness"), "icon": s.get("icon_type"),
                "order": first, "orders": len(o),
                "subs_ok": sum(1 for x in subs if x.get("status") == rules.SUB_OK), "subs": len(subs)}

    def units(self, side):
        return {"units": [self._unit_row(side, n, u) for n, u in sorted(self.ref.world.own(side).items())]}

    def unit(self, side, name):
        u = self.ref.world.own(side).get(name)
        if u is None:
            return None
        return {"name": name, "status": u.get("s"), "orders": self._clean_orders(side, u.get("o")),
                "subunits": u.get("u")}

    # --- enemy, public ---
    def contacts(self, side):
        return {"contacts": [c.public() for c in self.ref.tables[side].all()]}

    def vp(self, side):
        w = self.ref.world
        return {"vp": [{"hex": h, **w.sc.vp_fields(h, side), "owner": "none" if w.owner.get(h) is None else
                        ("you" if w.owner[h] == side else "enemy")} for h, c in sorted(w.sc.vp_cells.items())],
                "source": VP_SOURCE}

    def terrain(self, params):
        sc = self.ref.sc
        if "box" in params:
            c0, c1, r0, r1 = (int(x) for x in params["box"].split(","))
            hexes = [c * 100 + r for c in range(c0, c1 + 1) for r in range(r0, r1 + 1)]
        else:
            hexes = [int(x) for x in params.get("hexes", "").split(",") if x]
        if len(hexes) > rules.HEX_QUERY_MAX:
            raise ValueError(f"at most {rules.HEX_QUERY_MAX} hexes per query")
        return {"terrain": [sc.terrain[h] for h in hexes if h in sc.terrain]}

    def briefing(self, side):
        sc = self.ref.sc
        c0, c1, r0, r1 = sc.start_areas[side]
        lv = sc.levels[side]
        # the lowest Contested share: the table's own (official_levels), else the enemy's win mirrored
        c_lo = lv[3] if len(lv) > 3 else 101 - sc.levels[1 - side][2]
        return {"scenario": sc.name, "you": SIDE_NAMES[side], "your_command_post": sc.commanders[side],
                # the P4 exam: no agent plays the other side, the game's own AI does
                "opponent": "commander" if 1 - side in self.ref.players else "game_ai",
                "game_hours": sc.total_sec / 3600, "h_hour": clock_str(sc, 0),
                "map": {"columns": sc.cols, "rows": sc.rows, "km": sc.map_km,
                        "hex": "col*100+row, flat-topped, even columns half a hex lower"},
                "victory_share_needed": {"decisive": lv[0], "tactical": lv[1], "marginal": lv[2]},
                # the game's own end-of-game table (Victory Status page), not the briefing text's numbers
                "victory_rule": f"judged at the end by the game's Victory Status table on your VP share: >= {lv[2]}% is a win "
                                f"(Marginal / Tactical / Decisive at {lv[2]} / {lv[1]} / {lv[0]}%), "
                                f"{c_lo}-{lv[2] - 1}% Contested, <= {c_lo - 1}% a loss",
                "starting_area": {"columns": [c0, c1], "rows": [r0, r1]},
                "vp_cells": [{"hex": h, **sc.vp_fields(h, side)} for h in sorted(sc.vp_cells)],
                "vp_rule": VP_RULE,
                # the notes and scripts in this side's workspace are the player's own, carried from the training
                **({"workspace": "carried"} if getattr(self.ref, "carried", {}).get(side) else {}),
                **({"exercise": self._exercise(side)} if getattr(self.ref, "exercise", False) else {})}

    def _exercise(self, side):
        """A training-period exercise (2.10): not scored; how it is paced and which units this side may use."""
        r = self.ref
        out = {"scored": False, "pacing": r.pacing,
               "opponent": "self" if len(r.players) == 2 else "game_ai"}
        if r.experiment.get(side):
            out["small_experiment"] = {"units": list(r.experiment[side]), "game_minutes": rules.EXERCISE_SMALL_GAME_MIN}
        return out

    def brief_both(self):
        """Training (P4): the match scenario's briefing of both sides, each with its unit roster (names for small
        experiments). Both players get the same; in exercises they may play either side anyway (2.10)."""
        sc, sides = self.ref.sc, {}
        for s in (0, 1):
            b = self.briefing(s)
            b.pop("scenario")
            b["side"] = b.pop("you")
            b["command_post"] = b.pop("your_command_post")
            b["units"] = [dict(r) for r in (sc.roster or {}).get(s, [])]
            sides[str(s)] = b
        out = {"scenario": sc.name, "sides": sides,
               "note": "both sides' briefings; play_side 0 or 1 of an exercise is one of these sides"}
        if getattr(self.ref, "supplement", None):
            out["supplement"] = self.ref.supplement
        return out

    def queue(self, side):
        return {"queue": self.ref.queue.listing(side, include_done=True)}

    def thinking(self, side):
        return self.ref.thinking.view(side)

    def events(self, side, params):
        after = int(params.get("since", 0))
        return {"events": self.ref.events.since(side, after)}

    # --- the wake summary ---
    def summary(self, side, reasons=None):
        r = self.ref
        w, sc = r.world, r.sc
        since = r.summary_from[side]
        snap = r.own_snapshot[side]
        changes = []
        for n, u in sorted(w.own(side).items()):
            row = self._unit_row(side, n, u)
            old = snap.get(n)
            if old is None or old != (row["hex"], row["status"], row["subs_ok"]):
                changes.append({"name": n, "hex": row["hex"], "from_hex": old[0] if old else None,
                                "status": row["status"], "subs_ok": row["subs_ok"], "subs": row["subs"],
                                "order": row["order"]})
        tab = r.tables[side]
        vis = [c.public() for c in tab.all() if c.visible]
        lost = [c.public() for c in tab.all() if not c.visible][:12]
        ok, tot = w.strength(side)
        # no thinking time here: the summary is what the player reads (r5-minden 11-04: time shown in every wake and
        # tool result framed each wake as "order and end"; the referee keeps the figure in its logs and final.json)
        out = {"game": {"sec": r.sec, "clock": clock_str(sc, r.sec), "remaining_sec": max(sc.total_sec - r.sec, 0)},
               "side": side, "wake": reasons or [],
               "turn": r.turn_info(side),
               "force": {"pct": w.strength_pct(side), "subunits_ok": ok, "subunits": tot,
                         "units_out": sum(1 for u in w.own(side).values() if u["s"].get("status") == rules.DEAD_STATUS)},
               "events": r.events.since(side, since),
               "own_changes": changes,
               "contacts_in_view": vis, "contacts_lost": lost,
               "vp": self.vp(side)["vp"], "vp_source": VP_SOURCE,
               "queue": r.queue.listing(side),
               "next_orders_phase": r.next_orders(side)}
        for key in ("events", "own_changes", "contacts_lost", "contacts_in_view"):
            while est_tokens(out) > rules.SUMMARY_MAX_TOKENS and out[key]:
                out[key] = out[key][len(out[key]) // 2 + (1 if key == "events" else 0):] if key == "events" \
                    else out[key][:len(out[key]) // 2]
                out["truncated"] = True
        return out

    # --- dispatch + assertion ---
    def build(self, side, what, params):
        if what == "summary":
            return self.summary(side)
        simple = {"units": self.units, "contacts": self.contacts, "vp": self.vp, "briefing": self.briefing,
                  "queue": self.queue, "thinking": self.thinking}
        if what in simple:
            return simple[what](side)
        if what == "terrain":
            return self.terrain(params)
        if what == "events":
            return self.events(side, params)
        if what.startswith("unit/"):
            return self.unit(side, what[5:])
        if what.startswith("queue/"):
            return self.ref.queue.detail(side, what[6:])
        if what == "score":
            from . import scoring                     # scoring imports this module
            return scoring.score(self.ref, side)
        if what == "fires":
            return self.ref.fires.view(side, self.ref.sec)
        return None

    def render(self, side, what, params=None):
        view = self.build(side, what, params or {})
        if view is not None and what not in ("briefing", "terrain"):
            self.check(side, view, what)
        return view

    def _names_rx(self, side):
        names = sorted((n for n in self.ref.world.names(1 - side) if len(n) >= 5 or " " in n or "/" in n),
                       key=len, reverse=True)
        key = (side, len(names))
        if key not in self._rx:
            self._rx[key] = re.compile(r"(?<![A-Za-z0-9/])(?:" + "|".join(map(re.escape, names)) + r")(?![A-Za-z0-9/])") \
                if names else None
        return self._rx[key]

    def check(self, side, view, what):
        r, tab = self.ref, self.ref.tables[side]
        problems, reports = [], []

        def walk(x):
            if isinstance(x, dict):
                # a script alarm's text is the side's own writing (round 4 game 2: Kimi's contact_watch, written for the
                # US side, said "consider FA Bty barrage" while it played the Soviets, and 12 summaries were withheld)
                if x.get("type") == "script_alarm" and isinstance(x.get("text"), str):
                    reports.append(x["text"])
                if "contact" in x:
                    c = tab.by_no.get(x["contact"])
                    if c is None or c.first_seen is None:
                        problems.append(f"contact {x['contact']} is not in the contact table")
                    else:
                        extra = set(x) - CONTACT_KEYS - EVENT_KEYS
                        if extra:
                            problems.append(f"contact {x['contact']}: fields {sorted(extra)} are not allowed")
                        if "report" in x:
                            reports.append(x["report"])
                            if x["report"] not in c.reports:
                                problems.append(f"contact {x['contact']}: report not recorded for this contact")
                        for k in ("last_pos", "pos"):
                            if x.get(k) is not None and x[k] not in c.positions:
                                problems.append(f"contact {x['contact']}: {k} {x[k]} was never reported")
                        if x.get("state") is not None and x["state"] not in c.states:
                            problems.append(f"contact {x['contact']}: state {x['state']} was never seen")
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)

        walk(view)
        text = json.dumps(view, ensure_ascii=False)
        for rep in reports:
            text = text.replace(json.dumps(rep, ensure_ascii=False)[1:-1], "")
        rx = self._names_rx(side)
        m = rx.search(text) if rx else None
        if m:
            problems.append(f"enemy unit name in view: {m[0]!r}")
        if problems:
            r.log.write("fog", {"t": r.clock(), "sec": r.sec, "kind": "fog_violation", "side": side, "view": what,
                                "problems": problems[:10]})
            r.fog_violations += 1
            raise FogViolation(problems)
