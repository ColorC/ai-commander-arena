"""Subscriptions, wakes, turn limits (materials/10 2.6, 2.7)."""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.referee import rules  # noqa: E402
from arena.referee.core import ApiError  # noqa: E402
from arena.referee.views import est_tokens  # noqa: E402
from arena.tests.fakegame import EnvCase  # noqa: E402
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Wakes(EnvCase):
    def awake(self, side=0):
        return "commander" in self.ref.agents[side]

    def wakes(self, side=0):
        return [w for w in self.env.log("wakes") if w.get("kind") == "wake" and w["side"] == side]

    def kill(self, unit="Alpha Platoon"):
        self.env.units[unit]["s"]["status"] = "Dead"

    def end(self, side=0, **body):
        return self.ref.turn_end(side, {"turn_id": self.ref.turn_id[side], **body})

    def test_default_subscriptions(self):
        self.assertEqual([s["type"] for s in self.ref.wake.subs[0]],
                         ["vp_change", "unit_out", "strength_drop", "new_contact", "timer", "script_alarm"])
        self.assertEqual(self.ref.wake.subs[0][2]["points"], 5)
        self.assertEqual(self.ref.wake.subs[0][4]["every_min"], 60)

    def test_combat_is_not_a_default_wake(self):
        self.env.units["Alpha Platoon"]["u"][0]["ammo"][0]["rounds"] = "6"
        self.env.sync(60)
        self.env.sync(100)
        self.assertIn("combat", [e["type"] for e in self.ref.events.items[0]])
        self.assertFalse(self.awake())

    def test_unit_out_wakes_after_the_30_second_window(self):
        self.kill()
        self.env.sync(100)
        self.env.sync(129)
        self.assertFalse(self.awake())
        self.env.sync(130)
        self.assertTrue(self.awake())
        w = self.ref.wait(0, 0)
        self.assertTrue(w["woke"])
        self.assertEqual([r["type"] for r in w["reasons"]], ["unit_out"])
        self.assertIn("unit_out", [e["type"] for e in w["summary"]["events"]])
        self.assertFalse(self.awake(1))                                      # the other side never heard of it

    def test_events_inside_30_seconds_make_one_wake(self):
        self.kill()
        self.env.sync(100)
        self.env.sync(120, lines=[self.env.contact(120, "9 MR", 120)])
        self.env.sync(130)
        self.assertEqual(len(self.wakes()), 1)
        self.assertEqual(sorted(r["type"] for r in self.wakes()[0]["reasons"]), ["new_contact", "unit_out"])
        self.end()
        self.env.sync(400)
        self.assertEqual(len(self.wakes()), 1)

    def test_events_during_a_turn_do_not_wake_again(self):
        self.env.wake(0)
        self.kill()
        self.env.sync(100)
        self.env.sync(200)
        self.end()
        self.env.sync(400)
        self.assertFalse(self.awake())
        self.assertIn("unit_out", [e["type"] for e in self.ref.events.items[0]])      # in the feed, no wake

    def test_new_contacts_merge_for_ten_minutes_by_default(self):
        self.env.sync(100, lines=[self.env.contact(100, "9 MR", 120)])
        self.env.sync(130)
        self.assertTrue(self.awake())
        self.end()
        self.env.sync(400, lines=[self.env.contact(400, "Red Tank Company", 144)])    # 300 s after the first
        self.env.sync(440)
        self.assertFalse(self.awake())
        self.assertTrue(any(w.get("kind") == "merged" for w in self.env.log("wakes")))
        self.env.sync(500, lines=[self.env.contact(450, "Red Tank Company", 144, "AutoSpot", "x")])   # lost ...
        self.env.sync(1100, lines=[self.env.contact(1100, "Red Tank Company", 144, "Classified", "2 x Tank")])
        self.env.sync(1140)
        self.assertTrue(self.awake())                                        # ... and seen again after 600 s: new

    def test_commander_turn_ends_after_eight_minutes_and_commands_stay(self):
        self.env.wake(0)
        cid = self.ref.submit(0, {"kind": "orders", "unit": "Alpha Platoon", "orders": [{"orders": "Hold"}]})["cmd_id"]
        self.clock.advance(rules.COMMANDER_TURN_SEC - 1)
        self.ref.tick(self.clock())
        self.assertTrue(self.awake())
        self.clock.advance(2)
        self.ref.tick(self.clock())
        self.assertFalse(self.awake())
        end = [w for w in self.env.log("wakes") if w.get("kind") == "agent_end"][-1]
        self.assertEqual(end["reason"], "timeout")
        self.assertAlmostEqual(end["seconds"], 481, delta=1.1)
        self.assertEqual(self.ref.queue.cmds[cid]["status"], "queued")      # kept
        with self.assertRaises(ApiError) as cm:
            self.ref.view(0, "summary", {})
        self.assertEqual(cm.exception.code, 409)
        self.assertIn("timeout", cm.exception.msg)
        # counted for the 480 seconds, not the 481st
        self.assertEqual(self.ref.thinking.view(0)["commander_sec"], 480)

    def test_end_turn_needs_the_current_turn_id(self):
        self.env.wake(0)
        with self.assertRaises(ApiError) as cm:
            self.ref.turn_end(0, {"turn_id": "s0-t9999"})
        self.assertEqual(cm.exception.code, 409)
        self.assertTrue(self.awake())

    def test_subagents_at_most_two_ten_minutes_each(self):
        self.env.wake(0)
        self.ref.turn_begin(0, {"agent": "deputy"})
        self.ref.turn_begin(0, {"agent": "chief"})
        for body, code in (({"agent": "third"}, 429), ({"agent": "deputy"}, 409), ({"agent": "commander"}, 400),
                           ({"agent": "Bad Name"}, 400)):
            with self.assertRaises(ApiError) as cm:
                self.ref.turn_begin(0, body)
            self.assertEqual(cm.exception.code, code, body)
        self.end()                                                           # the commander is done, the deputies work on
        self.assertEqual(self.ref.gate(), "sleep")
        self.clock.advance(rules.SUBAGENT_TASK_SEC + 1)
        self.ref.tick(self.clock())
        self.assertEqual(self.ref.agents[0], {})
        self.assertEqual(self.ref.gate(), "off")

    def test_subagent_cannot_change_subscriptions(self):
        self.env.wake(0)
        self.ref.turn_begin(0, {"agent": "deputy"})
        with self.assertRaises(ApiError) as cm:
            self.ref.turn_end(0, {"agent": "deputy", "subscriptions": []})
        self.assertEqual(cm.exception.code, 403)

    def test_end_turn_replaces_subscriptions_or_refuses_them_whole(self):
        self.env.wake(0)
        ok = self.end(subscriptions=[{"type": "combat"}, {"type": "timer", "every_min": 15},
                                     {"type": "new_contact", "area": {"box": [1, 20, 5, 30]}, "min_size": 3}])
        self.assertEqual([s["type"] for s in ok["subscriptions"]], ["combat", "timer", "new_contact"])
        self.assertEqual([s["type"] for s in self.ref.wake.subs[0]], ["combat", "timer", "new_contact"])
        for bad in ([{"type": "nonsense"}], [{"type": "timer", "every_min": 5}], [{"type": "timer"}],
                    [{"type": "timer", "at": 10, "every_min": 20}], [{"type": "contact_near", "center": 9999, "radius_km": 2}],
                    [{"type": "strength_drop", "points": 0}], [{"type": "combat", "bogus": 1}], "combat",
                    [{"type": "combat"}] * (rules.MAX_SUBSCRIPTIONS + 1), [{"type": "new_contact", "area": {"box": [1]}}]):
            self.env.wake(0)
            with self.assertRaises(ApiError, msg=str(bad)) as cm:
                self.end(subscriptions=bad)
            self.assertEqual(cm.exception.code, 400)
            self.assertTrue(self.awake())                                    # refused: the turn stays open
            self.assertEqual([s["type"] for s in self.ref.wake.subs[0]], ["combat", "timer", "new_contact"])

    def test_subscription_filters(self):
        self.env.wake(0)
        self.end(subscriptions=[{"type": "new_contact", "area": {"box": [40, 53, 1, 34]}, "min_size": 3}])
        self.env.sync(100, lines=[self.env.contact(100, "9 MR", 120, "Detected", "2x vehicle(s)")])     # too small
        self.env.sync(140)
        self.assertFalse(self.awake())
        self.env.sync(200, lines=[self.env.contact(200, "Red Tank Company", 144, "Detected", "3x vehicle(s)")])
        self.env.sync(240)
        self.assertTrue(self.awake())
        self.end(subscriptions=[{"type": "new_contact", "area": {"box": [1, 10, 1, 34]}}])
        self.env.sync(300, lines=[self.env.contact(250, "Red Tank Company", 144, "AutoSpot", "x")])
        self.env.sync(900, lines=[self.env.contact(900, "Red Tank Company", 144, "Classified", "2 x Tank")])
        self.env.sync(940)                                                   # regained, but in the east: outside the box
        self.assertFalse(self.awake())

    def test_default_timer_every_hour(self):
        self.env.sync(3590)
        self.env.sync(3600)
        self.env.sync(3629)
        self.assertFalse(self.awake())
        self.env.sync(3630)
        self.assertTrue(self.awake())
        self.assertEqual(self.wakes()[0]["reasons"][0]["type"], "timer")
        self.end()
        self.env.sync(7200)
        self.env.sync(7230)
        self.assertEqual(len(self.wakes()), 2)

    def test_one_shot_timer(self):
        self.env.wake(0)
        self.end(subscriptions=[{"type": "timer", "at": 500}])
        self.env.sync(499)
        self.env.sync(530)
        self.assertTrue(self.awake())
        self.end()
        self.env.sync(1500)
        self.env.sync(1600)
        self.assertEqual(len([w for w in self.wakes() if w["reasons"][0]["type"] == "timer"]), 1)   # one-shot

    def test_script_alarm_wakes_at_most_every_five_minutes(self):
        with self.ref.lock:
            self.ref.emit(0, "script_alarm", 100, script="s", text="a")
        self.env.sync(130)
        self.assertTrue(self.awake())
        self.end()
        with self.ref.lock:
            self.ref.emit(0, "script_alarm", 250, script="s", text="b")
        self.env.sync(300)
        self.assertFalse(self.awake())
        with self.ref.lock:
            self.ref.emit(0, "script_alarm", 420, script="s", text="c")          # 320 s after the first wake
        self.env.sync(460)
        self.assertTrue(self.awake())

    def test_strength_drop_events_by_threshold(self):
        subs = self.env.units["Big Company"]["u"]
        for i in range(2):
            subs[i]["status"] = "Fallen Out"                                  # 2 of 26 = 7.7 points
        self.env.sync(60)
        drops = [e for e in self.ref.events.items[0] if e["type"] == "strength_drop"]
        self.assertEqual([(e["drop"], e["threshold"]) for e in drops], [(7, 5)])
        self.env.sync(100)
        self.assertTrue(self.awake())
        self.end()
        for i in range(2, 5):
            subs[i]["status"] = "Dead"                                        # 5 of 26 = 19 points since the start,
        self.env.sync(200)                                                    # 3 of 26 = 11.5 since the last wake
        drops = [e for e in self.ref.events.items[0] if e["type"] == "strength_drop"]
        self.assertEqual((drops[-1]["drop"], drops[-1]["threshold"]), (11, 5))       # only subscribed marks

    def test_contact_near_fires_once_when_a_contact_comes_within_r(self):
        self.env.wake(0)
        self.end(subscriptions=[{"type": "contact_near", "center": 1612, "radius_km": 3}])
        self.env.sync(100, lines=[self.env.contact(90, "Red Tank Company", 144, "Detected", "3x vehicle(s)")])
        self.env.sync(160)
        self.assertEqual([e for e in self.ref.events.items[0] if e["type"] == "contact_near"], [])
        self.env.units["Red Tank Company"]["s"]["location"] = 1814
        self.env.sync(220)
        near = [e for e in self.ref.events.items[0] if e["type"] == "contact_near"]
        self.assertEqual(len(near), 1)
        self.assertEqual((near[0]["contact"], near[0]["pos"]), (144, 1814))
        self.env.sync(260)
        self.assertTrue(self.awake())
        self.env.sync(280)
        self.assertEqual(len([e for e in self.ref.events.items[0] if e["type"] == "contact_near"]), 1)

    def test_summary_stays_under_the_token_budget(self):
        self.env.wake(0)
        with self.ref.lock:
            for i in range(600):
                self.ref.emit(0, "combat", 10 + i, unit="Alpha Platoon", kind="fire", n=3, text="x" * 120)
        s = self.ref.view(0, "summary", {})
        self.assertLessEqual(est_tokens(s), rules.SUMMARY_MAX_TOKENS)
        self.assertTrue(s.get("truncated"))
        self.assertEqual(s["events"][-1]["t"], 609)                           # the newest events are the ones kept

    def test_summary_content(self):
        self.env.sync(100, lines=[self.env.contact(90, "9 MR", 120, "Classified", "2 x Tank")])
        self.env.wake(0)
        s = self.ref.view(0, "summary", {})
        self.assertNotIn("thinking", s)                    # no thinking time in what the player reads (11-04)
        for key in ("game", "turn", "force", "events", "own_changes", "contacts_in_view", "vp", "queue",
                    "next_orders_phase"):
            self.assertIn(key, s)
        self.assertEqual(s["contacts_in_view"][0]["contact"], 120)
        self.assertEqual(s["force"]["pct"], 100.0)
        self.assertNotIn("deadline_in", s["turn"])         # the backstop is not shown to the player
        self.assertEqual({v["hex"]: v["owner"] for v in s["vp"]}, {2118: "you", 2321: "you", 1822: "enemy"})  # 1822 is Polish at the start

    def test_observe_mode_delivers_every_minute(self):
        self.env.wake(0)
        self.ref.observe(0, True)
        self.assertFalse(self.ref.wait(0, 0)["woke"])
        self.env.sync(60)
        w = self.ref.wait(0, 0)
        self.assertTrue(w["woke"] and w["observe"])
        self.assertFalse(self.ref.wait(0, 0)["woke"])
        self.env.sync(120)
        self.assertTrue(self.ref.wait(0, 0)["woke"])
        self.ref.observe(0, False)
        with self.assertRaises(ApiError):
            self.ref.wait(0, 0)                                               # turn open, not observing: end_turn first

    def test_wait_blocks_until_a_wake_arrives(self):
        out, started = [], threading.Event()

        def waiter():
            started.set()
            out.append(self.ref.wait(0, 10))
        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        started.wait(5)
        self.kill()
        self.env.sync(100)
        self.env.sync(130)
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertTrue(out[0]["woke"])

    def test_wait_times_out_quietly(self):
        r = self.ref.wait(0, 0.05)
        self.assertEqual(r["woke"], False)
        self.assertEqual(r["thinking_sec"], 0)

    def test_pause_freezes_thinking_time_and_turn_clock(self):
        self.env.wake(0)
        self.ref.pause(True)
        self.clock.advance(1000)
        self.ref.tick(self.clock())
        self.assertTrue(self.awake())
        self.assertEqual(self.ref.thinking.total(0), 0)
        self.ref.pause(False)
        self.clock.advance(10)
        self.ref.tick(self.clock())
        self.assertEqual(self.ref.thinking.total(0), 10)
        a = self.ref.agents[0]["commander"]              # the pause did not use up the (hidden) backstop
        self.assertAlmostEqual(a["deadline"] - self.clock(), rules.COMMANDER_TURN_SEC - 10, delta=0.01)


if __name__ == "__main__":
    unittest.main()
