"""Chat-completions client for arena-agent: retries, per-request records, and the scripted `fake` model.

ChatClient.chat() retries network errors, 429 and 5xx with backoff (Retry-After honoured, capped), waits and retries
when the route's credential is unavailable (another process may refresh it), and gives up at the caller's deadline.
Reply carries what the provider reported: model, usage, rate-limit headers.
ScriptedChat replays a JSON list of steps instead of calling a model:
  [{"agent": "commander" | "<sub name>" | "sub", "sleep": 0.2, "content": "...",
    "tool_calls": [{"name": "arena", "arguments": {"cmd": "unit list"}}]}, ...]
A step is used by the first request of a matching agent (default "commander"; "sub" matches any subagent);
when an agent has no step left it ends its turn by itself. A call written with a tool name from before season 2
(get_summary, submit_command, end_turn, ...) is sent as the `arena` command it became (arena/agent/cli.legacy).
"""
import http.client
import json
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from arena.agent import routes

UA = "ai-commander-arena/0.1"  # Cloudflare answers 1010 to the default Python-urllib agent
RETRY_AFTER_CAP = 60.0


class ModelError(RuntimeError):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


class DeadlineExceeded(ModelError):
    pass


@dataclass
class Reply:
    message: dict
    usage: dict = field(default_factory=dict)
    model: str = ""
    seconds: float = 0.0
    finish_reason: str = ""
    headers: dict = field(default_factory=dict)
    id: str = ""
    attempts: int = 1


class ChatClient:
    def __init__(self, route, on_retry=None, now=time.time, sleep=time.sleep, max_attempts=6, backoff=2.0,
                 backoff_max=30.0, key_wait=15.0, request_timeout=300.0):
        self.route, self.on_retry, self.now, self.sleep = route, on_retry or (lambda info: None), now, sleep
        self.max_attempts, self.backoff, self.backoff_max = max_attempts, backoff, backoff_max
        self.key_wait, self.request_timeout = key_wait, request_timeout

    def _post(self, key, body, timeout):
        headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key, "User-Agent": UA, **self.route.headers}
        req = urllib.request.Request(self.route.base + "/chat/completions", data=json.dumps(body).encode(), method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def chat(self, messages, tools, deadline=None, agent=None):
        body = {"model": self.route.model, "messages": messages, "tools": tools, **self.route.extra}
        err = ""
        for attempt in range(1, self.max_attempts + 1):
            timeout = self.request_timeout if deadline is None else min(self.request_timeout, deadline - self.now())
            if timeout < 1:
                raise DeadlineExceeded(f"deadline reached before the model answered ({err or 'no attempt made'})")
            t0, wait = self.now(), self._backoff(attempt)
            try:
                status, headers, raw = self._post(self.route.key(), body, timeout)
                if status == 200:
                    data = json.loads(raw)
                    if data.get("choices"):
                        c = data["choices"][0]
                        rl = {k: v for k, v in headers.items() if any(w in k.lower() for w in ("limit", "remaining", "reset"))}
                        return Reply({k: v for k, v in c["message"].items() if v is not None}, data.get("usage") or {},
                                     data.get("model") or "", self.now() - t0, c.get("finish_reason") or "", rl, data.get("id") or "", attempt)
                    err = "HTTP 200 without choices"
                else:
                    err = f"HTTP {status}: {raw.decode('utf-8', 'replace')[:500]}"
                    if status != 429 and status < 500 and status != 408:
                        raise ModelError(err, status)
                    try:
                        wait = min(RETRY_AFTER_CAP, float(headers.get("Retry-After", ""))) if status == 429 else self._backoff(attempt)
                    except ValueError:
                        wait = self._backoff(attempt)
            except routes.KeyUnavailable as e:
                err, wait = f"{type(e).__name__}: {e}", self.key_wait
            except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
                err, wait = f"{type(e).__name__}: {e}", self._backoff(attempt)
            self.on_retry({"attempt": attempt, "error": err, "wait": round(wait, 1)})
            if deadline is not None and self.now() + wait >= deadline:
                raise DeadlineExceeded(f"deadline reached while retrying: {err}")
            if attempt < self.max_attempts:
                self.sleep(wait)
        raise ModelError(f"gave up after {self.max_attempts} attempts: {err}")

    def _backoff(self, attempt):
        return min(self.backoff_max, self.backoff * 2 ** (attempt - 1)) * random.uniform(0.75, 1.25)


class ScriptedChat:
    def __init__(self, steps, now=time.time, sleep=time.sleep):
        self.steps, self.now, self.sleep, self.lock, self.n = list(steps), now, sleep, threading.Lock(), 0

    def chat(self, messages, tools, deadline=None, agent="commander"):
        with self.lock:
            sub = agent != "commander"
            for i, s in enumerate(self.steps):
                if s.get("agent", "commander") == agent or (sub and s.get("agent") == "sub"):
                    step = self.steps.pop(i)
                    break
            else:
                step = {"tool_calls": [{"name": "arena", "arguments": {"cmd": "turn end", **({"input": "script exhausted"} if sub else {})}}]}
            self.n += 1
            n = self.n
        self.sleep(step.get("sleep", 0))
        calls = [{"id": f"fake-{n}-{j}", "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments", {}), ensure_ascii=False)}}
                 for j, c in enumerate(self.translate(c) for c in step.get("tool_calls", []))]
        msg = {"role": "assistant", "content": step.get("content") or ""}
        if calls:
            msg["tool_calls"] = calls
        return Reply(msg, {"prompt_tokens": sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) // 4, "completion_tokens": 10},
                     "fake", 0.0, "tool_calls" if calls else "stop", {}, f"fake-{n}", 1)

    @staticmethod
    def translate(c):
        """An old tool call of a script -> the `arena` call it became; other calls unchanged."""
        from arena.agent import cli
        if c.get("name") == cli.TOOL_NAME:
            return c
        t = cli.legacy(c.get("name"), c.get("arguments") or {})
        if t is None:
            return c
        return {"name": cli.TOOL_NAME, "arguments": {"cmd": t[0], **({"input": t[1]} if t[1] is not None else {})}}
