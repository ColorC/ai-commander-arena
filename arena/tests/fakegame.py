"""Test doubles for arena-referee: a fake clock, a tiny synthetic world, and a fake game + relay + agents.

Synthetic: mini_state() / sync_body() / Env drive a Referee object directly (no HTTP, no real time).
Replay: Replay reads a computer-vs-computer recording of Alamo at Soest (rec.jsonl snapshots + per-minute diffs) and
its debug log; FakeGame plays it into a referee over HTTP as the relay would (/relay/sync every step, Orders stops
that wait for F9, release batches answered, LUA> lines for the audit) while ScriptedAgent objects act as the two
commanders through the real /agent/* endpoints. Time is virtual: nothing here sleeps; FakeClock moves 1:1 with
game time while the referee says gate=sleep and by 1/ffwd of it otherwise.
"""
import http.client
import json
import os
import re
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from arena.referee import contacts as ct  # noqa: E402
from arena.referee.core import Referee  # noqa: E402
from arena.referee.server import Server, write_tokens  # noqa: E402
from arena.referee.world import Scenario  # noqa: E402

SCENARIO = "alamo-at-soest"
# a recorded computer-vs-computer game of Alamo at Soest (fpc/remote/job-p0.ps1 calibration run, pulled folder), not
# in the repo: $ARENA_DATA/recordings/p1-cal-als2 (tests that need it are skipped without it)
CAL_DIR = os.path.join(os.environ.get("ARENA_DATA") or os.path.join(ROOT, "data"), "recordings", "p1-cal-als2")


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


# ---------------- synthetic world ----------------
def mini_unit(side, loc, name, subs=2, status="Active", parent=None, order="Hold"):
    return {"s": {"name": name, "side": side, "location": loc, "status": status, "morale": 70, "readiness": 100,
                  "icon_type": "Tank", "parent_name": parent or name, "size": "..", "training": 70},
            "o": [{"orders": order, "start_location": str(loc), "start_time": "1989-09-25 07:00:00",
                   "end_time": "1989-09-27 07:00:00"}],
            "u": [{"index": i + 1, "name": "Tank", "status": "Runner", "type": "Tank", "unit": name,
                   "ammo": [{"ammo_id": "AP", "rounds": "10", "max_rounds": "10"}]} for i in range(subs)]}


def mini_units():
    """Side 0: command post, two platoons and a 20-vehicle company in its starting area; side 1: two units in its own."""
    return {"TF Alamo": mini_unit(0, 1320, "TF Alamo"), "Alpha Platoon": mini_unit(0, 1510, "Alpha Platoon"),
            "Bravo Platoon": mini_unit(0, 1612, "Bravo Platoon"), "Big Company": mini_unit(0, 1815, "Big Company", subs=20),
            "9 MR": mini_unit(1, 5110, "9 MR"), "Red Tank Company": mini_unit(1, 5212, "Red Tank Company", subs=3)}


def mini_scenario():
    """The match scenario with the mini world's units as its roster (small experiments check names against it)."""
    sc = Scenario(SCENARIO)
    sc.roster = {s: [{"name": n, "type": u["s"]["icon_type"], "parent": n, "subunits": len(u["u"])}
                     for n, u in mini_units().items() if u["s"]["side"] == s] for s in (0, 1)}
    return sc


def mini_state(sec, units=None):
    return {"seq": sec // 60, "sec": sec, "wall_ms": 0, "phase_hint": "minute",
            "units": units if units is not None else mini_units()}


def contact_line(clock_sec, unit, no, state_word, report):
    h, m, s = clock_sec // 3600, clock_sec % 3600 // 60, clock_sec % 60
    return (f"2026-10-30 08:44:25.911 - ({h:02d}:{m:02d}:{s:02d}) UpdateSpottedStepsAndReport() {unit}: "
            f"Contact {no}, {state_word}: {report}, spotted steps 3, steps 3")


