"""How each official CLI is started as an ACP server and locked down to the referee's MCP tools.

Both CLIs run in a working directory per side (<run>/cli/side<N>/) with everything set for this process only:
nothing under ~/.kimi-code or ~/.config/opencode is changed, no credential is copied, nothing is refreshed here.
prepare() writes the per-process configuration once the system prompt is known (first wake), env() gives the
environment for the CLI process (KIMI_API_KEY and the variables named in ARENA_DROP_ENV removed).

Kimi Code CLI (`kimi acp`): the session's default agent profile "agent" is overridden by a workspace agent file
<cwd>/.kimi-code/agents/agent.md (override: true) whose tool allowlist is `mcp__arena__*` and whose body is the arena
system prompt, so the stock prompt (coding instructions, user AGENTS.md, skills, plugin sections, directory listing)
is not used. The MCP server is given per session in session/new. Model k3.1-pro and the thinking effort ("max" from
round 4 of 11-04, "high" before; ARENA_KIMI_THINKING overrides) are set per session with session/set_config_option. KIMI_CODE_NO_AUTO_UPDATE keeps the CLI from updating itself mid-match.
Checked 10-30: the wire file's llm.tools_snapshot lists only the 24 mcp__arena__ tools, llm.request has
thinkingEffort "high", profile.bind carries exactly the arena prompt.

opencode (`opencode acp`, which starts a private `opencode serve --stdio` as its child): OPENCODE_CONFIG_DIR points
at <run>/agents/side<N>/opencode-config/, which replaces the global config directory for this process (the global
config's provider policy cannot be overridden from any later config document: policies are read newest document
first and the first document's `deny *` always matches last). Its opencode.json: the model, the "proxy" provider
(an OpenAI-compatible endpoint; base URL and key by reference to ARENA_PROXY_BASE_URL / ARENA_PROXY_API_KEY, not copied), an "arena" primary agent with the arena system prompt, the
arena MCP server in the v2 shape mcp.servers with codemode false (its tools go to the model directly as arena_<tool>;
a legacy mcp.<name> entry loses the codemode flag and the tools end up behind code mode's `execute`), and every
built-in tool denied by name. Login data stays in opencode's data directory (shared, read by the CLI as usual).
Checked 10-30 against a fake OpenAI-compatible endpoint: the request carries exactly the 26 arena_ tools, and the
system message is the arena prompt plus opencode's model/date/working-directory lines.
"""
import json
import os
import shutil
import sqlite3
from glob import glob

HOME = os.path.expanduser("~")
APPDATA = os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming")
MCP_NAME = "arena"
BUILTIN_DENY = ("execute", "shell", "bash", "read", "write", "edit", "patch", "apply_patch", "multiedit", "glob", "grep", "list", "lsp",
                "webfetch", "websearch", "codesearch", "subagent", "task", "skill", "question", "todowrite", "todoread", "plan_enter",
                "plan_exit", "external_directory", "doom_loop")
# KIMI_API_KEY gets 403 on Kimi Code (the CLI's own login is used); ARENA_DROP_ENV (comma-separated) names more
# variables to keep away from the CLIs, e.g. a host terminal's session id that a child CLI would otherwise rebind
DROP_ENV = ("KIMI_API_KEY", *[v for v in os.environ.get("ARENA_DROP_ENV", "").split(",") if v])
# `arena turn end` blocks until the side's next wake and returns it as the tool result (r5-minden 11-04: a wake sent as
# a new user prompt framed every wake as "order and end"). The CLI must let one MCP tool call run that long: the tool
# timeout below is set per process for both CLIs, and the driver gives up a block a little before it (BLOCK_SEC) with
# a neutral "游戏还在进行" result that asks for turn end again. Between two own Orders phases the block lasts the other
# side's turn and one game cycle; after the prematch at most the other side's prematch.
TOOL_TIMEOUT_MS = 6 * 3600 * 1000
BLOCK_SEC = TOOL_TIMEOUT_MS / 1000 - 15 * 60


