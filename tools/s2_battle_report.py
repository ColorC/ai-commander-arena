"""Season 2 battle report as one offline HTML file: the report source plus the map viewer (tools/s2_battle_maps.js)
and the map JSON of every game it shows (tools/s2_battle_maps.py output), all inlined.

python -I -X utf8 tools/s2_battle_report.py <report.src.html> <maps dir> <out.html>

In the source, `<!-- INLINE_JS -->`, `<!-- INLINE_DATA -->` and `<!-- BOOT -->` mark where the viewer, the data and
the start-up script go. Maps are placeholders the start-up script fills: `<div class="replay" data-game="formal-1">`,
`<figure class="snap" data-game="formal-1" data-t="<game sec>" data-hl="<hex,hex>" [data-cap="<map caption>"]>`
(its figcaption stays below the map; without data-cap the map is captioned with the game clock), `<div class="chart" data-game="formal-1">`, `<div class="legend">`. data-game names a <maps dir>/<name>.json.
"""
import json
import os
import re
import sys

BOOT = """<script>
(function () {
  BattleMap.lang = 'zh';
  function clock(sec) { var m = Math.floor(sec / 60); return String(7 + Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0'); }
  document.querySelectorAll('.replay').forEach(function (el) { BattleMap.replay(el, TERRAIN, GAMES[el.dataset.game], {}); });
  document.querySelectorAll('figure.snap').forEach(function (f) {
    var box = document.createElement('div');
    f.insertBefore(box, f.firstChild);
    var t = +f.dataset.t;
    var hl = (f.dataset.hl || '').split(',').filter(Boolean).map(Number);
    var o = f.dataset.cap ? {caption: f.dataset.cap, clock: false} : {caption: clock(t)};
    o.highlight = hl;
    BattleMap.snapshot(box, TERRAIN, GAMES[f.dataset.game], t, o);
  });
  document.querySelectorAll('.chart').forEach(function (el) { BattleMap.chart(el, GAMES[el.dataset.game], {}); });
  document.querySelectorAll('.legend').forEach(function (el) { BattleMap.legend(el); });
})();
</script>"""


def script_json(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def main(src, maps, out):
    html = open(src, encoding="utf-8").read()
    names = sorted(set(re.findall(r'data-game="([^"]+)"', html)))
    games = {}
    for n in names:
        p = os.path.join(maps, n + ".json")
        if not os.path.exists(p):
            sys.exit(f"missing map data for data-game={n}: {p}")
        games[n] = json.load(open(p, encoding="utf-8"))
    terrain = json.load(open(os.path.join(maps, "terrain.json"), encoding="utf-8"))
    js = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "s2_battle_maps.js"), encoding="utf-8").read()
    for mark in ("<!-- INLINE_JS -->", "<!-- INLINE_DATA -->", "<!-- BOOT -->"):
        if mark not in html:
            sys.exit(f"{src}: marker {mark} not found")
    data = f"<script>\nconst TERRAIN = {script_json(terrain)};\nconst GAMES = {script_json(games)};\n</script>"
    html = (html.replace("<!-- INLINE_JS -->", "<script>\n" + js.replace("</script", "<\\/script") + "\n</script>")
                .replace("<!-- INLINE_DATA -->", data).replace("<!-- BOOT -->", BOOT))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print(f"{out}: {len(html.encode('utf-8')) // 1024} KB, games {', '.join(names)}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
