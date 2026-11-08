"""The training referee (materials/10 P4, 2.10): wakes, both briefings, exercise requests, quotas and refusals, results
that wake the player, the director's admin endpoints, and the agent's two exercise tools over HTTP."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.agent import cli, tools  # noqa: E402
from arena.referee import rules  # noqa: E402
from arena.referee.core import ApiError, Referee  # noqa: E402
from arena.referee.training import Training  # noqa: E402
from arena.referee.world import Scenario  # noqa: E402
from arena.tests import fakegame as fg  # noqa: E402

SMALL = {"type": "small", "opponent": "ai", "play_side": 0, "units": ["TF Alamo", "1/AA"]}
FULL = {"type": "full", "opponent": "self", "play_side": 1, "pacing": "formal"}
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


class TrainingCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = fg.FakeClock()
        self.ws = {p: os.path.join(self.tmp.name, f"ws{p}") for p in (0, 1)}
        self.t = Training(os.path.join(self.tmp.name, "run"), Scenario(fg.SCENARIO), clock=self.clock, workspaces=self.ws)
        self.addCleanup(self.t.close)

    def turn(self, p):
        w = self.t.wait(p, 0)
        self.assertTrue(w["woke"], w)
        return w

    def refused(self, p, body, code=409):
        with self.assertRaises(ApiError) as cm:
            self.t.request_exercise(p, body)
        self.assertEqual(cm.exception.code, code, cm.exception.msg)
        return cm.exception

    def finish(self, eid, status="done", wall=600, report=None):
        return self.t.exercise_update({"id": eid, "status": status, "wall_sec": wall, "report": report})


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Training_(TrainingCase):
    def test_the_first_wake_brings_both_briefings_with_their_rosters(self):
        w = self.turn(0)
        self.assertEqual([r["type"] for r in w["reasons"]], ["training_start"])
        self.assertAlmostEqual(w["deadline_in"], rules.TRAINING_TURN_SEC, delta=1)
        b = w["briefing"]
        self.assertEqual(set(b["sides"]), {"0", "1"})
        self.assertEqual(len(b["sides"]["0"]["units"]), 23)
        self.assertEqual(len(b["sides"]["1"]["units"]), 39)
        self.assertEqual(b["sides"]["1"]["command_post"], "9 MR")
        self.assertEqual(self.t.view(0, "briefing", {}), b)
        self.assertEqual(len(self.t.view(0, "terrain", {"hexes": "101,102"})["terrain"]), 2)
        with self.assertRaises(ApiError) as cm:
            self.t.view(0, "units", {})
        self.assertEqual(cm.exception.code, 404)
        self.assertTrue(self.t.wait(1, 0)["woke"])                      # player 1 gets its own first wake
        with self.assertRaises(ApiError):
            self.t.wait(0, 0)                                           # its turn is open

    def test_a_request_ends_the_turn_and_the_result_wakes_the_player(self):
        w = self.turn(0)
        self.t.turn_begin(0, {"agent": "deputy"})
        r = self.t.request_exercise(0, SMALL)
        self.assertEqual((r["id"], r["status"], r["turn_over"]), ("p0-1", "queued", True))
        self.assertEqual(self.t.agents[0], {})
        with self.assertRaises(ApiError):
            self.t.turn_end(0, {"turn_id": w["turn_id"]})
        self.assertFalse(self.t.wait(0, 0)["woke"])
        q = self.t.admin_exercises()
        self.assertEqual([(e["id"], e["player"], e["status"]) for e in q["exercises"]], [("p0-1", 0, "queued")])
        self.assertEqual(q["exercises"][0]["request"]["units"], ["TF Alamo", "1/AA"])
        self.assertEqual(q["exercises"][0]["request"]["scenario"], "Alamo at Soest")
        self.t.exercise_update({"id": "p0-1", "status": "running"})
        report = {"ended_by": "stopped", "losses": {"0": {}, "1": {}}, "official": None}
        self.clock.advance(1800)
        self.finish("p0-1", wall=1500, report=report)
        w = self.turn(0)
        self.assertEqual(w["reasons"][0]["type"], "exercise_done")
        self.assertEqual(w["reasons"][0]["report"], report)                   # as the director gave it
        self.assertEqual(w["summary"]["quota"]["small_wall_left_sec"], rules.EXERCISE_SMALL_WALL_SEC - 1500)
        listed = self.t.exercises(0)
        self.assertEqual(listed["exercises"][0]["report"], report)
        self.assertEqual(listed["exercises"][0]["wall_sec"], 1500)
        with open(os.path.join(self.tmp.name, "run", "exercises.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["exercises"][0]["status"], "done")

    def test_bad_requests_are_400(self):
        self.turn(0)
        for body in ({}, {"type": "huge", "opponent": "ai", "play_side": 0}, dict(SMALL, opponent="friend"),
                     dict(SMALL, play_side=2), dict(SMALL, play_side=True), dict(SMALL, pacing="slow"),
                     dict(SMALL, units=[]), dict(SMALL, units=["9 MR"]), dict(SMALL, units=["No Such"]),
                     dict(SMALL, units=[r["name"] for r in self.t.sc.roster[0]][:11]),
                     dict(FULL, units=["TF Alamo"]), dict(SMALL, scenario="other"), dict(SMALL, play_side=1, units=["9 MR"]),
                     dict(FULL, opponent="ai"), "full"):
            self.refused(0, body, code=400)
        self.assertEqual(self.t.ex, {})

    def test_quotas_and_one_at_a_time(self):
        self.turn(0)
        for i in range(rules.EXERCISE_FULL_MAX):
            self.t.request_exercise(0, FULL)
            with self.t.lock:
                self.t._start_turn(0, self.clock(), [{"type": "test"}])   # a turn while it is still queued
            e = self.refused(0, SMALL)
            self.assertIn("one exercise at a time", e.msg)
            self.finish(f"p0-{i + 1}", status="failed" if i == 0 else "done")
            self.turn(0)
        self.t.request_exercise(0, FULL)                                   # the failed one does not count
        self.finish(f"p0-{rules.EXERCISE_FULL_MAX + 1}")
        self.turn(0)
        e = self.refused(0, FULL)
        self.assertIn("full exercises are used", e.msg)
        self.assertEqual(e.extra["quota"]["full_used"], rules.EXERCISE_FULL_MAX)
        refusals = [x for x in self.t.feed[0] if x["type"] == "exercise_refused"]
        self.assertEqual(len(refusals), rules.EXERCISE_FULL_MAX + 1)
        # small experiments: by the real time of the done ones
        self.t.request_exercise(0, SMALL)
        self.finish(f"p0-{rules.EXERCISE_FULL_MAX + 2}", wall=rules.EXERCISE_SMALL_WALL_SEC - 600)
        self.turn(0)
        e = self.refused(0, SMALL)
        self.assertIn("small-experiment time left", e.msg)
        self.assertNotIn("no open turn", e.msg)                            # the agent must not read it as turn over
        self.assertEqual(self.t.quota(1)["full_used"], 0)                  # the other player's quota is its own

    def test_a_training_carried_on_keeps_clocks_exercises_and_numbers(self):
        # 11-02: the director could no longer start processes; a new one carries the training on
        done = {"type": "full", "opponent": "ai", "play_side": 0, "pacing": "turn"}
        carry = {"players": {"0": {"clock_sec": 3000.0}, "1": {"clock_sec": 500.0}},
                 "exercises": [{"id": "p0-1", "player": 0, "status": "done", "request": done, "wall_sec": 2000.0,
                                "report": {"official": {"result": "Contested Result"}}},
                               {"id": "p1-1", "player": 1, "status": "parked", "request": FULL, "wall_sec": 0.0},
                               {"id": "p0-2", "player": 0, "status": "running", "request": done}]}
        t = Training(os.path.join(self.tmp.name, "carry"), Scenario(fg.SCENARIO), clock=self.clock, workspaces=self.ws,
                     limit_sec=3600, carry=carry)
        self.addCleanup(t.close)
        self.clock.advance(60)
        self.assertEqual((t.player_clock(0), t.player_clock(1)), (3060.0, 560.0))
        self.assertEqual((t.quota(0)["full_used"], t.quota(1)["full_used"]), (1, 0))      # parked is not used
        t.wait(0, 0)
        self.assertEqual([e["id"] for e in t.exercises(0)["exercises"]], ["p0-1"])           # an open one is not carried
        self.assertEqual(t.exercises(0)["exercises"][0]["report"]["official"]["result"], "Contested Result")
        self.assertEqual(t.request_exercise(0, done)["id"], "p0-2")
        t.wait(1, 0)
        self.assertEqual(t.request_exercise(1, FULL)["id"], "p1-2")

    def test_a_supplementary_session_has_its_own_quotas_and_the_rulings_text(self):
        t = Training(os.path.join(self.tmp.name, "sup"), Scenario(fg.SCENARIO), clock=self.clock, workspaces=self.ws,
                     full_max=1, small_wall_sec=0, limit_sec=7200, supplement="RULING_TEXT")
        self.addCleanup(t.close)
        w = t.wait(1, 0)
        self.assertEqual(w["briefing"]["supplement"], "RULING_TEXT")
        self.assertEqual(w["summary"]["training"], {"wall_sec": 0.0, "limit_sec": 7200, "scenario": t.sc.name,
                                                    "supplement": "RULING_TEXT"})
        self.assertEqual((w["summary"]["quota"]["full_max"], w["summary"]["quota"]["small_wall_left_sec"]), (1, 0))
        with self.assertRaises(ApiError) as cm:
            t.request_exercise(1, SMALL)
        self.assertIn("small-experiment time left", cm.exception.msg)
        t.request_exercise(1, FULL)
        t.exercise_update({"id": "p1-1", "status": "done", "wall_sec": 600, "report": None})
        t.wait(1, 0)
        with self.assertRaises(ApiError) as cm:
            t.request_exercise(1, FULL)
        self.assertIn("all 1 full exercises are used", cm.exception.msg)
        self.assertNotIn("supplement", self.turn(0)["briefing"])            # the main training has none

    def test_the_full_exercises_kept_for_the_sparring_partner(self):   # 11-03: the new match scenario, vs self once
        vs_ai = {"type": "full", "opponent": "ai", "play_side": 0}
        t = Training(os.path.join(self.tmp.name, "self"), Scenario(fg.SCENARIO), clock=self.clock, workspaces=self.ws,
                     full_max=2, self_full_min=1)
        self.addCleanup(t.close)
        t.wait(0, 0)
        t.request_exercise(0, vs_ai)                                        # the first may be against the AI
        t.exercise_update({"id": "p0-1", "status": "done", "wall_sec": 600, "report": None})
        t.wait(0, 0)
        with self.assertRaises(ApiError) as cm:
            t.request_exercise(0, vs_ai)                                    # the last one is the sparring partner's
        self.assertIn("must be against your sparring partner", cm.exception.msg)
        self.assertEqual((cm.exception.extra["quota"]["self_full_min"], cm.exception.extra["quota"]["self_full_used"]), (1, 0))
        self.assertEqual(t.request_exercise(0, FULL)["id"], "p0-2")
        t.wait(1, 0)
        t.request_exercise(1, FULL)                                         # vs self first: the AI one stays open
        t.exercise_update({"id": "p1-1", "status": "done", "wall_sec": 600, "report": None})
        t.wait(1, 0)
        self.assertEqual(t.request_exercise(1, vs_ai)["id"], "p1-2")
        self.assertNotIn("self_full_min", self.t.quota(0))                  # none asked: the quota is as before

    def test_a_failed_exercise_wakes_with_its_reason_and_finished_ones_stay_finished(self):
        self.turn(1)
        self.t.request_exercise(1, FULL)
        self.t.exercise_update({"id": "p1-1", "status": "failed", "error": "relay never connected"})
        w = self.turn(1)
        self.assertEqual((w["reasons"][0]["status"], w["reasons"][0]["error"]), ("failed", "relay never connected"))
        for bad, code in (({"id": "p1-1", "status": "done"}, 409), ({"id": "p9-9", "status": "done"}, 404),
                          ({"id": "p1-1", "status": "won"}, 400)):
            with self.assertRaises(ApiError) as cm:
                self.t.exercise_update(bad)
            self.assertEqual(cm.exception.code, code)

    def test_the_end_of_training(self):
        self.turn(0)
        self.t.request_exercise(0, SMALL)
        self.turn(1)
        self.t.request_exercise(1, FULL)
        self.t.exercise_update({"id": "p1-1", "status": "running"})
        self.t.stop()
        self.assertEqual(self.t.wait(0, 0)["game_over"], True)
        st = {e["id"]: e for e in self.t.admin_exercises()["exercises"]}
        self.assertEqual((st["p0-1"]["status"], st["p1-1"]["status"]), ("failed", "running"))
        self.finish("p1-1", wall=5400, report={"ended_by": "stopped"})    # a late result is recorded, wakes nobody
        self.assertEqual(self.t.agents[1], {})
        with open(os.path.join(self.tmp.name, "run", "exercises.json"), encoding="utf-8") as f:
            rec = json.load(f)
        self.assertTrue(rec["ended"])
        self.assertEqual({e["id"]: e["status"] for e in rec["exercises"]}, {"p0-1": "failed", "p1-1": "done"})

    def test_notes_and_scripts_are_the_players_and_scripts_are_only_stored(self):
        self.turn(0)
        self.t.notes(0, "PUT", "plan.md", {"content": "A"})
        self.t.scripts_api(0, "PUT", "watch", {"source": "return 1"})
        self.assertTrue(os.path.isfile(os.path.join(self.ws[0], "plan.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.ws[0], "scripts", "watch.json")))
        self.turn(1)
        with self.assertRaises(ApiError):
            self.t.notes(1, "GET", "plan.md", None)
        self.assertEqual(self.t.scripts_api(1, "GET", "", None)["scripts"], {})
        # an exercise referee changed the stored scripts meanwhile: the next turn sees them
        self.t.request_exercise(0, SMALL)
        os.remove(os.path.join(self.ws[0], "scripts", "watch.json"))
        self.finish("p0-1")
        self.turn(0)
        self.assertEqual(self.t.scripts_api(0, "GET", "", None)["scripts"], {})
        self.assertIn("note_exercise", self.t.turn_end(0, {"turn_id": self.t.turn_id[0]}))


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class OverHttp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Training(self.tmp.name, Scenario(fg.SCENARIO), clock=fg.FakeClock())
        self.addCleanup(self.t.close)
        self.srv, tok = fg.serve(self.t, self.tmp.name)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.port = self.srv.server_address[1]
        self.tok = tok
        self.p0, self.admin, self.relay = (fg.Http(self.port, tok[k]) for k in ("side0", "admin", "relay"))
        for h in (self.p0, self.admin, self.relay):
            self.addCleanup(h.close)

    def test_endpoints_of_training_and_what_it_does_not_have(self):
        code, w = self.p0.call("GET", "/agent/wait?timeout=0")
        self.assertEqual((code, w["reasons"][0]["type"]), (200, "training_start"))
        for method, path in (("POST", "/agent/queue"), ("POST", "/agent/observe"), ("GET", "/agent/view/units")):
            self.assertEqual(self.p0.call(method, path, {})[0], 404, path)
        self.assertEqual(self.relay.call("POST", "/relay/sync", {})[0], 404)
        self.assertEqual(self.relay.call("GET", "/relay/ping")[1]["scenario"], "Alamo at Soest")
        code, r = self.p0.call("GET", "/agent/exercises")
        self.assertEqual((code, r["exercises"], r["quota"]["full_max"]), (200, [], rules.EXERCISE_FULL_MAX))
        code, r = self.p0.call("POST", "/agent/exercise", SMALL)
        self.assertEqual((code, r["id"]), (200, "p0-1"))
        self.assertEqual(self.p0.call("GET", "/agent/exercises")[0], 409)        # the turn is over
        code, q = self.admin.call("GET", "/admin/exercises")
        self.assertEqual((code, q["exercises"][0]["status"]), (200, "queued"))
        code, r = self.admin.call("POST", "/admin/exercise_update", {"id": "p0-1", "status": "done", "wall_sec": 60,
                                                                     "report": {"ended_by": "game_end"}})
        self.assertEqual(code, 200)
        code, w = self.p0.call("GET", "/agent/wait?timeout=0")
        self.assertEqual(w["reasons"][0]["report"], {"ended_by": "game_end"})
        self.assertEqual(self.admin.call("GET", "/admin/state")[1]["mode"], "training")
        self.assertEqual(self.admin.call("POST", "/admin/stop")[1], {"stopped": True})
        self.assertTrue(self.p0.call("GET", "/agent/wait?timeout=0")[1]["game_over"])

    def test_the_agents_exercise_commands(self):
        c = cli.Cli(tools.Referee(f"http://127.0.0.1:{self.port}", self.tok["side0"]), tools.Library(tempfile.gettempdir()))
        self.assertEqual(self.p0.call("GET", "/agent/wait?timeout=0")[0], 200)
        r = c.run("exercise list")
        self.assertEqual(r.status, 200)
        self.assertFalse(r.turn_over)
        r = c.run("exercise request", '{"type": "huge", "opponent": "ai", "play_side": 0}')     # raw JSON reaches the referee as is
        self.assertEqual((r.status, r.turn_over), (400, False))
        # unit names as the player types them: case and a unique prefix, checked against the briefing's roster
        r = c.run("exercise request small ai side=0 units=tf")
        self.assertEqual((r.status, r.turn_over), (200, True))
        self.assertIn("p0-1", r.text)
        self.assertEqual(self.t.ex["p0-1"]["request"]["units"], ["TF Alamo"])
        self.assertEqual(set(cli.NOUNS["exercise"]["verbs"]), {"request", "list", "show"})

    def test_a_game_referee_has_no_exercises(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref = Referee(tmp, Scenario(fg.SCENARIO), clock=fg.FakeClock())
            srv, tok = fg.serve(ref, tmp)
            a0, admin = fg.Http(srv.server_address[1], tok["side0"]), fg.Http(srv.server_address[1], tok["admin"])
            try:
                self.assertEqual(a0.call("POST", "/agent/exercise", SMALL)[0], 404)
                self.assertEqual(a0.call("GET", "/agent/exercises")[0], 404)
                self.assertEqual(admin.call("GET", "/admin/exercises")[0], 404)
                c = cli.Cli(tools.Referee(f"http://127.0.0.1:{srv.server_address[1]}", tok["side0"]), tools.Library(tmp))
                self.assertEqual(c.run("exercise request", json.dumps(FULL)).status, 404)
            finally:
                a0.close()
                admin.close()
                srv.shutdown()
                srv.server_close()
                ref.close()


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class OfficialLevels(unittest.TestCase):
    """The briefing gives the levels the game judges by (its Victory Status table), not the briefing text's."""
    def test_alamo_levels(self):
        import types
        from arena.referee.views import Views
        sc = Scenario("alamo-at-soest")
        me = types.SimpleNamespace(ref=types.SimpleNamespace(sc=sc, players=(0, 1)))
        b0, b1 = Views.briefing(me, 0), Views.briefing(me, 1)
        self.assertEqual(b0["victory_share_needed"], {"decisive": 85, "tactical": 76, "marginal": 66})
        self.assertIn("54-65% Contested, <= 53% a loss", b0["victory_rule"])
        # the Polish table is not the plain mirror: 25% is its Tactical Loss, 26% its Marginal Loss (10-31)
        self.assertEqual(b1["victory_share_needed"], {"decisive": 67, "tactical": 59, "marginal": 48})
        self.assertIn("36-47% Contested, <= 35% a loss", b1["victory_rule"])


if __name__ == "__main__":
    unittest.main()
