"""A stand-in for `kimi acp` / `opencode acp` in the harness tests: an ACP server whose "model" is a script.

python fake_acp_cli.py <script.json>

script: {"<agent name>": <plan>} where a plan is either a list of step lists (one per prompt, in order) or
{"rules": [{"match": text, "seq": [step list, ...], "then": step list}, ...], "default": step list}: each prompt runs
the first rule whose `match` is in the prompt text (its seq one after another, then `then`; a rule with neither left
is skipped), else `default`. A step is {"arena": cmd, "input": text} (a call of the `arena` tool), {"call": tool,
"args": {...}} (any tool by name), {"say": text}, {"sleep": seconds} or {"exit": code} (the CLI process dies right
there: a facility failure for the harness). A tool step with "chain": true goes on in the same prompt turn: its
result text picks the next step list as a prompt would (the blocking `arena turn end`, whose result is the next wake,
the way a real model carries on after it); the rest of the current list is dropped.
session/new starts the session's first MCP server (stdio) and lists its tools; the agent name is the server's --agent
argument. Each session/prompt runs that agent's next step list through the MCP server (tools/call), reports it as
session/update notifications, and answers stopReason end_turn (or cancelled after a session/cancel).
Sessions persist in <script>.sessions/<id>.json (plan position, every prompt and reply), so session/load and
session/resume in a new process continue them; load first replays the history as session/update notifications.
{"_cli": {"no_resume": true}} in the script: session/resume answers "method not found" (a CLI without it).
Every message received is appended to <script>.log (one JSON line) for the tests to read.
"""
import json
import os
import subprocess
import sys
import threading
import time
import uuid

script_path = sys.argv[1]
SCRIPT = json.load(open(script_path, encoding="utf-8"))
LOG = open(script_path + ".log", "a", encoding="utf-8")
STORE = script_path + ".sessions"
OUT_LOCK = threading.Lock()
sessions = {}


def send(msg):
    with OUT_LOCK:
        sys.stdout.buffer.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()


class Mcp:
    def __init__(self, server):
        env = None
        if server.get("env"):
            env = {**os.environ, **{e["name"]: e["value"] for e in server["env"]}}
        self.p = subprocess.Popen([server["command"], *server["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        self.n = 0
        self.agent = server["args"][server["args"].index("--agent") + 1] if "--agent" in server["args"] else "commander"
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake-acp-cli"}})
        self.tools = [t["name"] for t in self.rpc("tools/list", {})["tools"]]

    def rpc(self, method, params):
        self.n += 1
        self.p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n").encode("utf-8"))
        self.p.stdin.flush()
        while True:
            m = json.loads(self.p.stdout.readline())
            if m.get("id") == self.n:
                return m.get("result")

    def close(self):
        self.p.stdin.close()
        self.p.wait(10)


def save(sid):
    s = sessions[sid]
    os.makedirs(STORE, exist_ok=True)
    tmp = os.path.join(STORE, f"{sid}.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: s[k] for k in ("agent", "pos", "used", "history")}, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(STORE, f"{sid}.json"))


def next_steps(s, text):
    plan = SCRIPT.get(s["agent"], [])
    if isinstance(plan, list):
        s["pos"] += 1
        return plan[s["pos"] - 1] if s["pos"] <= len(plan) else [{"say": "（脚本已用完）"}]
    for i, r in enumerate(plan.get("rules") or []):
        if r.get("match", "") not in text:
            continue
        k = s["used"].get(str(i), 0)
        seq = r.get("seq") or []
        if k < len(seq) or "then" in r:
            s["used"][str(i)] = k + 1
            return seq[k] if k < len(seq) else r["then"]
    return plan.get("default") or [{"say": "（没有匹配的规则）"}]


def run_prompt(mid, sid, text):
    s = sessions[sid]
    s["history"].append({"role": "user", "text": text})
    steps = list(next_steps(s, text))
    save(sid)
    while steps:
        st = steps.pop(0)
        if s["cancel"].is_set():
            break
        if "exit" in st:
            s["history"].append({"role": "agent", "text": f"(exit {st['exit']})"})
            save(sid)
            LOG.flush()
            os._exit(int(st["exit"]))
        if "sleep" in st:
            s["cancel"].wait(st["sleep"])
        elif "say" in st:
            s["history"].append({"role": "agent", "text": st["say"]})
            send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {
                "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": st["say"]}}}})
        else:
            if "arena" in st:
                name, args = "arena", {"cmd": st["arena"], **({"input": st["input"]} if "input" in st else {})}
            else:
                name, args = st["call"], st.get("args") or {}
            r = s["mcp"].rpc("tools/call", {"name": name, "arguments": args})
            s["history"].append({"role": "agent", "text": f"(tool {name} {args.get('cmd') or ''})".replace(" )", ")")})
            send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {
                "sessionUpdate": "tool_call_update", "toolCallId": name, "status": "completed",
                "rawOutput": r}}})
            if st.get("chain"):            # go on in this prompt turn with the steps the tool result matches
                result = "".join(c.get("text") or "" for c in (r or {}).get("content") or [])
                s["history"].append({"role": "tool", "text": result})
                steps = list(next_steps(s, result))
                save(sid)
    save(sid)
    send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "cancelled" if s["cancel"].is_set() else "end_turn"}})


def open_stored(mid, params, replay):
    sid = params.get("sessionId")
    try:
        with open(os.path.join(STORE, f"{sid}.json"), encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError, TypeError):
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown sessionId: {sid}"}})
        return
    mcp = Mcp(params["mcpServers"][0]) if params.get("mcpServers") else None
    sessions[sid] = {**st, "mcp": mcp, "cancel": threading.Event()}
    if replay:
        for h in st["history"]:
            kind = "user_message_chunk" if h["role"] == "user" else "agent_message_chunk"
            send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {
                "sessionUpdate": kind, "content": {"type": "text", "text": h["text"]}}}})
    send({"jsonrpc": "2.0", "id": mid, "result": {}})


for raw in sys.stdin.buffer:
    if not raw.strip():
        continue
    m = json.loads(raw)
    LOG.write(json.dumps(m, ensure_ascii=False) + "\n")
    LOG.flush()
    method, mid, params = m.get("method"), m.get("id"), m.get("params") or {}
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1, "agentCapabilities": {
            "loadSession": True, "sessionCapabilities": {"resume": {}}}, "agentInfo": {"name": "fake-acp-cli"}}})
    elif method == "session/new":
        sid = f"fake-{uuid.uuid4().hex[:12]}"
        mcp = Mcp(params["mcpServers"][0])
        sessions[sid] = {"agent": mcp.agent, "pos": 0, "used": {}, "history": [], "mcp": mcp, "cancel": threading.Event()}
        save(sid)
        send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": sid, "_meta": {"tools": mcp.tools}}})
    elif method == "session/resume" and (SCRIPT.get("_cli") or {}).get("no_resume"):
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found: session/resume"}})
    elif method in ("session/load", "session/resume"):
        open_stored(mid, params, method == "session/load")
    elif method == "session/set_config_option":
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
    elif method == "session/prompt":
        sessions[params["sessionId"]]["cancel"].clear()
        text = "".join(p.get("text") or "" for p in params.get("prompt") or [])
        threading.Thread(target=run_prompt, args=(mid, params["sessionId"], text), daemon=True).start()
    elif method == "session/cancel":
        sessions[params["sessionId"]]["cancel"].set()
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "not here"}})
for s in sessions.values():
    if s.get("mcp"):
        s["mcp"].close()
time.sleep(0.1)
