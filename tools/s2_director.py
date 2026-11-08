"""Season 2 director (materials/18 2.2, 2.3): a deterministic program, no model in its loop.

    python -I -X utf8 tools/s2_director.py --run $ARENA_DATA/runs/s2-<what>-<HHMMSS> \
        --players A=kimi,B=opencode --phases start,baseline,training,formal [--homes $ARENA_DATA/season2] [--dry-run]

Players are persistent homes (arena/harness/player.py): every phase starts arena/harness/driver.py with the player's
home, which continues the player's one ACP session (session/resume in a fresh CLI process). Phases, in order:
  start     one game per player against the game's AI (the start task), the player commands side 0;
  baseline  one game per player on the formal scenario (--baseline-scenario), side 0 against the game's AI, no pass
            mark: the player's level against the computer and the humans (materials/20 section 1);
  training  the training referee (--mode training) and one training driver per player; an exercise request goes into
            the game queue; when it runs, the player's training driver is stopped (POST <control>/stop, it gives the
            session back), the player's own session commands the exercise, a vs-self exercise's other side is the
            player's sparring partner (<homes>/sparring/<player>: same model, its own session, workspace, memory);
            afterwards the result goes to the training referee and the training driver is started again (load);
  formal    the formal game(s) (--formal "A:0,B:1;B:0,A:1"), owner "referee" in the same queue.
Games: one at a time (arena/harness/gamequeue.py): the queue serves its owners round robin; a request's waiting time
is not charged (the training referee takes queue time and failed attempts off the player's clock); before every game
the health check (our leftovers cleaned by exact PID / file name); a facility failure (not loaded, hung, relay lost,
CLI died, provider errors, session load) restarts the same game from the start with a notice to the players, up to
2 restarts, then the game is parked and reported (parked.jsonl). The game machine lock <data>/game-machine.lock
(PID + start time) keeps two director processes from running games at the same time; the health check refuses to
start while tools/s2_calibrate.sh or another game holds the machine.
Files: <run>/director.jsonl (every step one JSON line), <run>/games.jsonl (one result record per game: finished with
the referee's ended_by and the official end screen, or parked with its named failures), <run>/games/<id>/a<n>/ (one
attempt: referee, drivers' transcripts, relay folder), <run>/training/ (training referee and drivers).
--dry-run: no game machine, a fake relay plays three game minutes into each game referee, players are the fake ACP CLI
with scripted models (each asks for two small exercises: vs the AI, vs its sparring partner), --inject <id>:<kind>[*N]
makes the first (N) attempts of that game fail (not_loaded, hang, relay_lost, cli_died; *3 parks it).
--smoke N: N short exercises in a row against the AI for the first player (default the fake CLI, no model cost;
--smoke-cli real for the player's own CLI) through queue, health check and restarts: the real-machine smoke.
Stopping: create the file <run>/STOP (or Ctrl-C / Ctrl-Break). The game running ends the way every game ends: the
referee stops the relay, the relay job is waited for (killed if it does not end) and our game is stopped by its
recorded PID; queued games are not played (director.jsonl not_played); then the machine lock is let go.
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import p4_training as p4  # noqa: E402
from arena.harness import acp, gamequeue as gq, player, scorecard  # noqa: E402
from arena.referee import rules  # noqa: E402
from arena.referee.world import Scenario  # noqa: E402


PY = sys.executable
BS = "\\"
GAME, NODE, LOCAL_IP = p4.GAME, p4.NODE, p4.LOCAL_IP
DATA = os.environ.get("ARENA_DATA") or os.path.join(ROOT, "data")
GAME_PORT, TRAINING_PORT, TELNET_PORT = 41011, 41020, 47823
POLL_SEC = 15
DRIVER_EXIT_SEC = 120          # a driver ends by itself at game over; then it is asked to stop, then stopped by PID
HEALTH_WAIT_SEC = 1800         # how long a busy machine (someone else's game, calibration) is waited for before parking


def now_tag():
    return time.strftime("%H%M%S")


def read_json(path):
    try:
        with open(path, encoding="utf-8-sig") as f:     # PowerShell 5.1 writes UTF-8 with a BOM (game-pid.json)
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")


def driver_cmd(side, seat, referee_url, token_file, run_dir, homes, phase, notice_file=None, training=False, dry=False):
    cmd = [PY, "-I", "-X", "utf8", os.path.join(ROOT, "arena", "harness", "driver.py"), "--side", str(side), "--cli", seat["cli"],
           "--referee", referee_url, "--token-file", token_file, "--run", run_dir, "--home", homes, "--player", seat["name"],
           "--kind", seat["kind"], "--phase", phase]
    if seat.get("model"):
        cmd += ["--model", seat["model"]]
    if seat.get("script"):
        cmd += ["--fake-script", seat["script"]]
    if notice_file:
        cmd += ["--notice-file", notice_file]
    if training:
        cmd += ["--turn-limit", str(rules.TRAINING_TURN_SEC), "--wait-timeout", "1" if dry else "10"]
    elif dry:
        cmd += ["--wait-timeout", "1"]
    return cmd


def start_referee(run_dir, scenario, args, port, host):
    """python -I arena/referee ...; waits for its 'on host:port;' line and the token files (p4_training's, any scenario)."""
    os.makedirs(run_dir, exist_ok=True)
    out = os.path.join(run_dir, "referee.out")
    cmd = [PY, "-I", "-X", "utf8", os.path.join(ROOT, "arena", "referee"), "--run", run_dir, "--scenario", scenario,
           "--port", str(port), "--host", host] + args
    with open(out, "ab") as f:
        proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=ROOT)
    until = time.time() + 60
    while time.time() < until and proc.poll() is None:
        try:
            with open(out, "rb") as f:
                m = re.search(r" on [^\s:]+:(\d+); tokens in", f.read().decode("utf-8", "replace"))
        except OSError:
            m = None
        if m and os.path.isfile(os.path.join(run_dir, "secrets", "admin.token")):
            return p4.Referee(proc, f"http://127.0.0.1:{m[1]}", run_dir)
        time.sleep(0.1)
    p4.stop_proc(proc)
    raise RuntimeError(f"referee did not start (see {out})")


