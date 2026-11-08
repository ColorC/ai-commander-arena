"""Join the captured FPC turn replays (fpc/remote/job-vcr-capture.ps1) into one fast-forward clip per stretch.

    python -I -X utf8 build_clip.py <capture dir>[;<capture dir>...] [clip id ...]   default: every clip of clips.py
    (with several capture dirs, a save tag is taken from the last dir that has it: a re-capture overrides)

Input, per save tag (e.g. M1-0742): <dir>/frames/<tag>/NNNNNN.jpg and <dir>/frames-<tag>.tsv (idx, wall_ms, shot_ms,
minute, hash, descr). The replay shows its minute only now and then ("Game resolution replay for minute N", minutes
since the scenario start), so every frame gets a game time by linear interpolation between the frames where that
minute changes; the replay starts at the first minute it shows, and ends at the save's own time once the picture
stops changing. Frames before the first change (the paused start) are dropped except one, and so is the still tail.

A clip is the frames whose game time falls in its span(s), in order. Its length is 0.6 s per game minute, kept
within 30-90 s; half of it is spread by game time and half by the replay's own wall time, so quiet minutes (the
replay runs them at several minutes a second) still pass at a readable pace and combat (10 s and more a minute in
the replay) is not crushed. 30 fps, 1920x1080, H.264 (libx264, CRF 18, yuv420p): the map itself (clips.MAP_BOX, no
menu bar or gray background) scaled up evenly to the largest size that fits, centred on a dark background.
Caption at the top left of the map (the right part of the frame stays free): game name, game clock, "原版游戏画面 · 快放".

Writes <OUT>/<id>.mp4 and <OUT>/<id>.timing.json {"fps", "duration", "map": [[video seconds, "HH:MM"], ...],
"content": [x, y, w, h]} with one map entry at the start of every part and every time the shown game minute changes;
"content" is where the map sits in the frame (compose_console.py crops to it).
"""
import bisect
import csv
import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clips  # noqa: E402

FPS = 30
W, H = 1920, 1080
SEC_PER_MIN, T_MIN, T_MAX = 0.6, 30.0, 90.0
# CJK fonts by file name (Pillow looks them up in the system font folder) or a path: ARENA_FONT_CJK / ARENA_FONT_CJK_BOLD
FONT = os.environ.get("ARENA_FONT_CJK", "msyh.ttc")
FONT_B = os.environ.get("ARENA_FONT_CJK_BOLD", "msyhbd.ttc")
LABEL = "原版游戏画面 · 快放"
BG = (14, 16, 18)


