"""Programme segment cut: a beat list (JSON spec) -> shot module + shot timeline (Markdown).

    python -I -X utf8 make_segment.py segments/F-minden-g1.json
    python -I -X utf8 make_segment.py segments/AB-opening-1.json segments/AB-opening-2.json   (parts of one cut)

The spec lists beats in order. Each beat lasts `dur` video seconds and moves the game clock to `g` (game seconds after
the scenario start; equal to the previous value = freeze). `cam` = [start, end] camera names from `cams` (a different
start than the previous end is a hard cut); `ease: "linear"` for slow pushes. Panels, order cards, captions and steps
inside a beat use times relative to the beat start (`at: [from, to]`). The generator writes:
- the shot module (`out_shot`, next to sandbox.js): `{ ...base shot, time, camera, steps, panels, orders, captions, ... }`
- the shot timeline (`timeline`): every beat's start / end in the cut, game clock span, camera, what is on screen and which
  programme rundown item it serves.

Spoken lines (programme opening and later voiced segments): a beat may carry `lines: [{speaker, text}]`. They are laid
one after another from `lines_start` (default 0.6 s), each lasting its reading time at `cpm` characters per minute
(spec level, default 260 (11-06: 220 felt slow); a Chinese character counts 1, a Latin word or number counts 1.5,
punctuation 0), with `line_gap` (default 0.35 s) between them; `dur: "auto"` makes the beat end `tail` (default 0.8 s)
after its last line. The lines become subtitles (`lines` in the shot) and a line table in the timeline, so the voice-over
can be fitted to it later. `people: [{img, name, role, x, y, beats, at}]` puts person cards on screen from `at` (beat
relative, default 0.2) through the end of the `beats`-th beat counted from this one (default 1, `end_pad` seconds before
it, default 0.3); a card is highlighted while a line of its `speaker` (default: its name) runs; `sync: true` shows it only
while those lines run, `small: true` draws it at 0.62 scale; `talk: [[from, to]]` (beat relative) marks speech without
subtitles (a reserved announcement), `voice` names the voice whose oscilloscope colour / wave the card shows (default: the
speaker), `tag` the plate under the portrait (default: the name). `quiet: true` on a beat hides order arrows, their labels, the front line and
engagement links (sand-table panoramas). `title: {k, title, sub, at}`
and `dim: {a, at}` are a title card and a screen dim inside the beat. `slides: [{src, zoom, pan, fade, at}]` are full-screen
stills with a slow push (drawn under the overlay), `boards: [{html, x, y, w, cls, at}]` are HTML panels (`{{src:<file>}}` in
the HTML becomes an inline image); both default to the whole beat. `duration` may be "auto"; a part's `offset` may be
"auto" when the parts are given in one run (each later part starts where the previous one ended).
Staging (staging/compile_staging.py): `g0` on a beat jumps the game clock to that value at the beat start (a cut to another
moment); `stage: {...}` adds the beat to the shot's `staging.shots` (with its video span), `cues: [{..., line: k}]` adds
cues to `staging.cues`, each from the start of the beat's k-th line (default the beat start) to the beat end.
"""
import json
import math
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import voice_cache  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent


def mmss(t):
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:04.1f}"


