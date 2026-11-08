"""P4 training director (materials/10 P4 and 2.10, arena/DESIGN.md 9): a deterministic program, no model in its loop.

    python -I -X utf8 tools/p4_training.py --run $ARENA_DATA/runs/p4-<time> \
        --route0 kimi-code --route1 deepseek-proxy --hours 12 [--dry-run]

Starts the training referee (--mode training, port 41020, workspaces <run>/workspace/player<N>) and one training
player per route, then every 15 s reads GET /admin/exercises (a program poll) and runs what is queued:
  lane = player (player 0: exercise referee 41011, Telnet 47823; player 1: 41012, 47824), the two lanes in parallel;
  exercise referee: --pacing, --agents (vs ai only the play_side), --prematch-sec 600, --units<play_side> (small),
    --workspace<side> = the player's workspace (vs self both sides), --batch-prefix l<lane>-, run dir <run>/ex/<id>;
  exercise players (the player's route; vs self two processes), then the relay on the game machine in the order of
    tools/p3_rehearsal.sh: push the relay token, spawn fpc/remote/relay.ps1, stopper (full: to the end, 90 min cap;
    small: 60 game minutes, cap = the player's small-experiment time left), wait, pull, remove the token, stop the
    local processes by PID; then tools/fpc_endscreen.py on the pulled relay folder fills `official`, and the
    exercise-report.json goes to POST /admin/exercise_update.
  Relay starts are one at a time: the game reads ai-arena\\in\\run.lua once at load and names its debug log by the
  start minute (P0.7), so the next lane's relay waits until this game sent its first state and START_GAP_SEC passed.
At --hours: the training referee is stopped (no more wakes), running exercises are stopped where they stand and
reported as usual, then the training players and the referee are stopped by PID. Never by process name.
Every step is a line of <run>/director.jsonl. --dry-run plays everything locally: no game machine, a fake relay
plays a three-minute game into each exercise referee, scripted fake players ask for one exercise each, and the
director ends once both are done (the test arena/tests/test_director.py runs it).
A supplementary session (an exercise lost to a facility failure, materials/10 section 8): --players 1 --full-max 1
--small-sec 0 --hours 2.5 --supplement-file <ruling.txt> --workspace-root <the training's run>/workspace.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arena.referee import rules  # noqa: E402
from arena.referee.world import Scenario  # noqa: E402

PY = sys.executable
BS = "\\"
# The game machine (README "数据目录与环境变量"): its game folder, its name for the game-host adapter, and this
# machine's address as the game machine reaches the referee (arena/DESIGN.md 1)
GAME = os.environ.get("FPC_GAME_ROOT", "")
NODE = os.environ.get("ARENA_GAME_NODE", "local")
LOCAL_IP = os.environ.get("ARENA_REFEREE_HOST", "127.0.0.1")


def gamehost_cmd():
    """ARENA_GAMEHOST (a command line) or the reference adapter tools/gamehost.py (the game on this machine)."""
    import shlex
    if os.environ.get("ARENA_GAMEHOST"):
        return shlex.split(os.environ["ARENA_GAMEHOST"], posix=os.name != "nt")
    return [PY, "-I", "-X", "utf8", os.path.join(ROOT, "tools", "gamehost.py")]


def need_game_root():
    if not GAME:
        raise SystemExit("set FPC_GAME_ROOT to the game folder on the game machine (README)")
TRAINING_PORT = 41020
LANES = {0: {"port": 41011, "telnet": 47823}, 1: {"port": 41012, "telnet": 47824}}
POLL_SEC = 15
START_GAP_SEC = 180                          # between two relay starts: different start minutes for the debug logs
GAME_LOAD_SEC = 300                          # a game that has sent no state by then failed to start
SETTLE_SEC = 20                              # after the relay is done, before local processes are stopped (p3)
PLAY_MINUTES = 0                             # p2_stopper: no game-minute mark, play to the end
# StyleOfPlay when the opponent is the game's AI, by the side the player takes. the game machine 10-30: launched without
# -config, StyleOfPlay 0, 1 or 3 all give Player 2 to the game's AI (with any -config both sides stay human), so
# against the AI the player is always Blue (the training referee refuses play_side 1); 3 is the two-player game.
AI_STYLE = {0: 0, 1: 1}
NO_ENV = ("KIMI_API_KEY",)  # env -u: the CLI's own session and no stray API key
HARNESS_CLI = {"kimi-code": "kimi", "deepseek-proxy": "opencode"}   # routes played by their official CLI (6a)


def start_player(side_or_player, route, run_dir, referee_url, token_file, mode, **opts):
    """Start one player process against a referee and return its Popen (the caller stops it by this PID).
    mode "training" (side_or_player = player, a 20-minute turn) or "exercise" (= the side it plays). A route in
    HARNESS_CLI is played by its official CLI through arena/harness/driver.py (as in the match); other routes (the
    dry run's fake one) by arena/agent/arena_agent.py. opts: script (fake route steps), out (log file)."""
    if route in HARNESS_CLI:
        cmd = [PY, "-I", "-X", "utf8", os.path.join(ROOT, "arena", "harness", "driver.py"), "--side", str(side_or_player),
               "--cli", HARNESS_CLI[route], "--referee", referee_url, "--token-file", str(token_file), "--run", str(run_dir)]
    else:
        cmd = [PY, "-I", "-X", "utf8", os.path.join(ROOT, "arena", "agent", "arena_agent.py"), "--side", str(side_or_player),
               "--route", route, "--referee", referee_url, "--token-file", str(token_file), "--run", str(run_dir)]
    if mode == "training":
        cmd += ["--turn-limit", str(rules.TRAINING_TURN_SEC)]
    if opts.get("script"):
        cmd += ["--script", str(opts["script"])]
    out = opts.get("out") or os.path.join(run_dir, f"{'player' if mode == 'training' else 'agent'}{side_or_player}.out")
    env = {k: v for k, v in os.environ.items() if k not in NO_ENV}
    with open(out, "ab") as f:
        return subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, env=env, cwd=ROOT)


