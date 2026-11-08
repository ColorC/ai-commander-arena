# build_console_track.py -- builds the sanitised "commander console" text track for each cut stretch.
# Reads the run dirs READ-ONLY (wakes/queue logs + agent transcripts), writes
#   $ARENA_DATA/video/program/raw-footage/console/<id>.json
# Sanitiser = allow-list (only game command fields, short game-reasoning sentences) + final regex scan
# (scan_text) that makes the build fail if anything path/URL/environment-like remains.
# Usage: python build_console_track.py [id ...]     (no id = all stretches)
import json, re, sys, os, collections
import pathlib

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[3] / "data")

RUNS = ARENA_DATA.as_posix() + '/runs/'
OUT = ARENA_DATA.as_posix() + '/video/program/raw-footage/console'
H = 3600
# id -> (game, run dir, scenario start sec-of-day, (from, to) in sec-of-day): the single-span clips of clips.py's
# configuration ($ARENA_DATA/video/program/raw-footage/clips.json)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clips as _clips  # noqa: E402
_sec = lambda hm: int(hm[:2]) * H + int(hm[3:5]) * 60
CLIPS = {cid: (g, os.path.relpath(_clips.GAMES[g][0], RUNS).replace(os.sep, '/'), _sec(_clips.GAMES[g][1]),
               (_sec(sp[0][0]), _sec(sp[0][1])))
         for cid, (g, sp) in _clips.CLIPS.items() if len(sp) == 1 and not _clips.is_full(cid)}
SIDE_ZH = {0: '蓝', 1: '红'}

# ---------------------------------------------------------------- sanitiser
# sentences of free text that mention anything environment-like are dropped entirely
BAD_SENT = re.compile(
    r'arena|mcp|tool|harness|prompt|transcript|session|token|secret|\bnotes?\b|script|\bcmd|checklist|'
    r'brief show|\blint|offline|sandbox|\bapi\b|bash|python|\blua\b|respond|turn end|end turn|end the turn|'
    r'\bs[01]-c\d+|\bp\d-\d\b|training|训练|\bwait\.|观察机|笔记|脚本|工具|提示词|系统|文件|目录|路径|\bfile|\bpath|'
    r'\.(?:py|md|json|jsonl|exe|txt|lua|log|js|csv)\b|[A-Za-z]:[\\/]|https?:|www\.|\\', re.I)

# final regex scan: the build fails when any of these still match in the saved track
SCAN = [
    (r'\b[A-Za-z]:[\\/]', 'drive letter'),
    (r'\\', 'backslash'),
    (r'(?:^|[\s"\'(])/[A-Za-z_~.][\w.~-]*/', 'unix-style path'),
    (r'~/|\.\./|\./', 'relative path'),
    (r'\.(?:py|json|jsonl|exe|md|txt|lua|log|js|ts|csv|ps1|sh|bat|mp4|wav|yml|yaml|toml|ini|dll)\b', 'file extension'),
    (r'(?i)https?:|ftp:|://|www\.|\b[a-z0-9-]+\.(?:com|cn|net|org|io|dev|ai)\b', 'url'),
    (r'(?i)localhost|127\.0\.0\.1|\b\d{1,3}(?:\.\d{1,3}){3}\b', 'host/ip'),
    (r'(?i)ai-commander|arena|harness|mcp|transcript|secret|token|session|prompt|opencode|kimi-code|deepseek-v|k3\.1|jsonl', 'environment term'),
    (r'(?i)\b(?:tool_?call|cmd_id|stderr|stdout|apikey|api_key|bearer)\b', 'harness term'),
    (r'\b[0-9a-f]{16,}\b', 'long hex id'),
    (r'(?i)\b(?:ses|session)_[A-Za-z0-9]{6,}', 'session id'),
    (r'\bs[01]-[ct]\d{3,}\b', 'internal id'),
]


def scan_text(s):
    bad = []
    for rx, why in SCAN:
        m = re.search(rx, s)
        if m:
            bad.append((why, m.group(0)))
    return bad


def cjk_ratio(s):
    n = len(re.findall(r'[\u4e00-\u9fff]', s))
    return n / max(1, len(re.sub(r'\s', '', s)))


def is_fused(s):
    # some streams lose spaces ("Apache2 at3519"); such text is unreadable -> use the model's own clean message
    n = len(re.findall(r'[a-z]{3,}\d|\d[a-z]{3,}|[a-z]{2,}[A-Z]{2,}', s))
    return n * 1000 / max(1, len(s)) >= 3


HEXY = re.compile(r'\b[1-9]\d{2,3}\b')
KEY = re.compile(r'(?i)hunt|move|withdraw|retreat|fire|artillery|barrage|attack|assault|hold|screen|enemy|blue|red|contact|'
                 r'apache|helo|tank|recon|vp|share|kill|lost|losses|advance|defen|keep|send|order|position|'
                 r'炮|火力|撤|攻|守|侦察|直升机|坦克|目标|份额|损失|集结|占领|增援|压制')


