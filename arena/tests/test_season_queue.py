"""Season 2 game facility (materials/18 2.3): the fair one-at-a-time queue, queue time and failed attempts kept off
the player's training clock, the pre-game health check, the game monitor's failure kinds, restart then park."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.harness import gamequeue as gq  # noqa: E402
from arena.referee.core import ApiError  # noqa: E402
from arena.referee.training import Training  # noqa: E402
from arena.referee.world import Scenario  # noqa: E402
from arena.tests import fakegame as fg  # noqa: E402

SMALL = {"type": "small", "opponent": "ai", "play_side": 0, "units": ["TF Alamo", "1/AA"]}
FULL = {"type": "full", "opponent": "self", "play_side": 1, "pacing": "turn"}
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


class Queue(unittest.TestCase):
    def test_round_robin_one_at_a_time(self):
        clock = fg.FakeClock(0)
        q = gq.GameQueue(clock)
        a1, a2 = q.submit("A", "exercise", {}), q.submit("A", "exercise", {})
        r = q.next()
        self.assertIs(r, a1)
        self.assertIsNone(q.next())                              # one game at a time
        clock.advance(100)
        b1 = q.submit("B", "exercise", {})                       # B asks while A's game runs
        f1 = q.submit("referee", "formal", {})
        clock.advance(500)
        q.finish(r, {"status": "finished"})
        order = []
        while True:
            r = q.next()
            if r is None:
                break
            order.append(r)
            clock.advance(60)
            q.finish(r, {"status": "finished"})
        self.assertEqual(order, [b1, f1, a2])                    # B is not behind A's second request
        self.assertEqual(b1.queued_sec, 500)                     # recorded, for the referee to keep off B's clock
        self.assertEqual(a2.queued_sec, 720)
        self.assertTrue(q.idle())

    def test_each_owner_in_order_and_rotation(self):
        q = gq.GameQueue(fg.FakeClock(0))
        reqs = [q.submit(o, "x", {}, rid=f"{o}{i}") for o, i in (("A", 1), ("A", 2), ("A", 3), ("B", 1), ("C", 1), ("B", 2))]
        got = []
        while (r := q.next()) is not None:
            got.append(r.id)
            q.finish(r, {})
        self.assertEqual(got, ["A1", "B1", "C1", "A2", "B2", "A3"])
        self.assertEqual(len(reqs), 6)


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class TrainingClock(unittest.TestCase):
    """The training referee keeps queue time and lost attempts off the player's clock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = fg.FakeClock()
        self.t = Training(os.path.join(self.tmp.name, "run"), Scenario(fg.SCENARIO), clock=self.clock, limit_sec=3600,
                          workspaces={p: os.path.join(self.tmp.name, f"ws{p}") for p in (0, 1)})
        self.addCleanup(self.t.close)

    def upd(self, **body):
        return self.t.exercise_update(body)

    def test_queue_wait_and_failed_attempts_are_not_charged(self):
        self.t.wait(0, 0)
        self.clock.advance(100)
        self.t.request_exercise(0, SMALL)                        # p0-1 at 100 s
        self.clock.advance(600)                                  # waits 600 s for the machine
        self.upd(id="p0-1", status="running")
        self.assertEqual(self.t.player_clock(0), 100.0)
        self.clock.advance(300)                                  # the first attempt: 300 s, then the relay was lost
        self.upd(id="p0-1", status="restart", failure={"kind": "relay_lost", "detail": "silent 180 s"})
        self.assertEqual(self.t.player_clock(0), 100.0)
        self.clock.advance(200)                                  # the restarted game: 200 s, charged
        self.upd(id="p0-1", status="done", wall_sec=200, report={"ended_by": "game_end"})
        self.assertEqual(self.t.player_clock(0), 300.0)
        self.assertEqual(self.t.quota(0)["small_wall_used_sec"], 200)        # the lost attempt uses no quota
        e = self.t.admin_exercises()["exercises"][0]
        self.assertEqual((e["queued_sec"], [f["kind"] for f in e["facility_failures"]]), (600.0, ["relay_lost"]))
        w = self.t.wait(0, 0)                                    # the result wakes the player and says what happened
        ev = w["summary"]["events"][-1]
        self.assertEqual((ev["type"], ev["status"]), ("exercise_done", "done"))
        self.assertIn("设施故障", ev["notice"])
        self.assertIn("不算你的训练时间", ev["notice"])
        self.assertEqual(w["summary"]["training"]["wall_sec"], 300.0)
        self.assertEqual(w["summary"]["training"]["uncharged_sec"], 900.0)
        p = self.t.admin_exercises()["players"]["0"]
        self.assertEqual((p["clock_sec"], p["uncharged_sec"]), (300.0, 900.0))

    def test_parked_uses_no_quota(self):
        self.t.wait(1, 0)
        self.t.request_exercise(1, FULL)
        self.upd(id="p1-1", status="running")
        for _ in range(2):
            self.clock.advance(50)
            self.upd(id="p1-1", status="restart", failure={"kind": "hang", "detail": "x"})
        self.clock.advance(50)
        self.upd(id="p1-1", status="parked", failure={"kind": "hang", "detail": "x"}, error="facility failures 3 times")
        self.assertEqual(self.t.quota(1)["full_used"], 0)
        self.assertEqual(self.t.player_clock(1), 0.0)
        ev = self.t.wait(1, 0)["summary"]["events"][-1]
        self.assertEqual(ev["status"], "parked")
        self.assertIn("已暂停", ev["notice"])

    def test_restart_needs_a_running_exercise_and_a_failure(self):
        self.t.wait(0, 0)
        self.t.request_exercise(0, SMALL)
        with self.assertRaises(ApiError) as cm:
            self.upd(id="p0-1", status="restart", failure={"kind": "hang"})
        self.assertEqual(cm.exception.code, 409)                 # still queued
        self.upd(id="p0-1", status="running")
        with self.assertRaises(ApiError) as cm:
            self.upd(id="p0-1", status="restart")
        self.assertEqual(cm.exception.code, 400)

    def test_clock_out_refuses_requests_then_ends_the_players_training(self):
        self.t.limit_sec = 600
        self.t.wait(0, 0)
        self.clock.advance(700)                                  # inside the 20-minute turn, past the clock
        with self.assertRaises(ApiError) as cm:
            self.t.request_exercise(0, SMALL)
        self.assertIn("used up", cm.exception.msg)
        self.t.turn_end(0, {"turn_id": "p0-t0001"})
        self.assertEqual(self.t.wait(0, 0), {"woke": False, "game_over": True, "training_over": True})
        self.assertTrue(self.t.admin_exercises()["players"]["0"]["over"])
        self.assertTrue(self.t.wait(1, 0)["woke"])               # the other player's first wait still starts its training


