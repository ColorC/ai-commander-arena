"""Model routes for arena-agent (OpenAI-compatible chat completions + tools), lifted from tools/model_probe.py.

Routes: kimi-code (k3.1-pro), deepseek-go, deepseek-proxy (any OpenAI-compatible endpoint, from the environment),
deepseek-1017-dashscope, fake (offline, scripted).
Secrets are read from the local CLI config files or the environment at request time and never written anywhere.
A credential that is missing or expired raises KeyUnavailable / TokenExpired; this module never refreshes anything
(the Kimi login is shared with other processes, so refreshing here would knock them out).
"""
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

HOME = os.path.expanduser("~")
SESSION = str(uuid.uuid4())

# Request field for Kimi "thinking high". Checked 10-30: k3.1-pro
# always thinks ("disabled" is refused); reasoning_effort "low" cuts reasoning tokens to about half, "high" is accepted
KIMI_THINKING = {"reasoning_effort": "high"}


class KeyUnavailable(RuntimeError):
    """The route's credential is missing or expired; only a human or another process can fix it."""


class TokenExpired(KeyUnavailable):
    pass


def kimi_token():
    """The Kimi CLI's OAuth access token (15-minute life, refreshed by the CLI). The KIMI_API_KEY of this machine's
    environment is not used: Kimi Code answers it 403 "subscription does not have access" (P0.6, again 10-30)."""
    path = os.path.join(HOME, ".kimi-code", "credentials", "kimi-code.json")
    try:
        c = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise KeyUnavailable(f"kimi-code credentials unreadable ({type(e).__name__}): run `kimi -p ok` once") from None
    if c.get("expires_at", 0) < time.time() + 30:
        raise TokenExpired("kimi-code access token expired: run `kimi -p ok` once so the CLI refreshes it")
    return c["access_token"]


def opencode_go_key():
    """OpenCode Console device login (opencode auth login opencode --method device)."""
    import sqlite3
    db = os.path.join(HOME, ".local", "share", "opencode", "opencode.db")
    try:
        con = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
        v = json.loads(con.execute("select value from credential where integration_id = 'opencode' and active = 1").fetchone()[0])
    except (sqlite3.Error, TypeError, ValueError) as e:
        raise KeyUnavailable(f"opencode credential unreadable ({type(e).__name__}): run `opencode auth login opencode --method device`") from None
    if v.get("expires", 0) < time.time() * 1000 + 60000:
        raise TokenExpired("OpenCode Console token expired: run `opencode auth login opencode --method device`")
    return v["access"]


# deepseek-proxy: any OpenAI-compatible endpoint serving the DeepSeek model, configured only through the environment
PROXY_BASE = os.environ.get("ARENA_PROXY_BASE_URL", "").rstrip("/")
PROXY_MODEL = os.environ.get("ARENA_PROXY_MODEL", "deepseek-v4.1-flash")


def proxy_key():
    if not PROXY_BASE:
        raise KeyUnavailable("ARENA_PROXY_BASE_URL is not set (OpenAI-compatible base URL, e.g. https://<host>/v1)")
    if not os.environ.get("ARENA_PROXY_API_KEY"):
        raise KeyUnavailable("ARENA_PROXY_API_KEY is not set")
    return os.environ["ARENA_PROXY_API_KEY"]


def dashscope_key():
    if not os.environ.get("DASHSCOPE_TOKEN_PLAN_API_KEY"):
        raise KeyUnavailable("DASHSCOPE_TOKEN_PLAN_API_KEY is not set")
    return os.environ["DASHSCOPE_TOKEN_PLAN_API_KEY"]


@dataclass(frozen=True)
class Route:
    name: str
    base: str
    model: str
    key: object                      # callable returning the bearer token
    headers: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)  # merged into every request body


ROUTES = {r.name: r for r in (
    Route("kimi-code", "https://api.kimi.com/coding/v1", "k3.1-pro", kimi_token, extra=KIMI_THINKING),
    Route("deepseek-go", "https://opencode.ai/inference/go/openai/v1", "deepseek-v4.1-flash", opencode_go_key,
          headers={"x-opencode-session": SESSION}),  # OpenCode Go refuses requests without it
    Route("deepseek-proxy", PROXY_BASE, PROXY_MODEL, proxy_key),
    Route("deepseek-1017-dashscope", "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
          "deepseek-v4.1-flash-1017", dashscope_key),
    Route("fake", "", "fake", lambda: "fake"),
)}


def get_route(name, extra=None):
    if name not in ROUTES:
        raise SystemExit(f"unknown route {name!r}; choose from {', '.join(ROUTES)}")
    r = ROUTES[name]
    return Route(r.name, r.base, r.model, r.key, dict(r.headers), {**r.extra, **(extra or {})})