def base_env(extra=None):
    env = {k: v for k, v in os.environ.items() if k not in DROP_ENV}
    env.update(extra or {})
    return env


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class Kimi:
    name = "kimi"
    tool_prefix = "mcp__arena__"
    default_model = "kimi-code/k3.1-pro"
    thinking = os.environ.get("ARENA_KIMI_THINKING") or "max"

    def __init__(self, model=None, main_js=None, node=None):
        self.main_js = main_js or os.environ.get("ARENA_KIMI_MAIN") or os.path.join(
            APPDATA, "npm", "node_modules", "@moonshot-ai", "kimi-code", "dist", "main.mjs")
        self.node = node or shutil.which("node") or "node"
        self.model = model or self.default_model

    def command(self):
        return [self.node, self.main_js, "acp"]

    # Season 2 (materials/18 2.2): one `kimi acp` per agent (commander and each subagent), as opencode. The MCP server
    # is given per session, in session/new and in session/load.
    shared_process = False
    mcp_in_session = True
    settle_s = 0.0         # the MCP tools are in the session once the server has listed them

    def prepare(self, cwd, state_dir, system_text, agent="commander", mcp=None):
        """Workspace agent file overriding the default profile for sessions in this cwd (the same for every agent)."""
        front = ("---\nname: agent\ndescription: Arena commander (referee tools only)\noverride: true\n"
                 f"tools:\n  - \"{self.tool_prefix}*\"\nsubagents: []\n---\n\n")
        write_text(os.path.join(cwd, ".kimi-code", "agents", "agent.md"), front + system_text.strip() + "\n")

    block_sec = BLOCK_SEC

    def env(self, cwd, state_dir, agent="commander"):
        # kimi-code main.mjs (checked 11-04): KIMI_MCP_TOOL_TIMEOUT_MS is the mcp section's toolTimeoutMs (an integer
        # 1..2147483647 ms, passed to the MCP client as each tools/call's timeout; without it the MCP SDK's 60 s
        # default); KIMI_LOOP_MAX_STEPS_PER_TURN is loopControl.maxStepsPerTurn, and every check is `maxSteps > 0 &&
        # step >= maxSteps`, so 0 = no cap: a whole game of wakes can run in one prompt turn. Per process only.
        return base_env({"KIMI_CODE_NO_AUTO_UPDATE": "1", "KIMI_CLI_NO_AUTO_UPDATE": "1",
                         "KIMI_MCP_TOOL_TIMEOUT_MS": str(TOOL_TIMEOUT_MS), "KIMI_LOOP_MAX_STEPS_PER_TURN": "0"})

    def settings(self):
        return [("model", self.model), ("thinking", self.thinking)]

    @staticmethod
    def wire_path(session_id):
        hits = glob(os.path.join(HOME, ".kimi-code", "sessions", "*", session_id, "agents", "main", "wire.jsonl"))
        return hits[0] if hits else None

    def usage(self, session_id, since=0):
        """Per-call usage the CLI writes to its session wire file (read only). -> (records, next offset)"""
        path = self.wire_path(session_id)
        if not path:
            return [], since
        with open(path, "rb") as f:
            f.seek(since)
            data = f.read()
        end = data.rfind(b"\n") + 1
        out, req = [], {}
        for line in data[:end].splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") == "llm.request":
                req = r
            elif r.get("type") == "usage.record":
                u = r.get("usage") or {}
                out.append({"time": r.get("time"), "model": req.get("model") or r.get("model"), "effort": req.get("thinkingEffort"),
                            "input": sum(u.get(k) or 0 for k in ("inputOther", "inputCacheRead", "inputCacheCreation")),
                            "output": u.get("output"), "cache_read": u.get("inputCacheRead"), "cache_write": u.get("inputCacheCreation")})
            elif r.get("type") == "llm.tools_snapshot":
                out.append({"time": r.get("time"), "tools_snapshot": [t.get("name") for t in r.get("tools") or []]})
        return out, since + end


