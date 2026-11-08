"""Audit of the game's Lua input log against the referee's release records (materials/10 2.4)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.tests.fakegame import EnvCase, lua_line, release_call  # noqa: E402

MOVE = {"kind": "orders", "unit": "Alpha Platoon", "orders": [{"orders": "Move Hasty", "destination_location": 1713}]}
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Audit(EnvCase):
    def clock_sec(self, sec):
        return self.env.start_clock + sec

    def mismatches(self):
        return [r for r in self.env.log("audit") if r["kind"] == "audit_mismatch"]

    def released_game(self):
        """Two commands released in side 0's phase, with the relay's LUA> lines delivered."""
        self.env.wake(0)
        self.ref.submit(0, MOVE)
        self.ref.submit(0, {"kind": "orders", "unit": "Bravo Platoon", "orders": [{"orders": "Hold"}]})
        rels, last = self.env.stop(100, 0)
        self.env.sync(102, lines=[lua_line(self.clock_sec(101), "arena_f9()")])
        return rels

    def test_log_that_matches_the_releases_has_no_mismatch(self):
        rels = self.released_game()
        self.assertEqual(len(rels), 1)
        s = self.ref.audit.finalize(200)
        self.assertEqual(s["mismatches"], 0)
        self.assertEqual(s["matched"], 2)                                    # the release call and the F9 call
        self.assertEqual(self.mismatches(), [])
        self.assertEqual(len([r for r in self.env.log("audit") if r["kind"] == "match"]), 1)

    def test_an_extra_unreleased_call_is_reported(self):
        self.released_game()
        self.env.sync(110, lines=[lua_line(self.clock_sec(105), 'SetUnitOrders("Alpha Platoon", {})')])
        s = self.ref.audit.finalize(200)
        self.assertEqual(s["mismatches"], 1)
        m = self.mismatches()[0]
        self.assertEqual((m["why"], m["names"]), ("unauthorized_call", ["SetUnitOrders"]))

    def test_every_forbidden_call_form_is_caught(self):
        for text in ('GameAPI.KillUnit("x")', 'print(SetUnitStatus("a", {location = 5}))', "AddUnit('x')", "PlaceMarker(1)",
                     "local x = 1", "arena_f9(); Pause()", "os.execute('calc')", "SetUnitDetection('a', 4)",
                     'SetUnitSubunitsAmmo("a")', "x = 5"):
            before = len(self.mismatches())
            self.env.sync(120, lines=[lua_line(self.clock_sec(110), text)])
            self.assertEqual(len(self.mismatches()), before + 1, text)

    def test_relay_helpers_and_the_probe_names_pass(self):
        lines = ['print("click", p0_click("TfIllustratedMessage", "&Proceed"))', 'print("rep", p0_shot("TfIllustratedMessage", "end-battle-over"))',
                 "arena_f9()", 'arena_click("TfTimedMessage", "&Proceed")', 'arena_close("TfRecap")',
                 'arena_shot("X", "end")', "arena_state_now()", "arena_snapshot()", "arena_windows()",
                 'print("SetUnitOrders only in a string")',
                 "print([==[ KillUnit(1) ]==])"]
        self.env.sync(120, lines=[lua_line(self.clock_sec(110), t) for t in lines])
        self.assertEqual(self.mismatches(), [])
        self.assertEqual(self.ref.audit.matched, len(lines))

    def test_an_arena_helper_the_relay_does_not_have_is_reported(self):
        for text in ("arena_markers_scan()", 'arena_kill("x")', "arena_release_all()"):
            before = len(self.mismatches())
            self.env.sync(120, lines=[lua_line(self.clock_sec(110), text)])
            self.assertEqual(len(self.mismatches()), before + 1, text)

    def test_release_of_a_batch_the_referee_never_issued(self):
        self.env.sync(120, lines=[lua_line(self.clock_sec(110), release_call("s0-0099"))])
        self.assertEqual(self.mismatches()[0]["why"], "release_of_unknown_batch")

    def test_release_logged_more_often_than_sent(self):
        self.released_game()
        self.env.sync(120, lines=[lua_line(self.clock_sec(110), release_call("s0-0001"))])
        self.assertEqual(self.mismatches()[0]["why"], "release_called_more_often_than_sent")

    def test_a_resent_batch_may_be_logged_twice(self):
        self.env.wake(0)
        self.ref.submit(0, MOVE)
        self.env.sync(100, "Orders", 0)
        self.clock.advance(11)
        self.env.sync(100, "Orders", 0)                                      # resent
        self.env.sync(100, "Orders", 0, lines=[lua_line(self.clock_sec(100), release_call("s0-0001")),
                                              lua_line(self.clock_sec(100), release_call("s0-0001"))])
        self.assertEqual(self.mismatches(), [])

    def test_issued_batch_that_never_appears_in_the_log(self):
        self.env.wake(0)
        self.ref.submit(0, MOVE)
        rels, _ = self.env.stop(100, 0)
        self.env.sync(105)
        # the relay answered but the LUA> line is missing: the stop helper delivers it, so drop that record by hand
        self.ref.audit.issued["s0-0001"]["called"] = 0
        s = self.ref.audit.finalize(300)
        self.assertEqual(s["mismatches"], 1)
        self.assertEqual(self.mismatches()[0]["why"], "released_but_never_logged")

    def test_result_that_names_other_commands_than_the_batch(self):
        self.env.wake(0)
        cid = self.ref.submit(0, MOVE)["cmd_id"]
        self.env.sync(100, "Orders", 0)
        self.env.sync(100, "Orders", 0, release_result={"batch_id": "s0-0001", "results": [
            {"cmd_id": cid, "ok": True}, {"cmd_id": "s0-c9999", "ok": True}]})
        m = self.mismatches()[0]
        self.assertEqual((m["why"], m["extra"]), ("result_cmds_differ", ["s0-c9999"]))

    def test_unanswered_batch_is_reported_at_the_end(self):
        self.env.wake(0)
        self.ref.submit(0, MOVE)
        self.env.sync(100, "Orders", 0)
        s = self.ref.audit.finalize(500)
        self.assertEqual(self.mismatches()[0]["why"], "issued_without_result")


if __name__ == "__main__":
    unittest.main()
