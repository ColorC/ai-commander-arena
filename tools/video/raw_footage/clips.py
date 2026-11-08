"""Raw footage clips: the six game-time stretches cut from the programme, and the FPC autosaves whose turn replays
cover them. A hot-seat autosave is written when a player presses F9 and holds the replay (VCR) that player watched:
everything resolved since that player's previous Orders phase, seen with that player's fog of war (test 11-08: the
Soviet 10:32 save of Marburg game 1 replays 10:03-10:32, the American 10:42 save 10:25-10:42). Only the saves of one
side (SIDE, the NATO side 0) are used, so the replays chain without overlap and the picture keeps one point of view:
for a span [a, b] those whose time is after a, up to and including the first one at or after b. Which side wrote a
save comes from the game's debug log ("(HH:MM:SS) SaveGame() Game.SideNow N").

    python -I -X utf8 clips.py stage <push dir> [clip id ...] [--have <capture dir>[;<capture dir>...]]
                                                  copy the needed saves (read only from the run dirs) to
                                                  <push dir>/rf/<game>-<HHMM>.sav and print the job's Saves list;
                                                  --have: leave out saves already captured whole (result ok in
                                                  that capture's summary.tsv, the last dir that has the tag counts)
    python -I -X utf8 clips.py list [clip id ...] print every clip with its saves, and any stretch no save covers

The "-full" clips are whole games, start to end (END); they go to OUT/full (out_dir) and are paced by the replay's
own speed (build_clip.py). Where the last save comes before the end, that stretch has no original footage at all.
"""
import glob
import json
import os
import re
import shutil
import sys
import pathlib

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[3] / "data")

RUNS = ARENA_DATA.as_posix() + "/runs"
OUT = ARENA_DATA.as_posix() + "/video/program/raw-footage"

SIDE = 0
# The games and stretches to cut come from $ARENA_DATA/video/program/raw-footage/clips.json (not in the repo):
# {"games": {"<game key>": {"run": "<game attempt dir under $ARENA_DATA/runs>", "start": "HH:MM", "name": "<caption>",
#                           "end": "HH:MM",          scenario end (debug log "Game Over" / clock.jsonl phase Ended)
#                           "map_box": [l, t, r, b]  the map inside the captured 1920x1080 window, exclusive (menu bar,
#                                                    white map rim and gray background left out; build_clip.py checks
#                                                    the rim on each clip's first frame)}},
#  "clips": {"<clip id>": {"game": "<game key>", "spans": [["HH:MM", "HH:MM"], ...]}}}   one output video per clip;
# an id ending in "-full" is a whole game. Example: tools/video/examples/raw-footage-clips.example.json
CONF = pathlib.Path(os.environ.get("ARENA_CLIPS") or pathlib.Path(OUT, "clips.json"))
_c = json.loads(CONF.read_text(encoding="utf-8")) if CONF.exists() else {"games": {}, "clips": {}}
GAMES = {k: (RUNS + "/" + g["run"], g["start"], g.get("name", k)) for k, g in _c["games"].items()}
MAP_BOX = {k: tuple(g["map_box"]) for k, g in _c["games"].items() if g.get("map_box")}
END = {k: g["end"] for k, g in _c["games"].items() if g.get("end")}
CLIPS = {k: (c["game"], [tuple(x) for x in c["spans"]]) for k, c in _c["clips"].items()}


def is_full(cid):
    return cid.endswith("-full")


def out_dir(cid):
    return OUT + "/full" if is_full(cid) else OUT


def hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def save_sides(game):
    """{clock minutes: side} of every SaveGame line in the game's debug log."""
    out = {}
    for p in glob.glob(os.path.join(GAMES[game][0], "s2-*", "collected", "*DEBUG*.txt")):
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.search(r"\((\d\d):(\d\d):\d\d\) SaveGame\(\) Game\.SideNow (\d)", line)
                if m:
                    out[int(m.group(1)) * 60 + int(m.group(2))] = int(m.group(3))
    return out


def saves(game, side=SIDE):
    """[(clock minutes of the day, path)] of a game's autosaves written by that side, by time."""
    run = GAMES[game][0]
    sides = save_sides(game)
    out = []
    for p in glob.glob(os.path.join(run, "s2-*", "collected", "*.sav")):
        m = re.search(r"__(\d\d)(\d\d)_hrs\.sav$", p)
        if m:
            t = int(m.group(1)) * 60 + int(m.group(2))
            if sides.get(t) == side:
                out.append((t, p))
    return sorted(out)


def needed(game, a, b):
    """Saves whose replays cover [a, b] (clock minutes), and whether the span's end is covered."""
    out = []
    for t, p in saves(game):
        if t > a:
            out.append((t, p))
            if t >= b:
                return out, True
    return out, False


def tag(game, t):
    return "%s-%02d%02d" % (game, t // 60, t % 60)


def captured_ok(dirs):
    """{save tag: capture dir} of saves captured whole (summary.tsv result ok), the last dir that has a tag wins"""
    out = {}
    for d in dirs:
        p = os.path.join(d, "summary.tsv")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8-sig") as f:
            for line in f.read().splitlines()[1:]:
                c = line.split("\t")
                if len(c) >= 5:
                    if c[4] == "ok" and os.path.exists(os.path.join(d, "frames-%s.tsv" % c[0])):
                        out[c[0]] = d
                    else:
                        out.pop(c[0], None)
    return out


def hhmm(t):
    return "%02d:%02d" % (t // 60, t % 60)


def main(argv):
    have = []
    if "--have" in argv:
        i = argv.index("--have")
        have = [d for d in argv[i + 1].split(";") if d]
        argv = argv[:i] + argv[i + 2:]
    if argv[:1] == ["list"]:
        for cid, (game, parts) in CLIPS.items():
            if argv[1:] and cid not in argv[1:]:
                continue
            for a, b in parts:
                ss, full = needed(game, hm(a), hm(b))
                gap = "" if full else "(no save after %s: %s-%s not covered)" % (hhmm(ss[-1][0]) if ss else a, hhmm(ss[-1][0]) if ss else a, b)
                print(cid, a, b, [tag(game, t) for t, _ in ss], gap)
        return 0
    if argv[:1] == ["stage"] and len(argv) >= 2:
        rf = os.path.join(argv[1], "rf")
        os.makedirs(rf, exist_ok=True)
        done = captured_ok(have)
        names, reused = [], []
        for cid, (game, parts) in CLIPS.items():
            if argv[2:] and cid not in argv[2:]:
                continue
            for a, b in parts:
                for t, p in needed(game, hm(a), hm(b))[0]:
                    n = tag(game, t) + ".sav"
                    if tag(game, t) in done:
                        if tag(game, t) not in reused:
                            reused.append(tag(game, t))
                        continue
                    if n not in names:
                        names.append(n)
                        if not os.path.exists(os.path.join(rf, n)):
                            shutil.copyfile(p, os.path.join(rf, n))
        if reused:
            print("reused: " + " ".join("%s (%s)" % (r, os.path.basename(done[r])) for r in reused), file=sys.stderr)
        print(";".join(names))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
