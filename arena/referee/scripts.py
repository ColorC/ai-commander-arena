"""Reflex scripts: registration and scheduling. The Lua sandbox is injected (another component).

runner(request) -> result, both plain dicts:
  request = {"side", "name", "source", "trigger", "input": <fog-checked view>, "events": [...], "persist": <table>,
             "limits": {"instructions", "ms", "memory_mb"}}
  result  = {"ok": bool, "limit": bool (over instruction / time / memory limit), "limit_kind": "instructions" | "time" |
             "memory" | None, "error": str|None, "instructions": int, "ms": float, "commands": [<queue bodies>],
             "alarm": str|None, "report": str|None (report() text), "persist": <table>, "log": [lines printed]}
(arena/sandbox/runner.py ScriptHost is such a runner; limit_kind and log only go to the scripts log.)
Triggers: {"on": "minute"} | {"on": "orders_phase"} | {"on": "wake"} | {"on": "event", "types": [...]}.
A wake script (r5-minden 11-04: both commanders read the fixed wake summary as the whole picture and hardly looked
further) runs in the game referee right before its side's commander is woken in the game (not the prematch); what it
gives report(text) is the wake text instead of the default summary (core.Referee._wake_text). run() returns each
script's outcome, so the caller can use the reports.
A call over the per-call limits is void; a side that has used SCRIPT_MINUTE_INSTR in a game minute runs nothing more
that minute; a script over the limit SCRIPT_STRIKES times in a row is switched off and the commander is woken.
With `stores` ({side: workspace.Workspace}) the scripts also live on disk in the player's workspace (scripts/<name>.json:
source and trigger, not the persistent table): put writes, delete removes, load() reads them back when a referee starts.
check_trigger() is the trigger rule put() applies; lint_source() is the `script lint` of the agent's CLI (materials/18
2.1): Lua syntax through an injected checker (arena/sandbox/runner.check_syntax compiles without running), the unit
names the source spells out, the trigger, and the library calls the sandbox does not have.
"""
import difflib
import json
import re

from . import rules

NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class ScriptError(Exception):
    pass


TRIGGER_RULE = "trigger must be {on: minute | orders_phase | wake} or {on: event, types: [...]}"
PLAIN_TRIGGERS = ("minute", "orders_phase", "wake")


def check_trigger(tr):
    """The trigger as put() stores it (None = every minute); ScriptError when it is not one of the forms."""
    tr = tr or {"on": "minute"}
    ok = isinstance(tr, dict) and (tr.get("on") in PLAIN_TRIGGERS or (
        tr.get("on") == "event" and isinstance(tr.get("types"), list) and tr["types"]
        and all(t in rules.SUB_TYPES for t in tr["types"])))
    if not ok:
        raise ScriptError(TRIGGER_RULE + f"; event types: {sorted(rules.SUB_TYPES)}")
    return tr


# a string literal right after one of these keys names an own unit (a submit() body); "..." or '...'
_UNIT_KEY = re.compile(r"\b(unit|target_unit|requester_unit|spotter_unit)\s*=\s*$")
_STRING = re.compile(r"""("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')""")
_MISSING = re.compile(r"\b(io|os|debug|package)\s*\.\s*\w+|\b(require|load|loadstring|dofile|loadfile)\s*\(")


