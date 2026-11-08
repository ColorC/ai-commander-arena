"""Thinking time per side (materials/10 2.6, changed 2026-11-04): real seconds the side's agents have had a turn open.

Only a count the agents see, no pool and no limit. Season 2's attention energy never ran low (lowest 1653 of 2700,
every turn ended by the commander itself) yet DeepSeek cut its own subscriptions "to save energy", so the budget went
and the figure stays. The commander's observe seconds and the subagents' seconds are kept apart; prematch time is
counted on its own and is not part of the game total.
"""
KINDS = ("commander", "observe", "subagents")


class ThinkTime:
    def __init__(self):
        self.sec = {s: dict.fromkeys(KINDS, 0.0) for s in (0, 1)}
        self.prematch = {0: 0.0, 1: 0.0}
        self.wakes = {0: 0, 1: 0}

    def add(self, side, kind, seconds, prematch=False):
        if seconds <= 0:
            return
        if prematch:
            self.prematch[side] += seconds
        else:
            self.sec[side][kind] += seconds

    def wake(self, side):
        self.wakes[side] += 1

    def total(self, side):
        return sum(self.sec[side].values())

    def view(self, side):
        out = {"total_sec": round(self.total(side), 1)}
        out.update({f"{k}_sec": round(v, 1) for k, v in self.sec[side].items()})
        out.update({"prematch_sec": round(self.prematch[side], 1), "wakes": self.wakes[side]})
        return out
