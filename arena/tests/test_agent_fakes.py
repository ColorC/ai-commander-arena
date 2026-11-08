"""Test doubles for the arena-agent tests: a minimal fake referee (http.server on 127.0.0.1, port 0) that answers in the
shapes of arena/referee/server.py and core.py, and a virtual clock."""
import json
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class FakeClock:
    """Virtual time for deadline tests: sleeping advances it instantly."""

    def __init__(self, t=1000.0):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class FakeReferee:
    def __init__(self, token="tok-side0", wakes=1, summary="t=0 summary", deadline_in=None, observe_updates=0, sub_deadline_in=600,
                 thinking_sec=125):
        self.token, self.summary, self.deadline_in, self.sub_deadline_in = token, summary, deadline_in, sub_deadline_in
        self.thinking_sec = thinking_sec   # the side's thinking time at each wake (the referee's figure)
        self.n_wakes, self.wakes_left, self.observe_updates = 0, wakes, observe_updates
        self.requests = []          # (method, path, query dict, body, Authorization header)
        self.turn_ends = []
        self.active_subs = self.max_active_subs = 0
        self.idle = False           # answer 409 "side is idle" to everything except wait
        self.end_rejects = 0        # refuse this many commander turn/end calls with 400
        self.hold = False           # wait answers "no wake yet" (the game is still going)
        self.turn_open, self.observing = False, False
        self.unit_names = ["Alpha", "Bravo"]
        self.lock = threading.Lock()
        ref = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def handle_any(self):
                u = urllib.parse.urlsplit(self.path)
                query = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                path = urllib.parse.unquote(u.path)[len("/agent/"):]
                auth = self.headers.get("Authorization")
                with ref.lock:
                    ref.requests.append((self.command, path, query, body, auth))
                    status, out = ref.route(self.command, path, query, body, auth)
                raw = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PUT = do_DELETE = handle_any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def wake(self, **extra):
        out = {"woke": True, "turn_id": "s0-t%04d" % self.n_wakes, "prematch": False, "observe": False,
               "reasons": [{"type": "timer"}], "summary": {"text": self.summary}, "thinking_sec": self.thinking_sec, **extra}
        if self.deadline_in is not None:
            out["deadline_in"] = self.deadline_in
        return out

    def route(self, method, path, query, body, auth):
        if auth != "Bearer " + self.token:
            return 401, {"error": "missing or wrong token"}
        if path == "wait":
            if self.turn_open and not self.observing:
                return 409, {"error": "your turn is open: call end_turn before waiting again"}
            if self.turn_open and self.observing:
                if self.observe_updates <= 0:
                    return 200, {"woke": False}
                self.observe_updates -= 1
                return 200, self.wake(observe=True, reasons=[{"type": "observe"}])
            if self.hold:
                return 200, {"woke": False}
            if not self.wakes_left:
                return 200, {"woke": False, "game_over": True}
            self.wakes_left -= 1
            self.n_wakes += 1
            self.turn_open = True
            return 200, self.wake()
        if self.idle:
            self.turn_open = self.observing = False  # the referee has closed the turn
            return 409, {"error": "side is idle: wait for a wake with /agent/wait"}
        if path == "turn/begin":
            self.active_subs += 1
            self.max_active_subs = max(self.max_active_subs, self.active_subs)
            return 200, {"ok": True, "agent": body["agent"], "deadline_in": self.sub_deadline_in}
        if path == "turn/end":
            name = body.get("agent", "commander")
            if name != "commander" and "subscriptions" in body:
                return 403, {"error": "only the commander changes subscriptions"}
            if name == "commander" and self.end_rejects > 0:
                self.end_rejects -= 1
                return 400, {"error": "subscriptions rejected: test"}
            self.turn_ends.append(body)
            if name == "commander":
                self.turn_open = self.observing = False
            else:
                self.active_subs -= 1
            return 200, {"ok": True}
        if path == "observe":
            self.observing = bool(body["on"])
            return 200, {"ok": True, "observing": self.observing}
        if path == "view/briefing":
            return 200, {"scenario": "Test Scenario", "you": "Blue (Player 1)", "your_command_post": "HQ 1 Bde Test"}
        if path == "view/units":                  # the names the command line resolves unit names against
            return 200, {"units": [{"name": n} for n in self.unit_names]}
        if path.startswith("view/"):
            return 200, {"view": path[5:], "query": query}
        if path == "queue" and method == "POST":
            return 200, {"cmd_id": "c1", "ok": True}
        if path.startswith("queue/") and method == "DELETE":
            return 200, {"ok": True}
        if path.startswith("notes/"):
            return 200, {"content": "stored"} if method == "GET" else {"ok": True}
        return 404, {"error": "no such endpoint"}

    def seen(self, method=None, prefix=""):
        return [r for r in self.requests if (method is None or r[0] == method) and r[1].startswith(prefix)]

    def __enter__(self):
        self.thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()
