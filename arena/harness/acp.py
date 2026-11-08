"""Agent Client Protocol client over a child process's stdio (JSON-RPC 2.0, one message per line).

The child is a CLI started as an ACP server (`kimi acp`, `opencode acp`). This side is the client: it sends
initialize / session/new / session/prompt / session/cancel, receives session/update notifications, and answers the
agent's requests to the client (permission asks are decided by a callback; file system and terminal requests are
refused because this client advertises neither capability).
"""
import json
import os
import subprocess
import threading
import time

PROTOCOL_VERSION = 1


class AcpError(RuntimeError):
    def __init__(self, msg, code=None, data=None):
        super().__init__(msg)
        self.code, self.data = code, data


class Pending:
    def __init__(self, method):
        self.method, self.event, self.result, self.error, self.t0 = method, threading.Event(), None, None, time.monotonic()

    def wait(self, timeout=None):
        if not self.event.wait(timeout):
            raise TimeoutError(f"no answer to {self.method} within {timeout} s")
        if self.error is not None:
            raise AcpError(f"{self.method}: {self.error.get('message')}", self.error.get("code"), self.error.get("data"))
        return self.result

    def done(self):
        return self.event.is_set()


class AcpClient:
    """on_update(params) gets every session/update; on_permission(params) -> optionId or None (None = cancelled);
    on_raw(direction, msg) sees every message (for the transcript)."""

    def __init__(self, cmd, cwd, env=None, stderr_path=None, on_update=None, on_permission=None, on_raw=None, on_exit=None):
        self.cmd, self.cwd, self.env, self.stderr_path = cmd, cwd, env, stderr_path
        self.on_update, self.on_permission = on_update or (lambda p: None), on_permission or (lambda p: None)
        self.on_raw, self.on_exit = on_raw or (lambda d, m: None), on_exit or (lambda code: None)
        self.proc, self.next_id, self.pending, self.lock, self.wlock = None, 0, {}, threading.Lock(), threading.Lock()
        self.info, self.closing = None, False  # closing: this side asked the CLI to exit (its exit is expected)

    @property
    def pid(self):
        return self.proc.pid if self.proc else None

    def start(self):
        err = open(self.stderr_path, "ab") if self.stderr_path else subprocess.DEVNULL
        try:
            self.proc = subprocess.Popen(self.cmd, cwd=self.cwd, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            if self.stderr_path:
                err.close()
        threading.Thread(target=self._read, name="acp-read", daemon=True).start()
        return self

    def initialize(self, timeout=120):
        self.info = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "ai-commander-arena-harness", "version": "0.1"}}, timeout)
        return self.info

    # ---- sending ----------------------------------------------------------------------------------------------------
    def _send(self, msg):
        self.on_raw("out", msg)
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self.wlock:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def request_async(self, method, params):
        with self.lock:
            self.next_id += 1
            mid = self.next_id
            p = self.pending[mid] = Pending(method)
        try:
            self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        except OSError as e:
            with self.lock:
                self.pending.pop(mid, None)
            p.error = {"message": f"cannot write to the CLI: {e}"}
            p.event.set()
        return p

    def request(self, method, params, timeout=None):
        return self.request_async(method, params).wait(timeout)

    def notify(self, method, params):
        try:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})
        except OSError:
            pass

    def respond(self, mid, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": mid}
        msg.update({"error": error} if error else {"result": result})
        try:
            self._send(msg)
        except OSError:
            pass

    # ---- receiving --------------------------------------------------------------------------------------------------
    def _read(self):
        for raw in self.proc.stdout:
            if not raw.strip():
                continue
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self.on_raw("in-junk", raw[:2000].decode("utf-8", "replace"))
                continue
            self.on_raw("in", msg)
            try:
                self._dispatch(msg)
            except Exception as e:  # a callback bug must not stop the reader
                self.on_raw("error", {"error": f"{type(e).__name__}: {e}"})
        code = self.proc.wait()
        with self.lock:
            left, self.pending = list(self.pending.values()), {}
        for p in left:
            p.error = {"message": f"the CLI exited (code {code})"}
            p.event.set()
        self.on_exit(code)

    def _dispatch(self, msg):
        if "method" not in msg:
            with self.lock:
                p = self.pending.pop(msg.get("id"), None)
            if p:
                p.result, p.error = msg.get("result"), msg.get("error")
                p.event.set()
            return
        method, params, mid = msg["method"], msg.get("params") or {}, msg.get("id")
        if method == "session/update":
            self.on_update(params)
        elif method == "session/request_permission" and mid is not None:
            option = self.on_permission(params)
            self.respond(mid, {"outcome": {"outcome": "selected", "optionId": option} if option else {"outcome": "cancelled"}})
        elif mid is not None:  # fs/*, terminal/*, elicitation: not offered
            self.respond(mid, error={"code": -32601, "message": f"{method} is not available in this client"})

    # ---- session helpers --------------------------------------------------------------------------------------------
    def new_session(self, cwd, mcp_servers, timeout=180):
        return self.request("session/new", {"cwd": cwd, "mcpServers": mcp_servers}, timeout)

    def load_session(self, session_id, cwd, mcp_servers, timeout=600):
        """session/load (agentCapabilities.loadSession): the CLI restores the stored session in this process and
        replays its history as session/update notifications before it answers. cwd must be the session's own
        (Kimi keys its session store by working directory)."""
        return self.request("session/load", {"sessionId": session_id, "cwd": cwd, "mcpServers": mcp_servers}, timeout)

    def resume_session(self, session_id, cwd, mcp_servers, timeout=600):
        """session/resume (sessionCapabilities.resume): as load, without the history replay."""
        return self.request("session/resume", {"sessionId": session_id, "cwd": cwd, "mcpServers": mcp_servers}, timeout)

    def prompt_async(self, session_id, text):
        return self.request_async("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]})

    def cancel(self, session_id):
        self.notify("session/cancel", {"sessionId": session_id})

    def close(self, grace=15.0):
        """Close stdin (the CLI's signal to shut down), wait, then end this one PID if it is still there."""
        if not self.proc:
            return None
        self.closing = True
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            return self.proc.wait(grace)
        except subprocess.TimeoutExpired:
            self.proc.terminate()  # TerminateProcess on exactly this PID
            try:
                return self.proc.wait(10)
            except subprocess.TimeoutExpired:
                return None


def stdio_server(name, command, args, env=None):
    """An ACP McpServer entry (stdio)."""
    return {"name": name, "command": command, "args": list(args), "env": [{"name": k, "value": v} for k, v in (env or {}).items()]}


def child_pids(pid):
    """PIDs whose parent is `pid` (Windows: Win32_Process via PowerShell; elsewhere: /proc). Used to check that a CLI's
    children are gone after it exits."""
    if pid is None:
        return []
    if os.name == "nt":
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  f"(Get-CimInstance Win32_Process -Filter 'ParentProcessId={int(pid)}').ProcessId"],
                                 capture_output=True, text=True, timeout=60, errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        return [int(x) for x in out.split() if x.strip().isdigit()]
    out = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as f:
                    if int(f.read().split(")")[-1].split()[1]) == pid:
                        out.append(int(d))
            except (OSError, ValueError, IndexError):
                pass
    return out


def pid_alive(pid):
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH", "/FO", "CSV"], capture_output=True, text=True, timeout=30, errors="replace",
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return f'"{int(pid)}"' in (out or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
