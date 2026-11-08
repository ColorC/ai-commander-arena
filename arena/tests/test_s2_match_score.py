"""tools/s2_match_score.py: main score = own shares over both games; a tie (under 2 points) goes to more losses
inflicted, not to less energy spent (that rewarded both sides doing nothing)."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import s2_match_score  # noqa: E402


def game(gid, blue, red, shares, inflicted):
    return {"id": gid, "kind": "formal", "seats": {"0": f"players/{blue}", "1": f"players/{red}"},
            "scorecard": {s: {"share": shares[i], "result": "Contested Result", "you": {"inflicted_vp": inflicted[i]}}
                          for i, s in enumerate(("0", "1"))}}


class Score(unittest.TestCase):
    def test_main_score_decides(self):   # season 2 formal of 11-03: B 105, A 95
        r = s2_match_score.score([game("formal-1", "A", "B", (49, 51), (881, 1703)),
                                  game("formal-2", "B", "A", (54, 46), (5503, 1550))])
        self.assertEqual((r["main_score"], r["winner"], r["decided_by"]), ({"A": 95, "B": 105}, "B", "main score"))

    def test_a_tie_goes_to_more_losses_inflicted(self):
        r = s2_match_score.score([game("formal-1", "A", "B", (56, 44), (3000, 500)),
                                  game("formal-2", "B", "A", (55.5, 44.5), (900, 100))])
        self.assertEqual(r["main_score"], {"A": 100.5, "B": 99.5})
        self.assertEqual((r["inflicted_vp"], r["winner"]), ({"A": 3100, "B": 1400}, "A"))
        self.assertIn("more losses inflicted", r["decided_by"])

    def test_both_idle_is_a_tie_with_no_winner(self):
        r = s2_match_score.score([game("formal-1", "A", "B", (56, 44), (0, 0)), game("formal-2", "B", "A", (56, 44), (0, 0))])
        self.assertEqual((r["winner"], r["decided_by"]), (None, "tie on main score and losses inflicted"))

    def test_the_last_record_of_a_game_counts_and_sides_must_swap(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "games.jsonl"), "w", encoding="utf-8") as f:
                for r in (game("formal-1", "A", "B", (40, 60), (0, 0)), {"id": "ex-p0-1", "kind": "exercise"},
                          game("formal-1", "A", "B", (49, 51), (881, 1703)), game("formal-2", "B", "A", (54, 46), (5503, 1550))):
                    f.write(json.dumps(r) + "\n")
            games = s2_match_score.formal_games(d)
        self.assertEqual([g["scorecard"]["0"]["share"] for g in games], [49, 54])
        with self.assertRaises(SystemExit):
            s2_match_score.score([games[0], games[0]])


if __name__ == "__main__":
    unittest.main()