def release_call(batch_id):
    """The Telnet line the relay sends for a batch: arena_release([[ai-arena + backslashes + in/release-<id>.lua]])"""
    bs = chr(92)
    return f"arena_release([[ai-arena{bs}in{bs}release-{batch_id}.lua]])"


def lua_line(clock_sec, text):
    h, m, s = clock_sec // 3600, clock_sec % 3600 // 60, clock_sec % 60
    return f"2026-10-30 08:44:25.911 - ({h:02d}:{m:02d}:{s:02d}) LUA>{text}"


def sync_body(seq, sec, phase="Executing", orders_side=None, state=None, lines=(), release_result=None, alive=True):
    return {"seq": seq, "wall_ms": seq * 300, "game": {"sec": sec, "phase": phase, "orders_side": orders_side,
                                                         "elapsed": sec, "total": 18000, "alive": alive},
            "state": state, "lines": list(lines), "dialogs": [], "release_result": release_result}


class Env:
    """A Referee on a temp run dir with a fake clock; sync() sends bodies straight to the object."""

    def __init__(self, run_dir, scenario=None, **kw):
        self.clock = FakeClock()
        self.ref = Referee(run_dir, scenario or Scenario(SCENARIO), clock=self.clock, **kw)
        self.seq = 0
        self.units = mini_units()
        self.start_clock = self.ref.sc.start_clock

    def sync(self, sec, phase="Executing", orders_side=None, state=True, lines=(), release_result=None, units=None,
             **extra):
        """extra: more fields of the relay body (end_shots=[...]) or of its game part (alive=False)"""
        self.seq += 1
        st = mini_state(sec, json.loads(json.dumps(units or self.units))) if state is True else (state or None)
        body = sync_body(self.seq, sec, phase, orders_side, st, lines, release_result)
        for k, v in extra.items():
            (body["game"] if k in body["game"] else body)[k] = v
        return self.ref.sync(body)

    def move(self, name, loc, **status):
        """Put a unit of the mini world on a hex (and change other GetUnitStatus fields) for the next sync."""
        self.units[name]["s"].update(location=loc, **status)

    def wake(self, side, reasons=None):
        """Open the commander's turn the way a wake does, without waiting for an event."""
        with self.ref.lock:
            if "commander" in self.ref.agents[side]:
                return self.ref.turn_id[side]
            self.ref._start_turn(side, self.clock(), reasons or [{"type": "test"}])
            self.ref.wake_payload[side] = None
            return self.ref.turn_id[side]

    def log(self, name):
        return self.ref.log.read(name)

    def stop(self, sec, side, phase="Orders", answer=True, limit=30):
        """One game stop as the relay lives it: sync until the referee says f9, answering release batches.
        -> (releases, last response)"""
        rels, result, lines = [], None, []
        for _ in range(limit):
            r = self.sync(sec, phase, side, release_result=result, lines=lines)
            result, lines = None, []
            if r["release"]:
                rels.append(r["release"])
                if answer:
                    ids = re.findall(r'cmd_id="([^"]+)"', r["release"]["lua"])
                    result = {"batch_id": r["release"]["batch_id"],
                              "results": [{"cmd_id": c, "ok": True, "ret": True} for c in ids]}
                    lines = [lua_line(self.start_clock + sec, release_call(r["release"]["batch_id"]))]
            if r["f9"]:
                return rels, r
            self.clock.advance(0.3)
        return rels, r

    def contact(self, sec, unit, no, word="Detected", report="2x vehicle(s)"):
        return contact_line(self.start_clock + sec, unit, no, word, report)


class EnvCase(unittest.TestCase):
    """setUp: referee with a loaded mini world at game second 0, fake clock, temp run dir."""

    env_kw = {}                                   # Referee keyword arguments (script_runner=, compute_runner=, ...)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = Env(self.tmp.name, **self.env_kw)
        self.addCleanup(self.env.ref.close)
        self.ref, self.clock = self.env.ref, self.env.clock
        self.env.sync(0)


