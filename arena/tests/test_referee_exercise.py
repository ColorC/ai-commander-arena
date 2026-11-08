"""Exercise options of the game referee (materials/10 2.10, arena/DESIGN.md 9): turn pacing, playing against the game's
AI, small experiments, the players' workspaces with stored scripts, and exercise-report.json."""
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.referee import rules  # noqa: E402
from arena.referee.core import ApiError, Referee  # noqa: E402
from arena.referee.world import Scenario  # noqa: E402
from arena.tests import fakegame as fg  # noqa: E402
from arena.tests.fakegame import EnvCase  # noqa: E402

MOVE = {"kind": "orders", "unit": "Alpha Platoon", "orders": [{"orders": "Move Hasty", "destination_location": 1713}]}
BRAVO = {"kind": "orders", "unit": "Bravo Platoon", "orders": [{"orders": "Hold"}]}
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


def cmd_ids(release):
    return re.findall(r'cmd_id="([^"]+)"', release["lua"])


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class TurnPacing(EnvCase):
    env_kw = {"pacing": "turn"}

    def orders(self, sec, side, n, dt):
        """n syncs of one Orders phase, dt real seconds apart -> the responses"""
        out = []
        for _ in range(n):
            out.append(self.env.sync(sec, "Orders", side))
            self.clock.advance(dt)
        return out

    def test_the_gate_stays_off(self):
        self.env.wake(0)
        self.assertEqual(self.env.sync(30)["gate"], "off")             # an open turn does not slow the game
        self.assertEqual(self.ref.admin_state()["pacing"], "turn")

    def test_the_own_orders_phase_wakes_and_waits_for_end_turn(self):
        r = self.env.sync(60, "Orders", 0)
        self.assertEqual((r["gate"], r["release"], r["f9"], r["orders_done"]), ("off", None, False, False))
        w = self.ref.wait(0, 0)
        self.assertTrue(w["woke"])
        self.assertEqual([x["type"] for x in w["reasons"]], ["orders_phase"])
        self.assertAlmostEqual(w["deadline_in"], rules.TURN_ORDERS_SEC, delta=1)
        self.ref.submit(0, MOVE)
        for r in self.orders(60, 0, 20, 5.0):                          # 100 s: still the agent's time
            self.assertEqual((r["release"], r["f9"]), (None, False))
        self.ref.turn_end(0, {"turn_id": w["turn_id"]})
        rels, last = self.env.stop(60, 0)
        self.assertEqual(len(rels), 1)
        self.assertTrue(last["f9"])
        released = [x for x in self.env.log("wakes") if x.get("kind") == "orders_released"]
        self.assertEqual(len(released), 1)
        self.assertGreaterEqual(released[0]["waited"], 100)

    def test_the_phase_goes_on_after_three_minutes_and_the_turn_ends(self):
        self.env.sync(60, "Orders", 0)
        w = self.ref.wait(0, 0)
        self.ref.turn_begin(0, {"agent": "deputy"})
        self.ref.submit(0, MOVE)
        rs = self.orders(60, 0, 18, 10.0)                               # 0 .. 170 s
        self.assertFalse(any(r["release"] or r["f9"] for r in rs))
        self.clock.advance(rules.TURN_ORDERS_SEC - 180 + 15)            # past 180 s
        rels, last = self.env.stop(60, 0)
        self.assertEqual((len(rels), last["f9"]), (1, True))
        self.assertEqual(self.ref.agents[0], {})                        # commander and sub-agent are both gone
        ends = {(x["agent"], x["reason"]) for x in self.env.log("wakes") if x.get("kind") == "agent_end"}
        self.assertIn(("commander", "turn_time"), ends)
        self.assertIn(("deputy", "turn_time"), ends)
        with self.assertRaises(ApiError):
            self.ref.turn_end(0, {"turn_id": w["turn_id"]})             # the turn is over

    def test_other_events_go_to_the_feed_but_do_not_wake(self):
        self.env.sync(60, lines=[self.env.contact(60, "9 MR", 101, "Detected")])
        self.env.move("Bravo Platoon", 1612, status="Dead")
        for sec in (120, 180, 240, 3700):
            self.env.sync(sec)
        self.assertFalse(self.ref.wait(0, 0)["woke"])
        types = {e["type"] for e in self.ref.events.items[0]}
        self.assertTrue({"new_contact", "unit_out", "timer"} <= types, types)
        self.assertEqual([x for x in self.env.log("wakes") if x.get("kind") == "wake"], [])

    def test_the_other_sides_phase_does_not_wait_for_me(self):
        self.env.sync(60, "Orders", 1)                                  # wakes side 1, not side 0
        self.assertFalse(self.ref.wait(0, 0)["woke"])
        self.assertTrue(self.ref.wait(1, 0)["woke"])

    def test_the_same_phase_after_the_replay_does_not_wait_again(self):
        self.env.sync(60, "Orders", 0)
        w = self.ref.wait(0, 0)
        self.ref.turn_end(0, {"turn_id": w["turn_id"]})
        self.env.stop(60, 0)
        self.env.sync(60, "Executing")                                  # the replay
        r = self.env.sync(60, "Orders", 0)                               # back to the same Orders phase
        self.assertTrue(r["f9"])
        self.assertFalse(self.ref.wait(0, 0)["woke"])


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class AgainstTheGamesAi(unittest.TestCase):
    """--agents 0: side 1 is the game's AI. Its token is refused, it gets no turns, its phases go straight on."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = fg.Env(self.tmp.name, players=(0,), pacing="turn")
        self.ref, self.clock = self.env.ref, self.env.clock
        self.addCleanup(self.ref.close)
        self.srv, tokens = fg.serve(self.ref, self.tmp.name)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        port = self.srv.server_address[1]
        self.a0, self.a1 = fg.Http(port, tokens["side0"]), fg.Http(port, tokens["side1"])
        self.addCleanup(self.a0.close)
        self.addCleanup(self.a1.close)

    def test_the_ai_sides_token_gets_403_everywhere(self):
        self.env.sync(0, "Setup")
        for method, path, body in (("GET", "/agent/wait?timeout=0", None), ("GET", "/agent/view/summary", None),
                                   ("POST", "/agent/queue", {}), ("GET", "/agent/notes/", None),
                                   ("POST", "/agent/turn/end", {}), ("GET", "/agent/scripts", None)):
            code, r = self.a1.call(method, path, body)
            self.assertEqual((code, r["error"]), (403, "not a player in this exercise"), path)
        code, r = self.a0.call("GET", "/agent/wait?timeout=0")
        self.assertEqual((code, r["woke"], r["prematch"]), (200, True, True))
        self.assertEqual(self.ref.turn_n[1], 0)

    def test_the_prematch_waits_only_for_the_player_and_the_ai_gets_no_turns(self):
        self.env.sync(0, "Setup")
        w = self.ref.wait(0, 0)
        self.ref.turn_end(0, {"turn_id": w["turn_id"]})
        rels, last = self.env.stop(0, 0, phase="Setup")
        self.assertTrue(last["f9"])                                     # side 1 was not waited for
        r = self.env.sync(60, "Orders", 1)                               # the AI side's phase: straight on
        self.assertTrue(r["f9"])
        self.env.move("Red Tank Company", 5212, status="Dead")
        for sec in (120, 180, 3700):
            self.env.sync(sec)
        self.assertEqual(self.ref.agents[1], {})
        self.assertEqual([x for x in self.env.log("wakes") if x.get("side") == 1 and x.get("kind") == "wake"], [])


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class SmallExperiment(EnvCase):
    def setUp(self):
        self.env_kw = {"scenario": fg.mini_scenario(), "experiment": {0: ["Alpha Platoon"]}, "prematch_sec": 30}
        super().setUp()

    def test_names_are_checked_when_the_referee_starts(self):
        sc = fg.mini_scenario()
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ({0: ["No Such Unit"]}, {0: ["9 MR"]}, {2: ["TF Alamo"]}):
                with self.assertRaises(ValueError):
                    Referee(tmp, sc, experiment=bad)
            real = Scenario(fg.SCENARIO)
            names = [r["name"] for r in real.roster[0]]
            with self.assertRaises(ValueError):
                Referee(tmp, real, experiment={0: names[:rules.EXERCISE_SMALL_UNITS + 1]})
            real.roster = None
            with self.assertRaises(ValueError):
                Referee(tmp, real, experiment={0: ["TF Alamo"]})
            r = Referee(tmp, Scenario(fg.SCENARIO), experiment={0: names[:rules.EXERCISE_SMALL_UNITS]})
            r.close()

    def test_the_briefing_says_it_is_an_exercise_only_when_it_is_one(self):
        self.assertNotIn("exercise", self.ref.views.render(0, "briefing"))      # a match or the exam: no block
        with tempfile.TemporaryDirectory() as tmp:
            r = Referee(tmp, fg.mini_scenario(), experiment={0: ["Alpha Platoon"]}, players=(0,), pacing="turn",
                        exercise=True)
            ex = r.views.render(0, "briefing")["exercise"]
            r.close()
        self.assertEqual(ex, {"scored": False, "pacing": "turn", "opponent": "game_ai",
                              "small_experiment": {"units": ["Alpha Platoon"], "game_minutes": rules.EXERCISE_SMALL_GAME_MIN}})

    def test_commands_for_other_units_are_refused_without_a_streak(self):
        self.env.wake(0)
        for _ in range(rules.ILLEGAL_STREAK + 1):
            with self.assertRaises(ApiError) as cm:
                self.ref.submit(0, BRAVO)
            self.assertEqual((cm.exception.code, cm.exception.msg), (400, "unit not in this experiment"))
        fire = {"kind": "fire_support", "request": {"request_type": "static_target", "location": 2015, "target_type": "soft"}}
        with self.assertRaises(ApiError):
            self.ref.submit(0, fire)                                     # the firing unit must be named
        with self.assertRaises(ApiError):
            self.ref.submit(0, dict(fire, request=dict(fire["request"], requester_unit="Big Company")))
        self.assertEqual((self.ref.queue.streak[0], self.ref.queue.violations[0], self.ref.queue.dropping[0]),
                         (0, 0, False))
        self.assertTrue(self.ref.submit(0, MOVE)["ok"])
        self.assertTrue(self.ref.submit(0, dict(fire, request=dict(fire["request"], requester_unit="Alpha Platoon")))["ok"])
        self.env.wake(1)
        self.assertTrue(self.ref.submit(1, {"kind": "orders", "unit": "9 MR", "orders": [{"orders": "Hold"}]})["ok"])

    def test_the_opening_batch_tells_the_other_units_to_hold(self):
        self.env.sync(0, "Setup")
        w = self.ref.wait(0, 0)
        self.ref.submit(0, MOVE)
        self.ref.turn_end(0, {"turn_id": w["turn_id"]})
        self.clock.advance(31)                                           # side 1 never ends: the 30 s are up
        rels, last = self.env.stop(0, 0, phase="Setup")
        self.assertTrue(last["f9"])
        self.assertEqual([r["side"] for r in rels], [0])
        cmds = [self.ref.queue.cmds[c] for c in cmd_ids(rels[0])]
        held = sorted(c["unit"] for c in cmds if c["source"] == "referee")
        self.assertEqual(held, ["Big Company", "Bravo Platoon", "TF Alamo"])
        self.assertTrue(all(c["args"]["orders"] == [{"orders": "Hold"}] for c in cmds if c["source"] == "referee"))
        self.assertIn("Alpha Platoon", [c["unit"] for c in cmds if c["source"] == "agent"])
        self.assertEqual(self.ref.audit.summary()["mismatches"], 0)
        self.assertTrue(all(self.ref.queue.cmds[c]["status"] == "done" for c in cmd_ids(rels[0])))

    def test_units_coming_later_are_held_in_their_sides_orders_phase(self):
        self.env.units["Late Platoon"] = fg.mini_unit(0, 1414, "Late Platoon")
        self.ref.sc.roster[0].append({"name": "Late Platoon"})
        self.env.sync(60)
        self.ref.held[0] = {"TF Alamo", "Bravo Platoon", "Big Company"}   # as after the opening batch
        rels, _ = self.env.stop(120, 0)
        self.assertEqual([self.ref.queue.cmds[c]["unit"] for c in cmd_ids(rels[0])], ["Late Platoon"])


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Workspaces(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = os.path.join(self.tmp.name, "player-a")

    def referee(self, run, **kw):
        env = fg.Env(os.path.join(self.tmp.name, run), workspaces={0: self.ws}, **kw)
        self.addCleanup(env.ref.close)
        env.sync(0)
        env.wake(0)
        return env.ref

    def test_notes_and_scripts_live_in_the_players_folder_and_come_back(self):
        ref = self.referee("ex1")
        ref.notes(0, "PUT", "plan.md", {"content": "hold the ridge"})
        ref.scripts_api(0, "PUT", "watch", {"source": "return 1", "trigger": {"on": "orders_phase"}})
        ref.scripts_api(0, "PUT", "gone", {"source": "return 2"})
        self.assertTrue(ref.scripts_api(0, "DELETE", "gone", None)["ok"])
        with open(os.path.join(self.ws, "scripts", "watch.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"name": "watch", "source": "return 1", "trigger": {"on": "orders_phase"}})
        self.assertFalse(os.path.exists(os.path.join(self.ws, "scripts", "gone.json")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "ex1", "workspace", "side0")))
        with self.assertRaises(ApiError) as cm:                         # scripts/ is the store, not a note folder
            ref.notes(0, "PUT", "scripts/sneak.json", {"content": "{}"})
        self.assertEqual(cm.exception.code, 400)
        with open(os.path.join(self.ws, "scripts", "broken.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        # the same player in a later exercise, now on the other side: both sides of an exercise against oneself
        ref2 = fg.Env(os.path.join(self.tmp.name, "ex2"), workspaces={0: self.ws, 1: self.ws}).ref
        self.addCleanup(ref2.close)
        for s in (0, 1):
            self.assertEqual(sorted(ref2.scripts.defs[s]), ["watch"])
            self.assertEqual(ref2.scripts.defs[s]["watch"]["trigger"], {"on": "orders_phase"})
        failed = [r for r in ref2.log.read("scripts") if r.get("event") == "load_failed"]
        self.assertEqual({r["script"] for r in failed}, {"broken"})
        ref2.wake_payload[1] = None
        with ref2.lock:
            ref2._start_turn(1, ref2.clock(), [{"type": "test"}])
        self.assertEqual(ref2.notes(1, "GET", "plan.md", None)["content"], "hold the ridge")

    def test_the_briefing_says_when_the_workspace_is_the_players_own(self):
        ref = self.referee("p5")
        self.assertEqual(ref.views.briefing(0).get("workspace"), "carried")
        self.assertNotIn("workspace", ref.views.briefing(1))

    def test_without_a_workspace_the_run_folder_is_used(self):
        env = fg.Env(os.path.join(self.tmp.name, "plain"))
        self.addCleanup(env.ref.close)
        env.sync(0)
        env.wake(1)
        env.ref.scripts_api(1, "PUT", "watch", {"source": "return 1"})
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "plain", "workspace", "side1", "scripts", "watch.json")))


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Report(EnvCase):
    def setUp(self):
        self.env_kw = {"experiment": {0: ["TF Alamo"]}, "scenario": fg.mini_scenario()}
        super().setUp()

    def read(self):
        with open(os.path.join(self.tmp.name, "exercise-report.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_a_stopped_exercise_reports_losses_and_vp_cells(self):
        self.env.wake(0)
        self.ref.notes(0, "PUT", "plan.md", {"content": "SECRET_NOTE"})
        self.ref.scripts_api(0, "PUT", "watch", {"source": "-- SECRET_SCRIPT"})
        self.env.units["Big Company"]["u"][0]["status"] = "Destroyed"
        self.env.units["Big Company"]["u"][1]["status"] = "Destroyed"
        self.env.move("Red Tank Company", 5212, status="Dead")
        self.env.move("Alpha Platoon", 2118)                             # onto a VP cell
        self.env.sync(60)
        self.clock.advance(75)
        self.ref.stop()
        rep = self.read()
        self.assertEqual((rep["ended_by"], rep["sec"], rep["official"]), ("stopped", 60, None))
        self.assertIn("not the game's own end", rep["note"])
        self.assertAlmostEqual(rep["wall_sec"], 75, delta=1)
        units0 = {r["unit"]: r for r in rep["losses"]["0"]["units"]}
        self.assertEqual((units0["Big Company"]["subunits_start"], units0["Big Company"]["subunits_now"]), (20, 18))
        units1 = {r["unit"]: r for r in rep["losses"]["1"]["units"]}
        self.assertEqual(units1["Red Tank Company"]["status"], "Dead")
        self.assertEqual(rep["losses"]["1"]["units_out"], 1)
        self.assertEqual(set(rep["losses"]), {"0", "1"})
        cells = {c["hex"]: c for c in rep["vp_cells"]}
        self.assertEqual(set(cells), set(self.ref.sc.vp_cells))
        self.assertEqual(cells[2118]["owner"], 0)
        self.assertEqual(rep["vp_held"]["0"], sum(c["vp"] for c in rep["vp_cells"] if c["owner"] == 0))
        self.assertEqual(rep["experiment_units"], {"0": ["TF Alamo"]})
        text = json.dumps(rep)
        for secret in ("SECRET_NOTE", "SECRET_SCRIPT", "plan.md", "transcript"):
            self.assertNotIn(secret, text)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "final.json")))

    def test_the_games_own_end_and_a_lost_game(self):
        self.env.sync(120, "Ended", end_shots=["TfTOC_ScnInfo"], alive=True)
        rep = self.read()
        self.assertEqual(rep["ended_by"], "game_end")
        self.assertNotIn("note", rep)
        with tempfile.TemporaryDirectory() as tmp:
            env = fg.Env(tmp)
            env.sync(0)
            env.sync(60, alive=False)
            env.ref.close()
            with open(os.path.join(tmp, "exercise-report.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["ended_by"], "game_gone")
        with tempfile.TemporaryDirectory() as tmp:     # Ended before the relay has shot the end screens
            env = fg.Env(tmp)
            env.sync(0)
            env.sync(60, "Ended", alive=True)
            env.ref.close()
            with open(os.path.join(tmp, "exercise-report.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["ended_by"], "game_end")

    def test_a_silent_relay_ends_the_game_without_a_report(self):
        self.env.sync(60)
        self.env.wake(0)
        self.clock.advance(rules.RELAY_SILENT_SEC - 1)
        st = self.ref.admin_state()
        self.assertFalse(st["ended"])
        self.assertAlmostEqual(st["relay_silent_sec"], rules.RELAY_SILENT_SEC - 1, delta=1)
        self.clock.advance(2)
        st = self.ref.admin_state()
        self.assertEqual((st["ended"], st["ended_by"]), (True, "relay_lost"))
        self.assertNotIn("commander", self.ref.agents[0])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "exercise-report.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "final.json")))


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class BatchPrefix(EnvCase):
    env_kw = {"batch_prefix": "l1-"}

    def test_batch_ids_carry_the_lane_and_the_audit_still_matches(self):
        self.env.wake(0)
        self.ref.submit(0, MOVE)
        rels, _ = self.env.stop(60, 0)
        self.assertEqual(rels[0]["batch_id"], "l1-s0-0001")
        self.assertEqual(self.ref.audit.summary()["matched"], 1)
        self.assertEqual(self.ref.audit.summary()["mismatches"], 0)


if __name__ == "__main__":
    unittest.main()
