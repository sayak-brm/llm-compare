"""Offline rescore of category C from saved review files (no model run, no network).
Usage: python -I c_rescore_2026_11_20.py <results-run-dir> [--show-text-only]
Reads <run>/review/C*-r*.txt (answer text, then CHECKS: [...] and MOCK CALLS: [...]) and
D:\\Tools and Plugins\\llm-compare\\harness\\tasks\\C*.json. Prints three scores:
  strict       = as run (no check error)
  actions-only = no tool-call error (right tools, order, arguments, no extra/forbidden calls, no invented ids)
  wording-only = failures whose only errors are wording ones (required text / answer-first / style / lines)
"""
import json, re, sys, pathlib, collections

TASKS = pathlib.Path(r'D:\Tools and Plugins\llm-compare\harness\tasks')
ACTION_PAT = re.compile(r'oneOf|tool calls|wrong|invented|rule-forbidden|invalid JSON|unsafe|outside approved|manage args|forbidden final text$', re.I)
WORDING_PAT = re.compile(r'missing final fact|answer must come first|report style|too many lines|missing required text|forbidden final text structure|incorrect final answer', re.I)

def parse(path):
    text = path.read_text(encoding='utf-8')
    m = re.search(r'^CHECKS: (\[.*\])\s*$', text, re.M)
    checks = json.loads(m.group(1)) if m else []
    answer = text[:m.start()].strip() if m else text.strip()
    return answer, checks

def main():
    run = pathlib.Path(sys.argv[1])
    show = '--show-text-only' in sys.argv
    trials = collections.OrderedDict()
    for p in sorted((run / 'review').glob('C*-r*.txt')):
        tid = p.name.split('-')[0]
        trials.setdefault(tid, []).append(parse(p))
    n = strict = actions = wording_only = 0
    per_task = {}
    for tid, rs in trials.items():
        for answer, checks in rs:
            n += 1
            act = [c for c in checks if ACTION_PAT.search(c) and not WORDING_PAT.search(c)]
            wor = [c for c in checks if WORDING_PAT.search(c)]
            if not checks:
                strict += 1
            if not act:
                actions += 1
            if checks and not act and wor:
                wording_only += 1
                if show:
                    spec = json.loads((TASKS / f'{tid}.json').read_text(encoding='utf-8'))
                    print(f'--- {tid}: {"; ".join(wor)}')
                    print('required_text:', spec['check'].get('required_text'), '| answer_first:', spec['check'].get('answer_first'))
                    print('answer:', answer[:420].replace('\n', ' / '))
        bad = sum(1 for a, c in rs if [x for x in c if ACTION_PAT.search(x) and not WORDING_PAT.search(x)])
        per_task[tid] = 3 - bad
    n_tasks = len(list(TASKS.glob('C*.json')))
    total = 3 * n_tasks
    passed = total - n  # review files exist only for failing trials
    strict = passed
    actions += passed
    n = total
    print(f'\ntrials={n} tasks={n_tasks}')
    print(f'strict (as run):        {strict}/{n} = {100*strict/n:.1f}%')
    print(f'actions-only:           {actions}/{n} = {100*actions/n:.1f}%')
    print(f'wording-only failures:  {wording_only} trials (right actions, text check failed)')
    full = sum(1 for p in TASKS.glob('C*.json') if per_task.get(p.stem, 3) == 3)
    print('tasks with all 3 trials action-correct:', full, 'of', n_tasks)

main()
