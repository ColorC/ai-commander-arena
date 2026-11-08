"""Review page for one programme segment: web preview video, keyframes, shot timeline (Markdown table -> HTML).

    python -I -X utf8 make_site.py <site dir> <title> <sub (html)> <web mp4[|label],...> <timeline.md> <still> [<still> ...] [-- <note html> ...]

Stills are copied into the site dir; the web mp4 must already be there.
"""
import html
import pathlib
import re
import shutil
import sys

args = sys.argv[1:]
notes = []
if '--' in args:
    i = args.index('--')
    args, notes = args[:i], args[i + 1:]
site, title, sub, web, tl = pathlib.Path(args[0]), args[1], args[2], args[3], pathlib.Path(args[4])
stills = [pathlib.Path(p) for p in args[5:]]
site.mkdir(parents=True, exist_ok=True)


def inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r'`([^`]+)`', r'<code>\1</code>', s)
    return re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', s)


def md2html(md):
    out, table, lst = [], None, False
    for line in md.splitlines():
        if line.startswith('|'):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if all(re.fullmatch(r'-+', c) for c in cells):
                continue
            if table is None:
                table = ['<table><thead><tr>' + ''.join(f'<th>{inline(c)}</th>' for c in cells) + '</tr></thead><tbody>']
            else:
                table.append('<tr>' + ''.join(f'<td>{inline(c)}</td>' for c in cells) + '</tr>')
            continue
        if table is not None:
            out.append(''.join(table) + '</tbody></table>')
            table = None
        if line.startswith('- '):
            if not lst:
                out.append('<ul>')
                lst = True
            out.append(f'<li>{inline(line[2:])}</li>')
            continue
        if lst:
            out.append('</ul>')
            lst = False
        m = re.match(r'(#+) (.*)', line)
        if m:
            lv = min(len(m.group(1)) + 1, 4)
            out.append(f'<h{lv}>{inline(m.group(2))}</h{lv}>')
        elif line.strip():
            out.append(f'<p>{inline(line)}</p>')
    if table is not None:
        out.append(''.join(table) + '</tbody></table>')
    if lst:
        out.append('</ul>')
    return '\n'.join(out)


vids = []
for w in web.split(','):
    f, _, lab = w.partition('|')
    vids.append((f'<h3>{html.escape(lab)}</h3>' if lab else '') + f'<video controls preload="metadata" src="{f}"></video>')
figs = []
for p in stills:
    shutil.copy2(p, site / p.name)
    figs.append(f'<figure><a href="{p.name}"><img src="{p.name}" loading="lazy" alt=""></a><figcaption>{html.escape(p.stem)}</figcaption></figure>')
page = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{--bg:#141413;--panel:#21211e;--line:#4a4a42;--ink:#e6e3d6;--mute:#aaa794;--gold:#f2c84b;--blue:#8fb4f0;--red:#f08a7c}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 "Noto Sans SC","Microsoft YaHei",sans-serif}}
main{{max-width:1500px;margin:0 auto;padding:24px 16px 60px}}
h1{{font-size:28px;margin:0 0 4px;letter-spacing:2px}}
h2{{font-size:19px;margin:34px 0 10px;color:var(--gold);letter-spacing:2px;border-bottom:1px solid var(--line);padding-bottom:6px}}
h3,h4{{font-size:16px;margin:26px 0 8px}}
.sub{{color:var(--mute);margin:0 0 18px}}
video{{width:100%;background:#000;border:1px solid var(--line)}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
@media (max-width:900px){{.grid{{grid-template-columns:1fr 1fr}}}}
@media (max-width:560px){{.grid{{grid-template-columns:1fr}}}}
figure{{margin:0;background:var(--panel);border:1px solid var(--line);overflow:hidden}}
figure img{{width:100%;display:block}}
figcaption{{padding:4px 10px;font-size:12px;color:var(--mute)}}
.tw{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:13px;min-width:1100px}}
td,th{{border:1px solid var(--line);padding:5px 7px;vertical-align:top}}
th{{background:#2a2a26;position:sticky;top:0}}
td:nth-child(-n+5){{white-space:nowrap}}
ul{{padding-left:20px;margin:6px 0}}
.b{{color:var(--blue)}}.r{{color:var(--red)}}
a{{color:var(--gold)}}
code{{font-family:Consolas,monospace;font-size:12.5px;background:#2a2a26;padding:1px 5px;border-radius:3px;word-break:break-all}}
</style>
</head>
<body>
<main>
<h1>{html.escape(title)}</h1>
<p class="sub">{sub}</p>
{''.join(vids)}
{''.join(f'<p>{n}</p>' for n in notes)}
<h2>关键帧（渲染前自查用，原尺寸 1920×1080，点开看大图）</h2>
<div class="grid">{''.join(figs)}</div>
<h2>镜头时间表</h2>
<div class="tw">{md2html(tl.read_text(encoding='utf-8'))}</div>
</main>
</body>
</html>
"""
(site / 'index.html').write_text(page, encoding='utf-8')
print(site / 'index.html')
