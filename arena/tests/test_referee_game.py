"""A whole recorded game (Alamo at Soest, computer vs computer, 5 hours) played into the referee over HTTP.

FakeGame is the relay and the game (per-minute states from the recording, the debug-log contact lines, the recorded
Orders phases as stops that wait for F9, a prematch deployment); two ScriptedAgent commanders act through the real
/agent/* endpoints. Nothing sleeps: a FakeClock moves 1:1 with game time while the referee says gate=sleep and at
6.5x otherwise. Side 0 thinks 45 s per wake; side 1 thinks 500 s, so the 8-minute backstop cuts it off.
At the end the invariants of the rules are checked from the referee's own logs and from what the fakes saw.
"""
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from arena.referee import contacts as ct  # noqa: E402
from arena.referee import rules  # noqa: E402
from arena.referee.core import Referee  # noqa: E402
from arena.referee.world import Scenario  # noqa: E402
from arena.tests import fakegame as fg  # noqa: E402

SUBS0 = [{"type": "timer", "every_min": 30}, {"type": "new_contact"}, {"type": "unit_out"}, {"type": "vp_change"},
         {"type": "strength_drop", "points": 5}, {"type": "combat"}, {"type": "script_alarm"}]


def name_regex(names):
    names = [n for n in names if len(n) >= 5 or " " in n or "/" in n]
    return re.compile(r"(?<![A-Za-z0-9/])(?:" + "|".join(map(re.escape, sorted(names, key=len, reverse=True))) + r")(?![A-Za-z0-9/])")


