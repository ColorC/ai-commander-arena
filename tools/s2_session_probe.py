"""Season 2 session continuity proof (materials/18 2.2): does session/load in a fresh CLI process continue the same
conversation? Deterministic, one CLI per run, no referee and no game.

    python -I -X utf8 tools/s2_session_probe.py --cli kimi|opencode --out $ARENA_DATA/runs/s2-infra-loadproof-<HHMMSS>/<cli>

Process 1: initialize, session/new, the phase settings, one prompt that gives a random code word (plus --pad-kb of
filler so the replay size can be measured), then the process is closed by its own PID. Process 2 (fresh, same cwd and
state dir): initialize, session/load, the settings again, one prompt asking for the word. With --resume and the CLI
offering session/resume, process 3 does the same with session/resume (no history replay). The CLI has only the
arena MCP tools (arena/harness/clis.py lockdown; the MCP server points at no referee, nothing is called).
Files in --out: transcript.jsonl (every ACP message both ways, per process), <cli>.stderr, mcp.jsonl, result.json
(answers, the load's time / number and bytes of session/update notifications, the CLI's own usage records), and the
player-style home it used (home/cwd, home/state).
"""
import argparse
import json
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arena.harness import acp, clis  # noqa: E402

SYSTEM = ("你是一个记忆测试里的被测者。只用中文简短回答，不要调用任何工具。\n"
          "每次回答只给对方要的内容，不加解释。\n")
WORDS = ("琥珀", "松针", "雷鸣", "青瓷", "铁砧", "白鹭", "珊瑚", "麦穗", "灯塔", "冰川", "铜铃", "苍鹰")


def filler(kb, rng):
    """Text the model is told to ignore: makes the history big enough to see how the replay scales."""
    out, n = [], 0
    while n < kb * 1024:
        line = "".join(rng.choice("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥") for _ in range(60))
        out.append(line)
        n += len(line.encode("utf-8")) + 1
    return "\n".join(out)


