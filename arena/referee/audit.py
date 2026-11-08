"""Audit of the game's own Lua input log (materials/10 2.4): every `LUA>` line must be explained by the referee.

Allowed input is only what the relay sends: the arena_* helpers it knows (ALLOWED; the p0_* names are the first
probes, found in the recorded sample game), possibly wrapped in print(). `arena_release([[...release-<batch_id>.lua]])` must name a batch the referee issued, once per
time it was sent. Any other identifier outside string literals (SetUnitOrders, AddUnit, KillUnit, ...) is a call the
referee did not release: audit_mismatch. At the end every issued batch must also have shown up in the log.
"""
import re

ALLOWED = {"print", "arena_release", "arena_f9", "arena_state_now", "arena_click", "arena_close", "arena_shot",
           "arena_snapshot", "arena_windows", "p0_click", "p0_close", "p0_shot"}
_STRINGS = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|\[(=*)\[.*?\]\1\]', re.S)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_BATCH = re.compile(r"release-([A-Za-z0-9_-]+)\.lua")


class Audit:
    def __init__(self, log):
        self.log = log
        self.issued = {}          # batch_id -> {"sent": n, "called": n, "result": bool, "cmds": set}
        self.mismatches = []
        self.matched = 0
        self.lines = 0

    def _bad(self, t, kind, **f):
        rec = {"t": t, "kind": "audit_mismatch", "why": kind, **f}
        self.mismatches.append(rec)
        self.log.write("audit", rec)

    def on_issue(self, batch):
        self.issued[batch["batch_id"]] = {"batch": batch, "called": 0, "result": None, "cmds": set(batch["cmd_ids"])}

    def on_result(self, res, t):
        a = self.issued.get(res.get("batch_id"))
        if a is None:
            return self._bad(t, "result_for_unknown_batch", batch_id=res.get("batch_id"))
        a["result"] = True
        got = {r.get("cmd_id") for r in res.get("results") or []}
        if got != a["cmds"]:
            self._bad(t, "result_cmds_differ", batch_id=res["batch_id"], extra=sorted(got - a["cmds"]),
                      missing=sorted(a["cmds"] - got))

    def on_lua_line(self, sec, text):
        self.lines += 1
        code = _STRINGS.sub('""', text)
        bad = sorted({i for i in _IDENT.findall(code) if i not in ALLOWED})
        if bad:
            return self._bad(sec, "unauthorized_call", line=text[:300], names=bad)
        if "arena_release" not in code:
            self.matched += 1
            return
        m = _BATCH.search(text)
        a = self.issued.get(m[1]) if m else None
        if a is None:
            return self._bad(sec, "release_of_unknown_batch", line=text[:300])
        a["called"] += 1
        if a["called"] > a["batch"]["sent"]:
            return self._bad(sec, "release_called_more_often_than_sent", batch_id=m[1], sent=a["batch"]["sent"],
                             called=a["called"])
        self.matched += 1
        self.log.write("audit", {"t": sec, "kind": "match", "batch_id": m[1]})

    def finalize(self, sec):
        for bid, a in self.issued.items():
            if a["result"] and not a["called"]:
                self._bad(sec, "released_but_never_logged", batch_id=bid)
            elif not a["result"]:
                self._bad(sec, "issued_without_result", batch_id=bid)
        return self.summary()

    def summary(self):
        return {"lua_lines": self.lines, "matched": self.matched, "mismatches": len(self.mismatches),
                "batches": len(self.issued), "detail": self.mismatches[:50]}
