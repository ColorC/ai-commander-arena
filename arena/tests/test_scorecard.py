"""The scorecard (arena/harness/scorecard.py, materials/21 section 6): a finished game set among the scenario's
reference games, as facts; and how the player reads it (exercise show / exercise list)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from arena.agent import cli, tools  # noqa: E402
from arena.harness import scorecard  # noqa: E402

LIB = os.path.join(ROOT, "arena", "library")
# the two baseline games of 11-01 (The Armored Fist, side 0 against the game's AI)
KIMI = {"result": "Decisive Loss", "vp_ratio_pct": 28.0, "starting_ratio_pct": 56.0, "survived_vp": 12531.0,
        "locations_vp": 0.0, "inflicted_vp": 13906.0}
DEEPSEEK = {"result": "Decisive Win", "vp_ratio_pct": 74.0, "starting_ratio_pct": 56.0, "survived_vp": 35169.0,
            "locations_vp": 4500.0, "inflicted_vp": 27291.0}
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Card(unittest.TestCase):
    def test_a_loss_below_every_reference(self):
        c = scorecard.card(KIMI, "the-armored-fist", 0)
        self.assertEqual(c["you"]["kept_pct"], 29.6)
        self.assertEqual(c["you"]["exchange"], 0.47)
        self.assertEqual(c["you"]["enemy_left_pct"], 64.2)
        self.assertIn("高于 0 局，低于 4 局", c["text"])
        self.assertIn("本方 29.6%（触发）", c["text"])
        self.assertEqual([g["reached"] for g in c["goals"]], [False] * 5)
        # the ladder runs low to high: the computer's mean (46.3) comes before doing nothing (49)
        self.assertEqual([g.get("above") for g in c["goals"][:3]], [46.3, 49, 63])

    def test_a_decisive_win_by_sudden_death_misses_the_top_rung(self):
        c = scorecard.card(DEEPSEEK, "the-armored-fist", 0)
        self.assertEqual(c["you"]["exchange"], 3.81)
        self.assertIn("敌方 29.8%（触发）", c["text"])
        self.assertEqual([g["reached"] for g in c["goals"]], [True, True, True, True, False])
        self.assertIn("人类中位 106.5", c["text"])

    def test_no_card_without_references_or_result(self):
        self.assertIsNone(scorecard.card(KIMI, "alamo-at-soest", 0))
        self.assertIsNone(scorecard.card({"result": "x"}, "the-armored-fist", 0))

    def test_head_to_head_has_no_ladder_against_the_ai(self):
        c = scorecard.card(DEEPSEEK, "the-armored-fist", 0, vs_ai=False)
        self.assertNotIn("goals", c)
        self.assertIn("对另一位指挥官", c["text"])

    def test_force_curve_and_the_steepest_hour(self):
        # 02:00 start; own force falls 100 -> 90 by 03:00, then to 50 between 03:30 and 04:30
        own = lambda sec: 100 - 10 * min(sec, 3600) / 3600 - (40 * min(max(sec - 5400, 0), 3600) / 3600)  # noqa: E731
        rows = [{"sec": s, "force_pct": {"0": round(own(s), 1), "1": 100.0}} for s in range(0, 4 * 3600 + 60, 60)]
        c = scorecard.card(KIMI, "the-armored-fist", 0, timeline=rows)
        hours = [h["clock"] for h in c["curve"]["hourly"]]
        self.assertEqual(hours, ["02:00", "03:00", "04:00", "05:00", "06:00"])
        w = c["curve"]["steepest_own_hour"]
        self.assertEqual((w["from"], w["to"], w["drop"]), ("03:30", "04:30", 40.0))
        self.assertIn("本方掉得最快的一小时：03:30–04:30", c["text"])

    def test_scenario_key_by_name(self):
        self.assertEqual(scorecard.scenario_key("The Armored Fist"), "the-armored-fist")
        self.assertIsNone(scorecard.scenario_key("No Such Scenario"))


# a game with two seats shows side 0's end screen whoever played it (training p0-1 of 11-02, its numbers)
SCREEN0 = {"commander": "1st Bde", "result": "Tactical Win", "vp_ratio_pct": 50.0, "starting_ratio_pct": 56.0,
           "survived_vp": 37202.0, "locations_vp": 500.0, "inflicted_vp": 7366.0, "stars": "4/7", "combat_ready_pct": 87.0,
           "force_points": 42337.0, "enemy_points": 38893.0}


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class View(unittest.TestCase):
    def test_the_other_sides_end_screen_is_turned_round(self):
        v = scorecard.own_view(SCREEN0, "the-armored-fist", 1)
        self.assertEqual((v["result"], v["vp_ratio_pct"], v["starting_ratio_pct"]), ("Tactical Loss", 50.0, 44.0))
        # kept = 38893 - 7366 inflicted by side 0; inflicted = 42337 - 37202 side 0 kept; cells = 6500 - 500
        self.assertEqual((v["survived_vp"], v["inflicted_vp"], v["locations_vp"]), (31527.0, 5135.0, 6000.0))
        self.assertEqual((v["commander"], v["end_screen_of"], v["derived"]), ("7. PD", 0, True))
        self.assertNotIn("stars", v)
        self.assertEqual(scorecard.own_view(dict(SCREEN0, result="Contested Result"), "the-armored-fist", 1)["result"],
                         "Contested Result")

    def test_own_or_unknown_view_is_as_is(self):
        self.assertEqual(scorecard.own_view(SCREEN0, "the-armored-fist", 0), SCREEN0)
        self.assertEqual(scorecard.own_view(KIMI, "the-armored-fist", 1), KIMI)       # no commander
        self.assertEqual(scorecard.view_side({"commander": "10.MSP"}, "quietly-flows-the-donau"), 1)
        self.assertIsNone(scorecard.view_side({"commander": "1st Bde"}, None))

    def test_each_side_takes_its_own_screen_and_fills_what_was_cut_off(self):
        # p1-1 as tools/fpc_endscreen.py now gives it: Battle Over side 0's, the information center side 1's
        bo = {"commander": "1st Bde", "result": "Marginal Win", "vp_ratio_pct": 68.0, "starting_ratio_pct": 56.0,
              "survived_vp": 29921.0, "locations_vp": 6500.0, "inflicted_vp": 21474.0}
        ic = {"commander": "7. PD (-)", "result": "Marginal Loss", "starting_ratio_pct": 44.0, "survived_vp": 17419.0,
              "inflicted_vp": 12416.0, "vp_share_pct": 32.0, "awarded_vp_pct": 32.4, "force_points": 38893.0}
        official = dict(bo, views={"battle-over": bo, "information-center": ic})
        v = scorecard.own_view(official, "the-armored-fist", 1)
        self.assertEqual((v["commander"], v["vp_ratio_pct"], v["survived_vp"], v["locations_vp"]), ("7. PD (-)", 32.0, 17419.0, 0.0))
        self.assertEqual((v.get("derived"), v["filled"]), (None, ["locations_vp"]))   # the share is its own page's
        self.assertNotIn("views", v)
        c = scorecard.card(official, "the-armored-fist", 1, vs_ai=False)
        self.assertEqual((c["view"], c["result"], c["share"], c["you"]["exchange"]), ("own", "Marginal Loss", 32.0, 0.58))
        self.assertIn("守住 VP 格在本方结算页上被截掉，由对方那页换算", c["text"])
        self.assertNotIn("份额、", c["text"])
        v0 = scorecard.own_view(official, "the-armored-fist", 0)
        self.assertEqual((v0["vp_ratio_pct"], v0["vp_share_pct"], v0["locations_vp"]), (68.0, 68.0, 6500.0))

    def test_a_cut_off_name_still_tells_the_side(self):
        # p1-3: Battle Over cut off to "commanding 1st", the information center (side 1's) spaced "7.PD(-)"
        self.assertEqual(scorecard.view_side({"commander": "1st"}, "the-armored-fist"), 0)
        self.assertEqual(scorecard.view_side({"commander": "7.PD(-)"}, "the-armored-fist"), 1)
        self.assertIsNone(scorecard.view_side({"commander": "1"}, "the-armored-fist"))     # too little to tell
        bo = {"commander": "1st", "inflicted_vp": 16648.0, "locations_vp": 6500.0, "vp_ratio_pct": 63.0}
        ic = {"commander": "7.PD(-)", "inflicted_vp": 11291.0, "locations_vp": 0.0, "survived_vp": 22245.0,
              "vp_share_pct": 37.0}
        official = dict(bo, views={"battle-over": bo, "information-center": ic})
        v = scorecard.own_view(official, "the-armored-fist", 1)
        # both result sentences cut off: 37% is side 1's Marginal Loss (28-38) in the scenario's table
        self.assertEqual((v["result"], v["result_from_share"], v["vp_ratio_pct"], v["survived_vp"], v["inflicted_vp"], v["locations_vp"]),
                         ("Marginal Loss", True, 37.0, 22245.0, 11291.0, 0.0))
        self.assertIn("结果按份额对照本图本方的等级表得出", scorecard.card(official, "the-armored-fist", 1, vs_ai=False)["text"])
        # side 0's 63% is a Marginal Win (63-73), 56% Contested (50-62)
        self.assertEqual(scorecard.own_view(official, "the-armored-fist", 0)["result"], "Marginal Win")
        self.assertEqual(scorecard.level_of("the-armored-fist", 0, 56.0), "Contested Result")
        self.assertIsNone(scorecard.level_of(None, 0, 56.0))

    def test_a_record_before_views_from_side_1(self):
        # the record posted for p1-1: Battle Over's fields with the information center's (side 1's) merged in
        legacy = {"commander": "1st Bde", "inflicted_vp": 21474.0, "survived_vp": 29921.0, "starting_ratio_pct": 56.0,
                  "result": "Marginal Win", "force_points": 38893.0, "awarded_vp_pct": 32.4, "vp_share_pct": 32.0}
        v = scorecard.own_view(legacy, "the-armored-fist", 1)
        self.assertEqual((v["result"], v["vp_ratio_pct"], v["survived_vp"], v["inflicted_vp"], v["force_points"]),
                         ("Marginal Loss", 32.0, 17419.0, 12416.0, 38893.0))
        self.assertTrue(v["derived"])
        self.assertIsNone(scorecard.own_view(legacy, "the-armored-fist", 0).get("vp_ratio_pct"))   # not side 1's share

    def test_the_card_of_side_1_from_side_0s_screen(self):
        c = scorecard.card(SCREEN0, "the-armored-fist", 1, vs_ai=False)
        self.assertEqual((c["view"], c["result"], c["share"]), ("derived", "Tactical Loss", 50.0))
        self.assertEqual((c["you"]["kept_pct"], c["you"]["cells_vp"], c["you"]["inflicted_vp"]), (81.1, 6000.0, 5135.0))
        self.assertIn("只读到 0 方视角的结算页", c["text"])
        self.assertEqual(scorecard.card(SCREEN0, "the-armored-fist", 0)["view"], "own")

    def test_recard_keeps_new_cards_and_makes_old_ones_again(self):
        new = scorecard.card(SCREEN0, "the-armored-fist", 1, vs_ai=False)
        self.assertIs(scorecard.recard(new, SCREEN0, "the-armored-fist", 1, vs_ai=False), new)
        curve = {"hourly": [{"clock": "02:00", "sec": 0, "you": 100.0, "enemy": 100.0}], "steepest_own_hour": None}
        old = {"side": 1, "share": 50.0, "result": "Tactical Win", "text": "成绩单：…", "curve": curve}   # before views
        c = scorecard.recard(old, SCREEN0, "the-armored-fist", 1, vs_ai=False)
        self.assertEqual((c["view"], c["result"], c["curve"]), ("derived", "Tactical Loss", curve))
        self.assertIn("兵力走势（裁判估算，本方/敌方 %）：02:00 100/100", c["text"])


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class ExerciseCommands(unittest.TestCase):
    REQ = {"type": "full", "opponent": "ai", "play_side": 0, "scenario": "The Armored Fist"}

    def cli(self, rows):
        c = cli.Cli(None, tools.Library(LIB))
        c.get = lambda path, query=None: {"exercises": rows, "quota": {"full_used": 1}}
        return c

    def test_show_puts_the_scorecard_first(self):
        card = scorecard.card(KIMI, "the-armored-fist", 0)
        rows = [{"id": "p0-1", "status": "done", "request": self.REQ, "report": {"official": KIMI, "scorecard": card}}]
        out = self.cli(rows).run("exercise show p0-1")
        self.assertFalse(out.error, out.text)
        self.assertTrue(out.text.startswith("成绩单：The Armored Fist"))
        self.assertIn("详细记录", out.text)

    def test_list_gives_the_official_result(self):
        rows = [{"id": "p0-1", "status": "done", "request": self.REQ, "report": {"official": KIMI}},
                {"id": "p0-2", "status": "queued", "request": self.REQ}]
        out = self.cli(rows).run("exercise list")
        self.assertIn("Decisive Loss 28.0%", out.text)
        self.assertIn("exercise show <编号>", out.text)

    def test_side_1_of_a_two_seat_game_sees_its_own_result(self):
        req = dict(self.REQ, opponent="self", play_side=1)
        old = {"side": 1, "share": 50.0, "result": "Tactical Win", "text": "成绩单：旧", "curve": None}
        rows = [{"id": "p1-1", "status": "done", "request": req, "report": {"official": SCREEN0, "scorecard": old}}]
        out = self.cli(rows).run("exercise list")
        self.assertIn("Tactical Loss 50.0%（由对方视角换算）", out.text)
        out = self.cli(rows).run("exercise show p1-1")
        self.assertIn("官方结果 Tactical Loss", out.text)
        self.assertIn("保住部队 81.1%", out.text)
        self.assertIn("详细记录（official 是结算页原文，对方视角）", out.text)
        self.assertNotIn("成绩单：旧", out.text)

    def test_no_card_for_a_small_experiment(self):
        rows = [{"id": "p0-3", "status": "done", "request": dict(self.REQ, type="small"), "report": {"official": KIMI}}]
        self.assertFalse(self.cli(rows).run("exercise show p0-3").text.startswith("成绩单"))


if __name__ == "__main__":
    unittest.main()
