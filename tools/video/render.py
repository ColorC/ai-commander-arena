"""Render a sand-table shot to mp4: Playwright drives headless Chromium frame by frame, the page encodes with WebCodecs.

    python -I -X utf8 render.py --data <data.js> --shot <shot.js> --out-dir <dir> [--name out.mp4] [--frames N] [--stills-only]

Assets (models, skins, bitmaps the shot and theme name) are read under $ARENA_DATA/video/ (default <repo>/data/video/;
--video overrides); the page gets that folder as ?video=<file URL>.

- Chromium: Playwright channel="chromium" (new headless mode) + file:// page, so WebGL2 runs on the GPU and
  VideoEncoder is available; --allow-file-access-from-files lets the file:// page load its ES modules.
- Every frame: page.evaluate(SANDBOX.renderFrame(i)) then SANDBOX.encodeFrame(i) (VideoFrame of the composite canvas ->
  VideoEncoder avc1, hardware preferred). No screenshots, no ffmpeg. The muxed mp4 (mp4-muxer) comes back through an
  exposed function in 4 MB base64 chunks and is written to <out-dir>/<name>.
- Key frames listed in the shot are saved as PNG from the same composite canvas.
- <out-dir>/render-log.json: GPU, encoder config, per-frame timings, totals, browser PIDs and whether they exited.
--stills-only [--at 75,836] renders only the key frames (or the listed frames) without encoding (quick look).
--range 600-1950 encodes only that span as a clip (starts at t = 0); the 90 frames before it are rendered, not encoded.
"""
import argparse
import asyncio
import base64
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid

from playwright.async_api import async_playwright

HERE = pathlib.Path(__file__).resolve().parent
# data root (README): ARENA_DATA, default <repo>/data; the sand table's assets are under its video/
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or HERE.parents[1] / "data")


def pids_with(tag):
    """PIDs of the Chromium processes started by this run: the ones whose command line has `tag`, plus their descendants."""
    ps = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
          "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True).stdout.decode("utf-8", "replace")
    rows = json.loads(out or "[]")
    found = {r["ProcessId"] for r in rows if tag in (r.get("CommandLine") or "")}
    grew = True
    while grew:
        more = {r["ProcessId"] for r in rows if r["ParentProcessId"] in found} - found
        grew = bool(more)
        found |= more
    return sorted(found)


