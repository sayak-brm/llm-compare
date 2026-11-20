"""Run the synthetic comparison suite against an operator-managed loopback server."""
import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))  # works under python -I
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import random
import re
import statistics
import subprocess
import threading
import time

from lib.checks import check
from lib.client import Client
from lib.context import prepare_context, measure_prompt, exceeds_context, calibrate_tasks
from lib.tasks import ROOT, load_tasks, expected_args

class GPU:
    def __init__(self):
        self.stop = threading.Event()
        self.samples = []
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            try:
                p = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,utilization.gpu', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=2,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                rows = [[float(v.strip()) for v in line.split(',')] for line in p.stdout.splitlines() if line.strip()]
                if p.returncode or not rows:
                    return
                # A single 3090 is intended; multi-GPU machines aggregate VRAM and mean utilization.
                self.samples.append((sum(r[0] for r in rows), statistics.mean(r[1] for r in rows)))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                return
            self.stop.wait(1)

    def finish(self):
        self.stop.set()
        self.thread.join(timeout=3)
        return {'peak_vram_mb': max((x[0] for x in self.samples), default=None),
                'mean_gpu_utilization': statistics.mean(x[1] for x in self.samples) if self.samples else None,
                'gpu_samples': len(self.samples)}

def metadata(client):
    props, slots = client.get('/props'), client.get('/slots')
    props = props if isinstance(props, dict) else {}
    defaults = props.get('default_generation_settings') or {}
    raw_model = props.get('model_path') or props.get('model_alias') or props.get('model')
    basename = re.split(r'[/\\]', raw_model)[-1] if isinstance(raw_model, str) else None
    top = props.get('n_ctx')
    per_slot_reported = defaults.get('n_ctx')
    count = props.get('total_slots') or (len(slots) if isinstance(slots, list) else None)
    count = count if type(count) is int and count > 0 else None
    top = top if type(top) is int and top > 0 else None
    per_slot_reported = per_slot_reported if type(per_slot_reported) is int and per_slot_reported > 0 else None
    # llama-server reports default_generation_settings.n_ctx as the PER-SLOT context (-c divided by -np);
    # a top-level n_ctx, when present, is treated as the total.
    if per_slot_reported:
        per_slot, n_ctx = per_slot_reported, per_slot_reported*(count or 1)
    elif top:
        n_ctx, per_slot = top, top//count if count else top
    else:
        n_ctx = per_slot = None
    return {'n_ctx': n_ctx, 'slots': count, 'per_slot_context': per_slot, 'model_basename': basename}

def skip_trial(task, repeat, reason, traces=None):
    return {'task_id': task['id'], 'category': task['category'], 'repeat': repeat, 'status': 'skipped_context',
            'skipped_context': True, 'pass': None, 'score': None, 'reasons': [reason], 'valid_calls': 0, 'total_calls': 0,
            'ctx_tokens_estimate': task['ctx_tokens_estimate'],
            **{k: task.get(k) for k in ('prompt_tokens_measured', 'prompt_tokens_source', 'prompt_framing_allowance', 'per_slot_context', 'n_ctx')},
            'content': '', 'tool_calls': [], 'reasoning_leak': False,
            'error': next((t['error'] for t in traces or [] if t.get('error')), None), 'http_requests': traces or [],
            'prompt_tokens': None, 'completion_tokens': None, 'ttft': None, 'total_time': sum(t['total_time'] for t in traces or []),
            'generated_tokens_s': None, 'prompt_tokens_s': None, 'finish_reason': None}

