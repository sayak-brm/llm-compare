"""Compare saved runs without network calls or model judging."""
import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))  # works under python -I
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent

def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None

def fmt(value, percent=False):
    if value is None:
        return 'N/A'
    return f'{value:.1%}' if percent else f'{value:.3f}'

def escape(text):
    return str(text).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')

def scored(records):
    return [r for r in records if not (r.get('skipped_context') or r.get('status') == 'skipped_context')]

def load(path):
    summary = json.loads((path/'summary.json').read_text(encoding='utf-8'))
    records = [json.loads(line) for line in (path/'requests.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    return path, summary, records

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', nargs='*', type=Path)
    parser.add_argument('--thresholds', type=Path, default=ROOT/'thresholds.json')
    args = parser.parse_args()
    paths = args.runs or sorted((ROOT/'results').glob('*/summary.json'))
    paths = [p.parent if p.name == 'summary.json' else p for p in paths]
    if not paths:
        raise SystemExit('No runs found')
    data = [load(p) for p in paths]
    thresholds = json.loads(args.thresholds.read_text(encoding='utf-8'))
    header = ['Candidate', 'Tasks', 'Trials scored/total', 'Requests', 'Skipped S/T/F/L/V/C/G/R', 'n_ctx', 'Slots', 'Context/slot', 'S', 'T', 'F', 'L', 'V-structure', 'C Fleet Hub coordination', 'G coding goal', 'R RCA/planning', 'Overall score', 'Median TTFT s', 'Median gen tok/s', 'Peak VRAM MB', 'Max passed prompt', 'Tool-call validity', 'Reasoning leaks', 'Parallel latency ratio']
    print('| '+' | '.join(header)+' |')
    print('| '+' | '.join('---' for _ in header)+' |')
    readiness = []
    for path, summary, records in data:
        included = scored(records)
        rates = {c: statistics.mean(r['pass'] for r in included if r['category'] == c) if any(r['category'] == c for r in included) else None for c in 'STFLVCGR'}
        tool_trials = [r for r in included if r['category'] in ('T', 'C')]
        tool_rate = statistics.mean(r['pass'] for r in tool_trials) if tool_trials else None
        all_traces = [t for r in records for t in r.get('http_requests', [r])]
        traces = [t for r in included for t in r.get('http_requests', [r])]
        good_sizes = [t.get('prompt_tokens') or r.get('prompt_tokens_measured') or r['ctx_tokens_estimate'] for r in included if r['pass'] for t in r.get('http_requests', [r])]
        ratio = None
        if summary['parallel'] > 1:
            # Compare matching model/thinking and exact task/repeat sets; avoid conflating workloads.
            key = {(r['task_id'], r['repeat']) for r in included}
            baseline = [scored(other) for _, sm, other in data if sm['model'] == summary['model'] and sm['parallel'] == 1 and sm.get('thinking') == summary.get('thinking') and sm.get('server') == summary.get('server') and {(r['task_id'], r['repeat']) for r in scored(other)} == key]
            if baseline:
                base = median(t['total_time'] for rs in baseline for r in rs for t in r.get('http_requests', [r]))
                current = median(t['total_time'] for t in traces)
                ratio = current/base if base and current is not None else None
        skip_counts = '/'.join(str(sum(r['category'] == c for r in records)-sum(r['category'] == c for r in included)) for c in 'STFLVCGR')
        server = summary.get('server') or {}
        n_ctx, slots = server.get('n_ctx'), server.get('slots')
        per_slot = server.get('per_slot_context') or (n_ctx//slots if n_ctx and slots else None)
        row = [escape(summary['label'])+' / '+escape(summary['model'])+f" (p={summary['parallel']})", str(len({r['task_id'] for r in records})), f'{len(included)}/{len(records)}', str(len(all_traces)), skip_counts, str(n_ctx) if n_ctx is not None else 'N/A', str(slots) if slots is not None else 'N/A', str(per_slot) if per_slot is not None else 'N/A']
        row += [fmt(rates[c], True) for c in 'STFLVCGR']
        row += [fmt(statistics.mean(r['score'] for r in included) if included else None), fmt(median(t['ttft'] for t in traces)), fmt(median(t['generated_tokens_s'] for t in traces)), fmt(summary.get('peak_vram_mb')), str(max(good_sizes)) if good_sizes else 'N/A', fmt(tool_rate, True), str(sum(t['reasoning_leak'] for t in all_traces)), fmt(ratio)]
        print('| '+' | '.join(row)+' |')
        readiness.append((summary['label'], {'tool-call': tool_rate, 'format': rates['F'], 'long-context': rates['L'], 'coding-goal': rates['G'], 'rca-planning': rates['R']}))
    print('\nScored '+str(len({r['task_id'] for _, _, records in data for r in scored(records)}))+' unique tasks; '+str(sum(len(scored(records)) for _, _, records in data))+' scored trials; '+str(sum(len(records)-len(scored(records)) for _, _, records in data))+' skipped_context; '+str(sum(len(r.get('http_requests', [r])) for _, _, records in data for r in records))+' HTTP requests.')
    print('\nTool-call validity is the full deterministic T/C trial pass rate, including tool choice, schema, arguments, sequence, restraint and required final text. Context skips are excluded from scores, rates and latency medians. Missing or entirely skipped categories fail readiness as N/A. Voice scores measure structure only. Max prompt uses server usage, then measured sizing, then legacy characters/4.')
    print('\nReadiness for light-agent roles')
    for label, rates in readiness:
        for name in ['tool-call', 'format', 'long-context', 'coding-goal', 'rca-planning']:
            if name not in thresholds:
                continue
            value, threshold = rates[name], thresholds[name]
            verdict = 'PASS' if value is not None and value >= threshold else 'FAIL'
            print(f'- {escape(label)}: {name} {fmt(value, True)} >= {threshold:.0%}: {verdict}')

if __name__ == '__main__':
    main()
