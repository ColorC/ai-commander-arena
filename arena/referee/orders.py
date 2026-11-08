"""Command queue: validation, hold until the side's own phase, release batches, results.

The game takes SetUnitOrders & co. at any moment (materials/11 P0.3), so nothing reaches the game except through
make_batch(), which hands out a side's queued commands only in that side's own Orders phase (deploy: the prematch
phases). A command is {cmd_id, side, kind, unit, args, status, ...}; statuses: queued released done failed rejected
withdrawn superseded dropped. Three illegal submissions in a row are a violation; after that illegal ones are dropped
without an answer that explains them.

Lua form of a batch (what fpc/lua/arena-bridge.lua reads as data, file release-<batch_id>.lua):
  return {batch_id=, side=, cmds={{cmd_id=, kind=, unit=, orders={..} | sop={..} | emission={..} | request={..} | location=}}}
A fire_support `contact` becomes `enemy_unit` (the true name) only here, and only if the contact is in view then.
Small experiment (materials/10 2.10): with `experiment[side]` = the unit names the side may use, a valid command for any
other unit (or a fire mission fired or spotted by one) is refused with "unit not in this experiment"; that refusal
does not count towards the illegal streak, and commands the referee itself queues (source "referee") are exempt.
lint() (materials/18 2.1) runs the same validation without queueing and without counting towards the streak, and adds
warnings (warnings()): a Barrage or fire mission aimed where no contact of the side is in view within
rules.FIRE_NEAR_HEXES (the game usually drops blind fire), an indirect-fire or helicopter order for a unit whose icon is
not of that kind, an SOP value the game has not been seen to take. submit() answers the same warnings with an accepted
command. Each command keeps the body the side sent (`display`), shown by detail(); the batch has the true names.
"""
import json
import os
import re

from . import lua_literal, rules
from .world import hex_dist, is_artillery_icon

_WHITELIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "doctrine_whitelist.json")
TIME = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def contact_no(v):
    """A contact reference as its number: 12, "12", "c12" and "#12" are all contact 12 (the CLI reads c12 / #12; a
    script's raw body may carry any of them, start task 11-01); anything else is returned as it is."""
    if isinstance(v, str):
        m = re.fullmatch(r"\s*[cC#]?(\d+)\s*", v)
        return int(m.group(1)) if m else v
    return v


def _simple(v, depth=0):
    """Scalar or short list/dict of scalars: what a doctrine / emission value may be."""
    if isinstance(v, bool) or _is_int(v):
        return True
    if isinstance(v, str):
        return len(v) <= 80
    if isinstance(v, list):
        return depth < 2 and len(v) <= 16 and all(_simple(x, depth + 1) for x in v)
    if isinstance(v, dict):
        return depth < 2 and len(v) <= 16 and all(isinstance(k, str) and len(k) <= 48 and _simple(x, depth + 1)
                                                  for k, x in v.items())
    return False


# what the agent reads when the game itself refused a released command (the API returns only false)
REFUSED = {
    None: "the game refused it (it gives no reason)",
    "orders": "the game refused it (it gives no reason); check in the manual that this kind of unit can carry out "
              "this order (Barrage and Counter Battery are artillery orders, Hunt a helicopter order)",
    "fire_support": "the game refused it (it gives no reason); on the game machine sead and direct_support requests were refused "
                    "however they were written, and enemy_target needs a contact in view",
}


class Invalid(Exception):
    pass


