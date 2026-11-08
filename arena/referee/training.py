"""The training period (materials/10 P4, 2.10): two players, no game, exercises on request.

Same HTTP front as the game referee (server.py), same tokens: side0.token is player 0 (A), side1.token player 1 (B).
A player has turns like a commander, but only three events wake it: training_start (once, at its first wait),
exercise_done (the director posted the result of its exercise, or that it failed) and exercise_refused (logged in
its feed when a request is refused; the 409 answer itself carries the reason). One turn lasts TRAINING_TURN_SEC.
In a turn a player may read the match scenario's briefings of both sides (with both unit rosters), the terrain table,
its notes and scripts (its workspace: --workspace0/1, the same folders the exercises and the match get; scripts are
only stored here, never run), run compute tasks (PREMATCH_COMPUTE_CPU_S of CPU for the whole training), and ask for
an exercise. A request ends the player's turn; at most one exercise per player is queued or running at a time.
Quotas per player (2.10): EXERCISE_FULL_MAX full exercises (failed ones do not count), small experiments
EXERCISE_SMALL_WALL_SEC of real time together (the wall time the director reports for each done one). A supplementary
session (--full-max, --small-sec, --limit-sec, --supplement-file) has its own quotas and the ruling's text.
--self-full-min N: N of the full exercises must be against the sparring partner (opponent self): a full one against the
AI is refused when the full exercises left are only enough for those (11-03, the new match scenario: each player plays
it against another commander before the formal games, whose engagement is checked).
The director (tools/p4_training.py) reads GET /admin/exercises, runs what is queued and posts the result with
POST /admin/exercise_update; the 12-hour limit is the director's (POST /admin/stop), the referee only records it.
<run>/exercises.json is rewritten on every change of an exercise: the record of the training.

Season 2 (materials/18 2.3): one game machine, one game at a time, so a request may wait in the director's queue.
Each player has its own training clock: wall time since the start minus the time not charged to it, which is the
time its request waited in the queue (requested to running), every game attempt lost to a facility failure
(status "restart": the director restarts the same game from the start) and a parked exercise (status "parked":
facility failures three times; final, uses no quota). A player whose clock passed limit_sec gets no new exercise;
its last open exercise still runs and wakes it once, after which its wait answers training_over.
"""
import json
import os
import threading
import time

from . import rules
from .errors import ApiError
from .log import Log
from .scripts import Scripts, ScriptError
from .views import Views
from .workspace import Workspace
from .core import AGENT_NAME, check_experiment

PLAYERS = (0, 1)
TYPES, OPPONENTS, PACINGS = ("full", "small"), ("ai", "self"), ("turn", "formal")
REQUEST_KEYS = {"type", "opponent", "play_side", "pacing", "units"}
FINAL = ("done", "failed", "parked")


