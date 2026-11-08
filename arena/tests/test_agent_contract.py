"""arena-agent against the real arena/referee over HTTP (fake model, synthetic mini world): every noun of the `arena`
command line must reach the referee with payloads it accepts.

Guards the interface between agent/cli.py and referee/server.py; when the referee changes a payload, this fails and
cli.py is the place to adjust.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_agent_fakes import FakeReferee  # noqa: E402,F401  (puts the repo root on sys.path)
from arena.agent import arena_agent, client, routes, tools  # noqa: E402

try:
    import fakegame  # noqa: E402
except ImportError as e:  # pragma: no cover - referee test doubles missing
    fakegame, why = None, str(e)
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


def call(cmd, input=None):
    return {"name": "arena", "arguments": {"cmd": cmd, **({"input": input} if input is not None else {})}}


@unittest.skipIf(fakegame is None, "referee test doubles not importable")
@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class AgainstRealReferee(unittest.TestCase):
    def test_prematch_turn_with_every_noun(self):
        tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)  # registered first, so it runs last (open files cannot be deleted on Windows)
        tmp = tmp_dir.name
        env = fakegame.Env(tmp)
        env.sync(0, phase="Setup")
        srv, tokens = fakegame.serve(env.ref, tmp)
        self.addCleanup(env.ref.close)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        c0, c1, r0, r1 = env.ref.sc.start_areas[0]
        home = c0 * 100 + r0
        subs = [{"type": "vp_change"}, {"type": "timer", "every_min": 60}, {"type": "new_contact", "area": {"box": [c0, c1, r0, r1]}}]
        steps = [
            {"tool_calls": [call(c) for c in ("help", "doc list", "brief show", "unit list", "contact list", "score show", "score explain",
                                               "turn status", "turn summary", "order list", "fire list", "fire report", "script list",
                                               "event tail", "map show")]},
            {"tool_calls": [call("unit show alpha"), call("unit alpha sop"), call(f"map show {c0}-{c0 + 1} {r0}-{r0 + 1}"), call(f"map hex {home}"),
                            call(f"map path {home} {home + 202}"), call("event list --since 0"), call("note put plan.md", "hello"),
                            call("note append plan.md", "more"), call("note show plan.md"), call("note list"),
                            call("script put watch --on minute", "alarm('x')"), call("script lint watch"), call("script show watch"),
                            call("script delete watch"), call("doc show basics --lines 5")]},
            {"tool_calls": [call("order add alpha hold"), call(f"order add alpha deploy {home}"), call("order add alpha hasty 9999"),
                            call(f"order lint alpha deploy {(c1 + 2) * 100 + r0}"), call('unit alpha sop acceptable_losses="Do or Die"'),
                            call("order show 1"), call("order withdraw all")]},
            {"tool_calls": [call("agent spawn deputy", "look around")]},
            {"agent": "deputy", "tool_calls": [call("turn summary"), call("turn end --report seen")]},
            {"tool_calls": [call("agent check --wait 10")]},
            {"tool_calls": [call("turn end", json.dumps(subs))]},
        ]
        tr = arena_agent.Transcript(os.path.join(tmp, "agents", "side0", "transcript.jsonl"))
        self.addCleanup(tr.close)
        agent = arena_agent.Agent(0, client.ScriptedChat(steps), tools.Referee("http://127.0.0.1:%d" % srv.server_address[1], tokens["side0"]),
                                  tools.Library(os.path.join(tmp, "library")), tr, Path(arena_agent.__file__).with_name("prompts").joinpath("system.md")
                                  .read_text(encoding="utf-8"), routes.get_route("fake"), wait_timeout=2, max_turns=1)
        agent.run()
        rows = [json.loads(l) for l in Path(tmp, "agents", "side0", "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
        tool_rows = [r for r in rows if r["type"] == "tool"]
        self.assertEqual(len(tool_rows), 42)
        bad = [(r["agent"], r["cmd"], r["http_status"], r["result"][:160]) for r in tool_rows
               if (r["http_status"] not in (None, 200) or r["result"].startswith("错误")) and r["cmd"] != "order add alpha hasty 9999"]
        self.assertEqual(bad, [])
        res = {r["cmd"]: r for r in tool_rows}
        submits = [res[c]["http_status"] for c in ("order add alpha hold", f"order add alpha deploy {home}", "order add alpha hasty 9999")]
        self.assertEqual(submits, [200, 200, 400])  # the hex off the map is the referee's refusal, not a payload error
        self.assertIn("is not a hex", res["order add alpha hasty 9999"]["result"])
        self.assertIn("不通过", res[f"order lint alpha deploy {(c1 + 2) * 100 + r0}"]["result"])     # outside the starting area
        self.assertIn("hello\nmore", res["note show plan.md"]["result"])
        self.assertIn("已撤回", res["order withdraw all"]["result"])
        self.assertIn("seen", res["agent check --wait 10"]["result"])
        self.assertIn("1: # 基础操作文档", res["doc show basics --lines 5"]["result"])
        self.assertEqual([r["reason"] for r in rows if r["type"] == "turn_end"], ["end_turn"])
        self.assertEqual(sorted(s["type"] for s in env.ref.wake.subs[0]), ["new_contact", "timer", "vp_change"])
        self.assertEqual(agent.exit_code, 0)
        self.assertIn(env.ref.sc.name, agent.system)
        self.assertNotIn("{{", agent.system)


if __name__ == "__main__":
    unittest.main()