# ---------------- the game machine ----------------
class Remote(p4.Remote):
    """The game machine through the game-host adapter (the order of tools/p3_rehearsal.sh, as p4_training), plus what
    the health check needs. Every adapter call's output is a file under <attempt>/args/."""

    def __init__(self, director):
        super().__init__(director)
        self.adir = director.run

    def _out(self, step, *args, timeout=900):
        path = os.path.join(self.adir, "args", f"{step}-{now_tag()}.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            r = subprocess.run([*self.gamehost, *args], capture_output=True, timeout=timeout, cwd=ROOT)
            rc, text = r.returncode, (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError) as e:
            rc, text = -1, f"{type(e).__name__}: {e}"
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        self.d.log("gamehost", step=step, rc=rc, out=path)
        return rc, text

    def occupancy(self):
        rc, text = self._out("occupancy", "occupancy", NODE, "--json", timeout=300)
        data = json.loads(text[text.index("{"):]) if "{" in text else {}
        if rc != 0 or not data.get("ok"):
            raise RuntimeError(f"occupancy rc {rc}")
        table = data.get("table") or data.get("snapshot") or {}
        out = []
        for e in table.get("entries") or []:
            blob = json.dumps({k: e.get(k) for k in ("name", "job", "purpose", "project", "cmdline")}, ensure_ascii=False)
            if re.search(r"fpc|flashpoint|ai-commander-arena", blob, re.I):
                out.append(f"{e.get('kind')} {e.get('name')} pid {e.get('pid')} ({e.get('purpose') or e.get('job') or ''})"[:160])
        return out

    def calibration_running(self):
        return calibration_running()

    def probe(self, last):
        args = os.path.join(self.adir, "args", "health.json")
        write_json(args, {"GamePid": int((last or {}).get("pid") or 0), "Creation": (last or {}).get("creation") or "",
                          "TelnetPort": TELNET_PORT})
        rc, text = self._out("health", "run", NODE, "--file", os.path.join(ROOT, "fpc", "remote", "health.ps1"), "--args", args,
                             "--timeout", "120")
        m = re.search(r"HEALTH (\{.*\})", text)
        if rc != 0 or not m:
            return {"foreign_games": [f"health probe failed (rc {rc})"]}
        r = json.loads(m[1])
        return {k: (v if isinstance(v, (list, bool)) else ([] if v is None else [v])) for k, v in r.items()}

    def stop_game(self, last):
        args = os.path.join(self.adir, "args", "stop-game.json")
        write_json(args, {"GamePid": int(last["pid"]), "Creation": last["creation"]})
        return self._out("stop-game", "run", NODE, "--file", os.path.join(ROOT, "fpc", "remote", "stop-game.ps1"), "--args", args,
                         "--timeout", "120")[1][-300:]

    def remove_tokens(self, names):
        out = []
        for n in names:
            if re.fullmatch(r"relay-[A-Za-z0-9._-]{1,80}\.token", n):
                path = GAME + BS + BS.join(["ai-arena", "in", n])
                out.append(self._out("token-rm", "ps", NODE, "-c", f"Remove-Item -LiteralPath '{path}' -Force")[0])
        return out

    def kill(self, exrun, name):
        return self._host(exrun, "kill", "kill", NODE, name)

    def wait(self, exrun, name, timeout=1800):
        return self._host(exrun, "wait", "wait", NODE, name, "--timeout", str(int(timeout)))


def calibration_running():
    """Is tools/s2_calibrate.sh running on this machine (the referee's calibration games hold the game machine)? Read only."""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -like '*s2_calibrate.sh*' }).ProcessId"],
                             capture_output=True, text=True, timeout=60, errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(x.strip().isdigit() and int(x) != os.getpid() for x in out.split())


class FailingRelay(p4.FakeRelay):
    """--dry-run: the fake relay, or one that fails the way a facility does: not_loaded (never syncs), relay_lost
    (silent after the setup), hang (keeps syncing the same game second until it is told to stop)."""

    def __init__(self, url, token_file, out_dir, fail=None, pid=None):
        super().__init__(url, token_file, out_dir)
        self.fail, self.halt, self.pid = fail, threading.Event(), pid

    def run(self):
        if self.fail not in ("not_loaded", "relay_lost", "hang"):
            return super().run()
        os.makedirs(self.out, exist_ok=True)
        try:
            if self.fail == "not_loaded":
                self.halt.wait(600)
            else:
                self.stop_until_f9(0, "Setup", None, self.state(0))
                while self.fail == "hang" and not self.halt.is_set():
                    if self.sync(60, "Executing", state=self.state(60) if self.seq < 3 else None).get("stop"):
                        break
                    time.sleep(0.1)
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
        with open(os.path.join(self.out, "relay.log"), "w", encoding="utf-8", newline="\n") as f:
            f.write(f"fake relay ({self.fail}) done; error: {self.error}\n")


class DryMachine:
    """--dry-run: the game machine on this computer. Records which fake game is 'alive' so the health check has
    something to check; inject {request id: failure kind} fails that game's first attempt."""

    def __init__(self, director, inject=None):
        self.d, self.inject, self.relays, self.alive, self.tokens, self.n = director, dict(inject or {}), {}, set(), set(), 0
        self.pushed = {}
        self.busy, self.calibrating = [], False

    def occupancy(self):
        return list(self.busy)

    def calibration_running(self):
        return self.calibrating

    def probe(self, last):
        return {"game_alive": bool(last) and last.get("pid") in self.alive, "foreign_games": [],
                "telnet_listeners": [last["pid"]] if last and last.get("pid") in self.alive else [], "tokens": sorted(self.tokens)}

    def stop_game(self, last):
        self.alive.discard(last.get("pid"))
        return f"stopped fake game {last.get('pid')}"

    def remove_tokens(self, names):
        for n in names:
            self.tokens.discard(n)
        return names

    def push_token(self, exrun, name):
        path = os.path.join(exrun, "secrets", "relay.token")
        self.pushed[path] = f"relay-{name}.token"
        self.tokens.add(self.pushed[path])
        return path

    def spawn(self, exrun, name, relay_args, cap_min, purpose, fail=None):
        write_json(os.path.join(exrun, "args", "relay.json"), relay_args)
        self.n += 1
        pid = 900000 + self.n
        r = self.relays[name] = FailingRelay(relay_args["Referee"], relay_args["TokenFile"], os.path.join(exrun, name), fail, pid)
        os.makedirs(r.out, exist_ok=True)
        write_json(os.path.join(r.out, "game-pid.json"), {"pid": pid, "creation": player.now_iso()})
        self.alive.add(pid)

        def go():
            r.run()
            if fail is None:          # a real relay stops its game at the end; a failed one may leave it (health cleans)
                self.alive.discard(pid)
        threading.Thread(target=go, daemon=True).start()
        return 0

    def wait(self, exrun, name, timeout=120):
        r = self.relays[name]
        end = time.time() + min(timeout, 15)       # a fake relay that is stopped ends in a second; a stuck one is killed
        while time.time() < end and not os.path.isfile(os.path.join(r.out, "relay.log")):
            time.sleep(0.1)
        return 0 if os.path.isfile(os.path.join(r.out, "relay.log")) else 75

    def kill(self, exrun, name):
        self.relays[name].halt.set()
        return 0

    def pull(self, exrun, name):
        return os.path.join(exrun, name)

    def remove_token(self, exrun, token_path):
        self.tokens.discard(self.pushed.pop(token_path, None))