class Probe:
    def __init__(self, a):
        self.a, self.out = a, os.path.abspath(a.out)
        self.cli = clis.make(a.cli, a.model)
        self.home = os.path.join(self.out, "home")
        self.cwd, self.state = os.path.join(self.home, "cwd"), os.path.join(self.home, "state")
        for d in (self.cwd, self.state):
            os.makedirs(d, exist_ok=True)
        self.tr = open(os.path.join(self.out, "transcript.jsonl"), "a", encoding="utf-8")
        self.result = {"cli": self.cli.name, "model": self.cli.model, "cwd": self.cwd, "state": self.state, "steps": []}
        self.proc_n, self.updates = 0, []

    def log(self, **f):
        self.tr.write(json.dumps({"ts": round(time.time(), 3), "proc": self.proc_n, **f}, ensure_ascii=False) + "\n")
        self.tr.flush()

    def mcp(self):
        token = os.path.join(self.state, "dummy.token")
        clis.write_text(token, "not-a-referee-token\n")
        return acp.stdio_server(clis.MCP_NAME, sys.executable, [
            "-I", "-X", "utf8", os.path.join(ROOT, "arena", "harness", "mcp_server.py"), "--referee", "http://127.0.0.1:9",
            "--token-file", token, "--log", os.path.join(self.out, "mcp.jsonl"), "--agent", "commander"])

    def start(self):
        self.proc_n += 1
        self.text, self.updates = "", []

        def on_update(p):
            u = p.get("update") or {}
            self.updates.append(u.get("sessionUpdate"))
            if u.get("sessionUpdate") == "agent_message_chunk":
                self.text += (u.get("content") or {}).get("text") or ""

        def on_raw(d, m):
            self.log(dir=d, msg=m, bytes=len(json.dumps(m, ensure_ascii=False).encode("utf-8")))

        c = acp.AcpClient(self.cli.command(), self.cwd, self.cli.env(self.cwd, self.state), os.path.join(self.out, f"{self.cli.name}.stderr"),
                          on_update=on_update, on_permission=lambda p: None, on_raw=on_raw)
        c.start()
        t0 = time.monotonic()
        info = c.initialize()
        self.step("initialize", pid=c.pid, seconds=round(time.monotonic() - t0, 2), agent_info=info.get("agentInfo"),
                  load_session=(info.get("agentCapabilities") or {}).get("loadSession"),
                  session_capabilities=(info.get("agentCapabilities") or {}).get("sessionCapabilities"))
        return c

    def step(self, name, **f):
        row = {"step": name, "proc": self.proc_n, **f}
        self.result["steps"].append(row)
        self.log(step=row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    def settings(self, c, sid):
        for k, v in self.cli.settings():
            c.request("session/set_config_option", {"sessionId": sid, "configId": k, "value": v}, 60)

    def ask(self, c, sid, text, name):
        self.text = ""
        t0 = time.monotonic()
        r = c.request("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": text}]}, self.a.prompt_timeout)
        self.step(name, seconds=round(time.monotonic() - t0, 2), stop_reason=(r or {}).get("stopReason"), answer=self.text.strip())
        return self.text.strip()

    def close(self, c):
        kids = acp.child_pids(c.pid)
        code = c.close()
        time.sleep(1)
        left = [k for k in [c.pid] + kids if acp.pid_alive(k)]
        self.step("closed", pid=c.pid, children=kids, exit_code=code, still_running=left)

    def reopen(self, how):
        """A fresh process continues the session with session/load or session/resume. -> (client, seconds)"""
        c = self.start()
        mcps = [self.mcp()] if self.cli.mcp_in_session else []
        sent = len(self.result["steps"])
        n0 = os.path.getsize(os.path.join(self.out, "transcript.jsonl"))
        t0 = time.monotonic()
        fn = c.load_session if how == "load" else c.resume_session
        r = fn(self.sid, self.cwd, mcps)
        sec = round(time.monotonic() - t0, 2)
        kinds = {}
        for k in self.updates:
            kinds[k] = kinds.get(k, 0) + 1
        replay_bytes = 0
        with open(os.path.join(self.out, "transcript.jsonl"), "rb") as f:
            f.seek(n0)
            for line in f:
                row = json.loads(line)
                if row.get("dir") == "in" and (row.get("msg") or {}).get("method") == "session/update":
                    replay_bytes += row.get("bytes") or 0
        self.step(f"session_{how}", session_id=self.sid, seconds=sec, updates=len(self.updates), update_kinds=kinds,
                  update_bytes=replay_bytes, result_keys=sorted((r or {}).keys()), since_step=sent)
        self.settings(c, self.sid)
        return c

    def run(self):
        rng = random.Random()
        word = f"{rng.choice(WORDS)}{rng.randint(1000, 9999)}"
        self.result["code_word"] = word
        self.cli.prepare(self.cwd, self.state, SYSTEM, "commander", self.mcp())
        c = self.start()
        t0 = time.monotonic()
        s = c.new_session(self.cwd, [self.mcp()] if self.cli.mcp_in_session else [])
        self.sid = s["sessionId"]
        self.settings(c, self.sid)
        self.step("session_new", session_id=self.sid, seconds=round(time.monotonic() - t0, 2))
        time.sleep(getattr(self.cli, "settle_s", 0.0))
        pad = filler(self.a.pad_kb, rng) if self.a.pad_kb else ""
        self.ask(c, self.sid, f"请记住这个暗号：{word}。之后我会问你。现在只回复“记住了”。"
                              + (f"\n\n下面是填充文本，不用理会：\n{pad}" if pad else ""), "prompt_remember")
        self.close(c)

        c = self.reopen("load")
        time.sleep(getattr(self.cli, "settle_s", 0.0))
        ans = self.ask(c, self.sid, "我前面让你记住的暗号是什么？只回复暗号本身。", "prompt_recall_after_load")
        self.result["load_ok"] = word in ans
        self.close(c)

        caps = next((r.get("session_capabilities") for r in self.result["steps"] if r["step"] == "initialize"), None) or {}
        if self.a.resume and "resume" in caps:
            c = self.reopen("resume")
            time.sleep(getattr(self.cli, "settle_s", 0.0))
            ans = self.ask(c, self.sid, "再说一次那个暗号，并说出这是我第几次问你。只回复“暗号，第N次”。", "prompt_recall_after_resume")
            self.result["resume_ok"] = word in ans
            self.close(c)

        recs, _ = self.cli.usage(self.sid, 0)
        self.result["usage"] = recs
        self.result["session_id"] = self.sid
        with open(os.path.join(self.out, "result.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.result, f, ensure_ascii=False, indent=1)
        self.tr.close()
        print(json.dumps({"load_ok": self.result["load_ok"], "resume_ok": self.result.get("resume_ok"), "session_id": self.sid,
                          "result": os.path.join(self.out, "result.json")}, ensure_ascii=False))
        return 0 if self.result["load_ok"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cli", required=True, choices=("kimi", "opencode"))
    ap.add_argument("--model")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pad-kb", type=int, default=24, help="filler in the first prompt (KB), to size the replay")
    ap.add_argument("--resume", action="store_true", help="also try session/resume when the CLI offers it")
    ap.add_argument("--prompt-timeout", type=float, default=600)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    return Probe(a).run()


if __name__ == "__main__":
    sys.exit(main())
