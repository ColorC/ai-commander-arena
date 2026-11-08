"""Tighten and speed up the voiced lines (11-08, owner: "慢的离谱…说话时见到标点符号的停顿长"):
1. every silence inside a line longer than cast.PAUSE_MAX is cut down to PAUSE_MAX (the TTS pauses 0.24 s at a median
   comma, up to 0.9 s);
2. the whole line is played cast.TEMPO times faster, pitch kept (ffmpeg rubberband).
Each line gets lines/t<tempo>p<pause>/<key>.wav and its index entry a "fast" {tempo, pause, path, dur}, which
voice_cache serves.

Run with the tts-lab venv:  <TTS venv python> -X utf8 speed_lines.py"""
import json
import pathlib
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import soundfile as sf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cast import FFMPEG, LINES, PAUSE_MAX, TEMPO  # noqa: E402

INDEX = LINES / "index.json"
FRAME = 0.01


def tighten(y, sr):
    """cut inner silences (10 ms frames 40 dB under the line's loudest frame) down to PAUSE_MAX"""
    f = int(sr * FRAME)
    n = len(y) // f
    e = np.sqrt((y[:n * f].reshape(n, f) ** 2).mean(1) + 1e-12)
    sil = e < e.max() * 10 ** (-40 / 20)
    keep = np.ones(len(y), bool)
    i, cap = 0, int(round(PAUSE_MAX / FRAME))
    while i < n:
        if sil[i]:
            j = i
            while j < n and sil[j]:
                j += 1
            if i > 0 and j < n and j - i > cap:            # inner pause only; ends were trimmed at synthesis
                a = i + cap // 2                            # keep the pause's start and end, drop its middle
                b = j - (cap - cap // 2)
                keep[a * f:b * f] = False
            i = j
        else:
            i += 1
    return y[keep]


def one(k, e, out):
    dst = out / f"{k}.wav"
    if not dst.exists() or dst.stat().st_mtime < pathlib.Path(e["path"]).stat().st_mtime:
        y, sr = sf.read(e["path"], dtype="float32")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=out) as tf:
            tmp = pathlib.Path(tf.name)
        sf.write(tmp, tighten(y, sr), sr, subtype="PCM_16")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(tmp), "-af",
                        f"rubberband=tempo={TEMPO}:pitchq=quality:window=short", "-ar", str(sr), "-ac", "1",
                        "-c:a", "pcm_s16le", str(dst)], check=True)
        tmp.unlink()
    info = sf.info(dst)
    return k, {"tempo": TEMPO, "pause": PAUSE_MAX, "path": dst.as_posix(), "dur": round(info.frames / info.samplerate, 3)}


def main():
    idx = json.loads(INDEX.read_text(encoding="utf-8"))
    out = LINES / f"t{round(TEMPO * 100)}p{round(PAUSE_MAX * 100)}"
    out.mkdir(exist_ok=True)
    live = {k: e for k, e in idx.items() if pathlib.Path(e["path"]).exists()}
    with ThreadPoolExecutor(6) as ex:
        for k, fast in ex.map(lambda kv: one(kv[0], kv[1], out), live.items()):
            idx[k]["fast"] = fast
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(INDEX)
    a = sum(e["dur"] for e in live.values())
    b = sum(idx[k]["fast"]["dur"] for k in live)
    print(f"{len(live)} lines, pauses <= {PAUSE_MAX}s, {TEMPO}x: {a / 60:.1f} -> {b / 60:.1f} min of speech, {out}")


if __name__ == "__main__":
    main()
