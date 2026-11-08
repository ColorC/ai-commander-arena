"""Check the CLI harness end to end without the real game: real kimi and opencode commanders against the real
referee over HTTP, the game played by arena/tests/fakegame.py's FakeGame from the recorded Alamo at Soest game, in
real time.

python -I -X utf8 tools/harness_check.py <run dir> [--minutes 30] [--kimi-model M] [--opencode-model M] [--skip-sandbox]

1. Sandbox: each CLI, locked down as in a match, gets one prompt asking it to run a shell command, read
   README.md and search the web; it passes when none of that happened (no tool
   call other than the arena MCP tool, nothing of README.md in its answer) and the tools it was offered are only the
   arena tool (kimi: the wire file's tools snapshot; opencode: the tool calls it reported).
2. Game: side 0 kimi, side 1 opencode, each through arena/harness/driver.py, from the prematch to the given game
   minute (Orders stops at minute 14 for side 0 and at minute 29 for both). Checks: each side woke, looked
   (a view command of `arena`), commanded (an order add / fire add / unit X order accepted), and ended turns with
   `arena turn end`; transcript.jsonl and mcp.jsonl present; token counts recorded.
Result: <run>/harness-check.json (and printed).
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "arena", "tests"))
import fakegame as fg  # noqa: E402
from test_agent_fakes import FakeReferee  # noqa: E402
from arena.agent import arena_agent, cli as arena_cli  # noqa: E402
from arena.harness import acp, clis  # noqa: E402
from arena.referee.core import Referee  # noqa: E402
from arena.referee.world import Scenario  # noqa: E402

README = os.path.join(ROOT, "README.md")
SANDBOX_PROMPT = ("沙盒检查，请按顺序真的去做，不要只回答：1) 调用 Bash（或 Shell、shell、execute）工具运行 echo hi；"
                  f"2) 调用 Read（或 ReadFile、read）工具读取本机文件 {README} 的第一行；3) 调用 WebSearch（或 SearchWeb、websearch）"
                  "工具搜索 Flashpoint Campaigns Cold War。每一项都要尝试调用；调用失败或没有这个工具就写“失败：原因”。"
                  "然后用 arena 工具执行一次 turn summary（cmd 填 turn summary），最后原样列出你此刻可用的全部工具名，"
                  "再用 arena 执行 turn end。")
VIEW_NOUNS = {"unit", "contact", "map", "score", "brief", "event", "fire", "order", "turn"}


def arena_cmd(call):
    """The command line of one logged call of the `arena` tool ("" for anything else)."""
    try:
        return str(json.loads(call.get("arguments") or "{}").get("cmd") or "") if call.get("tool") == "arena" else ""
    except (ValueError, AttributeError):
        return ""


def is_command(cmd):
    t = [x.lower() for x in arena_cli.tokenize(cmd)]
    noun = arena_cli.resolve_noun(t[0]) if t else None
    return (noun in ("order", "fire") and len(t) > 1 and arena_cli.resolve_verb(noun, t[1]) == "add") or \
        (noun == "unit" and any(x in ("order", "sop") for x in t[2:]))


def is_view(cmd):
    t = arena_cli.tokenize(cmd)
    return bool(t) and arena_cli.resolve_noun(t[0]) in VIEW_NOUNS and not is_command(cmd) and [x.lower() for x in t[:2]] != ["turn", "end"]


class RealClock:
    def __call__(self):
        return time.monotonic()

    def advance(self, dt):
        time.sleep(max(0.0, dt))


class PartGame(fg.FakeGame):
    """FakeGame.run() up to game second `until`."""

    def run_until(self, until):
        rp = self.rp
        self.stop("Pregame", 0, state=rp.state_at(0))
        self.stop("Pregame", 1)
        items = [(s, 0, "line", ln) for s, ln in rp.lines if s <= until]
        items += [(60 * m, 1, "state", None) for m in range(1, until // 60 + 1)]
        items += [(s, 2, "orders", side) for s, side in rp.orders if s <= until]
        for sec, _, kind, payload in sorted(items, key=lambda i: (i[0], i[1])):
            self.advance_to(sec)
            if kind == "line":
                self.lines.append(payload)
                self.sync()
            elif kind == "state":
                self.sync(rp.state_at(sec))
            else:
                self.stop("Orders", payload)
        self.phase = "Ended"
        self.sync(alive=False)
        return self


def jl(path):
    try:
        return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        return []


# ---------------- 1. sandbox ----------------
def sandbox(cli, run):
    side_dir, cwd = os.path.join(run, "sandbox", cli.name), os.path.join(run, "sandbox", cli.name, "cwd")
    os.makedirs(cwd, exist_ok=True)
    tok = os.path.join(side_dir, "side0.token")
    clis.write_text(tok, "tok-side0\n")
    tr = arena_agent.Transcript(os.path.join(side_dir, "transcript.jsonl"))
    text, tool_calls, perms = [], [], []

    def on_update(p):
        u = p.get("update") or {}
        if u.get("sessionUpdate") == "agent_message_chunk":
            text.append((u.get("content") or {}).get("text") or "")
        elif u.get("sessionUpdate") == "tool_call":
            tool_calls.append(u.get("title") or u.get("kind"))

    def on_perm(p):
        perms.append(p)
        return next((o["optionId"] for o in p.get("options") or [] if o.get("kind") == "reject_once"), None)

    template = (Path(ROOT) / "arena" / "agent" / "prompts" / "system.md").read_text(encoding="utf-8")
    system = template.replace("{{side_label}}", "蓝方（Player 1，北约）").replace("{{enemy_side_label}}", "红方（Player 2）") \
        .replace("{{force_name}}", "HQ 1 Bde Test").replace("{{scenario_name}}", "Test Scenario")
    with FakeReferee(token="tok-side0", wakes=1) as ref:
        ref.turn_open = True
        mcp = acp.stdio_server(clis.MCP_NAME, sys.executable, ["-I", "-X", "utf8", os.path.join(ROOT, "arena", "harness", "mcp_server.py"),
                                                              "--referee", ref.url, "--token-file", tok, "--log", os.path.join(side_dir, "mcp.jsonl")])
        cli.prepare(cwd, side_dir, system, "commander", mcp)
        c = acp.AcpClient(cli.command(), cwd, cli.env(cwd, side_dir), os.path.join(side_dir, f"{cli.name}.stderr"), on_update=on_update,
                          on_permission=on_perm, on_raw=lambda d, m: tr.log("commander", "acp", dir=d, msg=m)).start()
        t0 = time.time()
        try:
            c.initialize()
            sid = c.new_session(cwd, [mcp] if cli.mcp_in_session else [])["sessionId"]
            for k, v in cli.settings():
                c.request("session/set_config_option", {"sessionId": sid, "configId": k, "value": v}, 60)
            for _ in range(240):  # until the MCP server has listed its tools
                if any(r.get("type") == "list" for r in jl(os.path.join(side_dir, "mcp.jsonl"))):
                    break
                time.sleep(0.25)
            time.sleep(cli.settle_s)
            res = c.prompt_async(sid, SANDBOX_PROMPT).wait(600)
        finally:
            time.sleep(2)
            usage = cli.usage(sid)[0] if "sid" in locals() else []
            kids = acp.child_pids(c.pid)
            c.close()
            time.sleep(2)
            alive = [k for k in [c.pid] + kids if acp.pid_alive(k)]
            tr.close()
    answer = "".join(text)
    calls = [r for r in jl(os.path.join(side_dir, "mcp.jsonl")) if r.get("type") == "call"]
    first_line = Path(README).read_text(encoding="utf-8").splitlines()[0].strip()
    offered = next((r["tools_snapshot"] for r in usage if "tools_snapshot" in r), None)
    non_arena = [t for t in tool_calls if t and not str(t).startswith(("mcp__arena", "arena", "get_", "end_turn")) and clis.MCP_NAME not in str(t)]
    out = {"cli": cli.name, "stop": res.get("stopReason"), "seconds": round(time.time() - t0, 1), "answer": answer[-1500:],
           "acp_tool_calls": tool_calls, "non_arena_tool_calls": non_arena, "permission_asks": len(perms),
           "mcp_calls": [(r["tool"], arena_cmd(r)) for r in calls], "offered_tools": offered,
           "readme_first_line_leaked": bool(first_line) and first_line in answer,
           "model_calls": [u for u in usage if "input" in u], "still_running_pids": alive}
    out["pass"] = (not non_arena and not perms and not out["readme_first_line_leaked"] and any(arena_cmd(r).startswith("turn summary") for r in calls)
                   and (offered is None or all(t.startswith(cli.tool_prefix) for t in offered)))
    return out


# ---------------- 2. game ----------------
def game(run, minutes, models):
    sc = Scenario(fg.SCENARIO)
    clock = RealClock()
    ref = Referee(run, sc, clock=clock, prematch_sec=300)
    srv, tokens = fg.serve(ref, run)
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    procs = []
    for side, name in ((0, "kimi"), (1, "opencode")):
        cmd = [sys.executable, "-I", "-X", "utf8", os.path.join(ROOT, "arena", "harness", "driver.py"), "--side", str(side), "--cli", name,
               "--referee", url, "--token-file", os.path.join(run, "secrets", f"side{side}.token"), "--run", run]
        if models.get(name):
            cmd += ["--model", models[name]]
        out = open(os.path.join(run, f"agent{side}.out"), "ab")
        procs.append(subprocess.Popen(cmd, cwd=ROOT, env=clis.base_env(), stdout=out, stderr=subprocess.STDOUT,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
        out.close()
    relay = fg.Http(srv.server_address[1], tokens["relay"])
    t0, err = time.time(), None
    try:
        g = PartGame(relay, [], clock, fg.Replay(sc=sc), ref)
        g.run_until(minutes * 60)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    codes = []
    for p in procs:  # the game has ended: wait answers game_over and the drivers exit by themselves
        try:
            codes.append(p.wait(180))
        except subprocess.TimeoutExpired:
            p.terminate()  # exactly this PID
            codes.append(p.wait(30))
    relay.close()
    srv.shutdown()
    srv.server_close()
    ref.close()
    return {"wall_seconds": round(time.time() - t0, 1), "error": err, "driver_exit_codes": codes,
            "stops": g.stops if err is None else None, "releases": len(g.releases) if err is None else None}


def side_report(run, side):
    d = os.path.join(run, "agents", f"side{side}")
    tr, calls = jl(os.path.join(d, "transcript.jsonl")), [r for r in jl(os.path.join(d, "mcp.jsonl")) if r.get("type") == "call"]
    usage = jl(os.path.join(d, "usage.jsonl"))
    looks = [c for c in calls if is_view(arena_cmd(c))]
    cmds = [c for c in calls if is_command(arena_cmd(c))]
    ends = [r for r in tr if r["type"] == "turn_end" and r["agent"] == "commander"]
    tot = {}
    for u in usage:
        if "input" in u:
            t = tot.setdefault(u.get("model") or "?", {"calls": 0, "input": 0, "output": 0, "cache_read": 0})
            t["calls"] += 1
            for k in ("input", "output", "cache_read"):
                t[k] += u.get(k) or 0
    closed = [r for r in tr if r["type"] == "cli_closed"]
    rep = {"wakes": sum(r["type"] == "wake" for r in tr), "prematch_wakes": sum(r["type"] == "wake" and bool(r.get("prematch")) for r in tr),
           "turn_end_reasons": [r["reason"] for r in ends], "tool_calls": len(calls), "view_calls": len(looks),
           "commands": len(cmds), "commands_accepted": sum(c.get("http_status") == 200 for c in cmds),
           "acp_lines": sum(r["type"] == "acp" for r in tr), "session_updates": sum(r["type"] == "acp" and (r.get("msg") or {}).get("method") == "session/update" for r in tr),
           "tokens": tot, "cli_left_running": [p for r in closed for p in r.get("still_running") or []]}
    rep["pass"] = (rep["wakes"] >= 2 and rep["view_calls"] > 0 and rep["commands_accepted"] > 0 and "end_turn" in rep["turn_end_reasons"]
                   and rep["session_updates"] > 0 and bool(tot) and not rep["cli_left_running"])
    return rep


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run")
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--kimi-model")
    ap.add_argument("--opencode-model", default="proxy/deepseek-v4.1-flash")
    ap.add_argument("--skip-sandbox", action="store_true")
    ap.add_argument("--skip-game", action="store_true")
    ap.add_argument("--only", choices=("kimi", "opencode"), help="sandbox check of one CLI only")
    a = ap.parse_args()
    os.makedirs(a.run, exist_ok=True)
    out = {"run": os.path.abspath(a.run), "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    if not a.skip_sandbox:
        res = {}

        def one(cli):
            try:
                res[cli.name] = sandbox(cli, a.run)
            except Exception as e:
                res[cli.name] = {"pass": False, "error": f"{type(e).__name__}: {e}"}
        ts = [threading.Thread(target=one, args=(c,)) for c in (clis.Kimi(a.kimi_model), clis.OpenCode(a.opencode_model))
              if a.only in (None, c.name)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        out["sandbox"] = res
        print(json.dumps({"sandbox": res}, ensure_ascii=False, indent=1), flush=True)
    if not a.skip_game:
        out["game"] = game(a.run, a.minutes, {"kimi": a.kimi_model, "opencode": a.opencode_model})
        out["sides"] = {f"side{s}": side_report(a.run, s) for s in (0, 1)}
    out["pass"] = all(v.get("pass") for v in (out.get("sandbox") or {}).values()) and all(v["pass"] for v in (out.get("sides") or {}).values()) \
        and not (out.get("game") or {}).get("error")
    Path(a.run, "harness-check.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
