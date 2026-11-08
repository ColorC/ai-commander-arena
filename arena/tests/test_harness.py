"""arena/harness offline: the MCP server against the real referee (synthetic world), and the driver against a fake
ACP CLI (arena/tests/fake_acp_cli.py) whose scripted "model" calls the one MCP tool, `arena`."""
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_agent_fakes import FakeReferee  # noqa: E402  (puts the repo root on sys.path)
from arena.agent import cli, tools  # noqa: E402
from arena.harness import clis, driver, mcp_server  # noqa: E402
import fakegame  # noqa: E402

TEMPLATE = (Path(HERE).parent / "agent" / "prompts" / "system.md").read_text(encoding="utf-8")
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


def rpc(i, method, params=None):
    return json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}) + "\n"


def arena(i, cmd, input=None):
    return rpc(i, "tools/call", {"name": "arena", "arguments": {"cmd": cmd, **({"input": input} if input is not None else {})}})


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class McpServerAgainstReferee(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.env = fakegame.Env(self.tmp)
        self.addCleanup(self.env.ref.close)
        self.env.sync(0)
        self.srv, self.tokens = fakegame.serve(self.env.ref, self.tmp)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.log = os.path.join(self.tmp, "mcp.jsonl")

    def serve(self, lines, agent="commander"):
        referee = tools.Referee("http://127.0.0.1:%d" % self.srv.server_address[1], self.tokens["side0"])
        h = mcp_server.ArenaTools(referee, tools.Library(os.path.join(self.tmp, "library")), agent)
        log = mcp_server.Log(self.log)
        out = io.BytesIO()
        mcp_server.Server(h, log, io.BytesIO("".join(lines).encode("utf-8")), out).serve()
        os.close(log.fd)
        return {m["id"]: m for m in map(json.loads, out.getvalue().decode("utf-8").splitlines())}

    def test_initialize_list_and_calls(self):
        turn = self.env.wake(0)
        r = self.serve([rpc(1, "initialize", {"protocolVersion": "2025-11-25", "clientInfo": {"name": "t"}}),
                        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n",
                        rpc(2, "tools/list"),
                        arena(3, "turn summary"),
                        rpc(4, "tools/call", {"name": "get_summary", "arguments": {}}),
                        arena(5, "unit show alpha"),
                        arena(8, "uint list"),
                        rpc(9, "tools/call", {"name": "arena", "arguments": {}}),
                        rpc(10, "ping")])
        self.assertEqual(r[1]["result"]["protocolVersion"], "2025-11-25")
        # the CLI sees one tool, the same one arena-agent sends
        self.assertEqual(r[2]["result"]["tools"], [cli.mcp_tool()])
        self.assertEqual([t["name"] for t in r[2]["result"]["tools"]], ["arena"])
        self.assertEqual(r[2]["result"]["tools"][0]["inputSchema"]["required"], ["cmd"])
        self.assertFalse(r[3]["result"]["isError"])
        self.assertIn('"game"', r[3]["result"]["content"][0]["text"])
        self.assertTrue(r[4]["result"]["isError"])                                  # the old tool names are gone
        self.assertIn("只有一个工具 arena", r[4]["result"]["content"][0]["text"])
        self.assertFalse(r[5]["result"]["isError"])
        self.assertIn("Alpha Platoon（Tank", r[5]["result"]["content"][0]["text"])  # a unique prefix of the name
        self.assertTrue(r[8]["result"]["isError"])
        self.assertIn("你是不是想说：unit", r[8]["result"]["content"][0]["text"])
        self.assertTrue(r[9]["result"]["isError"])
        self.assertEqual(r[10]["result"], {})
        # turn end belongs to the driver (it has the turn id): without one it is an error and the turn stays open
        r = self.serve([arena(6, "turn end")])
        self.assertTrue(r[6]["result"]["isError"], r[6])
        self.assertIn("驱动程序", r[6]["result"]["content"][0]["text"])
        self.env.ref.turn_end(0, {"turn_id": turn})
        # outside a turn the referee answers 409 and the call is an error
        r = self.serve([arena(7, "turn summary")])
        self.assertTrue(r[7]["result"]["isError"])
        calls = [json.loads(x) for x in Path(self.log).read_text(encoding="utf-8").splitlines() if '"call"' in x]
        self.assertEqual(sorted(c["tool"] for c in calls), ["arena"] * 6 + ["get_summary"])  # calls run concurrently
        self.assertEqual(calls[-1]["http_status"], 409)

    def test_subagent_list_has_the_same_one_tool(self):
        r = self.serve([rpc(1, "tools/list"), arena(2, "agent spawn x", "look"), arena(3, "agent check"), arena(4, "turn end")],
                       agent="scout")
        self.assertEqual([t["name"] for t in r[1]["result"]["tools"]], ["arena"])
        self.assertIn("子代理不能再派子代理", r[2]["result"]["content"][0]["text"])
        self.assertIn("子代理没有自己的子代理", r[3]["result"]["content"][0]["text"])
        self.assertIn("要交报告", r[4]["result"]["content"][0]["text"])
        self.assertTrue(all(r[i]["result"]["isError"] for i in (2, 3, 4)))


class FakeCli:
    name, model, shared_process = "fake", "fake/model", True

    def __init__(self, script_path):
        self.script = script_path

    def command(self):
        return [sys.executable, "-I", "-X", "utf8", os.path.join(HERE, "fake_acp_cli.py"), self.script]

    def prepare(self, cwd, state_dir, system_text, agent="commander", mcp=None):
        clis.write_text(os.path.join(cwd, "system.txt"), system_text)

    def env(self, cwd, state_dir, agent="commander"):
        return clis.base_env()

    def settings(self):
        return [("model", self.model)]

    def usage(self, session_id, since=0):
        return ([{"model": self.model, "input": 100, "output": 10}], since + 1) if since == 0 else ([], since)


class DriverWithFakeCli(unittest.TestCase):
    def make(self, ref, script, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        run = tmp.name
        sp = os.path.join(run, "script.json")
        Path(sp).write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
        tok = os.path.join(run, "side0.token")
        Path(tok).write_text(ref.token + "\n", encoding="utf-8")
        d = driver.Driver(0, FakeCli(sp), tools.Referee(ref.url, ref.token), run, TEMPLATE, os.path.join(run, "library"), ref.url, tok,
                          wait_timeout=1, end_grace=2.0, **kw)
        return d, run

    def read(self, run, name):
        return [json.loads(x) for x in Path(run, "agents", "side0", name).read_text(encoding="utf-8").splitlines()]

    def finish(self, d):
        d.run()
        self.assertEqual(d.shutdown(), [])
        d.tr.close()

    def test_two_wakes_tools_end_turn_and_a_subagent(self):
        script = {"commander": [
            [{"arena": "turn summary"}, {"arena": "unit list"}, {"say": "看过了"}, {"arena": "order add alpha move 21,18"},
             {"arena": "turn end", "input": '[{"type": "vp_change"}]'}],
            [{"arena": "agent spawn scout", "input": "看看敌情"}, {"arena": "agent check --wait 30"}, {"arena": "turn end"}]],
            "scout": [[{"arena": "contact list"}, {"arena": "turn end --report 北面两辆车"}]]}
        with FakeReferee(wakes=2) as ref:
            d, run = self.make(ref, script)
            self.finish(d)
            ends = ref.turn_ends
        self.assertEqual([e.get("agent", "commander") for e in ends], ["commander", "scout", "commander"])
        self.assertEqual(ends[0], {"turn_id": "s0-t0001", "subscriptions": [{"type": "vp_change"}]})
        self.assertEqual(ref.seen("POST", "queue")[0][3], {"kind": "orders", "unit": "Alpha",
                                                           "orders": [{"orders": "Move Deliberate", "destination_location": 2118}]})
        tr = self.read(run, "transcript.jsonl")
        turns = [r for r in tr if r["type"] == "turn_end"]
        self.assertEqual([t["reason"] for t in turns], ["end_turn", "end_turn"])
        sub = next(r for r in tr if r["type"] == "subagent" and r.get("event") == "end")
        self.assertEqual((sub["reason"], sub["report"]), ("end_turn", "北面两辆车"))
        calls = [r for r in self.read(run, "mcp.jsonl") if r["type"] == "call"]
        self.assertEqual({c["tool"] for c in calls}, {"arena"})
        self.assertEqual([(c["agent"], json.loads(c["arguments"])["cmd"]) for c in calls],
                         [("commander", "turn summary"), ("commander", "unit list"), ("commander", "order add alpha move 21,18"),
                          ("commander", "turn end"), ("commander", "agent spawn scout"), ("scout", "contact list"),
                          ("scout", "turn end --report 北面两辆车"), ("commander", "agent check --wait 30"), ("commander", "turn end")])
        res = {(c["agent"], json.loads(c["arguments"])["cmd"]): c["result"] for c in calls}
        self.assertIn("北面两辆车", res[("commander", "agent check --wait 30")])
        self.assertIn("已入队 c1：Alpha：Move Deliberate 2118", res[("commander", "order add alpha move 21,18")])
        # the blocking turn end: the first one's result is the next wake, the last one's that the game is over
        ends_text = [c["result"] for c in calls if (c["agent"], json.loads(c["arguments"])["cmd"]) == ("commander", "turn end")]
        self.assertTrue(ends_text[0].startswith("[唤醒 #2｜回合 s0-t0002]\n唤醒原因："), ends_text[0])
        self.assertIn("t=0 summary", ends_text[0])
        self.assertEqual(ends_text[1], driver.GAME_OVER)
        self.assertIn("任务已结束", res[("scout", "turn end --report 北面两辆车")])
        self.assertFalse(any("已思考" in c["result"] or "已用" in c["result"] for c in calls))   # no time stamps (11-04)
        self.assertNotIn("剩余", calls[0]["result"])
        self.assertFalse(any(c["is_error"] for c in calls), [c for c in calls if c["is_error"]])
        prompts = [r["msg"]["params"]["prompt"][0]["text"] for r in tr if r["type"] == "acp" and r["dir"] == "out"
                   and r["msg"].get("method") == "session/prompt"]
        self.assertTrue(prompts[0].startswith("[唤醒 #1｜回合 s0-t0001]\n"), prompts[0])
        self.assertIn("t=0 summary", prompts[0])
        # wake 2 came as the tool result; the prompt turn ended without another turn end, so the model is nudged
        self.assertEqual([p for p in prompts if not p.startswith("你是子代理")], [prompts[0], driver.NUDGE])
        self.assertEqual(sum(p.startswith("你是子代理“scout”") for p in prompts), 1)
        self.assertEqual([(r["n"], r["via"]) for r in tr if r["type"] == "wake"], [(1, "prompt"), (2, "turn_end")])
        system = Path(run, "cli", "side0", "system.txt").read_text(encoding="utf-8")
        self.assertIn("HQ 1 Bde Test", system)  # rendered with the briefing as arena-agent renders it
        usage = self.read(run, "usage.jsonl")
        self.assertEqual({u["agent"] for u in usage}, {"commander", "scout"})
        self.assertTrue(any(r["type"] == "usage_total" and r["totals"]["fake/model"]["calls"] == 2 for r in tr))

    def test_old_turn_end_without_blocking(self):
        """block=False: every wake is a prompt, turn end answers at once (the training referee's way)."""
        script = {"commander": [[{"arena": "turn end"}], [{"arena": "turn end"}]]}
        with FakeReferee(wakes=2) as ref:
            d, run = self.make(ref, script, block=False)
            self.finish(d)
            self.assertEqual(len(ref.turn_ends), 2)
        tr = self.read(run, "transcript.jsonl")
        prompts = [r["msg"]["params"]["prompt"][0]["text"] for r in tr if r["type"] == "acp" and r["dir"] == "out"
                   and r["msg"].get("method") == "session/prompt"]
        self.assertEqual(len(prompts), 2)
        self.assertTrue(prompts[0].startswith("[唤醒 #1｜回合 s0-t0001]\n") and prompts[1].startswith("[唤醒 #2｜回合 s0-t0002]\n"), prompts)
        calls = [r for r in self.read(run, "mcp.jsonl") if r["type"] == "call"]
        self.assertTrue(all("回合已结束" in c["result"] for c in calls), calls)

    def test_a_whole_game_in_one_prompt_turn(self):
        """The model goes on after each blocking turn end (the fake's chain): one prompt, every wake a tool result."""
        script = {"commander": {"rules": [{"match": "对局结束", "then": [{"say": "完"}]},
                                          {"match": "唤醒", "then": [{"arena": "unit list"}, {"arena": "turn end", "chain": True}]}]}}
        with FakeReferee(wakes=3) as ref:
            d, run = self.make(ref, script)
            self.finish(d)
            self.assertEqual([e["turn_id"] for e in ref.turn_ends], ["s0-t0001", "s0-t0002", "s0-t0003"])
        tr = self.read(run, "transcript.jsonl")
        prompts = [r for r in tr if r["type"] == "acp" and r["dir"] == "out" and r["msg"].get("method") == "session/prompt"]
        self.assertEqual(len(prompts), 1)
        self.assertEqual([(r["n"], r["via"]) for r in tr if r["type"] == "wake"], [(1, "prompt"), (2, "turn_end"), (3, "turn_end")])
        self.assertEqual([r["reason"] for r in tr if r["type"] == "turn_end"], ["end_turn"] * 3)
        self.assertFalse(any(r["type"] == "cancel" for r in tr))
        ends = [c["result"] for c in self.read(run, "mcp.jsonl") if c["type"] == "call" and "turn end" in c["arguments"]]
        self.assertEqual([e[:10] for e in ends], ["[唤醒 #2｜回合 ", "[唤醒 #3｜回合 ", driver.GAME_OVER[:10]])

    def test_a_block_that_reaches_the_cli_timeout_asks_for_turn_end_again(self):
        script = {"commander": {"rules": [{"match": "对局结束", "then": [{"say": "完"}]},
                                          {"match": "游戏还在进行", "then": [{"arena": "turn end", "chain": True}]},
                                          {"match": "唤醒", "then": [{"arena": "turn end", "chain": True}]}]}}
        with FakeReferee(wakes=2) as ref:
            d, run = self.make(ref, script)
            d.cli.block_sec = 1.5

            def held():                      # the second wake is held back for a while (the game still going)
                ref.hold = True
                threading.Timer(2.5, lambda: setattr(ref, "hold", False)).start()
            real_end = d.end_turn
            d.end_turn = lambda w, a: (held(), real_end(w, a))[1]
            self.finish(d)
            self.assertEqual([e["turn_id"] for e in ref.turn_ends], ["s0-t0001", "s0-t0002"])   # the retry sends none
        ends = [c["result"] for c in self.read(run, "mcp.jsonl") if c["type"] == "call" and "turn end" in c["arguments"]]
        self.assertEqual(ends[0], driver.STILL_GOING)
        self.assertTrue(any(e.startswith("[唤醒 #2｜") for e in ends[1:]), ends)
        self.assertEqual(ends[-1], driver.GAME_OVER)
        self.assertNotIn("秒", driver.STILL_GOING)

    def test_after_the_backstop_the_next_turn_end_still_returns_the_next_wake(self):
        """The referee closed the turn on its own clock while the model was still thinking: no cancel, the model's
        calls answer that the turn is over, and its turn end blocks and brings the next wake."""
        script = {"commander": {"rules": [{"match": "对局结束", "then": [{"say": "完"}]},
                                          {"match": "唤醒 #1", "then": [{"arena": "turn summary"}, {"sleep": 2.5}, {"arena": "unit list"},
                                                                       {"arena": "turn end", "chain": True}]},
                                          {"match": "唤醒 #2", "then": [{"arena": "turn end", "chain": True}]}]}}
        with FakeReferee(wakes=2, deadline_in=1) as ref:
            d, run = self.make(ref, script, turn_limit=30)
            backstop = threading.Timer(1.5, lambda: setattr(ref, "idle", True))   # the referee ends the turn itself
            backstop.start()
            self.finish(d)
        tr = self.read(run, "transcript.jsonl")
        self.assertFalse(any(r["type"] == "cancel" for r in tr))
        calls = [c for c in self.read(run, "mcp.jsonl") if c["type"] == "call"]
        by = [(json.loads(c["arguments"])["cmd"], c["result"]) for c in calls]
        self.assertTrue(by[1][0] == "unit list" and "本回合已经结束" in by[1][1], by[1])
        self.assertTrue(by[2][0] == "turn end" and by[2][1].startswith("[唤醒 #2｜回合 s0-t0002]"), by[2])
        self.assertEqual([(r["n"], r["via"]) for r in tr if r["type"] == "wake"], [(1, "prompt"), (2, "turn_end")])

    def test_deadline_cancels_the_prompt_and_closes_the_turn(self):
        # a blocking commander's prompt is cancelled as stuck a whole turn_limit after the referee's deadline
        with FakeReferee(wakes=1, deadline_in=2) as ref:
            d, run = self.make(ref, {"commander": [[{"arena": "turn summary"}, {"sleep": 30}, {"arena": "turn end"}]]}, turn_limit=1)
            self.finish(d)
            ends = ref.turn_ends
        self.assertEqual(ends, [{"reason": "deadline", "turn_id": "s0-t0001"}])
        tr = self.read(run, "transcript.jsonl")
        self.assertTrue(any(r["type"] == "cancel" and r["reason"] == "deadline" for r in tr))
        self.assertTrue(any(r["type"] == "acp" and r["msg"].get("method") == "session/cancel" for r in tr))

    def test_three_replies_without_tools_end_the_turn(self):
        with FakeReferee(wakes=1) as ref:
            d, run = self.make(ref, {"commander": [[{"say": "嗯"}], [{"say": "嗯"}], [{"say": "嗯"}], [{"say": "嗯"}]]})
            self.finish(d)
            ends = ref.turn_ends
        self.assertEqual(ends, [{"reason": "no_tool_call", "turn_id": "s0-t0001"}])
        prompts = [r["msg"]["params"]["prompt"][0]["text"] for r in self.read(run, "transcript.jsonl") if r["type"] == "acp"
                   and r["dir"] == "out" and r["msg"].get("method") == "session/prompt"]
        self.assertEqual(prompts[1:], [driver.NUDGE] * 2)  # as arena-agent: the third reply without a tool call ends it


class CliToolTimeouts(unittest.TestCase):
    """The blocking turn end needs a long MCP tool call and no step cap in both CLIs, set per process only."""

    def test_kimi_env_and_opencode_config(self):
        env = clis.Kimi(main_js="x.mjs", node="node").env("cwd", "state")
        self.assertEqual(env["KIMI_MCP_TOOL_TIMEOUT_MS"], str(clis.TOOL_TIMEOUT_MS))
        self.assertEqual(env["KIMI_LOOP_MAX_STEPS_PER_TURN"], "0")
        self.assertLessEqual(clis.TOOL_TIMEOUT_MS, 2147483647)
        mcp = {"command": "py", "args": ["-m", "x"], "env": [{"name": "A", "value": "1"}]}
        conf = clis.OpenCode(exe="oc.exe").config("sys", mcp)
        self.assertEqual(conf["mcp"]["servers"][clis.MCP_NAME]["timeout"], {"execution": clis.TOOL_TIMEOUT_MS})
        self.assertNotIn("steps", conf["agent"]["arena"])
        for c in (clis.Kimi, clis.OpenCode):
            self.assertLess(c.block_sec, clis.TOOL_TIMEOUT_MS / 1000)       # the driver answers before the CLI gives up


if __name__ == "__main__":
    unittest.main()