def sentences(txt):
    txt = txt.replace('**', '').replace('（', '').replace('）', '')
    # repair spaces lost at stream-chunk joins ("MRRis", "...1424).At")
    txt = re.sub(r'(?<=[A-Z]{2})(?=[a-z]{3,})', ' ', txt)
    txt = re.sub(r'(?<=[a-z0-9)])\.(?=[A-Z][a-z])', '. ', txt)
    parts = re.split(r'(?<=[.!?。！？])\s+|\n+|(?<=[。！？])', txt)
    return [p.strip(' -*\t') for p in parts if p and p.strip(' -*\t')]


def trim(s, n):
    s = re.sub(r'\s+', ' ', s).strip()
    if len(s) <= n:
        return s
    cut = s[:n]
    sp = max(cut.rfind(' '), cut.rfind('，'), cut.rfind(','), cut.rfind('；'))
    if sp > n * 0.6:
        cut = cut[:sp]
    return cut.rstrip(' ,，;；') + '…'


def summarise(thought, message):
    """Short game-reasoning summary in the language of the source text (never translated)."""
    thought = re.sub(r'(?i)\b(?:end|wait)\.?\s*respond\.?|respond\.', ' ', thought)
    src = thought if (len(thought.strip()) >= 80 and not is_fused(thought)) else message
    if len(src.strip()) < 6:
        src = message or thought
    if src is thought and is_fused(src):
        return '', 'zh'
    cn = cjk_ratio(src) > 0.2
    ss = [s for s in sentences(src) if not BAD_SENT.search(s) and len(s) > 3]
    if not ss:
        return '', 'zh' if cn else 'en'
    limit = 150 if cn else 240
    if src is message or len(ss) <= 3:
        picked, tot = [], 0
        for s in ss:
            if tot + len(s) > limit and picked:
                break
            picked.append(s)
            tot += len(s)
    else:
        sc = []
        for i, s in enumerate(ss):
            v = (2 if HEXY.search(s) else 0) + min(3, len(KEY.findall(s))) + (1 if i == 0 else 0) + (1 if i >= len(ss) - 3 else 0)
            if len(s) < 25:
                v -= 2
            sc.append((v, i))
        idx, tot = [], 0
        for v, i in sorted(sc, key=lambda x: (-x[0], x[1])):
            t = min(len(ss[i]), 120)
            if tot + t > limit and idx:
                continue
            idx.append(i)
            tot += t
            if len(idx) == 3:
                break
        idx.sort()
        picked = [ss[i] for i in idx]
    out = ' '.join(trim(s, 70 if cn else 120) for s in picked)
    return trim(out, limit + 20), 'zh' if cn else 'en'


# ---------------------------------------------------------------- command allow-list
ORD_OK = {'Move Hasty', 'Move Deliberate', 'Screen', 'Hold', 'Hunt', 'Assault', 'On Call', 'Barrage'}
SOP_OK = {'acceptable_losses', 'fire_discipline', 'stand_off_range', 'relocate_when'}
SAFE_VAL = re.compile(r'^[\w .:/-]{1,40}$')
UNIT_OK = re.compile(r'^[\w .:/+-]{1,40}$')


def fmt_order(o):
    n = o.get('orders')
    if n not in ORD_OK:
        return None
    if n == 'Barrage':
        tgt = o.get('target_unit') or (('hex ' + str(o['target_location'])) if o.get('target_location') else None)
        if tgt is None or not SAFE_VAL.match(str(tgt)):
            return None
        return 'Barrage %s %s x%s/%smin' % (tgt, o.get('barrage_type', ''), o.get('barrage_rounds', '?'), o.get('barrage_duration_mins', '?'))
    d = o.get('destination_location')
    return n + (' %s' % d if d else '')


def fmt_command(c):
    u = c.get('unit')
    if u is not None and not UNIT_OK.match(u):
        return None
    k, a = c['kind'], c['args']
    if k == 'orders':
        parts = [fmt_order(o) for o in a.get('orders', []) if isinstance(o, dict)]
        parts = [p for p in parts if p]
        return '%s: %s' % (u, ' > '.join(parts)) if parts else None
    if k == 'doctrine':
        parts = ['%s=%s' % (kk.replace('_', ' '), a['sop'][kk]) for kk in a.get('sop', {}) if kk in SOP_OK and SAFE_VAL.match(str(a['sop'][kk]))]
        return '%s: SOP %s' % (u, ', '.join(parts)) if parts else None
    if k == 'deploy':
        return '%s: Deploy %s' % (u, a.get('location'))
    if k == 'fire_support':
        r = a.get('request', {})
        return 'Fire request %s hex %s (%s)' % (r.get('request_type', ''), r.get('location', ''), r.get('target_type', ''))
    return None


def abbrev_code(code, keep=re.compile(r'\b(order|fire|move|hunt|hold|screen|barrage|assault|withdraw|deploy|unit)\b', re.I)):
    """Script text an agent wrote: keep only game-call lines, elide the rest with an ellipsis.
    (No in-span wake contains script code today; kept so a future run is handled the same way.)"""
    out, gap = [], False
    for ln in code.splitlines():
        ln = ln.strip()
        if ln and keep.search(ln) and not BAD_SENT.search(ln) and len(ln) < 90:
            out.append(ln)
            gap = False
        elif not gap:
            out.append('…')
            gap = True
    return '\n'.join(out[:6])


