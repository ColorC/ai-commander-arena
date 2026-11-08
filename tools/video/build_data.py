"""Bundle the renderer's inputs into one script file the file:// page can load (no fetch needed).

    python -I -X utf8 build_data.py --terrain <terrain.json> --game <formal-2.json> --heightmap <dir with heightmap.bin/.json>
                                    --out <data.js>

terrain.json / formal-2.json come from tools/s2_battle_maps.py (fields described in its header: p / ev / sc / vc ...);
the height map from dem_to_heightmap.mjs. Output: window.ARENA = {terrain, game, hm: {meta..., b64}}.
"""
import argparse
import base64
import json
import os


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--terrain", required=True)
    ap.add_argument("--game", required=True)
    ap.add_argument("--heightmap", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    terrain = json.load(open(a.terrain, encoding="utf-8"))
    game = json.load(open(a.game, encoding="utf-8"))
    game.pop("chk", None)
    meta = json.load(open(os.path.join(a.heightmap, "heightmap.json"), encoding="utf-8"))
    raw = open(os.path.join(a.heightmap, "heightmap.bin"), "rb").read()
    assert len(raw) == meta["width"] * meta["height"] * 4, (len(raw), meta["width"], meta["height"])
    meta["b64"] = base64.b64encode(raw).decode("ascii")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("window.ARENA = ")
        json.dump({"terrain": terrain, "game": game, "hm": meta}, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    print(a.out, os.path.getsize(a.out), "bytes")


if __name__ == "__main__":
    main()
