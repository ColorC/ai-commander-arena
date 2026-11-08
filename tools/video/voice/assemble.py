"""The voiced programme: mix the voice into every rendered clip (mix.py) and join them in programme order
(AB -> C -> D0 -> exercises T1 T3 T2 T4 -> M1 M2 -> E0 -> F -> G -> H results, review and end credits).

Run with the tts-lab venv after the clips are rendered:
    <TTS venv python> -X utf8 assemble.py
Inputs: intro clips $ARENA_DATA/video/program/v1107-intro/<name>.mp4 with their sidecars
tools/video/<out_shot>.voice.json; game clips <site>/video/<case>-<seg>.mp4 with staging/keys/<case>/shot-<case>-<seg>.voice.json.
Out: $ARENA_DATA/video/program/v1108-voiced/clips/<clip>.mp4, programme-v1108.mp4, chapters.md;
the review site's video/ and film/ copies are replaced by the voiced clips (hard links)."""
import json
import os
import pathlib
import shutil
import subprocess
import sys

from mix import FFMPEG, mix

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[3] / "data")

VID = pathlib.Path(__file__).resolve().parent.parent
PROG = ARENA_DATA / "video/program"
INTRO = PROG / "v1107-intro"
ST = PROG / "staging"
SITE = PROG / "site-staging-review"
OUT = PROG / "v1108-voiced"
FFPROBE = os.environ.get("FFPROBE", "ffprobe")

INTROS = {"AB-opening-1": "AB-opening-1", "AB-opening-2": "AB-opening-2", "C": "C-intro", "D0": "D0-intro-marburg",
          "E0": "E0-intro-minden", "H": "H-outro"}
ORDER = ["AB-opening-1", "AB-opening-2", "C", "D0", "T1", "T3", "T2", "T4", "M1", "M2", "E0", "F", "G", "H"]
TITLES = {"AB-opening-1": "A+B 开场", "C": "C 游戏怎么玩", "D0": "D 马尔堡：想定与比赛规则", "T1": "演习：Kimi（T1 执蓝）",
          "T3": "演习：Kimi（T3 执红）", "T2": "演习：DeepSeek（T2 执蓝）", "T4": "演习：DeepSeek（T4 执红）",
          "M1": "马尔堡正赛第一局", "M2": "马尔堡正赛第二局", "E0": "E 明登：想定", "F": "F 明登第一局", "G": "G 明登第二局",
          "H": "H 结算与复盘", "credits": "片尾致谢"}
CREDITS = 8 * 5.5   # make_h.py: the end credits are the last 8 beats of H, 5.5 s each


def seg_file(name):
    return json.loads((VID / "segments" / f"{name}.json").read_text(encoding="utf-8"))


def clips():
    out = []
    for item in ORDER:
        if item in INTROS:
            name = INTROS[item]
            out.append((item, INTRO / f"{name}.mp4", VID / pathlib.Path(seg_file(name)["out_shot"]).with_suffix(".voice.json"),
                        name))
        else:
            for s in json.loads((ST / f"{item}.json").read_text(encoding="utf-8"))["segments"]:
                sid = f"{item}-{s['seg']:02d}"
                out.append((item, SITE / "video" / f"{sid}.mp4", ST / "keys" / item / f"shot-{sid}.voice.json", sid))
    return out


def dur(p):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def same_copy(a, b):
    sa, sb = a.stat(), b.stat()
    return sa.st_size == sb.st_size and abs(sa.st_mtime - sb.st_mtime) < 0.01


def link(src, dst):
    if dst.exists():
        dst.unlink()
    os.link(src, dst)


def main():
    (OUT / "clips").mkdir(parents=True, exist_ok=True)
    allow = "--allow-missing" in sys.argv
    lst, chapters, t = [], [], 0.0
    for item, mp4, sc, name in clips():
        dst = OUT / "clips" / f"{name}.mp4"
        if item not in INTROS:
            # 11-08 incident: the review page's copy used to be a hard link to the voiced clip, so a re-mix read and
            # wrote the same file and truncated all 50 game clips. Now a fresh render (any file there that is not our
            # voiced copy) is moved to OUT/raw first, the mix always reads OUT/raw, and the review page gets a copy.
            raw = OUT / "raw" / f"{name}.mp4"
            raw.parent.mkdir(exist_ok=True)
            if mp4.exists() and not (dst.exists() and (os.path.samefile(mp4, dst) or same_copy(mp4, dst))):
                os.replace(mp4, raw)
            if not raw.exists():
                raise SystemExit(f"no raw render for {name}: run render_all.py {item} --video")
            mp4 = raw
        elif not mp4.exists() or not sc.exists():
            raise SystemExit(f"missing {mp4 if not mp4.exists() else sc}")
        assert not (dst.exists() and os.path.samefile(mp4, dst)), (mp4, dst)
        if not dst.exists() or dst.stat().st_mtime < max(mp4.stat().st_mtime, sc.stat().st_mtime):
            mix(mp4, sc, dst, allow)
        if item in TITLES and (not chapters or chapters[-1][1] != item):
            chapters.append((t, item))
        t += dur(dst)
        lst.append(dst)
        if item not in INTROS:
            site = SITE / "video" / dst.name                # the review page plays a copy of the voiced clip
            if site.exists():
                site.unlink()
            shutil.copy2(dst, site)
    (OUT / "concat.txt").write_text("".join(f"file '{p.as_posix()}'\n" for p in lst), encoding="utf-8")
    prog = OUT / "programme-v1108.mp4"
    r = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(OUT / "concat.txt"),
                        "-c", "copy", "-movflags", "+faststart", str(prog)], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"concat failed: {r.stderr[-800:]}")
    # the opening as one film again (the review page's film/AB-opening.mp4), the other intros as they are
    ab = OUT / "clips" / "AB-opening.mp4"
    (OUT / "ab.txt").write_text("".join(f"file '{(OUT / 'clips' / n).as_posix()}.mp4'\n" for n in ("AB-opening-1", "AB-opening-2")),
                                encoding="utf-8")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(OUT / "ab.txt"), "-c", "copy",
                    str(ab)], check=True)
    for n in ("AB-opening", "C-intro", "D0-intro-marburg", "E0-intro-minden"):
        link(OUT / "clips" / f"{n}.mp4", SITE / "film" / f"{n}.mp4")
    chapters.append((t - CREDITS, "credits"))
    spans = [{"item": i, "start": round(s, 3), "end": round(chapters[k + 1][0] if k + 1 < len(chapters) else t, 3)}
             for k, (s, i) in enumerate(chapters)]
    (OUT / "chapters.json").write_text(json.dumps(spans, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "chapters-titles.json").write_text(json.dumps(TITLES, ensure_ascii=False), encoding="utf-8")
    md = ["# 《AI 陆战军事演习》配音版", "", f"成片：`{prog}`，{t / 60:.1f} 分钟。", "", "| 起点 | 章节 |", "|---|---|"]
    md += [f"| {int(s // 60)}:{int(s % 60):02d} | {TITLES[i]} |" for s, i in chapters]
    (OUT / "chapters.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    print("programme", prog, "chapters", OUT / "chapters.md")


if __name__ == "__main__":
    main()
