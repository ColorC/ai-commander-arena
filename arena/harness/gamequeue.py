"""Season 2 game facility (materials/18 2.3): one game at a time on the game machine, no wasted games.

GameQueue: every game (start task, exercise, formal game) is a request of an owner (a player, or "referee" for formal
games); one runs at a time, owners are served round robin (each owner's requests in order), and the time a request
waits is recorded so that it is not charged to the player (the training referee takes it off the player's clock).
Health: the pre-game check through a machine object (the game machine through the game-host adapter, or the dry run's fake): nobody else's
game or calibration on the machine, no game process of ours left (recorded PID + creation time, never by name), the
Telnet port free, no relay token left in ai-arena\\in, and a start minute different from the last game's (autosaves
and debug logs are named by minute; START_GAP as in season 1). Our own leftovers are cleaned by exact PID / exact
file name, then it checks again.
watch(): one step of the game monitor, from the referee's /admin/state: the official end, or a facility failure
(the game did not load, hung, lost its relay). play(): restart on a facility failure, up to MAX_RESTARTS, then park.
Every game ends with a result record: finished (the referee's ended_by, the official end screen) or a named failure.
"""
import threading
import time
from collections import deque

MAX_RESTARTS = 2
START_GAP_SEC = 180                 # between two game starts (season 1: debug log and autosave names are per minute)
GAME_LOAD_SEC = 300                 # a game that sent no state by then failed to load
HANG_SEC = 600                      # game clock still this long outside the deployment phase: hung
PREMATCH_PHASES = ("Pregame", "Setup")
FACILITY_KINDS = {
    "not_loaded": "游戏没有在规定时间内载入",
    "hang": "游戏卡死（游戏时间长时间不走）",
    "relay_lost": "游戏机上的中继失联",
    "cli_died": "CLI 进程意外退出",
    "provider_error": "模型服务连续出错（CLI 自己重试之后）",
    "session_load": "会话续接失败",
    "session_busy": "会话被另一个驱动程序占着",
    "driver_lost": "驱动程序和裁判失去联系",
    "health": "开局前体检一直不过",
    "start_failed": "游戏没能启动",
}


def notice_text(failures):
    """What the players' next wake says after a restart (driver --notice-file)."""
    last = failures[-1]
    return (f"上一次开这一局时出了设施故障：{FACILITY_KINDS.get(last['kind'], last['kind'])}（{last.get('detail') or '无细节'}）。"
            f"这不是你的问题。这一局已经从头重开（第 {len(failures) + 1} 次开局），不算你的时间；"
            "上一次开局里看到的局面作废，以这一局为准。")


class Request:
    def __init__(self, rid, owner, kind, spec, now):
        self.id, self.owner, self.kind, self.spec = rid, owner, kind, spec
        self.enqueued_at, self.started_at, self.finished_at, self.result = now, None, None, None

    @property
    def queued_sec(self):
        return round((self.started_at or time.time()) - self.enqueued_at, 1)

    def public(self):
        return {"id": self.id, "owner": self.owner, "kind": self.kind, "enqueued_at": self.enqueued_at, "started_at": self.started_at,
                "finished_at": self.finished_at, "queued_sec": self.queued_sec if self.started_at else None,
                "status": (self.result or {}).get("status") if self.finished_at else ("running" if self.started_at else "queued")}


class GameQueue:
    """Fair one-at-a-time queue. Owners rotate in the order they first asked; each owner's requests stay in order."""

    def __init__(self, clock=time.time):
        self.clock, self.cv = clock, threading.Condition()
        self.pending, self.rotation, self.last_owner = {}, [], None
        self.all, self.running, self.n = [], None, 0

    def submit(self, owner, kind, spec, rid=None):
        with self.cv:
            self.n += 1
            r = Request(rid or f"g{self.n:03d}", owner, kind, spec, self.clock())
            if owner not in self.pending:
                self.pending[owner] = deque()
                self.rotation.append(owner)
            self.pending[owner].append(r)
            self.all.append(r)
            self.cv.notify_all()
            return r

    def next(self):
        """The next request (round robin over owners), marked running; None when nothing waits or one is running."""
        with self.cv:
            if self.running is not None:
                return None
            k = len(self.rotation)
            first = self.rotation.index(self.last_owner) + 1 if self.last_owner in self.rotation else 0
            for i in range(k):                       # starting after the owner served last (owners may join later)
                owner = self.rotation[(first + i) % k]
                if self.pending[owner]:
                    r = self.pending[owner].popleft()
                    self.last_owner = owner
                    r.started_at, self.running = self.clock(), r
                    return r
            return None

    def requeue(self, r):
        """A request that could not start after all (the machine lock is held elsewhere) goes back to its place."""
        with self.cv:
            if self.running is r:
                self.running = None
            r.started_at = None
            self.pending[r.owner].appendleft(r)
            self.cv.notify_all()

    def finish(self, r, result):
        with self.cv:
            r.finished_at, r.result = self.clock(), result
            if self.running is r:
                self.running = None
            self.cv.notify_all()

    def waiting(self, owner=None):
        with self.cv:
            return [r for o, q in self.pending.items() if owner in (None, o) for r in q]

    def idle(self):
        with self.cv:
            return self.running is None and not any(self.pending.values())


