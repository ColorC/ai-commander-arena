"""The referee proper: game picture, agent turns, thinking time, wakes, queue and audit behind one lock.

server.py only translates HTTP into calls on Referee. All time that matters for fairness (turn limits, thinking
time per real second) comes from the injected `clock()` (seconds, monotonic), all game time from the relay's `game.sec`.
Real-time rules: a side is active while any of its agents has an open turn; gate = "sleep" then (game paced 1:1),
else "off" (as fast as the machine goes). Matching events that arrive while a side is active are only fed to its
event list; the agent reads them with the events view or in the next summary.
Exercise options (materials/10 2.10, arena/DESIGN.md 9): pacing "turn" (gate always off, a side is woken
only by its own Orders phase, which then waits for its commander up to TURN_ORDERS_SEC), `players` (the sides with an
agent; the other one is the game's AI), `prematch_sec`, `experiment` ({side: unit names} of a small experiment) and
`workspaces` ({side: folder}). At the end exercise-report.json is written next to final.json.
Season 2 (materials/18 2.1): lint() checks a command without queueing it (orders.Queue.lint); fires.FireWatch turns the
side's own artillery ammunition into fire_report / fire_pending events (not subscribable, they wake no one);
script_test() runs one script once on the current picture with no effect (its commands are only linted).
Wake text (r5-minden 11-04: the fixed summary read as the whole picture): the side's wake scripts ({on: wake}) run
right before an in-game wake and their report() text, in name order, is the wake text (wake_text in the wait answer);
without one, or when all fail, the default summary as before, a failed one adding a line (wake_errors). The driver
hands the wake back as the result of the blocking `turn end` (arena/harness/driver.py); the referee only sees the turn
end and the next wait, so the time in between is no thinking time.
"""
import json
import os
import re
import threading
import time

from . import audit as audit_mod
from . import contacts as ct
from . import events as ev_mod
from . import fires as fires_mod
from . import orders, rules, scoring, thinking, wake as wake_mod
from .errors import ApiError  # noqa: F401  (re-exported: server and tests import it from here)
from .log import Log
from .scripts import Scripts, ScriptError
from .views import VP_SOURCE, FogViolation, Views, clock_str
from .workspace import Workspace
from .world import World

SIDES = (0, 1)
AGENT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PACINGS = ("formal", "turn")
HOLD = [{"orders": "Hold"}]


def check_experiment(scenario, experiment):
    """{side: [unit names]} of a small experiment -> the same with clean lists; ValueError names what is wrong."""
    out = {}
    for side, names in (experiment or {}).items():
        if not names:
            continue
        if side not in SIDES:
            raise ValueError(f"experiment side must be 0 or 1, not {side!r}")
        if scenario.roster is None:
            raise ValueError(f"{scenario.slug} has no unit roster (units.json): no small experiments on it")
        names = list(dict.fromkeys(names))
        own = {r["name"] for r in scenario.roster[side]}
        bad = [n for n in names if n not in own]
        if bad:
            raise ValueError(f"not units of side {side} in {scenario.name}: {', '.join(map(repr, bad))}")
        if len(names) > rules.EXERCISE_SMALL_UNITS:
            raise ValueError(f"a small experiment uses at most {rules.EXERCISE_SMALL_UNITS} units, side {side} lists "
                             f"{len(names)}")
        out[side] = names
    return out


