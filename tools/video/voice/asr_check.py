"""Listen to every voiced line with Whisper (faster-whisper medium) and reject the ones that do not say their text:
character error rate over the Chinese characters (digits / Latin are read in too many valid ways to compare).
A rejected line's wav becomes <key>.bad.wav, so synth_lines.py voices it again with another seed.

Run with the tts-lab ASR venv:  <ASR venv python> -X utf8 asr_check.py [--max-cer 0.25]
Out: lines/asr.json {key: {cer, heard}}, rejected lines listed on stdout."""
import json
import pathlib
import re
import sys
import wave

import numpy as np
import os

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[3] / "data")

# CTranslate2 needs the CUDA 12 cuBLAS / cuDNN DLLs; the TTS venv's torch ships them
TORCH_LIB = os.environ.get("TORCH_LIB", "")   # e.g. <tts venv>/Lib/site-packages/torch/lib
if TORCH_LIB and pathlib.Path(TORCH_LIB).exists():
    import os
    os.add_dll_directory(TORCH_LIB)
    os.environ["PATH"] = TORCH_LIB + os.pathsep + os.environ.get("PATH", "")

ROOT = ARENA_DATA / "video/voice/lines"
INDEX = ROOT / "index.json"
ASR = ROOT / "asr.json"
HAN = re.compile(r"[\u4e00-\u9fff]")


NUM = re.compile(r"[零〇一二两三四五六七八九十百千万点]")


def load16k(p):
    """the wav as 16 kHz float samples (this venv's PyAV is too new for faster-whisper's own decoder)"""
    with wave.open(str(p)) as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        y = np.frombuffer(w.readframes(n), np.int16).astype(np.float32).reshape(-1, ch).mean(axis=1) / 32768
    t = np.arange(int(len(y) * 16000 / sr)) * sr / 16000
    return np.interp(t, np.arange(len(y)), y).astype(np.float32)


def han(s):
    # numbers come back as digits or as characters at Whisper's whim: both sides drop the numeral characters
    return NUM.sub("", "".join(HAN.findall(s)))


def cer(ref, hyp):
    r, h = han(ref), han(hyp)
    if not r:
        return 0.0
    d = list(range(len(h) + 1))
    for i, a in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, b in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (a != b))
    return d[len(h)] / len(r)


def main(argv):
    max_cer = float(argv[argv.index("--max-cer") + 1]) if "--max-cer" in argv else 0.25
    from faster_whisper import WhisperModel
    try:
        m = WhisperModel("medium", device="cuda", compute_type="float16")
    except Exception as e:                       # no CUDA runtime for CTranslate2 here
        print("cuda unavailable, cpu:", e, flush=True)
        m = WhisperModel("small", device="cpu", compute_type="int8")
    idx = json.loads(INDEX.read_text(encoding="utf-8"))
    res = json.loads(ASR.read_text(encoding="utf-8")) if ASR.exists() else {}
    bad = []
    for n, (k, e) in enumerate(idx.items()):
        p = pathlib.Path(e["path"])
        if not p.exists() or res.get(k, {}).get("dur") == e["dur"]:
            continue                             # rejected already, or this exact take was checked
        segs, _ = m.transcribe(load16k(p), language="zh", beam_size=5, initial_prompt="以下是普通话的军事解说。")
        heard = "".join(s.text for s in segs)
        c = cer(e["spoken"], heard)
        res[k] = {"cer": round(c, 3), "heard": heard, "dur": e["dur"]}
        if c > max_cer and e.get("attempt", 1) < 3:      # third take stays (Whisper mishears names: 明登 -> 明灯)
            bad.append(k)
            p.replace(p.with_suffix(".bad.wav"))
            print(f"reject {k} cer {c:.2f}\n  text  {e['spoken']}\n  heard {heard}", flush=True)
        if n % 25 == 0:
            ASR.write_text(json.dumps(res, ensure_ascii=False, indent=0), encoding="utf-8")
    ASR.write_text(json.dumps(res, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"checked, {len(bad)} rejected", ASR)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