# ---------------- the director ----------------
class Director:
    def __init__(self, a):
        self.a, self.run, self.dry = a, os.path.abspath(a.run), a.dry_run
        os.makedirs(self.run, exist_ok=True)
        self.homes = os.path.abspath(a.homes or (os.path.join(self.run, "season2") if self.dry or a.smoke else os.path.join(DATA, "season2")))
        self.players = []                                  # [{"name", "cli", "model"}], index = training player number
        for i, item in enumerate(x for x in a.players.split(",") if x.strip()):
            name, _, cli = item.partition("=")
            cli, _, model = cli.partition(":")
            self.players.append({"name": name.strip(), "cli": cli.strip() or "kimi", "model": model.strip() or None, "index": i})
        self.poll = a.poll_sec if a.poll_sec is not None else (0.2 if self.dry else POLL_SEC)
        self.inject = dict(x.split(":", 1) for x in (a.inject or "").split(",") if ":" in x)
        self.machine = DryMachine(self, self.inject) if self.dry else Remote(self)
        self.health = gq.Health(self.machine, TELNET_PORT, gap=0 if self.dry else gq.START_GAP_SEC, per_minute=not self.dry)
        self.health.last_game = read_json(os.path.join(DATA, "game-machine-last-game.json")) if not self.dry else None
        self.mlock = player.PidLock(os.path.join(self.run if self.dry else DATA, "game-machine.lock"), "the game machine")
        self.queue = gq.GameQueue()
        self.log_lock = threading.Lock()
        self.t0 = time.time()
        self.tref, self.tdrivers, self.training_over = None, {}, threading.Event()
        self.submitted, self.results = set(), {}
        self.stop_runner, self.aborting, self.runner_thread = threading.Event(), threading.Event(), None
        self.stop_file = os.path.join(self.run, "STOP")

    def log(self, event, **f):
        row = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "wall": round(time.time() - self.t0, 1), "event": event, **f}
        line = json.dumps(row, ensure_ascii=False, default=str)
        with self.log_lock:
            print(line, flush=True)
            try:
                with open(os.path.join(self.run, "director.jsonl"), "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    def record(self, name, row):
        with self.log_lock:
            with open(os.path.join(self.run, name), "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    # --- seats and fake models ---
    def seat(self, pl, kind="players"):
        s = {"kind": kind, "name": pl["name"], "cli": pl["cli"], "model": pl["model"]}
        if pl["cli"] == "fake":
            s["script"] = self.fake_script(pl, kind)
        return s

    def fake_script(self, pl, kind):
        """The scripted model of a dry-run (or smoke) player: training wakes ask for two small exercises, vs the AI and
        vs the sparring partner, then end; game wakes look at the summary and end the turn, the blocking turn end
        bringing the next wake in the same prompt; a cli_died injection exits once at the start of that game's first
        attempt."""
        path = os.path.join(self.run, "scripts", f"{kind}-{pl['name']}.json")
        if os.path.isfile(path):
            return path
        sc = Scenario(rules.EXERCISE_SCENARIO)
        units = {s: [r["name"] for r in sc.roster[s] if not r.get("arrives_later")][:3] for s in (0, 1)}
        rules_ = [{"match": f"({rid})", "seq": [[{"exit": 3}]] * times} for rid, (kind_, times) in self.injected().items()
                  if kind_ == "cli_died"]
        if kind == "players":
            rules_.append({"match": '"training":{', "seq": [
                [{"arena": "exercise request --json", "input": json.dumps({"type": "small", "opponent": "ai", "play_side": 0, "units": units[0]})}],
                [{"arena": "exercise request --json", "input": json.dumps({"type": "small", "opponent": "self", "play_side": 1, "units": units[1]})}]],
                "then": [{"arena": "turn summary"}, {"arena": "turn end"}]})
        # a game wake: look, end the turn and go on with what the blocking turn end returns (the next wake, in the same
        # prompt turn, as a real model would), until it answers that the game is over
        rules_.append({"match": "对局结束", "then": [{"say": "对局结束"}]})
        rules_.append({"match": "唤醒", "then": [{"arena": "turn summary"}, {"arena": "turn end", "chain": True}]})
        write_json(path, {"commander": {"rules": rules_, "default": [{"arena": "turn end"}]}})
        return path

    def injected(self):
        """--inject id:kind[*N] -> {id: (kind, N)}: the first N attempts of that game fail that way (N 3: parked)."""
        out = {}
        for rid, v in self.inject.items():
            kind, _, n = v.partition("*")
            out[rid] = (kind, int(n or 1))
        return out

    # --- one game attempt ---
    def attempt(self, req, n, failures):
        spec = req.spec
        adir = os.path.join(self.run, "games", req.id, f"a{n + 1}")
        os.makedirs(os.path.join(adir, "args"), exist_ok=True)
        if not self.dry:
            self.machine.adir = adir
        procs, t_start, relay = [], time.time(), None
        aborted = {"status": "aborted", "detail": "the director is stopping", "dir": adir}
        try:
            if self.aborting.is_set():
                return aborted
            ok, problems = self.wait_healthy(req)
            if self.aborting.is_set():
                return aborted
            if not ok:
                return {"status": "facility", "failure": {"kind": "health", "detail": json.dumps(problems, ensure_ascii=False)[:300]}}
            wait = self.health.start_wait()
            if wait:
                self.log("start_gap_wait", id=req.id, sec=round(wait, 1))
                if self.aborting.wait(wait):
                    return aborted
            sides = sorted(spec["seats"], key=int)
            args = ["--pacing", spec["pacing"], "--agents", ",".join(map(str, sides)), "--batch-prefix", f"{req.id}a{n + 1}-"]
            if spec.get("exercise"):
                args.append("--exercise")
            if spec.get("prematch_sec") is not None:
                args += ["--prematch-sec", str(spec["prematch_sec"])]
            for s, units in (spec.get("units") or {}).items():
                args += [f"--units{s}", ",".join(units)]
            for s, seat in spec["seats"].items():
                args += [f"--workspace{s}", player.Home(self.homes, seat["name"], seat["kind"]).workspace]
            ref = start_referee(adir, spec["scenario"], args, 0 if self.dry else GAME_PORT, "127.0.0.1" if self.dry else "0.0.0.0")
            procs.append(("referee", ref.proc))
            self.log("game_referee", id=req.id, attempt=n + 1, pid=ref.proc.pid, url=ref.url, args=args)
            notice_file = None
            if failures or spec.get("notice"):
                notice_file = os.path.join(adir, "notice.txt")
                with open(notice_file, "w", encoding="utf-8", newline="\n") as f:
                    f.write("\n".join(x for x in (spec.get("notice"), gq.notice_text(failures) if failures else None) if x))
            drivers = {}
            for s, seat in spec["seats"].items():
                cmd = driver_cmd(s, seat, ref.url, os.path.join(adir, "secrets", f"side{s}.token"), adir, self.homes,
                                 f"{spec['label']} ({req.id})" + (f"（重开第 {n} 次）" if n else ""), notice_file, dry=self.dry)
                out = os.path.join(adir, f"driver{s}.out")
                with open(out, "ab") as f:
                    drivers[s] = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, env=player_env(), cwd=ROOT)
                procs.append((f"driver-side{s}", drivers[s]))
                self.log("driver", id=req.id, attempt=n + 1, side=s, home=f"{seat['kind']}/{seat['name']}", cli=seat["cli"],
                         pid=drivers[s].pid, out=out)
            name = f"s2-{req.id}-a{n + 1}-{time.strftime('%m%d-%H%M%S')}"
            port = int(ref.url.rsplit(":", 1)[1])
            relay_args = {"RunName": name, "TokenFile": None, "TimedMessages": 1, "MaxMinutes": int(spec["cap_min"]) + 10,
                          "Scenario": Scenario(spec["scenario"]).name,
                          "StyleOfPlay": spec["style"], **({"Config": ""} if spec.get("vs_ai") else {}),
                          "Telnet": f"127.0.0.1:{TELNET_PORT}", "Referee": f"http://{'127.0.0.1' if self.dry else LOCAL_IP}:{port}",
                          "PassDeferSec": rules.TURN_ORDERS_SEC + 20 if spec["pacing"] == "turn" else 20}
            relay_args["TokenFile"] = self.machine.push_token(adir, name)
            relay = {"name": name, "token": relay_args["TokenFile"], "ref": ref}
            kind, times = self.injected().get(req.id, (None, 0))
            fail = kind if n < times and kind != "cli_died" else None
            purpose = f"ai-commander-arena season 2 {spec['label']} (attempt {n + 1}), one game, minimized"
            rc = self.machine.spawn(adir, name, relay_args, spec["cap_min"], purpose, fail) if self.dry else \
                self.machine.spawn(adir, name, relay_args, spec["cap_min"], purpose)
            self.log("relay_spawned", id=req.id, attempt=n + 1, name=name, rc=rc, style=relay_args["StyleOfPlay"], injected=fail)
            self.health.started(None)
            outcome = self.watch(req, ref, drivers, spec, adir) if rc == 0 else ("facility", "start_failed", f"spawn rc {rc}")
            wall = round(time.time() - t_start, 1)
            r_, relay = relay, None
            relay_dir = self.end_relay(req, adir, r_, stop=outcome[0] != "end")
            self.stop_procs(req, procs, adir)
            if outcome[0] == "abort":
                return dict(aborted, wall_sec=wall)
            if outcome[0] == "facility":
                return {"status": "facility", "failure": {"kind": outcome[1], "detail": outcome[2]}, "dir": adir, "wall_sec": wall}
            official = self.official(req, relay_dir)
            report = read_json(os.path.join(adir, "exercise-report.json"))
            return {"status": "finished", "ended_by": outcome[1], "official": official, "dir": adir, "wall_sec": wall, "report": report}
        except Exception as e:                       # a bug here is ours: the game is restarted like a facility failure
            self.log("attempt_error", id=req.id, attempt=n + 1, error=f"{type(e).__name__}: {e}")
            if relay:
                try:
                    self.end_relay(req, adir, relay, stop=True)
                except Exception as e2:
                    self.log("relay_end_error", id=req.id, error=f"{type(e2).__name__}: {e2}")
            self.stop_procs(req, procs, adir)
            return {"status": "facility", "failure": {"kind": "start_failed", "detail": f"{type(e).__name__}: {e}"}, "dir": adir}

    def end_relay(self, req, adir, relay, stop):
        """The end of a relay job, on every path (finished, facility failure, abort, our own error): the referee tells the
        relay to stop (it then stops its game), the job is waited for and killed if it does not end, its folder pulled,
        its token removed; then our game is stopped by its recorded PID + creation time in any case (stop-game.ps1 does
        nothing when it is gone): a killed job cannot stop its game, and no game of ours may outlive its job.
        -> the pulled relay folder"""
        name = relay["name"]
        if stop:
            relay["ref"].call("POST", "/admin/stop")
        rc = self.machine.wait(adir, name, 120 if stop else 1800)
        if rc != 0:
            self.log("relay_kill", id=req.id, name=name, rc=self.machine.kill(adir, name))
        relay_dir = self.machine.pull(adir, name)
        self.machine.remove_token(adir, relay["token"])
        game = read_json(os.path.join(relay_dir, "game-pid.json"))
        if game and game.get("pid") and game.get("creation"):
            self.health.last_game = game
            if not self.dry:
                write_json(os.path.join(DATA, "game-machine-last-game.json"), game)
            self.log("game_stopped", id=req.id, name=name, pid=game["pid"], out=self.machine.stop_game(game))
        else:
            self.log("game_pid_missing", id=req.id, name=name, relay_dir=relay_dir)
        return relay_dir

    def wait_healthy(self, req):
        until = time.time() + (5 if self.dry else HEALTH_WAIT_SEC)
        while True:
            ok, problems = self.health.ensure(lambda ev, **f: self.log(ev, id=req.id, **f))
            if ok or time.time() >= until or self.aborting.is_set():
                return ok, problems
            self.aborting.wait(1 if self.dry else 60)

    def watch(self, req, ref, drivers, spec, adir):
        """Until the game ends or a facility failure. -> ("end", ended_by) or ("facility", kind, detail)"""
        w = gq.Watch(load_sec=8 if self.dry else gq.GAME_LOAD_SEC, hang_sec=3 if self.dry else gq.HANG_SEC,
                     prematch_sec=spec.get("prematch_sec") or rules.PREMATCH_REAL_SEC)
        silent_max = 3 if self.dry else rules.RELAY_SILENT_SEC
        t0, stopped = time.time(), None
        while True:
            if self.aborting.is_set():
                self.log("game_abort", id=req.id)
                return ("abort", "the director is stopping")
            code, st = ref.call("GET", "/admin/state")
            r = w.step(code, st)
            if r is None and code == 200 and w.loaded and not st.get("ended") and (st.get("relay_silent_sec") or 0) > silent_max:
                r = ("facility", "relay_lost", f"the relay has been silent for {st.get('relay_silent_sec')} s")
            if r is not None:
                if r[0] == "end" and stopped and r[1] == "stopped":
                    r = ("end", stopped)
                self.log("game_end" if r[0] == "end" else "facility_failure", id=req.id, outcome=list(r), sec=(st or {}).get("sec"))
                if r[0] == "end":
                    write_json(os.path.join(adir, "admin-state.json"), st)
                return r
            for s, p in drivers.items():
                rc = p.poll()
                if rc is not None and rc != 0:
                    f = read_json(os.path.join(adir, "agents", f"side{s}", "facility.json")) or {}
                    kind = f.get("kind") or {6: "session_busy", 2: "driver_lost", 3: "driver_lost"}.get(rc, "driver_lost")
                    r = ("facility", kind, f"side {s} driver exited {rc}: {f.get('detail') or ''}"[:300])
                    self.log("facility_failure", id=req.id, outcome=list(r))
                    return r
            game_min, cap_min = spec.get("game_min") or 0, spec["cap_min"]
            if stopped is None and w.loaded and ((game_min and (st or {}).get("sec", 0) >= game_min * 60) or time.time() - t0 >= cap_min * 60):
                stopped = "game_minutes" if game_min and (st or {}).get("sec", 0) >= game_min * 60 else "wall_cap"
                self.log("game_stop", id=req.id, why=stopped, status=ref.call("POST", "/admin/stop")[0])
            time.sleep(self.poll)

    def official(self, req, relay_dir):
        shots = os.listdir(relay_dir) if os.path.isdir(relay_dir) else []
        if not self.dry and any(n.startswith("end-") and n.endswith(".bmp") for n in shots):
            with open(os.path.join(os.path.dirname(relay_dir), "endscreen.out"), "ab") as f:
                rc = subprocess.run([PY, "-X", "utf8", os.path.join(ROOT, "tools", "fpc_endscreen.py"), relay_dir],
                                    stdout=f, stderr=subprocess.STDOUT, cwd=ROOT).returncode
            self.log("endscreen", id=req.id, rc=rc, dir=relay_dir)
        return (read_json(os.path.join(relay_dir, "end-result.json")) or {}).get("result")

    def stop_procs(self, req, procs, adir):
        """Drivers end by themselves at game over; then a control stop; then their PID (and their CLIs' PIDs)."""
        until = time.time() + (15 if self.dry else DRIVER_EXIT_SEC)
        for name, p in procs:
            if name.startswith("driver") and p.poll() is None:
                while time.time() < until and p.poll() is None:
                    time.sleep(0.2)
                if p.poll() is None:
                    self.control_stop(os.path.join(adir, "agents", f"side{name[-1]}"))
                    try:
                        p.wait(30)
                    except subprocess.TimeoutExpired:
                        pass
                if p.poll() is None:
                    for pid in cli_pids(os.path.join(adir, "agents", f"side{name[-1]}", "transcript.jsonl")):
                        stop_pid(pid)
        for name, p in procs:
            self.log("stopped", id=req.id, process=name, pid=p.pid, rc=p4.stop_proc(p))
        procs.clear()

    def control_stop(self, side_dir):
        c = read_json(os.path.join(side_dir, "state", "control.json")) or {}
        try:
            with open(os.path.join(side_dir, "state", "control.token"), encoding="utf-8") as f:
                tok = f.read().strip()
        except OSError:
            return None
        code, _ = p4.http(c.get("url") or "http://127.0.0.1:9", tok, "POST", "/stop", {"by": "s2_director"}, timeout=10)
        self.log("control_stop", dir=side_dir, status=code)
        return code

    # --- the queue runner ---
    def runner(self):
        while not self.stop_runner.is_set():
            req = self.queue.next()
            if req is None:
                time.sleep(self.poll)
                continue
            self.log("game_start", id=req.id, owner=req.owner, kind=req.kind, queued_sec=req.queued_sec, label=req.spec["label"])
            try:
                self.mlock.acquire(run=self.run, game=req.id)
            except player.SessionBusy as e:
                self.log("machine_busy", id=req.id, error=str(e))
                self.queue.requeue(req)
                time.sleep(5 if self.dry else 60)
                continue
            try:
                res = self.play(req)
            finally:
                self.mlock.release()
            if res.get("status") == "aborted":
                res.setdefault("attempts", 0)
                res.setdefault("facility_failures", [])
            rec = {"id": req.id, "owner": req.owner, "kind": req.kind, "label": req.spec["label"],
                   "seats": {s: f"{x['kind']}/{x['name']}" for s, x in req.spec["seats"].items()}, "queued_sec": req.queued_sec,
                   **{k: res.get(k) for k in ("status", "ended_by", "official", "attempts", "facility_failures", "wall_sec", "dir", "detail")},
                   "charged": res.get("status") == "finished", "scenario": req.spec.get("scenario"),
                   "scorecard": self.scorecards(req, res)}
            self.record("games.jsonl", rec)
            if res.get("status") == "parked":
                self.record("parked.jsonl", rec)
            self.log("game_done", **{k: rec[k] for k in ("id", "status", "attempts", "ended_by", "official")},
                     failures=[f["kind"] for f in rec["facility_failures"] or []])
            self.results[req.id] = rec
            self.queue.finish(req, res)

    def play(self, req):
        if req.kind == "exercise":
            return self.play_exercise(req)
        return gq.play(lambda n, f: self.attempt(req, n, f), on_restart=lambda f: self.log("restart", id=req.id, failure=f[-1]))

    @staticmethod
    def scorecards(req, res):
        """materials/21 section 6: the scorecard of each player's seat of a finished full game, by side (none for a small
        experiment: part of the force for an hour does not compare with the reference games; none for the sparring
        partner's seat)."""
        sp = req.spec
        if res.get("status") != "finished" or not res.get("official") or sp.get("units"):
            return {}
        timeline = scorecard.read_timeline(res.get("dir") or "")
        out = {}
        for side, seat in sp["seats"].items():
            if seat.get("kind") != "sparring":
                c = scorecard.card(res["official"], sp["scenario"], int(side), vs_ai=bool(sp.get("vs_ai")), timeline=timeline)
                if c:
                    out[str(side)] = c
        return out

    @staticmethod
    def player_games(home):
        """The player's finished games so far, oldest first: (label, games.jsonl record, scorecard), from the runs its
        session history points at (each phase's driver notes its run). Records of games before scorecards existed get
        theirs computed here."""
        seen, out = set(), []
        for h in home.record().get("history") or []:
            run = h.get("run")
            if not run or os.path.normcase(run) in seen:
                continue
            seen.add(os.path.normcase(run))
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(run))), "games.jsonl")
            try:
                rows = [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()]
            except (OSError, ValueError):
                continue
            rec = next((r for r in reversed(rows) if os.path.normcase(str(r.get("dir"))) == os.path.normcase(run)), None)
            if not rec or not rec.get("official"):
                continue
            side = next((int(k) for k, v in (rec.get("seats") or {}).items() if v == f"players/{home.name}"), 0)
            card = (rec.get("scorecard") or {}).get(str(side))
            if not (card or {}).get("view"):
                key = rec.get("scenario") or scorecard.scenario_key((read_json(os.path.join(run, "exercise-report.json")) or {}).get("scenario"))
                card = key and scorecard.recard(card, rec["official"], key, side, vs_ai=len(rec.get("seats") or {}) == 1,
                                                timeline=scorecard.read_timeline(run))
            out.append((rec.get("label") or rec.get("id"), rec, card))
        return out

    @classmethod
    def record_text(cls, home):
        """For a phase's notice: one line per finished game, then the last game's scorecard in full. "" when none."""
        games = cls.player_games(home)
        if not games:
            return ""
        lines = ["你到目前为止的战绩（按时间）："]
        for label, rec, card in games:
            o = rec["official"]
            result, share = (card["result"], card["share"]) if card else (o.get("result"), o.get("vp_ratio_pct"))
            line = f"- {label}：{result}，份额 {share}%"
            if card:
                y = card["you"]
                line += (f"；保住部队 {y['kept_pct']}%，VP 格 {y['cells_vp']:,.0f}，给敌方造成损失 {y['inflicted_vp']:,.0f}，"
                         f"交换比 {y['exchange']}")
                if card.get("goals"):
                    line += f"；目标阶梯 {sum(g['reached'] for g in card['goals'])}/{len(card['goals'])}"
            lines.append(line)
        last = games[-1][2]
        if last:
            lines += ["", "最近一局的" + last["text"]]
        return "\n".join(lines)

    def training_notice(self, pl):
        """The first wake of the training phase: the player's record so far and how its games will be judged
        (materials/21 section 6), never what to change."""
        sc = Scenario(rules.EXERCISE_SCENARIO)
        rec = self.record_text(player.Home(self.homes, pl["name"]))
        head = ("训练接着进行（裁判的总控出了故障，换了一个）：之前的演习和用掉的训练时间照算，你起不来的那段不算。"
                if getattr(self.a, "training_carry", None) else "训练阶段开始：")
        return ((rec + "\n\n" if rec else "")
                + head + f"演习都在想定 {sc.name} 上打（正式赛也是它）。怎么判断自己打得好不好："
                "每局完整演习打完，exercise show <编号> 给这一局的成绩单——官方结果，份额在参照局（同一方原地不动、"
                "电脑指挥同一方）里的位置，保住部队、守住 VP 格、给敌方造成损失三个分项和交换比，双方离突然死亡多远，"
                "兵力走势和本方掉得最快的一小时，以及目标阶梯；exercise list 列出每局的官方结果。小实验只有部分兵力、"
                "一小时，不出成绩单。目标阶梯是对内置 AI 的；最终目标是正式赛对另一位选手：两边各打一局，比两局份额之和，"
                "差不到 2 个百分点算平局，平局比两局合计给敌方造成的损失。成绩单只陈述事实，怎么改由你自己判断。"
                "详见 doc show basics 第 10 节。"
                + (f"\n\n{self.a.training_note}" if getattr(self.a, "training_note", None) else ""))

    # --- phases ---
    def stop_requested(self):
        """<run>/STOP exists (or we are stopping already): the running game ends, nothing more starts."""
        if not self.aborting.is_set() and os.path.exists(self.stop_file):
            self.log("stop_file", path=self.stop_file)
            self.aborting.set()
        return self.aborting.is_set()

    def wait_queue(self, cap_sec):
        until = time.time() + cap_sec
        while time.time() < until and not self.queue.idle() and not self.stop_requested():
            time.sleep(self.poll)
        return self.queue.idle()

    @staticmethod
    def last_result(home, phase="起步课题"):
        """The official result of the player's last game of that phase (its label's head), from the games.jsonl of the
        run that played it (the game ends with the player's CLI closed, so the result reaches the player only in the
        next notice). "" when there is none."""
        for h in reversed(home.record().get("history") or []):
            if not str(h.get("phase") or "").startswith(phase) or not h.get("run"):
                continue
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(h["run"]))), "games.jsonl")
            try:
                rows = [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()]
            except (OSError, ValueError):
                return ""
            o = next((r.get("official") for r in reversed(rows) if os.path.normcase(str(r.get("dir"))) == os.path.normcase(h["run"])), None)
            if not o:
                return ""
            n = {k: (f"{v:,.0f}" if isinstance(v, (int, float)) else v) for k, v in o.items()}
            return (f"上一局{phase}的官方结果：{n.get('result')}，本方份额 {n.get('vp_ratio_pct')}%（开局 {n.get('starting_ratio_pct')}%）："
                    f"存活部队 {n.get('survived_vp')} VP，VP 格 {n.get('locations_vp')}，给敌方造成损失 {n.get('inflicted_vp')}。")
        return ""

    def phase_start(self):
        sc = Scenario(self.a.start_scenario)
        lv = sc.levels[0]
        # materials/18 section 4: the start task is a game the player's side should win; the pass mark is the game's own
        # level (--start-pass; materials/19 section 3: on Donau a passive West German side already gets a Tactical Win)
        level, at = {"marginal": ("Marginal Win", lv[2]), "tactical": ("Tactical Win", lv[1]),
                     "decisive": ("Decisive Win", lv[0])}[self.a.start_pass]
        notice = (f"这是起步课题：在想定 {sc.name} 里指挥 0 方（指挥部 {sc.commanders[0]}），对手是游戏内置 AI。"
                  f"这一方开局就占优；过关标准是游戏终局的官方结果达到 {level} 或更好（本方份额 ≥ {at}%，"
                  f"或敌方兵力跌破 30% 触发突然死亡时结果升一档；档线以 arena brief show / score show 为准）。"
                  + (self.a.start_note.strip() if self.a.start_note else "") + "只打这一局，打完裁判告诉你结果。")
        for pl in self.players:
            # a player who already has a session (a retry of the start task, 11-01 Kimi) is not at its start
            home = player.Home(self.homes, pl["name"])
            again = ("你的长会话接着进行：上一局的笔记和复盘都在工作区里。" + self.last_result(home)) if home.session_id else                 "这是你这个长会话的开头：之后的训练、演习和正式赛都在同一个会话里接着进行，工作区里的笔记会一直保留。"
            spec = {"label": f"起步课题 {pl['name']}", "seats": {0: self.seat(pl)}, "pacing": self.a.start_pacing, "scenario": self.a.start_scenario,
                    "notice": notice + again,
                    "vs_ai": True, "style": p4.AI_STYLE[0], "cap_min": self.a.start_cap_min,
                    "prematch_sec": 5 if self.dry else rules.EXERCISE_PREMATCH_SEC}
            self.log("submit", **self.queue.submit(pl["name"], "start", spec, rid=f"start-{pl['name']}").public())
        return self.wait_queue(3600 * 12)

    def phase_baseline(self):
        """materials/20 section 1: after the start task, one game per player on the formal scenario as side 0 against the
        game's AI (the launcher gives the AI side 1 only): no pass mark, the player's level against the computer's
        calibration games and the human tournament."""
        sc = Scenario(self.a.baseline_scenario)
        notice = (f"这是能力基准局：在想定 {sc.name} 里指挥 0 方（指挥部 {sc.commanders[0]}），对手是游戏内置 AI。"
                  "这一局不设过关线：结果用来和电脑、人类的成绩对照，定之后的训练和正式赛怎么安排。"
                  "胜负按游戏终局的官方结果，档线以 arena brief show / score show 为准。"
                  + (self.a.baseline_note.strip() if self.a.baseline_note else "") + "只打这一局，打完裁判告诉你结果。")
        for pl in self.players:
            home = player.Home(self.homes, pl["name"])
            again = ("你的长会话接着进行：笔记和复盘都在工作区里。起步课题你已过关。" + self.last_result(home)) if home.session_id else                 "这是你这个长会话的开头：之后的训练、演习和正式赛都在同一个会话里接着进行，工作区里的笔记会一直保留。"
            spec = {"label": f"能力基准 {pl['name']}", "seats": {0: self.seat(pl)}, "pacing": "turn", "scenario": self.a.baseline_scenario,
                    "notice": notice + again,
                    "vs_ai": True, "style": p4.AI_STYLE[0], "cap_min": self.a.baseline_cap_min,
                    "prematch_sec": 5 if self.dry else rules.EXERCISE_PREMATCH_SEC}
            self.log("submit", **self.queue.submit(pl["name"], "baseline", spec, rid=f"baseline-{pl['name']}").public())
        return self.wait_queue(3600 * 16)

    def phase_formal(self):
        for i, game in enumerate(x for x in self.a.formal.split(";") if x.strip()):
            seats = {}
            for part in game.split(","):
                name, _, side = part.partition(":")
                pl = next(p for p in self.players if p["name"] == name.strip())
                seats[int(side)] = self.seat(pl)
            spec = {"label": f"正式赛第 {i + 1} 局", "seats": seats, "pacing": self.a.formal_pacing, "scenario": self.a.formal_scenario,
                    "style": 3, "cap_min": self.a.formal_cap_min,
                    "prematch_sec": 5 if self.dry else (self.a.formal_prematch_min * 60 if self.a.formal_prematch_min else None)}
            self.log("submit", **self.queue.submit("referee", "formal", spec, rid=f"formal-{i + 1}").public())
        return self.wait_queue(3600 * 24)

    def phase_smoke(self):
        pl = self.players[0]
        if self.a.smoke_cli == "fake":
            pl = dict(pl, cli="fake", model=None)
        sc = Scenario(rules.EXERCISE_SCENARIO)
        units = [r["name"] for r in sc.roster[0] if not r.get("arrives_later")][:3]
        for i in range(self.a.smoke):
            spec = {"label": f"冒烟 {i + 1}/{self.a.smoke}", "seats": {0: self.seat(pl)}, "pacing": "turn", "scenario": rules.EXERCISE_SCENARIO,
                    "exercise": True, "units": {0: units}, "vs_ai": True, "style": p4.AI_STYLE[0], "game_min": self.a.smoke_minutes,
                    "cap_min": self.a.smoke_minutes * 3 + 15, "prematch_sec": 5 if self.dry else 120}
            self.queue.submit(pl["name"], "smoke", spec, rid=f"smoke-{i + 1:02d}")
        return self.wait_queue(3600 * 8)

    # training
    def start_training_driver(self, pl, notice=None):
        p = pl["index"]
        tdir = os.path.join(self.run, "training")
        notice_file = None
        if notice:
            notice_file = os.path.join(tdir, f"notice-{pl['name']}-{now_tag()}.txt")
            with open(notice_file, "w", encoding="utf-8", newline="\n") as f:
                f.write(notice)
        cmd = driver_cmd(p, self.seat(pl), self.tref.url, os.path.join(tdir, "secrets", f"side{p}.token"), tdir, self.homes, "训练",
                         notice_file, training=True, dry=self.dry)
        with open(os.path.join(tdir, f"player{p}.out"), "ab") as f:
            proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, env=player_env(), cwd=ROOT)
        self.tdrivers[p] = {"proc": proc, "stopping": False}
        self.log("training_driver", player=pl["name"], index=p, pid=proc.pid, notice=bool(notice))

    def stop_training_driver(self, p):
        d = self.tdrivers.get(p)
        if not d or d["proc"].poll() is not None:
            return
        d["stopping"] = True
        self.control_stop(os.path.join(self.run, "training", "agents", f"side{p}"))
        try:
            d["proc"].wait(60 if not self.dry else 20)
        except subprocess.TimeoutExpired:
            for pid in cli_pids(os.path.join(self.run, "training", "agents", f"side{p}", "transcript.jsonl")):
                stop_pid(pid)
        self.log("stopped", process=f"training-driver{p}", pid=d["proc"].pid, rc=p4.stop_proc(d["proc"]))

    def play_exercise(self, req):
        e, pl = req.spec["ex"], req.spec["player"]
        p = pl["index"]
        self.stop_training_driver(p)               # the player's session goes to the exercise
        code, _ = self.tref.call("POST", "/admin/exercise_update", {"id": e["id"], "status": "running"})
        self.log("exercise_running", id=e["id"], update=code)

        def restart(failures):
            code, _ = self.tref.call("POST", "/admin/exercise_update", {"id": e["id"], "status": "restart", "failure": failures[-1]})
            self.log("restart", id=req.id, failure=failures[-1], update=code)
        res = gq.play(lambda n, f: self.attempt(req, n, f), on_restart=restart)
        if res["status"] == "finished":
            report = dict(res.get("report") or {}, official=res.get("official"), id=e["id"], attempts=res["attempts"],
                          facility_failures=res["facility_failures"],
                          scorecard=self.scorecards(req, res).get(str(e["request"]["play_side"])))
            body = {"id": e["id"], "status": "done", "wall_sec": res.get("wall_sec") or 0.0, "report": report}
        elif res["status"] == "aborted":
            body = {"id": e["id"], "status": "parked", "failure": {"kind": "aborted", "detail": res.get("detail")}, "error": res.get("detail")}
        else:
            body = {"id": e["id"], "status": "parked", "failure": res["facility_failures"][-1], "error": res.get("detail")}
        code, r = self.tref.call("POST", "/admin/exercise_update", body)
        self.log("exercise_done", id=e["id"], status=body["status"], update=code)
        if not self.training_over.is_set():
            self.start_training_driver(pl)
        return res

    def exercise_spec(self, e, pl):
        rq = e["request"]
        side, small = rq["play_side"], rq["type"] == "small"
        seats = {side: self.seat(pl)}
        if rq["opponent"] == "self":
            seats[1 - side] = self.seat(pl, "sparring")
        quota = self.quota.get(str(pl["index"])) or {}
        return {"label": f"演习 {e['id']}（{'小实验' if small else '完整'}，对{'内置 AI' if rq['opponent'] == 'ai' else '陪练'}）",
                "seats": seats, "pacing": rq["pacing"], "scenario": rules.EXERCISE_SCENARIO, "exercise": True,
                "units": {side: rq["units"]} if small else {}, "vs_ai": rq["opponent"] == "ai",
                "style": 3 if rq["opponent"] == "self" else p4.AI_STYLE[side],
                "game_min": rules.EXERCISE_SMALL_GAME_MIN if small else 0,
                "cap_min": (quota.get("small_wall_left_sec", rules.EXERCISE_SMALL_WALL_SEC) / 60) if small else rules.EXERCISE_FULL_WALL_MIN,
                "prematch_sec": 5 if self.dry else rules.EXERCISE_PREMATCH_SEC, "ex": e, "player": pl}

    def phase_training(self):
        tdir = os.path.join(self.run, "training")
        args = ["--mode", "training", "--limit-sec", str(self.a.hours * 3600), "--full-max", str(self.a.full_max),
                "--small-sec", str(self.a.small_sec), "--self-full-min", str(self.a.self_full_min)]
        for pl in self.players:
            args += [f"--workspace{pl['index']}", player.Home(self.homes, pl["name"]).workspace]
        if self.a.training_carry:
            args += ["--carry-file", self.a.training_carry]
        self.tref = start_referee(tdir, rules.EXERCISE_SCENARIO, args, 0 if self.dry else TRAINING_PORT, "127.0.0.1")
        self.log("training_referee", pid=self.tref.proc.pid, url=self.tref.url, args=args)
        self.quota = {}
        for pl in self.players:
            self.start_training_driver(pl, self.training_notice(pl))
        # each player's clock is the training referee's (queue time off); this is only the safety net on wall time
        deadline, why, idle = time.time() + self.a.hours * 3600 * max(1, len(self.players)) + 3600, "hours", 0
        try:
            while time.time() < deadline:
                if self.stop_requested():
                    why = "stopped"
                    break
                code, st = self.tref.call("GET", "/admin/exercises")
                if code != 200:
                    self.log("poll_error", status=code)
                    time.sleep(self.poll)
                    continue
                self.quota = st.get("quota") or {}
                for e in st["exercises"]:
                    if e["status"] == "queued" and e["id"] not in self.submitted:
                        self.submitted.add(e["id"])
                        pl = self.players[e["player"]]
                        r = self.queue.submit(pl["name"], "exercise", self.exercise_spec(e, pl), rid=f"ex-{e['id']}")
                        self.log("submit", **r.public())
                for p, d in list(self.tdrivers.items()):   # a training driver that died of a facility failure: again
                    rc = d["proc"].poll()
                    if rc == 4 and not d["stopping"]:
                        f = read_json(os.path.join(tdir, "agents", f"side{p}", "facility.json")) or {}
                        self.log("training_facility", player=p, failure=f)
                        os.replace(os.path.join(tdir, "agents", f"side{p}", "facility.json"),
                                   os.path.join(tdir, "agents", f"side{p}", f"facility-{now_tag()}.json"))
                        self.start_training_driver(self.players[p], gq.notice_text([f]).replace("这一局已经从头重开", "训练已经接着进行"))
                players = st.get("players") or {}
                done = all(self.player_done(st, players.get(str(pl["index"])) or {}, pl["index"]) for pl in self.players)
                idle = idle + 1 if done else 0
                if idle >= 2:
                    why = "players_finished"
                    break
                time.sleep(self.poll)
        except KeyboardInterrupt:
            why = "interrupted"
            self.aborting.set()                     # the game running ends (its game stopped), queued ones are not played
        self.training_over.set()
        self.log("training_over", why=why)
        self.wait_queue(3600 * 3 if not self.dry else 120)
        code, _ = self.tref.call("POST", "/admin/stop")
        for p in list(self.tdrivers):
            d = self.tdrivers[p]
            try:
                d["proc"].wait(30 if self.dry else 120)
            except subprocess.TimeoutExpired:
                self.stop_training_driver(p)
            self.log("stopped", process=f"training-driver{p}", pid=d["proc"].pid, rc=p4.stop_proc(d["proc"]))
        self.log("stopped", process="training-referee", pid=self.tref.proc.pid, rc=p4.stop_proc(self.tref.proc),
                 record=os.path.join(tdir, "exercises.json"))

    def player_done(self, st, s, p):
        """Player p has had a turn, has none open or due, and nothing of its is queued or running (or its clock is out)."""
        mine = [e for e in st["exercises"] if e["player"] == p]
        return s.get("turns", 0) > 0 and not s.get("active") and not s.get("wake_pending") and \
            not any(e["status"] in ("queued", "running") for e in mine) and not self.queue.waiting(self.players[p]["name"]) and \
            (self.queue.running is None or self.queue.running.owner != self.players[p]["name"])

    def main(self):
        self.log("start", run=self.run, homes=self.homes, players=self.players, phases=self.a.phases, dry_run=self.dry,
                 smoke=self.a.smoke, inject=self.inject, poll_sec=self.poll)
        self.runner_thread = threading.Thread(target=self.runner, name="queue", daemon=True)
        self.runner_thread.start()
        try:
            if self.a.smoke:
                self.phase_smoke()
            else:
                for ph in [x.strip() for x in self.a.phases.split(",") if x.strip()]:
                    if self.aborting.is_set():
                        break
                    self.log("phase", phase=ph)
                    {"start": self.phase_start, "baseline": self.phase_baseline, "training": self.phase_training,
                     "formal": self.phase_formal}[ph]()
        except KeyboardInterrupt:
            self.log("interrupted")
            self.aborting.set()
        finally:
            # stopping (done, interrupted or our error): the game running, if any, ends through end_relay (its relay
            # job and its game stopped) before the machine lock is let go; queued games are not started
            self.stop_runner.set()
            self.aborting.set()
            try:
                self.runner_thread.join(120 if self.dry else 900)     # end_relay + stop_procs of the game running
            except KeyboardInterrupt:
                self.log("interrupted_again", running=getattr(self.queue.running, "id", None))
            self.mlock.release()
            left = [r.id for r in self.queue.waiting()]
            if left:
                self.log("not_played", ids=left)
        sessions = {}
        for pl in self.players:
            for kind in player.KINDS:
                h = player.Home(self.homes, pl["name"], kind)
                rec = h.record()
                if rec:
                    sessions[f"{kind}/{pl['name']}"] = {"session_id": rec.get("session_id"), "path": h.session_path,
                                                         "ops": [x["op"] for x in rec.get("history") or []]}
        games = list(self.results.values())
        self.log("end", games=len(games), finished=sum(g["status"] == "finished" for g in games),
                 parked=sum(g["status"] == "parked" for g in games), aborted=sum(g["status"] == "aborted" for g in games),
                 restarted=sum(1 for g in games if g["facility_failures"]),
                 sessions=sessions, record=os.path.join(self.run, "games.jsonl"))
        return 0 if all(g["status"] == "finished" for g in games) else 1


