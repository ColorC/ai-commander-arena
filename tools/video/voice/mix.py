"""Put the voice on a rendered clip: every line of the clip's voice sidecar (<shot>.voice.json, written by
make_segment.py) is placed at its subtitle start, levelled to the same loudness, and muxed into the mp4 (video copied).

Run with the tts-lab venv:
    <TTS venv python> -X utf8 mix.py <clip.mp4> <shot.voice.json> <out.mp4> [--allow-missing]
A line without audio stops the mix (it would be a silent subtitle) unless --allow-missing."""
import json
import pathlib
import subprocess
import sys

import numpy as np
import soundfile as sf

from cast import FFMPEG  # noqa: E402
SR = 24000
TARGET_RMS = 10 ** (-20 / 20)


def track(sidecar, allow_missing):
    sc = json.loads(pathlib.Path(sidecar).read_text(encoding="utf-8"))
    out = np.zeros(int((sc["duration"] + 1) * SR), np.float32)
    missing = [ln["text"] for ln in sc["lines"] if not ln.get("audio")]
    if missing and not allow_missing:
        raise SystemExit(f"{len(missing)} lines without audio in {sidecar}: {missing[:3]}")
    for ln in sc["lines"]:
        if not ln.get("audio"):
            continue
        y, sr = sf.read(ln["audio"], dtype="float32")
        assert sr == SR, (ln["audio"], sr)
        rms = float(np.sqrt(np.mean(y ** 2))) or 1e-6
        y = y * min(TARGET_RMS / rms, 0.95 / max(float(np.abs(y).max()), 1e-6))
        a = int(ln["from"] * SR)
        seg = out[a:a + len(y)]
        seg += y[:len(seg)]
    return out[:int(sc["duration"] * SR) + 1], len(sc["lines"]) - len(missing), len(missing)


def mix(clip, sidecar, dst, allow_missing=False):
    if pathlib.Path(dst).exists() and pathlib.Path(dst).samefile(clip):
        raise SystemExit(f"mix would overwrite its own input: {clip}")
    y, n, miss = track(sidecar, allow_missing)
    wav = pathlib.Path(dst).with_suffix(".voice.wav")
    sf.write(wav, np.clip(y, -1, 1), SR, subtype="PCM_16")
    r = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(clip), "-i", str(wav), "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-af", "aresample=48000,pan=stereo|c0=c0|c1=c0", "-c:a", "aac", "-b:a", "192k",
                        "-shortest", str(dst)], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"ffmpeg failed for {clip}: {r.stderr[-800:]}")
    wav.unlink()
    print(f"{dst}  {n} lines voiced, {miss} without audio")


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    mix(a[0], a[1], a[2], "--allow-missing" in sys.argv)
