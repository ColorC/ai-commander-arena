"""Sand-table data for staging (STANDARD.md): the four exercises' replays (T1-T4, Marburg) and the FPC side data of every
commented game (T1-T4, M1, M2, F, G). Inputs come from the staging manifest (raw.events -> the game's a1 folder).

    python -I -X utf8 build_staging_data.py [case ...]        (default: all; exercises get data-*.js, every case fpc-*.json)

Per case, into $ARENA_DATA/video/program/staging/data/:
  <case>-game.json   (exercises) s2_battle_maps.py replay of the game's a1 folder
  data-<case>.js     (exercises) build_data.py bundle: Marburg terrain + replay + Marburg height map (same inputs as D2)
  fpc-<case>.json    fpc_symbology_data.py side data (platforms, morale / readiness / ammo, order queues, wakes)
  build-<case>.log   stdout / stderr of the three tools
Then make_manifest.py writes the paths into manifest.json (`data`, `fpc`)."""
import json
import pathlib
import subprocess
import sys
import os

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[3] / "data")

REPO = pathlib.Path(__file__).resolve().parents[3]
VID = REPO / "tools" / "video"
P = ARENA_DATA / "video/program"
OUT = P / "staging" / "data"
D2 = P / "D2-work"                      # Marburg terrain JSON and height map (the D2 segment's inputs)
EXISTING = {"M1": P / "D2-work/data-marburg-g1.js", "M2": P / "D2-work/data-marburg-g2.js",
            "F": ARENA_DATA / "video/v1/build/data-minden.js", "G": P / "G-work/data-minden-g2.js"}
PY = [sys.executable, "-I", "-X", "utf8"]


def run(cmd, log):
    log.write("$ " + " ".join(str(c) for c in cmd) + "\n")
    log.flush()
    r = subprocess.run([str(c) for c in cmd], cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
    if r.returncode:
        raise SystemExit(f"failed ({r.returncode}): {cmd}; log {log.name}")


def main(only):
    OUT.mkdir(parents=True, exist_ok=True)
    man = json.loads((P / "staging/manifest.json").read_text(encoding="utf-8"))
    done = {}
    for m in man:
        k = m["case"]
        if only and k not in only:
            continue
        a1 = pathlib.Path(m["raw"]["events"]).parent.parent
        rec = next(a1.glob("s2-*/rec.jsonl"))
        logp = OUT / f"build-{k}.log"
        with open(logp, "w", encoding="utf-8") as log:
            if k in EXISTING:
                data = EXISTING[k]
            else:
                game = OUT / f"{k}-game.json"
                data = OUT / f"data-{k}.js"
                run(PY + [REPO / "tools/s2_battle_maps.py", a1, game], log)
                run(PY + [VID / "build_data.py", "--terrain", D2 / "terrain-marburg.json", "--game", game,
                          "--heightmap", D2 / "heightmap", "--out", data], log)
            fpc = OUT / f"fpc-{k}.json"
            wakes = a1 / "logs/wakes.jsonl"
            run(PY + [VID / "fpc_symbology_data.py", "--rec", rec, "--data", data, "--out", fpc]
                + (["--wakes", wakes] if wakes.exists() else []), log)
        done[k] = {"data": str(data).replace("\\", "/"), "fpc": str(fpc).replace("\\", "/"), "log": str(logp).replace("\\", "/")}
        print(json.dumps({k: done[k]}, ensure_ascii=False), flush=True)
    subprocess.run(PY + [VID / "staging/make_manifest.py"], cwd=REPO, check=True)


if __name__ == "__main__":
    main(set(sys.argv[1:]))
