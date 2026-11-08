"""Command queue: held until the side's own phase, one batch per phase, validation, violation streak, deploy rules."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.referee import rules  # noqa: E402
from arena.referee.core import ApiError  # noqa: E402
from arena.tests import fakegame as fg  # noqa: E402
from arena.tests.fakegame import EnvCase  # noqa: E402

FIRE = {"kind": "fire_support", "request": {"request_type": "static_target", "location": 2015, "target_type": "soft", "duration_mins": 6}}
MOVE = {"kind": "orders", "unit": "Alpha Platoon", "orders": [{"orders": "Move Hasty", "destination_location": 1713}]}
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Queue(EnvCase):
    def submit(self, side, body):
        return self.ref.submit(side, body)

    def bad(self, side, body):
        with self.assertRaises(ApiError) as cm:
            self.ref.submit(side, body)
        return cm.exception

    def status(self, cid):
        return self.ref.queue.cmds[cid]["status"]

    def test_idle_side_cannot_queue(self):
        e = self.bad(0, MOVE)
        self.assertEqual(e.code, 409)

    def test_not_released_outside_the_orders_phase(self):
        self.env.wake(0)
        cid = self.submit(0, MOVE)["cmd_id"]
        for sec in (30, 60, 90):
            r = self.env.sync(sec)                                        # Executing
            self.assertIsNone(r["release"])
        self.assertEqual(self.status(cid), "queued")

    def test_other_sides_orders_phase_does_not_release_my_commands(self):
        self.env.wake(0)
        cid = self.submit(0, MOVE)["cmd_id"]
        rels, last = self.env.stop(100, 1)                                # side 1's turn to give orders
        self.assertEqual(rels, [])
        self.assertTrue(last["f9"])                                       # the game goes on, nothing to release
        self.assertEqual(self.status(cid), "queued")

    def test_own_orders_phase_releases_everything_in_one_batch(self):
        self.env.wake(0)
        a = self.submit(0, MOVE)["cmd_id"]
        b = self.submit(0, {"kind": "doctrine", "unit": "Bravo Platoon",
                            "sop": {"fire_discipline": "Hold until fired on", "stand_off_range": 5}})["cmd_id"]
        c = self.submit(0, FIRE)["cmd_id"]
        self.env.sync(40)
        rels, last = self.env.stop(100, 0)
        self.assertEqual(len(rels), 1)
        self.assertEqual(sorted(re.findall(r'cmd_id="([^"]+)"', rels[0]["lua"])), sorted([a, b, c]))
        self.assertTrue(rels[0]["lua"].startswith("return {"))
        self.assertIn('destination_location="1713"', rels[0]["lua"])
        self.assertTrue(last["f9"])
        for cid in (a, b, c):
            self.assertEqual(self.status(cid), "done")
        rels2, _ = self.env.stop(400, 0)                                  # nothing new: nothing sent
        self.assertEqual(rels2, [])

    def test_nothing_goes_out_while_the_relay_holds_for_a_replay(self):
        self.env.wake(0)
        self.submit(0, MOVE)
        self.env.seq += 1
        body = fg.sync_body(self.env.seq, 100, "Orders", 0, fg.mini_state(100, self.env.units))
        body["game"]["hold"] = True                                       # the replay is on: Lua input is not logged
        r = self.ref.sync(body)
        self.assertEqual((r["release"], r["f9"]), (None, False))
        r = self.env.sync(100, "Orders", 0)                               # replay over
        self.assertIsNotNone(r["release"])

    def test_f9_waits_for_the_release_result(self):
        self.env.wake(0)
        self.submit(0, MOVE)
        r = self.env.sync(100, "Orders", 0)
        self.assertIsNotNone(r["release"])
        self.assertFalse(r["f9"])
        r = self.env.sync(100, "Orders", 0)                               # relay has not answered yet: no resend, no f9
        self.assertIsNone(r["release"])
        self.assertFalse(r["f9"])
        self.clock.advance(rules.RELEASE_RETRY_SEC + 1)
        r = self.env.sync(100, "Orders", 0)                               # still nothing: the same batch is offered again
        self.assertEqual(r["release"]["batch_id"], "s0-0001")
        self.assertEqual(self.ref.queue.batches["s0-0001"]["sent"], 2)

    def test_orders_done_once_the_phase_batch_has_its_result(self):
        self.env.wake(0)
        self.submit(0, MOVE)
        r = self.env.sync(100, "Orders", 0)
        self.assertIsNotNone(r["release"])
        self.assertFalse(r["orders_done"])                                # the relay keeps the hand-over dialog open
        ids = re.findall(r'cmd_id="([^"]+)"', r["release"]["lua"])
        result = {"batch_id": r["release"]["batch_id"], "results": [{"cmd_id": c, "ok": True, "ret": True} for c in ids]}
        r = self.env.sync(100, "Orders", 0, release_result=result)
        self.assertTrue(r["orders_done"])
        self.assertFalse(self.env.sync(160)["orders_done"])               # Executing

    def test_a_command_the_game_refused_is_failed_with_a_reason(self):
        self.env.wake(0)
        cid = self.submit(0, FIRE)["cmd_id"]
        r = self.env.sync(100, "Orders", 0)
        result = {"batch_id": r["release"]["batch_id"], "results": [{"cmd_id": cid, "kind": "fire_support", "ok": False}]}
        self.env.sync(100, "Orders", 0, release_result=result)
        self.assertEqual(self.status(cid), "failed")
        row = [c for c in self.ref.queue.listing(0, include_done=True) if c["cmd_id"] == cid][0]
        self.assertIn("the game refused it", row["error"])
        ev = [e for e in self.ref.events.since(0) if e["type"] == "command_failed"]   # the side is told (rehearsal 6)
        self.assertEqual([(e["cmd_id"], e["kind"]) for e in ev], [(cid, "fire_support")])
        self.assertEqual(ev[0]["error"], row["error"])
        self.assertEqual([e for e in self.ref.events.since(1) if e["type"] == "command_failed"], [])

    def test_an_order_the_game_read_back_without_is_told_as_changed(self):
        # start task 11-01: Kimi's Resupply → Hold came back as Hold → Hold (in combat, FM01 21.7) and it never knew
        self.env.wake(0)
        body = {"kind": "orders", "unit": "Alpha Platoon", "orders": [{"orders": "Resupply"}, {"orders": "Hold"}]}
        cid = self.submit(0, body)["cmd_id"]
        same = self.submit(0, {**MOVE, "unit": "Bravo Platoon"})["cmd_id"]
        r = self.env.sync(100, "Orders", 0)
        result = {"batch_id": r["release"]["batch_id"], "results": [
            {"cmd_id": cid, "kind": "orders", "ok": True, "ret": True, "readback": [{"orders": "Hold"}, {"orders": "Hold"}]},
            {"cmd_id": same, "kind": "orders", "ok": True, "ret": True, "readback": [{"orders": "Move Hasty"}, {"orders": "Hold"}]}]}
        self.env.sync(100, "Orders", 0, release_result=result)
        self.assertEqual(self.status(cid), "done")
        ev = [e for e in self.ref.events.since(0) if e["type"] == "command_changed"]
        self.assertEqual([(e["cmd_id"], e["dropped"], e["got"]) for e in ev], [(cid, ["Resupply"], ["Hold", "Hold"])])
        self.assertIn("FM01 21.7", ev[0]["why"])
        self.assertEqual([e for e in self.ref.events.since(1) if e["type"] == "command_changed"], [])

    def test_f9_is_repeated_while_the_phase_stays_then_logged_once(self):
        # the relay skips an F9 unless the game's banner shows it waiting, so the referee asks again
        f9s = 0
        for _ in range(rules.F9_MAX + 3):
            f9s += self.env.sync(100, "Orders", 1)["f9"]
            self.clock.advance(rules.F9_RETRY_SEC + 0.1)
        self.assertEqual(f9s, rules.F9_MAX)
        rows = [v for v in self.env.log("violations") if v["kind"] == "f9_no_effect"]
        self.assertEqual(len(rows), 1)

    def test_back_from_the_replay_to_the_same_orders_phase_is_not_a_new_one(self):
        # P3 rehearsal 10-30: released, hand-over answered, replay (reported as Executing), then the game came back to
        # the same Orders phase and waited for F9
        self.env.wake(0)
        self.submit(0, MOVE)
        rels, _ = self.env.stop(100, 0)
        self.assertEqual(len(rels), 1)
        events = len(self.env.log("events"))
        self.env.sync(100)                                                # the replay
        self.env.wake(0)
        late = self.submit(0, {"kind": "orders", "unit": "Bravo Platoon", "orders": [{"orders": "Hold"}]})["cmd_id"]
        rels2, last = self.env.stop(100, 0)
        self.assertEqual(rels2, [])                                       # its batch is out already
        self.assertTrue(last["f9"])
        self.assertEqual(self.status(late), "queued")
        self.assertEqual(self.ref.orders_seen[0], [100])
        self.assertEqual(len(self.env.log("events")), events)            # no second orders_phase event
        self.env.sync(160)
        rels3, _ = self.env.stop(400, 0)                                  # the next Orders phase is new
        self.assertIn(late, rels3[0]["lua"])
        self.assertEqual(self.ref.orders_seen[0], [100, 400])

    def test_commands_submitted_during_the_stop_wait_for_the_next_one(self):
        self.env.wake(0)
        self.submit(0, MOVE)
        r = self.env.sync(100, "Orders", 0)
        late = self.submit(0, {"kind": "orders", "unit": "Bravo Platoon", "orders": [{"orders": "Hold"}]})["cmd_id"]
        self.assertNotIn(late, r["release"]["lua"])
        self.assertEqual(self.status(late), "queued")

    def test_orders_phase_with_unknown_side_lets_the_game_go_after_a_grace_time(self):
        self.env.wake(0)
        self.submit(0, MOVE)
        self.assertFalse(self.env.sync(100, "Orders", None)["f9"])
        self.clock.advance(rules.ORDERS_SIDE_GRACE_SEC + 0.1)
        r = self.env.sync(100, "Orders", None)
        self.assertTrue(r["f9"])
        self.assertIsNone(r["release"])
        self.assertEqual(self.status(list(self.ref.queue.cmds)[0]), "queued")

    def test_newer_command_for_same_unit_and_kind_supersedes(self):
        self.env.wake(0)
        a = self.submit(0, MOVE)["cmd_id"]
        b = self.submit(0, {**MOVE, "orders": [{"orders": "Hold"}]})["cmd_id"]
        self.assertEqual((self.status(a), self.status(b)), ("superseded", "queued"))

    def test_withdraw(self):
        self.env.wake(0)
        a = self.submit(0, MOVE)["cmd_id"]
        self.ref.withdraw(0, a)
        rels, _ = self.env.stop(100, 0)
        self.assertEqual((rels, self.status(a)), ([], "withdrawn"))
        with self.assertRaises(ApiError):
            self.ref.withdraw(0, a)

    def test_unit_lost_before_the_phase_is_dropped_at_release(self):
        self.env.wake(0)
        a = self.submit(0, MOVE)["cmd_id"]
        self.env.units["Alpha Platoon"]["s"]["status"] = "Dead"
        self.env.sync(50)
        rels, _ = self.env.stop(100, 0)
        self.assertEqual((rels, self.status(a)), ([], "rejected"))


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Validation(EnvCase):
    def setUp(self):
        super().setUp()
        self.env.wake(0)

    def refuse(self, body):
        with self.assertRaises(ApiError) as cm:
            self.ref.submit(0, body)
        self.assertEqual(cm.exception.code, 400)
        return cm.exception.msg

    def test_unit_must_be_mine_and_on_the_map(self):
        self.refuse({**MOVE, "unit": "9 MR"})
        self.refuse({**MOVE, "unit": "nobody"})
        self.refuse({**MOVE, "unit": None})
        self.env.units["Bravo Platoon"]["s"]["status"] = "Reinforcement"
        self.env.sync(10)
        msg = self.refuse({**MOVE, "unit": "Bravo Platoon"})
        self.assertIn("Reinforcement", msg)                                # own unit not arrived: says so
        self.assertIn("not one of your units", self.refuse({**MOVE, "unit": "9 MR"}))

    def test_hex_must_be_on_the_map(self):
        for h in (9999, 0, -1, 5399, "1713x", 17.5, True):
            self.refuse({**MOVE, "orders": [{"orders": "Move Hasty", "destination_location": h}]})

    def test_barrage_aims_at_a_hex(self):
        barrage = {"orders": "Barrage", "target_location": 1713, "barrage_type": "HE", "barrage_rounds": 20,
                   "barrage_duration_mins": 10, "barrage_timing": "asap"}
        self.assertTrue(self.ref.submit(0, {**MOVE, "orders": [barrage]})["ok"])
        for h in (9999, "1713", True):
            self.refuse({**MOVE, "orders": [{**barrage, "target_location": h}]})
        # what the game refused on the game machine (10-31) is refused here with the reason
        no_aim = {k: v for k, v in barrage.items() if k != "target_location"}
        self.assertIn("not destination_location", self.refuse({**MOVE, "orders": [{**no_aim, "destination_location": 1713}]}))
        self.assertIn("target_location", self.refuse({**MOVE, "orders": [no_aim]}))
        for k in rules.BARRAGE_NEEDS:
            self.assertIn(k, self.refuse({**MOVE, "orders": [{x: v for x, v in barrage.items() if x != k}]}))
        for bad in ({"barrage_type": "Smoke"}, {"barrage_type": "DPICM"}, {"barrage_rounds": 0}, {"barrage_duration_mins": "10"},
                    {"barrage_timing": "fixed"}, {"barrage_first_round_time": "1989-09-25 10:00:00"}):
            self.refuse({**MOVE, "orders": [{**barrage, **bad}]})
        self.ref.submit(0, {**MOVE, "orders": [{**barrage, "barrage_type": "icm", "barrage_timing": "ASAP", "barrage_area_fire": True}]})
        cmd = list(self.ref.queue.cmds.values())[-1]
        o = cmd["args"]["orders"][0]
        self.assertEqual((o["barrage_type"], o["barrage_area_fire"]), ("ICM", "true"))   # the game reads only the string

    def test_hunt_needs_a_destination(self):
        self.assertIn("destination_location", self.refuse({**MOVE, "orders": [{"orders": "Hunt"}]}))
        self.assertTrue(self.ref.submit(0, {**MOVE, "orders": [{"orders": "Hunt", "destination_location": 1713}]})["ok"])
        self.assertTrue(self.ref.submit(0, {**MOVE, "orders": [{"orders": "Counter Battery"}]})["ok"])

    def test_order_fields_and_types_are_checked(self):
        self.refuse({**MOVE, "orders": [{"orders": "Fly Away"}]})
        self.assertIn("unknown order fields ['evil']", self.refuse({**MOVE, "orders": [{"orders": "Hold", "evil": 1}]}))
        self.refuse({**MOVE, "orders": []})
        self.refuse({**MOVE, "orders": [{"orders": "Hold"}] * (rules.MAX_ORDERS_PER_CMD + 1)})
        self.refuse({**MOVE, "orders": "Hold"})
        self.refuse({**MOVE, "orders": [{"orders": "Screen", "target_unit": "9 MR"}]})        # an enemy unit by name
        self.refuse({**MOVE, "orders": [{"orders": "Hold", "start_time": "tomorrow"}]})

    def test_doctrine_fields_come_from_the_whitelist(self):
        self.refuse({"kind": "doctrine", "unit": "Alpha Platoon", "sop": {"god_mode": 1}})
        self.refuse({"kind": "doctrine", "unit": "Alpha Platoon", "sop": {}})
        self.refuse({"kind": "doctrine", "unit": "Alpha Platoon", "sop": {"fire_discipline": {"a": {"b": {"c": 1}}}}})
        self.assertIn("unknown sop fields ['standoff_range']",                 # P4 exam: Kimi's misspelling
                      self.refuse({"kind": "doctrine", "unit": "Alpha Platoon", "sop": {"standoff_range": 0}}))
        ok = self.ref.submit(0, {"kind": "doctrine", "unit": "Alpha Platoon",
                                 "sop": {"movement_preferences": ["Road movement"], "stand_off_range": 3}})
        self.assertTrue(ok["ok"])

    def test_forbidden_kinds_do_not_exist(self):
        for kind in ("kill", "add_unit", "set_status", "pause", "place_marker", "SetUnitDetection"):
            self.refuse({"kind": kind, "unit": "Alpha Platoon"})

    def test_target_contact_is_translated_only_for_a_seen_contact(self):
        self.refuse({**MOVE, "orders": [{"orders": "Assault", "target_contact": 144}]})
        self.env.sync(100, lines=[self.env.contact(90, "Red Tank Company", 144, "Detected", "3x vehicle(s)")])
        cid = self.ref.submit(0, {**MOVE, "orders": [{"orders": "Assault", "target_contact": 144}]})["cmd_id"]
        cmd = self.ref.queue.cmds[cid]
        self.assertEqual(cmd["args"]["orders"][0]["target_unit"], "Red Tank Company")   # only inside the lua batch
        rels, _ = self.env.stop(200, 0)
        self.assertIn("Red Tank Company", rels[0]["lua"])

    def test_a_contact_may_be_written_c12_in_a_raw_body(self):
        # start task 11-01: a script wrote target_contact = "c3"; "c144", "#144" and "144" are contact 144
        self.env.sync(100, lines=[self.env.contact(90, "Red Tank Company", 144, "Detected", "3x vehicle(s)")])
        for i, ref in enumerate(("c144", "#144", "144")):
            unit = ("Alpha Platoon", "Bravo Platoon", "Big Company")[i]
            cid = self.ref.submit(0, {**MOVE, "unit": unit, "orders": [{"orders": "Assault", "target_contact": ref}]})["cmd_id"]
            self.assertEqual(self.ref.queue.cmds[cid]["args"]["orders"][0]["target_unit"], "Red Tank Company")
        self.refuse({**MOVE, "orders": [{"orders": "Assault", "target_contact": "c145"}]})

    def test_fire_support_keys_are_the_games(self):
        fire = lambda **rq: {"kind": "fire_support", "request": rq}  # noqa: E731
        ok = dict(request_type="static_target", location=2015, target_type="soft", projectile_type="HE", priority="High",
                  duration_mins=5, delay=2, requester_unit="Alpha Platoon", spotter_unit="Bravo Platoon")
        self.ref.submit(0, fire(**ok))
        for t in ("obscuration", "cb_scan", "sead", "minefield"):
            self.ref.submit(0, fire(request_type=t, location=2015))
        self.ref.submit(0, fire(request_type="direct_support"))                    # no location needed
        for bad in (dict(ok, request_type="nuke"), {k: v for k, v in ok.items() if k != "request_type"},
                    {k: v for k, v in ok.items() if k != "location"}, dict(ok, location=99999), dict(ok, rounds=6),
                    dict(ok, target_hex=2015), dict(ok, requester_unit="Red Tank Company"), dict(ok, spotter_unit="nobody"),
                    dict(ok, duration_mins="5"), dict(ok, delay=-1), dict(ok, priority=""), dict(ok, priority=3),
                    dict(ok, contact=3), dict(ok, enemy_unit="Red Tank Company"),
                    dict(ok, target_type="Area"), dict(ok, target_type="Soft"),         # the game takes lower case only
                    {k: v for k, v in ok.items() if k != "target_type"}):                # static_target needs one
            self.refuse(fire(**bad))
        self.refuse({"kind": "fire_support", "request": "static_target"})

    def test_enemy_target_names_a_contact_in_view_and_the_true_name_only_enters_the_batch(self):
        fire = {"kind": "fire_support", "request": {"request_type": "enemy_target", "contact": 144, "priority": "High"}}
        self.refuse(fire)                                                         # no such contact yet
        self.env.sync(100, lines=[self.env.contact(90, "Red Tank Company", 144, "Detected", "3x vehicle(s)")])
        cid = self.ref.submit(0, fire)["cmd_id"]
        self.assertEqual(self.ref.queue.cmds[cid]["args"]["request"]["contact"], 144)
        self.assertNotIn("Red Tank Company", str(self.ref.queue.cmds[cid]["args"]))
        self.assertNotIn("Red Tank Company", str(self.ref.queue.listing(0)))
        rels, _ = self.env.stop(200, 0)
        self.assertIn('enemy_unit="Red Tank Company"', rels[0]["lua"])
        self.assertIn('request_type="enemy_target"', rels[0]["lua"])
        self.assertNotIn("contact=", rels[0]["lua"])
        self.assertEqual(self.ref.queue.cmds[cid]["status"], "done")

    def test_enemy_target_out_of_view_at_release_is_rejected_without_a_batch(self):
        self.env.sync(100, lines=[self.env.contact(90, "Red Tank Company", 144, "Detected", "3x vehicle(s)")])
        cid = self.ref.submit(0, {"kind": "fire_support", "request": {"request_type": "enemy_target", "contact": 144}})["cmd_id"]
        self.env.sync(130, lines=[self.env.contact(120, "Red Tank Company", 144, "Undetected", "3x vehicle(s)")])
        rels, last = self.env.stop(200, 0)
        self.assertEqual(rels, [])
        self.assertEqual(self.ref.queue.cmds[cid]["status"], "rejected")
        self.assertIn("no longer in view", self.ref.queue.cmds[cid]["error"])
        self.assertTrue(last["f9"])
        ev = [e for e in self.ref.events.since(0) if e["type"] == "command_failed"]   # the side is told
        self.assertEqual([(e["cmd_id"], e["error"]) for e in ev], [(cid, "contact is no longer in view")])
        self.assertNotIn("Red Tank Company", str(ev))

    def test_three_illegal_in_a_row_is_a_violation_then_dropped(self):
        for i in range(2):
            self.assertIn("streak", self._bad_body())
        self.assertEqual(self.env.log("violations"), [])
        e = self._bad()
        self.assertEqual(e.extra["streak"], 3)
        v = self.env.log("violations")
        self.assertEqual([(r["side"], r["kind"]) for r in v], [(0, "illegal_streak")])
        e = self._bad()
        self.assertTrue(e.extra.get("dropped"))
        self.assertIn("not one of your units", e.msg)                      # dropped, but the reason still comes back
        self.assertEqual(len(self.env.log("violations")), 1)               # one violation, later ones just dropped
        self.assertTrue(self.ref.submit(0, MOVE)["ok"])                    # legal commands still go through
        self.assertTrue(self.ref.queue.dropping[0])
        self.assertFalse(self.ref.queue.dropping[1])

    def _bad(self):
        with self.assertRaises(ApiError) as cm:
            self.ref.submit(0, {**MOVE, "unit": "nobody"})
        return cm.exception

    def _bad_body(self):
        return self._bad().extra

    def test_a_legal_command_resets_the_streak(self):
        self._bad()
        self._bad()
        self.ref.submit(0, MOVE)
        self._bad()
        self._bad()
        self.assertEqual(self.env.log("violations"), [])
        self.assertEqual(self.ref.queue.streak[0], 2)

    def test_queue_size_is_bounded(self):
        for i in range(rules.MAX_QUEUE):
            self.ref.queue.cmds[f"x{i}"] = {"side": 0, "status": "queued", "kind": "orders", "unit": f"u{i}"}
        with self.assertRaises(ApiError) as cm:
            self.ref.submit(0, MOVE)
        self.assertEqual(cm.exception.code, 429)


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Deploy(EnvCase):
    def deploy(self, side, unit, hex_, expect_ok=True):
        body = {"kind": "deploy", "unit": unit, "hex": hex_}
        if expect_ok:
            return self.ref.submit(side, body)
        with self.assertRaises(ApiError) as cm:
            self.ref.submit(side, body)
        return cm.exception

    def test_deploy_only_before_the_game(self):
        self.env.wake(0)
        e = self.deploy(0, "Alpha Platoon", 1015, expect_ok=False)          # phase is Executing
        self.assertIn("before the game", e.msg)

    def test_deploy_target_must_be_inside_my_starting_area(self):
        self.env.sync(0, "Pregame", 0)
        self.env.wake(0)
        self.assertTrue(self.deploy(0, "Alpha Platoon", 1015)["ok"])        # columns 1-29, rows 8-29
        for h in (4010, 1005, 1030, 3015, 5110):
            self.assertIn("starting area", self.deploy(0, "Alpha Platoon", h, expect_ok=False).msg, h)
            self.deploy(0, "Alpha Platoon", 1015)                           # a legal one in between: no streak
        self.env.wake(1)
        self.assertTrue(self.deploy(1, "9 MR", 5210)["ok"])                  # columns 51-53, rows 4-31
        self.assertIn("starting area", self.deploy(1, "9 MR", 1015, expect_ok=False).msg)

    def test_deploy_unit_must_be_mine(self):
        self.env.sync(0, "Pregame", 0)
        self.env.wake(0)
        self.assertIn("your units", self.deploy(0, "9 MR", 1015, expect_ok=False).msg)

    def test_deploy_is_released_in_pregame_after_the_preparation_and_uses_integer_hex(self):
        self.env.sync(0, "Pregame", 0)
        self.env.wake(0)
        cid = self.deploy(0, "Alpha Platoon", 1015)["cmd_id"]
        rels, last = self.env.stop(0, 0, "Pregame", limit=5)               # preparation still running: held
        self.assertEqual(rels, [])
        self.assertFalse(last["f9"])
        self.ref.start()
        rels, last = self.env.stop(0, 0, "Pregame")
        self.assertEqual(len(rels), 1)
        self.assertIn('location=1015', rels[0]["lua"])
        self.assertIn('kind="deploy"', rels[0]["lua"])
        self.assertTrue(last["f9"])
        self.assertEqual(self.ref.queue.cmds[cid]["status"], "done")

    def test_deploy_never_goes_out_in_an_orders_phase(self):
        self.env.sync(0, "Pregame", 0)
        self.env.wake(0)
        cid = self.deploy(0, "Alpha Platoon", 1015)["cmd_id"]
        self.env.sync(0)
        rels, _ = self.env.stop(900, 0, "Orders")
        self.assertEqual(rels, [])
        self.assertEqual(self.ref.queue.cmds[cid]["status"], "queued")


if __name__ == "__main__":
    unittest.main()