async def main(a):
    out_dir = pathlib.Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4_path = out_dir / a.name
    tag = "arena-video-run=" + uuid.uuid4().hex[:12]
    log = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "data": a.data, "mp4": str(mp4_path), "frames": []}
    t_start = time.perf_counter()
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chromium", headless=True,
                                          args=["--allow-file-access-from-files", "--ignore-gpu-blocklist",
                                                "--enable-gpu-rasterization", "--" + tag])
        log["browser_version"] = browser.version
        log["browser_pids"] = pids_with(tag)
        try:
            page = await browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
            page.on("console", lambda m: print("[page]", m.text) if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: print("[pageerror]", e))
            fh = None if a.stills_only else open(mp4_path, "wb")

            async def arena_write(b64, last):
                fh.write(base64.b64decode(b64))
                if last:
                    fh.flush()
                return True

            async def arena_save(name, b64):
                (out_dir / name).write_bytes(base64.b64decode(b64))
                print("saved", out_dir / name)
                return True

            await page.expose_function("arenaWrite", arena_write)
            await page.expose_function("arenaSave", arena_save)
            video = pathlib.Path(a.video or ARENA_DATA / "video").resolve().as_uri().rstrip("/") + "/"
            url = (HERE / "sandbox.html").as_uri() + "?data=" + pathlib.Path(a.data).resolve().as_uri() + "&video=" + video
            await page.goto(url)
            await page.wait_for_function("window.SANDBOX_READY || window.SANDBOX_ERROR", timeout=120000)
            err = await page.evaluate("window.SANDBOX_ERROR || null")
            if err:
                raise RuntimeError(err)
            t0 = time.perf_counter()
            shot = pathlib.Path(a.shot).resolve().as_uri()
            opts = {"shot": shot}
            if a.models:                         # else SHOT.models, else the v2 kit folder
                opts["models"] = pathlib.Path(a.models).resolve().as_uri() + "/"
            if a.theme:
                opts["theme"] = pathlib.Path(a.theme).resolve().as_uri()
            if a.narration:
                opts["narration"] = pathlib.Path(a.narration).resolve().as_uri()
            info = await page.evaluate("o => SANDBOX.init(o)", opts)
            log["init_s"] = round(time.perf_counter() - t0, 2)
            log["info"] = info
            print("init", log["init_s"], "s", json.dumps(info, ensure_ascii=False))
            keyframes = {k["frame"]: k["name"] for k in info["keyframes"]}
            n = a.frames or info["frames"]
            order, base, warm = list(range(n)), 0, set()
            if a.range:                          # clip lo..hi-1: warm up from lo-90 (not encoded), clip time starts at lo
                base, hi = (int(x) for x in a.range.split("-"))
                order = list(range(max(0, base - 90), min(hi, n)))
                warm = set(range(max(0, base - 90), base))
                keyframes = {}
                log["range"] = [base, hi]
            if a.stills_only:
                order = [int(x) for x in a.at.split(",")] if a.at else sorted(keyframes)
                for k in order:
                    keyframes.setdefault(k, f"still-{k:04d}.png")
                if a.probe:                      # render a range without saving, print effect counts
                    lo, hi = (int(x) for x in a.probe.split("-"))
                    order, keyframes = list(range(lo, hi)), {}
            else:
                log["encoder"] = await page.evaluate("([hw, bitrate]) => SANDBOX.startEncoder({hw, bitrate})", [a.hw, a.bitrate or None])
                print("encoder", json.dumps(log["encoder"]))
            t0 = time.perf_counter()
            for i in order:
                tf = time.perf_counter()
                # per-frame cap: a wedged page never returns from evaluate (11-06: segment C hung for hours past frame ~7000)
                if a.stills_only or i in warm:
                    r = await asyncio.wait_for(page.evaluate("i => SANDBOX.renderFrame(i)", i), a.frame_timeout)
                else:
                    r = await asyncio.wait_for(page.evaluate("async ([i, b]) => { const r = await SANDBOX.renderFrame(i); await SANDBOX.encodeFrame(i, b); return r; }", [i, base]), a.frame_timeout)
                r["wall_ms"] = round((time.perf_counter() - tf) * 1000, 1)
                if a.probe:
                    print("probe", i, r["clock"], r.get("fx"))
                r["i"] = i
                log["frames"].append(r)
                if i in keyframes:
                    await page.evaluate("n => SANDBOX.snapshot(n)", keyframes[i])
                    log.setdefault("keyframes", []).append(str(out_dir / keyframes[i]))
                if i % 150 == 0:
                    print(f"frame {i}/{n} {r['clock']} render {r['render_ms']} ms overlay {r['overlay_ms']} ms wall {r['wall_ms']} ms")
            log["frames_s"] = round(time.perf_counter() - t0, 2)
            if not a.stills_only:
                t1 = time.perf_counter()
                fin = await page.evaluate("SANDBOX.finish()")
                fh.close()
                log["finish_s"] = round(time.perf_counter() - t1, 2)
                log["finish"] = fin
                print("mp4", mp4_path, fin["bytes"], "bytes")
        finally:
            await browser.close()
    time.sleep(1.0)
    log["browser_pids_after_close"] = [x for x in pids_with(tag) if x in log["browser_pids"]]
    if log["browser_pids_after_close"]:            # close by the exact PIDs this run started, then check again
        for pid in log["browser_pids_after_close"]:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        time.sleep(1.0)
        log["browser_pids_killed"] = log["browser_pids_after_close"]
        log["browser_pids_after_kill"] = [x for x in pids_with(tag) if x in log["browser_pids"]]
    log["total_s"] = round(time.perf_counter() - t_start, 2)
    fr = log["frames"]
    if fr:
        log["avg_wall_ms_per_frame"] = round(sum(f["wall_ms"] for f in fr) / len(fr), 1)
        log["avg_render_ms"] = round(sum(f["render_ms"] for f in fr) / len(fr), 1)
        log["avg_overlay_ms"] = round(sum(f["overlay_ms"] for f in fr) / len(fr), 1)
    log_path = out_dir / ("render-log-stills.json" if a.stills_only else "render-log.json")
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print("log", log_path, "total", log["total_s"], "s; avg/frame", log.get("avg_wall_ms_per_frame"), "ms;",
          "browser pids", log["browser_pids"], "left after close", log["browser_pids_after_close"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--name", default="f34-v0.mp4")
    ap.add_argument("--frames", type=int, default=0)
    ap.add_argument("--frame-timeout", type=float, default=60.0, help="seconds one frame may take before the run aborts")
    ap.add_argument("--bitrate", type=float, default=0, help="encoder bits/s (default: SHOT.bitrate, else 16e6); review copies use less")
    ap.add_argument("--hw", default="prefer-hardware", help="VideoEncoder hardwareAcceleration: prefer-hardware | prefer-software")
    ap.add_argument("--shot", required=True, help="shot module (make_segment.py output or a hand-written one)")
    ap.add_argument("--video", default="", help="asset root (default $ARENA_DATA/video)")
    ap.add_argument("--models", default="", help="folder with the glTF model kit (default: SHOT.models, else $ARENA_DATA/video/models)")
    ap.add_argument("--theme", default="", help="UI theme module (default: theme-default.js, or SHOT.theme)")
    ap.add_argument("--narration", default="", help="narration / subtitle track JSON (see narration-minden.sample.json)")
    ap.add_argument("--stills-only", action="store_true", help="no encoding; render only the key frames (or --at)")
    ap.add_argument("--at", default="", help="with --stills-only: comma-separated frame indices")
    ap.add_argument("--range", default="", help="encode only frames lo-hi (hi exclusive) as a clip; 90 frames before lo are rendered first, not encoded")
    ap.add_argument("--probe",default="", help="with --stills-only: frame range lo-hi rendered without saving (prints effect counts)")
    sys.stdout.reconfigure(line_buffering=True)   # logs redirected to a file stay live (block buffering hid a hang 11-06)
    asyncio.run(main(ap.parse_args()))
