"""Key stills for whole games: compile every segment with --keys (one key frame per shot, 1 s into its last line), render
the stills headless, and turn them into the review page's JPEGs (full 1920 and a 960 thumbnail); the PNGs are dropped after
that (render again any time).

    python -I -X utf8 render_all.py <case | case:seg,seg> [...] [--site <dir>] [--video]

--video: the segments as mp4s (<site>/video/<case>-<seg>.mp4) instead of key stills.

Writes <site>/img/<case>-<seg>-<shot>.jpg and <site>/thumb/... (default site: $ARENA_DATA/video/program/
site-staging-review), the shot modules under $ARENA_DATA/video/program/staging/keys/<case>/, the render logs
next to them, and one line per segment to <site>/render-all.log."""
import json
import pathlib
import subprocess
import sys
import time

from PIL import Image
import os

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[3] / "data")

HERE = pathlib.Path(__file__).resolve().parent
VID = HERE.parent
ST = ARENA_DATA / "video/program/staging"
KEYS = ST / "keys"


def segments(case):
    return [s["seg"] for s in json.loads((ST / f"{case}.json").read_text(encoding="utf-8"))["segments"]]


def one(case, seg, data, site, log):
    out = KEYS / case
    t0 = time.time()
    r = subprocess.run([sys.executable, "-I", "-X", "utf8", str(HERE / "compile_staging.py"), case, str(seg), "--keys", "--out", str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        log.write(f"{case}-{seg:02d} compile failed: {r.stderr[-600:]}\n"); return False
    sid = f"{case}-{seg:02d}"
    rd = out / f"render-{sid}"
    with open(out / f"render-{sid}.log", "w", encoding="utf-8") as f:
        r = subprocess.run([sys.executable, "-I", "-X", "utf8", str(VID / "render.py"), "--data", data, "--shot", str(out / f"shot-{sid}.js"),
                            "--out-dir", str(rd), "--stills-only"], stdout=f, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    pngs = sorted(rd.glob(f"{sid}-*.png"))
    if r.returncode or not pngs:
        log.write(f"{sid} render failed (exit {r.returncode}), log {out / f'render-{sid}.log'}\n"); return False
    for p in pngs:
        im = Image.open(p).convert("RGB")
        im.save(site / "img" / (p.stem + ".jpg"), quality=86)
        im.resize((960, 540), Image.LANCZOS).save(site / "thumb" / (p.stem + ".jpg"), quality=82)
        p.unlink()
    log.write(f"{sid} ok {len(pngs)} stills {time.time() - t0:.0f}s\n"); log.flush()
    return True


def video(case, seg, data, site, log):
    """The whole segment as an mp4 (subtitles, no voice yet) -> <site>/video/<case>-<seg>.mp4."""
    out = KEYS / case
    t0 = time.time()
    r = subprocess.run([sys.executable, "-I", "-X", "utf8", str(HERE / "compile_staging.py"), case, str(seg), "--out", str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        log.write(f"{case}-{seg:02d} compile failed: {r.stderr[-600:]}\n"); return False
    sid = f"{case}-{seg:02d}"
    rd = out / f"video-{sid}"
    with open(out / f"video-{sid}.log", "w", encoding="utf-8") as f:
        r = subprocess.run([sys.executable, "-I", "-X", "utf8", str(VID / "render.py"), "--data", data, "--shot", str(out / f"shot-{sid}.js"),
                            "--out-dir", str(rd), "--name", f"{sid}.mp4", "--bitrate", "3e6"], stdout=f, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    mp4 = rd / f"{sid}.mp4"
    if r.returncode or not mp4.exists():
        log.write(f"{sid} video failed (exit {r.returncode}), log {out / f'video-{sid}.log'}\n"); return False
    (site / "video").mkdir(exist_ok=True)
    mp4.replace(site / "video" / mp4.name)
    log.write(f"{sid} video ok {(site / 'video' / mp4.name).stat().st_size / 1e6:.1f} MB {time.time() - t0:.0f}s\n"); log.flush()
    return True


def main(argv):
    site = pathlib.Path(argv[argv.index("--site") + 1] if "--site" in argv else ARENA_DATA.as_posix() + "/video/program/site-staging-review")
    args = [a for k, a in enumerate(argv) if not a.startswith("--") and (k == 0 or argv[k - 1] != "--site")]
    (site / "img").mkdir(parents=True, exist_ok=True); (site / "thumb").mkdir(exist_ok=True)
    man = {m["case"]: m for m in json.loads((ST / "manifest.json").read_text(encoding="utf-8"))}
    with open(site / "render-all.log", "a", encoding="utf-8") as log:
        log.write(f"== {time.strftime('%m-%d %H:%M')} {' '.join(args)}\n")
        bad = 0
        for a in args:
            case, _, segs = a.partition(":")
            for seg in ([int(s) for s in segs.split(",")] if segs else segments(case)):
                bad += not (video if "--video" in argv else one)(case, seg, man[case]["data"], site, log)
        log.write(f"done, {bad} failed\n")
    print(site / "render-all.log", "failed", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
