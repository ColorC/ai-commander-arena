"""The staging review page: every game, segment and shot on one page, in programme order, with its key still, shot size,
game time, what it shows, the lines and the commander cards; each segment carries its review status.

    python -I -X utf8 build_review.py [--site <dir>]

Reads the staging files and commentary scripts (manifest.json), the stills render_all.py left in <site>/img and <site>/thumb,
and <site>/review.json ({"F-03": {"status": "确认", "note": "..."}}; missing = 待审). Writes <site>/index.html.
Default site: $ARENA_DATA/video/program/site-staging-review."""
import html
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from compile_staging import lines_of  # noqa: E402
import os

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[3] / "data")

ST = ARENA_DATA / "video/program/staging"
# the programme (rundown-v1.md) in order: finished films (film/<name>.mp4 + its timeline) and staging games by case;
# a part with neither is listed as not made yet
PROGRAM = [
    ("A 冷开场 + B 开场白", [("film", "AB-opening", "冷开场、片头、裁判与两位解说、两位指挥官登场")]),
    ("C 游戏怎么玩", [("film", "C-intro", "游戏介绍、指挥周期、命令准备时间、命令行指挥工具")]),
    ("D 马尔堡：想定与比赛规则", [("film", "D0-intro-marburg", "地图、兵力、命令；训练与陪练、计分")]),
    ("演习：Kimi", [("case", "T1"), ("case", "T3")]),
    ("演习：DeepSeek", [("case", "T2"), ("case", "T4")]),
    ("马尔堡正赛", [("case", "M1"), ("case", "M2")]),
    ("E 调令：明登", [("film", "E0-intro-minden", "换图、任务原文、兵力、目标格三倍计分")]),
    ("F 第一仗", [("case", "F")]),
    ("G 第二仗", [("case", "G")]),
    ("H 结算与复盘", []),
]
ZOOM = {"close": "近景", "mid": "中景", "wide": "远景"}
STATUS = {"确认": "ok", "待改": "fix", "待审": "todo"}
E = html.escape

CSS = """
:root{--bg:#1b1c18;--panel:#24251f;--line:#3a3a30;--ink:#e8e4d6;--dim:#a9a593;--gold:#e9c35a;--blue:#8fb8ef;--red:#f0a090;--ok:#7fbf6a;--fix:#e0904a;--todo:#8a8778}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 "Microsoft YaHei","Noto Sans SC",system-ui,sans-serif}
a{color:var(--blue)}header{padding:14px 16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:rgba(27,28,24,.96);z-index:5}
header h1{font-size:19px;margin:0 0 4px}header .sum{color:var(--dim);font-size:13px}header .bar{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
header button{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:4px;padding:3px 10px;font:inherit;font-size:13px;cursor:pointer}
header button.on{border-color:var(--gold);color:var(--gold)}
.wrap{display:flex;max-width:1680px;margin:0 auto}nav{width:230px;flex:none;padding:12px 10px 40px 16px;position:sticky;top:96px;align-self:flex-start;max-height:calc(100vh - 96px);overflow:auto;font-size:13px}
nav .g{color:var(--gold);margin:10px 0 2px}nav a{display:flex;align-items:center;gap:6px;color:var(--ink);text-decoration:none;padding:1px 4px;border-radius:3px}nav a:hover{background:var(--panel)}
.dot{width:8px;height:8px;border-radius:50%;flex:none;background:var(--todo)}.dot.ok{background:var(--ok)}.dot.fix{background:var(--fix)}
main{flex:1;min-width:0;padding:8px 16px 60px}section.game>h2{font-size:18px;color:var(--gold);margin:26px 0 2px}section.game>.who{color:var(--dim);font-size:13px}
section.seg{margin:18px 0 10px;border-top:1px solid var(--line);padding-top:10px}section.seg h3{font-size:16px;margin:0;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.badge{font-size:12px;padding:0 8px;border-radius:10px;border:1px solid}.badge.ok{color:var(--ok);border-color:var(--ok)}.badge.fix{color:var(--fix);border-color:var(--fix)}.badge.todo{color:var(--todo);border-color:var(--todo)}
.note{color:var(--dim);font-size:13px;margin:2px 0 6px}
.shot{display:grid;grid-template-columns:minmax(0,560px) minmax(0,1fr);gap:14px;margin:12px 0;padding:10px;background:var(--panel);border:1px solid var(--line);border-radius:4px}
.shot img{width:100%;height:auto;display:block;border:1px solid #000}.shot .noimg{aspect-ratio:16/9;display:flex;align-items:center;justify-content:center;color:var(--todo);border:1px dashed var(--line)}
.meta{font-size:13px;color:var(--dim)}.meta b{color:var(--ink);font-weight:700}.shows{margin:4px 0 6px}
.ln{margin:3px 0}.ln .sp{display:inline-block;min-width:52px;color:var(--gold);font-weight:700}
.card{margin:6px 0;padding:4px 8px;border-left:3px solid var(--dim);background:rgba(0,0,0,.18);font-size:14px}.card.s0{border-color:#4f93ea}.card.s1{border-color:#e0473b}
.card .k{font-size:12px;color:var(--dim)}
body.only-todo section.seg.ok{display:none}
h1.part{font-size:20px;color:var(--ink);margin:34px 0 0;padding:6px 10px;background:var(--panel);border-left:4px solid var(--gold)}
nav .part{color:var(--ink);font-weight:700;margin:14px 0 2px;border-top:1px solid var(--line);padding-top:6px}
.film{margin:8px 0}.film video{width:100%;max-width:1100px;display:block;background:#000;border:1px solid #000}
@media (max-width:900px){nav{display:none}.shot{grid-template-columns:1fr}header{position:static}}
"""


