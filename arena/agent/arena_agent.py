"""arena-agent: one model plays one side's commander through the referee's HTTP interface (arena/DESIGN.md section 6).

python -I arena/agent/arena_agent.py --side N --route <route> --referee http://127.0.0.1:41010 --token-file <path> --run <run dir>
Loop: wait for a wake-up -> one turn (model <-> the `arena` tool until `turn end` or the turn limit) -> turn/end -> wait again.
The model has one tool, `arena` (arena/agent/cli.py: cmd = a command line, input = multi-line text); `turn end`,
`agent spawn` and `agent check` come back from the command line as requests this loop carries out itself.
Subagents are extra conversations in threads of this process (at most 2 at once, 10 minutes per task, no sub-subagents).
Every request, reply, tool call and usage record goes to <run>/agents/side<N>/transcript.jsonl.
Exit codes: 0 game over / turn cap reached, 2 referee unreachable too long, 3 token rejected.
"""
import argparse
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
from arena.agent import cli, client, routes, tools  # noqa: E402

PLACEHOLDER = "[对话已裁剪]"
SUB_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")  # the referee's rule for subagent names
SIDES = {0: ("蓝方（Player 1，北约）", "红方（Player 2）"), 1: ("红方（Player 2）", "蓝方（Player 1，北约）")}
PLAYERS = {0: "参赛方 A", 1: "参赛方 B"}
NUDGE = "你没有调用工具。结束回合用 arena 工具执行 turn end；否则继续用 arena 查看或下令（不会写就 arena help）。"
OBSERVE_NOTE = "\n（观察中：不调用任何工具的回复 = 等待下一次更新）"
# no time limit in the text (rule of 11-04: no durations or caps in what a player reads; the referee ends a task that
# runs too long on its own clock)
SUB_FIRST = ("你是子代理“{name}”，由指挥官派出，只做下面这一项任务；你不能再派子代理。完成后用 arena 工具执行 "
             "turn end --report \"报告\"（长报告放进 input）交结果，报告要简明、自包含。\n\n任务：\n{task}")


def exercise_line(ex, enemy):
    """The opponent line of the system prompt in a training-period exercise (the briefing's `exercise` block)."""
    # season 2 (materials/18 2.2): the other side of an exercise against oneself is the sparring partner
    who = "你的陪练（同一模型，但独立的进程、工作区和记忆；它读不到你的笔记，你也读不到它的）" if ex.get("opponent") == "self" else "游戏内置 AI（电脑）"
    out = f"对手是{who}指挥的{enemy}。这是训练期的演习，不计分，用来检验你的计划、命令写法和脚本。"
    if ex.get("pacing") == "turn":
        out += "本场是回合制：每个本方下令阶段游戏停下等你下完命令。"
    small = ex.get("small_experiment")
    if isinstance(small, dict):
        out += (f"本场是小规模实验：你只能指挥这些单位：{'、'.join(small.get('units') or [])}，其余单位原地待命；"
                f"按你申请的长度，打 {small.get('game_minutes')} 游戏分钟结束。")
    return out


def est_tokens(s):
    """Rough, on the high side: ASCII ~3.5 chars/token, everything else ~1.2 tokens/char."""
    a = sum(1 for c in s if ord(c) < 128)
    return int(a / 3.5 + (len(s) - a) * 1.2)


def msg_tokens(m):
    n = 8 + est_tokens(m.get("content") or "") + est_tokens(m.get("reasoning_content") or "")
    return n + sum(est_tokens(c["function"]["name"] + c["function"]["arguments"]) + 8 for c in m.get("tool_calls") or [])


class Transcript:
    def __init__(self, path, now=time.time):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f, self.lock, self.now = open(path, "a", encoding="utf-8"), threading.Lock(), now

    def log(self, agent, type_, **fields):
        line = json.dumps({"ts": round(self.now(), 3), "agent": agent, "type": type_, **fields}, ensure_ascii=False, default=str)
        with self.lock:
            self.f.write(line + "\n")
            self.f.flush()

    def close(self):
        with self.lock:
            self.f.close()


