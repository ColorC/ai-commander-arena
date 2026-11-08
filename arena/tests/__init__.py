"""Tests of arena-referee: python -I -X utf8 -m unittest discover -s arena/tests -t .

Many tests build their synthetic world from a real scenario's start data under $ARENA_DATA/scenarios/ (game data,
not in this repository; see README). Without it those test classes are skipped, not failed."""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = pathlib.Path(os.environ.get("ARENA_DATA") or ROOT / "data")
HAVE_SCENARIOS = (DATA / "scenarios" / "alamo-at-soest" / "start.json").is_file()
NO_SCENARIOS = "needs the scenario data in $ARENA_DATA/scenarios/ (game data, not shipped)"
LIBRARY = ROOT / "arena" / "library"
HAVE_MANUALS = any(LIBRARY.glob("FM01*.txt"))
NO_MANUALS = "needs the game manuals as text in arena/library/ (arena/library/fetch.py; not shipped)"
