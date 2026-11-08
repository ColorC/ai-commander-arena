"""MCP server (stdio, JSON-RPC 2.0, one message per line) that gives a CLI harness arena-agent's one tool, `arena`.

python -I -X utf8 arena/harness/mcp_server.py --referee http://127.0.0.1:41010 --token-file <side token> --log <run>/agents/side<N>/mcp.jsonl
       [--agent commander|<sub name>] [--control http://127.0.0.1:<port> --control-token-file <path>] [--library <dir>]

The tool is arena/agent/cli.py's (name, description and schema are the ones arena-agent sends: cmd = a command line,
input = multi-line text) and runs the same command line, so both ways to play behave alike. `turn end`, `agent spawn`
and `agent check` come back from the command line as requests for the driver (arena/harness/driver.py), which owns the
turn ids and the subagent sessions: they go to its control endpoint as its ops end_turn / spawn_subagent /
check_subagents, and the answer is printed the way arena-agent prints it (cli.format_driver); the commander's turn
end blocks there until the next wake and its answer is the wake text. After every other call the driver is told the
result (turn over, observation mode) and may answer a note to append (no time stamps since 11-04). Without --control
(offline use) those three commands answer an error.
Every tools/call is one line of the log: arguments, result, HTTP status, seconds.
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from arena.agent import cli, tools  # noqa: E402

SERVER_INFO = {"name": "arena-referee", "version": "0.1"}
DEFAULT_PROTOCOL = "2025-06-18"
DRIVER_OPS = ("end_turn", "spawn_subagent", "check_subagents")      # what the command line hands to the driver
TURN_END_TIMEOUT = 24 * 3600


def mcp_tools(sub=False):
    """The one tool in MCP shape: name, description, inputSchema (a subagent's server lists the same)."""
    return [cli.mcp_tool()]


class Log:
    """Appends one JSON line per record; every line is a single write on an O_APPEND handle (the commander's and the
    subagents' servers share one file)."""

    def __init__(self, path, now=time.time):
        self.path, self.now, self.lock = path, now, threading.Lock()
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self.fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)

    def write(self, **fields):
        if not self.path:
            return
        line = json.dumps({"ts": round(self.now(), 3), **fields}, ensure_ascii=False, default=str) + "\n"
        with self.lock:
            os.write(self.fd, line.encode("utf-8"))


class Control:
    """The driver's control endpoint (127.0.0.1, its own token)."""

    def __init__(self, url, token, timeout=90):
        self.url, self.timeout, self._auth = url.rstrip("/"), timeout, "Bearer " + token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def post(self, op, body, timeout=None):
        req = urllib.request.Request(f"{self.url}/{op}", data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                     headers={"Authorization": self._auth, "Content-Type": "application/json"})
        with self.opener.open(req, timeout=timeout or self.timeout) as r:
            return json.loads(r.read() or b"{}")


class ArenaTools:
    """tools/call for one agent of one side."""

    def __init__(self, referee, library, agent="commander", control=None):
        self.referee, self.agent, self.control = referee, agent, control
        self.sub = agent != "commander"
        self.cli = cli.Cli(referee, library, sub=self.sub)
        self.lock = threading.Lock()        # tools/call runs in threads; one command line runs one command at a time
        self.names = {cli.TOOL_NAME}

    def call(self, name, args):
        """-> (text, is_error, http_status)"""
        if name not in self.names:
            return f"错误：没有工具 {name}。只有一个工具 arena：cmd 写命令行，如 help、unit list、order add 1/A move 2118", True, None
        if not isinstance(args, dict):
            return "错误：参数必须是 JSON 对象", True, None
        cmd = args.get("cmd")
        if not isinstance(cmd, str) or not cmd.strip():
            return "错误：cmd 不能为空；先试 arena help", True, None
        with self.lock:
            out = self.cli.run(cmd, args.get("input"))
        if out.driver:
            return self._driver(*out.driver)
        text = out.text
        if self.control:
            observe = out.observe if out.status and 200 <= out.status < 300 else None
            try:
                info = self.control.post("after", {"agent": self.agent, "name": name, "cmd": cmd, "status": out.status,
                                                   "turn_over": out.turn_over, "observe": observe})
            except (OSError, ValueError):
                info = {}
            text += info.get("note") or ""
            if info.get("footer"):
                text += "\n" + info["footer"]
        return text, out.error, out.status

    def _driver(self, op, args):
        if self.control:
            try:
                # a commander's turn end blocks in the driver until the next wake (driver.py block_for_wake), at most
                # the CLI's tool timeout; if the driver dies the connection drops at once
                out = self.control.post("tool", {"agent": self.agent, "name": op, "arguments": args},
                                        timeout=90 if op == "check_subagents" else
                                        TURN_END_TIMEOUT if op == "end_turn" and not self.sub else 60)
            except (OSError, ValueError) as e:
                return f"错误：驱动程序没有响应：{type(e).__name__}: {e}", True, None
            text = cli.format_driver(op, out.get("text") or "")
            if out.get("footer"):
                text += "\n" + out["footer"]
            return text, bool(out.get("is_error")), out.get("http_status")
        # the commander's turn/end needs the turn id, which only the driver has
        what = {"end_turn": "turn end", "spawn_subagent": "agent spawn", "check_subagents": "agent check"}[op]
        return f"错误：{what} 需要驱动程序（arena/harness/driver.py）", True, None