def trial(task, repeat, args):
    if task.get('prompt_tokens_measured') is not None and exceeds_context(task, task['max_tokens'], task.get('per_slot_context')):
        return skip_trial(task, repeat, 'Measured prompt plus framing/completion reservation exceeds per-slot context')
    client = Client(args.base_url)
    messages = [dict(m) for m in task['messages']]
    calls, traces = [], []
    content = ''
    steps = task['check'].get('steps', [])
    fake_repo = dict(task.get('snapshot', {}).get('fake_repo', {}))
    # A strict finite turn budget detects loops/extra calls, even for refusal cases.
    for turn in range(len(steps)+2):
        payload = {'model': args.model, 'messages': messages, 'max_tokens': task['max_tokens'], 'temperature': task.get('temperature', .2),
                   'stream': True, 'stream_options': {'include_usage': True}, 'chat_template_kwargs': {'enable_thinking': args.thinking == 'on'}}
        if task.get('tools'):
            payload['tools'] = task['tools']
        response = client.chat(payload, task['timeout'])
        response.update(task_id=task['id'], repeat=repeat, turn=turn, ctx_tokens_estimate=task['ctx_tokens_estimate'])
        traces.append(response)
        if response.get('skipped_context'):
            return skip_trial(task, repeat, 'Server HTTP 400 reports context exceeded', traces)
        content += response['content']
        if response['error'] or not response['tool_calls']:
            break
        # Some chat templates (e.g. Mistral) require tool-call ids of exactly 9 alphanumeric characters,
        # so replayed ids are normalised; recorded calls keep the model/server ids.
        wire = [dict(c, id=hashlib.md5(f"{task['id']}-{repeat}-{turn}-{i}".encode()).hexdigest()[:9]) for i, c in enumerate(response['tool_calls'])]
        messages.append({'role': 'assistant', 'content': response['content'] or None, 'tool_calls': wire})
        for wire_call, call in zip(wire, response['tool_calls']):
            index = len(calls)
            calls.append(call)
            # Results are static fixture data, never invocations, shell commands, or file operations.
            result = steps[index].get('result', {'ok': True}) if index < len(steps) else {'error': 'unexpected mock call'}
            try:
                fn = call['function']
                actual = json.loads(fn['arguments'])
                if not isinstance(actual, dict):
                    raise ValueError('arguments must be an object')
                correct = index < len(steps) and fn['name'] == steps[index]['name'] and actual == expected_args(steps[index])
                if fn['name'] == 'mycroft_manage' and index < len(steps) and 'manage' in steps[index]:
                    correct = actual.get('op') == steps[index]['args_op'] and json.loads(actual.get('args', '')) == steps[index]['manage']
                if not correct:
                    result = {'error': 'mock call did not match the scenario contract'}
                elif fn['name'] == 'append_file':
                    if actual['path'] not in fake_repo:
                        result = {'error': 'path outside mock repo'}
                    else:
                        fake_repo[actual['path']] += actual['text']
                        result = {'ok': True}
                elif fn['name'] == 'read_file' and 'result' not in steps[index]:
                    result = fake_repo.get(actual['path'], {'error': 'path outside mock repo'})
            except (KeyError, ValueError, TypeError):
                result = {'error': 'invalid mock call'}
            response.setdefault('mock_results', []).append({'tool_call_id': call.get('id', ''), 'result': result})
            messages.append({'role': 'tool', 'tool_call_id': wire_call['id'], 'content': json.dumps(result)})
        if len(calls) > len(steps):
            break
    leak = any(t['reasoning_leak'] for t in traces)
    outcome = check(task, content, calls, leak, ROOT)
    errs = [t['error'] for t in traces if t['error']]
    if errs:
        outcome.update({'pass': False, 'score': 0, 'reasons': outcome['reasons']+['request error']})
    # Sequential tasks require a separate model turn after each result.
    if task['category'] == 'C' and any(len(t['tool_calls']) > 1 for t in traces):
        outcome.update({'pass': False, 'score': 0, 'reasons': outcome['reasons']+['sequential calls must wait for mock results']})
    nums = lambda k: [t[k] for t in traces if t[k] is not None]
    result = {'task_id': task['id'], 'category': task['category'], 'repeat': repeat, **outcome,
              'status': 'passed' if outcome['pass'] else 'failed', 'skipped_context': False,
              **{k: task.get(k) for k in ('prompt_tokens_measured', 'prompt_tokens_source', 'prompt_framing_allowance', 'per_slot_context', 'n_ctx')},
              'ctx_tokens_estimate': task['ctx_tokens_estimate'], 'content': content, 'tool_calls': calls,
              'reasoning_leak': leak, 'error': '\n'.join(errs) or None, 'http_requests': traces,
              'prompt_tokens': sum(nums('prompt_tokens')) if nums('prompt_tokens') else None,
              'completion_tokens': sum(nums('completion_tokens')) if nums('completion_tokens') else None,
              'ttft': traces[0]['ttft'], 'total_time': sum(nums('total_time')),
              'generated_tokens_s': statistics.median(nums('generated_tokens_s')) if nums('generated_tokens_s') else None,
              'prompt_tokens_s': statistics.median(nums('prompt_tokens_s')) if nums('prompt_tokens_s') else None,
              'finish_reason': traces[-1]['finish_reason']}
    return result

def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label')
    parser.add_argument('--model')
    parser.add_argument('--calibrate', action='store_true', help='Resize selected generated task fixtures using /tokenize, then exit; no model completions')
    parser.add_argument('--target-tokens', type=int, help='Largest calibrated prompt target (capped at 90%% of per-slot context)')
    parser.add_argument('--base-url', default='http://127.0.0.1:8080/v1')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--parallel', type=int, default=1)
    parser.add_argument('--only', help='Comma-separated task ids')
    parser.add_argument('--category', help='Comma-separated categories S,T,F,L,V,C,G,R')
    parser.add_argument('--profile', choices=['quick', 'full'], default='full')
    parser.add_argument('--thinking', choices=['off', 'on'], default='off')
    args = parser.parse_args()
    if args.repeats < 1 or args.parallel < 1:
        parser.error('repeats and parallel must be positive')
    if not args.calibrate and (not args.label or not args.model):
        parser.error('--label and --model are required unless --calibrate is selected')
    if args.target_tokens is not None and (not args.calibrate or args.target_tokens < 1):
        parser.error('--target-tokens requires --calibrate and a positive value')
    if args.label is not None and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', args.label):
        parser.error('label must be 1-80 filename-safe characters')
    try:
        Client(args.base_url)
    except ValueError as exc:
        parser.error(str(exc))
    return args

