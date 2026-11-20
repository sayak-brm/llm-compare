"""Best-effort tokenizer sizing and explicit, persistent generator calibration."""
import copy
import json
import math

from tasks.gen import generate

def prompt_text(messages, tools=None):
    # /tokenize does not apply a chat template. Include role/tool serialization
    # and reserve a small framing allowance when making a fit decision.
    obj = {'messages': messages}
    if tools:
        obj['tools'] = tools
    return json.dumps(obj, ensure_ascii=False)

def measure_prompt(client, messages, tools=None, probe=True):
    text = prompt_text(messages, tools)
    count = client.tokenize(text) if probe else None
    return {'prompt_tokens_measured': count if count is not None else math.ceil(len(text)/2.2),
            'prompt_tokens_source': 'tokenize' if count is not None else 'chars/2.2',
            'prompt_framing_allowance': 16*(len(messages)+1)}

def exceeds_context(sizing, max_tokens, per_slot_context):
    return (per_slot_context is not None and
            sizing['prompt_tokens_measured']+sizing.get('prompt_framing_allowance', 0)+max_tokens > per_slot_context)

def prepare_context(tasks, client, meta):
    for task in tasks:
        task.update(measure_prompt(client, task['messages'], task.get('tools'), probe=bool(task['check'].get('generator'))))
        task['per_slot_context'] = meta.get('per_slot_context')
        task['n_ctx'] = meta.get('n_ctx')

def calibrate_tasks(client, tasks, meta, task_dir, target_tokens=None):
    """Resize generated S/L inputs, proportionally to their nominal targets.

    Never generates completions. Only --calibrate calls this mutating function.
    A bounded search chooses the closest non-overflowing full prompt.
    """
    per_slot = meta.get('per_slot_context')
    if not per_slot:
        raise ValueError('Calibration requires a known positive n_ctx and slot count from the server')
    selected = [t for t in tasks if t['category'] in ('S', 'L') and t['check'].get('generator')]
    if not selected:
        raise ValueError('No generated S/L tasks selected for calibration')
    largest = max(t['check']['generator']['tokens'] for t in selected)
    ceiling = min(int(per_slot*.9), target_tokens if target_tokens is not None else int(per_slot*.9))
    if ceiling <= 0:
        raise ValueError('Target tokens must be positive')
    changes = []
    for task in selected:
        path = task_dir/(task['id']+'.json')
        raw = json.loads(path.read_text(encoding='utf-8'))
        spec = raw['check']['generator']
        allowance = 16*(len(raw['messages'])+1)
        goal = min(int(ceiling*spec['tokens']/largest), int(per_slot)-raw['max_tokens']-allowance)
        if goal <= 0:
            raise ValueError('Context is too small even for the completion reservation')
        def candidate(chars):
            candidate_spec = dict(spec, target_chars=chars)
            generated = generate(candidate_spec)
            messages = [dict(m, content=m['content'].replace('{{GENERATED}}', generated)) for m in raw['messages']]
            return measure_prompt(client, messages, raw.get('tools')), messages
        low, high = 2048, max(2048, min(8_000_000, max(spec['tokens']*4, goal*12)))
        size = max(low, min(high, spec.get('target_chars', spec['tokens']*4)))
        best = None
        for _ in range(16):
            sizing, messages = candidate(size)
            count = sizing['prompt_tokens_measured']
            if count <= goal:
                if best is None or count > best[1]['prompt_tokens_measured']:
                    best = size, sizing, messages
                if count >= goal*.98:
                    break
                low = size+1
            else:
                high = size-1
            if low > high:
                break
            ratio = int(size*goal/max(1, count))
            size = ratio if low <= ratio <= high else (low+high)//2
        if best is None:
            # Keep all needle/fact markers intact; never shrink below the safe
            # generator minimum just to claim a fit.
            changes.append({'task_id': task['id'], 'status': 'unchanged_context_too_small', 'target_prompt_tokens': goal})
            continue
        chars, sizing, messages = best
        spec['target_chars'] = chars
        spec['calibration'] = {'target_prompt_tokens': goal, **sizing, 'n_ctx': meta['n_ctx'],
                               'slots': meta['slots'], 'per_slot_context': per_slot, 'model_basename': meta.get('model_basename')}
        raw['ctx_tokens_estimate'] = sum(len(m['content']) for m in messages)//4
        path.write_text(json.dumps(raw, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
        changes.append({'task_id': task['id'], 'status': 'calibrated', 'target_chars': chars, 'target_prompt_tokens': goal, **sizing})
    return changes
