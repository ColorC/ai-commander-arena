"""Read the replay log inside an FPC autosave (.sav is a zip; member FPC21.$$$).

FPC21.$$$ is a UTF-16LE stream of records, each a uint32 character count followed by that many characters:
"<Kind>\t<Field>\t<value>", Kind is Game / Unit / Map. Unit values are "<unit index>,<value>".
Game TimeNow (game seconds) is written before the changes made at that second. A save holds several game
segments (each restarts at TimeNow 0); the one that reaches the latest TimeNow is the current game.

python -I tools/fpc_save.py <file.sav> [field ...]   prints the state of every unit at the end of the save.
"""
import re
import struct
import sys
import zipfile

KINDS = ("Game\t", "Unit\t", "Map\t")


def records(data):
    """Yield (kind, field, value) for every record; skips bytes that do not start a record."""
    p, n = 0, len(data)
    while p + 4 <= n:
        (count,) = struct.unpack_from("<I", data, p)
        end = p + 4 + 2 * count
        if 0 < count < 100000 and end <= n:
            text = data[p + 4:end].decode("utf-16-le", "replace")
            if text.startswith(KINDS):
                parts = text.split("\t", 2)
                if len(parts) == 3:
                    yield parts[0], parts[1], parts[2]
                    p = end
                    continue
        p += 2


def replay(path):
    """Changes of the current game as a list of (game second, kind, field, unit index or None, value)."""
    data = zipfile.ZipFile(path).read("FPC21.$$$")
    recs = list(records(data))
    starts = [i for i, r in enumerate(recs) if r[:2] == ("Game", "TimeNow") and r[2].strip() == "0"] or [0]
    bounds = list(zip(starts, starts[1:] + [len(recs)]))

    def last_time(b):
        times = [int(m.group()) for k, f, v in recs[b[0]:b[1]] if (k, f) == ("Game", "TimeNow")
                 for m in [re.match(r"\d+", v)] if m]
        return max(times, default=0)

    a, b = max(bounds, key=last_time)  # the segment that reaches furthest is the current game
    out, t = [], 0
    for kind, field, value in recs[a:b]:
        if (kind, field) == ("Game", "TimeNow"):
            m = re.match(r"\d+", value)
            t = int(m.group()) if m else t
            continue
        idx = None
        if kind == "Unit":
            m = re.match(r"(\d+),(.*)", value, re.S)
            if not m:
                continue
            idx, value = int(m.group(1)), m.group(2)
        out.append((t, kind, field, idx, value))
    return out


def state_at(changes, t_max=None):
    """Latest value per (unit index, field) at game second t_max (default: end of the save)."""
    st = {}
    for t, kind, field, idx, value in changes:
        if t_max is not None and t > t_max:
            break
        if kind == "Unit":
            st.setdefault(idx, {})[field] = value
    return st


def end_time(changes):
    return max((c[0] for c in changes), default=0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ch = replay(sys.argv[1])
    fields = sys.argv[2:] or ["Location", "Status", "Spotted", "SpottedSteps", "SpottedMarker", "EnemySpottingReport"]
    print("end", end_time(ch))
    for idx, f in sorted(state_at(ch).items()):
        print(idx, {k: f.get(k) for k in fields})
