"""The P4 training director end to end in its dry run (tools/p4_training.py --dry-run): the training referee and two
scripted players, one small experiment against the AI and one full exercise against itself, each played by its own
exercise referee and a fake relay, reported back with the official result, every process stopped by its PID."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from arena.referee import rules  # noqa: E402
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class DirectorDryRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.run_dir = os.path.join(cls.tmp.name, "p4")
        cls.proc = subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(ROOT, "tools", "p4_training.py"),
                                   "--run", cls.run_dir, "--dry-run", "--hours", "0.05", "--poll-sec", "0.1"],
                                  capture_output=True, timeout=150, cwd=ROOT)
        with open(os.path.join(cls.run_dir, "director.jsonl"), encoding="utf-8") as f:
            cls.rows = [json.loads(line) for line in f if line.strip()]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def events(self, name):
        return [r for r in self.rows if r["event"] == name]

    def report(self, eid):
        with open(os.path.join(self.run_dir, "ex", eid, "exercise-report.json"), encoding="utf-8") as f:
            return json.load(f)

    def relay_args(self, eid):
        with open(os.path.join(self.run_dir, "ex", eid, "args", "relay.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_both_exercises_ran_and_the_training_ended_by_itself(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stdout[-3000:])
        done = {r["id"]: r for r in self.events("exercise_done")}
        self.assertEqual(set(done), {"p0-1", "p1-1"})
        for r in done.values():
            self.assertEqual((r["status"], r["error"], r["update"], r["ended_by"]), ("done", None, 200, "game_end"))
        self.assertEqual(self.events("training_over")[0]["why"], "dry_run_done")
        self.assertEqual(self.rows[-1]["event"], "end")

    def test_the_record_holds_both_results_and_the_quota_used(self):
        with open(os.path.join(self.run_dir, "exercises.json"), encoding="utf-8") as f:
            rec = json.load(f)
        self.assertTrue(rec["ended"])
        by = {e["id"]: e for e in rec["exercises"]}
        self.assertEqual({e["status"] for e in by.values()}, {"done"})
        self.assertEqual(rec["quota"]["1"]["full_used"], 1)
        self.assertGreater(rec["quota"]["0"]["small_wall_used_sec"], 0)
        self.assertEqual(rec["quota"]["0"]["full_used"], 0)
        self.assertEqual(by["p0-1"]["report"]["official"]["result"], "Contested Result")

    def test_small_experiment_against_the_ai(self):
        rep = self.report("p0-1")
        self.assertEqual((rep["id"], rep["players"], rep["pacing"], rep["ended_by"]), ("p0-1", [0], "turn", "game_end"))
        self.assertEqual(len(rep["experiment_units"]["0"]), 3)
        self.assertEqual(rep["official"], {"vp_share_pct": 50.0, "result": "Contested Result", "dry_run": True})
        self.assertIn("vp_cells", rep)
        self.assertEqual(set(rep["losses"]), {"0", "1"})
        args = self.relay_args("p0-1")
        self.assertEqual((args["StyleOfPlay"], args["Telnet"], args["PassDeferSec"]), (0, "127.0.0.1:47823", rules.TURN_ORDERS_SEC + 20))
        self.assertEqual(args["Config"], "")
        self.assertEqual(args["MaxMinutes"], 190)               # cap = the small time left (3 h) + 10
        ref = [r for r in self.events("exercise_referee") if r["id"] == "p0-1"][0]
        a = ref["args"]
        self.assertEqual(a[a.index("--agents") + 1], "0")
        self.assertEqual(a[a.index("--batch-prefix") + 1], "l0-")
        self.assertIn("--units0", a)
        self.assertNotIn("--workspace1", a)
        self.assertTrue(a[a.index("--workspace0") + 1].endswith(os.path.join("workspace", "player0")))

    def test_full_exercise_against_itself(self):
        rep = self.report("p1-1")
        self.assertEqual((rep["players"], rep["experiment_units"]), ([0, 1], {}))
        args = self.relay_args("p1-1")
        self.assertEqual((args["StyleOfPlay"], args["Telnet"], args["MaxMinutes"]), (3, "127.0.0.1:47824", rules.EXERCISE_FULL_WALL_MIN + 10))
        self.assertNotIn("Config", args)
        ref = [r for r in self.events("exercise_referee") if r["id"] == "p1-1"][0]["args"]
        ws = os.path.join("workspace", "player1")
        self.assertTrue(ref[ref.index("--workspace0") + 1].endswith(ws) and ref[ref.index("--workspace1") + 1].endswith(ws))
        self.assertEqual(len([r for r in self.events("exercise_player") if r["id"] == "p1-1"]), 2)

    def test_every_process_started_is_stopped_by_its_pid(self):
        started = {r["pid"] for r in self.rows if r["event"] in
                   ("training_referee", "training_player", "exercise_referee", "exercise_player")}
        stopped = {r["pid"] for r in self.events("stopped")}
        self.assertEqual(len(started), 8)                         # 1 + 2 training, 1 + 1 and 1 + 2 exercise
        self.assertLessEqual(started, stopped)


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class SupplementDryRun(unittest.TestCase):
    """A supplementary session: one player, its own quotas, the ruling's text, the training's workspaces."""
    def test_only_that_player_with_its_quota_and_workspace(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            run, ws, note = os.path.join(tmp, "sup"), os.path.join(tmp, "train", "workspace"), os.path.join(tmp, "ruling.txt")
            with open(note, "w", encoding="utf-8") as f:
                f.write("补训说明")
            subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(ROOT, "tools", "p4_training.py"),
                            "--run", run, "--dry-run", "--hours", "0.05", "--poll-sec", "0.1", "--players", "1",
                            "--full-max", "1", "--small-sec", "0", "--supplement-file", note, "--workspace-root", ws],
                           capture_output=True, timeout=150, cwd=ROOT)
            with open(os.path.join(run, "director.jsonl"), encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
            self.assertEqual([r["player"] for r in rows if r["event"] == "training_player"], [1])
            self.assertEqual([r["why"] for r in rows if r["event"] == "training_over"], ["dry_run_done"])
            self.assertEqual([(r["id"], r["status"]) for r in rows if r["event"] == "exercise_done"], [("p1-1", "done")])
            ref_args = [r for r in rows if r["event"] == "exercise_referee"][0]["args"]
            self.assertEqual(ref_args[ref_args.index("--workspace1") + 1], os.path.join(ws, "player1"))
            with open(os.path.join(run, "exercises.json"), encoding="utf-8") as f:
                q = json.load(f)["quota"]["1"]
            self.assertEqual((q["full_used"], q["full_max"], q["small_wall_max_sec"]), (1, 1, 0))
            targs = [r for r in rows if r["event"] == "training_referee"][0]["args"]
            self.assertEqual(targs[targs.index("--workspace1") + 1], os.path.join(ws, "player1"))
            self.assertEqual(targs[targs.index("--supplement-file") + 1], os.path.abspath(note))


class Finished(unittest.TestCase):
    """The training ends early when both players are idle with nothing left to wake them."""
    def test_finished(self):
        import importlib.util
        import types
        spec = importlib.util.spec_from_file_location("p4_training", os.path.join(ROOT, "tools", "p4_training.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fin = mod.Director.finished
        me = types.SimpleNamespace(busy=lambda p: False)
        idle = {"turns": 3, "active": False, "wake_pending": False}
        st = {"players": {"0": idle}, "exercises": [{"player": 1, "status": "queued"}]}
        self.assertTrue(fin(me, st, 0))
        for change in ({"turns": 0}, {"active": True}, {"wake_pending": True}):
            self.assertFalse(fin(me, {**st, "players": {"0": {**idle, **change}}}, 0))
        self.assertFalse(fin(me, {**st, "exercises": [{"player": 0, "status": "running"}]}, 0))
        self.assertFalse(fin(types.SimpleNamespace(busy=lambda p: True), st, 0))
        self.assertFalse(fin(me, {"exercises": []}, 0))


if __name__ == "__main__":
    unittest.main()