# ---------------------------------------------------------------- data
def clock(sec):
    sec %= 86400
    return '%02d:%02d:%02d' % (sec // 3600, sec // 60 % 60, sec % 60)


def model_name(m):
    return 'Kimi' if 'kimi' in m.lower() else 'DeepSeek' if 'deepseek' in m.lower() else None


def load_game(run):
    base = RUNS + run
    wakes = {0: {}, 1: {}}
    for l in open(base + '/logs/wakes.jsonl', encoding='utf8'):
        x = json.loads(l)
        if x.get('kind') == 'wake':
            wakes[x['side']][x['turn_id']] = x['sec']
    queue = [json.loads(l) for l in open(base + '/logs/queue.jsonl', encoding='utf8')]
    unit_of = {q['cmd_id']: q.get('unit') for q in queue if q.get('event') == 'submitted'}
    sides = {}
    for s in (0, 1):
        model, cur, rec = None, None, {}
        for l in open(base + '/agents/side%d/transcript.jsonl' % s, encoding='utf8'):
            d = json.loads(l)
            t = d['type']
            if t == 'start':
                model = model_name(d['model'])
            elif t == 'wake':
                cur = d['turn_id']
                rec[cur] = {'th': '', 'ms': '', 'cancel': []}
            elif t == 'acp' and cur:
                u = d['msg'].get('params', {}).get('update', {})
                k = u.get('sessionUpdate')
                if k == 'agent_thought_chunk':
                    rec[cur]['th'] += u['content']['text']
                elif k == 'agent_message_chunk':
                    rec[cur]['ms'] += u['content']['text']
                elif k == 'tool_call_update':
                    ri = u.get('rawInput')
                    if ri is None and u.get('status') == 'in_progress':
                        try:
                            ri = json.loads(u['content'][0]['content']['text'])
                        except Exception:
                            ri = None
                    if isinstance(ri, dict) and str(ri.get('cmd', '')).startswith('order withdraw '):
                        cid = ri['cmd'].split()[-1]
                        if cid not in rec[cur]['cancel']:
                            rec[cur]['cancel'].append(cid)
        sides[s] = (model, rec)
    return wakes, queue, unit_of, sides


def build(cid):
    game, run, start, (a, b) = CLIPS[cid]
    wakes, queue, unit_of, sides = load_game(run)
    entries = []
    for s in (0, 1):
        model, rec = sides[s]
        ws = sorted((sec, tid) for tid, sec in wakes[s].items())
        cmds = collections.defaultdict(list)
        for q in queue:
            if q.get('event') != 'submitted' or q['side'] != s:
                continue
            owner = None
            for sec, tid in ws:
                if sec <= q['sec_submitted']:
                    owner = tid
            if owner:
                f = fmt_command(q)
                if f:
                    cmds[owner].append(f)
        for sec, tid in ws:
            g = start + sec
            if not (a <= g <= b) or tid not in rec:
                continue
            r = rec[tid]
            summ, lang = summarise(r['th'], r['ms'])
            cl = list(cmds[tid])
            for c in r['cancel']:
                if unit_of.get(c) and UNIT_OK.match(unit_of[c]):
                    cl.append('%s: cancel queued order' % unit_of[c])
            if len(cl) > 7:
                cl = cl[:6] + ['… (+%d more orders)' % (len(cl) - 6)]
            if not summ and not cl:
                continue          # nothing game-relevant to show for this wake
            entries.append({'t': g, 'clock': clock(g), 'side': SIDE_ZH[s], 'side_id': s, 'model': model,
                            'lang': lang, 'summary': summ, 'commands': cl})
    entries.sort(key=lambda e: (e['t'], e['side_id']))
    return {'id': cid, 'game': game, 'from': clock(a), 'to': clock(b),
            'blue': sides[0][0], 'red': sides[1][0], 'entries': entries}


def check(track):
    bad = []
    for e in track['entries']:
        for fld in [e['summary'], e['model'], e['clock']] + e['commands']:
            for why, m in scan_text(fld):
                bad.append((track['id'], e['clock'], why, m, fld[:80]))
    return bad


def main():
    ids = sys.argv[1:] or list(CLIPS)
    os.makedirs(OUT, exist_ok=True)
    allbad = []
    for cid in ids:
        tr = build(cid)
        bad = check(tr)
        allbad += bad
        if not bad:
            with open('%s/%s.json' % (OUT, cid), 'w', encoding='utf8') as f:
                json.dump(tr, f, ensure_ascii=False, indent=1)
        print(cid, len(tr['entries']), 'entries', 'FAIL' if bad else 'ok')
    if allbad:
        for b in allbad:
            print('SCAN-FAIL', b)
        sys.exit(1)
    print('regex scan: clean')


if __name__ == '__main__':
    main()
