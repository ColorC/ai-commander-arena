"""Reflex script registration and scheduling with an injected (fake) sandbox: limits, strikes, budgets, fog."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.referee import rules  # noqa: E402
from arena.referee.core import ApiError  # noqa: E402
from arena.tests.fakegame import Env, EnvCase  # noqa: E402

HOLD = {"kind": "orders", "unit": "Bravo Platoon", "orders": [{"orders": "Hold"}]}
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


class FakeSandbox:
    """Stands in for the Lua sandbox: scripted answers by script name, every request recorded."""

    def __init__(self):
        self.requests, self.answers = [], {}

    def __call__(self, req):
        self.requests.append(req)
        a = self.answers.get(req["name"], {"ok": True})
        return a(req) if callable(a) else dict(a)


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Scripts(EnvCase):
    def setUp(self):
        self.box = FakeSandbox()
        self.env_kw = {"script_runner": self.box}
        super().setUp()
        self.env.wake(0)

    def put(self, name="watch", trigger=None, source="return 1"):
        body = {"source": source}
        if trigger:
            body["trigger"] = trigger
        return self.ref.scripts_api(0, "PUT", name, body)

    def refuse(self, name, body):
        with self.assertRaises(ApiError) as cm:
            self.ref.scripts_api(0, "PUT", name, body)
        self.assertEqual(cm.exception.code, 400)

    def test_registration_rules(self):
        self.assertTrue(self.put("watch")["ok"])
        for name in ("Bad", "9lives", "has space", "", "x" * 40, "../x"):
            self.refuse(name, {"source": "return 1"})
        self.refuse("a", {"source": ""})
        self.refuse("a", {"source": "x" * (rules.SCRIPT_SOURCE_BYTES + 1)})
        self.refuse("a", {"source": "return 1", "trigger": {"on": "whenever"}})
        self.refuse("a", {"source": "return 1", "trigger": {"on": "event", "types": ["nonsense"]}})
        self.refuse("a", {"source": "return 1", "trigger": {"on": "event"}})
        for i in range(rules.MAX_SCRIPTS - 1):
            self.put(f"s{i}")
        self.refuse("one_too_many", {"source": "return 1"})
        self.assertTrue(self.put("s0", source="return 2")["ok"])             # replacing an existing one is fine
        self.assertEqual(len(self.ref.scripts_api(0, "GET", "", None)["scripts"]), rules.MAX_SCRIPTS)
        self.assertTrue(self.ref.scripts_api(0, "DELETE", "s0", None)["ok"])
        self.assertFalse(self.ref.scripts_api(0, "DELETE", "s0", None)["ok"])

    def test_sides_do_not_see_each_others_scripts(self):
        self.put("mine")
        self.env.wake(1)
        self.assertEqual(self.ref.scripts_api(1, "GET", "", None)["scripts"], {})

    def test_minute_script_runs_with_the_fog_checked_view_and_its_commands_are_queued(self):
        self.box.answers["watch"] = {"ok": True, "instructions": 1000, "ms": 1.5, "commands": [HOLD],
                                     "persist": {"seen": 1}}
        self.put("watch")
        self.env.sync(100, lines=[self.env.contact(90, "Red Tank Company", 144, "Detected", "3x vehicle(s)")])
        req = self.box.requests[-1]
        self.assertEqual((req["side"], req["trigger"], req["limits"]["instructions"]), (0, "minute", 2_000_000))
        self.assertEqual(req["limits"]["ms"], 200)
        text = json.dumps(req["input"])
        for name in ("Red Tank Company", "9 MR"):
            self.assertNotIn(name, text)
        self.assertEqual(req["input"]["contacts_in_view"][0]["contact"], 144)
        self.assertEqual([c["contact"] for c in req["input"]["contacts"]], [144])
        units = {u["name"]: u for u in req["input"]["units"]}                 # every own unit, as list_units gives it
        self.assertEqual(set(units), {n for n, sd in self.ref.world.side_of.items() if sd == 0})
        self.assertIn("status", units["Bravo Platoon"])
        cmds = [c for c in self.ref.queue.cmds.values() if c["source"] == "script:watch"]
        self.assertEqual(len([c for c in cmds if c["status"] == "queued"]), 1)   # newer replaces older: one stays
        self.env.sync(160)
        self.assertEqual(self.box.requests[-1]["persist"], {"seen": 1})

    def test_script_commands_are_validated_but_do_not_count_as_an_illegal_streak(self):
        self.box.answers["bad"] = {"ok": True, "commands": [{"kind": "orders", "unit": "nobody",
                                                             "orders": [{"orders": "Hold"}]}] * 5}
        self.put("bad")
        self.env.sync(60)
        self.assertEqual(self.ref.queue.streak[0], 0)
        self.assertEqual(self.ref.queue.cmds, {})
        self.assertEqual([r for r in self.env.log("queue") if r.get("event") == "script_command_rejected"][0]["source"],
                         "script:bad")
        # the side is told (start task 11-01: DeepSeek's script had 5 Barrages refused and never knew)
        failed = [e for e in self.ref.events.items[0] if e["type"] == "script_command_failed"]
        self.assertEqual(len(failed), 5)
        self.assertEqual((failed[0]["script"], failed[0]["unit"]), ("bad", "nobody"))
        self.assertTrue(failed[0]["error"])
        self.assertEqual([e for e in self.ref.events.items[1] if e["type"] == "script_command_failed"], [])

    def test_triggers(self):
        self.put("m", {"on": "minute"})
        self.put("o", {"on": "orders_phase"})
        self.put("e", {"on": "event", "types": ["unit_out"]})
        self.env.sync(60)
        self.assertEqual([r["name"] for r in self.box.requests], ["m"])
        self.env.units["Alpha Platoon"]["s"]["status"] = "Dead"
        self.env.sync(120)
        self.assertEqual([r["name"] for r in self.box.requests], ["m", "m", "e"])
        self.assertEqual(self.box.requests[-1]["events"][0]["type"], "unit_out")
        self.env.stop(200, 0)
        self.assertIn("o", [r["name"] for r in self.box.requests])
        self.assertEqual([r["trigger"] for r in self.box.requests if r["name"] == "o"], ["orders_phase"])

    def next_wake(self, between=None):
        """End the open turn and wake the commander again the way an in-game wake does -> the wait answer."""
        self.ref.turn_end(0, {"turn_id": self.ref.turn_id[0]})
        if between:
            between()
        self.env.sync(60)
        with self.ref.lock:
            self.ref._start_turn(0, self.clock(), [{"type": "timer"}])
        return self.ref.wait(0, 0)

    def test_wake_scripts_write_the_wake_text_in_name_order(self):
        self.box.answers["b_vp"] = {"ok": True, "report": "VP 都在手里"}
        self.box.answers["a_front"] = lambda req: {"ok": True, "report": f"前沿：{len(req['input']['contacts'])} 个接触，"
                                                                         f"{len(req['events'])} 条新事件"}
        self.put("b_vp", {"on": "wake"})
        self.put("a_front", {"on": "wake"})
        self.put("m", {"on": "minute"})
        w = self.next_wake(lambda: self.ref.emit(0, "combat", 30, unit="Alpha Platoon", kind="fire", n=1, text="x"))
        self.assertTrue(w["woke"])
        self.assertEqual(w["wake_text"], "前沿：0 个接触，1 条新事件\n\nVP 都在手里")
        self.assertNotIn("summary", w)                         # the player's own text instead of the default summary
        self.assertEqual(w["reasons"], [{"type": "timer"}])
        wake_reqs = [r for r in self.box.requests if r["trigger"] == "wake"]
        self.assertEqual([r["name"] for r in wake_reqs], ["a_front", "b_vp"])
        self.assertEqual([e["type"] for e in wake_reqs[0]["events"]], ["combat"])   # the events since the last wake
        self.assertIn("units", wake_reqs[0]["input"])
        self.assertNotIn("wake", [r["trigger"] for r in self.box.requests if r["name"] == "m"])
        self.assertEqual([r.get("wake_scripts") for r in self.env.log("wakes") if r.get("kind") == "wake"][-2:], [None, True])

    def test_a_failed_wake_script_adds_a_line_and_all_failed_gives_the_summary(self):
        self.box.answers["a"] = {"ok": False, "error": "a:3: attempt to index a nil value"}
        self.box.answers["b"] = {"ok": True, "report": "我的摘要"}
        self.put("a", {"on": "wake"})
        self.put("b", {"on": "wake"})
        w = self.next_wake()
        self.assertEqual(w["wake_text"], "我的摘要")
        self.assertEqual(w["wake_errors"], ["[唤醒脚本 a 出错：a:3: attempt to index a nil value]"])
        self.box.answers["b"] = {"ok": False, "error": "boom"}
        w = self.next_wake()
        self.assertNotIn("wake_text", w)
        self.assertIn("game", w["summary"])                    # all failed: the default summary as before
        self.assertEqual(len(w["wake_errors"]), 2)
        self.assertIn("脚本 b", w["wake_errors"][1])

    def test_no_wake_script_no_wake_text_and_none_before_the_game(self):
        w = self.next_wake()
        self.assertNotIn("wake_text", w)
        self.assertIn("summary", w)
        self.box.answers["d"] = {"ok": True, "report": "x"}
        self.put("d", {"on": "wake"})
        self.ref.turn_end(0, {"turn_id": self.ref.turn_id[0]})
        with self.ref.lock:
            self.ref._start_turn(0, self.clock(), [{"type": "prematch"}], prematch=True)
        self.assertNotIn("wake_text", self.ref.wait(0, 0))
        self.assertEqual([r for r in self.box.requests if r["trigger"] == "wake"], [])

    def test_script_test_of_a_wake_script_shows_the_wake_text(self):
        self.box.answers["digest"] = {"ok": True, "report": "看这里"}
        self.put("digest", {"on": "wake"})
        out = self.ref.script_test(0, {"name": "digest"})
        self.assertEqual((out["trigger"], out["report"]), ("wake", "看这里"))
        self.assertEqual(self.box.requests[-1]["trigger"], "wake")

    def test_alarm_becomes_an_event_and_wakes_after_the_window(self):
        self.box.answers["watch"] = {"ok": True, "alarm": "enemy at the gates"}
        self.put("watch")
        self.ref.turn_end(0, {"turn_id": self.ref.turn_id[0]})
        self.env.sync(60)
        self.env.sync(95)
        self.assertIn("commander", self.ref.agents[0])
        alarm = [e for e in self.ref.events.items[0] if e["type"] == "script_alarm"][0]
        self.assertEqual((alarm["script"], alarm["text"]), ("watch", "enemy at the gates"))

    def test_three_overruns_in_a_row_switch_the_script_off_and_wake_the_commander(self):
        self.box.answers["hog"] = {"ok": False, "limit": True, "instructions": 2_000_000, "error": "instruction limit"}
        self.put("hog")
        self.ref.turn_end(0, {"turn_id": self.ref.turn_id[0], "subscriptions": []})    # no subscription at all
        for sec in (60, 120):
            self.env.sync(sec)
        self.assertTrue(self.ref.scripts.defs[0]["hog"]["enabled"])
        self.env.sync(180)
        self.assertFalse(self.ref.scripts.defs[0]["hog"]["enabled"])
        n = len(self.box.requests)
        self.env.sync(215)
        self.assertEqual(len(self.box.requests), n)                          # no longer called
        self.assertIn("commander", self.ref.agents[0])                       # woken though nothing is subscribed
        self.assertEqual([r["event"] for r in self.env.log("scripts") if r.get("event") == "disabled"], ["disabled"])

    def test_a_good_run_resets_the_strikes(self):
        answers = iter([{"ok": False, "limit": True}, {"ok": False, "limit": True}, {"ok": True},
                        {"ok": False, "limit": True}, {"ok": False, "limit": True}])
        self.box.answers["w"] = lambda req: next(answers)
        self.put("w")
        for i in range(1, 6):
            self.env.sync(60 * i)
        self.assertTrue(self.ref.scripts.defs[0]["w"]["enabled"])
        self.assertEqual(self.ref.scripts.defs[0]["w"]["strikes"], 2)

    def test_a_void_call_changes_nothing(self):
        self.box.answers["w"] = {"ok": False, "limit": True, "commands": [HOLD], "persist": {"x": 1}, "alarm": "boo"}
        self.put("w")
        self.env.sync(60)
        self.assertEqual(self.ref.queue.cmds, {})
        self.assertEqual(self.ref.scripts.defs[0]["w"]["persist"], {})
        self.assertEqual([e for e in self.ref.events.items[0] if e["type"] == "script_alarm"], [])

    def test_side_budget_per_game_minute(self):
        for n in ("a", "b", "c"):
            self.box.answers[n] = {"ok": True, "instructions": 6_000_000}
            self.put(n)
        self.env.sync(60)
        self.assertEqual([r["name"] for r in self.box.requests], ["a", "b"])  # 12M used: the third waits
        self.assertTrue(any(r.get("event") == "skipped_minute_budget" for r in self.env.log("scripts")))
        self.env.sync(120)
        self.assertEqual([r["name"] for r in self.box.requests][2:], ["a", "b"])

    def test_oversize_persist_table_is_dropped(self):
        self.box.answers["w"] = {"ok": True, "persist": {"blob": "x" * (rules.SCRIPT_PERSIST_BYTES + 10)}}
        self.put("w")
        self.env.sync(60)
        self.assertEqual(self.ref.scripts.defs[0]["w"]["persist"], {})

    def test_without_a_sandbox_scripts_are_stored_but_never_run(self):
        env = Env(self.tmp.name + "/nosandbox")
        env.sync(0)
        env.wake(0)
        env.ref.scripts_api(0, "PUT", "w", {"source": "return 1"})
        env.sync(60)
        self.assertEqual(env.ref.scripts.defs[0]["w"]["runs"], 0)
        env.ref.close()

    def test_compute_needs_the_sandbox_and_is_limited_to_60_per_side_and_game(self):
        with self.assertRaises(ApiError) as cm:
            self.ref.compute(0, {"code": "return 1"})
        self.assertEqual(cm.exception.code, 501)
        calls = []

        def runner(code, input=None, cpu_s=10, mem_mb=256):
            calls.append((code, input, cpu_s, mem_mb))
            return {"ok": True, "result": 7, "cpu_ms": 100, "ms": 120, "log": ["hi"]}
        self.ref.compute_runner = runner
        self.env.sync(10)
        for _ in range(rules.COMPUTE_JOBS):
            self.assertEqual(self.ref.compute(0, {"code": "return 1", "input": {"a": 1}})["result"], 7)
        with self.assertRaises(ApiError) as cm:
            self.ref.compute(0, {"code": "return 1"})
        self.assertEqual(cm.exception.code, 429)
        self.assertEqual(calls[0], ("return 1", {"a": 1}, 10, 256))
        self.env.wake(1)
        self.assertTrue(self.ref.compute(1, {"code": "return 1"})["ok"])         # the other side's count is its own
        rec = [r for r in self.env.log("scripts") if r.get("event") == "compute"][0]
        self.assertEqual((rec["cpu_s"], rec["log"], rec["prematch"]), (0.1, ["hi"], False))

    def test_prematch_compute_has_no_task_limit_but_two_hours_of_cpu(self):
        env = Env(self.tmp.name + "/pre", compute_runner=lambda code, input=None, cpu_s=10, mem_mb=256:
                  {"ok": True, "cpu_ms": 5_000_000})                             # each task reports 5000 s
        env.ref.phase = "Pregame"
        env.sync(0, "Pregame")
        env.wake(0)
        for _ in range(2):
            self.assertTrue(env.ref.compute(0, {"code": "x"})["ok"])             # 10000 s
        with self.assertRaises(ApiError) as cm:
            env.ref.compute(0, {"code": "x"})                                    # 7200 s is the budget
        self.assertEqual(cm.exception.code, 429)
        self.assertEqual(env.ref.compute_count[0], 0)
        env.ref.close()

    def test_limit_kind_and_printed_lines_go_to_the_scripts_log(self):
        self.box.answers["w"] = {"ok": False, "limit": True, "limit_kind": "memory", "log": ["a", "b"],
                                 "error": "not enough memory"}
        self.put("w")
        self.env.sync(60)
        rec = [r for r in self.env.log("scripts") if r.get("event") == "over_limit"][0]
        self.assertEqual((rec["limit_kind"], rec["log"]), ("memory", ["a", "b"]))

    def test_close_closes_the_runner(self):
        closed = []

        class Box(FakeSandbox):
            def close(self):
                closed.append(1)
        env = Env(self.tmp.name + "/closing", script_runner=Box())
        env.ref.close()
        self.assertEqual(closed, [1])


if __name__ == "__main__":
    unittest.main()