class Training:
    players = PLAYERS

    def __init__(self, run_dir, scenario, clock=time.monotonic, compute_runner=None, workspaces=None,
                 full_max=rules.EXERCISE_FULL_MAX, small_wall_sec=rules.EXERCISE_SMALL_WALL_SEC,
                 limit_sec=rules.TRAINING_REAL_SEC, supplement=None, carry=None, self_full_min=0):
        self.run, self.sc, self.clock = run_dir, scenario, clock
        # a supplementary session (an exercise lost to a facility failure, materials/10 section 8): its own quotas and
        # length, and the ruling's text in the briefing and in every summary
        self.full_max, self.small_wall_sec, self.limit_sec = full_max, small_wall_sec, limit_sec
        self.self_full_min = min(self_full_min, full_max)
        self.supplement = supplement or None
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.log = Log(os.path.join(run_dir, "logs"))
        self.views = Views(self)
        ws = workspaces or {}
        self.ws = {p: Workspace(ws.get(p) or os.path.join(run_dir, "workspace", f"player{p}")) for p in PLAYERS}
        self.scripts = Scripts(self.log, None, self.ws)
        for p in PLAYERS:
            self.scripts.load(p)
        self.compute_runner = compute_runner
        self.compute_cpu = {p: 0.0 for p in PLAYERS}
        self.agents = {p: {} for p in PLAYERS}           # player -> {name: {t0, deadline, turn_id?}}
        self.turn_n = {p: 0 for p in PLAYERS}
        self.turn_id = {p: None for p in PLAYERS}
        self.wake_payload = {p: None for p in PLAYERS}
        self.last_end = {p: None for p in PLAYERS}
        self.feed = {p: [] for p in PLAYERS}
        self.seen = {p: 0 for p in PLAYERS}              # feed id the player's last summary went up to
        self.ex, self.ex_n, self._ev = {}, {p: 0 for p in PLAYERS}, 0
        self.uncharged = {p: 0.0 for p in PLAYERS}       # seconds not on the player's training clock (queue, failures)
        # a training carried on from an earlier referee (its director could no longer start processes, 11-02): each
        # player's clock so far and its finished exercises, which count for the quotas and stay in exercise list/show
        self.carried = {p: 0.0 for p in PLAYERS}
        for p, s in ((carry or {}).get("players") or {}).items():
            self.carried[int(p)] = float(s.get("clock_sec") or 0)
        for r in (carry or {}).get("exercises") or []:
            if r.get("status") in FINAL:
                p = int(r["player"])
                self.ex[r["id"]] = dict(r, player=p)
                self.ex_n[p] = max(self.ex_n[p], int(str(r["id"]).rsplit("-", 1)[-1]))
        self.t0 = clock()
        self.ended = False
        self.ended_at = None

    # ---------------- time and turns ----------------
    def _t(self, now=None):
        return round((self.clock() if now is None else now) - self.t0, 1)

    def player_clock(self, p, now=None):
        """The player's training time: wall time minus what is not charged to it (queue wait, facility failures), plus
        what an earlier referee of the same training charged (carry)."""
        return round((self.clock() if now is None else now) - self.t0 - self.uncharged[p] + self.carried[p], 1)

    def _uncharge(self, p, sec, why, eid):
        sec = max(0.0, float(sec))
        self.uncharged[p] += sec
        self.log.write("exercises", {"player": p, "event": "uncharged", "id": eid, "why": why, "sec": round(sec, 1),
                                     "total": round(self.uncharged[p], 1)})

    def over(self, p, now=None):
        """Player p's clock is out and nothing of its is open: its training is over."""
        return (self.player_clock(p, now) >= self.limit_sec and not self.agents[p] and self.wake_payload[p] is None
                and not any(e["status"] not in FINAL for e in self._mine(p)))

    def tick(self, now):
        for p in PLAYERS:
            for name, a in list(self.agents[p].items()):
                if now >= a["deadline"]:
                    self.end_agent(p, name, now, "timeout")

    def end_agent(self, p, name, now, reason):
        a = self.agents[p].pop(name, None)
        if a is None:
            return
        self.log.write("wakes", {"t": now, "player": p, "kind": "agent_end", "agent": name, "reason": reason,
                                 "turn_id": a.get("turn_id"), "seconds": round(now - a["t0"], 2)})
        self.last_end[p] = f"{name} turn ended ({reason})"
        self.cond.notify_all()

    def _end_all(self, p, now, reason):
        for name in list(self.agents[p]):
            self.end_agent(p, name, now, reason)

    def _start_turn(self, p, now, reasons):
        self.turn_n[p] += 1
        tid = self.turn_id[p] = f"p{p}-t{self.turn_n[p]:04d}"
        self.agents[p]["commander"] = {"t0": now, "deadline": now + rules.TRAINING_TURN_SEC, "turn_id": tid}
        self.scripts.reload(p)                       # an exercise referee may have changed the stored scripts
        self.wake_payload[p] = {"turn_id": tid, "reasons": reasons, "first": self.turn_n[p] == 1}
        self.log.write("wakes", {"t": now, "player": p, "kind": "wake", "turn_id": tid,
                                 "reasons": [{k: v for k, v in r.items() if k != "report"} for r in reasons]})
        self.cond.notify_all()

    def _event(self, p, etype, now, **fields):
        """One event in the player's feed; it wakes the player when idle (training has no merging)."""
        self._ev += 1
        ev = {"id": self._ev, "t": self._t(now), "type": etype, **fields}
        self.feed[p].append(ev)
        self.log.write("events", {"player": p, **{k: v for k, v in ev.items() if k != "report"}})
        if not self.ended and not self.agents[p] and etype != "exercise_refused":
            self._start_turn(p, now, [ev])
        return ev

    def _need_active(self, p):
        if not self.agents[p]:
            raise ApiError(409, "no open turn" + (f", last: {self.last_end[p]}" if self.last_end[p] else "")
                           + ": wait for a wake with /agent/wait")

    def _api(self, p, fn):
        with self.lock:
            self.tick(self.clock())
            self._need_active(p)
            return fn()

    def turn_info(self, p):
        a = self.agents[p].get("commander")
        return {"turn_id": self.turn_id[p] if a else None,           # no deadline_in: the player is not shown the backstop
                "subagents": sorted(n for n in self.agents[p] if n != "commander")}

    # ---------------- agent API ----------------
    def wait(self, p, timeout):
        stop_at = time.monotonic() + max(0.0, float(timeout))
        with self.cond:
            while True:
                now = self.clock()
                self.tick(now)
                if self.ended or (self.turn_n[p] and self.over(p, now)):
                    return {"woke": False, "game_over": True, "training_over": True}
                if self.turn_n[p] == 0 and not self.agents[p]:
                    self._start_turn(p, now, [{"type": "training_start"}])
                if self.wake_payload[p] is not None:
                    w, self.wake_payload[p] = self.wake_payload[p], None
                    return self._wake_response(p, w)
                if "commander" in self.agents[p]:
                    raise ApiError(409, "your turn is open: call end_turn before waiting again")
                if time.monotonic() >= stop_at:
                    return {"woke": False, "training": True}
                self.cond.wait(min(0.25, max(stop_at - time.monotonic(), 0.01)))

    def _wake_response(self, p, w):
        a = self.agents[p].get("commander")
        out = {"woke": True, "turn_id": w["turn_id"], "prematch": False, "observe": False, "training": True,
               "reasons": w["reasons"], "deadline_in": round(a["deadline"] - self.clock(), 1) if a else None,
               "summary": self.summary(p)}
        if w.get("first"):
            out["briefing"] = self.views.brief_both()
        return out

    def summary(self, p):
        since, self.seen[p] = self.seen[p], (self.feed[p][-1]["id"] if self.feed[p] else 0)
        return {"training": {"wall_sec": self.player_clock(p), "limit_sec": self.limit_sec, "scenario": self.sc.name,
                             **({"uncharged_sec": round(self.uncharged[p], 1)} if self.uncharged[p] else {}),
                             **({"supplement": self.supplement} if self.supplement else {})},
                "player": p, "turn": self.turn_info(p),
                "events": [{k: v for k, v in e.items() if k != "report"} for e in self.feed[p] if e["id"] > since],
                "exercises": [self._public(e, report=False) for e in self._mine(p)],
                "quota": self.quota(p)}

    def turn_begin(self, p, body):
        with self.lock:
            self.tick(self.clock())
            self._need_active(p)
            name = (body or {}).get("agent")
            if name == "commander" or not isinstance(name, str) or not AGENT_NAME.match(name):
                raise ApiError(400, "agent must be a sub-agent name [a-z][a-z0-9_]{0,31}; the commander's turn starts with a wake")
            if name in self.agents[p]:
                raise ApiError(409, f"{name} is already working")
            if sum(1 for n in self.agents[p] if n != "commander") >= rules.MAX_SUBAGENTS:
                raise ApiError(429, f"at most {rules.MAX_SUBAGENTS} sub-agents at the same time")
            now = self.clock()
            self.agents[p][name] = {"t0": now, "deadline": now + rules.SUBAGENT_TASK_SEC}
            self.log.write("wakes", {"t": now, "player": p, "kind": "sub_begin", "agent": name})
            return {"ok": True, "agent": name, "deadline_in": rules.SUBAGENT_TASK_SEC}

    def turn_end(self, p, body):
        with self.lock:
            now = self.clock()
            self.tick(now)
            self._need_active(p)
            body = body or {}
            name = body.get("agent", "commander")
            if name not in self.agents[p]:
                raise ApiError(409, f"{name} has no open turn")
            if name == "commander" and body.get("turn_id") != self.turn_id[p]:
                raise ApiError(409, "turn_id does not match the open turn")
            self.end_agent(p, name, now, "end_turn")
            out = {"ok": True, "subscriptions": None}
            if "subscriptions" in body:
                out["note"] = "training has no subscriptions: wakes are exercise_done and exercise_refused only"
            if name == "commander" and not any(e["status"] not in FINAL for e in self._mine(p)):
                out["note_exercise"] = "no exercise of yours is queued or running: nothing will wake you before the training ends"
            return out

    def view(self, p, what, params):
        def go():
            if what == "briefing":
                return self.views.brief_both()
            if what == "terrain":
                try:
                    return self.views.terrain(params or {})
                except (ValueError, KeyError) as e:
                    raise ApiError(400, f"bad query: {e}")
            if what == "summary":
                return self.summary(p)
            raise ApiError(404, "training has no game: the views are briefing, terrain and summary")
        return self._api(p, go)

    def notes(self, p, method, rel, body):
        return self._api(p, lambda: self.ws[p].notes(method, rel, body))

    def scripts_api(self, p, method, name, body):
        def go():
            try:
                if method == "GET":
                    return {"scripts": self.scripts.describe(p), "note": "stored for the exercises and the match; not run in training"}
                if method == "PUT":
                    return {"ok": True, "script": self.scripts.put(p, name, body or {})}
                return {"ok": self.scripts.delete(p, name)}
            except ScriptError as e:
                raise ApiError(400, str(e))
        return self._api(p, go)

    def compute(self, p, body):
        body = body or {}
        with self.lock:
            self.tick(self.clock())
            self._need_active(p)
            if self.compute_runner is None:
                raise ApiError(501, "compute sandbox is not available")
            if self.compute_cpu[p] >= rules.PREMATCH_COMPUTE_CPU_S:
                raise ApiError(429, f"the {rules.PREMATCH_COMPUTE_CPU_S // 3600} hours of CPU before the match are used up")
            if not isinstance(body.get("code"), str):
                raise ApiError(400, "code must be Lua text")
            now = self.clock()
        res = self.compute_runner(body["code"], body.get("input"), cpu_s=rules.COMPUTE_CPU_S, mem_mb=rules.COMPUTE_MEM_MB)
        cpu_s = (res.get("cpu_ms") if res.get("cpu_ms") is not None else res.get("ms") or 0) / 1000
        with self.lock:
            self.compute_cpu[p] += cpu_s
            self.log.write("scripts", {"t": now, "player": p, "event": "compute", "ok": res.get("ok"),
                                       "cpu_s": round(cpu_s, 3), "limit_kind": res.get("limit_kind"),
                                       "error": res.get("error")})
        return res

    # --- exercises ---
    def _mine(self, p):
        return [e for e in self.ex.values() if e["player"] == p]

    def quota(self, p):
        mine = self._mine(p)
        full = sum(1 for e in mine if e["request"]["type"] == "full" and e["status"] not in ("failed", "parked"))
        small = sum(e.get("wall_sec") or 0 for e in mine if e["request"]["type"] == "small" and e["status"] == "done")
        vs_self = sum(1 for e in mine if e["request"]["type"] == "full" and e["request"]["opponent"] == "self"
                      and e["status"] not in ("failed", "parked"))
        return {"full_used": full, "full_max": self.full_max, "full_wall_min": rules.EXERCISE_FULL_WALL_MIN,
                **({"self_full_min": self.self_full_min, "self_full_used": vs_self} if self.self_full_min else {}),
                "small_wall_used_sec": round(small, 1), "small_wall_max_sec": self.small_wall_sec,
                "small_wall_left_sec": round(max(0.0, self.small_wall_sec - small), 1),
                "small_game_min": rules.EXERCISE_SMALL_GAME_MIN, "small_units_max": rules.EXERCISE_SMALL_UNITS}

    def _public(self, e, report=True):
        out = {k: e[k] for k in ("id", "status", "request", "requested_at", "started_at", "finished_at", "wall_sec",
                                 "error", "queued_sec", "facility_failures") if e.get(k) is not None}
        if report and e.get("report") is not None:
            out["report"] = e["report"]
        return out

    def _check_request(self, body):
        if not isinstance(body, dict):
            raise ApiError(400, "the request must be a JSON object")
        extra = set(body) - REQUEST_KEYS
        if extra:
            raise ApiError(400, f"unknown fields {sorted(extra)}; fields are {sorted(REQUEST_KEYS)}")
        rq = {"type": body.get("type"), "opponent": body.get("opponent"), "play_side": body.get("play_side"),
              "pacing": body.get("pacing", "turn"), "scenario": self.sc.name}
        if rq["type"] not in TYPES:
            raise ApiError(400, f"type must be one of {list(TYPES)}")
        if rq["opponent"] not in OPPONENTS:
            raise ApiError(400, f"opponent must be one of {list(OPPONENTS)}")
        if isinstance(rq["play_side"], bool) or rq["play_side"] not in (0, 1):
            raise ApiError(400, "play_side must be 0 (Blue, Player 1) or 1 (Red, Player 2)")
        if rq["opponent"] == "ai" and rq["play_side"] != 0:
            # the game gives its AI only Player 2 (no -config, any command-line StyleOfPlay; the game machine 10-30)
            raise ApiError(400, "opponent=ai needs play_side 0: the game's AI can only command Red (Player 2)")
        if rq["pacing"] not in PACINGS:
            raise ApiError(400, f"pacing must be one of {list(PACINGS)}")
        units = body.get("units")
        if rq["type"] == "small":
            if not isinstance(units, list) or not units or not all(isinstance(u, str) for u in units):
                raise ApiError(400, f"a small experiment needs units: 1 to {rules.EXERCISE_SMALL_UNITS} unit names of "
                                    "play_side (see the briefing's unit lists)")
            try:
                rq["units"] = check_experiment(self.sc, {rq["play_side"]: units})[rq["play_side"]]
            except ValueError as e:
                raise ApiError(400, str(e))
        elif units:
            raise ApiError(400, "units belong to small experiments only")
        return rq

    def _refuse(self, p, now, why):
        self._event(p, "exercise_refused", now, reason=why)
        self.log.write("exercises", {"t": now, "player": p, "event": "refused", "reason": why})
        return ApiError(409, f"exercise refused: {why}", quota=self.quota(p))

    def request_exercise(self, p, body):
        def go():
            now = self.clock()
            rq = self._check_request(body)
            q = self.quota(p)
            open_ = [e["id"] for e in self._mine(p) if e["status"] not in FINAL]
            if open_:
                raise self._refuse(p, now, f"{open_[0]} is not finished yet (one exercise at a time)")
            if self.player_clock(p, now) >= self.limit_sec:
                raise self._refuse(p, now, f"your training time ({self.limit_sec:.0f} s) is used up")
            if rq["type"] == "full" and q["full_used"] >= q["full_max"]:
                raise self._refuse(p, now, f"all {q['full_max']} full exercises are used")
            vs_self_left = self.self_full_min - q.get("self_full_used", 0)
            if rq["type"] == "full" and rq["opponent"] == "ai" and q["full_max"] - q["full_used"] <= vs_self_left:
                raise self._refuse(p, now, f"{self.self_full_min} of the {q['full_max']} full exercises must be against your "
                                           f"sparring partner (opponent self); the ones left are for that")
            if rq["type"] == "small" and q["small_wall_left_sec"] < rules.EXERCISE_SMALL_MIN_LEFT_SEC:
                raise self._refuse(p, now, f"{q['small_wall_left_sec']:.0f} s of small-experiment time left, a new one "
                                           f"needs at least {rules.EXERCISE_SMALL_MIN_LEFT_SEC}")
            self.ex_n[p] += 1
            eid = f"p{p}-{self.ex_n[p]}"
            self.ex[eid] = {"id": eid, "player": p, "request": rq, "status": "queued", "requested_at": self._t(now), "_req": now}
            self.log.write("exercises", {"t": now, "player": p, "event": "requested", "id": eid, "request": rq})
            self._record()
            self._end_all(p, now, "exercise_requested")
            return {"ok": True, "id": eid, "status": "queued", "turn_over": True, "quota": self.quota(p),
                    "note": "your turn is over; the result wakes you (exercise_done)"}
        return self._api(p, go)

    def exercises(self, p):
        return self._api(p, lambda: {"exercises": [self._public(e) for e in self._mine(p)], "quota": self.quota(p)})

    # ---------------- admin (the director) ----------------
    def admin_exercises(self):
        with self.lock:
            self.tick(self.clock())
            return {"exercises": [dict(self._public(e), player=e["player"]) for e in self.ex.values()],
                    "quota": {str(p): self.quota(p) for p in PLAYERS}, "ended": self.ended, "wall_sec": self._t(),
                    # the director ends the training early once both players are idle with nothing left to wake them
                    "players": {str(p): {"turns": self.turn_n[p], "active": bool(self.agents[p]),
                                         "wake_pending": self.wake_payload[p] is not None,
                                         "clock_sec": self.player_clock(p), "uncharged_sec": round(self.uncharged[p], 1),
                                         "over": bool(self.turn_n[p]) and self.over(p)} for p in PLAYERS}}

    def exercise_update(self, body):
        with self.lock:
            now = self.clock()
            self.tick(now)
            e = self.ex.get(body.get("id"))
            if e is None:
                raise ApiError(404, "no such exercise")
            status = body.get("status")
            if status not in ("running", "restart") + FINAL:
                raise ApiError(400, "status must be running, restart, done, failed or parked")
            if e["status"] in FINAL or (status == "running" and e["status"] != "queued") or \
                    (status in ("restart", "parked") and e["status"] != "running"):
                raise ApiError(409, f"{e['id']} is {e['status']}")
            failure = body.get("failure")
            if status in ("restart", "parked"):
                if not isinstance(failure, dict) or not isinstance(failure.get("kind"), str):
                    raise ApiError(400, "a restart or a parked exercise needs failure: {kind, detail}")
                failure = {"kind": failure["kind"][:40], "detail": str(failure.get("detail") or "")[:300], "at": self._t(now)}
                # the lost attempt is not on the player's clock; its game starts again from the start
                self._uncharge(e["player"], now - e["_attempt"], "facility_failure", e["id"])
                e.setdefault("facility_failures", []).append(failure)
                e["_attempt"] = now
                if status == "restart":
                    self.log.write("exercises", {"t": now, "player": e["player"], "event": "restart", "id": e["id"], "failure": failure})
                    self._record()
                    return {"ok": True, "exercise": self._public(e, report=False)}
            wall = body.get("wall_sec")
            if wall is not None and (isinstance(wall, bool) or not isinstance(wall, (int, float)) or wall < 0):
                raise ApiError(400, "wall_sec must be a number of seconds")
            e["status"] = status
            if status == "running":
                e["started_at"], e["_attempt"] = self._t(now), now
                e["queued_sec"] = round(now - e["_req"], 1)
                self._uncharge(e["player"], now - e["_req"], "queued", e["id"])
            else:
                e["finished_at"] = self._t(now)
                e["wall_sec"] = round(float(wall or 0), 1)
                e["report"] = body.get("report")
                if body.get("error"):
                    e["error"] = str(body["error"])[:500]
            self.log.write("exercises", {"t": now, "player": e["player"], "event": status, "id": e["id"],
                                         "wall_sec": e.get("wall_sec"), "error": e.get("error")})
            if status in FINAL:
                ff = e.get("facility_failures") or []
                extra = {"error": e["error"]} if e.get("error") else {}
                if ff:
                    extra["facility_failures"] = ff
                    extra["notice"] = (f"这场演习遇到 {len(ff)} 次设施故障（" + "；".join(f"{x['kind']}：{x['detail']}" for x in ff)
                                       + ("），已从头重开；故障那几次不算你的训练时间。" if status != "parked" else
                                          "），重开两次仍失败，已暂停，交裁判处理；这场不算你的训练时间。"))
                self._event(e["player"], "exercise_done", now, exercise=e["id"], status=status, wall_sec=e["wall_sec"],
                            report=e["report"], **extra)
            self._record()
            return {"ok": True, "exercise": self._public(e, report=False)}

    def _record(self):
        rec = {"scenario": self.sc.name, "ended": self.ended, "wall_sec": self._t(), "ended_at": self.ended_at,
               "quota": {str(p): self.quota(p) for p in PLAYERS},
               "exercises": [dict(self._public(e), player=e["player"]) for e in self.ex.values()]}
        tmp = os.path.join(self.run, "exercises.json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(rec, f, ensure_ascii=False, indent=1, default=str)
            os.replace(tmp, os.path.join(self.run, "exercises.json"))
        except OSError as e:                         # a full disk must not stop the training
            self.log.write("exercises", {"event": "record_failed", "error": str(e)})

    def stop(self):
        """The training is over: open turns end, queued exercises fail, later results are still recorded."""
        with self.lock:
            now = self.clock()
            if not self.ended:
                self.ended, self.ended_at = True, self._t(now)
                for p in PLAYERS:
                    self._end_all(p, now, "training_over")
                for e in self.ex.values():
                    if e["status"] == "queued":
                        e.update(status="failed", finished_at=self._t(now), wall_sec=0.0,
                                 error="the training ended before it ran")
                self.log.write("exercises", {"t": now, "event": "training_over", "wall_sec": self._t(now)})
                self._record()
                self.cond.notify_all()
            return {"stopped": True}

    def admin_state(self):
        with self.lock:
            self.tick(self.clock())
            return {"mode": "training", "ended": self.ended, "wall_sec": self._t(), "limit_sec": self.limit_sec,
                    "players": {str(p): {"agents": sorted(self.agents[p]), "turns": self.turn_n[p],
                                         "quota": self.quota(p), "scripts": self.scripts.describe(p)} for p in PLAYERS}}

    def close(self):
        with self.lock:
            self.log.close()