class FakeMachine:
    def __init__(self):
        self.busy, self.calibrating, self.alive, self.listeners, self.tokens, self.calls = [], False, set(), [], [], []
        self.foreign, self.unstoppable = [], False

    def occupancy(self):
        if self.busy == "error":
            raise RuntimeError("no table")
        return list(self.busy)

    def calibration_running(self):
        return self.calibrating

    def probe(self, last):
        alive = bool(last) and last["pid"] in self.alive
        return {"game_alive": alive, "foreign_games": list(self.foreign), "tokens": list(self.tokens),
                "telnet_listeners": list(self.listeners) + ([last["pid"]] if alive else [])}

    def stop_game(self, last):
        self.calls.append(("stop_game", last["pid"]))
        if not self.unstoppable:
            self.alive.discard(last["pid"])
        return "stopped"

    def remove_tokens(self, names):
        self.calls.append(("remove_tokens", tuple(names)))
        self.tokens = [t for t in self.tokens if t not in names]
        return names


class HealthCheck(unittest.TestCase):
    def setUp(self):
        self.m, self.rows = FakeMachine(), []
        self.h = gq.Health(self.m, 47823)
        self.log = lambda ev, **f: self.rows.append((ev, f))

    def test_clean_machine(self):
        self.assertEqual(self.h.ensure(self.log), (True, []))
        self.assertEqual(self.m.calls, [])

    def test_our_leftovers_are_cleaned_by_exact_pid_and_name_then_rechecked(self):
        self.h.last_game = {"pid": 4242, "creation": "2026-11-01T00:00:00"}
        self.m.alive.add(4242)
        self.m.tokens = ["relay-s2-g001-a1.token"]
        ok, problems = self.h.ensure(self.log)
        self.assertTrue(ok, problems)
        self.assertEqual(self.m.calls, [("stop_game", 4242), ("remove_tokens", ("relay-s2-g001-a1.token",))])
        self.assertEqual([e for e, _ in self.rows], ["health_clean", "health_clean", "health"])

    def test_someone_elses_game_or_calibration_or_port_is_waited_for_not_touched(self):
        for setup in (lambda m: setattr(m, "busy", ["experiment fpc-s2-cal-c1 pid 77"]), lambda m: setattr(m, "calibrating", True),
                      lambda m: setattr(m, "listeners", [5555]), lambda m: setattr(m, "busy", "error")):
            m = FakeMachine()
            setup(m)
            ok, problems = gq.Health(m, 47823).ensure(self.log)
            self.assertFalse(ok)
            self.assertIn(problems[0]["kind"], ("busy", "telnet_busy"))
            self.assertEqual(m.calls, [])

    def test_any_game_process_on_the_machine_refuses_the_start(self):
        # health.ps1 counts every FlashpointCampaigns.exe on the game machine (not our job registry): one that is not our
        # recorded game (pid + creation) is someone else's, waited for and never touched
        self.h.last_game = {"pid": 4242, "creation": "2026-11-01T00:00:00"}
        self.m.foreign = [9001]
        ok, problems = self.h.ensure(self.log)
        self.assertFalse(ok)
        self.assertEqual(problems[0]["kind"], "busy")
        self.assertIn("9001", problems[0]["detail"])
        self.assertEqual(self.m.calls, [])

    def test_our_game_that_does_not_stop_still_refuses(self):
        self.h.last_game = {"pid": 4242, "creation": "2026-11-01T00:00:00"}
        self.m.alive.add(4242)
        self.m.unstoppable = True
        ok, problems = self.h.ensure(self.log)
        self.assertFalse(ok)
        self.assertEqual([p["kind"] for p in problems], ["our_game_alive"])

    def test_start_gap_and_another_minute(self):
        clock = fg.FakeClock(time.mktime((2026, 11, 1, 10, 0, 5, 0, 0, -1)))
        h = gq.Health(self.m, 47823, clock=clock, gap=180)
        self.assertEqual(h.start_wait(), 0.0)
        h.started(None)
        clock.advance(30)
        self.assertAlmostEqual(h.start_wait(), 150, delta=0.01)
        h = gq.Health(self.m, 47823, clock=clock, gap=10)       # a short gap still moves to the next minute
        h.started(None)
        self.assertAlmostEqual(h.start_wait(), 60 - 35 + 1, delta=0.01)


