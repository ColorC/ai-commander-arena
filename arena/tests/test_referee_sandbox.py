"""The referee with the real Lua sandbox (arena/sandbox, started the way __main__ does): scripts and compute tasks."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.referee import rules  # noqa: E402
from arena.referee.__main__ import sandbox  # noqa: E402
from arena.sandbox import runner  # noqa: E402
from arena.tests.fakegame import Env  # noqa: E402

HOLD = 'submit({kind = "orders", unit = "Bravo Platoon", orders = {{orders = "Hold"}}})'


@unittest.skipUnless(os.path.exists(runner.lua_exe()), "lua.exe not built (fpc/native/build.sh)")
class RealSandbox(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        scripts, compute = sandbox(self.tmp.name)
        self.assertIsNotNone(scripts)
        self.env = Env(self.tmp.name, script_runner=scripts, compute_runner=compute)
        self.addCleanup(self.env.ref.close)
        self.ref = self.env.ref
        self.env.sync(0)
        self.env.wake(0)

    def put(self, name, source, trigger=None):
        body = {"source": source}
        if trigger:
            body["trigger"] = trigger
        return self.ref.scripts_api(0, "PUT", name, body)

    def scripts_log(self, **match):
        return [r for r in self.env.log("scripts") if all(r.get(k) == v for k, v in match.items())]

    def test_a_minute_script_queues_commands_keeps_memory_and_logs_its_prints(self):
        self.put("watch", 'memory.runs = (memory.runs or 0) + 1\nprint("run", memory.runs, #view.contacts_in_view)\n' + HOLD)
        self.env.sync(60)
        self.env.sync(120)
        self.assertEqual(self.ref.scripts.defs[0]["watch"]["persist"], {"runs": 2})
        cmds = [c for c in self.ref.queue.cmds.values() if c["source"] == "script:watch"]
        self.assertEqual(sorted(c["status"] for c in cmds), ["queued", "superseded"])
        rec = self.scripts_log(script="watch", trigger="minute")
        self.assertEqual([r["log"] for r in rec], [["run 1 0"], ["run 2 0"]])
        self.assertTrue(all(r["ok"] for r in rec))

    def test_a_script_cannot_see_the_enemy_or_the_machine(self):
        self.put("probe", 'print(tostring(io), tostring(os), tostring(require), tostring(view.enemy_units))\n'
                          'local n = {} for _, u in ipairs(view.units) do n[#n + 1] = u.name end print(table.concat(n, "|"))')
        self.env.sync(60)
        log = self.scripts_log(script="probe")[0]["log"]
        self.assertEqual(log[0], "nil nil nil nil")
        own = {n for n, sd in self.ref.world.side_of.items() if sd == 0}
        self.assertEqual(set(log[1].split("|")), own)                     # view.units: own units only
        self.assertNotIn("Red Tank Company", str(self.ref.views.render(0, "summary")))

    def test_a_runaway_script_is_cut_off_three_times_then_switched_off_and_the_commander_woken(self):
        self.put("spin", "while true do end")
        self.ref.turn_end(0, {"turn_id": self.ref.turn_id[0], "subscriptions": []})
        for sec in (60, 120, 180):
            self.env.sync(sec)
        rec = self.scripts_log(script="spin", event="over_limit")
        self.assertEqual(len(rec), 3)
        self.assertIn(rec[0]["limit_kind"], ("instructions", "time"))
        self.assertFalse(self.ref.scripts.defs[0]["spin"]["enabled"])
        self.env.sync(215)
        self.assertIn("commander", self.ref.agents[0])
        before = len(self.scripts_log(script="spin"))
        self.env.sync(240)
        self.assertEqual(len(self.scripts_log(script="spin")), before)

    def test_the_sides_have_their_own_hosts_and_a_script_error_is_not_a_limit(self):
        self.put("broken", "error('boom')")
        self.env.sync(60)
        rec = self.scripts_log(script="broken")[0]
        self.assertFalse(rec["ok"])
        self.assertIn("boom", rec["error"])
        self.assertNotIn("event", rec)
        self.assertTrue(self.ref.scripts.defs[0]["broken"]["enabled"])

    def test_compute_returns_a_result_and_stops_a_hog(self):
        self.env.sync(10)
        r = self.ref.compute(0, {"code": "local s = 0\nfor i = 1, input.n do s = s + i end\nprint('sum')\nreturn s",
                                 "input": {"n": 100}})
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["result"], r["log"]), (5050, ["sum"]))
        hog = self.ref.compute(0, {"code": "local t = {}\nwhile true do t[#t + 1] = ('x'):rep(1024 * 1024) end"})
        self.assertFalse(hog["ok"])
        self.assertEqual(hog["limit_kind"], "memory")
        rec = self.scripts_log(event="compute")
        self.assertEqual([r["ok"] for r in rec], [True, False])
        self.assertEqual(rec[1]["limit_kind"], "memory")
        self.assertEqual(self.ref.compute_count[0], 2)

    def test_close_stops_the_host_processes(self):
        self.put("watch", HOLD)
        self.env.sync(60)
        hosts = self.ref.scripts.runner.hosts
        procs = [h.host.proc for h in hosts.values() if h.host is not None]
        self.assertTrue(procs and all(p.poll() is None for p in procs))
        self.ref.close()
        self.assertTrue(all(p.poll() is not None for p in procs))
        self.assertTrue(all(h.host is None for h in hosts.values()))
        self.assertEqual(rules.COMPUTE_JOBS, 60)


if __name__ == "__main__":
    unittest.main()
