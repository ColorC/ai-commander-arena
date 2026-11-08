"""Fog of war: a side's views hold enemy information only from its own contact table, never an enemy unit name."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.referee.core import ApiError  # noqa: E402
from arena.referee.views import FogViolation  # noqa: E402
from arena.tests.fakegame import EnvCase  # noqa: E402

ENEMY_NAMES = ("9 MR", "Red Tank Company")
FULL_TOE = "3 x T-72 and 1 x Truck and 2 x Strelas"
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Views(EnvCase):
    def view_text(self, side, what):
        self.env.wake(side)
        v = self.ref.view(side, what, {})
        return v, json.dumps(v, ensure_ascii=False)

    def test_enemy_enters_only_through_a_contact(self):
        self.env.sync(120, lines=[self.env.contact(100, "Red Tank Company", 144, "Classified", "2 x Recce")])
        v, text = self.view_text(0, "contacts")
        self.assertEqual(len(v["contacts"]), 1)
        c = v["contacts"][0]
        self.assertEqual((c["contact"], c["state_name"], c["report"], c["visible"]), (144, "Classified", "2 x Recce", True))
        self.assertEqual(c["last_pos"], 5212)                         # true position at the time it was spotted
        self.assertEqual(c["first_seen"], 100)
        for name in ENEMY_NAMES:
            self.assertNotIn(name, text)
        self.env.wake(1)
        self.assertEqual(self.ref.view(1, "contacts", {}), {"contacts": []})

    def test_summary_and_units_never_contain_enemy_names(self):
        self.env.sync(120, lines=[self.env.contact(100, "Red Tank Company", 144, "Identified", "3 x T-72"),
                                  self.env.contact(110, "9 MR", 120, "Detected", "5x vehicle(s)")])
        for what in ("summary", "units", "contacts", "vp", "queue", "thinking", "events"):
            _, text = self.view_text(0, what)
            for name in ENEMY_NAMES:
                self.assertNotIn(name, text, what)
        for what in ("summary", "units"):
            _, text = self.view_text(1, what)
            for name in ("Alpha Platoon", "TF Alamo", "Big Company"):
                self.assertNotIn(name, text, what)

    def test_unspotted_unit_is_invisible_even_if_the_log_names_it(self):
        # state 4 lines carry the true composition: they must create nothing and show nothing
        self.env.sync(60, lines=[self.env.contact(30, "Red Tank Company", 144, "AutoSpot", FULL_TOE)])
        v, text = self.view_text(0, "summary")
        self.assertEqual(v["contacts_in_view"], [])
        self.assertEqual(self.ref.tables[0].by_no, {})
        self.assertNotIn("T-72", text)
        self.assertNotIn("Truck", text)

    def test_report_of_a_lost_contact_is_the_last_seen_one_not_the_autospot_one(self):
        self.env.sync(60, lines=[self.env.contact(30, "Red Tank Company", 144, "Classified", "3 x Tank")])
        self.env.sync(120, lines=[self.env.contact(100, "Red Tank Company", 144, "AutoSpot", FULL_TOE)])
        v, text = self.view_text(0, "contacts")
        self.assertEqual(v["contacts"][0]["report"], "3 x Tank")
        self.assertFalse(v["contacts"][0]["visible"])
        self.assertNotIn("T-72", text)

    def test_position_follows_a_visible_contact_and_freezes_when_lost(self):
        self.env.sync(60, lines=[self.env.contact(30, "Red Tank Company", 144, "Detected", "3x vehicle(s)")])
        self.env.units["Red Tank Company"]["s"]["location"] = 5015
        self.env.sync(120)
        self.assertEqual(self.ref.tables[0].by_no[144].last_pos, 5015)
        self.env.sync(130, lines=[self.env.contact(125, "Red Tank Company", 144, "AutoSpot", "x")])
        self.env.units["Red Tank Company"]["s"]["location"] = 4815
        self.env.sync(180)
        c = self.ref.tables[0].by_no[144]
        self.assertEqual((c.last_pos, c.visible), (5015, False))

    def test_plain_lines_without_a_state_word(self):
        line = ("2026-10-30 08:44:41.983 - (07:33:24) UpdateSpottedStepsAndReport() Red Tank Company: Contact 101, "
                "Counter-Battery contact (6 tubes), spotted steps 6, steps 8")
        self.env.sync(2100, lines=[line])
        c = self.ref.tables[0].by_no[101]
        self.assertEqual((c.state, c.report), (5, "Counter-Battery contact (6 tubes)"))

    def test_scenario_orders_naming_an_enemy_unit_are_hidden(self):
        """Real data: the Polish units start with "Screen" orders whose target_unit is an American unit."""
        self.env.units["9 MR"]["o"][0].update({"orders": "Screen", "target_unit": "Alpha Platoon"})
        self.env.sync(60)
        v, text = self.view_text(1, "units")
        self.assertNotIn("Alpha Platoon", text)
        row = next(u for u in v["units"] if u["name"] == "9 MR")
        self.assertEqual(row["order"], {"orders": "Screen", "target_hidden": True})
        u, text = self.view_text(1, "unit/9 MR")
        self.assertNotIn("Alpha Platoon", text)
        # once the side holds that contact the order shows the contact number instead
        self.env.sync(120, lines=[self.env.contact(100, "Alpha Platoon", 130, "Detected", "2x vehicle(s)")])
        v, text = self.view_text(1, "units")
        self.assertNotIn("Alpha Platoon", text)
        row = next(u for u in v["units"] if u["name"] == "9 MR")
        self.assertEqual(row["order"], {"orders": "Screen", "target_contact": 130})

    def test_enemy_unit_detail_is_not_available(self):
        self.env.wake(0)
        with self.assertRaises(ApiError) as a:
            self.ref.view(0, "unit/9 MR", {})
        with self.assertRaises(ApiError) as b:
            self.ref.view(0, "unit/no such unit", {})
        self.assertEqual((a.exception.code, a.exception.msg), (b.exception.code, b.exception.msg))   # no existence leak

    def test_events_are_per_side(self):
        self.env.sync(120, lines=[self.env.contact(100, "Red Tank Company", 144, "Detected", "2x vehicle(s)")])
        e0 = [e["type"] for e in self.ref.events.items[0]]
        e1 = [e["type"] for e in self.ref.events.items[1]]
        self.assertIn("new_contact", e0)
        self.assertNotIn("new_contact", e1)


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Assertion(EnvCase):
    """The assertion itself: it must stop a view that carries something the table does not justify."""

    def setUp(self):
        super().setUp()
        self.env.sync(120, lines=[self.env.contact(100, "Red Tank Company", 144, "Classified", "2 x Recce")])
        self.env.wake(0)

    def check(self, view, what="test"):
        self.ref.views.check(0, view, what)

    def violations(self):
        return [r for r in self.env.log("fog") if r["kind"] == "fog_violation"]

    def test_good_entry_passes(self):
        self.check({"contacts": [self.ref.tables[0].by_no[144].public()]})
        self.assertEqual(self.violations(), [])

    def test_inserted_undiscovered_enemy_is_stopped_and_logged(self):
        self.ref.views.contacts = lambda side: {"contacts": [{"contact": 777, "state": 3, "report": "x"}]}   # never spotted
        with self.assertRaises(ApiError) as cm:
            self.ref.view(0, "contacts", {})
        self.assertEqual(cm.exception.code, 503)
        v = self.violations()
        self.assertEqual(len(v), 1)
        self.assertEqual((v[0]["side"], v[0]["view"]), (0, "contacts"))
        self.assertEqual(self.ref.fog_violations, 1)

    def test_enemy_name_in_the_view_is_stopped(self):
        for view in ({"contacts": [{"contact": 144, "unit": "Red Tank Company"}]},
                     {"note": "enemy at Red Tank Company"},
                     {"own": {"parent": "9 MR"}},
                     {"nested": [[{"x": "see 9 MR."}]]}):
            with self.assertRaises(FogViolation, msg=str(view)):
                self.check(view)
        self.assertEqual(len(self.violations()), 4)

    def test_report_or_position_the_table_never_had_is_stopped(self):
        good = self.ref.tables[0].by_no[144].public()
        for patch in ({"report": "5 x T-72"}, {"last_pos": 1234}, {"state": 1}, {"extra": 1}, {"contact": 9999}):
            with self.assertRaises(FogViolation, msg=str(patch)):
                self.check({"contacts": [{**good, **patch}]})

    def test_same_words_in_a_verified_report_are_not_a_leak(self):
        """A vehicle type that equals a unit name (CEV, ...) shown as the game's own report text is fine."""
        self.ref.tables[0].by_no[144].reports.add("1 x Red Tank Company")
        good = {**self.ref.tables[0].by_no[144].public(), "report": "1 x Red Tank Company"}
        self.check({"contacts": [good]})

    def test_own_script_alarm_text_naming_an_enemy_unit_is_not_a_leak(self):
        """The side wrote that text itself (a script written for the other side of the map names its units)."""
        self.check({"events": [{"type": "script_alarm", "script": "w", "text": "consider Red Tank Company barrage"}]})
        with self.assertRaises(FogViolation):
            self.check({"events": [{"type": "combat", "text": "consider Red Tank Company barrage"}]})

    def test_names_are_matched_on_boundaries(self):
        self.check({"own": "Alpha Platoon of 9 Mechanised Rifle", "x": "Red Tank Companies"})   # not the enemy's names


if __name__ == "__main__":
    unittest.main()