def lint_source(source, trigger=None, unit_names=None, syntax=None):
    """Static checks of a reflex script -> {"ok", "errors", "warnings", "units"}.
    unit_names: the side's own unit names (None = not known, no name check); syntax(source) -> {"ok", "error"} or None
    (no sandbox: the syntax is not checked, which is said in a warning)."""
    errors, warnings, units = [], [], []
    if not isinstance(source, str) or not source.strip():
        return {"ok": False, "errors": ["source is empty"], "warnings": [], "units": []}
    if len(source.encode("utf-8")) > rules.SCRIPT_SOURCE_BYTES:
        errors.append(f"source is over {rules.SCRIPT_SOURCE_BYTES} bytes")
    try:
        check_trigger(trigger)
    except ScriptError as e:
        errors.append(str(e))
    res = syntax(source) if syntax else None
    if res is None:
        warnings.append("语法没检查：本机没有 Lua 沙盒")
    elif res.get("unavailable"):
        warnings.append(f"语法没检查：{res.get('error')}")
    elif not res.get("ok"):
        errors.append(f"Lua 语法错误：{res.get('error')}")
    for m in _MISSING.finditer(source):
        warnings.append(f"沙盒里没有 {m.group(0).rstrip('(').strip()}：只有 string / table / math / utf8 和安全的基础函数")
    names = set(unit_names or [])
    low = {n.lower(): n for n in names}
    for m in _STRING.finditer(source):
        text = m.group(0)[1:-1]
        if not text or "\\" in text:
            continue
        keyed = bool(_UNIT_KEY.search(source[max(0, m.start() - 40):m.start()]))
        if text in names:
            units.append(text)
            continue
        if unit_names is None:
            continue
        guess = low.get(text.lower()) or next(iter(difflib.get_close_matches(text, names, 1, 0.8)), None)
        if keyed:
            errors.append(f"单位名 {text!r} 不是本方单位" + (f"；你是不是想说 {guess!r}" if guess else "") +
                          "（单位名区分大小写，要和 unit list 里的写法完全一样）")
        elif guess:
            warnings.append(f"字符串 {text!r} 像单位名但对不上本方单位；你是不是想说 {guess!r}（脚本里比较单位名区分大小写）")
    return {"ok": not errors, "errors": errors, "warnings": list(dict.fromkeys(warnings)),
            "units": sorted(set(units))}


