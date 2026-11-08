"""The fog of war source: the game's own debug log, and each side's contact table built from it.

Line forms (the clock in brackets is the game clock of day):
  (07:31:39) UpdateSpottedStepsAndReport() <unit>: Contact 144, Classified: 2 x Recce, spotted steps 2, steps 3
  (07:33:24) UpdateSpottedStepsAndReport() <unit>: Contact 101, Counter-Battery contact (6 tubes), spotted steps 6, steps 8
  (07:14:00) PlayGame() *Event 00130: Orders, Player 1 Orders Phase
  (07:20:00) LUA>print(...)
The state of a unit is how its enemies see it (tools/fog_check.py): 1 2 3 5 6 7 are "seen", 0 and 4 are not.
A contact is created by the first "seen" line only. The report of a state-4 line lists the unit's true composition
(it is replayed from load), so it is never stored or shown.
"""
import re

from . import rules

STATE_NAMES = {0: "Undetected", 1: "Detected", 2: "Classified", 3: "Identified", 4: "AutoSpot", 5: "RadarOnly",
               6: "SigInt", 7: "EmissionsOnly"}
STATES = {"Undetected": 0, "Detected": 1, "Classified": 2, "Identified": 3, "AutoSpot": 4, "Auto": 4,
          "RadarOnly": 5, "Radar": 5, "SigInt": 6, "EmissionsOnly": 7, "Emissions": 7}
SEEN = {1, 2, 3, 5, 6, 7}
# lines without a state word
PLAIN = (("Counter-Battery", 5), ("Signals intelligence", 6), ("Electronic emissions", 7))

CLOCK = re.compile(r"\((\d+):(\d\d):(\d\d)\)")
CONTACT = re.compile(r"\((\d+):(\d\d):(\d\d)\) UpdateSpottedStepsAndReport\(\) (.+?): Contact (\d+), "
                     r"(?:(\w+): )?(.*?), spotted steps")
ORDERS = re.compile(r"\((\d+):(\d\d):(\d\d)\) PlayGame\(\) \*Event \d+: Orders, Player (\d) Orders Phase")
LUA = re.compile(r"\((\d+):(\d\d):(\d\d)\) LUA>(.*)$")


def _clock(m):
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])


def parse_line(line):
    """-> ("contact", clock, unit, no, state, report) | ("orders", clock, side) | ("lua", clock, text) | None"""
    if "UpdateSpottedStepsAndReport" in line:
        m = CONTACT.search(line)
        if not m:
            return None
        word, report = m[6], m[7]
        if word is not None and word in STATES:
            state = STATES[word]
        elif word is None:
            state = next((s for key, s in PLAIN if report.startswith(key)), -1)
        else:
            state = -1
            report = f"{word}: {report}"
        return ("contact", _clock(m), m[4], int(m[5]), state, report)
    if "Orders Phase" in line:
        m = ORDERS.search(line)
        return ("orders", _clock(m), int(m[4]) - 1) if m else None
    if "LUA>" in line:
        m = LUA.search(line)
        return ("lua", _clock(m), m[4].strip()) if m else None
    return None


def size_of(report):
    """Number of vehicles / items the report claims ("5x vehicle(s)", "2 x Tank and 1 x Recce")."""
    return sum(int(n) for n in re.findall(r"(\d+)\s*x", report)) or 1


class Contact:
    def __init__(self, unit, no):
        self.unit = unit                  # true unit name: never leaves the referee
        self.no = no
        self.state = -1
        self.report = ""
        self.first_seen = self.last_seen = self.last_pos = None
        self.visible = False
        self.reports, self.positions, self.states = set(), set(), set()   # provenance for the fog assertion

    def public(self):
        return {"contact": self.no, "state": self.state, "state_name": STATE_NAMES.get(self.state, "?"),
                "report": self.report, "size": size_of(self.report), "visible": self.visible,
                "first_seen": self.first_seen, "last_seen": self.last_seen, "last_pos": self.last_pos}


class ContactTable:
    """What `viewer` knows about the enemy."""

    def __init__(self, viewer):
        self.viewer = viewer
        self.by_unit, self.by_no = {}, {}

    def update(self, t, unit, no, state, report, pos):
        """Apply one log line. Returns the new_contact event fields for a first or regained sighting."""
        c = self.by_unit.get(unit)
        if state in SEEN:
            fresh = c is None
            regained = c is not None and not c.visible and t - c.last_seen >= rules.CONTACT_REGAIN_SEC
            if fresh:
                c = self.by_unit[unit] = self.by_no[no] = Contact(unit, no)
            c.state, c.report, c.visible = state, report[:300], True
            c.first_seen = t if c.first_seen is None else c.first_seen
            c.last_seen, c.last_pos = t, pos
            c.reports.add(c.report)
            c.states.add(state)
            if pos is not None:
                c.positions.add(pos)
            if fresh or regained:
                return {"contact": no, "state": state, "state_name": STATE_NAMES[state], "report": c.report,
                        "pos": pos, "size": size_of(report), "regained": regained}
        elif c is not None and c.visible:
            c.visible = False
        return None

    def refresh(self, sec, locate):
        """Once a minute: contacts in view are followed at their true position."""
        for c in self.by_unit.values():
            if c.visible:
                pos = locate(c.unit)
                if pos is not None:
                    c.last_pos = pos
                    c.positions.add(pos)
                c.last_seen = sec

    def visible(self):
        return [c for c in self.by_unit.values() if c.visible]

    def all(self):
        return sorted(self.by_unit.values(), key=lambda c: -(c.last_seen or 0))