class Referee:
    def __init__(self, run_dir, scenario, clock=time.monotonic, script_runner=None, compute_runner=None,
                 pacing="formal", players=SIDES, prematch_sec=None, experiment=None, workspaces=None, batch_prefix="",
                 exercise=False):
        if pacing not in PACINGS:
            raise ValueError(f"pacing must be one of {PACINGS}")
        players = tuple(sorted(set(players)))
        if not players or any(s not in SIDES for s in players):
            raise ValueError("players: one or both of the sides 0 and 1")
        self.run, self.clock, self.sc = run_dir, clock, scenario
        self.pacing, self.players = pacing, players
        self.exercise = exercise                      # a training-period exercise (the briefing says so)
        self.prematch_sec = rules.PREMATCH_REAL_SEC if prematch_sec is None else float(prematch_sec)
        self.experiment = check_experiment(scenario, experiment)
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.log = Log(os.path.join(run_dir, "logs"))
        self.world = World(scenario)
        self.tables = {s: ct.ContactTable(s) for s in SIDES}
        self.events = ev_mod.EventLog(self.log)
        self.thinking = thinking.ThinkTime()
        self.wake = wake_mod.WakeManager(scenario, self.log)
        self.queue = orders.Queue(self.world, self.log, self._contact_unit, self._visible_unit, self.experiment,
                                  batch_prefix, in_view=self._in_view)
        self.fires = fires_mod.FireWatch(self.world.side_of, lambda sec: clock_str(scenario, sec))
        self.audit = audit_mod.Audit(self.log)
        ws = workspaces or {}
        self.ws = {s: Workspace(ws.get(s) or os.path.join(run_dir, "workspace", f"side{s}")) for s in SIDES}
        self.carried = {s: bool(ws.get(s)) for s in SIDES}    # a player's own folder (training, exercises, P5)
        self.scripts = Scripts(self.log, script_runner, self.ws)
        for s in players:
            self.scripts.load(s)
        self.compute_runner = compute_runner
        self.views = Views(self)
        self.sec, self.phase, self.orders_side = 0, "Unknown", None
        self.ended = self.paused = False
        self.agents = {s: {} for s in SIDES}          # side -> {name: {t0, deadline}}
        self.turn_n = {s: 0 for s in SIDES}
        self.turn_id = {s: None for s in SIDES}
        self.observe_on = {s: False for s in SIDES}
        self.observe_seen = {s: 0 for s in SIDES}
        self.wake_payload = {s: None for s in SIDES}
        self.summary_from = {s: 0 for s in SIDES}
        self.own_snapshot = {s: {} for s in SIDES}
        self.baseline = {s: 100.0 for s in SIDES}
        self.crossed = {s: set() for s in SIDES}
        self.orders_seen = {s: [] for s in SIDES}
        self.prematch_ended = {s: s not in players for s in SIDES}   # the game's AI side has nothing to prepare
        self.prematch_done = False
        self.held = {s: set() for s in SIDES}           # units outside a small experiment already told to hold
        self.end_shots, self.ended_by, self.end_detail = [], None, None
        self.t_start = clock()
        self.prematch_t0 = None
        self.last_end = {s: None for s in SIDES}
        self.ep_key, self.ep, self.ep_last = None, None, None
        self.relay_hold = False
        self.relay_at = None                            # referee clock of the relay's last sync
        self.minute_seq = 0
        self.fog_violations = 0
        self.compute_count = {s: 0 for s in SIDES}        # tasks per side in the real game
        self.compute_cpu = {s: 0.0 for s in SIDES}        # CPU seconds per side before the game
        self.held_lines = []
        self.last_tick = clock()
        self._pause_at = None
        self._clock_logged = (-1e9, None)
        self._fresh = {s: [] for s in SIDES}
        self.final = None
        for s in SIDES:
            self.wake.set_subs(s, rules.DEFAULT_SUBSCRIPTIONS, 0)

    # ---------------- helpers ----------------
    def _contact_unit(self, side, no):
        c = self.tables[side].by_no.get(no)
        return c.unit if c is not None and c.first_seen is not None else None

    def _visible_unit(self, side, no):
        c = self.tables[side].by_no.get(no)
        return c.unit if c is not None and c.visible else None

    def _in_view(self, side):
        return [(c.no, c.last_pos) for c in self.tables[side].visible()]

    def _last_event_id(self, side):
        return self.events.items[side][-1]["id"] if self.events.items[side] else 0

    def _snapshot(self, side):
        rows = (self.views._unit_row(side, n, u) for n, u in self.world.own(side).items())
        self.own_snapshot[side] = {r["name"]: (r["hex"], r["status"], r["subs_ok"]) for r in rows}
        self.summary_from[side] = self._last_event_id(side)

    def emit(self, side, etype, t, **fields):
        ev = self.events.emit(side, etype, t, **fields)
        if self.pacing == "formal":                   # turn pacing: only the side's Orders phase wakes (_phase_step)
            self.wake.offer(side, ev, active=bool(self.agents[side]))
        if etype != "script_alarm":
            self._fresh[side].append(ev)
        return ev

    def _tell_failed(self, cmd_ids):
        """A command that will not take effect (refused by the referee at release, or by the game) goes into its side's
        feed and next summary as command_failed (P3 rehearsal 6: the Polish side sent 38 fire requests the game
        refused and never learnt it). Not subscribable: it wakes no one."""
        for cid in cmd_ids:
            c = self.queue.cmds[cid]
            self.emit(c["side"], "command_failed", self.sec, cmd_id=cid, kind=c["kind"], unit=c.get("unit"),
                      error=c.get("error"))

    def _tell_changed(self, c):
        """An order the game took (ok) but left out of what it read back goes into the side's feed as command_changed
        (start task 11-01: Kimi's Resupply → Hold was read back as Hold → Hold four times and it never learnt why; 50
        of 3,054 accepted orders in all runs so far lost an order this way, FM01 21.7 and 21.8). Not subscribable."""
        sent = [o.get("orders") for o in c["args"].get("orders") or [] if isinstance(o, dict)]
        got = [o.get("orders") for o in c.get("readback") or [] if isinstance(o, dict)]
        if not got:
            return
        gone = [o for o in sent if o and o not in got]
        if gone:
            self.emit(c["side"], "command_changed", self.sec, cmd_id=c["cmd_id"], unit=c.get("unit"), sent=sent, got=got,
                      dropped=gone, why=rules.ORDERS_CHANGED_WHY)

    def turn_info(self, side):
        a = self.agents[side].get("commander")
        # no deadline_in here: the summary is what the player reads, and the backstop is not shown (r5-minden 11-04:
        # DeepSeek read "deadline_in 480" and wrote "used ~290s. Let me hurry", then ordered without checking)
        return {"turn_id": self.turn_id[side] if a else None, "observing": self.observe_on[side],
                "subagents": sorted(n for n in self.agents[side] if n != "commander")}

    def next_orders(self, side):
        seen = self.orders_seen[side]
        if not seen:
            return None
        step = seen[-1] - seen[-2] if len(seen) > 1 else 900
        nxt = seen[-1] + max(step, 60)
        while nxt <= self.sec:
            nxt += max(step, 60)
        return {"sec": nxt, "clock": clock_str(self.sc, nxt), "estimated": True}

    def gate(self):
        if self.pacing == "turn":                     # the game waits in the Orders phase instead (2.10)
            return "off"
        return "sleep" if any(self.agents[s] for s in SIDES) and self.phase not in rules.PREMATCH_PHASES else "off"

    # ---------------- time: thinking time and turn limits ----------------
    def tick(self, now):
        with self.lock:
            t_prev, self.last_tick = self.last_tick, now
            if self.paused or now <= t_prev:
                return
            prematch = self.phase in rules.PREMATCH_PHASES
            for side in SIDES:
                for name, a in list(self.agents[side].items()):
                    secs = max(0.0, min(now, a["deadline"]) - max(t_prev, a["t0"]))
                    kind = "subagents" if name != "commander" else "observe" if self.observe_on[side] else "commander"
                    self.thinking.add(side, kind, secs, prematch)
                    if now >= a["deadline"]:
                        self.end_agent(side, name, now, a.get("limit_reason") or "timeout")

    def end_agent(self, side, name, now, reason):
        a = self.agents[side].pop(name, None)
        if a is None:
            return
        self.log.write("wakes", {"t": now, "sec": self.sec, "side": side, "kind": "agent_end", "agent": name,
                                 "reason": reason, "turn_id": a.get("turn_id"), "seconds": round(now - a["t0"], 2)})
        self.last_end[side] = f"{name} turn ended ({reason})"
        if name == "commander":
            self.observe_on[side] = False
            self._snapshot(side)
            if self.phase in rules.PREMATCH_PHASES:
                self.prematch_ended[side] = True
        self.cond.notify_all()

    def _start_turn(self, side, now, reasons, prematch=False, limit=None, limit_reason=None):
        self.turn_n[side] += 1
        tid = self.turn_id[side] = f"s{side}-t{self.turn_n[side]:04d}"
        limit = limit or (self.prematch_sec if prematch else rules.COMMANDER_TURN_SEC)
        self.agents[side]["commander"] = {"t0": now, "deadline": now + limit, "turn_id": tid,
                                          "limit_reason": limit_reason}
        self.observe_on[side] = False
        if not prematch:
            self.thinking.wake(side)
            self.baseline[side] = self.world.strength_pct(side)
            self.crossed[side].clear()
        self.wake_payload[side] = {"turn_id": tid, "reasons": reasons, "prematch": prematch}
        if not prematch:
            self.wake_payload[side].update(self._wake_text(side, now))
        self.log.write("wakes", {"t": now, "sec": self.sec, "side": side, "kind": "wake", "turn_id": tid,
                                 "prematch": prematch, "reasons": reasons, "thinking_sec": round(self.thinking.total(side), 1),
                                 **({"wake_scripts": True} if self.wake_payload[side].get("wake_text") is not None else {})})
        self.cond.notify_all()

    def _wake_text(self, side, now):
        """The side's wake scripts on the picture of this wake (summary + units + contacts, the events since the last
        wake) -> {"wake_text": their reports in name order, or None when none ran ok (the default summary then),
        "wake_errors": a line per failed one}. {} when the side has no wake script."""
        if self.scripts.runner is None or not any(d["trigger"]["on"] == "wake" for d in self.scripts.defs[side].values()):
            return {}
        events = self.events.since(side, self.summary_from[side])
        res = self._run_scripts(side, "wake", events, now) or []
        good = [r for r in res if r["ok"]]
        out = {"wake_text": "\n\n".join(r["report"] for r in good if r["report"]) if good else None}
        bad = [f"[唤醒脚本 {r['name']} 出错：{r['error']}]" for r in res if not r["ok"]]
        if bad:
            out["wake_errors"] = bad
        return out

    # ---------------- relay ----------------
    def sync(self, body):
        with self.lock:
            now = self.clock()
            self.relay_at = now
            self.tick(now)
            g = body.get("game") or {}
            sec = self.sec if g.get("sec") is None else int(g["sec"])   # null until the recorder has a game clock
            phase = g.get("phase") or "Unknown"
            self.sec, self.phase = sec, phase
            self.orders_side = g.get("orders_side") if g.get("orders_side") in SIDES else None
            self.relay_hold = bool(g.get("hold"))     # a replay is on: Lua input would not reach the game's log
            if isinstance(body.get("end_shots"), list) and body["end_shots"]:
                self.end_shots = [str(x)[:64] for x in body["end_shots"][:10]]   # the relay shot the end screens
            for d in body.get("dialogs") or []:
                self.log.write("sync", {"t": now, "sec": sec, "dialog": d})
            state, first = body.get("state"), self.world.state is None
            if not (isinstance(state, dict) and isinstance(state.get("units"), dict) and state["units"]):
                state = None                      # the bridge's load-time state can come before the units exist
            if state and first:
                self._state(state, now)
            self._lines(body.get("lines") or [], now)
            if state and not first:
                self._state(state, now)
            if body.get("release_result"):
                res = body["release_result"]
                b = self.queue.apply_result(res, now, sec)
                self.audit.on_result(res, now)
                for cid in (b or {}).get("cmd_ids", []):
                    c = self.queue.cmds[cid]
                    if c["status"] == "done" and c["kind"] == "orders":
                        self.fires.on_release(c["side"], c["unit"], c["args"]["orders"], cid, sec)
                        self._tell_changed(c)
                self._tell_failed([c for c in (b or {}).get("cmd_ids", []) if self.queue.cmds[c]["status"] == "failed"])
            resp = {"gate": "off", "release": None, "f9": False, "orders_done": False, "click": [], "stop": False,
                    "seq": body.get("seq")}
            if not self.ended:
                resp.update(self._phase_step(now))
                # the relay keeps the hand-over dialog of an Orders phase open until this is true: in the two-player
                # game some Orders phases go on by themselves right after it (the replay follows), P2 smoke 10-30
                resp["orders_done"] = bool(self.phase == "Orders" and self.ep and self.ep.get("done"))
                self._timers()
                self._flush_event_scripts(now)
                self._process_wakes(now)
            # the game's own Ended phase is its end even before the relay has shot the end screens (baseline-B 11-01:
            # after the enemy's sudden death Ended came one sync before Battle Over)
            if (g.get("alive") is False or phase == "Ended") and not self.ended:
                self._finish(now, "game_end" if self.end_shots or phase == "Ended" else "game_gone",
                             {"phase": phase, "alive": g.get("alive"), "end_shots": self.end_shots})
            resp["gate"] = self.gate()
            resp["stop"] = self.ended
            self._clock_row(now, body.get("wall_ms"), resp["gate"])
            self.cond.notify_all()
            return resp

    def _lines(self, lines, now):
        """Debug-log lines. Contact lines of units not yet known (no state yet) wait for the next call."""
        parsed = self.held_lines + [p for p in map(ct.parse_line, lines) if p]
        self.held_lines = []
        for p in parsed:
            if p[0] == "lua":
                self.audit.on_lua_line((p[1] - self.sc.start_clock) % 86400, p[2])
            elif p[0] == "contact":
                _, clock, unit, no, state, report = p
                side = self.world.side_of.get(unit)
                if side is None:
                    if len(self.held_lines) < 2000:
                        self.held_lines.append(p)
                    continue
                if state < 0:
                    continue
                t = (clock - self.sc.start_clock) % 86400
                fields = self.tables[1 - side].update(t, unit, no, state, report, self.world.location(unit))
                if fields:
                    self.emit(1 - side, "new_contact", t, **fields)

    def _state(self, state, now):
        first = self.world.state is None
        before = dict(self.world.owner)
        prev = self.world.state
        self.world.update(state)
        if first:
            before = dict(self.world.owner)
        sec = self.world.sec
        vp = {h: c["vp"] for h, c in self.sc.vp_cells.items()}
        rel = lambda side, o: "none" if o is None else ("you" if o == side else "enemy")  # noqa: E731
        for side, etype, f in ev_mod.diff(prev, state, self.world.side_of, self.sc.commanders, before,
                                          self.world.owner, vp):
            if side is None:
                for s in SIDES:   # a vp_change: the cell's value to each side
                    vpf = self.sc.vp_fields(f["hex"], s) if etype == "vp_change" else {}
                    self.emit(s, etype, sec, **{**f, **vpf, "from": rel(s, f["from"]), "to": rel(s, f["to"])})
            else:
                self.emit(side, etype, sec, **f)
        for side, etype, f in self.fires.update(prev, state, sec, self.views._clean_orders):
            self.fires.note(side, self.emit(side, etype, sec, **f))
        for s in SIDES:
            self.tables[s].refresh(sec, self.world.location)
            self._strength_events(s, sec)
            self._near_events(s, sec)
        if first:
            for s in SIDES:
                self._snapshot(s)
                self.baseline[s] = self.world.strength_pct(s)
        row = scoring.timeline_row(self.world)
        if row:
            self.log.write("score", {"t": now, "sec": sec, **row})
        self.minute_seq += 1
        self._run_scripts_all("minute", now)

    def _timers(self):
        for s in SIDES:
            for sub_id, at in self.wake.timers_due(s, self.sec):
                self.emit(s, "timer", at, sub=sub_id, at=at)

    def _strength_events(self, side, sec):
        drop = int(self.baseline[side] - self.world.strength_pct(side))
        marks = {s["points"] for s in self.wake.subs[side] if s["type"] == "strength_drop"}
        new = [m for m in sorted(marks) if m <= drop and m not in self.crossed[side]]
        if new:
            self.crossed[side].update(new)
            self.emit(side, "strength_drop", sec, drop=drop, threshold=new[-1],
                      pct=self.world.strength_pct(side))

    def _near_events(self, side, sec):
        for sub in self.wake.subs[side]:
            if sub["type"] != "contact_near":
                continue
            for c in self.tables[side].visible():
                d = self.sc.dist_km(sub["center"], c.last_pos) if c.last_pos else None
                if d is not None and d <= sub["radius_km"]:
                    if c.no not in sub["inside"]:
                        sub["inside"].add(c.no)
                        self.emit(side, "contact_near", sec, sub=sub["id"], contact=c.no, pos=c.last_pos,
                                  dist_km=round(d, 2), report=c.report, state=c.state)
                else:
                    sub["inside"].discard(c.no)

    # ---------------- scripts ----------------
    def _run_scripts(self, side, trigger, events, now):
        if self.scripts.runner is None or not self.scripts.defs[side]:
            return []
        try:
            # the wake summary plus the full own-unit and contact lists (list_units / get_contacts rows): the summary
            # alone has only the changes since the last wake (P4 exam 10-31: Kimi's script looked for view.units to
            # catch arriving reinforcements, found nothing in 107 runs and never fired)
            view = self.views.render(side, "summary")
            view["units"] = self.views.render(side, "units")["units"]
            view["contacts"] = self.views.render(side, "contacts")["contacts"]
        except FogViolation:
            return []
        return self.scripts.run(
            side, trigger, view, events, self.sec, now,
            submit=lambda body, src: self._script_submit(side, body, src, now),
            alarm=lambda name, text, forced: self.emit(side, "script_alarm", self.sec, script=name, text=text,
                                                       **({"forced": True} if forced else {})))

    def _script_submit(self, side, body, src, now):
        """A script's command; one the referee refuses goes into the side's feed as script_command_failed (start task
        11-01: DeepSeek's artillery script aimed at "c<row index>", all 5 Barrages were refused and it only saw the
        script never fire). Not subscribable."""
        status, ans = self.queue.submit(side, body, self.phase, self.sec, now, src)
        if status != 200:
            self.emit(side, "script_command_failed", self.sec, script=src.partition(":")[2], error=ans.get("error"),
                      unit=body.get("unit") if isinstance(body, dict) else None)
        return status, ans

    def _run_scripts_all(self, trigger, now):
        for s in SIDES:
            self._run_scripts(s, trigger, [], now)

    def _flush_event_scripts(self, now):
        for s in SIDES:
            fresh, self._fresh[s] = self._fresh[s], []
            if fresh:
                self._run_scripts(s, "event", fresh, now)

    # ---------------- phases and release ----------------
    def _phase_step(self, now):
        out = {"release": None, "f9": False}
        phase, os_ = self.phase, self.orders_side
        # the prematch is one step for both sides: whose setup screen the game shows does not matter
        key = ("Orders", os_) if phase == "Orders" else ((phase,) if phase in rules.PREMATCH_PHASES else None)
        if key != self.ep_key:
            old, self.ep_key = self.ep, key
            if old is not None and old["key"][0] == "Orders":
                self.ep_last = old
            last = self.ep_last
            if (phase == "Orders" and os_ is not None and last is not None and last["key"] == key
                    and last["sec"] == self.sec):
                # after the replay the two-player game can come back to the Orders phase it showed before (same side,
                # same game second) and wait for F9 (P3 rehearsal 10-30): the same phase going on, not a new one
                self.ep = {**last, "f9_n": 0, "f9_at": 0.0}
            else:
                self.ep = None if key is None else {"t0": now, "batch": None, "done": False, "f9_n": 0, "f9_at": 0.0,
                                                    "key": key, "sec": self.sec}
                if phase == "Orders" and os_ is not None:
                    self.orders_seen[os_].append(self.sec)
                    ev = self.emit(os_, "orders_phase", self.sec)
                    self._run_scripts(os_, "orders_phase", [ev], now)
                    if os_ in self.experiment:
                        self._hold_outsiders(os_, now)          # units that came onto the map since
                    if self.pacing == "turn" and os_ in self.players:
                        self._turn_for_phase(os_, now, ev)
        ep = self.ep
        if ep is None or self.relay_hold:
            return out
        if os_ is None and phase == "Orders":
            if (now - ep["t0"] >= rules.ORDERS_SIDE_GRACE_SEC and ep["f9_n"] < rules.F9_MAX
                    and (not ep["f9_n"] or now - ep["f9_at"] >= rules.F9_RETRY_SEC)):
                if not ep["f9_n"]:
                    self.log.write("violations", {"t": now, "sec": self.sec, "kind": "orders_side_unknown_f9"})
                ep["f9_n"], ep["f9_at"], out["f9"] = ep["f9_n"] + 1, now, True
            return out
        if phase == "Orders" and self._turn_waits(ep, os_, now):
            return out
        if phase in rules.PREMATCH_PHASES:
            self.prematch_t0 = self.prematch_t0 if self.prematch_t0 is not None else now
            if not self.prematch_done and (all(self.prematch_ended.values())
                                           or now - self.prematch_t0 >= self.prematch_sec):
                self.prematch_done = True
            if not self.prematch_done:
                return out
            if not ep.get("held"):                        # the opening batch tells experiment outsiders to hold
                ep["held"] = True
                for s in self.experiment:
                    self._hold_outsiders(s, now)
        # an Orders phase releases its own side's batch; the prematch releases both sides' batches one after the other
        # (in a two-player game the pregame goes straight from Player 1's setup into play, P2 smoke 10-30)
        todo = ep.setdefault("todo", [os_] if phase == "Orders" else [0, 1])
        if ep["batch"] is not None and ep["batch"]["result"] is not None:
            ep["batch"] = None
        while ep["batch"] is None and todo and not ep["done"]:
            s = todo.pop(0)
            b = self.queue.make_batch(s, phase, os_ if phase == "Orders" else s, self.sec, now)
            dropped, self.queue.dropped = self.queue.dropped, []
            self._tell_failed(dropped)
            if b:
                ep["batch"] = b
                b["last_sent"] = -1e9
                self.audit.on_issue(b)
        if ep["batch"] is None and not todo:
            ep["done"] = True
        b = ep["batch"]
        if b and not ep["done"]:
            if b["sent"] == 0 or now - b["last_sent"] >= rules.RELEASE_RETRY_SEC:
                b["sent"] += 1
                b["last_sent"] = now
                out["release"] = {"batch_id": b["batch_id"], "side": b["side"], "lua": b["lua"]}
        if ep["done"] and not self.paused and (not ep["f9_n"] or now - ep["f9_at"] >= rules.F9_RETRY_SEC):
            if ep["f9_n"] < rules.F9_MAX:
                ep["f9_n"] += 1
                ep["f9_at"] = now
                out["f9"] = True
            elif ep["f9_n"] == rules.F9_MAX:
                ep["f9_n"] += 1
                self.log.write("violations", {"t": now, "sec": self.sec, "kind": "f9_no_effect", "phase": phase,
                                              "orders_side": os_})
        return out

    def _hold_outsiders(self, side, now):
        """Small experiment: queue a referee Hold for each own unit on the map outside the experiment, once per unit
        (it goes out with the side's next batch and through the audit like any other command)."""
        allowed = set(self.experiment[side])
        for name in sorted(self.world.own(side)):
            if name in allowed or name in self.held[side] or not self.world.present(side, name):
                continue
            code, ans = self.queue.submit(side, {"kind": "orders", "unit": name, "orders": HOLD}, self.phase, self.sec,
                                          now, source="referee")
            if code == 200:
                self.held[side].add(name)

    def _turn_for_phase(self, side, now, ev):
        """Turn pacing: the side's own Orders phase is its wake, and the phase waits for that turn (_turn_waits)."""
        if "commander" not in self.agents[side]:
            self._start_turn(side, now, [{"id": ev["id"], "type": "orders_phase", "sub": "turn"}],
                             limit=rules.TURN_ORDERS_SEC, limit_reason="turn_time")
        self.ep["turn_wait"] = self.turn_id[side] if "commander" in self.agents[side] else None

    def _turn_waits(self, ep, side, now):
        """True while the Orders phase still waits for the commander's turn it woke; at TURN_ORDERS_SEC the turn is
        ended (turn_time), sub-agents with it, so no agent of the side runs on into the next phase."""
        tid = ep.get("turn_wait")
        if tid is None:
            return False
        a = self.agents[side].get("commander")
        if now - ep["t0"] >= rules.TURN_ORDERS_SEC:
            for name in list(self.agents[side]):
                self.end_agent(side, name, now, "turn_time")
        elif a is not None and a.get("turn_id") == tid:
            return True
        ep["turn_wait"] = None
        self.log.write("wakes", {"t": now, "sec": self.sec, "side": side, "kind": "orders_released", "turn_id": tid,
                                 "waited": round(now - ep["t0"], 2)})
        return False

    # ---------------- wakes ----------------
    def _process_wakes(self, now):
        for side in SIDES:
            p = self.wake.due(side, self.sec)
            if p is None:
                continue
            self.wake.clear(side)
            if self.agents[side] or side not in self.players:
                continue
            self._start_turn(side, now, p["events"])

    def next_due(self):
        """(next game second something is due, next real second an agent deadline hits)"""
        with self.lock:
            d = [a["deadline"] for s in SIDES for a in self.agents[s].values()]
            return self.wake.next_due(), (min(d) if d else None)

    def _clock_row(self, now, wall_ms, gate):
        last_t, last_gate = self._clock_logged
        if now - last_t >= 10 or gate != last_gate:
            self._clock_logged = (now, gate)
            self.log.write("clock", {"t": now, "relay_wall_ms": wall_ms, "sec": self.sec, "phase": self.phase, "gate": gate})

    # ---------------- end of game ----------------
    def _finish(self, now, ended_by="stopped", detail=None):
        for s in SIDES:
            for name in list(self.agents[s]):
                self.end_agent(s, name, now, "game_over")
        self.ended, self.ended_by, self.end_detail = True, ended_by, detail
        self.final = {"sec": self.sec, "audit": self.audit.finalize(self.sec),
                      "fog_violations": self.fog_violations, "violations": self.queue.violations,
                      "thinking": {s: self.thinking.view(s) for s in SIDES},
                      "queue": {s: self.queue.counts(s) for s in SIDES},
                      "strength": {s: self.world.strength_pct(s) for s in SIDES}}
        with open(os.path.join(self.run, "final.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.final, f, ensure_ascii=False, indent=1, default=str)
        # a lost relay is a facility failure, not a played exercise: no report, so the director records the exercise
        # as failed and it uses none of the player's quota (2.10)
        if ended_by != "relay_lost":
            with open(os.path.join(self.run, "exercise-report.json"), "w", encoding="utf-8", newline="\n") as f:
                json.dump(self.exercise_report(now), f, ensure_ascii=False, indent=1, default=str)
        self.log.write("clock", {"t": now, "sec": self.sec, "phase": "Ended"})

    def exercise_report(self, now):
        """What the side that asked for an exercise gets back (materials/10 2.10): how it ended, both sides' loss lists
        and the VP cells, as the game's end report would show them. Never anything from a workspace, a conversation
        or a script. `official` (the game's own end screen) is filled in by the director after OCR."""
        w = self.world
        losses = {}
        for s in SIDES:
            rows = w.losses(s)
            ok, tot = w.strength(s)
            losses[str(s)] = {"strength_pct": w.strength_pct(s) if w.state else None, "subunits_start": tot if w.state else 0,
                              "subunits_now": ok, "units_out": sum(1 for r in rows if r["status"] == rules.DEAD_STATUS),
                              "units": rows}
        cells = [{"hex": h, "vp": c["vp"], **({"side_vp": c["side_vp"]} if len(set(c["side_vp"])) > 1 else {}),
                  "owner": w.owner.get(h)} for h, c in sorted(self.sc.vp_cells.items())]
        out = {"scenario": self.sc.name, "pacing": self.pacing, "players": list(self.players),
               "experiment_units": {str(s): v for s, v in self.experiment.items()},
               "ended_by": self.ended_by, "end_detail": self.end_detail, "sec": self.sec,
               "game_clock": clock_str(self.sc, self.sec), "wall_sec": round(now - self.t_start, 1),
               "losses": losses, "vp_cells": cells,
               "vp_held": {str(s): sum(self.sc.cell_vp(c["hex"], s) for c in cells if c["owner"] == s) for s in SIDES},
               "vp_source": VP_SOURCE, "official": None}
        if self.ended_by != "game_end":
            out["note"] = ("not the game's own end: the game was stopped or lost before its end screen; losses and VP "
                           "cells are the referee's picture at that moment")
        return out

    def close(self):
        with self.lock:
            self.log.close()
        runner = self.scripts.runner
        if hasattr(runner, "close"):                      # sandbox hosts (arena/sandbox/runner.py ScriptHost)
            runner.close()

    # ---------------- agent API ----------------
    def _need_active(self, side):
        if not self.agents[side]:
            raise ApiError(409, "no open turn" + (f", last: {self.last_end[side]}" if self.last_end[side] else "")
                           + ": wait for a wake with /agent/wait")

    def _api(self, side, fn):
        with self.lock:
            self.tick(self.clock())
            self._need_active(side)
            return fn()

    def wait(self, side, timeout):
        stop_at = time.monotonic() + max(0.0, float(timeout))
        with self.cond:
            while True:
                now = self.clock()
                self.tick(now)
                if self.ended:
                    return {"woke": False, "game_over": True}
                if self.phase in rules.PREMATCH_PHASES and not self.agents[side] and not self.prematch_ended[side] \
                        and self.turn_n[side] == 0:
                    self._start_turn(side, now, [{"type": "prematch"}], prematch=True)
                if self.wake_payload[side] is not None:
                    p, self.wake_payload[side] = self.wake_payload[side], None
                    return self._wake_response(side, p)
                if "commander" in self.agents[side]:
                    if not self.observe_on[side]:
                        raise ApiError(409, "your turn is open: call end_turn before waiting again")
                    if self.minute_seq > self.observe_seen[side]:
                        self.observe_seen[side] = self.minute_seq
                        return self._wake_response(side, {"turn_id": self.turn_id[side], "reasons": [{"type": "observe"}],
                                                          "observe": True})
                if time.monotonic() >= stop_at:
                    return {"woke": False, "sec": self.sec, "thinking_sec": round(self.thinking.total(side), 1)}
                self.cond.wait(min(0.25, max(stop_at - time.monotonic(), 0.01)))

    def _wake_response(self, side, p):
        a = self.agents[side].get("commander")
        out = {"woke": True, "turn_id": p["turn_id"], "prematch": bool(p.get("prematch")),
               "observe": bool(p.get("observe")), "reasons": p["reasons"],
               "deadline_in": round(a["deadline"] - self.clock(), 1) if a else None,
               "thinking_sec": round(self.thinking.total(side), 1)}
        if p.get("wake_errors"):
            out["wake_errors"] = p["wake_errors"]
        if p.get("wake_text") is not None:                # the player's own wake text instead of the default summary
            out["wake_text"] = p["wake_text"]
            return out
        try:
            out["summary"] = self.views.render(side, "summary", None)
            if p.get("prematch"):
                out["briefing"] = self.views.briefing(side)
        except FogViolation as e:
            out["summary"], out["error"] = None, "summary withheld: fog assertion failed"
            out["problems"] = e.problems[:3]
        return out

    def turn_begin(self, side, body):
        with self.lock:
            self.tick(self.clock())
            self._need_active(side)
            name = (body or {}).get("agent")
            if name == "commander" or not isinstance(name, str) or not AGENT_NAME.match(name):
                raise ApiError(400, "agent must be a sub-agent name [a-z][a-z0-9_]{0,31}; the commander's turn starts with a wake")
            if name in self.agents[side]:
                raise ApiError(409, f"{name} is already working")
            if sum(1 for n in self.agents[side] if n != "commander") >= rules.MAX_SUBAGENTS:
                raise ApiError(429, f"at most {rules.MAX_SUBAGENTS} sub-agents at the same time")
            now = self.clock()
            self.agents[side][name] = {"t0": now, "deadline": now + rules.SUBAGENT_TASK_SEC}
            self.log.write("wakes", {"t": now, "sec": self.sec, "side": side, "kind": "sub_begin", "agent": name})
            return {"ok": True, "agent": name, "deadline_in": rules.SUBAGENT_TASK_SEC}

    def turn_end(self, side, body):
        with self.lock:
            now = self.clock()
            self.tick(now)
            self._need_active(side)
            body = body or {}
            name = body.get("agent", "commander")
            if name not in self.agents[side]:
                raise ApiError(409, f"{name} has no open turn")
            if name == "commander":
                if body.get("turn_id") != self.turn_id[side]:
                    raise ApiError(409, "turn_id does not match the open turn")
            elif "subscriptions" in body:
                raise ApiError(403, "only the commander changes subscriptions")
            subs = None
            if "subscriptions" in body:
                try:
                    subs = self.wake.set_subs(side, body["subscriptions"], self.sec)
                except ValueError as e:
                    raise ApiError(400, f"subscriptions rejected: {e}")
            self.end_agent(side, name, now, "end_turn")
            return {"ok": True, "thinking_sec": round(self.thinking.total(side), 1), "subscriptions": subs}

    def observe(self, side, on):
        with self.lock:
            self.tick(self.clock())
            if "commander" not in self.agents[side]:
                raise ApiError(409, "observation belongs to the commander's open turn")
            self.observe_on[side] = bool(on)
            self.observe_seen[side] = self.minute_seq
            return {"ok": True, "observing": self.observe_on[side]}

    def view(self, side, what, params):
        def go():
            try:
                v = self.views.render(side, what, params)
            except FogViolation:
                raise ApiError(503, "view withheld: fog assertion failed (logged)")
            except (ValueError, KeyError) as e:
                raise ApiError(400, f"bad query: {e}")
            if v is None:
                raise ApiError(404, "no such view or unit")
            return v
        return self._api(side, go)

    def submit(self, side, body):
        def go():
            code, ans = self.queue.submit(side, body, self.phase, self.sec, self.clock())
            if code != 200:
                raise ApiError(code, ans.pop("error"), **ans)
            return ans
        return self._api(side, go)

    def lint(self, side, body):
        """Check a command as submit would, without queueing it or counting it as illegal (materials/18 2.1)."""
        return self._api(side, lambda: self.queue.lint(side, body if isinstance(body, dict) else {}, self.phase))

    def script_test(self, side, body):
        """Run a stored script (`name`) or a given `source` once on the side's current picture without any effect: its
        commands are linted, not queued; its alarm wakes no one; its memory is not kept."""
        def go():
            b = body if isinstance(body, dict) else {}
            d = self.scripts.defs[side].get(b.get("name")) if b.get("name") else None
            src = b.get("source") if b.get("source") is not None else (d or {}).get("source")
            if not isinstance(src, str) or not src.strip():
                raise ApiError(404 if b.get("name") else 400,
                               f"no script {b.get('name')!r}" if b.get("name") else "give name (a stored script) or source")
            if self.scripts.runner is None:
                raise ApiError(501, "the Lua sandbox is not available here: scripts cannot be test-run")
            stored = ((d or {}).get("trigger") or {}).get("on")       # a stored wake script runs as one by default
            trigger = b.get("trigger") if b.get("trigger") in ("minute", "orders_phase", "event", "wake") else (
                "wake" if stored == "wake" else "minute")
            try:
                view = self.views.render(side, "summary")
                view["units"] = self.views.render(side, "units")["units"]
                view["contacts"] = self.views.render(side, "contacts")["contacts"]
            except FogViolation:
                raise ApiError(503, "view withheld: fog assertion failed (logged)")
            persist = json.loads(json.dumps((d or {}).get("persist") or {}))
            # a wake script sees the events since the last wake, as it would at the next one
            events = self.events.since(side, self.summary_from[side]) if trigger == "wake" else []
            res = self.scripts.runner({"side": side, "name": b.get("name") or "test", "source": src, "trigger": trigger,
                                       "input": view, "events": events, "persist": persist,
                                       "limits": {"instructions": rules.SCRIPT_CALL_INSTR, "ms": rules.SCRIPT_CALL_MS,
                                                  "memory_mb": rules.SCRIPT_MEM_MB}})
            cmds = [c for c in (res.get("commands") or [])][:100]
            self.log.write("scripts", {"t": self.clock(), "sec": self.sec, "side": side, "script": b.get("name"),
                                       "event": "test", "ok": res.get("ok"), "error": res.get("error"),
                                       "commands": len(cmds)})
            return {"ok": bool(res.get("ok")), "error": res.get("error"), "limit_kind": res.get("limit_kind"),
                    "instructions": res.get("instructions"), "ms": res.get("ms"), "trigger": trigger,
                    "log": res.get("log") or [], "alarm": res.get("alarm"), "report": res.get("report"),
                    "commands": [{"body": c, "lint": self.queue.lint(side, c if isinstance(c, dict) else {}, self.phase)}
                                 for c in cmds],
                    "memory": res.get("persist"), "note": "test run: nothing was queued, no alarm, memory not kept"}
        return self._api(side, go)

    def withdraw(self, side, cid):
        def go():
            code, ans = self.queue.withdraw(side, cid)
            if code != 200:
                raise ApiError(code, ans["error"])
            return ans
        return self._api(side, go)

    def scripts_api(self, side, method, name, body):
        def go():
            try:
                if method == "GET":
                    return {"scripts": self.scripts.describe(side)}
                if method == "PUT":
                    return {"ok": True, "script": self.scripts.put(side, name, body or {})}
                return {"ok": self.scripts.delete(side, name)}
            except ScriptError as e:
                raise ApiError(400, str(e))
        return self._api(side, go)

    def compute(self, side, body):
        body = body or {}
        with self.lock:
            self.tick(self.clock())
            self._need_active(side)
            if self.compute_runner is None:
                raise ApiError(501, "compute sandbox is not available")
            prematch = self.phase in rules.PREMATCH_PHASES
            if prematch and self.compute_cpu[side] >= rules.PREMATCH_COMPUTE_CPU_S:
                raise ApiError(429, f"the {rules.PREMATCH_COMPUTE_CPU_S // 3600} hours of CPU for the prematch are used up")
            if not prematch and self.compute_count[side] >= rules.COMPUTE_JOBS:
                raise ApiError(429, f"at most {rules.COMPUTE_JOBS} compute tasks per game")
            if not isinstance(body.get("code"), str):
                raise ApiError(400, "code must be Lua text")
            self.compute_count[side] += 0 if prematch else 1
            now = self.clock()
        res = self.compute_runner(body["code"], body.get("input"), cpu_s=rules.COMPUTE_CPU_S, mem_mb=rules.COMPUTE_MEM_MB)
        cpu_s = (res.get("cpu_ms") if res.get("cpu_ms") is not None else res.get("ms") or 0) / 1000
        with self.lock:
            if prematch:
                self.compute_cpu[side] += cpu_s
            rec = {"t": now, "sec": self.sec, "side": side, "event": "compute", "ok": res.get("ok"),
                   "cpu_s": round(cpu_s, 3), "prematch": prematch, "limit_kind": res.get("limit_kind"),
                   "error": res.get("error")}
            if res.get("log"):
                rec["log"] = [str(x)[:200] for x in res["log"][:20]]
            self.log.write("scripts", rec)
        return res

    # --- workspace notes (the folder is the player's: --workspace0/1, else <run>/workspace/side<N>) ---
    def notes(self, side, method, rel, body):
        return self._api(side, lambda: self.ws[side].notes(method, rel, body))

    # ---------------- admin ----------------
    def pause(self, on):
        with self.lock:
            now = self.clock()
            self.tick(now)
            if on and not self.paused:
                self.paused, self._pause_at = True, now
            elif not on and self.paused:
                d = now - self._pause_at
                for s in SIDES:
                    for a in self.agents[s].values():
                        a["deadline"] += d
                        a["t0"] += d
                self.paused = False
                self.last_tick = now
            return {"paused": self.paused}

    def start(self):
        """Admin: end the prematch preparation now (deploy commands are released, the game goes on)."""
        with self.lock:
            self.prematch_done = True
            return {"started": True}

    def stop(self):
        with self.lock:
            if not self.ended:
                self._finish(self.clock(), "stopped", {"phase": self.phase})
            return {"stopped": True}

    def admin_state(self):
        with self.lock:
            now = self.clock()
            # checked here, where the stopper polls every 15 s; not while an admin pause is on
            if not self.ended and not self.paused and self.relay_at is not None and now - self.relay_at > rules.RELAY_SILENT_SEC:
                self._finish(now, "relay_lost", {"phase": self.phase, "silent_sec": round(now - self.relay_at, 1)})
            self.tick(now)
            due_game, due_wall = self.next_due()
            return {"sec": self.sec, "phase": self.phase, "orders_side": self.orders_side, "ended": self.ended,
                    "ended_by": self.ended_by, "pacing": self.pacing, "players": list(self.players),
                    "paused": self.paused, "gate": self.gate(), "minute_seq": self.minute_seq,
                    "relay_silent_sec": None if self.relay_at is None else round(self.clock() - self.relay_at, 1),
                    "next_game_due": due_game, "next_wall_due": due_wall,
                    "sides": {str(s): {"thinking": self.thinking.view(s),
                                       "agents": sorted(self.agents[s]), "queue": self.queue.counts(s),
                                       "strength_pct": self.world.strength_pct(s) if self.world.state else None,
                                       "illegal_streak": self.queue.streak[s], "dropping": self.queue.dropping[s],
                                       "contacts": len(self.tables[s].by_no), "scripts": self.scripts.describe(s)}
                              for s in SIDES},
                    "audit": self.audit.summary(), "fog_violations": self.fog_violations,
                    "final": self.final}
