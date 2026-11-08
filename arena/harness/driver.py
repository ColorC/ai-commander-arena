"""One side's commander played by an official CLI harness (Kimi Code CLI or opencode) instead of arena-agent's own loop.

python -I -X utf8 arena/harness/driver.py --side 0 --cli kimi|opencode --referee http://127.0.0.1:41010 \
       --token-file <run>/secrets/side0.token --run <run> [--model <provider/model>]

The loop is deterministic and the same as arena-agent's (arena/agent/arena_agent.py, whose Agent this class extends):
/agent/wait long-polls; a wake becomes the same text arena-agent sends (head line, subagent reports, rendered
summary or the player's own wake text) and is sent as one ACP session/prompt to the side's commander session; the
model ends the turn with `arena turn end`. The system prompt (system.md rendered exactly as arena-agent renders it) is
the session's agent prompt, so both CLIs start from the same text.

Wake as a tool return (r5-minden 11-04: every wake arriving as a new user prompt "[唤醒 #N…] + summary" framed each
wake as "order and end", and both commanders hardly looked before ending): in a game the commander's `turn end`
closes the turn at the referee and then blocks (block_for_wake) until the side's next wake, which is the tool result;
the CLI's prompt turn just goes on. Only the first wake of a run (the prematch, with the briefing) is a prompt, and any
wake after a prompt turn that ended without a blocking turn end (the model stopped, a step cap, an error): the
fallback is the old way. The game ending during the block answers GAME_OVER; a block that reaches the CLI's tool
timeout (clis.BLOCK_SEC) answers STILL_GOING, which asks for turn end again. The referee's hidden backstop (its turn
deadline) no longer cancels the prompt: the model may think on, its calls answer that the turn is over, and its next
turn end returns the next wake (if the prompt ends first, that wake comes as a prompt); a prompt still running a whole
turn_limit past the deadline is cancelled as stuck. The training referee keeps the old non-blocking turn end (its turn
end can hand the session over to an exercise driver).

Tools: every agent's session has its own arena/harness/mcp_server.py (stdio MCP) with the one `arena` tool. Its
`turn end`, `agent spawn` and `agent check` are answered here (ops end_turn, spawn_subagent, check_subagents) through
a 127.0.0.1 control endpoint (own token) because the driver owns the turn ids and the subagents. At most 2
subagents: spawn opens a new CLI session (kimi: a session of the same process; opencode: one more process), calls
turn/begin, sends the task, and its end_turn report goes to the inbox that check_subagents and the next wake read,
as in arena-agent.

Files: <run>/agents/side<N>/transcript.jsonl (driver events and every ACP message: session/update, permission asks,
prompts), mcp.jsonl (every tool call), usage.jsonl (the provider's token counts per model call, read from the CLI's
own records), <cli>.stderr; the CLI works in <run>/cli/side<N>/. Exit codes as arena-agent: 0 game over, 2 referee
unreachable, 3 token refused.

Season 2 (materials/18 2.2), --home <homes> --player <name> [--kind players|sparring] --phase <label>: the commander
is the player's one season-long session (arena/harness/player.py). The driver takes the home's lock, writes the CLI
config for this phase, starts a fresh CLI process in the home's fixed cwd and continues the session with
session/resume (no history replay), session/load only when resume fails (session/new only when the home has none
yet), recording each (op new|resume|load) in session.json. The session's system
prompt is the fixed season prompt; this phase's rules (system.md / training.md rendered as before) are the head of
the first wake of the run, in full the first time the session sees that text, by reference after that, after the
referee's notice (--notice-file: e.g. that the game was restarted after a facility failure). Every agent has its own
CLI process; a subagent works in the home's scratch/<name>/. The replayed history of a load is counted, not logged.
After either continuation the CLI's settings (model) are set again: Kimi binds its default model on restore.
Facility failures end the run with exit code 4 and <run>/agents/side<N>/facility.json (kind, detail): the
commander's CLI exited without being asked to (cli_died), three prompt errors in a turn after the CLI's own retries
(provider_error), the session could not be loaded (session_load). 6: the session is held by another live driver.
POST <control>/stop (url in <run>/agents/side<N>/state/control.json, token in control.token) ends the run after the
current wait (the director hands the session from training to an exercise this way).
"""
import argparse
import hashlib
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from arena.agent import arena_agent, tools  # noqa: E402
from arena.harness import acp, clis, player  # noqa: E402

