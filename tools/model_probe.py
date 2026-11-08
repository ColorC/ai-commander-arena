"""P0.6: 20 rounds of tool calls against one model route (OpenAI-compatible chat completions).

python -I tools/model_probe.py <route> [rounds] [out.jsonl]
routes: kimi-code (Kimi Code subscription, k3.1-pro), deepseek-go (OpenCode Go), deepseek-proxy (any OpenAI-compatible
endpoint: ARENA_PROXY_BASE_URL / ARENA_PROXY_API_KEY / ARENA_PROXY_MODEL),
deepseek-1017-dashscope (Alibaba Cloud token plan, the 2026-10-17 DeepSeek V4.1 Flash release).
The model must call next_step once per round and pass the sum asked for in the previous tool result; each call's
status, latency, reported model, usage and rate-limit headers go to out.jsonl. Secrets are read from the local
CLI config files and never written out.
"""
import json
import os
import random
import sys
import time
import uuid
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")


def kimi_token():
    """Kimi Code API key from the environment (KIMI_API_KEY); otherwise the CLI's OAuth token (15-minute life)."""
    if os.environ.get("KIMI_API_KEY"):
        return os.environ["KIMI_API_KEY"]
    c = json.load(open(os.path.join(HOME, ".kimi-code", "credentials", "kimi-code.json"), encoding="utf-8"))
    if c.get("expires_at", 0) < time.time() + 30:
        raise SystemExit("kimi-code access token expired: run `kimi -p ok` once so the CLI refreshes it")
    return c["access_token"]


def opencode_go_key():
    """OpenCode Console device login (opencode auth login opencode --method device); the old opencode-go API key expired."""
    import sqlite3
    db = os.path.join(HOME, ".local", "share", "opencode", "opencode.db")
    con = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
    row = con.execute("select value from credential where integration_id = 'opencode' and active = 1").fetchone()
    v = json.loads(row[0])
    if v.get("expires", 0) < time.time() * 1000 + 60000:
        raise SystemExit("OpenCode Console token expired: run `opencode auth login opencode --method device`")
    return v["access"]


def proxy_key():
    if not os.environ.get("ARENA_PROXY_API_KEY"):
        raise SystemExit("ARENA_PROXY_API_KEY is not set")
    return os.environ["ARENA_PROXY_API_KEY"]


ROUTES = {
    "kimi-code": ("https://api.kimi.com/coding/v1", "k3.1-pro", kimi_token),
    "deepseek-go": ("https://opencode.ai/inference/go/openai/v1", "deepseek-v4.1-flash", opencode_go_key),
    "deepseek-proxy": (os.environ.get("ARENA_PROXY_BASE_URL", "").rstrip("/"),
                       os.environ.get("ARENA_PROXY_MODEL", "deepseek-v4.1-flash"), proxy_key),
    "deepseek-1017-dashscope": ("https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
                                "deepseek-v4.1-flash-1017", lambda: os.environ["DASHSCOPE_TOKEN_PLAN_API_KEY"]),
}

TOOLS = [{"type": "function", "function": {
    "name": "next_step",
    "description": "Report the answer to the current step and receive the next one.",
    "parameters": {"type": "object", "properties": {
        "step": {"type": "integer", "description": "number of the step being answered (0 to start)"},
        "answer": {"type": "integer", "description": "sum asked for in that step (0 to start)"}},
        "required": ["step", "answer"]}}}]

SYSTEM = ("You are testing tool calls. Call next_step exactly once per reply, starting with step=0, answer=0. "
          "Each tool result gives the next step and two numbers; call next_step with that step number and their sum. "
          "When a tool result says done, reply with the single word: finished.")


SESSION = str(uuid.uuid4())
EXTRA_HEADERS = {"deepseek-go": {"x-opencode-session": SESSION}}  # OpenCode Go refuses requests without it


def post(base, key, body, extra=None):
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key,
               "User-Agent": "ai-commander-arena/0.1"}  # Cloudflare answers 1010 to the default Python-urllib agent
    headers.update(extra or {})
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), method="POST", headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, dict(r.headers), json.loads(r.read()), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), {"error_body": e.read().decode("utf-8", "replace")[:2000]}, time.time() - t0


def main():
    route = sys.argv[1]
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    out = open(sys.argv[3] if len(sys.argv) > 3 else f"model-probe-{route}.jsonl", "a", encoding="utf-8")
    base, model, keyfn = ROUTES[route]
    rng = random.Random(1017)
    tasks = [(rng.randint(100, 999), rng.randint(100, 999)) for _ in range(rounds)]
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Start."}]
    ok_rounds, errors, step = 0, 0, 0
    for call in range(rounds + 3):
        status, headers, data, dt = post(base, keyfn(), {"model": model, "messages": msgs, "tools": TOOLS},
                                       EXTRA_HEADERS.get(route))
        rl = {k: v for k, v in headers.items() if "limit" in k.lower() or "remaining" in k.lower() or "reset" in k.lower()}
        rec = {"route": route, "call": call, "status": status, "seconds": round(dt, 2), "model": data.get("model"),
               "usage": data.get("usage"), "rate_headers": rl, "id": data.get("id"), "system_fingerprint": data.get("system_fingerprint")}
        if status != 200:
            rec["error"] = data.get("error_body")
            out.write(json.dumps(rec, ensure_ascii=False) + "\n"); out.flush()
            errors += 1
            print(json.dumps(rec, ensure_ascii=False)[:600])
            if errors >= 3:
                break
            time.sleep(5)
            continue
        msg = data["choices"][0]["message"]
        rec["finish_reason"] = data["choices"][0].get("finish_reason")
        calls = msg.get("tool_calls") or []
        rec["tool_calls"] = [c["function"]["name"] + c["function"]["arguments"] for c in calls]
        rec["content"] = (msg.get("content") or "")[:200]
        rec["has_reasoning"] = bool(msg.get("reasoning_content"))
        out.write(json.dumps(rec, ensure_ascii=False) + "\n"); out.flush()
        print(call, status, f"{dt:.1f}s", rec["model"], rec["tool_calls"], rec["content"][:40])
        msgs.append({k: v for k, v in msg.items() if v is not None})
        if not calls:
            break
        for c in calls:
            try:
                args = json.loads(c["function"]["arguments"])
            except ValueError:
                args = {}
            if step > 0 and args.get("step") == step and args.get("answer") == sum(tasks[step - 1]):
                ok_rounds += 1
            if step >= rounds:
                result = "done"
            else:
                a, b = tasks[step]
                step += 1
                result = f"step {step}: {a} and {b}"
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": result})
    summary = {"route": route, "summary": True, "correct_rounds": ok_rounds, "rounds": rounds, "http_errors": errors}
    out.write(json.dumps(summary) + "\n")
    print(summary)


if __name__ == "__main__":
    main()