def player_env():
    return {k: v for k, v in os.environ.items() if k not in p4.NO_ENV}


def cli_pids(transcript):
    """CLI processes a driver started and did not see closed (from its transcript), for a driver stopped by PID."""
    started, closed = set(), set()
    try:
        with open(transcript, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("type") == "cli_start":
                    started.add(r.get("pid"))
                elif r.get("type") == "cli_closed":
                    closed.add(r.get("pid"))
    except OSError:
        pass
    return sorted(p for p in started - closed if p)


def stop_pid(pid):
    """End a CLI process a driver started and its children (its MCP server), one exact PID at a time."""
    for x in acp.child_pids(pid) + [pid]:
        try:
            os.kill(int(x), 15)             # Windows: TerminateProcess on exactly this PID
        except OSError:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", required=True, help="run directory, e.g. $ARENA_DATA/runs/s2-<what>-<HHMMSS>")
    ap.add_argument("--homes", help=f"players' homes (default {DATA}/season2; dry run and smoke: <run>/season2)")
    ap.add_argument("--players", default="A=kimi,B=opencode", help="name=cli[:model],... in training-player order")
    ap.add_argument("--phases", default="start,training,formal")
    ap.add_argument("--dry-run", action="store_true", help="no game machine: fake relay, scripted fake players")
    ap.add_argument("--inject", help="dry run: <game id>:<not_loaded|hang|relay_lost|cli_died>[*N],... fails that game's first "
                                     "(N) attempts")
    ap.add_argument("--smoke", type=int, default=0, help="N short exercises in a row vs the AI (the real-machine smoke)")
    ap.add_argument("--smoke-minutes", type=int, default=10, help="game minutes per smoke exercise")
    ap.add_argument("--smoke-cli", choices=("fake", "real"), default="fake", help="fake: scripted model, no provider calls")
    ap.add_argument("--hours", type=float, default=rules.TRAINING_REAL_SEC / 3600, help="training clock per player")
    ap.add_argument("--full-max", type=int, default=rules.EXERCISE_FULL_MAX)
    ap.add_argument("--small-sec", type=float, default=rules.EXERCISE_SMALL_WALL_SEC)
    ap.add_argument("--self-full-min", type=int, default=0, help="full exercises per player that must be vs the sparring partner")
    ap.add_argument("--training-note", help="one more paragraph for the training notice (e.g. the match scenario changed)")
    ap.add_argument("--training-carry", help="carry the training on from an earlier director's: JSON with each player's "
                                             "clock so far and its finished exercises (the training referee's --carry-file)")
    ap.add_argument("--start-scenario", default=rules.EXERCISE_SCENARIO)
    ap.add_argument("--start-pacing", default="turn", choices=("turn", "formal"))
    ap.add_argument("--formal-pacing", default="turn", choices=("turn", "formal"),
                    help="formal games: turn = the game's own orders cycle (default since round 5), formal = real time")
    ap.add_argument("--start-cap-min", type=float, default=300)
    ap.add_argument("--start-pass", choices=("marginal", "tactical", "decisive"), default="marginal",
                    help="the start task's pass mark: the game's official result at this win level or better")
    ap.add_argument("--start-note", help="one more sentence for the start task's notice (e.g. the calibration baselines)")
    ap.add_argument("--baseline-scenario", default=rules.EXERCISE_SCENARIO)
    ap.add_argument("--baseline-cap-min", type=float, default=420)
    ap.add_argument("--baseline-note", help="one more sentence for the baseline game's notice (e.g. the calibration games)")
    ap.add_argument("--formal", default="A:0,B:1", help='formal games, ";" between games: "A:0,B:1;B:0,A:1"')
    ap.add_argument("--formal-scenario", default=rules.EXERCISE_SCENARIO)
    ap.add_argument("--formal-cap-min", type=float, default=420)
    ap.add_argument("--formal-prematch-min", type=float, help="deployment time of a formal game (default the rules' 120 min; "
                    "a shorter one for experiments)")
    ap.add_argument("--poll-sec", type=float, help=f"poll interval (default {POLL_SEC}; dry run 0.2)")
    a = ap.parse_args(argv)
    if a.dry_run:
        a.players = ",".join(f"{x.partition('=')[0]}=fake" for x in a.players.split(","))
    elif not p4.GAME:
        ap.error("set FPC_GAME_ROOT to the game folder on the game machine (README)")
    if hasattr(signal, "SIGBREAK"):            # Ctrl-Break (a console without Ctrl-C) stops the same way as Ctrl-C
        signal.signal(signal.SIGBREAK, _interrupt)
    return Director(a).main()


def _interrupt(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    sys.exit(main())