NUDGE = arena_agent.NUDGE
OBSERVE_NOTE = "\n（观察中：不调用任何工具的回复 = 等待下一次更新）"
FACILITY_EXIT, BUSY_EXIT = 4, 6
GAME_OVER = "对局结束。"
STILL_GOING = "游戏还在进行，本方下一个下令阶段还没到。执行 arena turn end 继续等待。"


class Control(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, driver, token):
        self.driver, self.token = driver, token

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    body = None
                if self.headers.get("Authorization") != "Bearer " + token or not isinstance(body, dict):
                    status, out = 401, {"error": "bad token or body"}
                else:
                    status, out = 200, driver.control(self.path.strip("/"), body)
                raw = json.dumps(out, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        super().__init__(("127.0.0.1", 0), Handler)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class Driver(arena_agent.Agent):
    def __init__(self, side, cli, referee, run, template, library_dir, referee_url, token_file, turn_limit=480, sub_limit=600,
                 max_subs=2, wait_timeout=60, max_turns=0, end_grace=20.0, mcp_ready_s=60.0, now=time.time, sleep=time.sleep,
                 home=None, phase=None, notice=None, block=True):
        self.dir = os.path.join(run, "agents", f"side{side}")
        self.home, self.phase, self.notice, self.run_dir = home, phase, (notice or "").strip(), os.path.abspath(run)
        self.cwd = home.cwd if home else os.path.join(run, "cli", f"side{side}")
        self.state_dir = home.state if home else self.dir   # CLI config (opencode) lives here
        os.makedirs(self.dir, exist_ok=True)
        os.makedirs(self.cwd, exist_ok=True)
        tr = arena_agent.Transcript(os.path.join(self.dir, "transcript.jsonl"), now)
        route = SimpleNamespace(name=f"cli:{cli.name}", model=cli.model, extra=None)
        super().__init__(side, None, referee, tools.Library(library_dir), tr, template, route, turn_limit, sub_limit, max_subs,
                         wait_timeout, 0, max_turns, now, sleep)
        self.cli, self.library_dir, self.referee_url, self.token_file = cli, library_dir, referee_url, os.path.abspath(token_file)
        self.end_grace, self.mcp_ready_s = end_grace, mcp_ready_s
        self.block = block                     # False: the old turn end (each wake a prompt), for tests and comparisons
        self.mcp_log = os.path.join(self.dir, "mcp.jsonl")
        self.usage_f = open(os.path.join(self.dir, "usage.jsonl"), "a", encoding="utf-8")
        self.shared = None            # kimi: the side's one ACP process
        self.clients = []             # every AcpClient started (closed at the end)
        self.sessions = {}            # ACP session id -> worker
        self.totals = {}
        self.replay = None            # while a session/load replays history: {"n", "bytes"} instead of logging it
        self.facility_failure, self.intro_sent = None, False
        tok = secrets.token_urlsafe(24)
        self.control_token_file = os.path.join(self.dir, "state", "control.token")
        clis.write_text(self.control_token_file, tok + "\n")
        self.ctl = Control(self, tok)
        threading.Thread(target=self.ctl.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
        clis.write_text(os.path.join(self.dir, "state", "control.json"), json.dumps({"url": self.ctl.url, "pid": os.getpid()}) + "\n")

    # ---- CLI sessions ----------------------------------------------------------------------------------------------
    def mcp_entry(self, agent):
        return acp.stdio_server(clis.MCP_NAME, sys.executable, [
            "-I", "-X", "utf8", os.path.join(HERE, "mcp_server.py"), "--referee", self.referee_url, "--token-file", self.token_file,
            "--log", self.mcp_log, "--agent", agent, "--control", self.ctl.url, "--control-token-file", self.control_token_file,
            "--library", self.library_dir])

    def _client(self, agent, cwd=None):
        def on_update(p):
            w = self.sessions.get(p.get("sessionId"))
            u = p.get("update") or {}
            if w is not None and u.get("sessionUpdate") == "agent_message_chunk":
                w.last_message += (u.get("content") or {}).get("text") or ""

        def on_raw(d, m, a=agent):
            r = self.replay
            if r is not None and d == "in" and isinstance(m, dict) and m.get("method") == "session/update":
                r["n"] += 1
                r["bytes"] += len(json.dumps(m, ensure_ascii=False).encode("utf-8"))
                return
            self.tr.log(a, "acp", dir=d, msg=m)

        holder = []

        def on_exit(code, a=agent):
            c = holder[0] if holder else None
            expected = bool(c and c.closing) or self.stop.is_set()
            self.tr.log(a, "cli_exit", code=code, expected=expected)
            if self.home is not None and a == "commander" and not expected:
                self.facility("cli_died", f"{self.cli.name} process {c.pid if c else '?'} of the commander exited with code {code}")
        cwd = cwd or self.cwd
        c = acp.AcpClient(self.cli.command(), cwd, self.cli.env(cwd, self.state_dir, agent), os.path.join(self.dir, f"{self.cli.name}.stderr"),
                          on_update=on_update, on_permission=lambda p, a=agent: self.permission(a, p), on_raw=on_raw, on_exit=on_exit)
        holder.append(c)
        c.start()
        self.clients.append(c)
        c.initialize()
        self.tr.log(agent, "cli_start", pid=c.pid, command=self.cli.command(), agent_info=(c.info or {}).get("agentInfo"))
        return c

    def open_session(self, w):
        """A CLI session for worker w (its MCP server knows w's name). Waits until that server has listed its tools.
        With a home, the commander continues the player's season session (load) or starts it (new, once)."""
        t0 = self.now()
        mcp = self.mcp_entry(w.name)
        season = self.home is not None and not w.sub
        cwd = self.home.cwd if season else (self.home.scratch(w.name) if self.home else self.cwd)
        self.cli.prepare(cwd, self.state_dir, self.home.system_text() if season else self.system, w.name, mcp)
        mcps = [mcp] if getattr(self.cli, "mcp_in_session", self.cli.shared_process) else []
        op, replay, extra = "new", None, {}
        if self.cli.shared_process:
            if self.shared is None or self.shared.proc.poll() is not None:
                self.shared = self._client("-")
            w.client = self.shared
            s = w.client.new_session(cwd, [mcp])
        else:
            w.client = self._client(w.name, cwd)
            sid = self.home.session_id if season else None
            if sid:
                op, replay = self.continue_session(w, sid, cwd, mcps, extra)
                s = {"sessionId": sid}
            else:
                s = w.client.new_session(cwd, mcps)
        w.session_id, w.usage_pos = s["sessionId"], 0
        self.sessions[w.session_id] = w
        for k, v in self.cli.settings():
            w.client.request("session/set_config_option", {"sessionId": w.session_id, "configId": k, "value": v}, 60)
        ready = self.wait_mcp_list(w.name, t0)
        time.sleep(getattr(self.cli, "settle_s", 0.0))
        secs = round(self.now() - t0, 2)
        if season:
            self.home.note_open(op, w.session_id, self.cli.name, self.cli.model, w.client.pid, phase=self.phase, run=self.run_dir,
                                seconds=secs, **({"replay_updates": replay["n"], "replay_bytes": replay["bytes"]} if replay else {}), **extra)
        self.tr.log(w.name, "session", session_id=w.session_id, pid=w.client.pid, mcp_listed=ready, seconds=secs, op=op,
                    **({"replay": replay} if replay else {}), **extra)

    def continue_session(self, w, sid, cwd, mcps, extra):
        """Continue the home's session in w's fresh CLI process: session/resume (no history replay; both CLIs passed it
        in tools/s2_session_probe.py), session/load (replays the whole history) only when resume fails; a failed
        resume is noted in extra. Either way the CLI binds its default model, so open_session sets it again after.
        -> (op, replay counts)"""
        self.replay = {"n": 0, "bytes": 0}
        try:
            try:
                w.client.resume_session(sid, cwd, mcps)
                return "resume", self.replay
            except Exception as e:                  # e.g. a CLI version without session/resume
                extra["resume_error"] = f"{type(e).__name__}: {e}"[:300]
                self.tr.log(w.name, "resume_failed", session_id=sid, error=extra["resume_error"])
            if w.client.proc.poll() is not None:     # the resume took the process down: load in a new one
                w.client = self._client(w.name, cwd)
            self.replay = {"n": 0, "bytes": 0}
            w.client.load_session(sid, cwd, mcps)
            return "load", self.replay
        finally:
            self.replay = None

    def wait_mcp_list(self, agent, since):
        end = time.monotonic() + self.mcp_ready_s
        while time.monotonic() < end:
            try:
                with open(self.mcp_log, encoding="utf-8") as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        if r.get("type") == "list" and r.get("agent") == agent and r.get("ts", 0) >= since - 1:
                            return True
            except OSError:
                pass
            time.sleep(0.25)
        return False

    def worker(self, name):
        return self.cmd if name in ("commander", "-") else self.subs.get(name)

    def permission(self, agent, p):
        """The lockdown should mean no permission asks at all; if one comes, allow only a referee tool."""
        blob = json.dumps(p.get("toolCall") or {}, ensure_ascii=False)
        ok = clis.MCP_NAME in blob and all(f'"{t}"' not in blob for t in ("shell", "bash", "read", "write", "edit", "webfetch", "websearch"))
        kind = "allow_once" if ok else "reject_once"
        pick = next((o.get("optionId") for o in p.get("options") or [] if o.get("kind") == kind), None)
        self.tr.log(agent, "permission", tool_call=tools.clip(blob, 1000), allowed=ok, option=pick)
        return pick

    def record_usage(self, w):
        if not getattr(w, "session_id", None):
            return
        try:
            recs, w.usage_pos = self.cli.usage(w.session_id, w.usage_pos)
        except Exception as e:  # usage is bookkeeping: never stop a turn for it
            self.tr.log(w.name, "error", where="usage", error=f"{type(e).__name__}: {e}")
            return
        for r in recs:
            self.usage_f.write(json.dumps({"agent": w.name, "session_id": w.session_id, **r}, ensure_ascii=False) + "\n")
            if "input" in r:
                t = self.totals.setdefault(r.get("model") or "?", {"calls": 0, "input": 0, "output": 0, "cache_read": 0})
                t["calls"] += 1
                for k in ("input", "output", "cache_read"):
                    t[k] += r.get(k) or 0
        self.usage_f.flush()

    # ---- control endpoint (called by the MCP servers) --------------------------------------------------------------
    def control(self, op, body):
        if op == "stop":  # the director takes the session back (after the current wait)
            self.tr.log("-", "stop_requested", by=body.get("by"))
            self.stop.set()
            return {"ok": True}
        w = self.worker(str(body.get("agent") or "commander"))
        if w is None:
            return {"text": json.dumps({"error": "未知代理"}, ensure_ascii=False), "is_error": True}
        w.tool_calls += 1
        if op == "after":                      # no time footer since 11-04 (wake_head / note_wake in arena_agent.py)
            note = ""
            if body.get("turn_over"):
                w.over = w.over or "turn_over"
            if body.get("observe") is not None and not w.sub:
                self.observing = bool(body["observe"])
                note = OBSERVE_NOTE if self.observing else ""
            return {"note": note}
        if op != "tool":
            return {"text": json.dumps({"error": f"unknown op {op}"}), "is_error": True}
        name, args = body.get("name"), body.get("arguments") if isinstance(body.get("arguments"), dict) else {}
        if name == "end_turn":
            args = tools._coerce(tools.ALL[name]["function"]["parameters"]["properties"], args)
            if self.blocks(w):
                text, over, err = self.end_and_wait(w, args)
                w.over = over
                return {"text": text, "is_error": err}
            text, done = self.end_turn(w, args)
            if done:
                w.over = done
            return {"text": text, "is_error": not done}
        if name == "spawn_subagent" and not w.sub:
            text = self.spawn(args)
        elif name == "check_subagents" and not w.sub:
            text = self.check(w, args)
        else:
            return {"text": json.dumps({"error": f"没有这个工具：{name}"}, ensure_ascii=False), "is_error": True}
        return {"text": text, "is_error": '"ok": false' in text}

    # ---- the blocking turn end -------------------------------------------------------------------------------------
    def blocks(self, w):
        """Whether w's turn end waits for the next wake: the commander in a game (not in training, whose turn end may
        hand the session to an exercise driver; not once --max-turns is reached)."""
        return (self.block and not w.sub and not self.training
                and not (self.max_turns and self.wake_n >= self.max_turns))

    def end_and_wait(self, w, args):
        """The commander's turn end in a game: close the open turn at the referee (one the referee closed itself, by
        its backstop, answers 409 and counts as closed; a turn already closed by an earlier turn end is not sent
        again), then block until the next wake. -> (tool text, over reason or None, is_error)"""
        if w.open:
            text, done = self.end_turn(w, args)
            if not done:
                return text, w.over, True             # refused (e.g. bad subscriptions): the turn stays open
            self.note_turn_end(w, done)
        w.blocking = True
        try:
            return self.block_for_wake(w)
        finally:
            w.blocking = False

    def block_for_wake(self, w):
        """Long-poll /agent/wait (no turn is open, so none of it is thinking time) until the side's next wake.
        -> (wake text, None, False) | (GAME_OVER, "game_over", False) | (STILL_GOING, "end_turn", False)"""
        end = self.now() + getattr(self.cli, "block_sec", clis.BLOCK_SEC)
        fails = conflicts = 0
        while not self.stop.is_set():
            left = end - self.now()
            if left <= 0:
                w.hard = self.now() + self.turn_limit     # the stuck guard of a prompt that goes on from here
                self.tr.log(w.name, "block_timeout", turn_id=w.turn_id)
                return STILL_GOING, "end_turn", False
            t0, wait = self.now(), max(1.0, min(self.wait_timeout, left))
            try:
                status, data = self.referee.call("GET", "wait", {"timeout": wait}, timeout=wait + 15)
            except tools.NET_ERRORS as e:
                fails += 1
                self.tr.log("-", "error", where="block_wait", error=f"{type(e).__name__}: {e}", consecutive=fails)
                if fails > 200:
                    self.exit_code = 2
                    self.stop.set()
                    break
                self.sleep(min(30, 2 * fails))
                continue
            fails = 0
            if status in (401, 403):
                self.tr.log("-", "error", where="block_wait", error=f"referee rejected the token (HTTP {status})")
                self.exit_code = 3
                self.stop.set()
                break
            if status == 410 or (isinstance(data, dict) and (data.get("game_over") or data.get("ended"))):
                self.tr.log("-", "game_over", status=status, via="turn_end")
                return GAME_OVER, "game_over", False
            if status == 409:                         # the referee still has our turn open: close it, as run() does
                conflicts += 1
                if conflicts > 60:
                    self.tr.log("-", "error", where="block_wait", error="the referee keeps answering 409 to wait", body=data)
                    self.exit_code = 2
                    self.stop.set()
                    break
                self.force_end(w, "wait_conflict")
                self.sleep(1)
                continue
            conflicts = 0
            if status != 200 or not isinstance(data, dict) or not data.get("woke") or data.get("turn_id") is None:
                if self.now() - t0 < 0.5:
                    self.sleep(0.5)
                continue
            self.wake_n += 1
            return self.begin_wake(w, self.wake_n, data, via="turn_end"), None, False
        return GAME_OVER, "stopped", False

    def begin_wake(self, w, n, data, via):
        """Open the commander's turn for a wait answer -> the wake text (head, subagent reports, rendered wake)."""
        w.turn_id, w.deadline, w.t0, w.end_failures, w.over = data["turn_id"], self.deadline_from(data, self.turn_limit), self.now(), 0, None
        w.hard = w.deadline + self.turn_limit
        self.observing = False
        self.note_wake(w, data)
        summary = self.render(data)
        self.tr.log(w.name, "wake", n=n, turn_id=w.turn_id, deadline=w.deadline, prematch=data.get("prematch"), reasons=data.get("reasons"),
                    summary=tools.clip(summary, 3000), summary_chars=len(summary), via=via)
        return self.wake_head(n, w) + self.drain_reports() + summary

    def note_turn_end(self, w, reason):
        w.open, w.ended_id = False, w.turn_id
        self.record_usage(w)
        self.tr.log(w.name, "turn_end", turn_id=w.turn_id, reason=reason, seconds=round(self.now() - w.t0, 2), requests=w.requests, tool_calls=w.tool_calls)

    @staticmethod
    def last_text(w):
        return (getattr(w, "last_message", "") or "").strip()

    def facility(self, kind, detail):
        """A failure outside the model's control: recorded once, the run ends with FACILITY_EXIT (the director
        restarts the game or the phase, free of charge)."""
        if self.facility_failure is None:
            self.facility_failure = {"kind": kind, "detail": detail, "time": player.now_iso(), "phase": self.phase}
            clis.write_text(os.path.join(self.dir, "facility.json"), json.dumps(self.facility_failure, ensure_ascii=False) + "\n")
            self.tr.log("-", "facility_failure", **self.facility_failure)
            self.exit_code = FACILITY_EXIT
        self.stop.set()

    def phase_intro(self):
        """The head of the first wake of this run (season mode): the referee's notice, then this phase's rules."""
        sha = hashlib.sha1((self.system or "").encode("utf-8")).hexdigest()
        if self.home.phase_text_seen(sha):
            rules = f"本阶段的规则与你之前收到的规则 {sha[:8]} 一字不差，这里不再重复。"
        else:
            rules = f"本阶段的规则全文（规则编号 {sha[:8]}）：\n\n{self.system.strip()}"
        notice = f"[裁判通知]\n{self.notice}\n\n" if self.notice else ""
        return f"[阶段开始：{self.phase or '未命名阶段'}]\n{notice}{rules}\n\n[本阶段第一次唤醒]\n", sha

    # ---- one turn --------------------------------------------------------------------------------------------------
    def turn(self, n, data):
        w = self.cmd
        w.requests, w.tool_calls = 0, 0
        if self.system is None:
            self.system = self.build_system(data.get("briefing"))
        first = self.begin_wake(w, n, data, via="prompt")
        reason, intro, sha = "error", "", None
        try:
            if self.home is not None and getattr(w, "session_id", None) is not None and w.client.proc.poll() is not None:
                self.facility("cli_died", f"the commander's {self.cli.name} process {w.client.pid} is gone (code {w.client.proc.poll()})")
                raise StopIteration("cli_died")
            if getattr(w, "session_id", None) is None or w.client.proc.poll() is not None:
                try:
                    self.open_session(w)
                except Exception as e:
                    if self.home is None:
                        raise
                    self.facility("session_load" if self.home.session_id else "session_new", f"{type(e).__name__}: {e}")
                    raise StopIteration("session_open_failed")
            if self.home is not None and not self.intro_sent:
                intro, sha = self.phase_intro()
            reason = self.converse(w, intro + first)
            if sha:
                self.intro_sent = True
                self.home.note_phase_text(sha)
        except StopIteration as e:
            reason = str(e)
        except Exception as e:
            self.tr.log(w.name, "error", where="turn", error=f"{type(e).__name__}: {e}")
            w.session_id = None  # the next wake opens a new session (the old one's context is lost; logged above)
        if reason not in ("end_turn", "turn_over", "game_over"):
            self.force_end(w, reason)
        if getattr(w, "ended_id", None) != w.turn_id:      # a blocking turn end has logged the turns it closed
            self.note_turn_end(w, reason)
        w.open = False

    def converse(self, w, first):
        """Prompt the worker's session until end_turn, the deadline, or three replies without a tool call. -> reason"""
        w.over, text, nudges, errors = None, first, 0, 0
        while True:
            w.requests += 1
            w.last_message, calls0 = "", w.tool_calls
            p = w.client.prompt_async(w.session_id, text)
            reason = self.await_prompt(w, p)
            if reason:
                return reason
            if p.error is not None:
                errors += 1
                self.tr.log(w.name, "error", where="prompt", error=p.error)
                if w.client.proc.poll() is not None:
                    return "error"
                if errors >= 3:
                    if self.home is not None and not w.sub:  # after the CLI's own retries: not the model's doing
                        self.facility("provider_error", f"three prompt errors in turn {w.turn_id}, last: {str(p.error)[:300]}")
                        return "provider_error"
                    return "error"
                text = NUDGE
                continue
            if w.over:
                return w.over
            if self.observing and not w.sub:
                obs = self.wait_observe(w)
                if obs is None:
                    return "deadline"
                text = obs
                continue
            nudges = 0 if w.tool_calls > calls0 else nudges + 1
            if nudges > 2:
                return "no_tool_call"
            text = NUDGE

    def await_prompt(self, w, p):
        """Wait for one prompt. -> None when it finished on its own; a reason when the driver had to stop it."""
        over_at = None
        while not p.done():
            now = self.now()
            if self.stop.is_set():
                return self.cancel(w, p, "stopped")
            if getattr(w, "blocking", False):         # inside the blocking turn end: no deadline, no grace
                over_at = None
                p.event.wait(1.0)
                continue
            blocking = self.blocks(w)
            if w.over and over_at is None:
                over_at = now
            # the old way (subagents, training, block off): end_grace after the turn ended, cancel at the deadline. A
            # blocking commander only after the game is over; the referee's deadline is its hidden backstop, the
            # driver cancels a prompt only a turn_limit past it (w.hard: stuck)
            if over_at is not None and (not blocking or w.over == "game_over") and now - over_at >= self.end_grace:
                return self.cancel(w, p, w.over)
            limit = getattr(w, "hard", w.deadline) if blocking else w.deadline
            if now >= limit:
                return self.cancel(w, p, w.over or "deadline")
            p.event.wait(min(1.0, max(0.05, limit - now)))
        return None

    def cancel(self, w, p, reason):
        w.client.cancel(w.session_id)
        try:
            p.wait(30)
        except (acp.AcpError, TimeoutError) as e:
            self.tr.log(w.name, "error", where="cancel", error=str(e))
        self.tr.log(w.name, "cancel", reason=reason)
        return reason

    # ---- subagents -------------------------------------------------------------------------------------------------
    def run_sub(self, s, task):
        s.t0, reason, s.last_message, s.over = self.now(), "error", "", None
        try:
            status, data = self.referee.call("POST", "turn/begin", body={"agent": s.name})
            if not 200 <= status < 300:
                s.report = f"referee 拒绝开始任务（HTTP {status}）：{json.dumps(data, ensure_ascii=False)[:300]}"
                return
            s.turn_id, s.deadline = (data or {}).get("turn_id") if isinstance(data, dict) else None, self.deadline_from(data, self.sub_limit)
            self.open_session(s)
            first = arena_agent.SUB_FIRST.format(name=s.name, secs=int(s.deadline - self.now()), task=task)
            reason = self.converse(s, first)
            if reason != "end_turn":
                if reason != "turn_over":
                    self.force_end(s, reason)
                s.report = f"（任务被强制结束：{reason}，未交正式报告）最后一段输出：" + (self.last_text(s) or "无")
        except Exception as e:  # a crashed subagent must not take the process down
            self.tr.log(s.name, "error", where="subagent", error=f"{type(e).__name__}: {e}")
            s.report = f"子代理出错：{type(e).__name__}: {e}"
        finally:
            self.record_usage(s)
            if not self.cli.shared_process and getattr(s, "client", None):
                self.close_client(s.client)
            self.tr.log(s.name, "subagent", event="end", reason=reason, seconds=round(self.now() - s.t0, 2), requests=s.requests,
                        tool_calls=s.tool_calls, report=tools.clip(s.report, 2000))
            with self.cv:
                s.running = False
                self.inbox.append({"name": s.name, "reason": reason, "report": s.report})
                self.cv.notify_all()

    # ---- shutdown --------------------------------------------------------------------------------------------------
    def close_client(self, c):
        kids = acp.child_pids(c.pid)
        code = c.close()
        time.sleep(1)
        left = [k for k in [c.pid] + kids if acp.pid_alive(k)]
        self.tr.log("-", "cli_closed", pid=c.pid, children=kids, exit_code=code, still_running=left)
        return left

    def shutdown(self):
        self.record_usage(self.cmd)
        left = []
        for c in self.clients:
            if c.proc and (c.proc.poll() is None or c is self.shared):
                left += self.close_client(c)
        self.ctl.shutdown()
        self.ctl.server_close()
        self.tr.log("-", "usage_total", cli=self.cli.name, totals=self.totals)
        self.usage_f.close()
        return left


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--side", type=int, required=True, choices=(0, 1))
    ap.add_argument("--cli", required=True, choices=("kimi", "opencode", "fake"))
    ap.add_argument("--fake-script", help="--cli fake: the scripted model (arena/tests/fake_acp_cli.py)")
    ap.add_argument("--home", help="season 2: the homes root (e.g. $ARENA_DATA/season2); needs --player")
    ap.add_argument("--player", help="season 2: the player's (or sparring partner's) name under the homes root")
    ap.add_argument("--kind", default="players", choices=player.KINDS)
    ap.add_argument("--phase", help="season 2: this run's phase label (start task, training, exercise p0-1, formal game 1)")
    ap.add_argument("--notice-file", help="season 2: the referee's notice for the head of the first wake (UTF-8)")
    ap.add_argument("--model", help="kimi: kimi-code/k3.1-pro (default); opencode: provider/model (default proxy/deepseek-v4.1-flash)")
    ap.add_argument("--referee", default="http://127.0.0.1:41010")
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--library", default=os.path.join(ROOT, "arena", "library"))
    ap.add_argument("--turn-limit", type=float, default=480)
    ap.add_argument("--sub-limit", type=float, default=600)
    ap.add_argument("--max-subs", type=int, default=2)
    ap.add_argument("--wait-timeout", type=float, default=60)
    ap.add_argument("--max-turns", type=int, default=0)
    a = ap.parse_args(argv)
    if bool(a.home) != bool(a.player):
        ap.error("--home and --player go together")
    token = Path(a.token_file).read_text(encoding="utf-8").strip()
    template = (Path(ROOT) / "arena" / "agent" / "prompts" / "system.md").read_text(encoding="utf-8")
    cli = clis.make(a.cli, a.model, a.fake_script)
    home = None
    if a.home:
        home = player.Home(a.home, a.player, a.kind)
        home.check_cli(cli.name, cli.model)
        try:
            home.acquire(a.phase, os.path.abspath(a.run))
        except player.SessionBusy as e:
            print(f"driver: {e}", file=sys.stderr, flush=True)
            return BUSY_EXIT
    notice = Path(a.notice_file).read_text(encoding="utf-8") if a.notice_file else None
    try:
        d = Driver(a.side, cli, tools.Referee(a.referee, token), a.run, template, a.library, a.referee, a.token_file,
                   a.turn_limit, a.sub_limit, a.max_subs, a.wait_timeout, a.max_turns, home=home, phase=a.phase, notice=notice)
        try:
            d.run()
        except KeyboardInterrupt:
            d.stop.set()
        finally:
            d.shutdown()
            d.tr.close()
    finally:
        if home is not None:
            home.release()
    return d.exit_code


if __name__ == "__main__":
    sys.exit(main())
