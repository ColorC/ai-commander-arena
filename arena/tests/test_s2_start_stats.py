"""tools/s2_start_stats.py: each player command in one class, by the CLI's own noun and verb resolution."""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("s2_start_stats", os.path.join(ROOT, "tools", "s2_start_stats.py"))
stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stats)


class ClassifyTest(unittest.TestCase):
    def test_classes(self):
        cases = {
            "unit show 1/PzJgKp": ("look", "unit show"),
            "unit '1/2/PzGrenBtl 111' sop direct_support=All": ("act", "unit sop"),
            "unit 1/A order move 2118": ("act", "unit order"),
            "unit 1/A": ("look", "unit show"),
            "list units": ("look", "unit list"),
            "order add 1/A move 2118": ("act", "order add"),
            "order add --help": ("docs", "help"),
            "fire add 3./FArtBtl barrage 2118": ("act", "fire add"),
            "fire report": ("look", "fire report"),
            "script test watch": ("check", "script test"),
            "order lint 1/A move 2118": ("check", "order lint"),
            "doc search barrage": ("docs", "doc search"),
            "help order": ("docs", "help"),
            "note append": ("note", "note append"),
            "turn end": ("turn", "turn end"),
            "turn status": ("look", "turn status"),
            "score": ("look", "score show"),
            "arena map show 2118": ("look", "map show"),
        }
        for cmd, want in cases.items():
            self.assertEqual(stats.classify(cmd), want, cmd)


if __name__ == "__main__":
    unittest.main()