def http(url, token, method, path, body=None, timeout=20):
    """-> (status, json) of a referee endpoint; (0, {"error": ...}) when it cannot be reached."""
    data = None if body is None and method == "GET" else json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(url + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "null")
        except ValueError:
            return e.code, None
    except (OSError, ValueError) as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def stop_proc(p, wait=10):
    """Stop one process we started, by its own handle (PID); never by name."""
    if p is not None and p.poll() is None:
        p.terminate()
        try:
            p.wait(wait)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(wait)
    return None if p is None else p.returncode


class Referee:
    """A referee process we started: its URL, admin token and process."""

    def __init__(self, proc, url, run_dir):
        self.proc, self.url, self.run = proc, url, run_dir
        with open(os.path.join(run_dir, "secrets", "admin.token"), encoding="utf-8") as f:
            self.admin = f.read().strip()

    def call(self, method, path, body=None):
        return http(self.url, self.admin, method, path, body)


def start_referee(run_dir, args, port, host):
    """python -I arena/referee ... ; waits for its 'on host:port;' line and the token files. -> Referee"""
    os.makedirs(run_dir, exist_ok=True)
    out = os.path.join(run_dir, "referee.out")
    pos = os.path.getsize(out) if os.path.isfile(out) else 0     # a run dir used before: only this start's line
    cmd = [PY, "-I", "-X", "utf8", os.path.join(ROOT, "arena", "referee"), "--run", run_dir,
           "--scenario", rules.EXERCISE_SCENARIO, "--port", str(port), "--host", host] + args
    with open(out, "ab") as f:
        proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=ROOT)
    until = time.time() + 60
    while time.time() < until:
        if proc.poll() is not None:
            break
        try:
            with open(out, "rb") as f:
                f.seek(pos)
                m = re.search(r" on [^\s:]+:(\d+); tokens in", f.read().decode("utf-8", "replace"))
        except OSError:
            m = None
        if m and os.path.isfile(os.path.join(run_dir, "secrets", "admin.token")):
            return Referee(proc, f"http://127.0.0.1:{m[1]}", run_dir)
        time.sleep(0.1)
    stop_proc(proc)
    raise RuntimeError(f"referee did not start (see {out})")