class Queue:
    def __init__(self, world, log, contact_unit, visible_unit=None, experiment=None, batch_prefix="", in_view=None):
        """contact_unit(side, contact_no) -> true enemy unit name, only for contacts that side has seen;
        visible_unit(side, contact_no) -> the same, only while the contact is in view (default: as contact_unit);
        in_view(side) -> [(contact_no, hex)] of the side's contacts in view now (blind-fire warning; default none);
        experiment: {side: set of unit names} for a small experiment; batch_prefix goes in front of every batch id
        (two exercise games on one machine share the relay's in\\ folder, release-<batch_id>.lua must not collide)."""
        self.world, self.log, self.contact_unit = world, log, contact_unit
        self.visible_unit = visible_unit or contact_unit
        self.experiment = {s: set(v) for s, v in (experiment or {}).items() if v}
        self.batch_prefix = batch_prefix
        self.in_view = in_view or (lambda side: [])
        self.sc = world.sc
        with open(_WHITELIST, encoding="utf-8") as f:
            wl = json.load(f)
        self.doctrine_fields = set(wl["fields"])
        self.doctrine_values = wl.get("values", {})
        self.doctrine_numbers = wl.get("numbers", {})
        self.cmds = {}
        self.streak = {0: 0, 1: 0}
        self.dropping = {0: False, 1: False}
        self.violations = {0: 0, 1: 0}
        self.batches = {}
        self.dropped = []          # cmd_ids refused when their batch was made; the referee tells the side (core)
        self._n = {0: 0, 1: 0}
        self._b = {0: 0, 1: 0}

    # --- validation ---
    def _unit(self, side, name, what="unit"):
        if not isinstance(name, str) or not self.world.present(side, name):
            u = self.world.own(side).get(name) if isinstance(name, str) else None
            status = u and u.get("s", {}).get("status")
            if status:              # own unit, not Active: a reinforcement not arrived yet, or one that is out
                raise Invalid(f"{what} {name!r} is not on the map now (game status {status}); it takes commands "
                              "only while Active (reinforcements: once they arrive)")
            raise Invalid(f"{what} is not one of your units on the map")
        return name

    def _hex(self, h, what):
        if not self.sc.valid_hex(h):
            raise Invalid(f"{what} is not a hex of this map")
        return h

    def _order(self, side, o):
        if not isinstance(o, dict) or set(o) - rules.ORDER_FIELDS:
            bad = sorted(map(str, set(o) - rules.ORDER_FIELDS)) if isinstance(o, dict) else []
            raise Invalid((f"unknown order fields {bad}; " if bad else "") + f"order fields must be among {sorted(rules.ORDER_FIELDS)}")
        if o.get("orders") not in rules.ORDER_TYPES:
            raise Invalid(f"order type must be one of {sorted(rules.ORDER_TYPES)}")
        c = dict(o)
        if "destination_location" in c:
            # the tested SetUnitOrders call (P0.3) took the hex as a string, GetUnitOrders returns it as one
            c["destination_location"] = str(self._hex(c["destination_location"], "destination_location"))
        if "target_location" in c:
            # Barrage aim point; the probed call (game machine 10-31) gave it as a number, GetUnitOrders returns a string
            c["target_location"] = self._hex(c["target_location"], "target_location")
        if "target_contact" in c:
            name = self.contact_unit(side, contact_no(c.pop("target_contact")))
            if name is None:
                raise Invalid("target_contact is not a contact you hold")
            c["target_unit"] = name
        elif "target_unit" in c:
            self._unit(side, c["target_unit"], "target_unit")
        for k in ("start_time", "end_time"):
            if k in c and not (isinstance(c[k], str) and TIME.match(c[k])):
                raise Invalid(f"{k} must look like 1989-09-25 07:30:00")
        for k, v in c.items():
            if k not in ("orders", "target_unit", "start_time", "end_time") and not (_is_int(v) or isinstance(v, (bool, str))):
                raise Invalid(f"{k}: bad value")
        if c["orders"] in rules.ORDERS_NEED_DESTINATION and "destination_location" not in c:
            raise Invalid(f"{c['orders']} needs destination_location")
        if c["orders"] == "Barrage":
            self._barrage(c)
        if isinstance(c.get("barrage_area_fire"), bool):     # the game reads only the string; a boolean true became "false"
            c["barrage_area_fire"] = "true" if c["barrage_area_fire"] else "false"
        return c

    @staticmethod
    def _barrage(c):
        """Barrage fields as the game takes them (rules.BARRAGE_*); refused here with the reason instead of by the game."""
        if "destination_location" in c or not ("target_location" in c or "target_unit" in c):
            raise Invalid("Barrage aims with target_location (hex number) or target_contact, not destination_location")
        missing = [k for k in rules.BARRAGE_NEEDS if k not in c]
        if missing:
            raise Invalid(f"Barrage needs {missing}")
        kind = c["barrage_type"].upper() if isinstance(c["barrage_type"], str) else None
        if kind not in rules.BARRAGE_TYPES:
            raise Invalid(f"barrage_type must be one of {list(rules.BARRAGE_TYPES)} (smoke: a fire_support obscuration request)")
        c["barrage_type"] = kind
        for k in ("barrage_rounds", "barrage_duration_mins"):
            if not (_is_int(c[k]) and c[k] >= 1):
                raise Invalid(f"{k} must be a whole number of at least 1")
        if "barrage_timing" in c and str(c["barrage_timing"]).lower() not in rules.BARRAGE_TIMINGS:
            raise Invalid("barrage_timing can only be asap: the barrage starts in the phase it is released; "
                          "to fire later, submit it later")

    def _request(self, side, rq):
        """IssueFireSupportRequest table as the agent may give it (rules.FIRE_SUPPORT_*); enemy_target names the
        target by `contact`, kept as a number until the batch is made."""
        if not isinstance(rq, dict) or set(rq) - rules.FIRE_SUPPORT_FIELDS:
            raise Invalid(f"request fields must be among {sorted(rules.FIRE_SUPPORT_FIELDS)}")
        rtype = rq.get("request_type")
        if rtype not in rules.FIRE_SUPPORT_TYPES:
            raise Invalid(f"request_type must be one of {sorted(rules.FIRE_SUPPORT_TYPES)}")
        out = {"request_type": rtype}
        if "location" in rq:
            out["location"] = self._hex(rq["location"], "location")
        elif rtype not in rules.FIRE_SUPPORT_NO_LOCATION:
            raise Invalid(f"{rtype} needs a location (hex number)")
        if rtype == "enemy_target":
            rq = {**rq, "contact": contact_no(rq.get("contact"))}
            if not _is_int(rq.get("contact")):
                raise Invalid("enemy_target needs `contact`: the number of a contact in view")
            if self.visible_unit(side, rq["contact"]) is None:
                raise Invalid("contact is not in view")
            out["contact"] = rq["contact"]
        elif "contact" in rq:
            raise Invalid("`contact` belongs to enemy_target only")
        for k in rules.FIRE_SUPPORT_UNITS:
            if k in rq:
                out[k] = self._unit(side, rq[k], k)
        for k in rules.FIRE_SUPPORT_STRINGS:
            if k in rq:
                if not isinstance(rq[k], str) or not 0 < len(rq[k]) <= 40:
                    raise Invalid(f"{k} must be a short string")
                out[k] = rq[k]
        if "target_type" in out and out["target_type"] not in rules.FIRE_SUPPORT_TARGET_TYPES:
            raise Invalid(f"target_type must be one of {list(rules.FIRE_SUPPORT_TARGET_TYPES)} (lower case)")
        if rtype == "static_target" and "target_type" not in out:
            raise Invalid(f"static_target needs target_type, one of {list(rules.FIRE_SUPPORT_TARGET_TYPES)}: "
                          "the game refuses it without one")
        for k in rules.FIRE_SUPPORT_INTS:
            if k in rq:
                if not _is_int(rq[k]) or not 0 <= rq[k] <= 100000:
                    raise Invalid(f"{k} must be a whole number")
                out[k] = rq[k]
        return out

    def validate(self, side, body, phase):
        kind = body.get("kind")
        if kind not in rules.COMMAND_KINDS:
            raise Invalid(f"kind must be one of {list(rules.COMMAND_KINDS)}")
        unit = self._unit(side, body.get("unit")) if kind != "fire_support" else None
        if kind == "orders":
            lst = body.get("orders")
            if not isinstance(lst, list) or not 1 <= len(lst) <= rules.MAX_ORDERS_PER_CMD:
                raise Invalid(f"orders must be a list of 1 to {rules.MAX_ORDERS_PER_CMD} orders")
            return unit, {"orders": [self._order(side, o) for o in lst]}
        if kind == "doctrine":
            sop = body.get("sop")
            if not isinstance(sop, dict) or not sop or set(sop) - self.doctrine_fields:
                bad = sorted(map(str, set(sop) - self.doctrine_fields)) if isinstance(sop, dict) else []
                raise Invalid((f"unknown sop fields {bad}; " if bad else "") + f"sop needs fields from {sorted(self.doctrine_fields)}")
            if not all(_simple(v) for v in sop.values()):
                raise Invalid("sop values must be numbers, strings or short lists of them")
            return unit, {"sop": {k: self._sop_value(k, v) for k, v in sop.items()}}
        if kind == "emission":
            em = body.get("emission")
            if not isinstance(em, dict) or not em or not _simple(em):
                raise Invalid("emission must be a small table of scalars")
            return unit, {"emission": dict(em)}
        if kind == "fire_support":
            return None, {"request": self._request(side, body.get("request"))}
        # deploy
        if phase not in rules.PREMATCH_PHASES:
            raise Invalid("deploy is only possible before the game starts (Setup / Pregame)")
        h = self._hex(body.get("hex"), "hex")
        if not self.sc.in_start_area(side, h):
            c0, c1, r0, r1 = self.sc.start_areas[side]
            raise Invalid(f"hex {h} is outside your starting area (columns {c0}-{c1}, rows {r0}-{r1}: "
                          f"{c0 * 100 + r0:04d} to {c1 * 100 + r1:04d})")
        return unit, {"location": h}

    def _sop_value(self, field, v):
        """An SOP value as the game writes it (doctrine_whitelist.json): numbers in their range; a closed field only takes
        a known option (any case, returned in the game's spelling); an open one takes any short string."""
        if field in self.doctrine_numbers:
            lo, hi = self.doctrine_numbers[field]
            if not (_is_int(v) and lo <= v <= hi):
                raise Invalid(f"sop {field} must be a whole number from {lo} to {hi}")
            return v
        spec = self.doctrine_values.get(field)
        if spec is None:
            return v
        known = spec.get("seen", []) + spec.get("manual", [])
        by_low = {k.lower(): k for k in known}
        if spec.get("list") and not isinstance(v, list):
            raise Invalid(f"sop {field} is a list of options, e.g. [{known[0]!r}]; options: {known}")
        out = []
        for x in v if spec.get("list") else [v]:
            if not isinstance(x, str):
                raise Invalid(f"sop {field} takes text options: {known}")
            if x.lower() in by_low:
                out.append(by_low[x.lower()])
            elif spec.get("closed"):
                raise Invalid(f"sop {field}: {x!r} is not an option; options: {known}")
            else:
                out.append(x)
        return out if spec.get("list") else out[0]

    # --- lint (check only) and warnings ---
    def lint(self, side, body, phase):
        """The checks of submit() without queueing and without counting towards the illegal streak.
        -> {"ok", "errors": [text], "warnings": [text]}"""
        if not isinstance(body, dict):
            body = {}
        try:
            unit, args = self.validate(side, body, phase)
        except Invalid as e:
            return {"ok": False, "errors": [str(e)], "warnings": []}
        outside = self.outside_experiment(side, unit, args)
        if outside:
            return {"ok": False, "errors": [f"unit not in this experiment: {outside}"], "warnings": []}
        return {"ok": True, "errors": [], "warnings": self.warnings(side, body, unit, args)}

    def _blind(self, side, aim, what):
        """Warning text when no contact of the side is in view within rules.FIRE_NEAR_HEXES of hex `aim`, else None."""
        if any(_is_int(h) and hex_dist(aim, h) <= rules.FIRE_NEAR_HEXES for _, h in self.in_view(side)):
            return None
        return (f"盲射：{what}瞄准 {aim:04d}，周围 {rules.FIRE_NEAR_HEXES} 格内没有本方当前在视的接触。"
                "游戏通常不执行看不到目标的炮击（第一届波军三个炮兵营盲轰 VP 格，一发没打出去）；"
                "先让侦察看到目标再打，或者瞄准在视的接触（target_contact）")

    def warnings(self, side, body, unit, args):
        """Things that will probably not work although the command is valid (texts for the side)."""
        out, kind = [], body.get("kind")
        icon = ((self.world.units.get(unit) or {}).get("s") or {}).get("icon_type") if unit else None
        if kind == "orders":
            raw = body.get("orders") or []
            for i, o in enumerate(args["orders"]):
                name = o.get("orders")
                ro = raw[i] if i < len(raw) and isinstance(raw[i], dict) else {}
                if name in rules.INDIRECT_ORDERS and icon and not is_artillery_icon(icon):
                    out.append(f"{name} 是炮兵命令，{unit} 的图标是 {icon}，不像炮兵；游戏多半会拒绝")
                if name in rules.HELO_ORDERS and icon and icon != "Helo":
                    out.append(f"{name} 是直升机命令，{unit} 的图标是 {icon}；游戏多半会拒绝")
                if name != "Barrage":
                    continue
                if "target_contact" in ro:
                    if self.visible_unit(side, contact_no(ro["target_contact"])) is None:
                        out.append(f"盲射：Barrage 的目标接触 {ro['target_contact']} 现在不在视野里。"
                                   "游戏通常不执行看不到目标的炮击；等它重新在视再打，或者换一个在视的接触")
                elif _is_int(o.get("target_location")):
                    w = self._blind(side, o["target_location"], "Barrage ")
                    if w:
                        out.append(w)
        elif kind == "fire_support":
            rq = args["request"]
            if rq["request_type"] == "static_target" and rq.get("target_type") not in ("obscuration", "illumination") \
                    and _is_int(rq.get("location")):
                w = self._blind(side, rq["location"], "火力请求 ")
                if w:
                    out.append(w)
        elif kind == "doctrine":
            for k, v in (body.get("sop") or {}).items():
                spec = self.doctrine_values.get(k)
                seen = {x.lower() for x in (spec or {}).get("seen", [])}
                for x in (v if isinstance(v, list) else [v]) if spec else []:
                    if isinstance(x, str) and x.lower() not in seen:
                        out.append(f"SOP {k} 的取值 {x!r} 还没在游戏里验证过（验证过的：{spec.get('seen')}），游戏可能不认")
        return out

    # --- submit / withdraw ---
    def submit(self, side, body, phase, sec, t, source="agent"):
        """-> (http status, answer). Illegal commands count towards the violation streak."""
        if not isinstance(body, dict):
            body = {}
        if sum(1 for c in self.cmds.values() if c["side"] == side and c["status"] == "queued") >= rules.MAX_QUEUE:
            return 429, {"ok": False, "error": f"queue full ({rules.MAX_QUEUE})"}
        try:
            unit, args = self.validate(side, body, phase)
        except Invalid as e:
            return self._illegal(side, str(e), body, sec, t, source)
        outside = self.outside_experiment(side, unit, args) if source != "referee" else None
        if outside:
            self.log.write("queue", {"t": t, "sec": sec, "event": "not_in_experiment", "side": side, "source": source,
                                     "unit": outside})
            return 400, {"ok": False, "error": "unit not in this experiment", "unit": outside}
        self.streak[side] = 0
        self._n[side] += 1
        cid = f"s{side}-c{self._n[side]:04d}"
        if unit is not None:
            for c in self.cmds.values():          # SetUnitOrders etc. replace; only the newest of a (unit, kind) is kept
                if c["side"] == side and c["status"] == "queued" and c["unit"] == unit and c["kind"] == body["kind"]:
                    c["status"] = "superseded"
                    self.log.write("queue", {"t": t, "sec": sec, "cmd_id": c["cmd_id"], "event": "superseded", "by": cid})
        cmd = {"cmd_id": cid, "side": side, "kind": body["kind"], "unit": unit, "args": args, "status": "queued",
               "source": source, "sec_submitted": sec}
        warn = self.warnings(side, body, unit, args)
        self.cmds[cid] = cmd
        self.log.write("queue", {"t": t, "sec": sec, "event": "submitted", **cmd, **({"warnings": warn} if warn else {})})
        # what the side sent, for its own detail view (args hold the true enemy name of a target contact)
        cmd["display"] = json.loads(json.dumps(body, default=str))
        return 200, {"ok": True, "cmd_id": cid, "status": "queued", **({"warnings": warn} if warn else {})}

    def outside_experiment(self, side, unit, args):
        """The first own unit a validated command would use that is not in the side's small experiment (or None).
        A fire mission in an experiment must name its firing unit, else the game would pick one from all."""
        allowed = self.experiment.get(side)
        if allowed is None:
            return None
        if unit is not None:
            return None if unit in allowed else unit
        rq = args["request"]
        if "requester_unit" not in rq:
            return "(no requester_unit)"
        return next((rq[k] for k in rules.FIRE_SUPPORT_UNITS if k in rq and rq[k] not in allowed), None)

    def _illegal(self, side, reason, body, sec, t, source):
        if source != "agent":
            self.log.write("queue", {"t": t, "sec": sec, "event": "script_command_rejected", "side": side,
                                     "source": source, "reason": reason})
            return 400, {"ok": False, "error": reason}
        if self.dropping[side]:
            # 2.11: no further violations, the command is dropped; the reason still goes back (P4 exam 10-31: Kimi got
            # 28 bare "dropped" answers, 20 of them for one misspelt doctrine field, and never learnt what was wrong)
            self.log.write("queue", {"t": t, "sec": sec, "event": "dropped", "side": side, "reason": reason})
            return 400, {"ok": False, "dropped": True, "error": reason}
        self.streak[side] += 1
        self.log.write("queue", {"t": t, "sec": sec, "event": "rejected", "side": side, "reason": reason,
                                 "streak": self.streak[side], "body": json.dumps(body, default=str)[:500]})
        if self.streak[side] >= rules.ILLEGAL_STREAK:
            self.dropping[side] = True
            self.violations[side] += 1
            self.log.write("violations", {"t": t, "sec": sec, "side": side, "kind": "illegal_streak",
                                          "streak": self.streak[side]})
        return 400, {"ok": False, "error": reason, "streak": self.streak[side]}

    def withdraw(self, side, cid):
        c = self.cmds.get(cid)
        if not c or c["side"] != side:
            return 404, {"ok": False, "error": "no such command"}
        if c["status"] != "queued":
            return 409, {"ok": False, "error": f"command is {c['status']}"}
        c["status"] = "withdrawn"
        self.log.write("queue", {"cmd_id": cid, "event": "withdrawn"})
        return 200, {"ok": True}

    # --- release ---
    @staticmethod
    def releasable(cmd, side, phase, orders_side):
        if phase == "Orders":
            return cmd["kind"] != "deploy" and orders_side == side
        if phase in rules.PREMATCH_PHASES:
            return orders_side in (None, side)
        return False

    def make_batch(self, side, phase, orders_side, sec, t):
        """All queued commands of `side` that may go out now as one batch (None when there are none)."""
        out = []
        for cid, c in sorted(self.cmds.items()):
            if c["side"] != side or c["status"] != "queued" or not self.releasable(c, side, phase, orders_side):
                continue
            why = None
            if c["unit"] is not None and not self.world.present(side, c["unit"]):
                why = "unit no longer on the map"
            elif c["kind"] == "fire_support" and "contact" in c["args"]["request"]:
                if self.visible_unit(side, c["args"]["request"]["contact"]) is None:
                    why = "contact is no longer in view"
            if why:
                c["status"], c["error"] = "rejected", why
                self.log.write("queue", {"t": t, "sec": sec, "cmd_id": cid, "event": "rejected_at_release", "why": why})
                self.dropped.append(cid)
                continue
            out.append(c)
        if not out:
            return None
        self._b[side] += 1
        bid = f"{self.batch_prefix}s{side}-{self._b[side]:04d}"
        table = {"batch_id": bid, "side": side, "cmds": []}
        for c in out:
            row = {"cmd_id": c["cmd_id"], "kind": c["kind"], **c["args"]}
            if c["kind"] == "fire_support" and "contact" in c["args"]["request"]:
                rq = dict(c["args"]["request"])               # the true name goes into the batch only
                rq["enemy_unit"] = self.visible_unit(side, rq.pop("contact"))
                row["request"] = rq
            if c["unit"] is not None:
                row["unit"] = c["unit"]
            table["cmds"].append(row)
            c["status"], c["batch"] = "released", bid
        b = {"batch_id": bid, "side": side, "sec": sec, "t": t, "phase": phase, "cmd_ids": [c["cmd_id"] for c in out],
             "lua": lua_literal.chunk(table), "sent": 0, "result": None}
        self.batches[bid] = b
        self.log.write("releases", {k: v for k, v in b.items() if k != "result"} | {"event": "issued"})
        return b

    def apply_result(self, res, t, sec):
        """Bridge answer {batch_id, results:[{cmd_id, ok, ret, readback}]} -> the batch, or None if unknown."""
        b = self.batches.get(res.get("batch_id"))
        if b is None:
            return None
        b["result"] = res
        for r in res.get("results") or []:
            c = self.cmds.get(r.get("cmd_id"))
            if c and c.get("batch") == b["batch_id"]:
                c["status"] = "done" if r.get("ok") else "failed"
                c["ret"], c["readback"] = r.get("ret"), r.get("readback")
                if not r.get("ok"):
                    c["error"] = r.get("error") or REFUSED.get(c["kind"], REFUSED[None])
        for cid in b["cmd_ids"]:
            if self.cmds[cid]["status"] == "released":
                self.cmds[cid]["status"] = "failed"
                self.cmds[cid]["error"] = "no result from the bridge"
        self.log.write("releases", {"event": "result", "t": t, "sec": sec, "batch_id": b["batch_id"],
                                    "results": res.get("results")})
        return b

    # --- views ---
    def listing(self, side, include_done=False, last=12):
        rows = [c for c in self.cmds.values() if c["side"] == side]
        live = [c for c in rows if c["status"] in ("queued", "released")]
        past = [c for c in rows if c["status"] not in ("queued", "released")][-last:] if include_done else []
        return [{"cmd_id": c["cmd_id"], "kind": c["kind"], "unit": c["unit"], "status": c["status"],
                 **({"orders": [o.get("orders") for o in (c.get("display") or {}).get("orders") or [] if isinstance(o, dict)]}
                    if c["kind"] == "orders" else {}),
                 **({"error": c["error"]} if c.get("error") else {})} for c in live + past]

    def detail(self, side, cid):
        """One command of the side as it sent it, with its status (None if there is no such command)."""
        c = self.cmds.get(cid)
        if not c or c["side"] != side:
            return None
        return {"cmd_id": cid, "kind": c["kind"], "unit": c["unit"], "status": c["status"], "source": c.get("source"),
                "sec_submitted": c.get("sec_submitted"), "batch": c.get("batch"), "body": c.get("display"),
                **({"error": c["error"]} if c.get("error") else {})}

    def counts(self, side):
        n = {}
        for c in self.cmds.values():
            if c["side"] == side:
                n[c["status"]] = n.get(c["status"], 0) + 1
        return n