def hhmm(g, h0):
    m = int(g // 60) + h0
    return f"{m // 60 % 24:02d}:{m % 60:02d}"


def reading_units(text):
    """Characters for the reading-time estimate: CJK characters 1, Latin words 1.5, punctuation 0."""
    latin = re.findall(r"[A-Za-z0-9]+", text)
    cjk = sum(1 for c in text if ord(c) > 0x2E80 and c not in "，。！？、；：“”‘’（）《》…—")
    return cjk + 1.5 * len(latin)


# 11-08 the owner: "慢的离谱…空隙缩短，语速大幅加快": the voices are tightened and sped up (voice/cast.py) and the air
# around them is cut: lines 0.2 s apart (the high-view benchmark's suggestion), each beat's lead-in and tail halved
LINE_GAP = 0.2
PACE = 0.5


def line_dur(ln, cpm=260):
    """seconds a line is spoken: its voiced audio when there is one, else the reading-time estimate"""
    vo = voice_cache.lookup(ln["speaker"], ln["text"])
    return round(vo["dur"], 3) if vo else round(max(reading_units(ln["text"]) / cpm * 60, 1.2), 2)


def build(spec_path, offset_auto):
    sp = json.loads(pathlib.Path(spec_path).read_text(encoding="utf-8"))
    h0 = int(sp["h0"].split(":")[0]) * 60 + int(sp["h0"].split(":")[1])
    cams = sp["cams"]
    cpm, gap0 = sp.get("cpm", 260), sp.get("line_gap", LINE_GAP)
    t, g = 0.0, sp.get("g0", 0)
    time = [[0, g]]
    camera, steps, panels, orders, captions, labels, focus, keys, rows = [], [], [], [], [], [], [], [], []
    lines, people_q, dim, bounds, quiet, slides, boards = [], [], [], [], [], [], []
    st_shots, st_cues = [], []
    end_card = title_card = None
    for bi, b in enumerate(sp["beats"]):
        blines, tr = [], b.get("lines_start", 0.6) * PACE
        for ln in b.get("lines", []):
            vo = voice_cache.lookup(ln["speaker"], ln["text"])     # voiced: the audio's own length
            d = line_dur(ln, cpm)
            blines.append(dict(ln, rel=(round(tr, 3), round(tr + d, 3)), units=reading_units(ln["text"]),
                               audio=vo["path"] if vo else None))
            tr += d + gap0
        dur = b["dur"] if b["dur"] != "auto" else math.ceil((tr - gap0 + b.get("tail", 0.8) * PACE) * 10 - 1e-6) / 10   # whole frames
        if "g0" in b and b["g0"] != g:              # cut to another moment of the game
            g = b["g0"]
            time.append([round(t, 3), g])
        t0, t1, g0, g1 = t, t + dur, g, b.get("g", g)
        c0, c1 = (cams[b["cam"][0]], cams[b["cam"][1]]) if isinstance(b["cam"][0], str) else b["cam"]
        k0 = dict(c0, t=round(t0, 3))
        if b.get("ease"):
            k0["ease"] = b["ease"]
        camera += [k0, dict(c1, t=round(t1, 3))]
        time.append([round(t1, 3), g1])
        rel = lambda a: [round(t0 + a[0], 3), round(t0 + a[1], 3)]
        for p in b.get("panels", []):
            f, to = rel(p["at"])
            panels.append({k: v for k, v in p.items() if k != "at"} | {"from": f, "to": to})
        for o in b.get("orders", []):
            f, to = rel(o["at"])
            orders.append({k: v for k, v in o.items() if k != "at"} | {"g": g0, "v": f, "dur": round(to - f, 3)})
        for c in b.get("captions", []):
            f, to = rel(c["at"])
            captions.append({"from": f, "to": to, "text": c["text"]})
        for s in b.get("steps", []):
            f, to = rel(s["at"])
            steps.append({k: v for k, v in s.items() if k != "at"} | {"from": f, "to": to})
        for lb in b.get("labels", []):
            f, to = rel(lb["at"])
            labels.append({k: v for k, v in lb.items() if k != "at"} | {"from": f, "to": to})
        for key, dst in (("slides", slides), ("boards", boards)):   # beat-relative `at` (default: the whole beat)
            for it in b.get(key, []):
                f, to = rel(it.get("at", [0, dur]))
                dst.append({k: v for k, v in it.items() if k != "at"} | {"from": f, "to": to})
        for ln in blines:
            f, to = rel(ln["rel"])
            lines.append({"from": f, "to": to, "speaker": ln["speaker"], "text": ln["text"], "units": ln["units"], "beat": b["id"],
                          "audio": ln["audio"]})
        if "stage" in b:
            st_shots.append(dict(b["stage"], id=b["id"], **{"from": round(t0, 3), "to": round(t1, 3), "g0": g0, "g1": g1}))
        for q in b.get("cues", []):
            f = t0 + (blines[q["line"]]["rel"][0] if q.get("line") is not None else 0)
            st_cues.append({k: v for k, v in q.items() if k != "line"} | {"from": round(f, 3), "to": round(t1, 3), "beat": b["id"]})
        for q in b.get("people", []):
            people_q.append((bi, q))
        if b.get("title"):
            f, to = rel(b["title"].get("at", [0, dur]))
            title_card = {k: v for k, v in b["title"].items() if k != "at"} | {"from": f, "to": to}
        if b.get("dim"):
            f, to = rel(b["dim"].get("at", [0, dur]))
            dim.append({"from": f, "to": to, "a": b["dim"].get("a", 0.55)})
        if b.get("quiet"):
            quiet.append([round(t0, 3), round(t1, 3)])
        if b.get("focus"):
            focus.append({"from": round(t0, 3), "to": round(t1, 3), "units": b["focus"], "counters": False})
        if b.get("end"):
            end_card = dict(b["end"], **{"from": round(t0 + b["end"].get("at", 0.5), 3)})
            end_card.pop("at", None)
        for i, k in enumerate(b.get("keys", [])):
            keys.append({"frame": round((t0 + k) * sp.get("fps", 30)), "name": f"{sp['still_prefix']}-{b['id']}{'' if i == 0 else chr(97 + i)}.png"})
        speed = (g1 - g0) / dur
        rows.append({"id": b["id"], "t0": t0, "t1": t1, "g0": g0, "g1": g1, "speed": speed, "b": b, "dur": dur})
        bounds.append((t0, t1))
        t, g = t1, g1
    duration = sp["duration"] if sp["duration"] != "auto" else round(t, 3)
    if abs(t - duration) > 1e-6:
        raise SystemExit(f"beats add up to {t:.2f} s, spec duration is {duration} s")
    people = []
    for bi, q in people_q:
        last = min(bi + q.get("beats", 1) - 1, len(bounds) - 1)
        f, to = round(bounds[bi][0] + q.get("at", 0.2), 3), round(bounds[last][1] - q.get("end_pad", 0.3), 3)
        who = q.get("speaker", q["name"])
        if q.get("sync"):                       # card shown exactly while its speaker's lines in these beats run
            mine = [ln for ln in lines if ln["speaker"] == who and bounds[bi][0] <= ln["from"] < bounds[last][1]]
            f, to = mine[0]["from"], mine[-1]["to"]
        on = [[ln["from"] - 0.15, ln["to"] + 0.15] for ln in lines if ln["speaker"] == who and ln["from"] < to and ln["to"] > f]
        on += [[round(bounds[bi][0] + a, 3), round(bounds[bi][0] + b, 3)] for a, b in q.get("talk", [])]   # speaking, no subtitle
        people.append({k: v for k, v in q.items() if k not in ("at", "beats", "end_pad", "speaker", "sync", "talk")}
                      | {"from": f, "to": to, "on": sorted(on), "voice": q.get("voice", who)})
    shot = {"id": sp["id"], "duration": duration, "time": time, "camera": camera, "steps": steps, "panels": panels,
            "orders": orders, "captions": captions, "labels": labels, "focus": focus, "fog": sp.get("fog", []),
            "endCard": end_card, "keyframes": keys}
    if lines:
        shot["lines"] = [{k: v for k, v in ln.items() if k in ("from", "to", "speaker", "text")} for ln in lines]
    if people:
        shot["people"] = people
    if title_card:
        shot["titleCard"] = title_card
    if dim:
        # back-to-back dims are one span: each beat fading its own dim out and the next one in lit the sand table up
        # for half a second at every card change (segment C 11-06, C07-C10)
        # (only dims of about the same strength; a zero dim under a still is left alone)
        dim = sorted((q for q in dim if q["a"] > 0), key=lambda q: q["from"])
        merged = dim[:1]
        for q in dim[1:]:
            if q["from"] - merged[-1]["to"] <= 0.1 and abs(q["a"] - merged[-1]["a"]) <= 0.15:
                merged[-1] = merged[-1] | {"to": max(merged[-1]["to"], q["to"]), "a": max(merged[-1]["a"], q["a"])}
            else:
                merged.append(q)
        shot["dim"] = merged
    if quiet:
        shot["quiet"] = quiet
    if st_shots or st_cues:
        shot["stagingCues"] = {"shots": st_shots, "cues": st_cues}
    if slides:
        # back-to-back stills crossfade directly: the outgoing one stays fully opaque until the incoming one is in,
        # otherwise the bright sand table shows through between them (the 11-06 "flash" at every cut in segment C)
        slides.sort(key=lambda s: s["from"])
        for cur, nxt in zip(slides, slides[1:]):
            if nxt["from"] - cur["to"] <= 0.1:
                cur["to"] = round(nxt["from"] + nxt.get("fade", 0.5) + 0.1, 3)
        # a still handing over to a dimmed sand table stays up until the dim is in (dim fades are 0.6 s)
        for s in slides:
            q = next((q for q in shot.get("dim", []) if abs(q["from"] - s["to"]) <= 0.1), None)
            if q:
                s["to"] = round(q["from"] + 0.7 + s.get("fade", 0.5), 3)
        shot["slides"] = slides
    if boards:
        # a board that ends where the next beat's board starts stays up until that one is in (a crossfade, not a gap)
        boards.sort(key=lambda b: b["from"])
        # (highlight frames sit on a still and leave with it)
        for cur in boards:
            nxt = next((b for b in boards if b is not cur and 0 <= b["from"] - cur["to"] <= 0.5), None)
            if nxt and "mark" not in (cur.get("cls") or "").split():
                cur["to"] = round(nxt["from"] + 0.95, 3)
        shot["boards"] = boards
    shot |= sp.get("override", {})
    js = [f"// Programme segment {sp['segment']} ({sp['title']}): generated by make_segment.py from {pathlib.Path(spec_path).name};",
          "// edit the spec, not this file. Base shot (models, ground, theme, moments, commanders) from " + sp["base"] + ".",
          f"import base from '{sp['base']}';",
          "export default { ...base,"]
    for k, v in shot.items():
        js.append(f"  {k}: {json.dumps(v, ensure_ascii=False)},")
    js.append("};\n")
    out = HERE / sp["out_shot"]
    out.write_text("\n".join(js), encoding="utf-8")
    print("shot", out)
    # the voice track for the mixdown (voice/mix.py): each line's place in this clip and its audio (null = not voiced yet)
    out.with_suffix(".voice.json").write_text(json.dumps({"duration": duration, "lines": [
        {k: ln[k] for k in ("from", "to", "speaker", "text", "audio")} for ln in lines]}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    part = sp.get("part")                      # {n, offset, label}: one part of a multi-part segment, rendered as its own clip
    off = (offset_auto if part.get("offset") == "auto" else part["offset"]) if part else 0
    total = sp.get("total", duration)
    total_txt = f"{int(total // 60)}:{total % 60:04.1f}" if total != "auto" else "见各部分"
    md = [f"# {sp['segment']} 段镜头时间表：{sp.get('segment_title', sp['title'])}", "",
          f"成片：`{sp['mp4']}`（{total_txt}，1920×1080，30 fps）。镜头表由 "
          f"`tools\\video\\make_segment.py` 从 "
          f"`{pathlib.Path(spec_path).as_posix()}` 生成，与成片同源。",
          "", sp.get("note", ""), "",
          "“口播空档”= 时钟定格或极慢回放、镜头慢推，留给解说和指挥官原话回放；台词按这里的起止时间对进来。", "",
          ]
    if part and part["n"] > 1:
        md = []
    if part:
        md += [f"## {part['label']}（成片 {mmss(off)}–{mmss(off + duration)}）", "", sp.get("part_note", ""), ""]
    md += ["| # | 起 | 止 | 时长 | 游戏时间 | 回放速度 | 镜头 | 画面内容 | 编排条目 | 台本对应 |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        b = r["b"]
        clock = hhmm(r["g0"], h0) if r["g0"] == r["g1"] else f"{hhmm(r['g0'], h0)}–{hhmm(r['g1'], h0)}"
        spd = "定格" if r["g0"] == r["g1"] else f"×{r['speed']:.0f}"
        md.append(f"| {b['id']} | {mmss(off + r['t0'])} | {mmss(off + r['t1'])} | {r['dur']:.1f} s | {clock} | {spd} | {b.get('shot', '')} | "
                  f"{b['what']} | {b.get('rundown', '')} | {b.get('script', '')} |")
    if lines:
        md += ["", f"### 台词（字幕，{part['label'] if part else ''}）：配音按这里的起止时间对", "",
               f"时长按每分钟 {cpm} 字估算（汉字计 1、一个英文词计 1.5、标点不计），最短 1.2 s，句间留 {gap0} s。", "",
               "| 句 | 起 | 止 | 时长 | 说话人 | 台词 | 计字 | 镜头 |", "|---|---|---|---|---|---|---|---|"]
        for i, ln in enumerate(lines, 1):
            md.append(f"| {sp['still_prefix']}{part['n'] if part else ''}-{i:02d} | {mmss(off + ln['from'])} | {mmss(off + ln['to'])} | "
                      f"{ln['to'] - ln['from']:.2f} s | {ln['speaker']} | {ln['text']} | {ln['units']:g} | {ln['beat']} |")
    if sp.get("sources"):
        md += ["", "## 画面元素的出处", ""] + [f"- {x}" for x in sp["sources"]]
    md.append("")
    tl = pathlib.Path(sp["timeline"])
    if part and part["n"] > 1:                 # later parts of the segment go under the first one's table
        with open(tl, "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(md))
    else:
        tl.write_text("\n".join(md), encoding="utf-8")
    print("timeline", tl)
    print("duration", duration, "offset", off)
    print("keyframes", ",".join(str(k["frame"]) for k in keys))
    return off + duration


def main(paths):
    end = 0
    for p in paths:
        end = build(p, end)


if __name__ == "__main__":
    main(sys.argv[1:])