@unittest.skipUnless(os.path.isdir(fg.CAL_DIR), "the recorded game is not on this machine ($ARENA_DATA/recordings/p1-cal-als2)")
class WholeGame(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.clock = fg.FakeClock()
        sc = Scenario(fg.SCENARIO)
        cls.ref = Referee(cls.tmp.name, sc, clock=cls.clock)
        cls.srv, tokens = fg.serve(cls.ref, cls.tmp.name)
        port = cls.srv.server_address[1]
        cls.replay = fg.Replay(sc=sc)
        cls.agents = [fg.ScriptedAgent(fg.Http(port, tokens["side0"]), 0, cls.clock, sc, think=45.0, subscriptions=SUBS0),
                      fg.ScriptedAgent(fg.Http(port, tokens["side1"]), 1, cls.clock, sc, think=500.0)]
        cls.relay = fg.Http(port, tokens["relay"])
        cls.game = fg.FakeGame(cls.relay, cls.agents, cls.clock, cls.replay, cls.ref).run()
        cls.side_of = {n: u["s"]["side"] for n, u in cls.replay.nested.items()}
        with open(os.path.join(cls.tmp.name, "final.json"), encoding="utf-8") as f:
            cls.final = json.load(f)

    @classmethod
    def tearDownClass(cls):
        for a in cls.agents:
            a.http.close()
        cls.relay.close()
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.ref.close()
        cls.tmp.cleanup()

    def log(self, name):
        return self.ref.log.read(name)

    # --- the game ran to the end ---
    def test_game_ran_to_its_end(self):
        self.assertTrue(self.ref.ended)
        self.assertEqual(self.final["sec"], 18000)
        self.assertEqual(self.game.states_sent, 301)
        self.assertEqual(len([s for s in self.game.stops if s[0] == "Orders"]), len(self.replay.orders))
        self.assertEqual({s[:2] for s in self.game.stops if s[0] == "Pregame"}, {("Pregame", 0), ("Pregame", 1)})

    # --- thinking time ---
    def test_thinking_time_matches_the_turns_that_were_played(self):
        wakes = [w for w in self.log("wakes") if w.get("kind") == "wake" and not w["prematch"]]
        ends = [w for w in self.log("wakes") if w.get("kind") == "agent_end"]
        self.assertFalse(os.path.exists(self.ref.log.path("energy")))
        for side in (0, 1):
            t = self.final["thinking"][str(side)]
            self.assertEqual(t["wakes"], len([w for w in wakes if w["side"] == side]))
            self.assertGreater(t["wakes"], 5)
            turns = [e for e in ends if e["side"] == side and not e["turn_id"].endswith("t0001")]   # t0001: prematch
            real = sum(min(e["seconds"], rules.COMMANDER_TURN_SEC) for e in turns)
            self.assertAlmostEqual(t["commander_sec"] + t["observe_sec"], real, delta=0.05 * len(turns) + 0.05)
            self.assertAlmostEqual(t["total_sec"], t["commander_sec"] + t["observe_sec"] + t["subagents_sec"], delta=0.2)
            if side == 0:
                done = [e for e in turns if e["reason"] != "game_over"]
                self.assertAlmostEqual(t["commander_sec"], 45.0 * len(done) + sum(e["seconds"] for e in turns
                                                                                 if e["reason"] == "game_over"),
                                       delta=0.05 * len(turns) + 0.05)   # every turn lasted exactly 45 s

    def test_turn_backstop_on_the_slow_side_and_no_exhaustion(self):
        ends = [e for e in self.log("wakes") if e.get("kind") == "agent_end" and e["side"] == 1]
        timeouts = [e for e in ends if e["reason"] == "timeout"]
        self.assertGreater(len(timeouts), 3)
        for e in timeouts:
            self.assertAlmostEqual(e["seconds"], rules.COMMANDER_TURN_SEC, delta=1.0)
        self.assertFalse(any(e["reason"] == "exhausted" for e in ends))
        self.assertFalse(any(w.get("kind") in ("exhausted", "suppressed") for w in self.log("wakes")))
        self.assertGreater(self.agents[1].lost_turns, 3)
        # commands the cut-off commander had queued before the cut-off stayed and were released later
        self.assertGreater(self.final["queue"]["1"].get("done", 0), 3)

    def test_wakes_followed_the_default_and_chosen_subscriptions(self):
        by_side = {s: [w for w in self.log("wakes") if w.get("kind") == "wake" and w["side"] == s and not w["prematch"]]
                   for s in (0, 1)}
        self.assertGreater(len(by_side[0]), 15)
        self.assertGreater(len(by_side[1]), 5)
        types1 = {r["type"] for w in by_side[1] for r in w["reasons"]}
        self.assertLessEqual(types1, {"vp_change", "unit_out", "strength_drop", "new_contact", "timer", "script_alarm"})
        self.assertIn("new_contact", types1)
        for w in by_side[0] + by_side[1]:
            self.assertTrue(w["reasons"])

    # --- fog ---
    def test_no_fog_violation_and_no_enemy_name_in_anything_the_agents_saw(self):
        self.assertEqual(self.ref.fog_violations, 0)
        self.assertEqual([r for r in self.log("fog") if r["kind"] == "fog_violation"], [])
        for a in self.agents:
            enemy = name_regex([n for n, s in self.side_of.items() if s != a.side])
            seen = 0
            for s in a.summaries:
                text = json.dumps(s)
                for rep in re.findall(r'"report": "((?:[^"\\]|\\.)*)"', text):          # the game's own report text
                    text = text.replace(rep, "")
                m = enemy.search(text)
                self.assertIsNone(m, f"side {a.side} was shown {m and m[0]!r}")
                seen += 1
            self.assertGreater(seen, 10)

    def test_contact_tables_equal_what_the_debug_log_says(self):
        last = {}
        for _, line in self.replay.lines:
            p = ct.parse_line(line)
            last[p[2]] = (p[3], p[4])
        for viewer in (0, 1):
            table = self.ref.tables[viewer]
            expect_seen = {u for u, (no, st) in last.items() if self.side_of[u] == 1 - viewer}
            ever = {u for _, line in self.replay.lines for p in [ct.parse_line(line)]
                    if self.side_of[p[2]] == 1 - viewer and p[4] in ct.SEEN for u in [p[2]]}
            self.assertEqual(set(table.by_unit), ever)
            self.assertTrue(ever <= expect_seen)
            for u in ever:
                self.assertEqual(table.by_unit[u].visible, last[u][1] in ct.SEEN, u)
                self.assertEqual(table.by_unit[u].no, last[u][0])
            self.assertGreater(len(ever), 3)

    # --- queue and release ---
    def test_commands_were_released_only_in_their_own_phase(self):
        stops = {(sec, side) for phase, side, sec in self.game.stops if phase == "Orders"}
        pre = {side for phase, side, sec in self.game.stops if phase == "Pregame"}
        self.assertGreater(len(self.game.releases), 10)
        sides = set()
        for rel in self.game.releases:
            sides.add(rel["side"])
            if rel["phase"] == "Orders":
                self.assertEqual(rel["orders_side"], rel["side"])
                self.assertIn((rel["sec"], rel["side"]), stops)
            else:  # the prematch releases both sides' batches (a two-player pregame has only Player 1's setup)
                self.assertEqual(rel["phase"], "Pregame")
                self.assertTrue(pre)
            for cid in rel["cmds"]:
                self.assertTrue(cid.startswith(f"s{rel['side']}-c"), "a batch carried the other side's command")
        self.assertEqual(sides, {0, 1})
        for cmd in self.ref.queue.cmds.values():
            if cmd.get("batch"):
                b = self.ref.queue.batches[cmd["batch"]]
                self.assertEqual(b["side"], cmd["side"])
                self.assertGreaterEqual(b["sec"], cmd["sec_submitted"] - 0)
        issued = [r for r in self.log("releases") if r.get("event") == "issued"]
        self.assertEqual(len(issued), len(self.game.releases))
        self.assertTrue(all(r["phase"] in ("Orders", "Pregame") for r in issued))

    def test_deploy_was_accepted_inside_the_area_and_refused_outside(self):
        for a in self.agents:
            self.assertEqual(a.bad_replies, [], "an expected answer did not come")
            self.assertEqual(a.prematch_turns, 1)
        deploys = [c for c in self.ref.queue.cmds.values() if c["kind"] == "deploy"]
        self.assertEqual(sorted(c["side"] for c in deploys), [0, 1])
        for c in deploys:
            self.assertEqual(c["status"], "done")
            self.assertTrue(self.ref.sc.in_start_area(c["side"], c["args"]["location"]))
        self.assertEqual(self.final["violations"], {"0": 0, "1": 0})

    def test_every_queued_command_has_an_outcome_that_the_rules_allow(self):
        for cmd in self.ref.queue.cmds.values():
            self.assertIn(cmd["status"], {"done", "queued", "superseded", "rejected", "failed"}, cmd)

    # --- audit ---
    def test_audit_found_no_mismatch(self):
        a = self.final["audit"]
        self.assertEqual(a["mismatches"], 0, a["detail"])
        self.assertEqual(a["batches"], len(self.game.releases))
        self.assertGreaterEqual(a["matched"], len(self.game.releases))
        self.assertEqual([r for r in self.log("audit") if r["kind"] == "audit_mismatch"], [])

    # --- events ---
    def test_vp_changes_reach_both_sides_from_their_own_point_of_view(self):
        ev = {s: [e for e in self.log("events") if e["type"] == "vp_change" and e["side"] == s] for s in (0, 1)}
        self.assertGreaterEqual(len(ev[0]), 2)
        self.assertEqual([(e["t"], e["hex"]) for e in ev[0]], [(e["t"], e["hex"]) for e in ev[1]])
        for a, b in zip(ev[0], ev[1]):
            self.assertEqual((a["from"], a["to"]), (b["to"], b["from"]))                  # mirrored: you <-> enemy
        owner = dict(self.ref.sc.vp_cells)
        owner = {h: c["owner"] for h, c in owner.items()}
        for e in ev[0]:
            owner[e["hex"]] = 0 if e["to"] == "you" else 1
        self.assertEqual(owner, self.ref.world.owner)

    def test_every_vp_change_has_a_ground_unit_of_the_new_owner_that_passed(self):
        """Independent of the referee's hex-line code: the straight segment of some Active ground unit of the new owner
        between two minutes comes within 0.35 km of the cell centre."""
        sc, replay = self.ref.sc, fg.Replay(sc=self.ref.sc)
        pos = {}
        for sec in range(0, 18001, 60):
            pos[sec] = {n: (u["s"].get("location"), u["s"].get("side"), u["s"].get("icon_type"))
                        for n, u in replay.state_at(sec)["units"].items() if u["s"].get("status") == "Active"}

        def near(a, b, cell):
            (ax, ay), (bx, by), (cx, cy) = sc.xy_km(a), sc.xy_km(b), sc.xy_km(cell)
            dx, dy = bx - ax, by - ay
            t = 0 if dx == dy == 0 else max(0, min(1, ((cx - ax) * dx + (cy - ay) * dy) / (dx * dx + dy * dy)))
            return ((ax + t * dx - cx) ** 2 + (ay + t * dy - cy) ** 2) ** 0.5 <= 0.35

        for e in [e for e in self.log("events") if e["type"] == "vp_change" and e["side"] == 0]:
            new_owner = 0 if e["to"] == "you" else 1
            now, before = pos[e["t"]], pos[e["t"] - 60]
            found = [n for n, (loc, side, icon) in now.items()
                     if side == new_owner and icon not in ("Helo", "Air") and near(before.get(n, (loc,))[0], loc, e["hex"])]
            self.assertTrue(found, e)

    def test_clock_log_ties_real_time_to_game_time(self):
        rows = [r for r in self.log("clock") if "gate" in r]
        self.assertGreater(len(rows), 50)
        self.assertEqual({r["gate"] for r in rows}, {"off", "sleep"})
        sec = [r["sec"] for r in rows]
        self.assertEqual(sec, sorted(sec))


if __name__ == "__main__":
    unittest.main()
