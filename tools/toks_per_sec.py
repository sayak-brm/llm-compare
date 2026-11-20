"""Observed output tokens/second per Claude model from our own Claude Code session logs (read-only, local).
For each assistant message id: output_tokens (max seen) / (time of its last block - time of the entry it answers).
That wall time includes time to first token, any thinking, and tool-result handling latency, so this is an
END-TO-END throughput for real turns, not the raw decode speed. Only turns with >= MIN_OUT output tokens and
1..600 s are kept. Usage: python -I toks_per_sec_2026_11_20.py [days]
"""
import json, sys, glob, os, statistics, collections, datetime

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 10
MIN_OUT = 150
root = os.path.expanduser(r'~\.claude\projects')
cut = datetime.datetime.now().timestamp() - DAYS * 86400

def ts(s):
    return datetime.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()

rates = collections.defaultdict(list)
files = [p for p in glob.glob(os.path.join(root, '**', '*.jsonl'), recursive=True) if os.path.getmtime(p) > cut]
for p in files:
    stamp = {}      # uuid -> timestamp
    msgs = {}       # message id -> [parent uuid, last ts, max out tokens, model]
    try:
        with open(p, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                u, t = d.get('uuid'), d.get('timestamp')
                if not (u and t):
                    continue
                try:
                    tt = ts(t)
                except ValueError:
                    continue
                stamp[u] = tt
                m = d.get('message') or {}
                if d.get('type') == 'assistant' and isinstance(m, dict) and m.get('id'):
                    use = m.get('usage') or {}
                    out = use.get('output_tokens') or 0
                    cur = msgs.get(m['id'])
                    if cur is None:
                        msgs[m['id']] = [d.get('parentUuid'), tt, out, m.get('model')]
                    else:
                        cur[1] = max(cur[1], tt); cur[2] = max(cur[2], out)
    except OSError:
        continue
    for mid, (parent, last, out, model) in msgs.items():
        if not model or model.startswith('<') or parent not in stamp or out < MIN_OUT:
            continue
        dt = last - stamp[parent]
        if 1 <= dt <= 600:
            rates[model].append(out / dt)

print(f'{len(files)} session files from the last {DAYS} days; turns kept per model (>= {MIN_OUT} output tokens, 1-600 s):')
for model, xs in sorted(rates.items(), key=lambda kv: -len(kv[1])):
    if len(xs) < 20:
        continue
    xs.sort(); q = lambda f: xs[int(f * (len(xs) - 1))]
    print(f'  {model:28s} n={len(xs):5d}  median {statistics.median(xs):6.1f} tok/s   p25 {q(.25):6.1f}   p75 {q(.75):6.1f}   p90 {q(.9):6.1f}')
