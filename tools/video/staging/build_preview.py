"""节目集中预览页（11-08 业主：「我需要一个集中的预览页」）：一页看成片（按章节跳转）、封面、B 站章节、背景音乐表、
原始画面素材、片尾致谢，以及其他审阅页的入口。手机竖屏可看。

    python -I -X utf8 build_preview.py
In: v1108-voiced/（成片、chapters.json、chapters-titles.json、chapters-bilibili.txt）、cover/、raw-footage/、credits/credits.md、
music/cue-sheet.json、music/plan.json（voice/music.py 的铺曲表）。
Out: $ARENA_DATA/video/program/site-preview/（index.html + 复制的成片、封面、素材；不用硬链接，
源文件重出不会改坏站点副本）。站点目录是纯静态文件，用任意静态托管发布。
可选：v1108-voiced/links.json（[[标题, URL], ...]，页首的下载 / 审阅链接）、v1108-voiced/issues.json（["待办一句", ...]）。"""
import html
import json
import os
import pathlib
import re
import shutil
import subprocess

import markdown

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[3] / "data")

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")          # ffmpeg on PATH unless FFMPEG names the executable

P = ARENA_DATA / "video/program"
V = P / "v1108-voiced"
SITE = P / "site-preview"
CUE = ARENA_DATA / "video/music/cue-sheet.json"
E = html.escape

def _optional_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


LINKS = [tuple(x) for x in _optional_json(V / "links.json", [])]      # [[label, url], ...]: downloads, review sites
ISSUES = _optional_json(V / "issues.json", [])                         # open points listed on the page
GAMES = {"M1": "马尔堡第一局", "M2": "马尔堡第二局", "F": "明登第一局", "G": "明登第二局"}

CSS = """
:root{--bg:#16171a;--panel:#212329;--line:#34363e;--ink:#ece8dc;--dim:#a3a091;--gold:#e9c35a;--blue:#8fb8ef}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 "Microsoft YaHei","Noto Sans SC",system-ui,sans-serif}
a{color:var(--blue)}main{max-width:1180px;margin:0 auto;padding:0 16px 80px}
header{padding:18px 0 6px}h1{font-size:24px;margin:0}.sub{color:var(--dim);font-size:14px}
nav.toc{position:sticky;top:0;z-index:5;background:rgba(22,23,26,.96);border-bottom:1px solid var(--line);margin:0 -16px;padding:8px 16px;
  display:flex;gap:6px;overflow-x:auto;white-space:nowrap;scrollbar-width:none}
nav.toc a{color:var(--ink);text-decoration:none;font-size:14px;padding:3px 10px;border:1px solid var(--line);border-radius:14px;flex:none}
h2{font-size:20px;color:var(--gold);margin:30px 0 10px;scroll-margin-top:52px}
video{width:100%;display:block;background:#000;border-radius:4px}
.chap{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:6px;margin-top:10px}
.chap button{all:unset;cursor:pointer;display:flex;gap:10px;align-items:baseline;padding:7px 10px;background:var(--panel);border:1px solid var(--line);border-radius:4px}
.chap button.on{border-color:var(--gold)}.chap .t{font:600 14px/1 ui-monospace,Consolas,monospace;color:var(--gold);min-width:44px}
.chap .m{display:block;font-size:12px;color:var(--dim)}
.links{display:flex;flex-direction:column;gap:6px;margin:12px 0 0;padding:0;list-style:none}
.links a{display:block;padding:8px 12px;background:var(--panel);border:1px solid var(--line);border-radius:4px;text-decoration:none}
.covers{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px}.covers figure{margin:0}
.covers img{width:100%;display:block;border-radius:4px}.covers figcaption{font-size:13px;color:var(--dim)}
textarea{width:100%;height:330px;background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:4px;padding:10px;font:14px/1.6 ui-monospace,Consolas,monospace}
button.copy{margin-top:6px;background:var(--panel);color:var(--gold);border:1px solid var(--gold);border-radius:4px;padding:5px 14px;font:inherit;cursor:pointer}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{border-bottom:1px solid var(--line);padding:6px 6px;text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:400}.tw{overflow-x:auto}
.raw{background:var(--panel);border:1px solid var(--line);border-radius:4px;padding:10px;margin:10px 0}
.raw h3{margin:0 0 6px;font-size:16px}.raw .pair{display:grid;grid-template-columns:1fr 1fr;gap:8px}
@media(max-width:720px){.raw .pair{grid-template-columns:1fr}}
.raw .cap{font-size:12px;color:var(--dim)}
.credits{background:var(--panel);border:1px solid var(--line);border-radius:4px;padding:4px 16px 12px;font-size:14px}
.credits h1{font-size:18px}.credits h2{font-size:16px;margin:18px 0 6px}.credits li{margin:3px 0}
ul.issues li{margin:4px 0}
"""

