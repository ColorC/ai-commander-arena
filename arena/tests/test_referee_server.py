"""HTTP plumbing: tokens decide the side, the three roles stay apart, bad input gets the right status, notes are
confined to the side's workspace, and the process starts with `python -I arena/referee`."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from arena.referee.core import Referee  # noqa: E402
from arena.referee.world import Scenario  # noqa: E402
from arena.tests import fakegame as fg  # noqa: E402
from arena.tests import HAVE_SCENARIOS, NO_SCENARIOS  # noqa: E402


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class Http(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = fg.FakeClock()
        self.ref = Referee(self.tmp.name, Scenario(fg.SCENARIO), clock=self.clock)
        self.addCleanup(self.ref.close)
        self.srv, self.tokens = fg.serve(self.ref, self.tmp.name)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        port = self.srv.server_address[1]
        self.a0, self.a1 = fg.Http(port, self.tokens["side0"]), fg.Http(port, self.tokens["side1"])
        self.relay, self.admin = fg.Http(port, self.tokens["relay"]), fg.Http(port, self.tokens["admin"])
        for h in (self.a0, self.a1, self.relay, self.admin):
            self.addCleanup(h.close)
        self.seq = 0
        self.sync(0)

    def sync(self, sec, phase="Executing", side=None, state=True, lines=()):
        self.seq += 1
        st = fg.mini_state(sec) if state else None
        code, r = self.relay.call("POST", "/relay/sync", fg.sync_body(self.seq, sec, phase, side, st, lines))
        self.assertEqual(code, 200, r)
        return r

    def wake(self, side):
        with self.ref.lock:
            self.ref._start_turn(side, self.clock(), [{"type": "test"}])
            return self.ref.turn_id[side]

    def test_token_files_exist_and_differ(self):
        d = os.path.join(self.tmp.name, "secrets")
        vals = []
        for n in ("side0", "side1", "relay", "admin"):
            with open(os.path.join(d, n + ".token"), encoding="utf-8") as f:
                vals.append(f.read().strip())
        self.assertEqual(len(set(vals)), 4)
        self.assertTrue(all(len(v) >= 40 for v in vals))

    def test_authentication_and_roles(self):
        port = self.srv.server_address[1]
        for bad in (fg.Http(port, ""), fg.Http(port, "x" * 43), fg.Http(port, self.tokens["side0"][:-1])):
            self.assertEqual(bad.call("GET", "/agent/wait?timeout=0")[0], 401)
            self.assertEqual(bad.call("POST", "/relay/sync", {})[0], 401)
            bad.close()
        self.assertEqual(self.a0.call("POST", "/relay/sync", {})[0], 403)         # agents cannot play the relay
        self.assertEqual(self.a0.call("GET", "/admin/state")[0], 403)
        self.assertEqual(self.relay.call("GET", "/agent/wait?timeout=0")[0], 403)
        self.assertEqual(self.relay.call("GET", "/admin/state")[0], 403)
        self.assertEqual(self.admin.call("GET", "/agent/wait?timeout=0")[0], 403)
        self.assertEqual(self.admin.call("GET", "/admin/state")[0], 200)
        self.assertEqual(self.a0.call("GET", "/nowhere")[0], 404)
        self.assertEqual(self.a0.call("GET", "/agent/nowhere")[0], 404)

    def test_side_comes_from_the_token_never_from_the_request(self):
        self.wake(0)
        self.wake(1)
        code, r = self.a0.call("POST", "/agent/queue", {"kind": "orders", "unit": "Alpha Platoon", "side": 1,
                                                        "orders": [{"orders": "Hold"}]})
        self.assertEqual((code, r["cmd_id"][:3]), (200, "s0-"))
        code, r = self.a1.call("POST", "/agent/queue", {"kind": "orders", "unit": "Alpha Platoon", "side": 0,
                                                        "orders": [{"orders": "Hold"}]})
        self.assertEqual(code, 400)                                               # not side 1's unit
        code, r = self.a1.call("DELETE", "/agent/queue/s0-c0001")
        self.assertEqual(code, 404)                                               # side 1 cannot touch side 0's command
        self.assertEqual(self.a0.call("DELETE", "/agent/queue/s0-c0001")[0], 200)

    def test_idle_calls_are_409_and_wait_is_the_exception(self):
        for method, path, body in (("GET", "/agent/view/summary", None), ("POST", "/agent/queue", {}),
                                   ("POST", "/agent/turn/end", {}), ("POST", "/agent/observe", {"on": True}),
                                   ("GET", "/agent/scripts", None), ("GET", "/agent/notes/a", None)):
            self.assertEqual(self.a0.call(method, path, body)[0], 409, path)
        code, r = self.a0.call("GET", "/agent/wait?timeout=0")
        self.assertEqual((code, r["woke"]), (200, False))

    def test_a_turn_over_http(self):
        tid = self.wake(0)
        code, s = self.a0.call("GET", "/agent/view/summary")
        self.assertEqual(code, 200)
        self.assertIn("contacts_in_view", s)
        self.assertEqual(self.a0.call("GET", "/agent/view/units")[1]["units"][0]["name"], "Alpha Platoon")
        code, u = self.a0.call("GET", "/agent/view/unit/Alpha%20Platoon")
        self.assertEqual((code, u["status"]["location"]), (200, 1510))
        self.assertEqual(self.a0.call("GET", "/agent/view/unit/9%20MR")[0], 404)
        code, t = self.a0.call("GET", "/agent/view/terrain?hexes=101,102,9999")
        self.assertEqual((code, [x["hex"] for x in t["terrain"]]), (200, [101, 102]))
        self.assertEqual(self.a0.call("GET", "/agent/view/terrain?box=1,40,1,40")[0], 400)   # too many hexes
        self.assertEqual(self.a0.call("GET", "/agent/view/briefing")[1]["starting_area"]["columns"], [1, 29])
        self.assertEqual(self.a1.call("GET", "/agent/view/briefing")[0], 409)                # side 1 is idle
        self.assertEqual(self.a0.call("GET", "/agent/view/nonsense")[0], 404)
        code, r = self.a0.call("POST", "/agent/turn/end", {"turn_id": tid, "subscriptions": [{"type": "combat"}]})
        self.assertEqual((code, r["subscriptions"][0]["type"]), (200, "combat"))
        self.assertEqual(self.a0.call("GET", "/agent/view/summary")[0], 409)

    def test_bad_bodies(self):
        self.wake(0)
        conn = self.a0
        conn.call("GET", "/agent/wait?timeout=0")                                   # make sure the connection exists
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1])
        c.request("POST", "/agent/queue", body=b"{not json", headers={"Authorization": "Bearer " + self.tokens["side0"]})
        r = c.getresponse()
        r.read()
        self.assertEqual(r.status, 400)
        c.close()
        self.assertEqual(self.a0.call("POST", "/agent/queue", "just a string")[0], 400)
        self.assertEqual(self.a0.call("POST", "/agent/queue", {"kind": "orders", "unit": 5})[0], 400)
        self.assertEqual(self.a0.call("GET", "/agent/wait?timeout=abc")[0], 400)
        code, r = self.a0.call("POST", "/agent/queue", {"kind": "orders", "unit": "Alpha Platoon",
                                                        "orders": [{"orders": "Hold", "start_time": {"a": 1}}]})
        self.assertEqual(code, 400)

    def test_oversized_body_is_refused(self):
        self.wake(0)
        code, r = self.a0.call("POST", "/agent/queue", {"kind": "orders", "pad": "x" * (1 << 21)})
        self.assertEqual(code, 413)

    def test_notes_are_confined_to_the_sides_workspace(self):
        self.wake(0)
        self.wake(1)
        self.assertEqual(self.a0.call("PUT", "/agent/notes/plan.md", {"content": "attack at dawn"})[0], 200)
        self.assertEqual(self.a0.call("PUT", "/agent/notes/sub/dir/x.txt", {"content": "deep"})[0], 200)
        self.assertEqual(self.a0.call("GET", "/agent/notes/plan.md")[1]["content"], "attack at dawn")
        self.assertEqual(sorted(self.a0.call("GET", "/agent/notes/")[1]["entries"]), ["plan.md", "sub"])
        self.assertEqual(self.a1.call("GET", "/agent/notes/plan.md")[0], 404)      # the other side has its own folder
        self.assertEqual(self.a1.call("GET", "/agent/notes/")[1]["entries"], [])
        for bad in ("..%2F..%2Fsecrets%2Fside1.token", "a/../b", "%2E%2E/x", "C:/Windows/x", "a%5Cb", "con", "nul.txt",
                    "x/y/z/a/b/c/d/e", ".hidden", "trailing.", "a b ", "a:b", "x" * 80):
            bad = bad.replace(" ", "%20")
            self.assertEqual(self.a0.call("PUT", "/agent/notes/" + bad, {"content": "x"})[0], 400, bad)
            self.assertEqual(self.a0.call("GET", "/agent/notes/" + bad)[0], 400, bad)
        self.assertEqual(self.a0.call("PUT", "/agent/notes/big.txt", {"content": "x" * (300 * 1024)})[0], 400)
        self.assertEqual(self.a0.call("PUT", "/agent/notes/sub", {"content": "x"})[0], 400)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "secrets", "side1.token.txt")))
        ws = os.path.join(self.tmp.name, "workspace")
        self.assertEqual(sorted(os.listdir(ws)), ["side0", "side1"])

    def test_relay_sync_answer_shape_and_stop_flow(self):
        r = self.sync(60)
        self.assertEqual(set(r), {"gate", "release", "f9", "orders_done", "click", "stop", "seq"})
        self.assertEqual((r["gate"], r["f9"], r["stop"], r["click"]), ("off", False, False, []))
        self.wake(0)
        self.assertEqual(self.sync(61)["gate"], "sleep")
        self.assertEqual(self.admin.call("POST", "/admin/pause", {"on": True})[1], {"paused": True})
        self.assertEqual(self.admin.call("POST", "/admin/pause", {"on": False})[1], {"paused": False})
        st = self.admin.call("GET", "/admin/state")[1]
        self.assertEqual((st["sec"], st["gate"], st["sides"]["0"]["agents"]), (61, "sleep", ["commander"]))
        self.assertIn("thinking", st["sides"]["0"])
        self.assertEqual(self.admin.call("POST", "/admin/stop")[1], {"stopped": True})
        self.assertTrue(self.sync(62)["stop"])
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "final.json")))
        code, w = self.a0.call("GET", "/agent/wait?timeout=0")
        self.assertEqual((code, w.get("game_over")), (200, True))

    def test_the_relay_body_before_the_game_has_a_clock(self):
        # exactly what relay.ps1 sends while the game is still loading: no clock, no state, unknown phase
        body = {"seq": 99, "wall_ms": 1, "run": "x", "game": {"sec": None, "phase": "Unknown", "orders_side": None,
                "alive": True, "elapsed": None, "total": None}, "state": None, "lines": [], "dialogs": [],
                "release_result": None, "end_shots": []}
        code, r = self.relay.call("POST", "/relay/sync", body)
        self.assertEqual(code, 200, r)
        self.assertEqual((r["gate"], r["stop"], r["release"]), ("off", False, None))
        self.assertEqual(self.admin.call("GET", "/admin/state")[1]["sec"], 0)

    def test_the_game_ending_phase_stops_the_relay(self):
        r = self.sync(18000, phase="Ended")
        self.assertTrue(r["stop"])
        with open(os.path.join(self.tmp.name, "final.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["sec"], 18000)


@unittest.skipUnless(HAVE_SCENARIOS, NO_SCENARIOS)
class EntryPoint(unittest.TestCase):
    def test_python_dash_I_on_the_package_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = subprocess.Popen([sys.executable, "-I", os.path.join(ROOT, "arena", "referee"), "--run", tmp, "--scenario",
                                  fg.SCENARIO, "--port", "0", "--host", "127.0.0.1"], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, cwd=tmp)
            try:
                line = p.stdout.readline()
                self.assertIn("referee for Alamo at Soest on 127.0.0.1:", line, p.stderr.read() if p.poll() is not None else "")
                port = int(line.split("127.0.0.1:")[1].split(";")[0])
                with open(os.path.join(tmp, "secrets", "relay.token"), encoding="utf-8") as f:
                    relay = fg.Http(port, f.read().strip())
                self.assertEqual(relay.call("GET", "/relay/ping")[1], {"ok": True, "scenario": "Alamo at Soest"})
                relay.close()
                self.assertTrue(os.path.isdir(os.path.join(tmp, "logs")))
            finally:
                p.terminate()
                p.wait(10)
                p.stdout.close()
                p.stderr.close()

    def test_help_text_mentions_the_flags(self):
        out = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "arena", "referee", "__main__.py"), "--help"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        for flag in ("--run", "--scenario", "--port"):
            self.assertIn(flag, out.stdout)


if __name__ == "__main__":
    unittest.main()
