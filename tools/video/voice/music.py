"""背景音乐：按章节铺曲（PLAN，章节内按比例切换），
曲子去掉首尾静音后循环、接缝交叉淡化，每段首尾淡入淡出；人声出现时音乐自动压低（慢释放，句间短停顿不抽动）。
曲目文件放 $ARENA_DATA/video/music/tracks/<stem>.wav（48 kHz，自备、按各自授权使用，不随本仓分发）；
铺曲表 $ARENA_DATA/video/music/plan.json，格式见下方 PLAN 注释。

Run with the tts-lab venv after assemble.py:
    <TTS venv python> -X utf8 music.py
In: v1108-voiced/programme-v1108.mp4, chapters.json.  Out: v1108-voiced/music-bed.wav, programme-v1108-music.mp4,
chapters-bilibili.txt（B 站章节格式）。"""
import json
import pathlib
import subprocess

import numpy as np
import soundfile as sf

from cast import FFMPEG
import os

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[3] / "data")

OUT = ARENA_DATA / "video/program/v1108-voiced"
TR = ARENA_DATA / "video/music/tracks"
SR = 48000
BED_DB, DUCK_DB = -25.0, -34.0        # music RMS in the gaps / under speech (voice lines are levelled to -20 dBFS RMS)
XF, FADE = 3.0, 1.5                   # loop crossfade, cue fade in / out (s)

# chapter item -> [(fraction of the chapter where the cue starts, track file stem)], read from plan.json, e.g.
#   {"opening": [[0, "theme-a"]], "game-1": [[0, "tension-b"], [0.48, "battle-c"]], "credits": [[0, "theme-a"]]}
# the item names are the chapters of assemble.py's chapters.json
PLAN_FILE = ARENA_DATA / "video/music/plan.json"
PLAN = {k: [tuple(c) for c in v] for k, v in json.loads(PLAN_FILE.read_text(encoding="utf-8")).items()}     if PLAN_FILE.exists() else {}
_cache = {}


def track(stem):
    if stem not in _cache:
        y, sr = sf.read(TR / f"{stem}.wav", dtype="float32", always_2d=True)
        assert sr == SR, (stem, sr)
        e = np.abs(y).max(1)
        nz = np.nonzero(e > 10 ** (-50 / 20))[0]
        y = y[nz[0]:nz[-1] + 1]
        _cache[stem] = y / (float(np.sqrt(np.mean(y ** 2))) or 1e-6)      # unit RMS
    return _cache[stem]


def fill(stem, n):
    y, x = track(stem), int(XF * SR)
    out = y.copy()
    while len(out) < n:
        ramp = np.linspace(0, 1, x, dtype=np.float32)[:, None]
        out = np.concatenate([out[:-x], out[-x:] * (1 - ramp) + y[:x] * ramp, y[x:]])
    return out[:n]


def voice_env(prog):
    """speech activity of the programme's (voice-only) audio, 100 frames/s, smoothed: fast attack, slow release"""
    r = subprocess.run([FFMPEG, "-v", "error", "-i", str(prog), "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                       capture_output=True, check=True)
    v = np.frombuffer(r.stdout, np.float32)
    n = len(v) // 80
    e = np.sqrt((v[:n * 80].reshape(n, 80) ** 2).mean(1))
    act = (e > 10 ** (-38 / 20)).astype(np.float32)
    g = np.zeros(n, np.float32)
    a, rel = 1 - np.exp(-1 / 8), 1 - np.exp(-1 / 90)        # ~80 ms attack, ~0.9 s release
    s, hold = 0.0, 0
    for i in range(n):
        if act[i]:
            hold = 40                                          # 0.4 s hold bridges the pauses between phrases
        tgt = 1.0 if hold > 0 else 0.0
        hold -= 1
        s += (a if tgt > s else rel) * (tgt - s)
        g[i] = s
    return g


def main():
    prog = OUT / "programme-v1108.mp4"
    spans = json.loads((OUT / "chapters.json").read_text(encoding="utf-8"))
    total = spans[-1]["end"]
    env = voice_env(prog)
    cues = []
    for sp in spans:
        L = sp["end"] - sp["start"]
        plan = PLAN[sp["item"]]
        for k, (f, stem) in enumerate(plan):
            a = sp["start"] + f * L
            b = sp["start"] + (plan[k + 1][0] * L if k + 1 < len(plan) else L)
            cues.append((a, b, stem))
    bed = OUT / "music-bed.wav"
    with sf.SoundFile(bed, "w", SR, 2, "PCM_16") as w:
        for a, b, stem in cues:
            i0, i1 = int(round(a * SR)), int(round(b * SR))
            y = fill(stem, i1 - i0)
            n = len(y)
            fr = min(int(FADE * SR), n // 2)
            ramp = np.ones(n, np.float32)
            ramp[:fr] = np.linspace(0, 1, fr)
            ramp[-fr:] = np.minimum(ramp[-fr:], np.linspace(1, 0, fr))
            t = np.arange(n) / SR + a
            duck = np.interp(t * 100, np.arange(len(env)), env).astype(np.float32)
            db = BED_DB + (DUCK_DB - BED_DB) * duck
            gain = (10 ** (db / 20)) * ramp
            w.write(np.clip(y * gain[:, None], -1, 1))
            print(f"{int(a // 60)}:{a % 60:04.1f}-{int(b // 60)}:{b % 60:04.1f}  {stem}")
    dst = OUT / "programme-v1108-music.mp4"
    r = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(prog), "-i", str(bed), "-filter_complex",
                        "[0:a]aresample=48000[v];[v][1:a]amix=inputs=2:normalize=0:duration=first[m]",
                        "-map", "0:v", "-map", "[m]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                        str(dst)], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(r.stderr[-800:])
    titles = json.loads((OUT / "chapters-titles.json").read_text(encoding="utf-8"))
    bl = [f"{int(s['start'] // 60):02d}:{int(s['start'] % 60):02d} {titles[s['item']]}" for s in spans]
    (OUT / "chapters-bilibili.txt").write_text("\n".join(bl) + "\n", encoding="utf-8")
    print(f"total {total / 60:.1f} min\n{dst}\n{bed}\n{OUT / 'chapters-bilibili.txt'}")


if __name__ == "__main__":
    main()
