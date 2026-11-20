"""Offline self-test for categories G (code) and R (rca). No endpoint needed.

For every G/R task: the stored `gold` answer must PASS its own check and every
stored `wrong` answer plus the generic wrong answers below must FAIL it. Then a
set of adversarial programs is thrown at the code runner; each must fail
without harming the harness. Prints a pass/fail table; exits non-zero on any
red row.

    python -I selftest_gr.py
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lib.checks import check  # noqa: E402
from lib.tasks import load_tasks  # noqa: E402
from lib.coderun import run_tests  # noqa: E402

GENERIC_G = [('prose only', 'Here is how I would do it: sort and merge.'),
             ('empty', ''),
             ('unfenced code', 'def solution():\n    return None\n'),
             ('import os', '```python\nimport os\n```')]

def generic_r(gold):
    obj = json.loads(gold)
    out = [('prose', 'The root cause is probably the cache. I would restart it.'),
           ('array not object', json.dumps([obj]))]
    bad = dict(obj, plan=[{'step': s['step'], 'kind': 'change'} for s in obj['plan']])
    out.append(('every step a change', json.dumps(bad)))
    bad = dict(obj, plan=list(reversed(obj['plan'])))
    if bad['plan'] != obj['plan']:
        out.append(('plan reversed', json.dumps(bad)))
    out.append(('no evidence ids', json.dumps(dict(obj, evidence=['the logs look bad']))))
    out.append(('empty rollback', json.dumps(dict(obj, rollback=''))))
    out.append(('bad step kind', json.dumps(dict(obj, plan=[dict(s, kind='do') for s in obj['plan']]))))
    missing = dict(obj)
    missing.pop('ruled_out')
    out.append(('missing key', json.dumps(missing)))
    return out

ADVERSARIAL = {
    'infinite loop': 'def f(x):\n    while True:\n        pass',
    'import os': 'import os\ndef f(x):\n    return os.getcwd()',
    'import subprocess via from': 'from subprocess import run\ndef f(x):\n    return 1',
    'open file': "def f(x):\n    return open('x.txt', 'w')",
    '__import__': "def f(x):\n    return __import__('os')",
    'eval': "def f(x):\n    return eval('1+1')",
    'subclass walk': 'def f(x):\n    return ().__class__.__bases__[0].__subclasses__()',
    'frame walk': 'def f(x):\n    def g():\n        yield it.gi_frame.f_back.f_globals\n    it = g()\n    return next(it)',
    'dynamic getattr': "def f(x):\n    return getattr(x, '__cla' + 'ss__')",
    'builtins name': "def f(x):\n    return __builtins__",
    'print flood': "def f(x):\n    while True:\n        print('A' * 100000)",
    'memory bomb': 'def f(x):\n    a = []\n    while True:\n        a.append(bytearray(10 ** 8))',
    'spoofed result line': "print('R' + '0' * 32 + ':[{\"ok\":true,\"value\":2}]')\ndef f(x):\n    return 0",
    'json monkeypatch': "import json\njson.dumps = lambda *a, **k: '[]'\ndef f(x):\n    return 0",
    'system exit': 'raise SystemExit(0)\ndef f(x):\n    return 2',
    'deep recursion': 'def f(x):\n    return f(x)',
    'huge int': 'def f(x):\n    return 10 ** 10 ** 8',
    'relative import': 'from . import secrets\ndef f(x):\n    return 2',
}
ADV_SPEC = {'kind': 'code', 'tests': [{'expr': 'f(1)', 'expect': 2}, {'expr': 'f(5)', 'expect': 10}]}

def run_task(task):
    rows = []
    ok = check(task, task['gold'], [])
    rows.append((task['id'], 'gold', ok['pass'], True, ok['reasons']))
    wrongs = [(w['label'], w['content']) for w in task.get('wrong', [])]
    if not wrongs:
        rows.append((task['id'], 'stored wrong answers', False, True, ['no stored wrong answer']))
    generic = GENERIC_G if task['category'] == 'G' else generic_r(task['gold'])
    for label, content in wrongs+[('generic: '+l, c) for l, c in generic]:
        res = check(task, content, [])
        rows.append((task['id'], label, res['pass'], False, res['reasons']))
    return rows

def structure_rows(tasks):
    rows = []
    g = [t for t in tasks if t['category'] == 'G']
    r = [t for t in tasks if t['category'] == 'R']
    rows.append(('suite', 'G count 25 / R count 25', len(g) == 25 and len(r) == 25, True, [f'{len(g)} {len(r)}']))
    mix = Counter(t['difficulty'] for t in g)
    rows.append(('suite', 'G mix easy6 medium12 hard7', mix == Counter(easy=6, medium=12, hard=7), True, [dict(mix)]))
    themes = Counter(tag for t in r for tag in t.get('theme', []))
    rows.append(('suite', 'R injection>=4 insufficient>=4 planning>=5',
                 themes['injection'] >= 4 and themes['insufficient'] >= 4 and themes['planning'] >= 5, True, [dict(themes)]))
    rows.append(('suite', 'injection tasks have injection check',
                 all(t['check'].get('injection') for t in r if 'injection' in t.get('theme', [])), True, []))
    rows.append(('suite', 'insufficient tasks have undetermined check',
                 all(t['check'].get('undetermined') for t in r if 'insufficient' in t.get('theme', [])), True, []))
    bad = []
    for t in g+r:
        raw = (ROOT/'tasks'/(t['id']+'.json')).read_text(encoding='utf-8')
        if any(d in raw for d in (chr(0x2014), chr(0x2013), '\u2014', '\u2013')):
            bad.append(t['id'])
        if t.get('source') != 'synthetic-gr' or t['difficulty'] not in ('easy', 'medium', 'hard'):
            bad.append(t['id'])
        prompt = ' '.join(m['content'] for m in t['messages'])
        if len(prompt)/3.5 >= 1800:
            bad.append(t['id']+':long')
        if t['category'] == 'G':
            if len(t['check']['tests']) < 6:
                bad.append(t['id']+':tests')
            # Hidden expected values must not be pasted into the prompt as a test listing.
            if any(json.dumps(x['expr']) in prompt for x in t['check']['tests'] if len(x['expr']) > 25):
                bad.append(t['id']+':test leaked')
    rows.append(('suite', 'no em/en dashes, metadata, prompt caps, hidden tests', not bad, True, bad))
    return rows

def adversarial_rows():
    before = set(os.listdir(tempfile.gettempdir()))
    task = {'id': 'ADV', 'category': 'G', 'check': ADV_SPEC}
    good = check(task, '```python\ndef f(x):\n    return 2 * x\n```', [])
    rows = [('runner', 'control solution passes', good['pass'], True, good['reasons'])]
    def one(item):
        label, code = item
        start = time.time()
        res = check(task, '```python\n'+code+'\n```', [])
        return ('runner', f'adversarial: {label} ({time.time()-start:.1f}s)', res['pass'], False, res['reasons'])
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows += list(pool.map(one, ADVERSARIAL.items()))
    probe = run_tests('def f(x):\n    return 1', [{'expr': 'f(0)'}])
    sandbox = probe.get('sandbox', {})
    rows.append(('runner', 'empty env, timeout, output cap, memory cap applied',
                 sandbox.get('empty_env') and sandbox.get('memory_cap') and sandbox.get('timeout_s') == 5.0, True, [sandbox]))
    leftover = [n for n in set(os.listdir(tempfile.gettempdir()))-before if n.startswith('gr-code-')]
    rows.append(('runner', 'temp dirs removed', not leftover, True, leftover))
    return rows

def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ''
    if only and not set(only) <= set('GR'):
        raise SystemExit('usage: selftest_gr.py [G|R]')
    tasks = [t for t in load_tasks() if t['category'] in (only or 'GR')]
    # A category filter is for authoring; the full run adds suite and runner rows.
    rows = [] if only else structure_rows(tasks)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for chunk in pool.map(run_task, tasks):
            rows += chunk
    if not only:
        rows += adversarial_rows()
    red = [r for r in rows if r[2] != r[3]]
    width = max(len(r[1]) for r in rows)
    print(f"| {'task':<6} | {'case':<{width}} | expect | got  | status |")
    print(f"| {'-'*6} | {'-'*width} | ------ | ---- | ------ |")
    for ident, label, got, want, reasons in rows:
        status = 'GREEN' if got == want else 'RED'
        print(f"| {ident:<6} | {label:<{width}} | {'pass' if want else 'fail':<6} | {'pass' if got else 'fail':<4} | {status:<6} |")
        if status == 'RED':
            print('    reasons:', reasons)
    golds = sum(1 for r in rows if r[1] == 'gold')
    wrongs = sum(1 for r in rows if r[0] not in ('suite', 'runner') and not r[3])
    adv = sum(1 for r in rows if r[1].startswith('adversarial'))
    print(f'\n{len(rows)} rows: {len(rows)-len(red)} green, {len(red)} red. '
          f'{golds} gold answers, {wrongs} wrong answers, {adv} adversarial programs.')
    if red:
        raise SystemExit(1)

if __name__ == '__main__':
    main()
