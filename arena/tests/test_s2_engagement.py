"""tools/s2_engagement.py: a game fought when each side lost at least 10 points of forces; VP cells changing hands
alone (the 11-03 formal games) do not count."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import s2_engagement  # noqa: E402


def game(d, force, events):
    os.makedirs(os.path.join(d, "logs"))
    with open(os.path.join(d, "logs", "score.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"sec": 18000, "force_pct": force, "share0": 50.0}) + "\n")
    with open(os.path.join(d, "logs", "events.jsonl"), "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return d


class Measure(unittest.TestCase):
    def test_cells_walked_into_are_not_a_fight(self):   # formal-1 of 11-03
        with tempfile.TemporaryDirectory() as d:
            m = s2_engagement.measure(game(d, {"0": 96.0, "1": 97.7}, [
                {"side": 0, "t": 2100, "type": "vp_change", "hex": 2223, "vp": 1500, "from": "you", "to": "enemy"},
                {"side": 1, "t": 2100, "type": "vp_change", "hex": 2223, "vp": 1500, "from": "enemy", "to": "you"},
                {"side": 0, "t": 11580, "type": "combat", "unit": "x", "kind": "fire", "n": 1}]))
        self.assertFalse(m["fought"])
        self.assertEqual(m["vp_changes"], [{"min": 35, "hex": 2223, "to": "side 1"}])
        self.assertEqual((m["sides"]["0"]["first_combat_min"], m["sides"]["1"]["first_combat_min"]), (193, None))

    def test_both_sides_losing_ten_points_is_a_fight(self):
        with tempfile.TemporaryDirectory() as d:
            m = s2_engagement.measure(game(d, {"0": 87.9, "1": 81.3}, [{"side": 1, "t": 600, "type": "unit_out", "unit": "y"}]))
        self.assertTrue(m["fought"])
        self.assertEqual((m["sides"]["0"]["lost_pct"], m["sides"]["1"]["units_out"]), (12.1, 1))


if __name__ == "__main__":
    unittest.main()
