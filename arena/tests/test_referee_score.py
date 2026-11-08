"""The share formula (arena/referee/scoring.py, World.vp): Runner subunits' VP + held VP cells at the game's value."""
import json
import os
import unittest

from arena.referee import world
from arena.referee.scoring import share

SC = "quietly-flows-the-donau"
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


def _unit(side, era, loc, subs, status="Active"):
    return {"s": {"side": side, "national_era": era, "location": loc, "status": status, "icon_type": "Tank"},
            "u": [{"name": n, "status": st} for n, st in subs]}


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Formula(unittest.TestCase):
    def test_the_armored_fist_end_screens(self):
        # af-pb4 11-01: 1st Bde 33,305 surviving + 500 locations, 7. PD 28,655 + 6,000 -> 49 %; af-ai1b: 36 %
        self.assertEqual(round(share(33305 + 500, 28655 + 6000)), 49)
        self.assertEqual(round(share(12612 + 0, 38893 - 23225 + 6500)), 36)
        self.assertIsNone(share(0, 0))

    def test_runners_and_cells_count_fallen_out_does_not(self):
        sc = world.Scenario(SC)
        table = json.load(open(os.path.join(world.DATA_ROOT, SC, "vp-table.json"), encoding="utf-8"))["table"]
        wg = next(r for r in table if r["era"].startswith("CW 80s West-German"))
        cz = next(r for r in table if r["era"].startswith("CW 80s Czechoslovakia"))
        w = world.World(sc)
        w.update({"sec": 0, "units": {
            "B1": _unit(0, wg["era"], 101, [(wg["name"], "Runner")] * 3),
            "R1": _unit(1, cz["era"], 3001, [(cz["name"], "Runner")] * 2),
            "R2": _unit(1, cz["era"], 3002, [(cz["name"], "Runner")], status="Reinforcement")}})
        cells0 = sum(c["vp"] for c in sc.vp_cells.values() if c["owner"] == 0)
        self.assertEqual(cells0, 7000)                          # briefing 3000 + 1500 + 1500 + 1000 = the game's value
        self.assertEqual(w.vp(0), {"forces": round(3 * wg["vp"]), "cells": cells0, "total": round(3 * wg["vp"]) + cells0})
        self.assertEqual(w.vp(1)["forces"], round(3 * cz["vp"]))  # a unit not yet arrived counts
        w.update({"sec": 60, "units": {
            "B1": _unit(0, wg["era"], 101, [(wg["name"], "Runner"), (wg["name"], "Fallen Out"), (wg["name"], "Dead")]),
            "R1": _unit(1, cz["era"], 3001, [(cz["name"], "Runner")] * 2),
            "R2": _unit(1, cz["era"], 3002, [(cz["name"], "Runner")], status="Reinforcement")}})
        self.assertEqual(w.vp(0)["forces"], round(wg["vp"]))
        self.assertEqual(w.start_vp[0]["forces"], round(3 * wg["vp"]))


if __name__ == "__main__":
    unittest.main()
