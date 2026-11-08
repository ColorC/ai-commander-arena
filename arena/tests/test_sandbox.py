"""Lua sandbox (arena/sandbox): what scripts can reach, the instruction / time / memory limits, host restart, tasks."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.sandbox import runner  # noqa: E402

LIMITS = {"instructions": 2_000_000, "ms": 200, "memory_mb": 64}


def req(source, persist=None, limits=LIMITS, view=None, events=None):
    return {"side": 0, "name": "t", "source": source, "trigger": "minute", "input": view or {"sec": 60},
            "events": events or [], "persist": persist or {}, "limits": limits}


@unittest.skipUnless(os.path.exists(runner.lua_exe()), "lua.exe not built (fpc/native/build.sh)")
class ScriptHostTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = runner.ScriptHost(mem_mb=64)

    @classmethod
    def tearDownClass(cls):
        cls.host.close()

    def test_commands_alarm_memory(self):
        src = """
          memory.n = (memory.n or 0) + 1
          submit({kind = "orders", unit = "TF Alamo", orders = {{orders = "Move Hasty", destination_hex = 2220}}})
          if view.sec == 60 and events[1] and events[1].type == "new_contact" then alarm("contact " .. events[1].id) end
          print("run", memory.n, 1.5, nil, {})
        """
        r = self.host(req(src, events=[{"type": "new_contact", "id": 125}]))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["commands"], [{"kind": "orders", "unit": "TF Alamo",
                                          "orders": [{"orders": "Move Hasty", "destination_hex": 2220}]}])
        self.assertEqual(r["alarm"], "contact 125")
        self.assertEqual(r["persist"], {"n": 1})
        self.assertEqual(r["log"], ["run 1 1.5 nil table"])
        r2 = self.host(req(src, persist=r["persist"]))
        self.assertEqual(r2["persist"], {"n": 2})
        self.assertIsNone(r2["alarm"])
        self.assertFalse(r["limit"])

    def test_report_is_the_wake_text_and_its_cap_is_silent(self):
        src = """
          report("前沿", #view.contacts, "个接触")
          for _, c in ipairs(view.contacts) do report("c" .. c.contact .. " 在 " .. c.last_pos) end
        """
        r = self.host(dict(req(src, view={"contacts": [{"contact": 12, "last_pos": 2118}, {"contact": 13, "last_pos": 2220}]}),
                           trigger="wake"))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["report"], "前沿 2 个接触\nc12 在 2118\nc13 在 2220")
        self.assertIsNone(self.host(req("return 1"))["report"])                 # no report() call: none
        big = self.host(req('for i = 1, 5000 do report(string.rep("x", 100)) end'))
        self.assertTrue(big["ok"], big)
        self.assertLessEqual(len(big["report"].encode("utf-8")), 64 * 1024 + 5000)
        self.assertNotIn("limit", big["report"])

    def test_nothing_outside_reach(self):
        src = """
          local seen = {}
          for _, n in ipairs({"io", "os", "package", "debug", "require", "load", "loadfile", "dofile", "collectgarbage",
                              "coroutine", "xpcall", "rawprint"}) do
            if _G[n] ~= nil then seen[#seen + 1] = n end
          end
          if string.dump or ("").dump then seen[#seen + 1] = "string.dump" end
          if getmetatable("") ~= nil then seen[#seen + 1] = "string metatable" end
          memory.seen = seen
          string.upper = nil       -- a copy: the next call still has it
        """
        r = self.host(req(src))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["persist"], {"seen": []})
        r = self.host(req("memory.u = string.upper('a') .. ('b'):upper()"))
        self.assertEqual(r["persist"], {"u": "AB"})

    def test_instruction_limit_and_pcall(self):
        for src in ("while true do end",
                    "while true do pcall(function() while true do end end) end",
                    "local function f() return f() end f()"):
            r = self.host(req(src))
            self.assertFalse(r["ok"], src)
            self.assertTrue(r["limit"], (src, r))
            self.assertEqual(r["limit_kind"], "instructions", src)
            self.assertLessEqual(r["instructions"], LIMITS["instructions"] + 1000)

    def test_time_limit_in_lua(self):
        r = self.host(req("while true do end", limits={"instructions": 10 ** 12, "ms": 50, "memory_mb": 64}))
        self.assertEqual(r["limit_kind"], "time", r)
        self.assertLess(r["ms"], 500)

    def test_hung_in_c_is_killed_and_host_restarts(self):
        before = self.host.restarts
        r = self.host(req('string.find(string.rep("a", 3000), ".-.-.-.-.-b")'))
        self.assertEqual(r["limit_kind"], "time", r)
        self.assertFalse(r["ok"])
        r = self.host(req("memory.ok = true"))
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.host.restarts, before + 1)

    def test_memory_limits(self):
        r = self.host(req("local t = {} for i = 1, 1e8 do t[i] = {i} end",
                          limits={"instructions": 10 ** 12, "ms": 5000, "memory_mb": 64}))
        self.assertEqual(r["limit_kind"], "memory", r)
        r = self.host(req('local s = string.rep("x", 15 * 1024 * 1024) local ok = pcall(function() memory.s = s .. s .. s .. s .. s end)',
                          limits={"instructions": 10 ** 12, "ms": 5000, "memory_mb": 64}))
        self.assertEqual(r["limit_kind"], "memory", r)
        r = self.host(req('string.rep("x", 1e9)'))
        self.assertFalse(r["ok"])
        self.assertIn("16 MB", r["error"])
        r = self.host(req("memory.ok = 1"))
        self.assertTrue(r["ok"], r)

    def test_refused_values(self):
        r = self.host(req("setmetatable({}, {__gc = function() while true do end end})"))
        self.assertIn("__gc", r["error"])
        r = self.host(req("memory.f = print"))
        self.assertFalse(r["ok"])
        self.assertIn("function", r["error"])
        r = self.host(req("memory = 5"))
        self.assertIn("memory must stay a table", r["error"])
        r = self.host(req("this is not lua"))
        self.assertFalse(r["ok"])
        self.assertFalse(r["limit"])
        r = self.host(req("error(setmetatable({}, {__tostring = function() while true do end end}))"))
        self.assertEqual(r["error"], "error object of type table")

    def test_strings_survive_round_trip(self):
        r = self.host(req('memory.s = view.s .. "|" .. #view.s', view={"s": "装甲\n\"q\"\\\x1a end"}))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["persist"]["s"], "装甲\n\"q\"\\\x1a end|" + str(len("装甲\n\"q\"\\\x1a end".encode("utf-8"))))


@unittest.skipUnless(os.path.exists(runner.lua_exe()), "lua.exe not built (fpc/native/build.sh)")
class ComputeTest(unittest.TestCase):
    def test_result(self):
        r = runner.compute("local s = 0 for _, h in ipairs(input.hexes) do s = s + h end return {sum = s, n = #input.hexes}",
                           {"hexes": [2118, 2119, 2220]})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["result"], {"sum": 6457, "n": 3})

    def test_cpu_limit(self):
        r = runner.compute("while true do end", cpu_s=1)
        self.assertFalse(r["ok"])
        self.assertEqual(r["limit_kind"], "time", r)

    def test_memory_limit(self):
        r = runner.compute("local t = {} for i = 1, 1e8 do t[i] = tostring(i) end", mem_mb=32)
        self.assertEqual(r["limit_kind"], "memory", r)

    def test_result_size(self):
        r = runner.compute('return string.rep("x", 300000)')
        self.assertFalse(r["ok"])
        self.assertIn("result over", r["error"])


class LuaLiteralTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(runner.to_lua({"a": [1, 2.5, None, True], "b": None}), '{["a"]={1,2.5,nil,true}}')
        self.assertEqual(runner.to_lua(float("inf")), "(1/0)")
        self.assertEqual(runner.to_lua('a"\\\n'), '"a\\"\\\\\\010"')


if __name__ == "__main__":
    unittest.main()