def side_no(s):
    return 1 if s in ("red", 1) else 0


def main(argv):
    site = pathlib.Path(argv[argv.index("--site") + 1] if "--site" in argv else ARENA_DATA.as_posix() + "/video/program/site-staging-review")
    rv_p = site / "review.json"
    review = json.loads(rv_p.read_text(encoding="utf-8")) if rv_p.exists() else {}
    man = {m["case"]: m for m in json.loads((ST / "manifest.json").read_text(encoding="utf-8"))}
    nav, body, cnt = [], [], {"ok": 0, "fix": 0, "todo": 0}
    n_img = n_shot = n_vid = 0
    for part, items in PROGRAM:
        pid = "p-" + part.split()[0]
        nav.append(f'<div class="part">{E(part)}</div>')
        body.append(f'<h1 class="part" id="{pid}">{E(part)}</h1>')
        if not items:
            body.append('<div class="note">还没做。</div>')
        for it in items:
            if it[0] == "film":
                _, name, what = it
                r = review.get(name, {}); stt = r.get("status", "待审"); cls = STATUS.get(stt, "todo"); cnt[cls] += 1
                nav.append(f'<a href="#{name}"><span class="dot {cls}"></span>{E(what[:10])}…</a>')
                vid = (f'<video controls preload="metadata" src="film/{name}.mp4"></video>' if (site / "film" / f"{name}.mp4").exists() else '<div class="noimg">还没有成片</div>')
                body.append(f'<section class="seg {cls}" id="{name}"><h3>{E(what)} <span class="badge {cls}">{E(stt)}</span>'
                            f'<span class="meta">{name} · 11-06 成片（不走演出稿，镜头表见 <a href="film/{name}-timeline.md">时间表</a>）</span></h3>'
                            + (f'<div class="note">{E(r["note"])}</div>' if r.get("note") else "") + f'<div class="film">{vid}</div></section>')
                continue
            sec = game(man[it[1]], review, site, nav, body, cnt)
            n_img += sec[0]; n_shot += sec[1]; n_vid += sec[2]
    total = sum(cnt.values())
    page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>演出审阅</title><style>{CSS}</style></head><body>
