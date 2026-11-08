"""HTTP front of the referee (stdlib only). Routes are listed in arena/DESIGN.md 3 and 5.2.

Auth: every request carries `Authorization: Bearer <token>`. The agent's side is decided by the token alone; the
relay and the admin each have their own token (tokens are written to <run>/secrets/ at start). Errors are
{"error": text, ...} with the HTTP status of the rule that was hit: 400 bad input, 401 no/wrong token, 403 not a
player (the game's AI side of an exercise), 404, 409 wrong state (idle side, stale turn, exercise quota), 429 limit,
503 view withheld by the fog assertion.
The same front serves a game referee (core.Referee) and the training referee (training.Training): an endpoint whose
method the object does not have is a 404 (a match has no exercises, training has no game).
"""
import hmac
import json
import os
import secrets
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote, urlsplit

from .core import ApiError

MAX_BODY = {"relay": 32 << 20, "agent": 1 << 20, "admin": 1 << 16}
WAIT_MAX = 120.0
DRAIN_MAX = 64 << 20


def write_tokens(run_dir):
    """One random token per side, for the relay and for the admin; files under <run>/secrets/."""
    d = os.path.join(run_dir, "secrets")
    os.makedirs(d, exist_ok=True)
    tokens = {}
    for name in ("side0", "side1", "relay", "admin"):
        tokens[name] = secrets.token_urlsafe(32)
        with open(os.path.join(d, name + ".token"), "w", encoding="utf-8", newline="\n") as f:
            f.write(tokens[name] + "\n")
    return tokens


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, ref, tokens):
        super().__init__(addr, Handler)
        self.ref, self.tokens = ref, tokens

    def who(self, header):
        """-> ("agent", side) | ("relay", None) | ("admin", None) | None"""
        tok = header[7:] if header.startswith("Bearer ") else ""
        for name, role in (("side0", ("agent", 0)), ("side1", ("agent", 1)), ("relay", ("relay", None)),
                           ("admin", ("admin", None))):
            if tok and hmac.compare_digest(tok, self.tokens[name]):
                return role
        return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "arena-referee"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method):
        try:
            url = urlsplit(self.path)
            path, q = unquote(url.path), dict(parse_qsl(url.query))
            n = int(self.headers.get("Content-Length") or 0)
            role = self.server.who(self.headers.get("Authorization", ""))
            if role is None:
                self.rfile.read(min(n, 1 << 20))
                return self._send(401, {"error": "missing or wrong token"})
            area = path.split("/")[1] if path.startswith("/") else ""
            if area not in MAX_BODY or role[0] != area:
                self.rfile.read(min(n, 1 << 20))
                return self._send(403 if area in MAX_BODY else 404, {"error": "not allowed here"})
            if n > MAX_BODY[area]:
                left = n                                        # read it off so the client sees the answer, not a reset
                while left > 0 and n <= DRAIN_MAX:
                    left -= len(self.rfile.read(min(left, 1 << 16)))
                self.close_connection = True
                return self._send(413, {"error": "body too large"})
            body = None
            if n:
                try:
                    body = json.loads(self.rfile.read(n).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return self._send(400, {"error": "body is not valid JSON"})
            self._send(200, self.route(method, area, path, q, body, role[1]))
        except ApiError as e:
            self._send(e.code, {"error": e.msg, **e.extra})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:                                      # a bug must not look like a rule decision
            traceback.print_exc(file=sys.stderr)
            self._send(500, {"error": "internal error"})

    def route(self, method, area, path, q, body, side):
        ref, parts = self.server.ref, path.strip("/").split("/")[1:]
        key = (method, parts[0] if parts else "")

        def call(name, *args):
            fn = getattr(ref, name, None)
            if fn is None:
                raise ApiError(404, "no such endpoint")
            return fn(*args)

        if area == "relay":
            if key == ("POST", "sync"):
                return call("sync", body or {})
            if key == ("GET", "ping"):
                return {"ok": True, "scenario": ref.sc.name}
        elif area == "admin":
            if key == ("GET", "state"):
                return call("admin_state")
            if key == ("POST", "pause"):
                return call("pause", bool((body or {}).get("on", True)))
            if key == ("POST", "start"):
                return call("start")
            if key == ("POST", "stop"):
                return call("stop")
            if key == ("GET", "exercises"):
                return call("admin_exercises")
            if key == ("POST", "exercise_update"):
                return call("exercise_update", body or {})
        else:
            if side not in getattr(ref, "players", (0, 1)):
                raise ApiError(403, "not a player in this exercise")
            rest = "/".join(parts[1:])
            if key == ("GET", "wait"):
                try:
                    timeout = min(float(q.get("timeout", 60)), WAIT_MAX)
                except ValueError:
                    raise ApiError(400, "timeout must be a number of seconds")
                return call("wait", side, timeout)
            if key == ("POST", "turn") and rest in ("begin", "end"):
                return call("turn_begin" if rest == "begin" else "turn_end", side, body)
            if key == ("GET", "view") and rest:
                return call("view", side, rest, q)
            if key == ("POST", "queue") and not rest:
                return call("submit", side, body)
            if key == ("POST", "queue") and rest == "lint":           # check only, nothing queued (materials/18 2.1)
                return call("lint", side, body or {})
            if key == ("POST", "scripts") and rest == "test":         # one run on the current picture, no effect
                return call("script_test", side, body or {})
            if key == ("DELETE", "queue") and rest:
                return call("withdraw", side, rest)
            if key == ("POST", "observe"):
                return call("observe", side, bool((body or {}).get("on", True)))
            if key[1] == "scripts" and method in ("GET", "PUT", "DELETE"):
                return call("scripts_api", side, method, rest, body)
            if key == ("POST", "compute"):
                return call("compute", side, body)
            if key[1] == "notes" and method in ("GET", "PUT"):
                return call("notes", side, method, rest, body)
            if key == ("POST", "exercise") and not rest:
                return call("request_exercise", side, body)
            if key == ("GET", "exercises") and not rest:
                return call("exercises", side)
        raise ApiError(404, "no such endpoint")

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")
