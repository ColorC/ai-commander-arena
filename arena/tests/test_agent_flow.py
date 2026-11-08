"""arena-agent end to end against a fake referee with the scripted (fake) model: wake-up, the `arena` tool, subagents, time
limits, auth."""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_agent_fakes import FakeClock, FakeReferee  # noqa: E402  (also puts the repo root on sys.path)
from arena.agent import arena_agent, cli, client, routes, tools  # noqa: E402

TOKEN = "tok-side0-SECRET"
PROMPT = os.path.join(os.path.dirname(arena_agent.__file__), "prompts", "system.md")


def call(cmd, input=None):
    """One call of the `arena` tool."""
    return {"name": "arena", "arguments": {"cmd": cmd, **({"input": input} if input is not None else {})}}


def legacy(tool, /, **arguments):
    """A call written with a tool name from before season 2 (ScriptedChat sends it as the command it became)."""
    return {"name": tool, "arguments": arguments}


def step(*calls, agent=None, **kw):
    return {**({"agent": agent} if agent else {}), "tool_calls": list(calls), **kw}


class AgentCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = self.tmp.name
        self.library = os.path.join(self.tmp.name, "library")
        os.makedirs(self.library)

    def make(self, ref, steps, token=TOKEN, clock=None, **kw):
        tr = arena_agent.Transcript(os.path.join(self.run_dir, "agents", "side0", "transcript.jsonl"))
        self.addCleanup(tr.close)  # registered after the tmp dir cleanup, so it runs first (Windows cannot delete open files)
        clk = {"now": clock.now, "sleep": clock.sleep} if clock else {}
        return arena_agent.Agent(0, client.ScriptedChat(steps, **clk), tools.Referee(ref.url, token), tools.Library(self.library), tr,
                                 Path(PROMPT).read_text(encoding="utf-8"), routes.get_route("fake"), wait_timeout=1, **clk, **kw)

    def records(self, type_=None, agent=None):
        path = os.path.join(self.run_dir, "agents", "side0", "transcript.jsonl")
        out = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()]
        return [r for r in out if (type_ is None or r["type"] == type_) and (agent is None or r["agent"] == agent)]

    def tool_results(self, agent):
        return {r["cmd"]: r["result"] for r in self.records("tool", agent)}


