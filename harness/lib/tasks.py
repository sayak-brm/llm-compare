import copy
import json
from pathlib import Path
from tasks.gen import generate
from lib.checks import emoji

ROOT = Path(__file__).resolve().parents[1]

def load_tasks():
    surfaces = json.loads((ROOT/'tasks/c/surfaces.json').read_text(encoding='utf-8'))
    rules = json.loads((ROOT/'tasks/c/rules.json').read_text(encoding='utf-8'))
    examples = []
    for path in sorted((ROOT/'tasks/voice_examples').rglob('*')):
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(ROOT.resolve()):
            # Treat all files here as local text examples; no external symlinks.
            examples.append(path.read_text(encoding='utf-8', errors='replace'))
    voice = '\n\n'.join(examples)
    result = []
    for path in sorted((ROOT/'tasks').glob('[STFLVCGR][0-9][0-9].json')):
        task = json.loads(path.read_text(encoding='utf-8'))
        if task['category'] == 'C':
            task['tools'] = [{'type': 'function', 'function': {'name': name, 'description': 'Mock only; '+surfaces[name]['status'], 'parameters': surfaces[name]['schema']}} for name in task['tool_surfaces']]
            task['messages'][0]['content'] += '\n'+'\n'.join(rules[k]['text'] for k in task['rule_ids'])
        generated = generate(task['check']['generator']) if task['check'].get('generator') else ''
        for msg in task['messages']:
            msg['content'] = msg['content'].replace('{{GENERATED}}', generated).replace('{{VOICE}}', voice)
        task['voice_emoji'] = emoji(voice)
        task['ctx_tokens_estimate'] = (sum(len(m['content']) for m in task['messages'])+len(json.dumps(task.get('tools', []))))//4
        result.append(task)
    return result

def expected_args(step):
    if 'manage' in step:
        return {'op': step['args_op'], 'args': json.dumps(step['manage'])}
    return step.get('args', step.get('command_exact', {}))