class Monitor(unittest.TestCase):
    def setUp(self):
        self.clock = fg.FakeClock(0)
        self.w = gq.Watch(self.clock, load_sec=300, hang_sec=600, prematch_sec=600)

    def test_not_loaded(self):
        self.assertIsNone(self.w.step(200, {"minute_seq": 0, "ended": False}))
        self.clock.advance(301)
        self.assertEqual(self.w.step(200, {"minute_seq": 0, "ended": False})[:2], ("facility", "not_loaded"))

    def test_hang_outside_deployment_only(self):
        st = {"minute_seq": 1, "sec": 0, "phase": "Setup", "ended": False}
        self.assertIsNone(self.w.step(200, st))
        self.clock.advance(900)                                  # deployment may stand still for prematch + hang
        self.assertIsNone(self.w.step(200, st))
        self.assertIsNone(self.w.step(200, dict(st, sec=60, phase="Executing")))
        self.clock.advance(500)
        self.assertIsNone(self.w.step(200, dict(st, sec=60, phase="Orders")))
        self.clock.advance(101)
        self.assertEqual(self.w.step(200, dict(st, sec=60, phase="Orders"))[:2], ("facility", "hang"))

    def test_long_deployment_is_not_a_hang_when_the_game_begins(self):
        st = {"minute_seq": 1, "sec": 0, "phase": "Setup", "ended": False}
        self.assertIsNone(self.w.step(200, st))
        self.clock.advance(1150)                                 # round 4 E1: deployment took 19 minutes
        self.assertIsNone(self.w.step(200, dict(st, phase="Executing")))
        self.clock.advance(599)
        self.assertIsNone(self.w.step(200, dict(st, phase="Executing")))
        self.clock.advance(2)
        self.assertEqual(self.w.step(200, dict(st, phase="Executing"))[:2], ("facility", "hang"))

    def test_relay_lost_and_official_end(self):
        self.assertEqual(self.w.step(200, {"ended": True, "ended_by": "relay_lost"})[:2], ("facility", "relay_lost"))
        self.assertEqual(self.w.step(200, {"ended": True, "ended_by": "game_end"}), ("end", "game_end"))
        self.assertIsNone(self.w.step(0, {"error": "unreachable"}))


class RestartPolicy(unittest.TestCase):
    def test_restart_until_finished(self):
        outcomes = [{"status": "facility", "failure": {"kind": "hang", "detail": "a"}},
                    {"status": "facility", "failure": {"kind": "cli_died", "detail": "b"}}, {"status": "finished", "ended_by": "game_end"}]
        seen, restarts = [], []
        res = gq.play(lambda n, f: seen.append((n, [x["kind"] for x in f])) or outcomes[n], on_restart=lambda f: restarts.append(len(f)))
        self.assertEqual((res["status"], res["attempts"], [f["kind"] for f in res["facility_failures"]]), ("finished", 3, ["hang", "cli_died"]))
        self.assertEqual(seen, [(0, []), (1, ["hang"]), (2, ["hang", "cli_died"])])   # each attempt knows what failed before
        self.assertEqual(restarts, [1, 2])

    def test_park_after_two_restarts(self):
        res = gq.play(lambda n, f: {"status": "facility", "failure": {"kind": "not_loaded", "detail": str(n)}})
        self.assertEqual((res["status"], res["attempts"], len(res["facility_failures"])), ("parked", 3, 3))
        self.assertIn("not_loaded", res["detail"])

    def test_notice_names_the_failure(self):
        t = gq.notice_text([{"kind": "relay_lost", "detail": "silent 180 s"}])
        for part in ("中继失联", "silent 180 s", "从头重开", "第 2 次开局", "不算你的时间"):
            self.assertIn(part, t)


if __name__ == "__main__":
    unittest.main()
