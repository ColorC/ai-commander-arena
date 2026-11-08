"""Contact sheet of converted HOI4 glbs (headless Chromium, closed by exact PID afterwards).

    python -I -X utf8 preview.py --dir $ARENA_DATA/video/hoi4-assets/glb --names a,b,c --anim attack --t 0.4 --out sheet.png
Prints per-model info (size in model units, clips, key nodes) and writes <out>.json next to the PNG.
"""
import argparse
import asyncio
import json
import pathlib
import subprocess
import time
import uuid

from playwright.async_api import async_playwright

HERE = pathlib.Path(__file__).resolve().parent


def pids_with(tag):
    ps = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
          "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress")
    rows = json.loads(subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True).stdout.decode("utf-8", "replace") or "[]")
    found = {r["ProcessId"] for r in rows if tag in (r.get("CommandLine") or "")}
    while True:
        more = {r["ProcessId"] for r in rows if r["ParentProcessId"] in found} - found
        if not more:
            return sorted(found)
        found |= more


async def main(a):
    names = a.names.split(",")
    cols = min(a.cols, len(names))
    tag = "arena-preview=" + uuid.uuid4().hex[:12]
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chromium", headless=True, args=["--allow-file-access-from-files", "--ignore-gpu-blocklist", "--" + tag])
        pids = pids_with(tag)
        try:
            pg = await b.new_page(viewport={"width": 320 * cols, "height": 260 * ((len(names) + cols - 1) // cols)})
            pg.on("console", lambda m: print("[page]", m.text) if m.type in ("error", "warning") else None)
            pg.on("pageerror", lambda e: print("[pageerror]", e))
            url = (HERE / "preview.html").as_uri() + "?dir=" + pathlib.Path(a.dir).resolve().as_uri() + "/&names=" + a.names + f"&anim={a.anim}&t={a.t}&cols={cols}"
            await pg.goto(url)
            await pg.wait_for_function("window.PREVIEW", timeout=60000)
            info = await pg.evaluate("window.PREVIEW")
            await pg.screenshot(path=a.out)
        finally:
            await b.close()
    time.sleep(1.0)
    left = [x for x in pids_with(tag) if x in pids]
    for pid in left:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    pathlib.Path(a.out).with_suffix(".json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    for r in info:
        print(json.dumps(r))
    print("sheet", a.out, "browser pids", pids, "left after close", left, "after kill", [x for x in pids_with(tag) if x in pids] if left else [])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--names", required=True)
    ap.add_argument("--anim", default="idle")
    ap.add_argument("--t", type=float, default=0.0)
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--out", required=True)
    asyncio.run(main(ap.parse_args()))