def load_segment(caps, tag, end_min):
    """[(path, wall seconds, game minute since start)] of one save's replay, trimmed to its moving part."""
    have = [c for c in caps if os.path.exists(os.path.join(c, "frames-%s.tsv" % tag))]
    if not have:
        raise SystemExit("no capture of " + tag)
    cap = have[-1]
    rows = []
    with open(os.path.join(cap, "frames-%s.tsv" % tag), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            rows.append((int(r["idx"]), int(r["wall_ms"]) / 1000.0, int(r["minute"]), r["hash"]))
    if not rows:
        return []
    first_hash = rows[0][3]
    start = next((i for i, r in enumerate(rows) if r[3] != first_hash), None)
    if start is None:
        return []
    last = max(i for i in range(1, len(rows)) if rows[i][3] != rows[i - 1][3])
    keep = rows[max(0, start - 1):last + 1]
    m0 = rows[start - 1][2] if start > 0 and rows[start - 1][2] >= 0 else end_min   # shown while paused at the start
    # anchors (wall, minute): the start, each frame where the shown minute changes, the end
    anchors = [(keep[0][1], float(m0))]
    for prev, cur in zip(keep, keep[1:]):
        if cur[2] > 0 and cur[2] != prev[2] and cur[2] > anchors[-1][1]:
            anchors.append((cur[1], float(min(cur[2], end_min))))
    if anchors[-1][1] < end_min:
        anchors.append((keep[-1][1], float(end_min)))
    ws = [a[0] for a in anchors]
    out = []
    for idx, wall, _, _ in keep:
        j = bisect.bisect_right(ws, wall) - 1
        if j < 0:
            t = anchors[0][1]
        elif j >= len(anchors) - 1:
            t = anchors[-1][1]
        else:
            (w0, t0), (w1, t1) = anchors[j], anchors[j + 1]
            t = t0 + (t1 - t0) * (wall - w0) / (w1 - w0) if w1 > w0 else t1
        out.append((os.path.join(cap, "frames", tag, "%06d.jpg" % idx), wall, t))
    return out


def clip_frames(cap, cid):
    """Parts of a clip: [[(path, wall, game minute since start)], ...], and the game's start minute of the day."""
    game, spans = clips.CLIPS[cid]
    start = clips.hm(clips.GAMES[game][1])
    parts = []
    for a, b in spans:
        a, b = clips.hm(a) - start, clips.hm(b) - start
        frames = []
        for t, _ in clips.needed(game, a + start, b + start)[0]:
            seg = load_segment(cap, clips.tag(game, t), t - start)
            frames += [f for f in seg if a <= f[2] <= b and (not frames or f[2] >= frames[-1][2])]
        parts.append(frames)
    return parts, start


def schedule(parts):
    """Video time of every frame: [(part index, frame index, video start seconds)], and the total duration."""
    gsum = sum(p[-1][2] - p[0][2] for p in parts if p)
    wsum = sum(sum(min(b[1] - a[1], 2.0) for a, b in zip(p, p[1:])) for p in parts)
    total = min(T_MAX, max(T_MIN, SEC_PER_MIN * gsum))
    sched, v = [], 0.0
    for pi, p in enumerate(parts):
        for fi, f in enumerate(p):
            sched.append((pi, fi, v))
            if fi + 1 < len(p):
                n = p[fi + 1]
                dg = (n[2] - f[2]) / gsum if gsum else 0
                dw = min(n[1] - f[1], 2.0) / wsum if wsum else 0
                v += total * (0.5 * dg + 0.5 * dw)
        v += 1.0 / FPS   # the last frame of a part shows for one frame
    return sched, v


def clock(start, t):
    m = int(start + t + 1e-6)
    return "%02d:%02d" % (m // 60 % 24, m % 60)


def caption(name, clk, cache={}):
    key = (name, clk)
    if key not in cache:
        f1, f2 = ImageFont.truetype(FONT_B, 30), ImageFont.truetype(FONT, 22)
        line1 = "%s   %s" % (name, clk)
        w = int(max(f1.getlength(line1), f2.getlength(LABEL))) + 36
        im = Image.new("RGBA", (w, 92), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((0, 0, w - 1, 91), radius=8, fill=(12, 14, 16, 190))
        d.text((18, 8), line1, font=f1, fill=(255, 255, 255, 255))
        d.text((18, 54), LABEL, font=f2, fill=(255, 214, 120, 255))
        cache[key] = im
    return cache[key]


def check_rim(path, box):
    """the white map rim must lie just outside the crop box, else the map moved in this capture"""
    im = Image.open(path).convert("L")
    x0, y0, x1, y1 = box
    pts = [(x, y0 - 2) for x in range(x0 + 50, x1 - 50, 97)] + [(x, y1 + 1) for x in range(x0 + 50, x1 - 50, 97)]
    pts += [(x0 - 2, y) for y in range(y0 + 50, y1 - 50, 97)] + [(x1 + 1, y) for y in range(y0 + 50, y1 - 50, 97)]
    bad = [p for p in pts if im.getpixel(p) < 215]
    if len(bad) > len(pts) // 4:   # unit counters at the map edge can cover a few rim points
        raise SystemExit("map rim not found around %s in %s (%d of %d points not white)" % (box, path, len(bad), len(pts)))


def fit(box):
    """(scaled size, offset) of the map box fitted into W x H, centred"""
    bw, bh = box[2] - box[0], box[3] - box[1]
    s = min(W / bw, H / bh)
    sw, sh = min(W, int(round(bw * s / 2)) * 2), min(H, int(round(bh * s / 2)) * 2)   # even: yuv420p crop later
    return (sw, sh), ((W - sw) // 4 * 2, (H - sh) // 4 * 2)


def build(cap, cid):
    game = clips.CLIPS[cid][0]
    name = clips.GAMES[game][2]
    parts, start = clip_frames(cap, cid)
    if not all(parts):
        raise SystemExit("%s: a part has no frames (%s)" % (cid, [len(p) for p in parts]))
    box = clips.MAP_BOX[game]
    for p in parts:
        check_rim(p[0][0], box)
    size, off = fit(box)
    sched, total = schedule(parts)
    nout = int(round(total * FPS))
    os.makedirs(clips.OUT, exist_ok=True)
    mp4 = os.path.join(clips.OUT, cid + ".mp4")
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H),
                           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                           "-pix_fmt", "yuv420p", "-movflags", "+faststart", mp4], stdin=subprocess.PIPE)
    vts = [s[2] for s in sched]
    mapping, shown, cur_path, base = [], None, None, None
    last_part = -1
    for k in range(nout):
        vt = k / FPS
        j = max(0, bisect.bisect_right(vts, vt) - 1)
        pi, fi, v0 = sched[j]
        f = parts[pi][fi]
        t = f[2]
        if j + 1 < len(sched) and sched[j + 1][0] == pi:
            n, v1 = parts[pi][sched[j + 1][1]], sched[j + 1][2]
            t = f[2] + (n[2] - f[2]) * (vt - v0) / (v1 - v0) if v1 > v0 else f[2]
        clk = clock(start, t)
        if pi != last_part or clk != shown:
            mapping.append([round(vt, 3), clk])
            shown, last_part = clk, pi
        if f[0] != cur_path:
            im = Image.open(f[0]).convert("RGB")
            if im.size != (W, H):
                raise SystemExit("unexpected frame size %s: %s" % (im.size, f[0]))
            base = Image.new("RGB", (W, H), BG)
            base.paste(im.crop(box).resize(size, Image.LANCZOS), off)
            cur_path = f[0]
        frame = base.copy()
        cap_im = caption(name, clk)
        frame.paste(cap_im, (off[0] + 16, off[1] + 6), cap_im)
        ff.stdin.write(frame.tobytes())
    ff.stdin.close()
    if ff.wait():
        raise SystemExit("ffmpeg failed for " + cid)
    dur = nout / FPS
    with open(os.path.join(clips.OUT, cid + ".timing.json"), "w", encoding="utf-8") as fo:
        json.dump({"fps": FPS, "duration": round(dur, 3), "map": mapping, "content": [off[0], off[1], size[0], size[1]]}, fo, ensure_ascii=False, indent=1)
    print("%s: %d source frames in %d part(s), %.1f s, %s .. %s -> %s" % (
        cid, sum(len(p) for p in parts), len(parts), dur, clock(start, parts[0][0][2]), clock(start, parts[-1][-1][2]), mp4))


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cap = [c for c in argv[0].split(";") if c]
    for cid in argv[1:] or list(clips.CLIPS):
        build(cap, cid)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