class Health:
    """The pre-game check. machine: occupancy() -> [foreign game holders], calibration_running() -> bool,
    probe(last) -> {"game_alive", "foreign_games", "telnet_listeners", "tokens"}, stop_game(last) -> text,
    remove_tokens(names) -> text. last = the previous game's {"pid", "creation", "telnet"} (ours, recorded)."""

    def __init__(self, machine, telnet_port, clock=time.time, gap=START_GAP_SEC, per_minute=True):
        self.m, self.port, self.clock, self.gap, self.per_minute = machine, telnet_port, clock, gap, per_minute
        self.last_game, self.last_start = None, None

    def check(self):
        problems, fixable = [], []
        try:
            foreign = self.m.occupancy()
        except Exception as e:  # an unreadable occupancy table is not a free machine
            foreign = [f"occupancy unreadable: {type(e).__name__}: {e}"]
        if foreign:
            problems.append({"kind": "busy", "detail": "; ".join(map(str, foreign))[:300]})
        if self.m.calibration_running():
            problems.append({"kind": "busy", "detail": "tools/s2_calibrate.sh is running"})
        pr = self.m.probe(self.last_game) or {}
        if pr.get("game_alive"):
            fixable.append({"kind": "our_game_alive", "detail": f"pid {self.last_game['pid']}"})
        if pr.get("foreign_games"):
            problems.append({"kind": "busy", "detail": f"another game process: {pr['foreign_games']}"})
        ours = self.last_game and pr.get("game_alive") and self.last_game.get("pid")
        others = [x for x in pr.get("telnet_listeners") or [] if x != ours]
        if others:
            problems.append({"kind": "telnet_busy", "detail": f"port {self.port} held by {others}"})
        if pr.get("tokens"):
            fixable.append({"kind": "leftover_tokens", "detail": ", ".join(pr["tokens"]), "names": pr["tokens"]})
        return problems, fixable

    def start_wait(self):
        """Seconds to wait so that this start is START_GAP after the last and in another minute."""
        if self.last_start is None:
            return 0.0
        now = self.clock()
        wait = max(0.0, self.last_start + self.gap - now)
        if self.per_minute and time.strftime("%Y%m%d%H%M", time.localtime(now + wait)) == time.strftime("%Y%m%d%H%M", time.localtime(self.last_start)):
            wait += 60 - (now + wait) % 60 + 1
        return wait

    def ensure(self, log):
        """Check, clean our own leftovers, check again. -> (ok, problems)"""
        problems, fixable = self.check()
        if fixable:
            for f in fixable:
                if f["kind"] == "our_game_alive":
                    log("health_clean", what=f, out=self.m.stop_game(self.last_game))
                elif f["kind"] == "leftover_tokens":
                    log("health_clean", what=f, out=self.m.remove_tokens(f["names"]))
            problems, fixable = self.check()
        ok = not problems and not fixable
        log("health", ok=ok, problems=problems, leftovers=fixable)
        return ok, problems + fixable

    def started(self, game_rec):
        self.last_start, self.last_game = self.clock(), game_rec


class Watch:
    """Game monitor: feed it /admin/state every poll. -> None (go on), ("end", ended_by) or ("facility", kind, detail)."""

    def __init__(self, clock=time.time, load_sec=GAME_LOAD_SEC, hang_sec=HANG_SEC, prematch_sec=600):
        self.clock, self.load_sec, self.hang_sec, self.prematch_sec = clock, load_sec, hang_sec, prematch_sec
        self.t0, self.loaded, self.last_sec, self.last_change, self.phase = clock(), False, None, None, None

    def step(self, code, st):
        now = self.clock()
        if code == 200 and isinstance(st, dict):
            if st.get("ended"):
                if st.get("ended_by") == "relay_lost":
                    return "facility", "relay_lost", f"the relay went silent ({(st.get('final') or {}).get('end_detail') or st.get('relay_silent_sec')})"
                return "end", st.get("ended_by")
            if st.get("minute_seq") and not self.loaded:
                self.loaded, self.last_change, self.last_sec = True, now, st.get("sec")
            if self.loaded:
                # leaving the deployment restarts the count: the clock is still at 0 s then (round 4 E1 11-04: a 19
                # minute deployment was called a hang the poll the game began executing)
                left = self.phase in PREMATCH_PHASES and st.get("phase") not in PREMATCH_PHASES
                if st.get("sec") != self.last_sec or st.get("paused") or left:
                    self.last_sec, self.last_change = st.get("sec"), now
                self.phase = st.get("phase")
                limit = self.hang_sec + (self.prematch_sec if st.get("phase") in PREMATCH_PHASES else 0)
                if now - self.last_change > limit:
                    return "facility", "hang", f"game time stayed at {self.last_sec} s for {int(now - self.last_change)} s (phase {st.get('phase')})"
        if not self.loaded and now - self.t0 > self.load_sec:
            return "facility", "not_loaded", f"no game state within {self.load_sec} s"
        return None


def play(attempt, on_restart=None, max_restarts=MAX_RESTARTS):
    """attempt(n, failures) -> {"status": "finished", ...} or {"status": "facility", "failure": {"kind", "detail"}}.
    A facility failure restarts the game from the start; after max_restarts restarts the game is parked.
    -> the result record: status finished|parked, attempts, facility_failures"""
    failures = []
    for n in range(1 + max_restarts):
        res = attempt(n, list(failures))
        if res.get("status") != "facility":
            return {**res, "attempts": n + 1, "facility_failures": failures}
        failures.append(dict(res["failure"], attempt=n + 1))
        if n < max_restarts and on_restart:
            on_restart(failures)
    return {"status": "parked", "attempts": 1 + max_restarts, "facility_failures": failures,
            "detail": f"facility failures {1 + max_restarts} times: " + "; ".join(f["kind"] for f in failures)}
