"""Rebuild each commander turn from an ACP transcript (agents/side*/transcript.jsonl): ordered thought / message text, tool calls between.

    python -I -X utf8 transcript_turns.py <transcript.jsonl> <out.json>
"""
import json, sys, pathlib
src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
turns, cur = [], None
for l in src.open(encoding='utf-8'):
    d = json.loads(l)
    if d['type'] == 'wake':
        cur = {'turn': d['turn_id'], 'summary_head': d.get('summary', '')[:300], 'segs': []}
        turns.append(cur); continue
    if d['type'] != 'acp' or cur is None: continue
    m = d['msg']
    if m.get('method') != 'session/update': continue
    u = m['params']['update']; k = u.get('sessionUpdate')
    if k in ('agent_message_chunk', 'agent_thought_chunk'):
        c = u.get('content', {}); t = c.get('text', '') if isinstance(c, dict) else ''
        kind = 'say' if k == 'agent_message_chunk' else 'think'
        if cur['segs'] and cur['segs'][-1]['k'] == kind: cur['segs'][-1]['t'] += t
        else: cur['segs'].append({'k': kind, 't': t, 'ts': d['ts']})
    elif k == 'tool_call':
        cur['segs'].append({'k': 'tool', 't': u.get('title', ''), 'ts': d['ts']})
out.write_text(json.dumps(turns, ensure_ascii=False, indent=1), encoding='utf-8')
for t in turns:
    print(t['turn'], len(t['segs']), sum(len(s['t']) for s in t['segs'] if s['k'] != 'tool'))
