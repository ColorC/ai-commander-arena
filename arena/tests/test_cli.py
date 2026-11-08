"""The players' command line `arena` (arena/agent/cli.py): parsing and synonyms, unit names, hexes, help for every noun
and verb, order lint (unknown unit, deploy outside the starting area, blind fire), fire reports from the side's own
ammunition, scripts, score, docs, and the old tool names of scripted fake players. Against the real referee over
HTTP with the synthetic mini world (arena/tests/fakegame.py); no game."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.agent import cli, tools  # noqa: E402
from arena.harness import mcp_server  # noqa: E402
from arena.referee import rules  # noqa: E402
from arena.referee.views import clock_str  # noqa: E402
from arena.sandbox import runner  # noqa: E402
from arena.tests import fakegame as fg  # noqa: E402

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "library")
# own unit names of Alamo at Soest (side 0), as the game spells them
ALAMO = ["1-B-1-200 ADA", "1/A/TF Alamo", "1/AA", "1/D/TF Alamo", "1/Sct", "2/A/TF Alamo", "2/D/TF Alamo", "2/Sct", "3/A/TF Alamo",
         "3/AA", "3/Sct", "4/A/TF Alamo", "A/TF Alamo", "B 1-149 Avn", "B 3-133 FA", "CEV", "D/TF Alamo", "Eng", "FARP",
         "Mtr/TF Alamo", "Sct", "TF Alamo"]
MORTAR = "Mtr Section"
from arena.tests import HAVE_MANUALS, HAVE_SCENARIOS, NO_MANUALS, NO_SCENARIOS  # noqa: E402


def mortar(rounds=68):
    u = fg.mini_unit(0, 1420, MORTAR, subs=2, order="On Call")
    u["s"]["icon_type"] = "SPArty"
    for x in u["u"]:
        x.update(name="M106A2", type="MORTH")
        x["ammo"] = [{"ammo_id": "MTR1_HE", "type": "HE", "rounds": str(rounds), "max_rounds": "68"},
                     {"ammo_id": "MTR1_SMK", "type": "Smoke", "rounds": "10", "max_rounds": "10"}]
    return u


class CliCase(unittest.TestCase):
    phase = "Executing"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.env = fg.Env(self.tmp)
        self.addCleanup(self.env.ref.close)
        self.env.units[MORTAR] = mortar()
        self.env.sync(0, phase=self.phase)
        self.srv, self.tokens = fg.serve(self.env.ref, self.tmp)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.env.wake(0)
        self.cli = self.make()

    def make(self, side=0, sub=False):
        ref = tools.Referee(f"http://127.0.0.1:{self.srv.server_address[1]}", self.tokens[f"side{side}"])
        return cli.Cli(ref, tools.Library(LIB), sub=sub)

    def ok(self, cmd, input=None):
        out = self.cli.run(cmd, input)
        self.assertFalse(out.error, f"{cmd}: {out.text}")
        return out.text

    def err(self, cmd, input=None):
        out = self.cli.run(cmd, input)
        self.assertTrue(out.error, f"{cmd}: {out.text}")
        return out.text


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Parsing(CliCase):
    def test_tokens_options_and_the_leading_arena(self):
        self.assertEqual(cli.tokenize('arena unit show "1/A/TF Alamo"'), ["unit", "show", "1/A/TF Alamo"])
        self.assertEqual(cli.tokenize("order add 'unbalanced"), ["order", "add", "'unbalanced"])
        pos, opts = cli.split_opts(["doc", "show", "fm01", "--from", "5", "--lines=3", "--json", "-h"])
        self.assertEqual((pos, opts), (["doc", "show", "fm01"], {"from": "5", "lines": "3", "json": True, "help": True}))

    def test_synonyms_default_verbs_and_word_order(self):
        base = self.ok("unit list")
        for same in ("units ls", "unit", "arena unit list", "list units", "unit all"):
            self.assertEqual(self.ok(same), base, same)
        self.assertEqual(self.ok("unit cat alpha"), self.ok("unit show alpha"))
        self.assertEqual(self.ok("unit alpha"), self.ok("unit show alpha"))                # unit-first form
        self.assertIn("接触 0 个", self.ok("contact"))
        self.assertIn("份额", self.ok("score"))
        self.assertIn("命令总览", self.ok("help help"))
        self.assertEqual(self.ok(""), cli.help_text())

    def test_typos_get_did_you_mean(self):
        self.assertIn("你是不是想说：unit？", self.err("uint list"))
        self.assertIn("你是不是想说：order list", self.err("order lsit"))
        self.assertIn("你是不是想说：fire report", self.err("fire reprot"))
        self.assertIn("你是不是想说", self.err("order add alpha mvoe 1713"))                 # order words too
        self.assertIn("你是不是想说：Alpha Platoon", self.err("unit show alpah"))

    def test_json_gives_the_raw_data(self):
        rows = json.loads(self.ok("unit list --json"))["units"]
        self.assertIn(MORTAR, [r["name"] for r in rows])
        self.assertEqual(json.loads(self.ok("score show --json"))["formula_known"], True)

    def test_one_tool_for_both_ways_in(self):
        self.assertEqual(cli.schema()["function"]["name"], "arena")
        self.assertEqual(cli.schema()["function"]["parameters"]["required"], ["cmd"])
        self.assertEqual(mcp_server.mcp_tools(), [cli.mcp_tool()])
        self.assertEqual(mcp_server.mcp_tools(sub=True), [cli.mcp_tool()])


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class UnitNames(CliCase):
    def test_case_prefix_segments_and_substring(self):
        for q, want in (("1/a", "1/A/TF Alamo"), ("1/A", "1/A/TF Alamo"), ("mtr", "Mtr/TF Alamo"), ("tf alamo", "TF Alamo"),
                        ("TF ALAMO", "TF Alamo"), ("sct", "Sct"), ("1/s", "1/Sct"), ("b 3", "B 3-133 FA"), ("133", "B 3-133 FA"),
                        ("avn", "B 1-149 Avn"), ("farp", "FARP"), ("4/a", "4/A/TF Alamo"), ("1/a/tf", "1/A/TF Alamo")):
            self.assertEqual(cli.match_name(q, ALAMO), (want, None), q)

    def test_ambiguity_lists_candidates_and_guesses_nothing(self):
        name, cands = cli.match_name("alamo", ALAMO)
        self.assertIsNone(name)
        self.assertIn("TF Alamo", cands)
        self.assertIn("1/A/TF Alamo", cands)
        self.assertEqual(cli.match_name("1/", ALAMO), (None, ["1/A/TF Alamo", "1/AA", "1/D/TF Alamo", "1/Sct"]))
        self.assertEqual(cli.match_name("nothing", ALAMO), (None, []))

    def test_against_the_referee(self):
        self.assertIn("Alpha Platoon（Tank", self.ok("unit show alp"))
        self.assertIn("Alpha Platoon（Tank", self.ok("unit show Alpha Platoon"))           # a name with a space, unquoted
        text = self.err("unit show platoon")
        self.assertIn("有歧义", text)
        self.assertIn("Alpha Platoon、Bravo Platoon", text)
        self.assertIn("本方没有叫 'nope' 的单位", self.err("unit show nope"))
        self.assertIn("本方没有叫 'red' 的单位", self.err("unit show red"))                  # the enemy's units are not names here
        self.assertIn("已入队", self.ok("order add tf alamo move 1320"))                      # two words of a name, then the order
        self.assertEqual(self.env.ref.queue.cmds["s0-c0001"]["unit"], "TF Alamo")


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Hexes(CliCase):
    def test_hex_forms(self):
        for toks, want in ((["2118"], (2118, 1)), (["21,18"], (2118, 1)), (["21", "18"], (2118, 2)), (["21,", "18"], (2118, 2)),
                           (["(21,18)"], (2118, 1)), (["21.18"], (2118, 1)), (["21/18"], (2118, 1)), (["0412"], (412, 1)),
                           (["412"], (412, 1)), (["c12"], (None, 0)), (["move"], (None, 0))):
            self.assertEqual(cli.parse_hex(toks), want, toks)
        self.assertEqual([cli.parse_contact(x) for x in ("c12", "#12", "C7", "12", "contact:3")], [12, 12, 7, None, 3])

    def test_same_answer_for_every_spelling(self):
        base = self.ok("map hex 1713")
        self.assertIn("1713", base)
        for same in ("map hex 17,13", "map hex 17 13", "map hex (17,13)"):
            self.assertEqual(self.ok(same), base, same)
        self.ok("order add alpha hasty 17 13 then hold")
        body = self.env.ref.queue.cmds["s0-c0001"]["display"]
        self.assertEqual(body["orders"], [{"orders": "Move Hasty", "destination_location": 1713}, {"orders": "Hold"}])


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Help(CliCase):
    def test_every_noun_and_verb_has_help_with_an_example(self):
        overview = cli.help_text()
        for noun, d in cli.NOUNS.items():
            self.assertIn(f"  {noun:<9}", overview)
            self.assertEqual(self.ok(f"{noun} --help"), cli.help_text(noun))
            self.assertEqual(self.ok(f"help {noun}"), cli.help_text(noun))
            self.assertIn("例子", cli.help_text(noun))
            for verb, h in d["verbs"].items():
                text = cli.help_text(noun, verb)
                self.assertTrue(h["use"] and h["usage"] and h["ex"] and h["err"], (noun, verb))
                self.assertIn("例子：\n  arena " + h["ex"][0], text)
                self.assertIn("常见错误：", text)
                self.assertEqual(self.ok(f"{noun} {verb} --help"), text)
                self.assertEqual(self.ok(f"help {noun} {verb}"), text)
                self.assertTrue(callable(getattr(self.cli, f"_{noun}_{verb}", None)), (noun, verb))
                for ex in h["ex"]:                                   # the examples speak the grammar they teach
                    w = cli.tokenize(ex.split("（")[0])
                    self.assertEqual(cli.resolve_noun(w[0]), noun, ex)
        self.assertGreaterEqual(sum(1 for line in overview.splitlines() if line.startswith("  arena ")), 5)
        self.assertNotIn("los", json.dumps(cli.NOUNS["map"]["verbs"]))  # what is not built is not offered
        self.assertIn("你是不是想说：add", self.err("help order ad"))

    def test_help_in_place_of_a_verb_and_after_options(self):
        self.assertEqual(self.ok("order add --help"), self.ok("order add -h"))
        self.assertEqual(self.ok("fire --help"), cli.help_text("fire"))


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Lint(CliCase):
    def test_unknown_unit(self):
        self.assertIn("本方没有叫 'ghost' 的单位", self.err("order lint ghost hold"))
        # the referee's own check, for raw JSON that skips the name lookup
        text = self.ok("order lint", '{"kind": "orders", "unit": "Ghost", "orders": [{"orders": "Hold"}]}')
        self.assertIn("不通过", text)
        self.assertIn("错误：", text)
        res = json.loads(self.ok("order lint --json", '{"kind": "orders", "unit": "9 MR", "orders": [{"orders": "Hold"}]}'))
        self.assertFalse(res["ok"])                                    # an enemy unit is not the side's
        self.assertEqual(self.env.ref.queue.streak[0], 0)              # lint never counts as an illegal command
        self.assertEqual(self.env.ref.queue.cmds, {})

    def test_blind_fire_is_a_warning_and_still_queued(self):
        text = self.ok("order lint mtr barrage 2830 rounds=20 mins=5")
        self.assertIn("通过", text)
        self.assertIn("警告：盲射", text)
        text = self.ok("fire add mtr 2830 rounds=20 mins=5")
        self.assertIn("已入队 s0-c0001", text)
        self.assertIn("警告：盲射", text)
        self.assertEqual(self.env.ref.queue.cmds["s0-c0001"]["display"]["orders"][0],
                         {"orders": "Barrage", "target_location": 2830, "barrage_rounds": 20, "barrage_duration_mins": 5,
                          "barrage_type": "HE"})
        self.assertIn("rounds", self.err("fire add mtr 2830"))         # the barrage fields are required
        # a contact in view near the aim point: observed fire, no warning
        self.env.sync(60, lines=[self.env.contact(60, "Red Tank Company", 7, "Detected")])
        self.assertNotIn("盲射", self.ok("order lint mtr barrage 5212 rounds=20 mins=5"))
        self.assertNotIn("盲射", self.ok("order lint mtr barrage c7 rounds=20 mins=5"))
        self.assertIn("警告：盲射", self.ok(f"order lint mtr barrage {5212 - 4 * 100} rounds=20 mins=5"))
        self.assertGreater(rules.FIRE_NEAR_HEXES, 0)

    def test_sop_values_from_the_whitelist(self):
        self.assertIn("已入队", self.ok('unit alpha sop acceptable_losses="do or die" stand_off_range=3'))
        self.assertEqual(self.env.ref.queue.cmds["s0-c0001"]["args"]["sop"]["acceptable_losses"], "Do or Die")
        self.assertIn("not an option", self.err("unit alpha sop acceptable_losses=Heavy"))
        self.assertIn("stand_off_range", self.err("unit alpha sop stand_off_range=99"))
        self.assertIn("整数 0–20", self.ok("unit alpha sop"))

    def test_value_with_spaces_without_quotes(self):
        # start task 11-01: Kimi's first 13 SOP commands were refused for `fire_discipline=Maximum Range` unquoted
        self.assertIn("已入队", self.ok("unit alpha sop fire_discipline=Maximum Range acceptable_losses=Moderate"))
        sop = self.env.ref.queue.cmds["s0-c0001"]["args"]["sop"]
        self.assertEqual((sop["fire_discipline"], sop["acceptable_losses"]), ("Maximum Range", "Moderate"))
        self.assertIn("已入队", self.ok("unit alpha sop resupply_action=Recover to %"))
        self.assertIn("not an option", self.err("unit alpha sop fire_discipline=Maximum Rnage"))   # the whole value is linted
        self.assertIn("要写成 键=值", self.err("unit alpha sop Maximum Range"))                     # no key before it
        self.assertIn("要写成 键=值", self.err("unit alpha sop stand_off_range=3 more"))            # not on a number


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Deploy(CliCase):
    phase = "Setup"

    def test_deploy_outside_the_starting_area(self):
        c0, c1, r0, r1 = self.env.ref.sc.start_areas[0]
        inside, outside = c0 * 100 + r0, (c1 + 2) * 100 + r0
        text = self.ok(f"order lint alpha deploy {outside}")
        self.assertIn("不通过", text)
        self.assertIn(f"{c0}", text)
        self.assertIn("没有入队", self.err(f"order add alpha deploy {outside}"))
        self.assertIn("通过", self.ok(f"order lint alpha deploy {inside}"))
        self.assertIn("已入队", self.ok(f"order add alpha deploy {inside}"))


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Fires(CliCase):
    def shoot(self, sec, rounds):
        self.env.units[MORTAR] = mortar(rounds)
        return self.env.sync(sec)

    def test_ammunition_drop_is_a_fire_report_of_the_own_side_only(self):
        self.env.sync(60)
        self.shoot(120, 60)                                            # 8 rounds per tube, two tubes
        evs = [e for e in self.env.ref.events.since(0) if e["type"] == "fire_report"]
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0]["unit"], evs[0]["rounds"], evs[0]["by_type"], evs[0]["order"]), (MORTAR, 16, {"HE": 16}, "On Call"))
        self.assertEqual(evs[0]["clock"], clock_str(self.env.ref.sc, 120))
        self.assertEqual(evs[0]["called_by"], "fscc")                  # On Call: the game's fire support centre
        self.assertEqual([e for e in self.env.ref.events.since(1) if e["type"].startswith("fire_")], [])
        text = self.ok("fire report")
        self.assertIn(MORTAR, text)
        self.assertIn("16", text)
        self.assertIn("HE", text)
        self.assertIn("游戏火力协调中心自行调用", text)
        self.env.wake(1)
        other = self.make(side=1).run("fire report")
        self.assertFalse(other.error, other.text)
        self.assertNotIn(MORTAR, other.text)                           # the other side learns nothing of it
        self.assertIn("fire_report", self.ok("event list --type fire_report"))

    def test_barrage_nobody_ordered_is_the_fire_support_centre(self):
        # round 4 game 1: the game put an On Call battalion on Barrage itself; no command, no script
        self.env.sync(60)
        u = mortar(60)
        u["o"] = [{"orders": "Barrage", "target_contact": 7}]
        self.env.units[MORTAR] = u
        self.env.sync(120)
        evs = [e for e in self.env.ref.events.since(0) if e["type"] == "fire_report"]
        self.assertEqual((evs[0]["order"], evs[0]["called_by"]), ("Barrage", "fscc"))

    def test_released_barrage_that_does_not_fire_gives_one_reminder(self):
        self.ok("fire add mtr 2830 rounds=20 mins=5")
        rels, _ = self.env.stop(60, 0)
        self.assertEqual(len(rels), 1)
        self.assertIn("已放行、还没开火", self.ok("fire report"))
        for sec in range(120, 60 + rules.FIRE_PENDING_SEC + 180, 60):
            self.env.sync(sec)
        late = [e for e in self.env.ref.events.since(0) if e["type"] == "fire_pending"]
        self.assertEqual(len(late), 1)
        self.assertEqual((late[0]["unit"], late[0]["target_hex"]), (MORTAR, 2830))
        self.assertIn("迟迟没开火", self.ok("fire report"))
        # firing afterwards is reported as usual and ends the wait
        self.shoot(60 + rules.FIRE_PENDING_SEC + 180, 63)
        rep = [e for e in self.env.ref.events.since(0) if e["type"] == "fire_report"][-1]
        self.assertEqual((rep["unit"], rep["rounds"]), (MORTAR, 10))
        self.assertEqual(rep["called_by"], "own_order")               # the released Barrage
        self.assertEqual(len([e for e in self.env.ref.events.since(0) if e["type"] == "fire_pending"]), 1)


    def test_request_names_a_unit_with_spaces_without_quotes(self):
        # start task 11-01: `fire add request enemy c125 requester=HQ PzGrenBtl 111` was refused at 'PzGrenBtl'
        self.ok("fire add request static 2830 target=hard requester=Mtr Section")
        rq = self.env.ref.queue.cmds["s0-c0001"]["args"]["request"]
        self.assertEqual((rq["requester_unit"], rq["location"], rq["target_type"]), (MORTAR, 2830, "hard"))
        self.ok("fire add request static requester=Bravo Platoon 2830 target=soft")  # the hex after the name stays a hex
        rq = self.env.ref.queue.cmds["s0-c0002"]["args"]["request"]
        self.assertEqual((rq["requester_unit"], rq["location"]), ("Bravo Platoon", 2830))


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class ScoreDocsScripts(CliCase):
    def test_score_show_and_explain(self):
        text = self.ok("score show")
        self.assertIn("当前份额估计：", text)
        self.assertIn("存活兵力", text)
        self.assertIn("VP 格合计", text)
        self.assertIn("敌方损失", text)
        self.assertIn("FM01 第 30 节", self.ok("score explain"))

    def test_score_show_marks_contested_cells_and_gives_value_strength(self):
        # start task 11-01, Kimi's second game: a Czech unit sat on 3923 with the West German one from 11:26 and the game
        # gave the cell to the Czechs, while score show still said "本方"; it also said sudden death at 29.1 % of the
        # subunits when the game had the Czechs at 31 % by value
        self.env.units["Alpha Platoon"]["s"]["location"] = 2118                    # own unit on a VP cell
        self.env.units["Red Tank Company"]["s"]["location"] = 2119                 # an enemy next to it, in view
        self.env.sync(120, lines=[self.env.contact(110, "Red Tank Company", 7, "Detected", "3x vehicle(s)")])
        d = json.loads(self.ok("score show --json"))
        hot = [c["hex"] for c in d["vp_cells"] if c.get("contested")]
        self.assertEqual(hot, [2118])
        lo, hi = d["share_range"]
        self.assertLess(lo, d["share"])
        self.assertEqual(hi, d["share"])                                          # 2118 is already the side's
        self.assertEqual(set(d["force_value_pct"]), {"you", "enemy"})
        text = self.ok("score show")
        self.assertIn("双方都在格上或相邻", text)
        self.assertIn("按 VP 价值", text)

    @unittest.skipUnless(HAVE_MANUALS, NO_MANUALS)
    def test_docs_cover_basics_and_the_manuals(self):
        listing = self.ok("doc list")
        self.assertIn("basics", listing)
        self.assertIn("FM01 Game Operations.txt", listing)
        self.assertIn("1: # 基础操作文档", self.ok("doc show basics"))
        self.assertIn("basics.md", self.ok("doc search 盲射"))
        self.assertIn("FM01", self.ok("doc search observed blind fire adjust --max 3"))
        self.assertIn("行 5611-5612", self.ok("doc show fm01 --from 5611 --lines 2"))
        self.assertIn("匹配多个文件", self.err("doc show fm03"))

    def test_script_lint_and_put(self):
        good = 'if trigger == "minute" then submit({kind="orders", unit="Alpha Platoon", orders={{orders="Hold"}}}) end'
        text = self.ok("script lint --on minute", good)
        self.assertIn("通过", text)
        self.assertIn("Alpha Platoon", text)
        bad_name = good.replace("Alpha Platoon", "alpha platoon")
        text = self.ok("script lint", bad_name)
        self.assertIn("不通过", text)
        self.assertIn("你是不是想说 'Alpha Platoon'", text)
        self.assertIn("没有登记", self.err("script put guard", bad_name))
        self.assertIn("trigger", self.ok("script lint --on sometimes", good))
        self.assertIn("已登记脚本 guard", self.ok("script put guard --on orders_phase", good))
        self.assertIn("guard", self.ok("script list"))
        self.assertIn('unit="Alpha Platoon"', self.ok("script show guard"))
        self.assertIn("已删除", self.ok("script rm guard"))

    def test_a_wake_script_is_put_and_its_test_shows_the_wake_text(self):
        seen = []
        self.env.ref.scripts.runner = lambda req: seen.append(req) or {"ok": True, "report": "前沿平静"}
        self.assertIn("已登记脚本 digest", self.ok("script put digest --on wake", 'report("前沿平静")'))
        self.assertEqual(self.env.ref.scripts.defs[0]["digest"]["trigger"], {"on": "wake"})
        text = self.ok("script test digest")
        self.assertIn("唤醒时会显示：\n前沿平静", text)
        self.assertEqual(seen[-1]["trigger"], "wake")
        self.env.ref.scripts.runner = lambda req: {"ok": True}
        self.assertIn("唤醒内容为空", self.ok("script test digest --trigger wake"))

    @unittest.skipUnless(os.path.exists(runner.lua_exe()), "lua.exe not built (fpc/native/build.sh)")
    def test_script_lint_catches_lua_syntax(self):
        text = self.ok("script lint", "if x then")
        self.assertIn("Lua 语法错误", text)
        self.assertIn("没有登记", self.err("script put broken", "local = 1"))


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class SubagentAndTurn(CliCase):
    def test_driver_requests(self):
        out = self.cli.run("turn end", '[{"type": "vp_change"}]')
        self.assertEqual(out.driver, ("end_turn", {"subscriptions": [{"type": "vp_change"}]}))
        self.assertEqual(self.cli.run("agent spawn scout", "look north").driver, ("spawn_subagent", {"name": "scout", "task": "look north"}))
        self.assertEqual(self.cli.run("agent check --wait 20").driver, ("check_subagents", {"wait_s": 20}))
        sub = self.make(sub=True)
        self.assertEqual(sub.run('turn end --report "two tanks"').driver, ("end_turn", {"report": "two tanks"}))
        self.assertTrue(sub.run("agent spawn x", "t").error)
        self.assertTrue(sub.run("turn observe on").error)
        out = self.cli.run("turn observe on")
        self.assertEqual((out.error, out.observe), (False, True))
        self.assertIn("游戏时刻", self.ok("turn status"))
        out = self.cli.run("note put plan.md", "hold the line")
        self.assertEqual(out.note, ("plan.md", "hold the line"))

    def test_turn_over_is_reported(self):
        self.env.ref.turn_end(0, {"turn_id": self.env.ref.turn_id[0]})
        out = self.cli.run("unit list")
        self.assertTrue(out.error and out.turn_over)
        self.assertEqual(out.status, 409)


class Legacy(unittest.TestCase):
    def test_every_old_tool_has_a_command(self):
        for name in tools.ALL:
            self.assertIsNotNone(cli.legacy(name, {}), name)
        self.assertEqual(cli.legacy("get_unit", {"name": "1/A/TF Alamo"}), ("unit show '1/A/TF Alamo' --json", None))
        self.assertEqual(cli.legacy("end_turn", {"report": "ok"}), ("turn end", '{"report": "ok"}'))
        self.assertIsNone(cli.legacy("no_such_tool", {}))


if __name__ == "__main__":
    unittest.main()