class WakeToEndTurn(AgentCase):
    def test_full_turn_with_subagent(self):
        steps = [
            step(call("turn summary"), content="look first"),
            step(call("order add alpha hasty 414")),
            step(call("agent spawn s1", "inspect Alpha")),
            step(call("unit show alp"), agent="s1"),
            step(call('turn end --report "Alpha is fine"'), agent="s1"),
            step(call("agent check --wait 10")),
            step(call("note put plan.md", "hold the line")),
            step(call("turn end", '[{"type": "vp_change"}]')),
        ]
        with FakeReferee(TOKEN) as ref:
            agent = self.make(ref, steps)
            agent.run()
            self.assertEqual(agent.exit_code, 0)
            # every request carried the side's token; the commander's calls reached the referee in order
            self.assertEqual({r[4] for r in ref.requests}, {"Bearer " + TOKEN})
            paths = [(r[0], r[1]) for r in ref.requests if r[1] not in ("view/unit/Alpha", "view/units", "turn/begin")
                     and not (r[1] == "turn/end" and r[3].get("agent"))]
            self.assertEqual(paths[:5], [("GET", "wait"), ("GET", "view/briefing"), ("GET", "view/summary"), ("POST", "queue"), ("PUT", "notes/plan.md")])
            self.assertEqual(ref.seen("POST", "queue")[0][3], {"kind": "orders", "unit": "Alpha",
                                                               "orders": [{"orders": "Move Hasty", "destination_location": 414}]})
            self.assertIsNone(ref.seen("GET", "view/unit/Alpha")[0][3])
            self.assertEqual(ref.seen("PUT", "notes/")[0][3], {"content": "hold the line"})
            ends = {e.get("agent", "commander"): e for e in ref.turn_ends}
            self.assertEqual(ends["commander"], {"turn_id": "s0-t0001", "subscriptions": [{"type": "vp_change"}]})
            self.assertEqual(ends["s1"], {"agent": "s1"})
        self.assertIn("HQ 1 Bde Test", agent.system)
        self.assertIn("Test Scenario", agent.system)
        self.assertNotIn("{{", agent.system)
        # transcript: both agents, every request answered, usage/model recorded, results present
        self.assertEqual(len(self.records("wake")), 1)
        for who, n in (("commander", 6), ("s1", 2)):
            self.assertEqual(len(self.records("request", who)), n, who)
            resp = self.records("response", who)
            self.assertEqual(len(resp), n, who)
            self.assertTrue(all(r["model"] == "fake" and r["usage"]["prompt_tokens"] > 0 and r["tool_calls"] for r in resp))
        self.assertEqual({r["name"] for r in self.records("tool")}, {"arena"})
        self.assertEqual([r["cmd"] for r in self.records("tool", "commander")],
                         ["turn summary", "order add alpha hasty 414", "agent spawn s1", "agent check --wait 10", "note put plan.md", "turn end"])
        results = self.tool_results("commander")
        self.assertIn("已入队 c1：Alpha：Move Hasty 414", results["order add alpha hasty 414"])
        self.assertIn("Alpha is fine", results["agent check --wait 10"])
        self.assertIn("已派出子代理 s1", results["agent spawn s1"])
        self.assertIn("回合已结束", results["turn end"])
        self.assertIn("Alpha（", self.tool_results("s1")["unit show alp"])            # a prefix of the name is enough
        self.assertEqual(agent.cmd.conv.notes, {"plan.md": "hold the line"})
        self.assertEqual(self.records("turn_end", "commander")[0]["reason"], "end_turn")
        self.assertEqual([r["event"] for r in self.records("subagent", "s1")], ["spawn", "end"])
        self.assertIn("Alpha is fine", self.records("subagent", "s1")[1]["report"])
        text = Path(self.run_dir, "agents", "side0", "transcript.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, text)
        first = agent.cmd.conv.messages[0]["content"]
        self.assertTrue(first.startswith("[唤醒 #1｜回合 s0-t0001]\n"), first[:60])        # no time in the head (11-04)
        tool = [m["content"] for m in agent.cmd.conv.messages if m["role"] == "tool"]
        self.assertTrue(tool and not any("已思考" in t or "已用" in t for t in tool), tool)  # nor under tool results
        self.assertIn("t=0 summary", first)
        self.assertIn("唤醒原因", first)

    def test_subagent_has_no_spawn_tool(self):
        steps = [step(call("agent spawn x", "t")), step(call("agent spawn y", "t"), agent="x"), step(call("agent check"), agent="x"),
                 step(call("turn observe on"), agent="x"), step(call("turn end"), agent="x"), step(call("turn end --report done"), agent="x"),
                 step(call("agent check --wait 10")), step(call("turn end"))]
        with FakeReferee(TOKEN) as ref:
            self.make(ref, steps).run()
            self.assertEqual(len(ref.seen("POST", "turn/begin")), 1)
            self.assertEqual(ref.seen("POST", "observe"), [])
        x = self.tool_results("x")
        self.assertIn("子代理不能再派子代理", x["agent spawn y"])
        self.assertIn("子代理没有自己的子代理", x["agent check"])
        self.assertIn("只限指挥官", x["turn observe on"])
        self.assertIn("要交报告", x["turn end"])                                    # a subagent's turn end needs a report
        self.assertEqual([t["function"]["name"] for t in [cli.schema()]], ["arena"])
        self.assertIn("done", self.tool_results("commander")["agent check --wait 10"])

    def test_subagent_names_follow_the_referee_rule(self):
        steps = [step(call("agent spawn 副指挥官", "t")), step(call("agent spawn Deputy", "t")), step(call("turn end"))]
        with FakeReferee(TOKEN) as ref:
            self.make(ref, steps).run()
            self.assertEqual(ref.seen("POST", "turn/begin"), [])
        spawns = [r["result"] for r in self.records("tool", "commander") if r["cmd"].startswith("agent spawn")]
        self.assertEqual(len(spawns), 2)
        self.assertTrue(all(r.startswith("错误：name 要") for r in spawns), spawns)


class SubagentLimits(AgentCase):
    def test_at_most_two_at_once(self):
        slow = {"agent": "sub", "sleep": 0.6, "tool_calls": [call("turn end --report ok")]}
        steps = [step(call("agent spawn a", "a")), step(call("agent spawn b", "b")),
                 step(call("agent spawn a dup")), step(call("agent spawn c", "c")),
                 slow, slow, step(call("agent check --wait 10")), step(call("agent check --wait 10")),
                 step(call("agent spawn c again")), {"agent": "c", "tool_calls": [call("turn end", "c done")]},
                 step(call("agent check --wait 10")), step(call("turn end"))]
        with FakeReferee(TOKEN) as ref:
            agent = self.make(ref, steps)
            agent.run()
            self.assertEqual(ref.max_active_subs, 2)
        spawns = [r["result"] for r in self.records("tool", "commander") if r["cmd"].startswith("agent spawn")]
        self.assertEqual([s.startswith("已派出") for s in spawns], [True, True, False, False, True])
        self.assertIn("已被占用", spawns[2])
        self.assertIn("上限 2", spawns[3])
        # every report reaches the commander once: in an `agent check` answer, or (finished between two requests) as a message
        checks = "".join(r["result"] for r in self.records("tool", "commander") if r["cmd"].startswith("agent check"))
        checks += "".join(m["content"] for m in agent.cmd.conv.messages if m["role"] == "user")
        self.assertEqual(sorted(re.findall(r"\[子代理 (\w+) 的报告", checks)), ["a", "b", "c"])

    def test_subagent_time_limit_forces_end(self):
        steps = [step(call("agent spawn slow", "t")), {"agent": "slow", "sleep": 0.5, "tool_calls": [call("turn summary")]},
                 {"agent": "slow", "sleep": 0.5, "tool_calls": [call("turn summary")]}, step(call("agent check --wait 10")), step(call("turn end"))]
        with FakeReferee(TOKEN, sub_deadline_in=0.7) as ref:  # the referee's per-task deadline is authoritative
            self.make(ref, steps).run()
            self.assertEqual(ref.turn_ends[0]["reason"], "deadline")
            self.assertEqual(ref.turn_ends[0]["agent"], "slow")
        self.assertIn("强制结束", self.records("subagent", "slow")[1]["report"])
        self.assertEqual([r["cmd"] for r in self.records("tool", "slow")], ["turn summary"])


class TurnLimit(AgentCase):
    def test_local_limit_ends_turn_and_keeps_waiting(self):
        clock = FakeClock()
        steps = [step(call("turn summary"), sleep=300), step(call("turn summary"), sleep=300), step(call("turn summary"))]
        with FakeReferee(TOKEN) as ref:
            agent = self.make(ref, steps, clock=clock)
            agent.run()
            self.assertEqual(ref.turn_ends, [{"reason": "deadline", "turn_id": "s0-t0001"}])
        self.assertEqual(len(self.records("tool", "commander")), 1)  # the second call was not executed
        self.assertEqual(self.records("turn_end")[0]["reason"], "deadline")
        self.assertIn("回合时限已到", agent.cmd.conv.messages[-1]["content"])
        self.assertEqual(agent.cmd.conv.messages[-1]["role"], "tool")  # history stays valid: every tool call answered

    def test_referee_deadline_is_authoritative(self):
        clock = FakeClock()
        with FakeReferee(TOKEN, deadline_in=100) as ref:  # shorter than the local 480
            agent = self.make(ref, [step(call("turn summary"), sleep=60), step(call("turn summary"), sleep=60)], clock=clock)
            agent.run()
            self.assertEqual(ref.turn_ends[0]["reason"], "deadline")
        self.assertEqual(len(self.records("tool", "commander")), 1)

    def test_long_referee_deadline_beats_local_limit(self):
        """The 120-minute pre-game turn must not be cut at the 8-minute commander default."""
        clock = FakeClock()
        with FakeReferee(TOKEN, deadline_in=7200) as ref:
            agent = self.make(ref, [step(call("turn summary"), sleep=600), step(call("turn summary"), sleep=600), step(call("turn end"))], clock=clock)
            agent.run()
            self.assertEqual([e.get("reason") for e in ref.turn_ends], [None])
        self.assertEqual(self.records("turn_end")[0]["reason"], "end_turn")
        self.assertEqual(len(self.records("tool", "commander")), 3)

    def test_no_tool_call_is_nudged_then_ended(self):
        with FakeReferee(TOKEN) as ref:
            agent = self.make(ref, [{"content": "thinking"}] * 3)
            agent.run()
            self.assertEqual(ref.turn_ends[0]["reason"], "no_tool_call")
        self.assertEqual(sum("你没有调用工具" in (m.get("content") or "") for m in agent.cmd.conv.messages), 2)

    def test_referee_409_idle_stops_the_turn_without_end_call(self):
        with FakeReferee(TOKEN) as ref:
            ref.idle = True
            agent = self.make(ref, [step(call("turn summary")), step(call("turn summary"))])
            agent.run()
            self.assertEqual(ref.turn_ends, [])
        self.assertEqual(self.records("turn_end")[0]["reason"], "turn_over")

    def test_open_turn_on_the_referee_is_closed_when_wait_answers_409(self):
        with FakeReferee(TOKEN) as ref:
            ref.end_rejects = 3                      # end_turn is refused three times, then the agent ends the turn locally
            agent = self.make(ref, [step(call("turn end")), step(call("turn end")), step(call("turn end"))])
            agent.run()
            self.assertEqual(len(ref.turn_ends), 1)
            self.assertEqual(ref.turn_ends[0]["reason"], "wait_conflict")
            self.assertFalse(ref.turn_open)
        self.assertEqual(self.records("turn_end")[0]["reason"], "end_turn")


class Observe(AgentCase):
    def test_reply_without_tools_waits_for_the_next_update(self):
        steps = [step(call("turn observe on")), {"content": "watching"}, {"content": "still watching"}, step(call("turn end"))]
        with FakeReferee(TOKEN, observe_updates=2) as ref:
            agent = self.make(ref, steps)
            agent.run()
            self.assertEqual(ref.observe_updates, 0)
            self.assertFalse(ref.observing)
        updates = [m["content"] for m in agent.cmd.conv.messages if m["role"] == "user" and m["content"].startswith("[观察更新")]
        self.assertEqual(len(updates), 2)
        self.assertIn("观察中", self.tool_results("commander")["turn observe on"])
        self.assertEqual(len(self.records("observe_update")), 2)
        self.assertEqual(self.records("turn_end")[0]["reason"], "end_turn")
        self.assertFalse(agent.observing)


class Context(AgentCase):
    def test_history_is_trimmed_across_wakes_and_stays_valid(self):
        steps = []
        for i in range(8):
            steps += [step(call("note put plan.md", f"plan v{i}")), step(call("turn summary")), step(call("turn end"))]
        with FakeReferee(TOKEN, wakes=8, summary="S" * 4000) as ref:
            agent = self.make(ref, steps, budget=5000)
            agent.run()
        self.assertTrue(self.records("trim"))
        ms = agent.cmd.conv.messages
        self.assertLess(len(ms), 40)                                   # 8 wakes untrimmed would be 56 messages
        self.assertTrue(ms[0]["content"].startswith(arena_agent.PLACEHOLDER) and "plan v" in ms[0]["content"])
        self.assertEqual(sum(m["role"] == "tool" for m in ms), sum(len(m.get("tool_calls") or []) for m in ms))
        self.assertEqual(len(self.records("turn_end")), 8)


class Auth(AgentCase):
    def test_wrong_token_exits_3(self):
        with FakeReferee(TOKEN) as ref:
            agent = self.make(ref, [], token="nope")
            agent.run()
        self.assertEqual(agent.exit_code, 3)
        self.assertIn("rejected the token", self.records("error")[0]["error"])

    def test_cli_wiring(self):
        steps = os.path.join(self.run_dir, "script.json")
        tokf = os.path.join(self.run_dir, "side0.token")
        # a fake script still written with the old tool names (tools/p4_training.py dry run) plays through the CLI
        Path(steps).write_text(json.dumps([step(legacy("get_summary")), step(legacy("end_turn"))]), encoding="utf-8")
        Path(tokf).write_text(TOKEN + "\n", encoding="utf-8")
        with FakeReferee(TOKEN, wakes=2) as ref:
            code = arena_agent.main(["--side", "0", "--route", "fake", "--referee", ref.url, "--token-file", tokf, "--run", self.run_dir,
                                     "--script", steps, "--library", self.library, "--max-turns", "1", "--wait-timeout", "1"])
            self.assertEqual(code, 0)
            self.assertEqual(len(ref.seen("GET", "wait")), 1)
        start = self.records("start")[0]
        self.assertEqual((start["side"], start["route"], start["model"]), (0, "fake", "fake"))
        self.assertEqual([(r["name"], r["cmd"]) for r in self.records("tool")], [("arena", "turn summary"), ("arena", "turn end")])
        self.assertEqual(self.records("turn_end")[0]["reason"], "end_turn")


if __name__ == "__main__":
    unittest.main()
