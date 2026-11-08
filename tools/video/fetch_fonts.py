"""Download subset web fonts (OFL) from Google Fonts and inline them as data: URLs.

    python -I -X utf8 fetch_fonts.py <source file> [<source file> ...] <out fonts.css>

Noto Sans SC (思源黑体) only carries the characters found in the source files (sandbox.js, shot-*.js) plus printable ASCII (Google's `text=` subset);
Oswald gets the same subset. Re-run whenever the UI gains new Chinese characters. The page and the SVG snapshot of the UI
overlay both use the generated CSS (data: URLs, so file:// pages and SVG images can load them without CORS).
"""
import base64
import re
import sys
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/145.0.0.0 Safari/537.36")
FAMILIES = ["Noto+Sans+SC:wght@500;700;900", "Oswald:wght@400;500;600;700", "JetBrains+Mono:wght@500"]
CHUNK = 150                                  # characters per request (see main)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main(paths, out_path):
    text = "".join(open(p, encoding="utf-8").read() for p in paths)
    chars = sorted(set(c for c in text if not c.isspace()) | set(chr(i) for i in range(32, 127)) | set("−·–—×★"))
    out = []
    # Google honours `text=` only up to a limited length (2026-11-05: 812 characters came back as the whole font in
    # ~100 unicode-range shards per weight, 18.7 MB). Request in chunks; every face carries a unicode-range (Google adds
    # one, else we do), so the browser combines the chunks of one family / weight. Oswald is Latin only: a CJK request
    # gets CSS whose font file gstatic then refuses (400), so it is asked for the non-CJK characters only.
    for fam in FAMILIES:
        cs = chars if fam.startswith("Noto") else [c for c in chars if ord(c) < 0x2E80]
        for chunk in [cs[i:i + CHUNK] for i in range(0, len(cs), CHUNK)]:
            url = "https://fonts.googleapis.com/css2?family=" + fam + "&display=block&text=" + \
                  urllib.parse.quote("".join(chunk))
            css = get(url).decode("utf-8")
            weights = len(fam.split("@", 1)[1].split(";")) if "@" in fam else 1
            if css.count("@font-face") > weights:     # the whole font in ~100 unicode-range shards per weight
                raise SystemExit(f"Google returned the unsubset font for {fam} ({len(chunk)} characters): lower CHUNK")
            if "unicode-range" not in css:            # older responses had none (Google adds it since about 2026-11)
                rng = ", ".join(f"U+{ord(c):04X}" for c in chunk)
                css = css.replace("font-display: block;", f"font-display: block;\n  unicode-range: {rng};")
            def inline(m):
                data = get(m.group(1))
                return "url(data:font/woff2;base64," + base64.b64encode(data).decode("ascii") + ") format('woff2')"
            css = re.sub(r"url\((https://[^)]+)\) format\('woff2'\)", inline, css)
            out.append(css)
    open(out_path, "w", encoding="utf-8").write("\n".join(out))
    print(out_path, sum(len(c) for c in out), "bytes,", len(chars), "chars")


if __name__ == "__main__":
    main(sys.argv[1:-1], sys.argv[-1])