JS = """
const v=document.getElementById('film'),bs=[...document.querySelectorAll('.chap button')];
const s=v.dataset.src;if(window.Hls&&Hls.isSupported()){const h=new Hls();h.loadSource(s);h.attachMedia(v)}else v.src=s;
bs.forEach(b=>b.onclick=()=>{v.currentTime=+b.dataset.t;v.play();v.scrollIntoView({behavior:'smooth',block:'center'})});
v.addEventListener('timeupdate',()=>{let k=-1;bs.forEach((b,i)=>{if(v.currentTime>=+b.dataset.t)k=i});bs.forEach((b,i)=>b.classList.toggle('on',i===k))});
document.querySelectorAll('button.copy').forEach(b=>b.onclick=()=>{const t=document.getElementById(b.dataset.for);t.select();
  (navigator.clipboard?navigator.clipboard.writeText(t.value):Promise.reject()).then(()=>b.textContent='已复制',()=>{document.execCommand('copy');b.textContent='已复制'})});
"""


def mmss(s):
    return f"{int(s // 60):02d}:{int(s % 60):02d}"


def put(src, rel):
    """copy src into the site unless an identical copy (size + mtime) is already there"""
    dst = SITE / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not (dst.exists() and dst.stat().st_size == src.stat().st_size and int(dst.stat().st_mtime) == int(src.stat().st_mtime)):
        shutil.copy2(src, dst)
    return rel


def hls(src):
    """the site gateway drops a 1.6 GB single upload (HTTP 502): the film goes up as 6 s HLS segments, stream copy
    (the render puts a keyframe every 2 s). Re-cut only when the film is newer than the playlist."""
    d = SITE / "film"
    m3u = d / "index.m3u8"
    if not (m3u.exists() and m3u.stat().st_mtime >= src.stat().st_mtime):
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("seg*.ts"):
            f.unlink()
        subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-c", "copy", "-f", "hls", "-hls_time", "6",
                        "-hls_playlist_type", "vod", "-hls_segment_filename", str(d / "seg%04d.ts"), str(m3u)], check=True)
    return "film/index.m3u8"


def music_plan():
    """voice/music.py's cue plan ($ARENA_DATA/video/music/plan.json)"""
    plan = ARENA_DATA / "video/music/plan.json"
    if not plan.exists():
        raise SystemExit(f"no music plan at {plan} (see voice/music.py)")
    return {k: [tuple(c) for c in v] for k, v in json.loads(plan.read_text(encoding="utf-8")).items()}


