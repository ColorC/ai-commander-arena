"""VP cell owners, reckoned by the referee from ground units standing on or crossing a cell (the game reports none)."""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.referee import world  # noqa: E402
from arena.tests.fakegame import EnvCase  # noqa: E402

CELL = 2118                    # Blue's cell in alamo-at-soest; the others are 2321 and 1822
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


def changes(env, side):
    return [e for e in env.ref.events.items[side] if e["type"] == "vp_change"]


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Geometry(unittest.TestCase):
    def test_hex_numbers_round_trip_through_cube_coordinates(self):
        for c in range(1, 52):
            for r in range(1, 35):
                x, y, z = world.hex_cube(c * 100 + r)
                self.assertEqual(x + y + z, 0)
                self.assertEqual(world.cube_hex(x, z), c * 100 + r)

    def test_cube_distance_agrees_with_the_scenario_s_map_geometry(self):
        sc = world.Scenario("alamo-at-soest")
        d = lambda a, b: max(abs(p - q) for p, q in zip(world.hex_cube(a), world.hex_cube(b)))  # noqa: E731
        window = [c * 100 + r for c in range(18, 26) for r in range(12, 24)]
        for a in (2118, 2218, 2119):
            for h in window:
                self.assertEqual(d(a, h) == 1, 0 < sc.dist_km(a, h) < 0.6, (a, h))     # next ring is 0.87 km away

    def test_lines_are_connected_cell_by_cell_and_reversible(self):
        rnd = random.Random(7)
        for _ in range(500):
            a, b = rnd.randint(1, 51) * 100 + rnd.randint(1, 34), rnd.randint(1, 51) * 100 + rnd.randint(1, 34)
            line = world.hex_line(a, b)
            self.assertEqual((line[0], line[-1]), (a, b))
            for p, q in zip(line, line[1:]):
                self.assertEqual(max(abs(u - v) for u, v in zip(world.hex_cube(p), world.hex_cube(q))), 1, (a, b))
        self.assertEqual(world.hex_line(2118, 2118), [2118])
        self.assertEqual(world.hex_line(2110, 2125)[:3], [2110, 2111, 2112])
        self.assertIn(2118, world.hex_line(2110, 2125))


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Ownership(EnvCase):
    def test_start_owners_come_from_start_json(self):
        self.assertEqual(self.ref.world.owner, {2118: 0, 2321: 0, 1822: 1})  # the game counts 1822 for the Polish side at the start

    def test_a_ground_unit_standing_on_the_cell_takes_it_and_each_side_sees_its_own_view(self):
        self.env.move("9 MR", CELL)
        self.env.sync(60)
        self.assertEqual(self.ref.world.owner[CELL], 1)
        e0, e1 = changes(self.env, 0), changes(self.env, 1)
        self.assertEqual([(e["hex"], e["from"], e["to"], e["t"], e["vp"]) for e in e0], [(CELL, "you", "enemy", 60, 2000)])
        self.assertEqual([(e["hex"], e["from"], e["to"]) for e in e1], [(CELL, "enemy", "you")])
        self.env.wake(0)
        self.env.wake(1)
        self.assertEqual({v["hex"]: v["owner"] for v in self.ref.view(0, "vp", {})["vp"]}[CELL], "enemy")
        self.assertEqual({v["hex"]: v["owner"] for v in self.ref.view(1, "vp", {})["vp"]}[CELL], "you")

    def test_a_unit_that_crosses_the_cell_inside_one_minute_takes_it(self):
        self.env.move("9 MR", 2110)
        self.env.sync(60)
        self.assertEqual(self.ref.world.owner[CELL], 0)
        self.env.move("9 MR", 2125)                          # 2110 -> 2125 runs through 2118
        self.env.sync(120)
        self.assertEqual(self.ref.world.owner[CELL], 1)

    def test_a_unit_that_left_the_cell_this_minute_still_passed_it(self):
        self.env.move("9 MR", CELL)
        self.env.sync(60)
        self.env.move("Alpha Platoon", CELL)
        self.env.move("9 MR", 2128)
        self.env.sync(120)
        self.assertEqual(self.ref.world.owner[CELL], 1)      # Red left but passed it this minute, as Blue arrived: unchanged
        self.env.sync(180)
        self.assertEqual(self.ref.world.owner[CELL], 0)      # now only Blue is there

    def test_both_sides_in_the_same_minute_leave_the_owner_alone(self):
        self.env.move("9 MR", CELL)
        self.env.move("Alpha Platoon", CELL)
        self.env.sync(60)
        self.assertEqual(self.ref.world.owner[CELL], 0)
        self.assertEqual(changes(self.env, 0), [])
        self.env.sync(120)                                   # still both there
        self.assertEqual(changes(self.env, 1), [])
        self.env.move("Alpha Platoon", 1510)
        self.env.sync(180)                                   # Blue left, but it was on the cell at the start of the minute
        self.assertEqual(self.ref.world.owner[CELL], 0)
        self.env.sync(240)                                   # Red's unit has the cell alone
        self.assertEqual(self.ref.world.owner[CELL], 1)

    def test_helicopters_and_aircraft_do_not_take_cells(self):
        for icon in ("Helo", "Air"):
            self.env.move("9 MR", CELL, icon_type=icon)
            self.env.sync(60)
            self.assertEqual(self.ref.world.owner[CELL], 0, icon)
        self.env.move("9 MR", CELL, icon_type="Tank")
        self.env.sync(120)
        self.assertEqual(self.ref.world.owner[CELL], 1)

    def test_units_that_are_not_on_the_map_do_not_take_cells(self):
        for status in ("Dead", "Reinforcement"):
            self.env.move("9 MR", CELL, status=status)
            self.env.sync(60)
            self.assertEqual(self.ref.world.owner[CELL], 0, status)

    def test_a_cell_stays_with_its_owner_until_the_other_side_comes_back(self):
        self.env.move("9 MR", CELL)
        self.env.sync(60)
        self.env.move("9 MR", 5110)
        self.env.sync(120)
        self.assertEqual(self.ref.world.owner[CELL], 1)      # nobody there: Red keeps it
        self.assertEqual(len(changes(self.env, 0)), 1)
        self.env.move("Bravo Platoon", CELL)
        self.env.sync(180)
        self.assertEqual(self.ref.world.owner[CELL], 0)
        self.assertEqual([(e["from"], e["to"]) for e in changes(self.env, 0)], [("you", "enemy"), ("enemy", "you")])

    def test_only_the_changed_cell_gives_an_event_and_each_change_once(self):
        self.env.move("9 MR", CELL)
        for sec in (60, 120, 180):
            self.env.sync(sec)
        self.assertEqual(len(changes(self.env, 0)), 1)
        self.assertEqual(len(changes(self.env, 1)), 1)

    def test_summary_and_briefing_say_where_the_owners_come_from(self):
        self.env.wake(0)
        s = self.ref.view(0, "summary", {})
        self.assertIn("reckoning", s["vp_source"])
        self.assertIn("reckoning", self.ref.view(0, "vp", {})["source"])
        self.assertIn("helicopters and aircraft", self.ref.view(0, "briefing", {})["vp_rule"])

    def test_the_game_s_markers_field_is_ignored(self):
        self.env.sync(60, state={"sec": 60, "units": self.env.units,
                                 "markers": [{"location": CELL, "side": 1, "victory_points": 400}]})
        self.assertEqual(self.ref.world.owner[CELL], 0)


if __name__ == "__main__":
    unittest.main()
