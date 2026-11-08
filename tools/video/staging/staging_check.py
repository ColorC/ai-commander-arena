"""Check a staging file (STANDARD.md) against its game: every commentary line is staged exactly once, unit codes exist,
hexes are on the map, game times are inside the game, cue types and fields are the standard's.

    python -I -X utf8 staging_check.py $ARENA_DATA/video/program/staging/F.json [--data <data-*.js>]

Prints one JSON object: ok, counts, errors (must fix), warnings (look at). Exit code 1 when there are errors."""
import json
import pathlib
import re
import sys
import os

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[3] / "data")

MANIFEST = ARENA_DATA / "video/program/staging/manifest.json"
CUES = {
    "highlight": {"units"},
    "tag": {"units", "show"},
    "order": {"units"},
    "fire": set(),
    "losses": set(),
    "hex": {"hexes"},
    "score": {"show"},
    "fog": {"view"},
    "card": {"kind"},
}
TAG_SHOW = {"name", "strength", "order", "morale", "ammo", "readiness", "delay", "losses", "subunits", "equipment"}
ZOOM = {"close", "mid", "wide"}


def load_data(p):
    s = pathlib.Path(p).read_text(encoding="utf-8")
    return json.loads(s[s.index("{"):s.rindex("}") + 1])


def lines_of(md):
    """{seg: [line text, ...]} from a live-commentary .md (## 第 N 段｜到 HH:MM, then 'Who：text' lines)."""
    segs, cur = {}, None
    for ln in md.splitlines():
        m = re.match(r"##\s*第\s*(\d+)\s*段", ln)
        if m:
            cur = int(m.group(1)); segs[cur] = []; continue
        if cur is not None and re.match(r"^(Claude|GPT)[：:]", ln.strip()):
            segs[cur].append(ln.strip())
    return segs


def main():
    a = sys.argv[1:]
    path = pathlib.Path(a[0])
    st = json.loads(path.read_text(encoding="utf-8"))
    man = {m["case"]: m for m in json.loads(MANIFEST.read_text(encoding="utf-8"))}
    m = man[st["case"]]
    data_p = a[a.index("--data") + 1] if "--data" in a else m.get("data")
    errs, warns = [], []
    codes, cols, rows, T, h0 = None, None, None, None, None
    if data_p and pathlib.Path(data_p).exists():
        d = load_data(data_p); g = d["game"]
        codes = {u[0] for u in g["u"]}; cols, rows = d["terrain"]["cols"], d["terrain"]["rows"]; T, h0 = g["T"], g["h0"]
    else:
        warns.append(f"no game data ({data_p}): unit codes, hexes and times not checked")
        # without the data: the scenario's start (minutes after midnight) and length (s) from the manifest entry
        h0 = int(m.get("h0_min", 420)); T = int(m.get("length_sec", 4 * 3600))

    def gsec(t, where):
        mm = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(t))
        if not mm:
            errs.append(f"{where}: time {t!r} is not HH:MM[:SS]"); return None
        s = ((int(mm.group(1)) * 60 + int(mm.group(2)) - h0) % 1440) * 60 + int(mm.group(3) or 0)
        if not 0 <= s <= T:
            errs.append(f"{where}: time {t} outside the game (0..{T} s after start)")
        return s

    def hexok(h, where):
        mm = re.fullmatch(r"\((\d+),\s*(\d+)\)", str(h)) if isinstance(h, str) else None
        if isinstance(h, list) and len(h) == 2:
            c, r = h
        elif mm:
            c, r = int(mm.group(1)), int(mm.group(2))
        else:
            errs.append(f"{where}: hex {h!r} must be \"(col,row)\""); return
        if cols and not (1 <= c <= cols and 1 <= r <= rows):
            errs.append(f"{where}: hex ({c},{r}) off the map {cols}x{rows}")

    def unitsok(us, where):
        if not isinstance(us, list) or not us:
            errs.append(f"{where}: units must be a non-empty list of unit codes"); return
        for u in us:
            if codes is not None and u not in codes:
                errs.append(f"{where}: unknown unit code {u!r}")

    segs = lines_of(pathlib.Path(m["commentary"]).read_text(encoding="utf-8"))
    seen = {}
    n_shots = n_cues = 0
    for S in st.get("segments", []):
        k = S.get("seg")
        if k not in segs:
            errs.append(f"segment {k}: not in the commentary"); continue
        for sh in S.get("shots", []):
            n_shots += 1
            w = f"seg {k} shot {sh.get('id')}"
            if not sh.get("shows"):
                errs.append(f"{w}: 'shows' (what the viewer should see) missing")
            for i in sh.get("lines", []):
                if not 0 <= i < len(segs[k]):
                    errs.append(f"{w}: line {i} out of range (segment has {len(segs[k])})")
                seen.setdefault((k, i), []).append(sh.get("id"))
            tm = sh.get("time") or {}
            if "at" in tm:
                gsec(tm["at"], w)
            elif "from" in tm and "to" in tm:
                a0, a1 = gsec(tm["from"], w), gsec(tm["to"], w)
                if a0 is not None and a1 is not None and a1 < a0:
                    errs.append(f"{w}: time runs backwards")
            else:
                errs.append(f"{w}: time needs 'at' or 'from'+'to'")
            fo = sh.get("focus") or {}
            if not fo:
                errs.append(f"{w}: focus missing")
            if "hex" in fo: hexok(fo["hex"], w)
            for h in fo.get("hexes", []): hexok(h, w)
            if "units" in fo: unitsok(fo["units"], w + " focus")
            if sh.get("zoom") not in ZOOM:
                errs.append(f"{w}: zoom must be one of {sorted(ZOOM)}")
            for ci, cu in enumerate(sh.get("cues", [])):
                n_cues += 1
                cw = f"{w} cue {ci} ({cu.get('type')})"
                t = cu.get("type")
                if t not in CUES:
                    errs.append(f"{cw}: unknown cue type"); continue
                for f in CUES[t] - set(cu):
                    errs.append(f"{cw}: field {f!r} missing")
                if "units" in cu: unitsok(cu["units"], cw)
                for h in cu.get("hexes", []): hexok(h, cw)
                if t == "tag":
                    bad = set(cu.get("show", [])) - TAG_SHOW
                    if bad: errs.append(f"{cw}: show has {sorted(bad)}; allowed {sorted(TAG_SHOW)}")
                for f in ("issued", "effective"):
                    if f in cu: gsec(cu[f], cw)
                if "window" in cu:
                    for x in cu["window"]: gsec(x, cw)
                on = cu.get("on")
                if on is not None and on not in sh.get("lines", []):
                    errs.append(f"{cw}: on={on} is not one of this shot's lines")
    for k, ls in segs.items():
        for i in range(len(ls)):
            ids = seen.get((k, i))
            if not ids:
                errs.append(f"segment {k} line {i} not staged: {ls[i][:40]}")
            elif len(ids) > 1:
                errs.append(f"segment {k} line {i} staged {len(ids)} times: {ids}")
    print(json.dumps({"file": str(path), "ok": not errs, "shots": n_shots, "cues": n_cues, "errors": errs, "warnings": warns},
                     ensure_ascii=False, indent=1))
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