# ---------------- the game machine: as tools/p3_rehearsal.sh does it ----------------
class Remote:
    def __init__(self, director):
        self.d = director
        self.gamehost = gamehost_cmd()
        need_game_root()

    def _host(self, exrun, step, *args):
        with open(os.path.join(exrun, "args", step + ".txt"), "ab") as f:
            rc = subprocess.run([*self.gamehost, *args], stdout=f, stderr=subprocess.STDOUT, cwd=ROOT).returncode
        self.d.log("gamehost", step=step, rc=rc, out=os.path.join(exrun, "args", step + ".txt"))
        return rc

    def push_token(self, exrun, name):
        """The relay token under a name of this exercise: the two lanes share the remote ai-arena\\in folder."""
        local = os.path.join(exrun, "secrets", f"relay-{name}.token")
        shutil.copyfile(os.path.join(exrun, "secrets", "relay.token"), local)
        self._host(exrun, "push", "push", NODE, local, GAME + BS + "ai-arena" + BS + "in")
        return GAME + BS + BS.join(["ai-arena", "in", f"relay-{name}.token"])

    def spawn(self, exrun, name, relay_args, cap_min, purpose):
        path = os.path.join(exrun, "args", "relay.json")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(relay_args, f)
        return self._host(exrun, "spawn", "spawn", NODE, name, "--file", os.path.join(ROOT, "fpc", "remote", "relay.ps1"),
                          "--args", path, "--cwd", GAME, "--window", "hidden", "--deadline", f"{int(cap_min) + 20}m",
                          "--kind", "experiment", "--purpose", purpose, "--mem-gb", "3")

    def wait(self, exrun, name):
        return self._host(exrun, "wait", "wait", NODE, name, "--timeout", "1800")

    def pull(self, exrun, name):
        self._host(exrun, "pull", "pull", NODE, GAME + BS + BS.join(["ai-arena", "out", name]), exrun + "/")
        return os.path.join(exrun, name)

    def remove_token(self, exrun, token_path):
        self._host(exrun, "token-rm", "ps", NODE, "-c", f"Remove-Item -LiteralPath '{token_path}' -Force")