class Scripts:
    def __init__(self, log, runner=None, stores=None):
        self.log, self.runner, self.stores = log, runner, stores or {}
        self.defs = {0: {}, 1: {}}
        self.minute_used = {0: (-1, 0), 1: (-1, 0)}     # side -> (game minute, instructions)

    def load(self, side):
        """Register the scripts stored in the side's workspace as they are; a file that does not load is logged and
        skipped. Returns the names loaded."""
        store, done = self.stores.get(side), []
        for name, path in store.stored_scripts() if store else []:
            try:
                with open(path, encoding="utf-8") as f:
                    rec = json.load(f)
                if not isinstance(rec, dict):
                    raise ValueError("not a JSON object")
                self.put(side, name, {"source": rec.get("source"), "trigger": rec.get("trigger")}, save=False)
                done.append(name)
            except (OSError, ValueError, ScriptError) as e:
                self.log.write("scripts", {"side": side, "script": name, "event": "load_failed", "error": str(e)[:300]})
        if done:
            self.log.write("scripts", {"side": side, "event": "loaded", "scripts": done})
        return done

    def reload(self, side):
        """Forget the side's scripts in memory and load the stored ones again (another referee may have changed them)."""
        self.defs[side] = {}
        return self.load(side)

    def put(self, side, name, body, save=True):
        if not NAME.match(name or ""):
            raise ScriptError("script name must match [a-z][a-z0-9_]{0,31}")
        src = body.get("source")
        if not isinstance(src, str) or not src.strip() or len(src.encode("utf-8")) > rules.SCRIPT_SOURCE_BYTES:
            raise ScriptError(f"source must be non-empty Lua text of at most {rules.SCRIPT_SOURCE_BYTES} bytes")
        tr = check_trigger(body.get("trigger"))
        if name not in self.defs[side] and len(self.defs[side]) >= rules.MAX_SCRIPTS:
            raise ScriptError(f"at most {rules.MAX_SCRIPTS} scripts per side")
        store = self.stores.get(side)
        if save and store is not None:
            try:
                store.save_script(name, {"name": name, "source": src, "trigger": tr})
            except OSError as e:
                raise ScriptError(f"the script could not be stored in your workspace: {e.strerror or e}")
        self.defs[side][name] = {"name": name, "source": src, "trigger": tr, "persist": {}, "strikes": 0,
                                 "enabled": True, "runs": 0}
        return self.describe(side)[name]

    def delete(self, side, name):
        store = self.stores.get(side)
        on_disk = store is not None and bool(NAME.match(name or "")) and store.remove_script(name)
        return (self.defs[side].pop(name, None) is not None) or on_disk

    def describe(self, side):
        return {n: {"trigger": d["trigger"], "enabled": d["enabled"], "runs": d["runs"], "strikes": d["strikes"],
                    "bytes": len(d["source"].encode("utf-8"))} for n, d in self.defs[side].items()}

    def _wanted(self, d, trigger, events):
        tr = d["trigger"]
        if tr["on"] != trigger:
            return False
        return trigger != "event" or any(e["type"] in tr["types"] for e in events)

    def run(self, side, trigger, view, events, sec, t, submit, alarm):
        """Run every enabled script of `side` that wants `trigger`, in name order. submit(body, source) /
        alarm(name, text, forced). -> [{"name", "ok", "error", "report"}] of the scripts that were due (a skipped or
        void run is ok False with its reason)"""
        out = []
        if self.runner is None:
            return out
        minute = sec // 60
        for name, d in sorted(self.defs[side].items()):
            if not d["enabled"] or not self._wanted(d, trigger, events):
                continue
            m, used = self.minute_used[side]
            used = used if m == minute else 0
            if used >= rules.SCRIPT_MINUTE_INSTR:
                self.log.write("scripts", {"t": t, "sec": sec, "side": side, "script": name, "event": "skipped_minute_budget"})
                out.append({"name": name, "ok": False, "error": "not run: the side's scripts used too much this game minute",
                            "report": None})
                continue
            req = {"side": side, "name": name, "source": d["source"], "trigger": trigger, "input": view,
                   "events": events, "persist": d["persist"],
                   "limits": {"instructions": rules.SCRIPT_CALL_INSTR, "ms": rules.SCRIPT_CALL_MS,
                              "memory_mb": rules.SCRIPT_MEM_MB}}
            res = self.runner(req)
            d["runs"] += 1
            self.minute_used[side] = (minute, used + int(res.get("instructions") or 0))
            rec = {"t": t, "sec": sec, "side": side, "script": name, "trigger": trigger, "ok": bool(res.get("ok")),
                   "instructions": res.get("instructions"), "ms": res.get("ms"), "error": res.get("error")}
            if res.get("limit_kind"):
                rec["limit_kind"] = res["limit_kind"]
            if res.get("log"):
                rec["log"] = [str(x)[:200] for x in res["log"][:20]]
            if res.get("limit"):
                d["strikes"] += 1
                rec["event"] = "over_limit"
                rec["strikes"] = d["strikes"]
                self.log.write("scripts", rec)
                if d["strikes"] >= rules.SCRIPT_STRIKES:
                    d["enabled"] = False
                    self.log.write("scripts", {"t": t, "sec": sec, "side": side, "script": name, "event": "disabled"})
                    alarm(name, f"script {name} switched off: over its limits {d['strikes']} times in a row", True)
                out.append({"name": name, "ok": False, "error": res.get("error") or "over its limit", "report": None})
                continue                                  # a void call changes nothing
            d["strikes"] = 0
            if res.get("ok"):
                new = res.get("persist")
                if new is not None:
                    if len(json.dumps(new, default=str).encode("utf-8")) > rules.SCRIPT_PERSIST_BYTES:
                        rec["persist"] = "too_large_dropped"
                    else:
                        d["persist"] = new
                for body in res.get("commands") or []:
                    submit(body, f"script:{name}")
                if res.get("alarm"):
                    alarm(name, str(res["alarm"])[:300], False)
                if res.get("report") is not None:
                    rec["report_chars"] = len(str(res["report"]))
            self.log.write("scripts", rec)
            out.append({"name": name, "ok": bool(res.get("ok")), "error": res.get("error"),
                        "report": str(res["report"]) if res.get("ok") and res.get("report") is not None else None})
        return out