class OpenCode:
    name = "opencode"
    tool_prefix = "arena_"
    # opencode-go was the first choice; its month quota ran out 10-30, so the OpenAI-compatible proxy is the default.
    # opencode-go/deepseek-v4.1-flash still works with --model
    default_model = "proxy/" + os.environ.get("ARENA_PROXY_MODEL", "deepseek-v4.1-flash")

    def __init__(self, model=None, exe=None):
        self.exe = exe or os.environ.get("ARENA_OPENCODE_EXE") or os.path.join(
            APPDATA, "npm", "node_modules", "@opencode", "cli", "bin", "opencode.exe")
        self.model = model or self.default_model

    def command(self):
        return [self.exe, "acp"]

    # One `opencode acp` per agent: an MCP server handed over in session/new is put behind opencode's code-mode
    # `execute` tool, and only a server defined in the config with codemode false exposes its tools directly (as
    # arena_<tool>). A config-defined server belongs to the process, so each agent gets its own config and process.
    shared_process = False
    mcp_in_session = False  # the arena server is in the per-process config (written for the phase before the start)
    # opencode adds the listed MCP tools to its registry a moment after tools/list (checked 10-30: a prompt sent right
    # after the list went to the model with no tools at all; 2 s and more later it had all 26)
    settle_s = 3.0
    block_sec = BLOCK_SEC

    @staticmethod
    def config_dir(state_dir, agent="commander"):
        return os.path.join(state_dir, "opencode-config" if agent == "commander" else f"opencode-config-{agent}")

    def config(self, system_text, mcp=None):
        # every built-in tool of opencode 2.0.32 (its opencode.tool.* plugins, plus `execute` of code mode and the
        # older names) is denied by name, which removes it from the tool list; a bare "*": "deny" would remove the
        # MCP tools as well (their permission key is not their exposed name)
        only_arena = {**{t: "deny" for t in BUILTIN_DENY}, f"{self.tool_prefix}*": "allow"}
        return {
            "$schema": "https://opencode.ai/config.json",
            "model": self.model,
            "small_model": self.model,
            "default_agent": "arena",
            "share": "disabled",
            "autoupdate": False,
            "instructions": [],
            "enabled_providers": ["opencode-go", "proxy"],
            "provider": {"opencode-go": {}, "proxy": {
                "npm": "@ai-sdk/openai", "name": "OpenAI-compatible proxy",
                "options": {"baseURL": "{env:ARENA_PROXY_BASE_URL}", "apiKey": "{env:ARENA_PROXY_API_KEY}"},
                "models": {os.environ.get("ARENA_PROXY_MODEL", "deepseek-v4.1-flash"): {"name": "DeepSeek V4.1 Flash", "tool_call": True, "reasoning": True,
                                                   "limit": {"context": 1000000, "output": 64000}}}}},
            "permission": only_arena,
            # the v2 shape (mcp.servers): a legacy top-level mcp.<name> entry is translated without its codemode flag.
            # timeout is Mcp.TimeoutConfig {startup, catalog, execution} in ms (opencode.exe 2.0.32, checked 11-04:
            # callTool gets `timeout: server.timeout?.execution ?? 43200000`); set so the blocking turn end is ours.
            # The agent has no `steps`: the loop stops only on `agent.steps !== undefined && step >= agent.steps`.
            "mcp": {"servers": {MCP_NAME: {"type": "local", "command": [mcp["command"], *mcp["args"]],
                                           "environment": {e["name"]: e["value"] for e in mcp["env"]}, "codemode": False,
                                           "timeout": {"execution": TOOL_TIMEOUT_MS}}}} if mcp else {},
            "agent": {"arena": {"mode": "primary", "description": "Arena commander (referee tools only)", "model": self.model,
                                "prompt": system_text.strip(), "permission": only_arena}},
        }

    def prepare(self, cwd, state_dir, system_text, agent="commander", mcp=None):
        write_text(os.path.join(self.config_dir(state_dir, agent), "opencode.json"),
                   json.dumps(self.config(system_text, mcp), ensure_ascii=False, indent=1) + "\n")

    def env(self, cwd, state_dir, agent="commander"):
        return base_env({"OPENCODE_CONFIG_DIR": self.config_dir(state_dir, agent), "OPENCODE_DISABLE_CLAUDE_CODE": "1",
                         "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1",
                         "OPENCODE_DISABLE_PROJECT_CONFIG": "1"})

    def settings(self):
        return [("mode", "arena"), ("model", self.model)]

    @staticmethod
    def db_path():
        return os.path.join(HOME, ".local", "share", "opencode", "opencode.db")

    def usage(self, session_id, since=0):
        """Per-call usage from opencode's own database (read only). -> (records, last time_updated seen)"""
        try:
            con = sqlite3.connect("file:" + self.db_path() + "?mode=ro", uri=True, timeout=10)
            try:
                rows = con.execute("select id, time_updated, data from session_message where session_id = ? and type = 'assistant' "
                                   "and time_updated > ? order by time_updated", (session_id, since)).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            return [], since
        out = []
        for mid, t, data in rows:
            try:
                d = json.loads(data)
            except (TypeError, ValueError):
                continue
            tok = d.get("tokens")
            if not isinstance(tok, dict) or not (d.get("time") or {}).get("completed"):
                continue
            cache, m = tok.get("cache") or {}, d.get("model") if isinstance(d.get("model"), dict) else {}
            out.append({"time": t, "id": mid, "model": f"{m.get('providerID')}/{m.get('id')}", "finish": d.get("finish"),
                        "input": tok.get("input"), "output": tok.get("output"), "reasoning": tok.get("reasoning"),
                        "cache_read": cache.get("read"), "cache_write": cache.get("write"), "cost": d.get("cost")})
            since = max(since, t)
        return out, since


class Fake:
    """Dry runs and tests: arena/tests/fake_acp_cli.py, an ACP server whose "model" is a script (no provider)."""
    name = "fake"
    tool_prefix = ""
    default_model = "fake/scripted"
    shared_process, mcp_in_session, settle_s = False, True, 0.0
    block_sec = BLOCK_SEC          # fake_acp_cli.py waits for a tool call without a timeout

    def __init__(self, model=None, script=None):
        self.model, self.script = model or self.default_model, os.path.abspath(script) if script else None
        self.main = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fake_acp_cli.py")

    def command(self):
        import sys
        return [sys.executable, "-I", "-X", "utf8", self.main, self.script]

    def prepare(self, cwd, state_dir, system_text, agent="commander", mcp=None):
        write_text(os.path.join(cwd, "system.txt"), system_text)

    def env(self, cwd, state_dir, agent="commander"):
        return base_env()

    def settings(self):
        return [("model", self.model)]

    def usage(self, session_id, since=0):
        return [], since


def make(name, model=None, script=None):
    if name == "kimi":
        return Kimi(model)
    if name == "opencode":
        return OpenCode(model)
    if name == "fake":
        return Fake(model, script)
    raise SystemExit(f"unknown cli {name!r}: kimi, opencode or fake")
