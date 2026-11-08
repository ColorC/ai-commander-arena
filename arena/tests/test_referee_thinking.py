"""Thinking time: a count per side with no budget behind it (materials/10 2.6, changed 2026-11-04)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.referee import rules  # noqa: E402
from arena.tests.fakegame import EnvCase  # noqa: E402
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


def contact_wake_events(env, sec, no):
    """A new contact for side 0 at game second `sec` (enemy unit 9 MR seen)."""
    return [env.contact(sec, "9 MR", no, "Detected", "2x vehicle(s)")]


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Counting(EnvCase):
    def think(self, side):
        return self.ref.thinking.view(side)

    def test_starts_at_zero(self):
        self.assertEqual(self.think(0), {"total_sec": 0.0, "commander_sec": 0.0, "observe_sec": 0.0,
                                         "subagents_sec": 0.0, "prematch_sec": 0.0, "wakes": 0})

    def test_commander_seconds_and_wakes(self):
        self.env.wake(0)
        self.clock.advance(10)
        self.ref.tick(self.clock())
        self.assertEqual(self.think(0)["commander_sec"], 10)
        self.assertEqual(self.think(0)["wakes"], 1)
        self.assertEqual(self.think(1)["total_sec"], 0)            # the other side is untouched

    def test_observe_and_subagents_are_kept_apart(self):
        self.env.wake(0)
        self.ref.turn_begin(0, {"agent": "deputy"})
        self.ref.observe(0, True)
        self.clock.advance(10)
        self.ref.tick(self.clock())
        t = self.think(0)
        self.assertEqual((t["commander_sec"], t["observe_sec"], t["subagents_sec"], t["total_sec"]), (0, 10, 10, 20))

    def test_prematch_is_counted_on_its_own(self):
        self.env.sync(0, phase="Setup")
        with self.ref.lock:
            self.ref._start_turn(0, self.clock(), [{"type": "prematch"}], prematch=True)
        self.clock.advance(30)
        self.env.sync(0, phase="Setup")
        t = self.think(0)
        self.assertEqual((t["prematch_sec"], t["total_sec"], t["wakes"]), (30, 0, 0))

    def test_no_budget_a_side_that_thought_for_hours_is_still_woken(self):
        for _ in range(30):                                          # 30 full turns of 8 minutes: 4 hours
            self.env.wake(0)
            self.clock.advance(rules.COMMANDER_TURN_SEC + 1)
            self.ref.tick(self.clock())
        self.assertFalse(self.ref.agents[0])
        self.assertGreater(self.think(0)["total_sec"], 4 * 3600 - 60)
        self.env.sync(100, lines=contact_wake_events(self.env, 100, 125))
        self.env.sync(131)
        self.assertIn("commander", self.ref.agents[0])

    def test_wake_turn_end_summary_and_final_carry_it(self):
        self.env.sync(100, lines=contact_wake_events(self.env, 100, 125))
        self.env.sync(131)
        w = self.ref.wait(0, 0)
        self.assertTrue(w["woke"])
        self.assertEqual(w["thinking_sec"], 0)
        self.assertNotIn("energy", w["summary"])
        self.assertNotIn("thinking", w["summary"])     # the player is not shown its thinking time (11-04)
        self.assertEqual(self.ref.view(0, "thinking", {})["wakes"], 1)
        self.clock.advance(5)
        self.assertEqual(self.ref.view(0, "thinking", {})["commander_sec"], 5)
        out = self.ref.turn_end(0, {"turn_id": w["turn_id"]})
        self.assertEqual(out["thinking_sec"], 5)
        self.ref._finish(self.clock(), "stopped")
        with open(os.path.join(self.tmp.name, "final.json"), encoding="utf-8") as f:
            final = json.load(f)
        self.assertEqual(final["thinking"]["0"]["commander_sec"], 5)
        self.assertNotIn("energy", final)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "logs", "energy.jsonl")))


if __name__ == "__main__":
    unittest.main()
