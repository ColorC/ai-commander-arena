"""Pixel-compare two folders of stills rendered from the same shot (fpc-symbology "off = unchanged" check): every
PNG name present in both is compared; prints and writes the per-image count of differing pixels and the max channel
difference.

    python -I -X utf8 fpc_off_check.py <dir A> <dir B> [--out <json>]
"""
import argparse
import glob
import json
import os

from PIL import Image, ImageChops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    rows = []
    for fa in sorted(glob.glob(os.path.join(a.a, "*.png"))):
        fb = os.path.join(a.b, os.path.basename(fa))
        if not os.path.exists(fb):
            continue
        x, y = Image.open(fa).convert("RGB"), Image.open(fb).convert("RGB")
        d = ImageChops.difference(x, y)
        mx = max(hi for lo, hi in d.getextrema())
        r, g, b = d.split()
        diff_mask = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda q: 255 if q else 0)
        row = {"image": os.path.basename(fa), "size": list(x.size), "same_size": x.size == y.size,
               "pixels_differing": diff_mask.histogram()[255], "max_channel_diff": mx}
        rows.append(row)
        print(row)
    if a.out:
        json.dump({"a": a.a, "b": a.b, "images": rows, "identical": all(r["pixels_differing"] == 0 for r in rows)},
                  open(a.out, "w"), indent=1)
        print(a.out)


if __name__ == "__main__":
    main()