# ---------------- replay of the recorded game ----------------
def unflatten(flat):
    root = {}
    for path, v in flat.items():
        node = root
        parts = path.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = v

    def fix(x):
        if isinstance(x, dict):
            x = {k: fix(v) for k, v in x.items()}
            if x and all(k.isdigit() for k in x):
                return [x[k] for k in sorted(x, key=int)]
        return x
    out = fix(root)
    for k, default in (("s", {}), ("o", []), ("u", [])):
        out.setdefault(k, default)
    return out


def flatten(v, prefix="", out=None):
    out = {} if out is None else out
    if isinstance(v, dict):
        for k, x in v.items():
            flatten(x, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(v, list):
        for i, x in enumerate(v, 1):
            flatten(x, f"{prefix}.{i}" if prefix else str(i), out)
    else:
        out[prefix] = v
    return out


class Replay:
    """Per-minute full states from rec.jsonl, the log lines that matter, and the Orders phases of the recorded game."""

    def __init__(self, cal_dir=CAL_DIR, sc=None):
        sc = sc or Scenario(SCENARIO)
        with open(os.path.join(cal_dir, "rec.jsonl"), encoding="utf-8") as f:
            recs = [json.loads(line) for line in f if line.strip()]
        snap = next(r for r in recs if r["k"] == "snap")
        self.flat = {u: flatten(c) for u, c in snap["units"].items()}
        self.nested = {u: unflatten(f) for u, f in self.flat.items()}
        self.diffs = {}
        for r in recs:
            if r["k"] == "diff":
                self.diffs.setdefault(r["sec"], []).append(r)
        self.end_sec = max(r["sec"] for r in recs if r["k"] in ("minute", "end") and r.get("sec") is not None)
        self.vp = sc.vp_cells
        debug = next(os.path.join(cal_dir, "collected", f) for f in os.listdir(os.path.join(cal_dir, "collected"))
                     if "DEBUG" in f and f.endswith("0843.txt"))
        self.lines, self.orders = [], []                  # (game sec, raw line) / (game sec, side)
        with open(debug, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                p = ct.parse_line(line)
                if p and p[0] == "contact":
                    self.lines.append(((p[1] - sc.start_clock) % 86400, line.rstrip("\n")))
                elif p and p[0] == "orders":
                    self.orders.append(((p[1] - sc.start_clock) % 86400, p[2]))
        self.sc = sc

    def state_at(self, sec):
        touched = set()
        for d in self.diffs.get(sec, []):
            f = self.flat[d["unit"]]
            for k, v in d.get("set", {}).items():
                f[k] = v
            for k in d.get("del", []):
                f.pop(k, None)
            touched.add(d["unit"])
        for u in touched:
            self.nested[u] = unflatten(self.flat[u])
        return {"seq": sec // 60, "sec": sec, "wall_ms": 0, "units": self.nested}


# ---------------- HTTP ----------------
def serve(ref, run_dir):
    """Start the referee's HTTP server on 127.0.0.1 with a system-chosen port. -> (server, tokens)"""
    tokens = write_tokens(run_dir)
    srv = Server(("127.0.0.1", 0), ref, tokens)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return srv, tokens


class Http:
    def __init__(self, port, token):
        self.port, self.token, self.conn = port, token, None

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        hdr = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        for attempt in (0, 1):
            try:
                if self.conn is None:
                    self.conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
                self.conn.request(method, path, body=data, headers=hdr)
                r = self.conn.getresponse()
                raw = r.read()
                return r.status, (json.loads(raw.decode("utf-8")) if raw else None)
            except (http.client.HTTPException, OSError):
                if self.conn is not None:
                    self.conn.close()
                self.conn = None
                if attempt:
                    raise

    def close(self):
        if self.conn:
            self.conn.close()


# ---------------- the two commanders ----------------
class TurnGone(Exception):
    pass


class ScriptedAgent:
    """wait -> look at the summary -> queue one move -> think for `think` seconds -> end_turn."""

    def __init__(self, http_, side, clock, sc, think=30.0, subscriptions=None):
        self.http, self.side, self.clock, self.sc = http_, side, clock, sc
        self.think, self.subscriptions = think, subscriptions
        self.state, self.ready_at, self.turn_id = "idle", None, None
        self.wakes, self.prematch_turns, self.lost_turns, self.summaries, self.submitted = 0, 0, 0, [], []
        self.turn_starts, self.bad_replies = [], []

    def _own_units(self):
        code, r = self.http.call("GET", "/agent/view/units")
        if code == 409:
            raise TurnGone()
        assert code == 200, (code, r)
        return [u for u in r["units"] if u["status"] == "Active"]

    def _submit(self, body, expect=200):
        code, r = self.http.call("POST", "/agent/queue", body)
        if code == 409:
            raise TurnGone()
        if code != expect:
            self.bad_replies.append((body, code, r))
        if code == 200:
            self.submitted.append(r["cmd_id"])
        return code, r

    def step(self):
        if self.state == "idle":
            code, r = self.http.call("GET", "/agent/wait?timeout=0")
            if code == 200 and r.get("woke"):
                self._on_wake(r)
        elif self.state == "thinking" and self.clock() >= self.ready_at - 1e-9:
            self._finish()

    def _on_wake(self, r):
        try:
            self._on_wake_calls(r)
        except TurnGone:
            self.lost_turns += 1                              # the turn ended before the agent looked (the backstop)
            self.state = "idle"

    def _on_wake_calls(self, r):
        self.state, self.turn_id = "thinking", r["turn_id"]
        self.ready_at = self.clock() + (1.0 if r["prematch"] else self.think)
        self.turn_starts.append(self.clock())
        self.summaries.append(r)
        if r["prematch"]:
            self.prematch_turns += 1
            unit = next(u for u in self._own_units() if u["icon"] != "HQ")
            a = self.sc.start_areas[self.side]
            inside = (a[0] + 2) * 100 + a[2] + 2
            outside = (a[1] + 5) * 100 + a[2] if a[1] + 5 <= self.sc.cols else 100 + 1
            self._submit({"kind": "deploy", "unit": unit["name"], "hex": inside})
            self._submit({"kind": "deploy", "unit": unit["name"], "hex": outside}, expect=400)
            return
        self.wakes += 1
        code, s = self.http.call("GET", "/agent/view/summary")
        if code == 409:
            raise TurnGone()
        assert code == 200, (code, s)
        self.summaries.append(s)
        movers = [u for u in self._own_units() if u["icon"] != "HQ" and self.sc.valid_hex(u["hex"])]   # not aircraft off the map
        if movers:
            u = movers[self.wakes % len(movers)]
            dest = min(u["hex"] + 300, self.sc.cols * 100 + u["hex"] % 100)
            if not self.sc.valid_hex(dest):
                dest = u["hex"]
            self._submit({"kind": "orders", "unit": u["name"], "orders": [{"orders": "Move Hasty", "destination_location": dest}]})
        if self.wakes == 1:                                   # one illegal command: refused, streak 1 only
            self._submit({"kind": "orders", "unit": "no such unit", "orders": [{"orders": "Hold"}]}, expect=400)

    def _finish(self):
        body = {"turn_id": self.turn_id}
        if self.subscriptions and self.wakes == 1:
            body["subscriptions"] = self.subscriptions
        code, r = self.http.call("POST", "/agent/turn/end", body)
        if code != 200:
            self.lost_turns += 1                              # the referee already ended the turn (the backstop)
        self.state = "idle"


# ---------------- the fake game ----------------
class FakeGame:
    def __init__(self, relay, agents, clock, replay, ref, ffwd=6.5, max_step=60.0):
        self.relay, self.agents, self.clock, self.rp, self.ref = relay, agents, clock, replay, ref
        self.ffwd, self.max_step = ffwd, max_step
        self.g, self.seq, self.gate = 0.0, 0, "off"
        self.phase, self.orders_side = "Pregame", 0
        self.lines, self.result = [], None
        self.releases, self.stops, self.sent_batches = [], [], set()
        self.states_sent = 0

    def clock_of_day(self):
        return (self.rp.sc.start_clock + int(self.g)) % 86400

    def sync(self, state=None, alive=True):
        self.seq += 1
        body = sync_body(self.seq, int(self.g + 1e-6), self.phase, self.orders_side, state, self.lines, self.result, alive)
        body["wall_ms"] = int(self.clock() * 1000)
        self.lines, self.result = [], None
        code, resp = self.relay.call("POST", "/relay/sync", body)
        assert code == 200, (code, resp)
        self.gate = resp["gate"]
        self.states_sent += state is not None
        rel = resp.get("release")
        if rel and rel["batch_id"] not in self.sent_batches:
            self.sent_batches.add(rel["batch_id"])
            ids = re.findall(r'cmd_id="([^"]+)"', rel["lua"])
            self.releases.append({"batch_id": rel["batch_id"], "side": rel["side"], "phase": self.phase,
                                  "orders_side": self.orders_side, "sec": int(self.g), "cmds": ids})
            self.lines.append(lua_line(self.clock_of_day(), release_call(rel["batch_id"])))
            self.result = {"batch_id": rel["batch_id"], "results": [{"cmd_id": c, "ok": True, "ret": True} for c in ids]}
        if resp.get("f9"):
            self.lines.append(lua_line(self.clock_of_day(), "arena_f9()"))
        return resp

    def agents_step(self):
        for a in self.agents:
            a.step()

    def stop(self, phase, side, state=None, limit=3000):
        """A game stop (Orders phase / Pregame): sync every 0.3 s until the referee answers f9."""
        self.phase, self.orders_side = phase, side
        self.stops.append((phase, side, int(self.g)))
        for _ in range(limit):
            self.agents_step()
            resp = self.sync(state)
            state = None
            if resp["f9"]:
                break
            self.clock.advance(0.3 if phase == "Orders" else 5.0)
        else:
            raise AssertionError(f"no f9 for {phase} of side {side} at {self.g}")
        self.phase, self.orders_side = "Executing", None
        self.clock.advance(0.3)
        self.sync()

    def advance_to(self, target):
        while self.g < target - 1e-9:
            speed = 1.0 if self.gate == "sleep" else self.ffwd
            step = (target - self.g) / speed
            due_game, due_wall = self.ref.next_due()
            cands = [self.max_step / speed * 1.0]
            cands += [a.ready_at - self.clock() for a in self.agents if a.state == "thinking"]
            if due_wall is not None:
                cands.append(due_wall - self.clock())
            if due_game is not None and due_game > self.g:
                cands.append((due_game - self.g) / speed)
            step_wall = max(min([step] + [c for c in cands if c > 0]), 1e-3)
            self.clock.advance(step_wall)
            self.g = target if step_wall >= step - 1e-9 else self.g + step_wall * speed
            self.sync()
            self.agents_step()

    def run(self):
        rp = self.rp
        self.stop("Pregame", 0, state=rp.state_at(0))
        self.stop("Pregame", 1)
        items = [(s, 0, "line", ln) for s, ln in rp.lines]
        items += [(60 * m, 1, "state", None) for m in range(1, rp.end_sec // 60 + 1)]
        items += [(s, 2, "orders", side) for s, side in rp.orders]
        for sec, _, kind, payload in sorted(items, key=lambda i: (i[0], i[1])):
            self.advance_to(sec)
            if kind == "line":
                self.lines.append(payload)
                self.sync()
                self.agents_step()
            elif kind == "state":
                self.sync(rp.state_at(sec))
                self.agents_step()
            else:
                self.stop("Orders", payload)
        self.phase = "Ended"
        self.sync(alive=False)
        return self