class Conversation:
    """Message history for one agent. trim() keeps the system prompt and the most recent messages, shortens old tool results,
    drops whole old (assistant + tool results) units, and leaves one placeholder carrying the latest notes the agent wrote."""

    def __init__(self, system, budget, schema_tokens, keep_recent=10):
        self.system, self.budget, self.keep_recent = system, budget, keep_recent
        self.fixed, self.messages, self.dropped, self.notes, self.scale, self._raw = est_tokens(system) + schema_tokens, [], 0, {}, 1.0, 0

    def add(self, m):
        if m["role"] == "assistant":
            m = {k: v for k, v in m.items() if k in ("role", "content", "tool_calls", "reasoning_content")}
        elif m["role"] == "user":  # a new wake-up: reasoning of earlier turns is no longer needed
            for old in self.messages:
                old.pop("reasoning_content", None)
        self.messages.append(m)

    def request(self):
        self._raw = self.fixed + sum(msg_tokens(m) for m in self.messages)
        return [{"role": "system", "content": self.system}] + self.messages

    def calibrate(self, prompt_tokens):
        if prompt_tokens and self._raw:
            self.scale = min(2.0, max(0.5, prompt_tokens / self._raw))

    def total(self):
        return (self.fixed + sum(msg_tokens(m) for m in self.messages)) * self.scale

    def _placeholder(self):
        return bool(self.messages) and (self.messages[0].get("content") or "").startswith(PLACEHOLDER)

    def trim(self):
        if self.total() <= self.budget:
            return None
        before, cut = self.total(), lambda: len(self.messages) - self.keep_recent
        for m in self.messages[:max(0, cut())]:
            m.pop("reasoning_content", None)
            if m["role"] == "tool" and len(m["content"]) > 600:
                m["content"] = m["content"][:600] + "…[旧结果已缩短]"
        n = 0
        while self.total() > self.budget:
            s = 1 if self._placeholder() else 0
            e = s + 1
            while e < len(self.messages) and self.messages[s].get("tool_calls") and self.messages[e]["role"] == "tool":
                e += 1
            if e > cut():
                break
            del self.messages[s:e]
            n += e - s
        for m in self.messages[-self.keep_recent:]:  # last resort: even recent results get shortened
            if self.total() > self.budget and m["role"] == "tool" and len(m["content"]) > 2000:
                m["content"] = m["content"][:2000] + "…[已缩短]"
        if n:
            self.dropped += n
            digest = "\n".join(f"- {p}: {c[:1200]}" for p, c in list(self.notes.items())[-6:])
            note = {"role": "user", "content": f"{PLACEHOLDER} 较早的 {self.dropped} 条消息已省略以控制长度。你最近写下的笔记要点：\n{digest or '（本进程内没写过笔记）'}\n完整内容用 arena note show <路径> 读取。"}
            if self._placeholder():
                self.messages[0] = note
            else:
                self.messages.insert(0, note)
        return {"dropped": n, "tokens_before": int(before), "tokens_after": int(self.total())}


class Worker:
    """One conversation with its own turn: the commander (persistent) or a subagent (one task)."""

    def __init__(self, name, sub=False):
        self.name, self.sub, self.conv, self.turn_id, self.deadline = name, sub, None, None, 0.0
        self.running, self.t0, self.requests, self.tool_calls, self.end_failures, self.report = False, 0.0, 0, 0, 0, ""
        self.open = False                # the commander's turn is open (a subagent uses `running`)


