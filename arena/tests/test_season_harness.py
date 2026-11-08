"""Season 2 harness (materials/18 2.2): the player's home (session.json, the lock), acp session/load against the fake
ACP CLI, and the driver continuing one session across runs, with facility failures ending a run with exit code 4."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_agent_fakes import FakeReferee  # noqa: E402  (puts the repo root on sys.path)
from arena.agent import tools  # noqa: E402
from arena.harness import acp, clis, driver, player  # noqa: E402
from arena.referee.errors import ApiError  # noqa: E402
from arena.referee.workspace import Workspace  # noqa: E402

TEMPLATE = (Path(HERE).parent / "agent" / "prompts" / "system.md").read_text(encoding="utf-8")


def tmpdir(case):
    t = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    case.addCleanup(t.cleanup)
    return t.name


class HomeAndLock(unittest.TestCase):
    def test_layout_and_session_record(self):
        h = player.Home(tmpdir(self), "A")
        for d in (h.cwd, h.workspace, h.state, h.scratch("scout")):
            self.assertTrue(os.path.isdir(d))
        self.assertIsNone(h.session_id)
        h.note_open("new", "s-1", "kimi", "kimi-code/k3.1-pro", 111, phase="start", run="r1")
        h.note_open("load", "s-1", "kimi", "kimi-code/k3.1-pro", 222, phase="training", run="r2", replay_updates=5)
        rec = json.loads(Path(h.session_path).read_text(encoding="utf-8"))
        self.assertEqual((rec["session_id"], rec["cli"], rec["cwd"]), ("s-1", "kimi", h.cwd))
        self.assertEqual([(x["op"], x["cli_pid"], x["phase"]) for x in rec["history"]], [("new", 111, "start"), ("load", 222, "training")])
        self.assertEqual(rec["history"][1]["replay_updates"], 5)
        with self.assertRaises(ValueError):                      # one session per season: no second session/new
            h.note_open("new", "s-2", "kimi", "kimi-code/k3.1-pro", 333)
        with self.assertRaises(ValueError):                      # a load of another id is not this home's session
            h.note_open("load", "s-2", "kimi", "kimi-code/k3.1-pro", 333)
        with self.assertRaises(ValueError):                      # another CLI or model cannot continue it
            h.check_cli("opencode", "proxy/deepseek-v4.1-flash")
        h.check_cli("kimi", "kimi-code/k3.1-pro")

    def test_season_prompt_is_fixed_per_home(self):
        a = player.Home(tmpdir(self), "A")
        s = player.Home(a.root, "A", "sparring")
        self.assertEqual(a.system_text(), player.Home(a.root, "A").system_text())
        self.assertIn("参赛选手（A）", a.system_text())
        self.assertIn("陪练", s.system_text())
        self.assertNotEqual(a.dir, s.dir)

    def test_proc_start_time(self):
        me = player.proc_start_time(os.getpid())
        self.assertIsNotNone(me)
        self.assertTrue(player.same_process(os.getpid(), me))
        self.assertFalse(player.same_process(os.getpid(), me - 100))   # same PID, another start time: not this process
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertIsNone(player.proc_start_time(p.pid))

    def test_lock_live_holder_stale_and_release(self):
        h = player.Home(tmpdir(self), "A")
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: p.poll() is None and p.kill())
        time.sleep(0.3)
        held = h.acquire("training", "r1", pid=p.pid)
        self.assertEqual(held["pid"], p.pid)
        self.assertIsNotNone(held["started"])
        with self.assertRaises(player.SessionBusy):
            h.acquire("exercise", "r2")
        self.assertEqual(h.holder()["pid"], p.pid)
        p.kill()
        p.wait()
        mine = h.acquire("exercise", "r2")                     # the holder is gone: the lock is stale, taken over
        self.assertEqual(mine["pid"], os.getpid())
        self.assertEqual(h.acquire("exercise", "r2")["pid"], os.getpid())   # re-entrant for the same process
        self.assertTrue(h.release())
        self.assertFalse(os.path.exists(h.lock_path))
        # a lock naming a live PID with another start time (a reused PID) is stale too
        Path(h.lock_path).write_text(json.dumps({"pid": os.getpid(), "started": 1.0}), encoding="utf-8")
        self.assertIsNone(h.holder())


class SparringIsolation(unittest.TestCase):
    def test_workspaces_do_not_reach_each_other(self):
        root = tmpdir(self)
        a, s = player.Home(root, "A"), player.Home(root, "A", "sparring")
        Workspace(s.workspace).notes("PUT", "plan.md", {"content": "陪练的计划"})
        Workspace(a.workspace).notes("PUT", "plan.md", {"content": "选手的计划"})
        rel = os.path.relpath(os.path.join(s.workspace, "plan.md"), a.workspace).replace(os.sep, "/")
        for path in (rel, "../plan.md", "../../sparring/A/workspace/plan.md", "..", "./../x"):
            with self.assertRaises(ApiError, msg=path):
                Workspace(a.workspace).notes("GET", path, None)
        self.assertEqual(Workspace(a.workspace).notes("GET", "plan.md", None)["content"], "选手的计划")
        for x, y in ((a, s), (s, a)):   # no folder of one is inside a folder of the other
            for d in (x.dir, x.workspace, x.cwd, x.scratch_root):
                for e in (y.dir, y.workspace, y.cwd, y.scratch_root):
                    self.assertFalse(os.path.commonpath([d, e]) in (d, e), (d, e))


class FakeCliLoad(unittest.TestCase):
    def test_new_then_load_in_a_fresh_process_replays_and_continues(self):
        tmp = tmpdir(self)
        sp = os.path.join(tmp, "script.json")
        Path(sp).write_text(json.dumps({"commander": [[{"say": "记住了"}], [{"say": "琥珀1234"}]]}), encoding="utf-8")
        tok = os.path.join(tmp, "t.token")
        Path(tok).write_text("x\n", encoding="utf-8")
        mcp = acp.stdio_server("arena", sys.executable, ["-I", "-X", "utf8", os.path.join(HERE, "..", "harness", "mcp_server.py"),
                                                        "--referee", "http://127.0.0.1:9", "--token-file", tok])
        cli = clis.Fake(script=sp)
        updates = []
        c = acp.AcpClient(cli.command(), tmp, cli.env(tmp, tmp), on_update=updates.append).start()
        c.initialize()
        sid = c.new_session(tmp, [mcp])["sessionId"]
        self.assertEqual(c.prompt_async(sid, "暗号是琥珀1234").wait(60)["stopReason"], "end_turn")
        c.close()
        c = acp.AcpClient(cli.command(), tmp, cli.env(tmp, tmp), on_update=updates.append).start()
        self.assertTrue(c.initialize()["agentCapabilities"]["loadSession"])
        n = len(updates)
        self.assertEqual(c.load_session(sid, tmp, [mcp]), {})
        replay = [u["update"] for u in updates[n:]]
        self.assertEqual([(u["sessionUpdate"], u["content"]["text"]) for u in replay],
                         [("user_message_chunk", "暗号是琥珀1234"), ("agent_message_chunk", "记住了")])
        c.prompt_async(sid, "暗号？").wait(60)
        self.assertEqual(updates[-1]["update"]["content"]["text"], "琥珀1234")   # the script went on where it was
        with self.assertRaises(acp.AcpError):
            c.load_session("fake-nope", tmp, [mcp], timeout=30)
        c.close()


class SeasonDriver(unittest.TestCase):
    """The driver in season mode against the fake referee and the fake CLI: one home, several runs."""

    def setUp(self):
        self.tmp = tmpdir(self)
        self.home = player.Home(os.path.join(self.tmp, "homes"), "A")

    def drive(self, ref, script, run, phase, notice=None, **kw):
        sp = os.path.join(self.tmp, "script.json")
        if script is not None:
            Path(sp).write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
        tok = os.path.join(run, "side0.token")
        os.makedirs(run, exist_ok=True)
        Path(tok).write_text(ref.token + "\n", encoding="utf-8")
        self.home.acquire(phase, run)
        d = driver.Driver(0, clis.Fake(script=sp), tools.Referee(ref.url, ref.token), run, TEMPLATE, os.path.join(run, "library"), ref.url,
                          tok, wait_timeout=1, end_grace=2.0, home=self.home, phase=phase, notice=notice, **kw)
        try:
            d.run()
            left = d.shutdown()
            d.tr.close()
        finally:
            self.home.release()
        self.assertEqual(left, [])
        return d

    def prompts(self, run):
        rows = [json.loads(x) for x in Path(run, "agents", "side0", "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
        return [r["msg"]["params"]["prompt"][0]["text"] for r in rows if r["type"] == "acp" and r["dir"] == "out"
                and r["msg"].get("method") == "session/prompt"], rows

    def test_one_session_across_runs_with_phase_intro_and_notice(self):
        script = {"commander": {"rules": [{"match": "唤醒", "then": [{"arena": "turn summary"}, {"arena": "turn end"}]}]}}
        with FakeReferee(wakes=1) as ref:
            d1 = self.drive(ref, script, os.path.join(self.tmp, "r1"), "start task")
        with FakeReferee(wakes=2) as ref:
            d2 = self.drive(ref, None, os.path.join(self.tmp, "r2"), "exercise p0-1", notice="上一局游戏卡死，已从头重开，不扣额度。")
            ends = ref.turn_ends
        self.assertEqual((d1.exit_code, d2.exit_code), (0, 0))
        self.assertEqual(len(ends), 2)
        rec = self.home.record()
        self.assertEqual([h["op"] for h in rec["history"]], ["new", "resume"])
        self.assertEqual({h["session_id"] for h in rec["history"]}, {rec["session_id"]})
        self.assertEqual([h["phase"] for h in rec["history"]], ["start task", "exercise p0-1"])
        self.assertEqual(rec["history"][1]["replay_updates"], 0)        # resume: no history replay
        p1, _ = self.prompts(os.path.join(self.tmp, "r1"))
        p2, rows2 = self.prompts(os.path.join(self.tmp, "r2"))
        self.assertTrue(p1[0].startswith("[阶段开始：start task]\n本阶段的规则全文（规则编号 "), p1[0][:80])
        self.assertIn("[唤醒 #1｜", p1[0])
        # same rules text: the second run refers to it; the notice comes first; only the run's first wake has a head
        self.assertTrue(p2[0].startswith("[阶段开始：exercise p0-1]\n[裁判通知]\n上一局游戏卡死"), p2[0][:80])
        self.assertIn("一字不差", p2[0])
        # wake 2 is the return of the first turn end (r5-minden 11-04), not a prompt of its own
        self.assertEqual([(r["n"], r["via"]) for r in rows2 if r["type"] == "wake"], [(1, "prompt"), (2, "turn_end")])
        self.assertTrue(any(r["type"] == "acp" and "tool_call_update" in json.dumps(r) and "[唤醒 #2｜" in json.dumps(r, ensure_ascii=False)
                            for r in rows2))
        self.assertFalse(any(p.startswith("[唤醒 #2｜") for p in p2))
        self.assertFalse(any(r["type"] == "acp" and r["msg"].get("method") == "session/update" and "user_message_chunk" in json.dumps(r["msg"])
                             for r in rows2))                               # the replay is counted, not logged
        # the season prompt went to the CLI, not the phase rules
        self.assertEqual(Path(self.home.cwd, "system.txt").read_text(encoding="utf-8"), self.home.system_text())

    def test_cli_death_is_a_facility_failure(self):
        script = {"commander": [[{"exit": 3}]]}
        with FakeReferee(wakes=3) as ref:
            d = self.drive(ref, script, os.path.join(self.tmp, "r1"), "exercise p0-1")
        self.assertEqual(d.exit_code, driver.FACILITY_EXIT)
        f = json.loads(Path(self.tmp, "r1", "agents", "side0", "facility.json").read_text(encoding="utf-8"))
        self.assertEqual(f["kind"], "cli_died")
        self.assertEqual(f["phase"], "exercise p0-1")
        # the next run continues the same session in a fresh process (the director restarts the game)
        with FakeReferee(wakes=1) as ref:
            Path(self.tmp, "script.json").write_text(json.dumps({"commander": [[{"exit": 3}], [{"arena": "turn end"}]]}), encoding="utf-8")
            d = self.drive(ref, None, os.path.join(self.tmp, "r2"), "exercise p0-1 (restart 1)")
        self.assertEqual(d.exit_code, 0)
        self.assertEqual([h["op"] for h in self.home.record()["history"]], ["new", "resume"])

    def test_load_when_resume_fails_and_the_model_is_set_after_either(self):
        script = {"_cli": {"no_resume": True}, "commander": {"rules": [{"match": "唤醒", "then": [{"arena": "turn end"}]}]}}
        with FakeReferee(wakes=1) as ref:
            self.drive(ref, script, os.path.join(self.tmp, "r1"), "start task")
        with FakeReferee(wakes=1) as ref:
            d = self.drive(ref, None, os.path.join(self.tmp, "r2"), "training")
        self.assertEqual(d.exit_code, 0)
        h = self.home.record()["history"]
        self.assertEqual([x["op"] for x in h], ["new", "load"])
        self.assertIn("Method not found", h[1]["resume_error"])
        self.assertEqual(h[1]["replay_updates"], 2)                     # load replays: the prompt and the tool line
        sent = [json.loads(x) for x in Path(self.tmp, "script.json.log").read_text(encoding="utf-8").splitlines()]
        methods = [m.get("method") for m in sent]
        i = methods.index("session/load")
        self.assertEqual(methods[i - 1], "session/resume")
        self.assertIn("session/set_config_option", methods[i + 1:])       # the model again after the continuation

    def test_three_prompt_errors_are_a_provider_failure(self):
        d = None
        with FakeReferee(wakes=2) as ref:
            sp = os.path.join(self.tmp, "script.json")
            Path(sp).write_text(json.dumps({"commander": [[{"say": "x"}]]}), encoding="utf-8")
            run = os.path.join(self.tmp, "r1")
            os.makedirs(run, exist_ok=True)
            tok = os.path.join(run, "t")
            Path(tok).write_text(ref.token, encoding="utf-8")
            d = driver.Driver(0, clis.Fake(script=sp), tools.Referee(ref.url, ref.token), run, TEMPLATE, os.path.join(run, "lib"), ref.url,
                              tok, wait_timeout=1, end_grace=2.0, home=self.home, phase="formal game 1")

            class Failing:  # a prompt that answers a JSON-RPC error, as a CLI does when the provider keeps failing
                def __init__(self, inner):
                    self.inner = inner

                def __getattr__(self, k):
                    return getattr(self.inner, k)

                def prompt_async(self, sid, text):
                    return self.inner.request_async("session/no_such_method", {})
            orig = d.open_session

            def open_and_break(w):
                orig(w)
                w.client = Failing(w.client)
            d.open_session = open_and_break
            d.run()
            d.shutdown()
            d.tr.close()
        self.assertEqual(d.exit_code, driver.FACILITY_EXIT)
        self.assertEqual(d.facility_failure["kind"], "provider_error")

    def test_busy_session_refused_by_main(self):
        with FakeReferee(wakes=1) as ref:
            run = os.path.join(self.tmp, "r1")
            os.makedirs(run, exist_ok=True)
            tok = os.path.join(run, "t")
            Path(tok).write_text(ref.token, encoding="utf-8")
            p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            self.addCleanup(lambda: p.poll() is None and p.kill())
            time.sleep(0.3)
            self.home.acquire("training", "rx", pid=p.pid)
            code = driver.main(["--side", "0", "--cli", "fake", "--fake-script", os.path.join(self.tmp, "none.json"), "--referee", ref.url,
                                "--token-file", tok, "--run", run, "--home", self.home.root, "--player", "A", "--phase", "exercise"])
            p.kill()
        self.assertEqual(code, driver.BUSY_EXIT)


if __name__ == "__main__":
    unittest.main()