<header><h1>演出审阅：整期节目按编排顺序</h1>
<div class="sum">{total} 段：已确认 {cnt['ok']} · 待改 {cnt['fix']} · 待审 {cnt['todo']}；演出段 {n_shot} 个镜头，已出图 {n_img}，已出视频 {n_vid} 段（审阅用 3 Mbps，无配音只有字幕）。点关键帧看 1920 原图。
<a href="time-audit.md">台词时间核对报告</a></div>
<div class="bar"><button id="todo">只看未确认</button></div></header>
<div class="wrap"><nav>{''.join(nav)}</nav><main>{''.join(body)}</main></div>
<script>const b=document.getElementById('todo');b.onclick=()=>{{document.body.classList.toggle('only-todo');b.classList.toggle('on')}};</script>
</body></html>"""
    (site / "index.html").write_text(page, encoding="utf-8")
    print(site / "index.html", f"{n_img}/{n_shot} stills, {n_vid} videos")


def game(m, review, site, nav, body, cnt):
    """One staging game: its segments (video when rendered, then the shots with key stills)."""
    n_img = n_shot = n_vid = 0
    if True:
        case = m["case"]
        st = json.loads((ST / f"{case}.json").read_text(encoding="utf-8"))
        lines = lines_of(pathlib.Path(m["commentary"]).read_text(encoding="utf-8"))
        nav.append(f'<div class="g">{E(m["title"])}</div>')
        body.append(f'<section class="game" id="{case}"><h2>{E(m["title"])}</h2><div class="who">蓝军 {E(m["blue"])} · 红军 {E(m["red"])}</div>')
        for S in st["segments"]:
            sid = f"{case}-{S['seg']:02d}"
            r = review.get(sid, {}); stt = r.get("status", "待审"); cls = STATUS.get(stt, "todo"); cnt[cls] += 1
            nav.append(f'<a href="#{sid}"><span class="dot {cls}"></span>第 {S["seg"]} 段 · {len(S["shots"])} 镜</a>')
            body.append(f'<section class="seg {cls}" id="{sid}"><h3>第 {S["seg"]} 段 <span class="badge {cls}">{E(stt)}</span>'
                        f'<span class="meta">{sid} · {len(S["shots"])} 个镜头</span></h3>'
                        + (f'<div class="note">{E(r["note"])}</div>' if r.get("note") else ""))
            if (site / "video" / f"{sid}.mp4").exists():
                n_vid += 1
                body.append(f'<div class="film"><video controls preload="none" poster="thumb/{sid}-{S["shots"][0]["id"]}.jpg" src="video/{sid}.mp4"></video></div>')
            segl = lines.get(S["seg"], [])
            for sh in S["shots"]:
                n_shot += 1
                stem = f"{sid}-{sh['id']}"
                if (site / "img" / f"{stem}.jpg").exists():
                    n_img += 1
                    pic = f'<a href="img/{stem}.jpg" target="_blank"><img loading="lazy" src="thumb/{stem}.jpg" alt="{E(sh["id"])}"></a>'
                else:
                    pic = '<div class="noimg">还没出图</div>'
                tm = sh["time"]
                t = f'{tm["from"]} → {tm["to"]} 播放' if "from" in tm else f'定格 {tm["at"]}'
                ls = "".join(f'<div class="ln"><span class="sp">{E(segl[i]["speaker"])}</span>{E(segl[i]["text"])}</div>' for i in sh["lines"] if i < len(segl))
                cards = "".join(
                    f'<div class="card s{side_no(q.get("side"))}"><span class="k">{"作战命令" if q.get("kind") == "order" else "指挥官原话"} · '
                    f'{E(m["red"] if side_no(q.get("side")) else m["blue"])}{" · " + E(q["time"]) if q.get("time") else ""}</span><br>{E(q.get("zh") or q.get("text", ""))}</div>'
                    for q in sh.get("cues", []) if q["type"] == "card")
                body.append(f'<div class="shot"><div>{pic}</div><div><div class="meta"><b>{E(sh["id"])}</b> · {ZOOM.get(sh["zoom"], sh["zoom"])} · {E(t)}'
                            f'（定格取最后一句开口 1 秒）</div><div class="shows">{E(sh.get("shows", ""))}</div>{ls}{cards}</div></div>')
            body.append("</section>")
        body.append("</section>")
    return n_img, n_shot, n_vid


if __name__ == "__main__":
    main(sys.argv[1:])
