"""The season 2 director end to end in its dry run (tools/s2_director.py --dry-run): fake relay, fake ACP CLI players.

SeasonDryRun: start task -> training with two exercises per player (vs the AI, vs the sparring partner) -> formal
game, one injected facility failure (relay lost) that is restarted, not charged and announced; every player keeps one
session id throughout. FailureKinds: the smoke path with each failure kind injected once (restarted) and one game
failing three times (parked); every attempt, finished or failed, stopped its own game by its recorded PID.
StopFile: <run>/STOP while a game runs ends that game (its relay and game stopped), the queued games are not played,
nothing is left running or locked. ReadJson: game-pid.json from PowerShell 5.1 (UTF-8 with a BOM) is read."""
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


def run_director(run_dir, *args, timeout=400):
    return subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(ROOT, "tools", "s2_director.py"), "--run", run_dir,
                           "--dry-run", "--poll-sec", "0.1", *args], capture_output=True, timeout=timeout, cwd=ROOT)


def jl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def prompts(transcript):
    return [r["msg"]["params"]["prompt"][0]["text"] for r in jl(transcript) if r["type"] == "acp" and r["dir"] == "out"
            and r["msg"].get("method") == "session/prompt"]


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class SeasonDryRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.rdir = os.path.join(cls.tmp.name, "s2")
        cls.proc = run_director(cls.rdir, "--inject", "ex-p1-2:relay_lost")
        cls.rows = jl(os.path.join(cls.rdir, "director.jsonl"))
        cls.games = {g["id"]: g for g in jl(os.path.join(cls.rdir, "games.jsonl"))}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ev(self, name):
        return [r for r in self.rows if r["event"] == name]

    def session(self, kind, name):
        with open(os.path.join(self.rdir, "season2", kind, name, "session.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_every_game_ended_with_a_record_in_fair_order(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stdout[-3000:])
        self.assertEqual([r["id"] for r in self.ev("game_start")],
                         ["start-A", "start-B", "ex-p0-1", "ex-p1-1", "ex-p0-2", "ex-p1-2", "formal-1"])
        self.assertEqual({g["status"] for g in self.games.values()}, {"finished"})
        self.assertEqual({g["ended_by"] for g in self.games.values()}, {"game_end"})
        self.assertTrue(all(g["official"]["result"] == "Contested Result" for g in self.games.values()))
        self.assertEqual(self.rows[-1]["event"], "end")
        self.assertFalse(os.path.exists(os.path.join(self.rdir, "parked.jsonl")))

    def test_one_session_per_player_for_the_whole_season(self):
        for name in ("A", "B"):
            rec = self.session("players", name)
            ops = [(h["op"], h["phase"]) for h in rec["history"]]
            self.assertEqual(ops[0][0], "new")
            self.assertTrue(all(op == "resume" for op, _ in ops[1:]), ops)              # no history replay
            self.assertEqual({h["session_id"] for h in rec["history"]}, {rec["session_id"]})
            phases = " ".join(p for _, p in ops)
            for word in ("起步课题", "训练", "演习", "正式赛"):
                self.assertIn(word, phases)
            self.assertEqual(len({h["cli_pid"] for h in rec["history"]}), len(rec["history"]))   # a fresh process each time
        self.assertNotEqual(self.session("players", "A")["session_id"], self.session("players", "B")["session_id"])

    def test_sparring_partner_is_separate(self):
        for name in ("A", "B"):
            sp, me = self.session("sparring", name), self.session("players", name)
            self.assertNotEqual(sp["session_id"], me["session_id"])
            self.assertNotEqual(sp["cwd"], me["cwd"])
        ref = next(r for r in self.ev("game_referee") if r["id"] == "ex-p0-2")["args"]
        ws0, ws1 = ref[ref.index("--workspace0") + 1], ref[ref.index("--workspace1") + 1]
        self.assertTrue(ws0.endswith(os.path.join("sparring", "A", "workspace")), ws0)
        self.assertTrue(ws1.endswith(os.path.join("players", "A", "workspace")), ws1)
        homes = {r["home"] for r in self.ev("driver") if r["id"] == "ex-p0-2"}
        self.assertEqual(homes, {"players/A", "sparring/A"})

    def test_the_injected_failure_was_restarted_free_and_announced(self):
        g = self.games["ex-p1-2"]
        self.assertEqual((g["attempts"], [f["kind"] for f in g["facility_failures"]], g["charged"]), (2, ["relay_lost"], True))
        with open(os.path.join(self.rdir, "training", "exercises.json"), encoding="utf-8") as f:
            ex = {e["id"]: e for e in json.load(f)["exercises"]}
        self.assertEqual([f["kind"] for f in ex["p1-2"]["facility_failures"]], ["relay_lost"])
        self.assertLess(ex["p1-2"]["wall_sec"], 30)                          # only the restarted game's time
        unch = [r for r in jl(os.path.join(self.rdir, "training", "logs", "exercises.jsonl")) if r.get("event") == "uncharged"]
        self.assertTrue(any(r["id"] == "p1-2" and r["why"] == "facility_failure" for r in unch))
        self.assertEqual({r["id"] for r in unch if r["why"] == "queued"}, {"p0-1", "p0-2", "p1-1", "p1-2"})
        for side in (0, 1):                                                  # both seats of the restart hear why
            first = prompts(os.path.join(self.rdir, "games", "ex-p1-2", "a2", "agents", f"side{side}", "transcript.jsonl"))[0]
            self.assertIn("[裁判通知]", first)
            self.assertIn("中继失联", first)
            self.assertIn("从头重开", first)
        later = [p for p in prompts(os.path.join(self.rdir, "training", "agents", "side1", "transcript.jsonl")) if "p1-2" in p]
        self.assertTrue(any("设施故障" in p and "不算你的" in p for p in later))

    def test_every_process_started_is_stopped_by_its_pid(self):
        started = {r["pid"] for r in self.rows if r["event"] in ("game_referee", "driver", "training_referee", "training_driver")}
        stopped = {r["pid"] for r in self.ev("stopped")}
        self.assertLessEqual(started, stopped)
        for f in glob.glob(os.path.join(self.rdir, "season2", "*", "*", "session.lock")):
            self.fail(f"a session lock was left: {f}")


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class FailureKinds(unittest.TestCase):
    def test_each_kind_restarts_and_three_failures_park(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            run = os.path.join(tmp, "smoke")
            p = run_director(run, "--smoke", "4", "--players", "A=fake",
                             "--inject", "smoke-01:not_loaded,smoke-02:hang,smoke-03:cli_died,smoke-04:relay_lost*3")
            games = {g["id"]: g for g in jl(os.path.join(run, "games.jsonl"))}
            self.assertEqual(p.returncode, 1, p.stdout[-2000:])              # one game parked
            for gid, kind in (("smoke-01", "not_loaded"), ("smoke-02", "hang"), ("smoke-03", "cli_died")):
                g = games[gid]
                self.assertEqual((g["status"], g["attempts"], [f["kind"] for f in g["facility_failures"]]), ("finished", 2, [kind]), g)
            parked = jl(os.path.join(run, "parked.jsonl"))
            self.assertEqual([(g["id"], g["status"], g["attempts"]) for g in parked], [("smoke-04", "parked", 3)])
            self.assertFalse(games["smoke-04"]["charged"])
            rec = json.load(open(os.path.join(run, "season2", "players", "A", "session.json"), encoding="utf-8"))
            self.assertEqual({h["session_id"] for h in rec["history"]}, {rec["session_id"]})
            # every attempt (a failed one leaves its game: the fake relay does not stop it) stopped its own game by PID
            rows = jl(os.path.join(run, "director.jsonl"))
            spawned = [r["id"] for r in rows if r["event"] == "relay_spawned"]
            self.assertEqual(len(spawned), 3 + 2 + 2 + 2)
            self.assertEqual(sorted(r["id"] for r in rows if r["event"] == "game_stopped"), sorted(spawned))
            self.assertFalse([r for r in rows if r["event"] == "health_clean"])      # so nothing was left for the check


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class StopFile(unittest.TestCase):
    def test_stop_ends_the_running_game_and_starts_no_other(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            run = os.path.join(tmp, "stop")
            log = os.path.join(run, "director.jsonl")
            p = subprocess.Popen([sys.executable, "-I", "-X", "utf8", os.path.join(ROOT, "tools", "s2_director.py"), "--run", run,
                                  "--dry-run", "--poll-sec", "0.1", "--smoke", "3", "--players", "A=fake", "--inject", "smoke-01:not_loaded"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT)
            try:
                until = time.time() + 120          # smoke-01 never loads (the dry run gives up after 8 s): stop it before
                while time.time() < until and not (os.path.isfile(log) and any(r["event"] == "relay_spawned" for r in jl(log))):
                    time.sleep(0.1)
                open(os.path.join(run, "STOP"), "w").close()
                self.assertEqual(p.wait(120), 1)
            finally:
                if p.poll() is None:
                    p.kill()
            rows = jl(log)
            ev = [r["event"] for r in rows]
            self.assertIn("stop_file", ev)
            self.assertIn("game_abort", ev)
            self.assertNotIn("facility_failure", ev)                       # stopped before the load timeout
            self.assertEqual([r["id"] for r in rows if r["event"] == "game_stopped"], ["smoke-01"])
            games = jl(os.path.join(run, "games.jsonl"))
            self.assertEqual([(g["id"], g["status"], g["charged"]) for g in games], [("smoke-01", "aborted", False)])
            self.assertEqual(next(r for r in rows if r["event"] == "not_played")["ids"], ["smoke-02", "smoke-03"])
            started = {r["pid"] for r in rows if r["event"] in ("game_referee", "driver")}
            self.assertLessEqual(started, {r["pid"] for r in rows if r["event"] == "stopped"})
            self.assertFalse(os.path.exists(os.path.join(run, "game-machine.lock")))
            self.assertFalse(glob.glob(os.path.join(run, "season2", "*", "*", "session.lock")))


class RemoteProbe(unittest.TestCase):
    """The real machine's probe (fpc/remote/health.ps1 through the game-host adapter), its output parsed: any game process on
    the game machine that is not our recorded one, or a probe that failed, refuses the start."""

    def machine(self, rc, text):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import s2_director
        m = object.__new__(s2_director.Remote)
        m.adir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(m.adir, ignore_errors=True))
        m._out = lambda step, *a, **k: (rc, text)
        return s2_director.gq.Health(m, s2_director.TELNET_PORT), m

    def check(self, rc, text, last=None):
        h, m = self.machine(rc, text)
        m.occupancy, m.calibration_running = (lambda: []), (lambda: False)
        h.last_game = last
        return h.check()

    def test_a_foreign_game_process(self):
        problems, fixable = self.check(0, 'x\nHEALTH {"game_alive":false,"foreign_games":7311,"telnet_listeners":[],"tokens":[]}\n')
        self.assertEqual([p["kind"] for p in problems], ["busy"])          # PS 5.1 may print a one-element list bare
        self.assertIn("7311", problems[0]["detail"])

    def test_the_probe_failed(self):
        problems, _ = self.check(1, "gamehost: node unreachable")
        self.assertEqual([p["kind"] for p in problems], ["busy"])

    def test_only_our_recorded_game(self):
        last = {"pid": 4242, "creation": "2026-11-01T00:29:00.0000000+08:00"}
        problems, fixable = self.check(0, 'HEALTH {"game_alive":true,"foreign_games":[],"telnet_listeners":[4242],"tokens":null}', last)
        self.assertEqual((problems, [f["kind"] for f in fixable]), ([], ["our_game_alive"]))


class ReadJson(unittest.TestCase):
    def test_powershell_utf8_bom(self):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import s2_director
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "game-pid.json")
            with open(path, "wb") as f:
                f.write(b"\xef\xbb\xbf" + json.dumps({"pid": 4242, "creation": "2026-11-01T00:29:00.0000000+08:00"}).encode())
            self.assertEqual(s2_director.read_json(path)["pid"], 4242)


class StartRetry(unittest.TestCase):
    def test_a_retry_notice_gives_the_last_start_result(self):
        # 11-01: the start game ends with the player's CLI closed, so Kimi never heard its official result
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import s2_director
        from arena.harness import player
        with tempfile.TemporaryDirectory() as tmp:
            home = player.Home(os.path.join(tmp, "season2"), "A")
            self.assertEqual(s2_director.Director.last_result(home), "")
            attempt = os.path.join(tmp, "runs", "s2-start-1", "games", "start-A", "a1")
            home.note_open("new", "ses-1", "kimi", "m", 1, phase="起步课题 A (start-A)", run=attempt)
            os.makedirs(attempt)
            with open(os.path.join(tmp, "runs", "s2-start-1", "games.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"id": "start-A", "dir": attempt, "official": {
                    "result": "Contested Result", "vp_ratio_pct": 51.0, "starting_ratio_pct": 56.0, "survived_vp": 6099.0,
                    "locations_vp": 2500.0, "inflicted_vp": 8370.0}}) + "\n")
            text = s2_director.Director.last_result(home)
            self.assertIn("Contested Result", text)
            self.assertIn("51%", text)
            self.assertIn("6,099", text)

    @unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)  # the record's scorecard reads the scenario
    def test_the_training_notice_gives_the_record_and_the_last_scorecard(self):
        # materials/21 section 6: every finished game of the player, oldest first, then the last one's scorecard
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import s2_director
        from arena.harness import player
        with tempfile.TemporaryDirectory() as tmp:
            home = player.Home(os.path.join(tmp, "season2"), "A")
            self.assertEqual(s2_director.Director.record_text(home), "")
            attempt = os.path.join(tmp, "runs", "s2-baseline-1", "games", "baseline-A", "a1")
            os.makedirs(attempt)
            home.note_open("new", "ses-1", "kimi", "m", 1, phase="能力基准 A (baseline-A)", run=attempt)
            home.note_open("resume", "ses-1", "kimi", "m", 1, phase="训练", run=os.path.join(tmp, "runs", "s2-train", "training"))
            with open(os.path.join(tmp, "runs", "s2-baseline-1", "games.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"id": "baseline-A", "label": "能力基准 A", "dir": attempt, "seats": {"0": "players/A"},
                                    "scenario": "the-armored-fist", "official": {
                                        "result": "Decisive Loss", "vp_ratio_pct": 28.0, "starting_ratio_pct": 56.0,
                                        "survived_vp": 12531.0, "locations_vp": 0.0, "inflicted_vp": 13906.0}}) + "\n")
            text = s2_director.Director.record_text(home)
            self.assertIn("能力基准 A：Decisive Loss，份额 28.0%；保住部队 29.6%", text)
            self.assertIn("目标阶梯 0/5", text)
            self.assertIn("最近一局的成绩单：The Armored Fist", text)

    @unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)  # the record's scorecard reads the scenario
    def test_the_record_of_side_1_is_its_own_not_the_end_screens(self):
        # a game with two seats shows side 0's end screen; a card stored before views took it as side 1's
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import s2_director
        from arena.harness import player
        with tempfile.TemporaryDirectory() as tmp:
            home = player.Home(os.path.join(tmp, "season2"), "B")
            attempt = os.path.join(tmp, "runs", "s2-training-1", "games", "ex-p1-1", "a1")
            os.makedirs(attempt)
            home.note_open("new", "ses-1", "opencode", "m", 1, phase="演习 p1-1", run=attempt)
            with open(os.path.join(tmp, "runs", "s2-training-1", "games.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"id": "ex-p1-1", "label": "演习 p1-1", "dir": attempt,
                                    "seats": {"0": "sparring/B", "1": "players/B"}, "scenario": "the-armored-fist",
                                    "scorecard": {"1": {"side": 1, "share": 50.0, "result": "Tactical Win", "text": "旧"}},
                                    "official": {"commander": "1st Bde", "result": "Tactical Win", "vp_ratio_pct": 50.0,
                                                 "survived_vp": 37202.0, "locations_vp": 500.0, "inflicted_vp": 7366.0}}) + "\n")
            text = s2_director.Director.record_text(home)
            self.assertIn("演习 p1-1：Tactical Loss，份额 50.0%；保住部队 81.1%，VP 格 6,000", text)
            self.assertIn("只读到 0 方视角的结算页", text)


if __name__ == "__main__":
    unittest.main()
