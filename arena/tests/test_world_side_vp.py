"""A VP location worth more to one side than to the other (start.json [hex, vp to side 0, vp to side 1], from
tools/fpc_objectives.py: High Sticking's 1507 is 500 to the Canadians, 1,500 to the Soviets)."""
import json
import os
import tempfile
import unittest

from arena.referee.world import Scenario, World


def scenario(d):
    os.makedirs(os.path.join(d, "x"))
    start = {"name": "X", "h_hour": "04:00", "hours": 4, "commanders": ["A", "B"], "levels": [[80, 70, 60], [80, 70, 60]],
             "start_areas": [[1, 5, 1, 5], [10, 15, 1, 5]], "map_km": [10, 10],
             "vp_locations": {"Side 0": [[1507, 500, 1500], [2018, 500]], "Unclaimed": [[2504, 2500]], "Side 1": [[3715, 1500, 500]]}}
    with open(os.path.join(d, "x", "start.json"), "w", encoding="utf-8") as f:
        json.dump(start, f)
    return Scenario("x", data_root=d)


class SideVp(unittest.TestCase):
    def test_each_side_counts_its_own_value(self):
        with tempfile.TemporaryDirectory() as d:
            sc = scenario(d)
        self.assertEqual((sc.cell_vp(1507, 0), sc.cell_vp(1507, 1), sc.cell_vp(2018, 1)), (500, 1500, 500))
        self.assertEqual(sc.vp_fields(1507, 1), {"vp": 1500, "enemy_vp": 500})
        self.assertEqual(sc.vp_fields(2504, 0), {"vp": 2500})                    # the same to both: no enemy_vp
        w = World(sc)
        w.owner[1507] = 1                                                        # the Soviets took 1507
        held = {s: sum(sc.cell_vp(h, s) for h in sc.vp_cells if w.owner.get(h) == s) for s in (0, 1)}
        self.assertEqual(held, {0: 500, 1: 1500 + 500})


if __name__ == "__main__":
    unittest.main()
