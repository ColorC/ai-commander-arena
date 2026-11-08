"""Voice every spoken line of the programme: the intro segments (beat `lines`) and the eight games' commentary
(live-commentary/<case>.md, `Claude：` / `GPT：` lines), each cloned from its character's reference (refs/cast.json,
Qwen3-TTS 1.7B-Base). Lines already in the cache are skipped, so this only voices what is new or was rejected by
asr_check.py.

Run with the tts-lab venv:  <TTS venv python> -X utf8 synth_lines.py
Out: $ARENA_DATA/video/voice/lines/<key>.wav (24 kHz mono, silence trimmed), lines/index.json
{key: {voice, text, spoken, path, dur, attempt}}; the key is voice_cache.key(voice, subtitle text)."""
import json
import pathlib
import re
import sys
import time

import numpy as np
import soundfile as sf
import torch

from cast import CAST, LINES, MODELS, VOICE_OF, spoken

VID = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VID))
import voice_cache  # noqa: E402
import os

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[3] / "data")

INTROS = ["AB-opening-1", "AB-opening-2", "C-intro", "D0-intro-marburg", "E0-intro-minden", "H-outro"]
CASES = ["T1", "T3", "T2", "T4", "M1", "M2", "F", "G"]
LC = ARENA_DATA / "video/program/live-commentary"
INDEX = LINES / "index.json"
BATCH = 6


def collect():
    out = []
    for n in INTROS:
        for b in json.loads((VID / "segments" / f"{n}.json").read_text(encoding="utf-8"))["beats"]:
            out += [(ln["speaker"], ln["text"]) for ln in b.get("lines", [])]
    for c in CASES:
        for ln in (LC / f"{c}.md").read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(Claude|GPT)[：:](.*)$", ln.strip())
            if m:
                out.append((m.group(1), m.group(2).strip()))
    todo = {}
    for who, text in out:
        v = VOICE_OF[who]
        todo.setdefault(voice_cache.key(v, text), (v, text))
    return todo


def trim(y, sr):
    a = np.abs(y)
    thr = max(a.max() * 0.02, 1e-4)
    idx = np.flatnonzero(a > thr)
    if not len(idx):
        return y
    pad = int(sr * 0.06)
    return y[max(idx[0] - pad, 0): idx[-1] + pad]


def plausible(text, dur):
    """a clone that ran away (long babble / silence) or cut short is off the reading speed by a lot"""
    n = len(re.sub(r"[\s，。、！？；：“”‘’（）《》,.!?;:\"'()]", "", text))
    return 2.2 <= n / max(dur, 0.1) <= 9.5


def main():
    cast = json.loads(CAST.read_text(encoding="utf-8"))
    LINES.mkdir(parents=True, exist_ok=True)
    idx = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
    todo = collect()
    pend = {k: vt for k, vt in todo.items() if k not in idx or not pathlib.Path(idx[k]["path"]).exists()}
    print(f"{len(todo)} lines, {len(pend)} to voice", flush=True)
    if not pend:
        return 0
    from qwen_tts import Qwen3TTSModel
    m = Qwen3TTSModel.from_pretrained(str(MODELS / "Qwen3-TTS-12Hz-1.7B-Base"), device_map="cuda:0",
                                      dtype=torch.bfloat16, attn_implementation="sdpa")
    prompts = {v: m.create_voice_clone_prompt(ref_audio=c["ref_audio"], ref_text=c["ref_text"]) for v, c in cast.items()}
    t_start, done, fails = time.perf_counter(), 0, []

    def save():
        tmp = INDEX.with_suffix(".tmp")
        tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=0), encoding="utf-8")
        tmp.replace(INDEX)

    for v in cast:
        items = sorted(((k, t) for k, (vv, t) in pend.items() if vv == v), key=lambda kt: len(kt[1]))
        for i in range(0, len(items), BATCH):
            chunk = items[i:i + BATCH]
            retry = []
            for attempt in range(4):
                group = chunk if attempt == 0 else retry
                if not group:
                    break
                texts = [spoken(t) for _, t in group]
                seed = 1107 + attempt * 97 + max(idx.get(k, {}).get("attempt", 0) for k, _ in group) * 13
                torch.manual_seed(seed)
                wavs, sr = m.generate_voice_clone(text=texts, language=["Chinese"] * len(texts),
                                                  voice_clone_prompt=prompts[v] * len(texts),
                                                  max_new_tokens=max(len(t) for t in texts) * 7 + 80)
                retry = []
                for (k, t), sp, w in zip(group, texts, wavs):
                    y = trim(np.asarray(w, dtype=np.float32), sr)
                    dur = len(y) / sr
                    if not plausible(sp, dur) and attempt < 3:
                        retry.append((k, t)); continue
                    p = LINES / f"{k}.wav"
                    sf.write(p, y, sr, subtype="PCM_16")
                    idx[k] = {"voice": v, "text": t, "spoken": sp, "path": str(p).replace("\\", "/"),
                              "dur": round(dur, 3), "attempt": idx.get(k, {}).get("attempt", 0) + attempt + 1,
                              "plausible": plausible(sp, dur)}
                    if not idx[k]["plausible"]:
                        fails.append(k)
                    done += 1
            save()
            el = time.perf_counter() - t_start
            print(f"[{v}] {done}/{len(pend)} lines, {el / 60:.1f} min", flush=True)
    print(f"done {done}, still implausible after retries: {len(fails)} {fails}", flush=True)
    print("index", INDEX)
    return 0


if __name__ == "__main__":
    sys.exit(main())