def main():
    args = parse_args()
    all_tasks = load_tasks()
    tasks = all_tasks
    if args.category:
        wanted = set(args.category.split(','))
        if not wanted <= set('STFLVCGR'):
            raise SystemExit('Unknown category')
        tasks = [t for t in tasks if t['category'] in wanted]
    if args.only:
        wanted = set(args.only.split(','))
        if not wanted <= {t['id'] for t in all_tasks}:
            raise SystemExit('Unknown task id')
        tasks = [t for t in tasks if t['id'] in wanted]
    if args.profile == 'quick':
        chosen = []
        for cat in 'STFLVCGR':
            group = [t for t in tasks if t['category'] == cat]
            random.Random(7341+ord(cat)).shuffle(group)
            # G and R are slow per task (sandboxed tests, long answers): quick takes 3 of each.
            take = min(3, len(group)) if cat in 'GR' else math.ceil(len(group)/3)
            chosen += sorted(group[:take], key=lambda t: t['id'])
        tasks = chosen
        args.repeats = 1
    if not tasks:
        raise SystemExit('No selected tasks')
    client = Client(args.base_url)
    meta = metadata(client)
    if args.calibrate:
        try:
            changes = calibrate_tasks(client, tasks, meta, ROOT/'tasks', args.target_tokens)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print(json.dumps({'server': meta, 'calibration': changes}, indent=2))
        return
    prepare_context(tasks, client, meta)
    output = ROOT/'results'/(args.label+'-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    # Fail instead of mixing simultaneous runs with identical names.
    output.mkdir(parents=True, exist_ok=False)
    (output/'review').mkdir()
    gpu = GPU()
    gpu.thread.start()
    records = []
    print(f'Running {len(tasks)} tasks x {args.repeats} repeats, parallel={args.parallel}', flush=True)
    try:
        with (output/'requests.jsonl').open('w', encoding='utf-8') as log, ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = [pool.submit(trial, task, repeat, args) for task in tasks for repeat in range(1, args.repeats+1)]
            for future in as_completed(futures):
                record = future.result()
                records.append(record)
                log.write(json.dumps(record, ensure_ascii=False)+'\n')
                log.flush()
                if record['category'] == 'V' or record['pass'] is False:
                    sample = record['content']+'\n\nCHECKS: '+json.dumps(record['reasons'])+'\nMOCK CALLS: '+json.dumps(record['tool_calls'], ensure_ascii=False)
                    (output/'review'/f"{record['task_id']}-r{record['repeat']}.txt").write_text(sample, encoding='utf-8')
    finally:
        telemetry = gpu.finish()
    scored = [r for r in records if not r.get('skipped_context')]
    passed = sum(r['pass'] for r in scored)
    skipped = len(records)-len(scored)
    summary = {'label': args.label, 'model': args.model, 'base_url': client.base, 'parallel': args.parallel, 'profile': args.profile,
               'thinking': args.thinking, 'repeats': args.repeats, 'task_count': len(tasks), 'trial_count': len(records),
               'request_count': sum(len(r['http_requests']) for r in records), 'passed': passed, 'failed': len(scored)-passed,
               'skipped_context': skipped, 'scored_trial_count': len(scored),
               'skipped_by_category': {cat: sum(r.get('skipped_context', False) for r in records if r['category'] == cat) for cat in 'STFLVCGR'},
               'counts': dict(Counter(t['category'] for t in tasks)), 'server': meta, **telemetry,
               'category_rates': {cat: statistics.mean(r['pass'] for r in scored if r['category'] == cat) if any(r['category'] == cat for r in scored) else None for cat in 'STFLVCGR'},
               'overall_score': statistics.mean(r['score'] for r in scored) if scored else None}
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    (output/'run.md').write_text(f"# {args.label}\n\nModel: {args.model}\n\n{passed}/{len(scored)} scored trials passed; {summary['request_count']} HTTP requests; {skipped} skipped_context.\nParallel: {args.parallel}; thinking: {args.thinking}; profile: {args.profile}.\n\nContext n_ctx: {meta['n_ctx']}; slots: {meta['slots']}; per-slot context: {meta['per_slot_context']}.\n\nCounts: {summary['counts']}\n\nSkipped by category: {summary['skipped_by_category']}\n\nVoice checks measure structure only. Review drafts in review/. Nominal token estimates use characters/4; context sizing uses /tokenize for generated prompts, otherwise characters/2.2, plus a framing allowance. Context skips are excluded from scores.\n", encoding='utf-8')
    print(f"{passed} passed, {len(scored)-passed} failed, {skipped} skipped_context\n{output}")

if __name__ == '__main__':
    main()
