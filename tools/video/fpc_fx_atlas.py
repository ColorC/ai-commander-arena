"""Pack the original FPC effect sequences (05-effects-animations/frames/<name>/*.png, unpacked from the game's
Common/Animations zips) into one atlas PNG per sequence for fpc-symbology.js: frames in reading order on a square grid,
native frame size, nothing rescaled or recoloured.

    python -I -X utf8 fpc_fx_atlas.py [--src <frames dir>] [--out <dir>]

Writes <out>/<name>.png and <out>/atlas.json {name: {w, h, n, cols}}.
"""
import argparse
import glob
import json
import math
import os

from PIL import Image
import pathlib

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[2] / "data")

SRC = ARENA_DATA.as_posix() + "/video/fpc-original/05-effects-animations/frames"
OUT = ARENA_DATA.as_posix() + "/video/fpc-symbology/assets/fx"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    meta = {}
    for d in sorted(glob.glob(os.path.join(a.src, "*"))):
        fs = sorted(glob.glob(os.path.join(d, "*.png")))
        if not fs:
            continue
        ims = [Image.open(f).convert("RGBA") for f in fs]
        w, h = ims[0].size
        cols = math.ceil(math.sqrt(len(ims)))
        rows = math.ceil(len(ims) / cols)
        sheet = Image.new("RGBA", (cols * w, rows * h), (0, 0, 0, 0))
        for k, im in enumerate(ims):
            if im.size != (w, h):
                im = im.resize((w, h))
            sheet.paste(im, ((k % cols) * w, (k // cols) * h))
        name = os.path.basename(d)
        sheet.save(os.path.join(a.out, name + ".png"), optimize=True)
        meta[name] = {"w": w, "h": h, "n": len(ims), "cols": cols}
        print(name, len(ims), (w, h), sheet.size)
    json.dump(meta, open(os.path.join(a.out, "atlas.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
