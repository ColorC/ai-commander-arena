# compose_console.py -- composites the commander console (right 35%, 672 px) beside the original-game footage
# (left, scaled to fit 1248x1080) -> 1920x1080 mp4. Console frames are drawn with PIL and piped to ffmpeg.
# Inputs : $ARENA_DATA/video/program/raw-footage/<id>.mp4, <id>.timing.json, console/<id>.json
# Output : $ARENA_DATA/video/program/raw-footage/<id>-console.mp4
# Usage  : python compose_console.py --all             (every clip whose footage+timing exist; reports the rest)
#          python compose_console.py M2-a G-a1         (specific clips)
#          python compose_console.py --preview M2-a    (placeholder background, writes console/preview-<id>.mp4)
#          add --force to re-render clips whose output already exists
import json, os, re, subprocess, sys, math
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_console_track as B
import pathlib

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[3] / "data")

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")          # ffmpeg on PATH unless FFMPEG names the executable
RAW = ARENA_DATA.as_posix() + '/video/program/raw-footage'
CON = RAW + '/console'
W, HH = 1920, 1080
CW = 672                      # console width = 35% of the frame
VW = W - CW                   # footage area 1248
FS = 19                       # font size
# fonts by file name (Pillow looks them up in the system font folder) or a path: ARENA_FONT_MONO / ARENA_FONT_MONO_BOLD / ARENA_FONT_CJK
LAT = ImageFont.truetype(os.environ.get('ARENA_FONT_MONO', 'consola.ttf'), FS)
LATB = ImageFont.truetype(os.environ.get('ARENA_FONT_MONO_BOLD', 'consolab.ttf'), FS)
CJK = ImageFont.truetype(os.environ.get('ARENA_FONT_CJK', 'msyh.ttc'), FS - 1)
CELL = LAT.getlength('M')     # monospace cell
LH = 27                       # line height
PADX, TOP, BOT = 22, 92, 16   # title bar height = TOP
COLS = int((CW - 2 * PADX - 8) // CELL)

BG = (9, 13, 11)
C = {0: (92, 170, 255), 1: (255, 106, 94)}       # blue / red headers
GREEN = (166, 227, 161)
AMBER = (255, 191, 71)
DIM = (96, 124, 100)


def cw(ch):
    return 2 if ord(ch) > 0x2e80 or ch in '…—·▸' and False else 1


def wrap(text, cols, indent=''):
    """wrap by monospace cells (CJK = 2 cells)"""
    lines, cur, n = [], indent, len(indent)
    for ch in text:
        w = 2 if ord(ch) >= 0x2e80 else 1
        if ch in '，。、；：！？）”…' and n + w <= cols + 2:
            cur += ch
            n += w
            continue
        if n + w > cols or (ch == ' ' and n == len(indent) and cur == indent):
            if ch == ' ':
                continue
            # break at last space if the line holds a latin word
            sp = cur.rfind(' ')
            if sp > len(indent) and ord(ch) < 0x2e80:
                lines.append(cur[:sp])
                cur = indent + cur[sp + 1:]
                n = sum(2 if ord(c) >= 0x2e80 else 1 for c in cur)
            else:
                lines.append(cur)
                cur, n = indent, len(indent)
        cur += ch
        n += w
    if cur.strip():
        lines.append(cur)
    return lines


def put(d, x, y, s, color, bold=False):
    base = y + FS          # shared baseline so Latin and CJK glyphs line up
    for ch in s:
        if ord(ch) >= 0x2e80:
            d.text((x, base), ch, font=CJK, fill=color, anchor='ls')
            x += CELL * 2
        else:
            d.text((x, base), ch, font=LATB if bold else LAT, fill=color, anchor='ls')
            x += CELL


def layout(track):
    """-> list of entries: {'t', 'lines': [(text,color,bold)], 'side'}"""
    out = []
    cols = COLS - 1
    for e in track['entries']:
        ls = [('[%s] %s %s' % (e['clock'][:5], e['side'], e['model']), C[e['side_id']], True)]
        if e['summary']:
            ls += [(t, GREEN, False) for t in wrap(e['summary'], cols, '  ')]
        for c in e['commands']:
            w = wrap(c, cols, '')
            for i, t in enumerate(w):
                ls.append((('▸ ' if i == 0 else '  ') + t if False else (('> ' if i == 0 else '  ') + t), AMBER, False))
        ls.append(('', GREEN, False))
        out.append({'t': e['t'], 'lines': ls, 'side': e['side_id']})
    return out


def parse_clock(s):
    p = [int(x) for x in s.split(':')]
    while len(p) < 3:
        p.append(0)
    return p[0] * 3600 + p[1] * 60 + p[2]


def make_game_clock(tmap):
    pts = sorted((float(v), parse_clock(g)) for v, g in tmap)
    # unwrap midnight
    for i in range(1, len(pts)):
        while pts[i][1] < pts[i - 1][1] - 43200:
            pts[i] = (pts[i][0], pts[i][1] + 86400)

    def gt(v):
        if v <= pts[0][0]:
            return pts[0][1]
        for (v0, g0), (v1, g1) in zip(pts, pts[1:]):
            if v <= v1:
                return g0 + (g1 - g0) * ((v - v0) / (v1 - v0) if v1 > v0 else 1)
        return pts[-1][1]
    return gt


class Console:
    def __init__(self, track):
        self.track = track
        self.ents = layout(track)
        # tall image with all entries
        n = sum(len(e['lines']) for e in self.ents)
        self.tall = Image.new('RGB', (CW, max(n * LH + 40, 100)), BG)
        d = ImageDraw.Draw(self.tall)
        y = 0
        self.ys = []
        for e in self.ents:
            y0 = y
            for t, col, b in e['lines']:
                put(d, PADX, y + 2, t, col, b)
                y += LH
            d.rectangle([8, y0 + 3, 11, y - LH - 2 + LH - 3], fill=C[e['side']])
            self.ys.append((y0, y, [y0 + (i + 1) * LH for i in range(len(e['lines']))]))
        self.off = 0.0
        self.appear = {}

    def frame(self, gt, vt, dt):
        VH = HH - TOP - BOT
        shown = [i for i, e in enumerate(self.ents) if e['t'] <= gt]
        for i in shown:
            self.appear.setdefault(i, vt)
        bottom = 0
        if shown:
            i = shown[-1]
            y0, y1, ends = self.ys[i]
            k = min(len(ends), int((vt - self.appear[i]) * 9) + 1)       # line-by-line reveal, 9 lines/s
            bottom = ends[k - 1]
        tgt = max(0, bottom - VH + 8)
        self.off += (tgt - self.off) * (1 - math.exp(-dt * 7))
        if abs(tgt - self.off) < 0.5:
            self.off = tgt
        o = int(round(self.off))
        img = Image.new('RGB', (CW, HH), BG)
        crop = self.tall.crop((0, o, CW, o + VH))
        # hide not-yet-revealed lines
        vis = max(0, min(VH, bottom - o))
        if vis > 0:
            img.paste(crop.crop((0, 0, CW, vis)), (0, TOP))
        d = ImageDraw.Draw(img)
        if not shown:
            put(d, PADX, TOP + 10, '_ standing by …', DIM)
        # title bar
        d.rectangle([0, 0, CW, TOP - 8], fill=(14, 22, 17))
        d.line([0, TOP - 8, CW, TOP - 8], fill=(40, 70, 48))
        put(d, PADX, 10, 'COMMAND CONSOLE', AMBER, True)
        h, m, s = int(gt // 3600) % 24, int(gt // 60) % 60, int(gt) % 60
        ck = 'GAME %02d:%02d:%02d' % (h, m, s)
        put(d, CW - PADX - CELL * len(ck), 10, ck, GREEN, True)
        put(d, PADX, 40, '蓝', C[0], True)
        put(d, PADX + CELL * 2, 40, ' ' + str(self.track.get('blue') or ''), C[0])
        put(d, CW // 2, 40, '红', C[1], True)
        put(d, CW // 2 + CELL * 2, 40, ' ' + str(self.track.get('red') or ''), C[1])
        d.line([0, 0, 0, HH], fill=(40, 70, 48), width=2)
        return img


def render(cid, footage, timing_path, track, out, label=None):
    tm = json.load(open(timing_path, encoding='utf8'))
    fps = float(tm['fps'])
    nfr = int(round(float(tm['duration']) * fps))
    gt = make_game_clock(tm['map'])
    con = Console(track)
    # footage that marks where the game picture sits ("content": [x, y, w, h], build_clip.py) is cropped to it first,
    # so the map, not the dark padding around it, fills the 1248x1080 area
    crop = 'crop=%d:%d:%d:%d,' % (tm['content'][2], tm['content'][3], tm['content'][0], tm['content'][1]) if tm.get('content') else ''
    cmd = [FFMPEG, '-y', '-v', 'error', '-i', footage,
           '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '%dx%d' % (CW, HH), '-r', repr(fps), '-i', '-',
           '-filter_complex',
           '[0:v]%sscale=%d:%d:force_original_aspect_ratio=decrease:flags=lanczos,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=%s[l];'
           '[l][1:v]hstack=inputs=2:shortest=1[v]' % (crop, VW, HH, VW, HH, repr(fps)),
           '-map', '[v]', '-map', '0:a?', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
           '-c:a', 'aac', '-b:a', '128k', '-t', '%.3f' % float(tm['duration']), '-movflags', '+faststart', out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(nfr):
            vt = i / fps
            p.stdin.write(con.frame(gt(vt), vt, 1 / fps).tobytes())
        p.stdin.close()
    except BrokenPipeError:
        pass
    rc = p.wait()
    if rc != 0:
        raise SystemExit('ffmpeg failed for %s (rc=%s)' % (cid, rc))
    print('wrote', out)


def make_placeholder(cid, track):
    """synthetic stand-in footage + timing (60 s, 25 fps, 1280x720) spanning the clip's game-time range"""
    os.makedirs(CON + '/_placeholder', exist_ok=True)
    mp4 = '%s/_placeholder/%s.mp4' % (CON, cid)
    tj = '%s/_placeholder/%s.timing.json' % (CON, cid)
    subprocess.run([FFMPEG, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=1280x720:r=25:d=60',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'veryfast', mp4], check=True)
    a, b = parse_clock(track['from']), parse_clock(track['to'])
    json.dump({'fps': 25, 'duration': 60.0, 'map': [[0, B.clock(a)], [60, B.clock(b)]]}, open(tj, 'w'))
    return mp4, tj


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = [a for a in sys.argv[1:] if a.startswith('--')]
    os.makedirs(CON, exist_ok=True)
    if '--preview' in flags:
        for cid in args:
            B.main_ids = None
            tr = B.build(cid)
            assert not B.check(tr), 'sanitiser scan failed'
            mp4, tj = make_placeholder(cid, tr)
            render(cid, mp4, tj, tr, '%s/preview-%s.mp4' % (CON, cid))
        return
    ids = args or list(B.CLIPS)
    waiting = []
    for cid in ids:
        foot, tj, cj = '%s/%s.mp4' % (RAW, cid), '%s/%s.timing.json' % (RAW, cid), '%s/%s.json' % (CON, cid)
        out = '%s/%s-console.mp4' % (RAW, cid)
        if not (os.path.exists(foot) and os.path.exists(tj)):
            waiting.append(cid)
            continue
        if os.path.exists(out) and '--force' not in flags:
            print('exists, skip (use --force):', out)
            continue
        tr = B.build(cid)
        bad = B.check(tr)
        if bad:
            raise SystemExit('sanitiser regex scan FAILED: %s' % bad[:3])
        json.dump(tr, open(cj, 'w', encoding='utf8'), ensure_ascii=False, indent=1)
        render(cid, foot, tj, tr, out)
    if waiting:
        print('WAITING for footage/timing:', ', '.join(waiting))


if __name__ == '__main__':
    main()