class Server:
    def __init__(self, handler, log, rin, rout):
        self.h, self.log, self.rin, self.rout, self.wlock = handler, log, rin, rout, threading.Lock()

    def send(self, msg):
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self.wlock:
            self.rout.write(data)
            self.rout.flush()

    def serve(self):
        threads = []
        for raw in self.rin:
            if not raw.strip():
                continue
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                continue
            for m in msg if isinstance(msg, list) else [msg]:
                if isinstance(m, dict) and m.get("method") == "tools/call" and "id" in m:  # may block (check_subagents): own thread
                    t = threading.Thread(target=self.dispatch, args=(m,), daemon=True)
                    t.start()
                    threads.append(t)
                else:
                    self.dispatch(m)
        for t in threads:
            t.join(5)

    def dispatch(self, m):
        if not isinstance(m, dict) or "method" not in m:
            return  # a response to a request we never send, or junk
        mid, method, params = m.get("id"), m["method"], m.get("params") or {}
        try:
            result = self.handle(method, params)
        except KeyError:
            if mid is not None:
                self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}})
            return
        except Exception as e:  # a bug must reach the client as an error, not kill the server
            if mid is not None:
                self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}})
            return
        if mid is not None:
            self.send({"jsonrpc": "2.0", "id": mid, "result": result})

    def handle(self, method, params):
        if method == "initialize":
            v = params.get("protocolVersion")
            self.log.write(agent=self.h.agent, type="initialize", client=params.get("clientInfo"), protocol=v)
            return {"protocolVersion": v if isinstance(v, str) and v else DEFAULT_PROTOCOL,
                    "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO}
        if method.startswith("notifications/"):
            return None
        if method == "ping":
            return {}
        if method == "tools/list":
            out = mcp_tools(self.h.sub)
            self.log.write(agent=self.h.agent, type="list", tools=[t["name"] for t in out])
            return {"tools": out}
        if method == "tools/call":
            name, args, t0 = params.get("name"), params.get("arguments") or {}, time.monotonic()
            text, error, status = self.h.call(name, args)
            self.log.write(agent=self.h.agent, type="call", tool=name, arguments=tools.clip(json.dumps(args, ensure_ascii=False), 2000),
                           result=tools.clip(text, 4000), result_chars=len(text), is_error=error, http_status=status,
                           seconds=round(time.monotonic() - t0, 3))
            return {"content": [{"type": "text", "text": text}], "isError": error}
        raise KeyError(method)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--referee", required=True)
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--log", help="mcp.jsonl path (one line per tools/call)")
    ap.add_argument("--agent", default="commander", help="commander or the subagent's name")
    ap.add_argument("--control", help="the driver's control endpoint")
    ap.add_argument("--control-token-file")
    ap.add_argument("--library", default=os.path.join(ROOT, "arena", "library"))
    a = ap.parse_args(argv)
    referee = tools.Referee(a.referee, Path(a.token_file).read_text(encoding="utf-8").strip())
    control = Control(a.control, Path(a.control_token_file).read_text(encoding="utf-8").strip()) if a.control else None
    handler = ArenaTools(referee, tools.Library(a.library), a.agent, control)
    Server(handler, Log(a.log), sys.stdin.buffer, sys.stdout.buffer).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
