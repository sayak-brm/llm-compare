"""Cost-per-intelligence Pareto frontier from the slopalytics.com / Artificial Analysis dataset (read-only, local).
Usage: python -I pareto_2026_11_20.py <models.json> [policy|all]
Frontier = points not dominated on (dollars per task: lower is better, intelligence index: higher is better).
Also prints, along the frontier, the marginal dollars per extra intelligence point (the price of the next step up),
and the intelligence per dollar for each point. 'policy' keeps only the points our model rules allow plus Codex and Opus-high fallback."""
import json, re, sys

data = json.load(open(sys.argv[1], encoding='utf-8'))
scope = sys.argv[2] if len(sys.argv) > 2 else 'policy'

def allowed(r):
    i, e = r['id'], r['effort']
    if r['lab'] == 'anthropic':
        if re.fullmatch(r'claude-haiku-5-5(-.*)?', i): return True                     # Haiku 5.5, every level
        if re.fullmatch(r'claude-sonnet-5-5(-.*)?', i): return e == 'high'             # Sonnet 5.5 high only
        if re.fullmatch(r'claude-opus-5-5(-.*)?', i): return e in ('medium', 'high', 'xhigh')   # high = Fable fallback
        if re.fullmatch(r'claude-fable-5-2(-.*)?', i): return e in ('low', 'medium', 'high')
        return False
    if r['lab'] == 'openai':
        return r['name'].startswith(('GPT-6.1 Sol', 'GPT-6 Luna'))                      # our Codex profiles use only gpt-6.1-sol and gpt-6-luna
    return False

pts = [r for r in data if r.get('intelligence') is not None and (r.get('cost') or 0) > 0
       and (scope == 'policy' or not r.get('deprecated'))]   # policy scope keeps deprecated points we actually run (gpt-6.1-sol)
if scope == 'policy':
    pts = [r for r in pts if allowed(r)]
pts.sort(key=lambda r: (r['cost'], -r['intelligence']))
front, best = [], -1
for r in pts:
    if r['intelligence'] > best:
        front.append(r); best = r['intelligence']

def label(r):
    return f"{r['name']} ({r['effortLabel']})"

print(f'scope={scope}: {len(pts)} candidate points, {len(front)} on the frontier (lowest cost for each intelligence level)')
print(f"{'point':44s} {'intel':>6} {'$/task':>8} {'int per $':>9} {'marginal $ per +1 int':>22} {'sec/task':>9}")
prev = None
for r in front:
    marg = '' if prev is None else f"{(r['cost'] - prev['cost']) / (r['intelligence'] - prev['intelligence']):.3f}"
    t = r.get('time')
    print(f"{label(r)[:44]:44s} {r['intelligence']:6.1f} {r['cost']:8.2f} {(r['intelligence'] / r['cost']) if r['cost'] else float('inf'):9.1f} {marg:>22} {('%.0f' % t) if t else '-':>9}")
    prev = r
if scope == 'policy':
    off = [r for r in pts if r not in front]
    print('\nallowed points OFF the frontier (dominated by a cheaper-or-equal, smarter-or-equal point):')
    for r in sorted(off, key=lambda r: r['cost']):
        dom = next(f for f in front if f['cost'] <= r['cost'] and f['intelligence'] >= r['intelligence'])
        print(f"  {label(r)[:40]:40s} int {r['intelligence']:5.1f} ${r['cost']:6.2f}  dominated by {label(dom)}")
