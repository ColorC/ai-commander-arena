"""Voiced lines: one wav per (voice, text), synthesised by voice/synth_lines.py (Qwen3-TTS, the cast in voice/cast.py).

make_segment.py asks `lookup(speaker, text)`: when the line has been voiced, its length is the audio's (else the reading-time
estimate) and the segment's voice sidecar (<out_shot>.voice.json) points at the file, for the mixdown (voice/mix.py).

Cache: $ARENA_DATA/video/voice/lines/<key>.wav, index.json {key: {voice, text, path, dur}}; key = sha1 of
"voice|text" (first 16 hex)."""
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "voice"))
from cast import LINES, PAUSE_MAX, TEMPO, VOICE_OF  # noqa: E402  (who speaks a line -> voice; the referee is Claude)

INDEX = LINES / "index.json"
_idx = None


def key(voice, text):
    return hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()[:16]


def voice_of(speaker):
    return VOICE_OF.get(speaker)


def index():
    global _idx
    if _idx is None:
        _idx = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
    return _idx


def lookup(speaker, text):
    """{path, dur} of the voiced line, or None"""
    v = voice_of(speaker)
    if not v:
        return None
    e = index().get(key(v, text))
    if not e or not pathlib.Path(e["path"]).exists():
        return None
    f = e.get("fast")                    # the sped-up take (speed_lines.py) when the cast plays faster
    if f and f["tempo"] == TEMPO and f.get("pause") == PAUSE_MAX and pathlib.Path(f["path"]).exists():
        return f
    return e