class Agent:
    def __init__(self, side, chat, referee, library, transcript, system_template, route, turn_limit=480, sub_limit=600, max_subs=2,
                 wait_timeout=60, budget=96000, max_turns=0, now=time.time, sleep=time.sleep):
        self.side, self.chat, self.referee, self.library = side, chat, referee, library
        self.tr, self.template, self.route, self.now, self.sleep = transcript, system_template, route, now, sleep
        self.turn_limit, self.sub_limit, self.max_subs, self.wait_timeout, self.budget, self.max_turns = turn_limit, sub_limit, max_subs, wait_timeout, budget, max_turns
        self.cv, self.inbox, self.subs, self.stop, self.system = threading.Condition(), [], {}, threading.Event(), None
        self.cmd = Worker("commander")
        self.cmd.cli = cli.Cli(referee, library)
        self.exit_code, self.observing = 0, False
        self.think_base, self.think_at, self.prematch = 0.0, 0.0, False
        self.wake_n, self.training = 0, False      # wakes so far (a driver also counts the ones a turn end returns)

    # ---- main loop -------------------------------------------------------------------------------------------------
    def run(self):
        self.tr.log("-", "start", side=self.side, route=self.route.name, model=self.route.model, extra=self.route.extra,
                    turn_limit=self.turn_limit, sub_limit=self.sub_limit, max_subs=self.max_subs, budget=self.budget)
        fails = conflicts = 0
        while not self.stop.is_set():
            t0 = self.now()
            try:
                status, data = self.referee.call("GET", "wait", {"timeout": self.wait_timeout}, timeout=self.wait_timeout + 15)
            except tools.NET_ERRORS as e:
                fails += 1
                self.tr.log("-", "error", where="wait", error=f"{type(e).__name__}: {e}", consecutive=fails)
                if fails > 200:
                    self.exit_code = 2
                    break
                self.sleep(min(30, 2 * fails))
                continue
            fails = 0
            if status in (401, 403):
                self.tr.log("-", "error", where="wait", error=f"referee rejected the token (HTTP {status})")
                self.exit_code = 3
                break
            if status == 410 or (isinstance(data, dict) and (data.get("game_over") or data.get("ended"))):
                self.tr.log("-", "game_over", status=status)
                break
            if status == 409:  # the referee still has our turn open (e.g. end_turn was refused three times): close it
                conflicts += 1
                if conflicts > 60:
                    self.tr.log("-", "error", where="wait", error="the referee keeps answering 409 to wait", body=data)
                    self.exit_code = 2
                    break
                self.force_end(self.cmd, "wait_conflict")
                self.sleep(1)
                continue
            conflicts = 0
            if status != 200 or not isinstance(data, dict) or not data.get("woke") or data.get("turn_id") is None:
                if self.now() - t0 < 0.5:
                    self.sleep(0.5)
                continue
            self.wake_n += 1
            self.turn(self.wake_n, data)
            if self.max_turns and self.wake_n >= self.max_turns:
                break
        self.stop.set()
        for s in list(self.subs.values()):
            if s.thread.is_alive():
                s.thread.join(5)

    def deadline_from(self, data, limit):
        """The referee's clock decides (deadline_in seconds: the 8-minute backstop per wake, 120 before the game); the local limit is the fallback."""
        d = data.get("deadline_in") if isinstance(data, dict) else None
        ok = isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0
        return self.now() + (d if ok else limit)

    def turn(self, n, data):
        w = self.cmd
        w.turn_id, w.deadline, w.t0, w.requests, w.tool_calls, w.end_failures = data["turn_id"], self.deadline_from(data, self.turn_limit), self.now(), 0, 0, 0
        self.observing = False
        self.note_wake(w, data)
        if self.system is None:
            self.system = self.build_system(data.get("briefing"))
            w.conv = Conversation(self.system, self.budget, est_tokens(json.dumps([cli.schema()], ensure_ascii=False)))
        summary = self.render(data)
        self.tr.log(w.name, "wake", n=n, turn_id=w.turn_id, deadline=w.deadline, prematch=data.get("prematch"), reasons=data.get("reasons"),
                    summary=tools.clip(summary, 3000), summary_chars=len(summary))
        text = self.wake_head(n, w) + self.drain_reports() + summary
        reason = self.converse(w, text)
        if reason not in ("end_turn", "turn_over"):
            self.force_end(w, reason)
        w.open = False
        self.tr.log(w.name, "turn_end", turn_id=w.turn_id, reason=reason, seconds=round(self.now() - w.t0, 2), requests=w.requests, tool_calls=w.tool_calls)

    # ---- thinking time (materials/10 2.6 since 2026-11-04: a figure, no countdown, no budget) ----------------------
    # Not shown to the model any more (r5-minden 11-04: "[本回合已用…｜本局已思考…]" under every tool result and in the
    # wake head framed each wake as "order and end"); the referee records it, think_total() is for the logs only.
    def note_wake(self, w, data):
        w.open, self.think_at, self.prematch = True, self.now(), bool(data.get("prematch"))
        v = data.get("thinking_sec")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            self.think_base = float(v)

    def think_total(self):
        """The side's thinking seconds: the referee's figure at the last wake plus what the open turns added since."""
        t = self.now()
        live = [s for s in self.subs.values() if s.running] + ([self.cmd] if self.cmd.open and not self.prematch else [])
        return self.think_base + sum(t - max(s.t0, self.think_at) for s in live)

    def wake_head(self, n, w):
        if self.prematch:
            return f"[唤醒 #{n}｜回合 {w.turn_id}｜赛前部署阶段]\n"
        return f"[唤醒 #{n}｜回合 {w.turn_id}]\n"

    @staticmethod
    def render(data):
        """Wake text from a wait response: reasons, a line per failed wake script, the player's own wake text (its wake
        scripts) or else the summary view, and (before the game) the briefing."""
        def txt(x):
            return x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, separators=(",", ":"))
        parts = [f"唤醒原因：{txt(data['reasons'])}"] if data.get("reasons") else []
        parts += [str(x) for x in data.get("wake_errors") or []]
        if data.get("wake_text") is not None:
            parts += [data["wake_text"]] if data["wake_text"] else []
        elif data.get("summary") is not None:
            parts += [txt(data["summary"])]
        else:
            parts += [txt({k: v for k, v in data.items() if k not in ("woke", "turn_id", "deadline_in", "thinking_sec", "briefing")})]
        parts += ["任务简报：" + txt(data["briefing"])] if data.get("briefing") else []
        return "\n".join(parts)

    def build_system(self, b=None):
        if not isinstance(b, dict):
            try:
                status, b = self.referee.call("GET", "view/briefing")
            except tools.NET_ERRORS:
                status, b = 0, None
            b = b if status == 200 and isinstance(b, dict) else {}
        self.training = "sides" in b
        if self.training:                             # the training referee's briefing (both sides): no game yet
            text = (Path(HERE) / "prompts" / "training.md").read_text(encoding="utf-8")
            # a supplementary session (or the notes round between the two games): the ruling's text as the last
            # section, under its own "# " title if it starts with one; it overrides the numbers above
            sup = b.get("supplement") or ""
            if sup:
                sup = f"\n{sup}\n" if sup.startswith("# ") else f"\n# 补训（与上文不同之处以本节为准）\n\n{sup}\n"
            for k, v in {"player_label": PLAYERS.get(self.side, "参赛方"), "scenario_name": b.get("scenario") or "赛用想定",
                         "supplement": sup}.items():
                text = text.replace("{{" + k + "}}", str(v))
            return text.rstrip("\n") + "\n"
        me, enemy = SIDES.get(self.side, ("本方", "对方"))
        values = {"side_label": me, "enemy_side_label": enemy, "force_name": b.get("your_command_post") or "本方指挥部",
                  "scenario_name": b.get("scenario") or "本场想定"}
        ex = b.get("exercise")
        if isinstance(ex, dict):                      # a training-period exercise
            values["opponent_line"] = exercise_line(ex, enemy)
        else:
            values["opponent_line"] = (f"对手是游戏内置 AI（电脑）指挥的{enemy}。这是对内置 AI 的考核局，规则、计时和计分与正式赛相同。"
                                       if b.get("opponent") == "game_ai" else
                                       f"对手是{enemy}的指挥官。对手拿到的规则、工具、资料库和这份提示与你完全相同。")
            if b.get("workspace") == "carried":       # P5: the player's training workspace
                values["opponent_line"] += ("你的工作区里是你在训练期写的笔记和登记的脚本，原样带进了这一局：开局先用 arena note list 和 "
                                            "arena script list 看一遍；已登记的脚本会照常触发运行。工作区在两局之间保留，"
                                            "这一局里删掉的脚本、改动的笔记，下一局也是这样。笔记里的规则数字和 arena brief show "
                                            "不一致时以 brief show 为准：训练期简报给的波军胜利档有误，已按游戏终局表更正。")
        text = self.template
        for k, v in values.items():
            text = text.replace("{{" + k + "}}", str(v))
        return text

    # ---- one conversation: model <-> tools -------------------------------------------------------------------------
    def converse(self, w, first):
        conv, nudges, shrinks = w.conv, 0, 0
        conv.add({"role": "user", "content": first})
        schemas = [cli.schema()]
        while True:
            if self.stop.is_set():
                return "stopped"
            if self.now() >= w.deadline:
                return "deadline"
            if not w.sub:
                rep = self.drain_reports()
                if rep:
                    conv.add({"role": "user", "content": rep})
            info = conv.trim()
            if info:
                self.tr.log(w.name, "trim", **info)
            msgs = conv.request()
            self.tr.log(w.name, "request", n_messages=len(msgs), est_tokens=int(conv._raw), model=self.route.model, last=self.summarize(msgs[-1]))
            w.requests += 1
            try:
                r = self.chat.chat(msgs, schemas, deadline=w.deadline, agent=w.name)
            except client.DeadlineExceeded as e:
                self.tr.log(w.name, "error", where="model", error=str(e))
                return "deadline"
            except client.ModelError as e:
                self.tr.log(w.name, "error", where="model", error=str(e), status=e.status)
                if e.status == 400 and shrinks < 2:  # most likely the context is too long for this provider: trim harder and retry
                    conv.budget, shrinks = int(conv.budget * 0.7), shrinks + 1
                    continue
                return "model_error"
            conv.add(r.message)
            conv.calibrate(r.usage.get("prompt_tokens"))
            calls = r.message.get("tool_calls") or []
            self.tr.log(w.name, "response", content=r.message.get("content") or "", reasoning=tools.clip(r.message.get("reasoning_content") or "", 4000),
                        tool_calls=[{"id": c["id"], "name": c["function"]["name"], "arguments": tools.clip(c["function"]["arguments"], 2000)} for c in calls],
                        usage=r.usage, model=r.model, seconds=round(r.seconds, 2), finish_reason=r.finish_reason, rate=r.headers, id=r.id, attempts=r.attempts)
            if not calls and self.observing and not w.sub:  # observation mode: an answer without tools means "wait for the next update"
                update = self.wait_observe(w)
                if update:
                    conv.add({"role": "user", "content": update})
                continue
            if not calls:
                nudges += 1
                if nudges > 2:
                    return "no_tool_call"
                conv.add({"role": "user", "content": NUDGE})
                continue
            nudges = 0
            for i, c in enumerate(calls):
                if self.now() >= w.deadline:
                    self.skip(conv, calls[i:], "回合时限已到，未执行。")
                    return "deadline"
                text, done = self.execute(w, c)
                conv.add({"role": "tool", "tool_call_id": c["id"], "content": text})
                if done:
                    self.skip(conv, calls[i + 1:], "回合已结束，未执行。")
                    return done

    def wait_observe(self, w):
        while self.now() < w.deadline and not self.stop.is_set():
            try:
                status, data = self.referee.call("GET", "wait", {"timeout": min(30, w.deadline - self.now())}, timeout=45)
            except tools.NET_ERRORS:
                self.sleep(2)
                continue
            if status != 200 or (isinstance(data, dict) and data.get("game_over")):
                self.observing = False
                return f"[观察更新失败：HTTP {status}，已退出观察模式]"
            if isinstance(data, dict) and data.get("woke"):
                self.tr.log(w.name, "observe_update", chars=len(self.render(data)))
                return "[观察更新]\n" + self.render(data)
        return None

    @staticmethod
    def skip(conv, calls, why):
        for c in calls:
            conv.add({"role": "tool", "tool_call_id": c["id"], "content": why})

    @staticmethod
    def summarize(m):
        return {"role": m["role"], "content": tools.clip(m.get("content") or "", 300), "tool_calls": [c["function"]["name"] for c in m.get("tool_calls") or []]}

    def execute(self, w, call):
        """Run one call of the `arena` tool. Returns (result text, None) or (result text, reason) when the turn is over."""
        name, raw, t0, done, status, cmd = call["function"]["name"], call["function"].get("arguments") or "{}", self.now(), None, None, None
        w.tool_calls += 1
        try:
            args = json.loads(raw)
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as e:
            args, text = None, f"错误：参数不是合法的 JSON 对象：{e}"
        if args is not None:
            cmd = args.get("cmd")
            if name != cli.TOOL_NAME:
                text = f"错误：没有工具 {name}。只有一个工具 arena：cmd 写命令行，如 help、unit list、order add 1/A move 2118"
            elif not isinstance(cmd, str) or not cmd.strip():
                text = "错误：cmd 不能为空；先试 arena help"
            else:
                out = self.worker_cli(w).run(cmd, args.get("input"))
                text, status, done = out.text, out.status, "turn_over" if out.turn_over else None
                if out.driver:
                    op, a = out.driver
                    if op == "end_turn":
                        text, done = self.end_turn(w, a)
                    elif op == "spawn_subagent":
                        text = self.spawn(a)
                    else:
                        text = self.check(w, a)
                    text = cli.format_driver(op, text)
                if out.observe is not None and status and 200 <= status < 300:
                    self.observing = out.observe and not w.sub
                    text += OBSERVE_NOTE if self.observing else ""
                if out.note and status and 200 <= status < 300:
                    w.conv.notes[out.note[0]] = out.note[1]
        self.tr.log(w.name, "tool", name=name, cmd=cmd if isinstance(cmd, str) else None, arguments=tools.clip(raw, 2000),
                    result=tools.clip(text, 4000), result_chars=len(text), http_status=status, seconds=round(self.now() - t0, 3), ends_turn=done)
        return text, done

    def worker_cli(self, w):
        """Each conversation has its own command line (subagents run in threads; a subagent's has no agent / observe)."""
        if getattr(w, "cli", None) is None:
            w.cli = cli.Cli(self.referee, self.library, sub=w.sub)
        return w.cli

    # ---- end of turn -----------------------------------------------------------------------------------------------
    def end_turn(self, w, args):
        body = {"turn_id": w.turn_id} if w.turn_id else {}
        if args.get("subscriptions") is not None and not w.sub:  # only the commander changes subscriptions
            body["subscriptions"] = args["subscriptions"]
        if w.sub:
            body["agent"] = w.name
        try:
            status, data = self.referee.call("POST", "turn/end", body=body)
        except tools.NET_ERRORS as e:
            status, data = 0, f"{type(e).__name__}: {e}"
        over = tools.is_turn_over(status, data)
        if not (200 <= status < 300 or over):
            w.end_failures += 1
            if w.end_failures < 3:
                return json.dumps({"ok": False, "http_status": status, "error": data}, ensure_ascii=False), None
            data = "（referee 拒绝了 end_turn，已本地结束回合）"
        if w.sub:
            w.report = args.get("report") or self.last_text(w) or "（无报告）"
        running = [s.name for s in self.subs.values() if s.running and s is not w]
        if not w.sub:
            self.observing = False
        return json.dumps({"ok": True, "subagents_still_running": running} if not w.sub else {"ok": True}, ensure_ascii=False), "turn_over" if over else "end_turn"

    def force_end(self, w, reason):
        try:
            self.referee.call("POST", "turn/end", body={"reason": reason, **({"agent": w.name} if w.sub else {"turn_id": w.turn_id})})
        except tools.NET_ERRORS:
            pass  # the referee ends the turn on its own clock anyway

    @staticmethod
    def last_text(w):
        return next((m["content"] for m in reversed(w.conv.messages) if m["role"] == "assistant" and m.get("content")), "")

    # ---- subagents -------------------------------------------------------------------------------------------------
    def spawn(self, args):
        name, task = str(args.get("name", "")).strip(), str(args.get("task", "")).strip()
        err = None
        with self.cv:
            running = [s.name for s in self.subs.values() if s.running]
            if not SUB_NAME.match(name) or not task:
                err = f"name 要{tools.NAME_RULE}，task 不能为空"
            elif name == "commander" or name in running:
                err = f"名字 {name!r} 已被占用，换一个"
            elif len(running) >= self.max_subs:
                err = f"已有 {len(running)} 个子代理在运行（上限 {self.max_subs}）：{', '.join(running)}。等其中一个结束（arena agent check）再派"
            else:
                s = Worker(name, sub=True)
                s.running, s.deadline = True, self.now() + self.sub_limit
                s.thread = threading.Thread(target=self.run_sub, args=(s, task), name="sub-" + name, daemon=True)
                self.subs[name] = s
        if err:
            return json.dumps({"ok": False, "error": err}, ensure_ascii=False)
        self.tr.log(name, "subagent", event="spawn", task=tools.clip(task, 2000))
        s.thread.start()
        return json.dumps({"ok": True, "name": name, "note": "用 arena agent check 取结果"}, ensure_ascii=False)

    def run_sub(self, s, task):
        s.t0, reason = self.now(), "error"
        try:
            status, data = self.referee.call("POST", "turn/begin", body={"agent": s.name})
            if not 200 <= status < 300:
                s.report = f"referee 拒绝开始任务（HTTP {status}）：{json.dumps(data, ensure_ascii=False)[:300]}"
                return
            s.turn_id, s.deadline = (data or {}).get("turn_id") if isinstance(data, dict) else None, self.deadline_from(data, self.sub_limit)
            s.conv = Conversation(self.system, self.budget, est_tokens(json.dumps([cli.schema()], ensure_ascii=False)))
            s.conv.notes = self.cmd.conv.notes  # one set of notes digests per side
            first = SUB_FIRST.format(name=s.name, secs=int(s.deadline - self.now()), task=task)
            reason = self.converse(s, first)
            if reason != "end_turn":
                if reason != "turn_over":
                    self.force_end(s, reason)
                s.report = f"（任务被强制结束：{reason}，未交正式报告）最后一段输出：" + (self.last_text(s) or "无")
        except Exception as e:  # a crashed subagent must not take the process down
            self.tr.log(s.name, "error", where="subagent", error=f"{type(e).__name__}: {e}")
            s.report = f"子代理出错：{type(e).__name__}: {e}"
        finally:
            self.tr.log(s.name, "subagent", event="end", reason=reason, seconds=round(self.now() - s.t0, 2), requests=s.requests,
                        tool_calls=s.tool_calls, report=tools.clip(s.report, 2000))
            with self.cv:  # report and "no longer running" become visible together, so a new spawn right after check_subagents works
                s.running = False
                self.inbox.append({"name": s.name, "reason": reason, "report": s.report})
                self.cv.notify_all()

    def drain_reports(self):
        with self.cv:
            items, self.inbox = self.inbox, []
        return "".join(f"[子代理 {i['name']} 的报告｜{i['reason']}]\n{i['report']}\n\n" for i in items)

    def check(self, w, args):
        wait = max(0, min(60, int(args.get("wait_s") or 0)))
        end = time.monotonic() + min(wait, max(0, w.deadline - self.now()))
        with self.cv:
            while not self.inbox and any(s.running for s in self.subs.values()) and time.monotonic() < end:
                self.cv.wait(min(1.0, end - time.monotonic()))
            items, self.inbox = self.inbox, []
            state = [{"name": s.name, "state": "running" if s.running else "done", "seconds": round(self.now() - s.t0, 1)} for s in self.subs.values()]
        return json.dumps({"subagents": state, "reports": items}, ensure_ascii=False)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--side", type=int, required=True, choices=(0, 1))
    ap.add_argument("--route", required=True, help=", ".join(routes.ROUTES))
    ap.add_argument("--referee", default="http://127.0.0.1:41010")
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--run", required=True, help="run directory; the transcript goes to <run>/agents/side<N>/transcript.jsonl")
    ap.add_argument("--script", help="fake route: JSON list of steps (see client.py)")
    ap.add_argument("--extra", help="JSON object merged into every chat request body (e.g. the thinking-effort field)")
    ap.add_argument("--library", default=os.path.join(ROOT, "arena", "library"))
    ap.add_argument("--turn-limit", type=float, default=480, help="commander seconds per wake-up (10 doc 2.6: 8 minutes)")
    ap.add_argument("--sub-limit", type=float, default=600, help="subagent seconds per task (10 minutes)")
    ap.add_argument("--max-subs", type=int, default=2)
    ap.add_argument("--wait-timeout", type=float, default=60)
    ap.add_argument("--context-budget", type=int, default=96000, help="estimated prompt tokens before old messages are dropped")
    ap.add_argument("--max-turns", type=int, default=0, help="exit after this many turns (0 = until the game ends)")
    ap.add_argument("--request-timeout", type=float, default=120,
                    help="seconds before a model request that has not answered is sent again (P3 check: the proxy "
                         "sometimes hangs a request; normal answers took under 40 s)")
    a = ap.parse_args(argv)
    token = Path(a.token_file).read_text(encoding="utf-8").strip()
    route = routes.get_route(a.route, json.loads(a.extra) if a.extra else None)
    tr = Transcript(os.path.join(a.run, "agents", f"side{a.side}", "transcript.jsonl"))
    if route.name == "fake":
        chat = client.ScriptedChat(json.loads(Path(a.script).read_text(encoding="utf-8")) if a.script else [])
    else:
        chat = client.ChatClient(route, on_retry=lambda info: tr.log("-", "retry", **info),
                                 request_timeout=a.request_timeout)
    template = (Path(HERE) / "prompts" / "system.md").read_text(encoding="utf-8")
    agent = Agent(a.side, chat, tools.Referee(a.referee, token), tools.Library(a.library), tr, template, route, a.turn_limit, a.sub_limit,
                  a.max_subs, a.wait_timeout, a.context_budget, a.max_turns)
    try:
        agent.run()
    except KeyboardInterrupt:
        agent.stop.set()
    finally:
        tr.close()
    return agent.exit_code


if __name__ == "__main__":
    sys.exit(main())