def main():
    spans = json.loads((V / "chapters.json").read_text(encoding="utf-8"))
    titles = json.loads((V / "chapters-titles.json").read_text(encoding="utf-8"))
    plan = music_plan()
    src = {pathlib.Path(c["track_file"]).stem: c for c in json.loads(CUE.read_text(encoding="utf-8"))}
    film = hls(V / "programme-v1108-music.mp4")
    total = spans[-1]["end"]

    def track_name(stem):
        return src[stem]["title"] if stem in src else stem

    chap, music_rows = [], []
    for sp in spans:
        L = sp["end"] - sp["start"]
        cues = plan[sp["item"]]
        names = " → ".join(track_name(s) for _, s in cues)
        chap.append(f'<button data-t="{sp["start"]:.1f}"><span class="t">{mmss(sp["start"])}</span>'
                    f'<span>{E(titles[sp["item"]])}<span class="m">♪ {E(names)}</span></span></button>')
        for k, (f, stem) in enumerate(cues):
            a = sp["start"] + f * L
            b = sp["start"] + (cues[k + 1][0] * L if k + 1 < len(cues) else L)
            c = src.get(stem, {})
            parts = c.get("source", "").split(" / ")
            url = parts[2] if len(parts) > 2 else ""
            music_rows.append(f"<tr><td>{mmss(a)}–{mmss(b)}</td><td>{E(titles[sp['item']])}</td><td>{E(track_name(stem))}</td>"
                              f"<td>{E(parts[1] if len(parts) > 1 else '')}</td><td>{f'<a href=\"{E(url)}\">来源</a>' if url else ''}</td></tr>")

    covers = []
    for name, cap in [("cover-16x10.png", "B 站投稿封面 16:10（1920×1200）"), ("cover-16x9.png", "16:9（1920×1080）"),
                      ("cover-4x3-check.png", "4:3 居中裁切检查（标题和两张脸都在）")]:
        rel = put(P / "cover" / name, f"cover/{name}")
        covers.append(f'<figure><a href="{rel}"><img src="{rel}" loading="lazy" alt="{E(cap)}"></a><figcaption>{E(cap)}</figcaption></figure>')

    raws = []
    rd = P / "raw-footage"
    order = list(GAMES)
    for mp4 in sorted(rd.glob("*.mp4"), key=lambda f: (order.index(f.stem.split("-")[0]), f.stem == "G-a", f.stem)):
        if mp4.stem.endswith("-console"):
            continue
        tm = json.loads((rd / f"{mp4.stem}.timing.json").read_text(encoding="utf-8"))
        gt = f'游戏内 {tm["map"][0][1]}–{tm["map"][-1][1]}，{tm["duration"]:.0f} 秒'
        game = GAMES.get(mp4.stem.split("-")[0], "")
        con = rd / f"{mp4.stem}-console.mp4"
        cells = [f'<div><video src="{put(mp4, f"raw/{mp4.name}")}" controls preload="metadata" playsinline></video><div class="cap">原版游戏快进画面</div></div>']
        if con.exists():
            cells.append(f'<div><video src="{put(con, f"raw/{con.name}")}" controls preload="metadata" playsinline></video>'
                         f'<div class="cap">同步的指挥官命令窗口（只留游戏指令）</div></div>')
        else:
            cells.append('<div class="cap">G-a1 与 G-a2 的合并版，命令窗口见上面两段</div>')
        raws.append(f'<div class="raw"><h3>{E(mp4.stem)} · {E(game)}</h3><div class="cap">{E(gt)}</div><div class="pair">{"".join(cells)}</div></div>')

    cred = (P / "credits" / "credits.md").read_text(encoding="utf-8")
    cred = re.sub(r"`?[A-Za-z]:[\\/][^\s`）)，。;；]*`?", "（本机文件）", cred)          # no local paths on the page
    cred = re.sub(r"逐条出处见同目录 `credits-evidence.md`。?", "", cred)
    cred_html = markdown.markdown(cred, extensions=["sane_lists"])

    bili = (V / "chapters-bilibili.txt").read_text(encoding="utf-8").strip()
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AI军事演习 预览</title><style>{CSS}</style></head><body><main>
<header><h1>AI军事演习 · 成片预览</h1><div class="sub">v1108 · {total / 60:.1f} 分钟 · 手机字号版 · Kimi 对 DeepSeek，Claude 与 GPT 解说</div></header>
<nav class="toc"><a href="#film-sec">成片</a><a href="#cover">封面</a><a href="#bili">B 站章节</a><a href="#music">背景音乐</a>
<a href="#raw">原始画面</a><a href="#credits">片尾致谢</a><a href="#more">其他</a></nav>
<h2 id="film-sec">成片</h2>
<video id="film" data-src="{film}" controls preload="metadata" playsinline poster="cover/cover-16x9.png"></video>
<div class="chap">{"".join(chap)}</div>
<ul class="links">{"".join(f'<li><a href="{E(u)}">{E(t)}</a></li>' for t, u in LINKS[:2])}</ul>
<h2 id="cover">封面</h2><div class="covers">{"".join(covers)}</div>
<h2 id="bili">B 站章节（可直接粘贴）</h2><textarea id="bili-t" readonly>{E(bili)}</textarea>
<button class="copy" data-for="bili-t">复制</button>
<h2 id="music">背景音乐</h2><div class="tw"><table><tr><th>时间</th><th>章节</th><th>曲目</th><th>作曲</th><th></th></tr>
{"".join(music_rows)}</table></div>
<h2 id="raw">原始画面素材（可选插入）</h2><div class="sub">原版游戏快进 + 指挥官命令窗口，命令窗口只显示游戏指令，不含本机目录和文件。</div>
{"".join(raws)}
<h2 id="credits">片尾致谢清单</h2><div class="credits">{cred_html}</div>
<h2 id="more">其他</h2><ul class="links">{"".join(f'<li><a href="{E(u)}">{E(t)}</a></li>' for t, u in LINKS[2:])}</ul>
<h3>还没解决的</h3><ul class="issues">{"".join(f"<li>{E(x)}</li>" for x in ISSUES)}</ul>
</main><script src="js/hls.min.js"></script><script>{JS}</script></body></html>"""
    (SITE / "index.html").write_text(page, encoding="utf-8")
    print(SITE / "index.html")


if __name__ == "__main__":
    main()