class DryRemote:
    """--dry-run: no game machine. spawn starts a fake relay thread on this machine, pull finds its folder in place."""

    def __init__(self, director):
        self.d, self.relays = director, {}

    def push_token(self, exrun, name):
        return os.path.join(exrun, "secrets", "relay.token")

    def spawn(self, exrun, name, relay_args, cap_min, purpose):
        with open(os.path.join(exrun, "args", "relay.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(relay_args, f)
        r = self.relays[name] = FakeRelay(relay_args["Referee"], relay_args["TokenFile"], os.path.join(exrun, name))
        r.start()
        return 0

    def wait(self, exrun, name):
        self.relays[name].join(120)
        return 0 if not self.relays[name].is_alive() else 1

    def pull(self, exrun, name):
        return os.path.join(exrun, name)

    def remove_token(self, exrun, token_path):
        pass


class FakeRelay(threading.Thread):
    """Three game minutes of Alamo at Soest as the relay would bring them: the setup stop, side 0's and side 1's
    Orders phases (each until the referee says F9, releases answered and logged as LUA> lines), then the end screen
    and the game gone. Units from the scenario roster, all at the edge of their own starting area."""

    def __init__(self, url, token_file, out_dir):
        super().__init__(daemon=True)
        self.url, self.out = url, out_dir
        with open(token_file, encoding="utf-8") as f:
            self.token = f.read().strip()
        self.sc = Scenario(rules.EXERCISE_SCENARIO)
        self.seq, self.lines, self.result, self.error = 0, [], None, None

    def state(self, sec):
        units = {}
        for side, rows in self.sc.roster.items():
            c0, _, r0, r1 = self.sc.start_areas[side]
            for i, r in enumerate(rows):
                units[r["name"]] = {"s": {"name": r["name"], "side": side, "location": c0 * 100 + r0 + i % (r1 - r0 + 1),
                                          "status": "Reinforcement" if r.get("arrives_later") else "Active",
                                          "icon_type": r["type"], "parent_name": r["parent"]},
                                    "o": [{"orders": "Hold"}], "u": [{"index": k + 1, "status": "Runner"}
                                                                   for k in range(r["subunits"])]}
        return {"seq": sec // 60, "sec": sec, "units": units}

    def sync(self, sec, phase, side=None, state=None, alive=True, end_shots=()):
        self.seq += 1
        body = {"seq": self.seq, "wall_ms": int(time.time() * 1000), "state": state, "lines": self.lines,
                "game": {"sec": sec, "phase": phase, "orders_side": side, "alive": alive, "hold": False},
                "dialogs": [], "release_result": self.result, "end_shots": list(end_shots)}
        self.lines, self.result = [], None
        code, r = http(self.url, self.token, "POST", "/relay/sync", body)
        if code != 200:
            raise RuntimeError(f"sync answered {code}: {r}")
        rel = r.get("release")
        if rel:
            ids = re.findall(r'cmd_id="([^"]+)"', rel["lua"])
            c = (self.sc.start_clock + sec) % 86400
            self.lines.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')}.000 - ({c // 3600:02d}:{c % 3600 // 60:02d}:{c % 60:02d}) LUA>"
                              f"arena_release([[ai-arena{BS}in{BS}release-{rel['batch_id']}.lua]])")
            self.result = {"batch_id": rel["batch_id"], "results": [{"cmd_id": i, "ok": True, "ret": True} for i in ids]}
        return r

    def stop_until_f9(self, sec, phase, side, state=None):
        until = time.time() + 120
        while time.time() < until:
            r = self.sync(sec, phase, side, state)
            state = None
            if r["f9"] or r["stop"]:
                return r
            time.sleep(0.05)
        raise RuntimeError(f"no F9 for {phase} side {side} at {sec}")

    def run(self):
        os.makedirs(self.out, exist_ok=True)
        try:
            for sec, phase, side in ((0, "Setup", None), (60, "Orders", 0), (120, "Orders", 1)):
                if sec:
                    self.sync(sec, "Executing", state=self.state(sec))
                if self.stop_until_f9(sec, phase, side, self.state(0) if not sec else None)["stop"]:
                    break
            else:
                self.sync(180, "Executing", state=self.state(180))
                self.sync(180, "Ended", end_shots=["TfTOC_ScnInfo"])
                self.sync(180, "Ended", alive=False, end_shots=["TfTOC_ScnInfo"])
                with open(os.path.join(self.out, "end-result.json"), "w", encoding="utf-8", newline="\n") as f:
                    json.dump({"result": {"vp_share_pct": 50.0, "result": "Contested Result", "dry_run": True}}, f)
        except Exception as e:                       # the exercise then ends by the stopper or as game_gone
            self.error = f"{type(e).__name__}: {e}"
        with open(os.path.join(self.out, "relay.log"), "w", encoding="utf-8", newline="\n") as f:
            f.write(f"fake relay done; error: {self.error}\n")


# ---------------- the director ----------------
class Director:
    def __init__(self, a):
        self.a, self.run, self.dry = a, os.path.abspath(a.run), a.dry_run
        self.fake = self.dry or a.smoke                # scripted fake players (--smoke: with the real game-machine path)
        self.routes = {0: a.route0, 1: a.route1}
        self.pl = tuple(int(x) for x in a.players.split(",") if x.strip())   # the players of this session
        self.poll = a.poll_sec if a.poll_sec is not None else (0.2 if self.dry else POLL_SEC)
        self.gap = 0 if self.dry else START_GAP_SEC
        self.settle = 0.3 if self.dry else SETTLE_SEC
        self.ai_style = {0: a.ai_style_0, 1: a.ai_style_1}
        self.remote = DryRemote(self) if self.dry else Remote(self)
        self.start_lock = threading.Lock()
        self.over = threading.Event()                 # set at the end of the training: no more relay starts
        self.log_lock = threading.Lock()
        self.lanes = {0: None, 1: None}               # player -> {"thread", "ex", "ref"}
        self.started = set()
        self.t0 = time.time()
        os.makedirs(os.path.join(self.run, "args"), exist_ok=True)
        os.makedirs(os.path.join(self.run, "ex"), exist_ok=True)

    def log(self, event, **f):
        row = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "wall": round(time.time() - self.t0, 1), "event": event, **f}
        line = json.dumps(row, ensure_ascii=False, default=str)
        with self.log_lock:
            print(line, flush=True)
            try:
                with open(os.path.join(self.run, "director.jsonl"), "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass                                  # a full disk must not end the training

    def workspace(self, p):
        return os.path.join(os.path.abspath(self.a.workspace_root) if self.a.workspace_root else
                            os.path.join(self.run, "workspace"), f"player{p}")

    # --- training ---
    def dry_script(self, p):
        """--dry-run: player 0 asks for a small experiment against the AI, player 1 for a full one against itself.
        --smoke: player 1 asks for a small one against itself instead (side 1), so both games stay short."""
        sc = Scenario(rules.EXERCISE_SCENARIO)
        units = {s: [r["name"] for r in sc.roster[s] if not r.get("arrives_later")][:3] for s in (0, 1)}
        ask = ({"type": "small", "opponent": "ai", "play_side": 0, "units": units[0]} if p == 0 else
               {"type": "small", "opponent": "self", "play_side": 1, "units": units[1]} if self.a.smoke else
               {"type": "full", "opponent": "self", "play_side": 1, "pacing": "turn"})
        path = os.path.join(self.run, "args", f"player{p}-script.json")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump([{"tool_calls": [{"name": "get_briefing", "arguments": {}}]},
                       {"tool_calls": [{"name": "request_exercise", "arguments": ask}]}], f)
        return path

    def start_training(self):
        args = ["--mode", "training", "--workspace0", self.workspace(0), "--workspace1", self.workspace(1),
                "--full-max", str(self.a.full_max), "--small-sec", str(self.a.small_sec), "--limit-sec", str(self.a.hours * 3600)]
        if self.a.supplement_file:
            args += ["--supplement-file", os.path.abspath(self.a.supplement_file)]
        port = 0 if self.dry else self.a.training_port
        self.tref = start_referee(self.run, args, port, "127.0.0.1")
        self.log("training_referee", pid=self.tref.proc.pid, url=self.tref.url, out=os.path.join(self.run, "referee.out"),
                 args=args)
        self.players = {}
        for p in self.pl:
            route = "fake" if self.fake else self.routes[p]
            self.players[p] = start_player(p, route, self.run, self.tref.url, os.path.join(self.run, "secrets", f"side{p}.token"),
                                           "training", script=self.dry_script(p) if self.fake else None)
            self.log("training_player", player=p, route=route, pid=self.players[p].pid,
                     out=os.path.join(self.run, f"player{p}.out"))

    # --- one exercise (a lane thread) ---
    def lane(self, ex, quota):
        eid, p, rq = ex["id"], ex["player"], ex["request"]
        exrun = os.path.join(self.run, "ex", eid)
        os.makedirs(os.path.join(exrun, "args"), exist_ok=True)
        procs, status, report, wall, error = [], "failed", None, 0.0, None
        code, r = self.tref.call("POST", "/admin/exercise_update", {"id": eid, "status": "running"})
        self.log("exercise_start", id=eid, player=p, request=rq, run=exrun, update=code)
        try:
            status, report, wall, error = self.exercise(ex, exrun, quota, procs)
        except Exception as e:                       # whatever broke, the player hears of it and may ask again
            error = f"{type(e).__name__}: {e}"
            self.log("exercise_error", id=eid, error=error)
        finally:
            for name, proc in procs:
                rc = stop_proc(proc)
                self.log("stopped", id=eid, process=name, pid=proc.pid, rc=rc)
        body = {"id": eid, "status": status, "wall_sec": round(wall, 1), "report": report}
        if error:
            body["error"] = error
        code, r = self.tref.call("POST", "/admin/exercise_update", body)
        self.log("exercise_done", id=eid, status=status, wall_sec=round(wall, 1), error=error, update=code,
                 ended_by=(report or {}).get("ended_by"), official=(report or {}).get("official"),
                 report=os.path.join(exrun, "exercise-report.json"))

    def exercise(self, ex, exrun, quota, procs):
        eid, p, rq = ex["id"], ex["player"], ex["request"]
        side, lane = rq["play_side"], LANES[p]
        sides = [side] if rq["opponent"] == "ai" else [0, 1]
        small = rq["type"] == "small"
        args = ["--exercise", "--pacing", rq["pacing"], "--agents", ",".join(map(str, sides)),
                "--prematch-sec", str(rules.EXERCISE_PREMATCH_SEC), "--batch-prefix", f"l{p}-"]
        if small:
            args += [f"--units{side}", ",".join(rq["units"])]
        for s in sides:
            args += [f"--workspace{s}", self.workspace(p)]
        ref = start_referee(exrun, args, 0 if self.dry else lane["port"], "127.0.0.1" if self.dry else "0.0.0.0")
        procs.append(("referee", ref.proc))
        self.lanes[p]["ref"] = ref
        port = int(ref.url.rsplit(":", 1)[1])
        self.log("exercise_referee", id=eid, pid=ref.proc.pid, url=ref.url, args=args)
        route = "fake" if self.fake else self.routes[p]
        for s in sides:
            ag = start_player(s, route, exrun, ref.url, os.path.join(exrun, "secrets", f"side{s}.token"), "exercise")
            procs.append((f"player-side{s}", ag))
            self.log("exercise_player", id=eid, side=s, route=route, pid=ag.pid, out=os.path.join(exrun, f"agent{s}.out"))
        if small:
            game_min, cap_min = rules.EXERCISE_SMALL_GAME_MIN, quota["small_wall_left_sec"] / 60
        else:
            game_min, cap_min = PLAY_MINUTES, rules.EXERCISE_FULL_WALL_MIN
        name = f"p4-{eid}-{time.strftime('%Y%m%d-%H%M%S')}"
        relay_url = f"http://{'127.0.0.1' if self.dry else LOCAL_IP}:{port}"
        with self.start_lock:                         # one game start at a time (run.lua, debug-log start minute)
            if self.over.is_set():
                return "failed", None, 0.0, "the training ended before the game started"
            token = self.remote.push_token(exrun, name)
            relay_args = {"RunName": name, "TokenFile": token, "TimedMessages": 1, "MaxMinutes": int(cap_min) + 10,
                          "StyleOfPlay": 3 if rq["opponent"] == "self" else self.ai_style[side],
                          **({} if rq["opponent"] == "self" else {"Config": ""}),   # vs the AI: no -config
                          "Telnet": f"127.0.0.1:{lane['telnet']}", "Referee": relay_url,
                          # turn pacing keeps the hand-over dialog open while the Orders phase waits for the player
                          "PassDeferSec": rules.TURN_ORDERS_SEC + 20 if rq["pacing"] == "turn" else 20}
            t_spawn = time.time()
            rc = self.remote.spawn(exrun, name, relay_args, cap_min,
                                   f"ai-commander-arena P4 exercise {eid}: {rq['type']} vs {rq['opponent']}, "
                                   f"side {side}, {rq['pacing']}")
            self.log("relay_spawned", id=eid, name=name, rc=rc, style=relay_args["StyleOfPlay"], telnet=relay_args["Telnet"])
            loaded = self.wait_loaded(ref)
            while time.time() - t_spawn < self.gap:
                time.sleep(1)
        if not loaded:
            ref.call("POST", "/admin/stop")
            self.log("game_not_started", id=eid, after_sec=GAME_LOAD_SEC)
        t_stop = time.time()
        if loaded:
            with open(os.path.join(exrun, "stopper.out"), "ab") as f:
                rc = subprocess.run([PY, "-I", "-X", "utf8", os.path.join(ROOT, "tools", "p2_stopper.py"), exrun,
                                     str(game_min), f"{cap_min:.2f}", ref.url, str(self.poll)],
                                    stdout=f, stderr=subprocess.STDOUT, cwd=ROOT).returncode
            self.log("stopper_done", id=eid, rc=rc, game_min=game_min, cap_min=round(cap_min, 2))
        wall = time.time() - t_stop
        self.remote.wait(exrun, name)
        relay_dir = self.remote.pull(exrun, name)
        self.remote.remove_token(exrun, token)
        time.sleep(self.settle)
        for pname, proc in procs:
            rc = stop_proc(proc)
            self.log("stopped", id=eid, process=pname, pid=proc.pid, rc=rc)
        procs.clear()
        if not loaded:
            return "failed", None, 0.0, f"the game did not start within {GAME_LOAD_SEC} s"
        official = self.official(eid, relay_dir)
        path = os.path.join(exrun, "exercise-report.json")
        if not os.path.isfile(path):
            why = "the exercise referee wrote no exercise-report.json"
            try:                                     # the stopper's last read of the referee
                with open(os.path.join(exrun, "admin-state.json"), encoding="utf-8") as f:
                    if json.load(f).get("ended_by") == "relay_lost":
                        why = ("the relay on the game machine stopped answering (a facility failure); "
                               "this exercise uses none of your quota")
            except (OSError, ValueError):
                pass
            return "failed", None, wall, why
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
        report["official"] = official
        report["id"] = eid
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        return "done", report, wall, None

    def wait_loaded(self, ref):
        until = time.time() + (60 if self.dry else GAME_LOAD_SEC)
        while time.time() < until:
            code, st = ref.call("GET", "/admin/state")
            if code == 200 and (st.get("minute_seq") or st.get("ended")):
                return True
            time.sleep(0.2 if self.dry else 5)
        return False

    def official(self, eid, relay_dir):
        """The game's own end screen, read by tools/fpc_endscreen.py (the user's Python, not -I: rapidocr)."""
        shots = os.listdir(relay_dir) if os.path.isdir(relay_dir) else []
        if not self.dry and any(n.startswith("end-") and n.endswith(".bmp") for n in shots):
            with open(os.path.join(os.path.dirname(relay_dir), "endscreen.out"), "ab") as f:
                rc = subprocess.run([PY, "-X", "utf8", os.path.join(ROOT, "tools", "fpc_endscreen.py"), relay_dir],
                                    stdout=f, stderr=subprocess.STDOUT, cwd=ROOT).returncode
            self.log("endscreen", id=eid, rc=rc, dir=relay_dir)
        try:
            with open(os.path.join(relay_dir, "end-result.json"), encoding="utf-8") as f:
                return json.load(f).get("result") or None
        except (OSError, ValueError):
            return None

    # --- main loop ---
    def finished(self, st, p):
        """Player p has had a turn, has none open or due, and nothing of its is queued, running or being played."""
        s = (st.get("players") or {}).get(str(p))
        return (s is not None and s["turns"] > 0 and not s["active"] and not s["wake_pending"] and not self.busy(p)
                and not any(e["player"] == p and e["status"] in ("queued", "running") for e in st["exercises"]))

    def busy(self, p):
        lane = self.lanes[p]
        return lane is not None and lane["thread"].is_alive()

    def step(self):
        code, st = self.tref.call("GET", "/admin/exercises")
        if code != 200:
            self.log("poll_error", status=code, body=st)
            return None
        for e in st["exercises"]:
            p = e["player"]
            if e["status"] == "queued" and e["id"] not in self.started and not self.busy(p):
                self.started.add(e["id"])
                t = threading.Thread(target=self.lane, args=(e, st["quota"][str(p)]), name=f"lane{p}", daemon=True)
                self.lanes[p] = {"thread": t, "ex": e, "ref": None}
                t.start()
        return st

    def main(self):
        self.log("start", run=self.run, routes=self.routes, hours=self.a.hours, dry_run=self.dry, smoke=self.a.smoke,
                 poll_sec=self.poll)
        self.start_training()
        deadline, why = self.t0 + self.a.hours * 3600, "hours"
        self.idle_polls = 0
        try:
            while time.time() < deadline:
                st = self.step()
                if self.fake and st and all(any(e["player"] == p and e["status"] in ("done", "failed")
                                               for e in st["exercises"]) for p in self.pl) \
                        and not any(e["status"] in ("queued", "running") for e in st["exercises"]) \
                        and not any(self.busy(p) for p in self.pl):
                    why = "dry_run_done" if self.dry else "smoke_done"
                    break
                gone = [p for p, pr in self.players.items() if pr.poll() is not None]
                if len(gone) == len(self.pl) and not any(self.busy(p) for p in self.pl):
                    why = "players_gone"
                    break
                # a player that ended its turn with no exercise queued or running gets no more wakes (training.py
                # turn_end note_exercise): when all are there for two polls running, the training is over
                idle = st is not None and all(self.finished(st, p) for p in self.pl)
                self.idle_polls = self.idle_polls + 1 if idle else 0
                if self.idle_polls >= 2:
                    why = "players_finished"
                    break
                time.sleep(self.poll)
        except KeyboardInterrupt:
            why = "interrupted"
        self.shutdown(why)

    def shutdown(self, why):
        self.over.set()
        self.log("training_over", why=why, wall_sec=round(time.time() - self.t0, 1))
        code, r = self.tref.call("POST", "/admin/stop")
        self.log("training_referee_stop", status=code)
        for p in (0, 1):                               # running exercises end where they stand (2.10)
            lane = self.lanes[p]
            if self.busy(p) and lane.get("ref") is not None:
                code, r = lane["ref"].call("POST", "/admin/stop")
                self.log("exercise_stop", id=lane["ex"]["id"], status=code)
        for p in (0, 1):
            if self.lanes[p] is not None:
                self.lanes[p]["thread"].join(120 if self.dry else 3600)
                if self.lanes[p]["thread"].is_alive():
                    self.log("lane_still_running", player=p, id=self.lanes[p]["ex"]["id"])
        code, st = self.tref.call("GET", "/admin/exercises")
        until = time.time() + (20 if self.dry else 120)
        while time.time() < until and any(pr.poll() is None for pr in self.players.values()):
            time.sleep(0.2)
        for p, pr in self.players.items():
            self.log("stopped", process=f"training-player{p}", pid=pr.pid, rc=stop_proc(pr))
        self.log("stopped", process="training-referee", pid=self.tref.proc.pid, rc=stop_proc(self.tref.proc))
        self.log("end", exercises=[{k: e.get(k) for k in ("id", "player", "status", "wall_sec", "error")}
                                   for e in (st or {}).get("exercises", [])] if code == 200 else None,
                 record=os.path.join(self.run, "exercises.json"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True, help="run directory, e.g. $ARENA_DATA/runs/p4-<time>")
    ap.add_argument("--route0", default="kimi-code", help="player 0 (A)")
    ap.add_argument("--route1", default="deepseek-proxy", help="player 1 (B)")
    ap.add_argument("--hours", type=float, default=rules.TRAINING_REAL_SEC / 3600)
    ap.add_argument("--dry-run", action="store_true", help="no game machine: fake relay, fake players (for tests)")
    ap.add_argument("--smoke", action="store_true",
                    help="the real game-machine path with scripted fake players: one small experiment each (vs AI, vs self)")
    ap.add_argument("--ai-style-0", type=int, default=AI_STYLE[0], help="StyleOfPlay vs the AI when playing side 0")
    ap.add_argument("--ai-style-1", type=int, default=AI_STYLE[1], help="StyleOfPlay vs the AI when playing side 1")
    ap.add_argument("--training-port", type=int, default=TRAINING_PORT)
    ap.add_argument("--poll-sec", type=float, help=f"poll interval (default {POLL_SEC}; dry run 0.2)")
    ap.add_argument("--players", default="0,1", help="the players of this session (a supplementary one: 1 or 0)")
    ap.add_argument("--full-max", type=int, default=rules.EXERCISE_FULL_MAX, help="full exercises per player")
    ap.add_argument("--small-sec", type=float, default=rules.EXERCISE_SMALL_WALL_SEC, help="small-experiment seconds per player")
    ap.add_argument("--supplement-file", help="a supplementary session: the ruling's text (UTF-8) the players get")
    ap.add_argument("--workspace-root", help="folder of the player<N> workspaces (default <run>/workspace)")
    a = ap.parse_args(argv)
    if not set(int(x) for x in a.players.split(",") if x.strip()) <= {0, 1}:
        ap.error("--players: 0, 1 or 0,1")
    Director(a).main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
