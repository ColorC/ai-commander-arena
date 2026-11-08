"""P2 smoke helper: watch the referee and stop the game after a number of game minutes (or a wall-clock cap).

    python -I -X utf8 tools/p2_stopper.py <run dir> <game minutes> <wall cap minutes> [referee url] [poll seconds]

Polls GET /admin/state every 15 s or [poll seconds] (deterministic loop, no model in it), POSTs /admin/stop once game
time reaches the mark (0: no mark, the game plays to its end) or the cap runs out, writes <run>/stopper.jsonl and the
last state to <run>/admin-state.json, exits when the referee reports the game ended (or 3 minutes after the stop).
A write that fails (full disk) is skipped, never the end of the watch (P3 rehearsal 10-30: E: filled up, the stopper
died and the run with it).
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

run, minutes, cap = Path(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
url = sys.argv[4] if len(sys.argv) > 4 else "http://127.0.0.1:41010"
poll = float(sys.argv[5]) if len(sys.argv) > 5 else 15.0
token = (run / "secrets" / "admin.token").read_text(encoding="utf-8").strip()


def note(row):
    try:
        with open(run / "stopper.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def call(method, path):
    req = urllib.request.Request(url + path, method=method, data=b"{}" if method == "POST" else None,
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


t0, stopped_at = time.time(), None
while True:
    try:
        st = call("GET", "/admin/state")
    except (OSError, ValueError) as e:
        st = {"error": str(e)}
    note({"t": round(time.time() - t0, 1), "sec": st.get("sec"), "phase": st.get("phase"), "gate": st.get("gate"),
          "ended": st.get("ended"), "error": st.get("error")})
    if "error" not in st:
        try:
            (run / "admin-state.json").write_text(json.dumps(st, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        except OSError:
            pass
    if st.get("ended"):
        break
    over_game = minutes > 0 and (st.get("sec") or 0) >= minutes * 60
    over_wall = time.time() - t0 >= cap * 60
    if stopped_at is None and (over_game or over_wall):
        try:
            note({"t": round(time.time() - t0, 1), "stop": call("POST", "/admin/stop"),
                  "why": "game minutes" if over_game else "wall cap"})
        except (OSError, ValueError) as e:
            note({"stop_error": str(e)})
        stopped_at = time.time()
    if stopped_at is not None and time.time() - stopped_at > 180:
        break
    time.sleep(poll)
