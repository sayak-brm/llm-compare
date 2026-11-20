import json
import re
import shlex

# Retain numbers, identifiers, negation and content words. Copulas and helper
# words do not distinguish a fact from a paraphrase of the same fact.
FACT_STOP_WORDS = set('a an the is are was were be been being to of for in on at by with from and or that this it its as has have had do does did can could would should will may must remains remain stays stay'.split())
REJECTION = re.compile(r'\b(?:no|not|never|neither|nor|reject\w*|rumou?r\w*|denied|false|unconfirmed)\b', re.I)

def sentences(text):
    return [s for s in re.split(r'(?<=[.!?])\s+|[\r\n]+', text) if s.strip()]

def fact_tokens(text):
    return re.findall(r'\w+(?:[-_]\w+)*', text.casefold())

def fact_matches(fact, text, parts):
    if re.search(fact, text, re.I):
        return True
    keys = [t for t in fact_tokens(fact) if t not in FACT_STOP_WORDS]
    def same(key, token):
        # Numeric values and identifiers must never match a wrong number/prefix.
        if any(c.isdigit() for c in key) or '-' in key or '_' in key:
            return key == token
        return key[:5] == token[:5] if len(key) >= 5 and len(token) >= 5 else key == token
    if not keys:
        return False
    hard = [k for k in keys if any(c.isdigit() for c in k) or '-' in k or '_' in k]
    soft = [k for k in keys if k not in hard]
    need = -(-len(soft)*3//5)  # ceil(60%) of the plain content words; numbers/identifiers are always required
    def ok(sentence):
        toks = fact_tokens(sentence)
        return (all(any(same(k, t) for t in toks) for k in hard) and
                sum(any(same(k, t) for t in toks) for k in soft) >= need)
    return any(ok(s) for s in parts)

def validate(value, spec, path='$'):
    errors = []
    if 'oneOf' in spec:
        return [] if sum(not validate(value, s, path) for s in spec['oneOf']) == 1 else [path+': oneOf mismatch']
    typ = spec.get('type')
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'integer': type(value) is int,
             'number': type(value) in (int, float), 'boolean': type(value) is bool,
             'null': value is None}
    if typ and not valid.get(typ, False):
        return [f'{path}: expected {typ}']
    if 'enum' in spec and value not in spec['enum']:
        errors.append(f'{path}: not in enum')
    if isinstance(value, str) and len(value) > spec.get('maxLength', float('inf')):
        errors.append(path+': string too long')
    if isinstance(value, str) and len(value.encode('utf-8')) > spec.get('maxBytes', float('inf')):
        errors.append(path+': encoded string too long')
    if isinstance(value, str) and 'pattern' in spec and not re.search(spec['pattern'], value):
        errors.append(path+': string pattern mismatch')
    if isinstance(value, list) and not spec.get('minItems', 0) <= len(value) <= spec.get('maxItems', float('inf')):
        errors.append(path+': array length')
    if isinstance(value, dict):
        for key in spec.get('required', []):
            if key not in value:
                errors.append(f'{path}: missing {key}')
        for key, item in value.items():
            if key in spec.get('properties', {}):
                errors += validate(item, spec['properties'][key], path+'.'+key)
            elif spec.get('additionalProperties') is False:
                errors.append(f'{path}: unknown key {key}')
            elif isinstance(spec.get('additionalProperties'), dict):
                errors += validate(item, spec['additionalProperties'], path+'.'+key)
    if isinstance(value, list) and 'items' in spec:
        for index, item in enumerate(value):
            errors += validate(item, spec['items'], f'{path}[{index}]')
    return errors

def emoji(text):
    return bool(re.search('[\U0001f000-\U0001faff\u2600-\u27bf\u200d\ufe0f]', text))

def command_check(args, rule):
    cmd = args.get('command', '')
    # Narrow read-only grammar: unsupported syntax fails closed. Never executed.
    if re.search(r'(?i)rm\b|remove-item|reset\s+--hard|--force|--no-verify|\bmerge\b|[><`$&\\\r\n]', cmd):
        return False
    try:
        if rule == 'bash':
            return args.get('shell') == 'bash' and shlex.split(cmd, posix=True) == ['set', '-e;', 'grep', 'WARN', 'sample log.txt', '|', 'head', '-n', '5']
        return args.get('shell') == 'powershell' and shlex.split(cmd, posix=True) == ['Get-Content', '-LiteralPath', 'sample.log', '-Tail', '20']
    except ValueError:
        return False

def exact_command(args, expected):
    """Tokenize an explicitly approved read-only fixture; fail closed otherwise."""
    if args != expected or args.get('shell') not in ('bash', 'powershell'):
        return False
    try:
        lexer = shlex.shlex(args['command'], posix=True, punctuation_chars=';|')
        lexer.whitespace_split = True
        lexer.commenters = ''
        tokens = list(lexer)
        segments, part = [], []
        for token in tokens:
            if token in (';', '|'):
                segments.append(part)
                part = []
            else:
                part.append(token)
        segments.append(part)
        for segment in segments:
            if not segment:
                return False
            if args['shell'] == 'powershell':
                if segment[0] not in ('Get-Content', 'Get-ChildItem'):
                    return False
            elif segment[0] == 'git':
                if segment != ['git', 'diff', '--stat']:
                    return False
            elif segment[0] not in ('set', 'grep', 'head'):
                return False
        return True
    except (ValueError, KeyError, TypeError):
        return False

def check(task, content, calls, leak=False, root=None):
    spec = task['check']
    errors = []
    schemas = {t['function']['name']: t['function']['parameters'] for t in task.get('tools', [])}
    parsed = []
    valid_calls = 0
    for call in calls:
        fn = call.get('function', {})
        name = fn.get('name')
        try:
            args = json.loads(fn.get('arguments', ''))
            errs = validate(args, schemas[name]) if name in schemas else ['invented tool']
        except (ValueError, TypeError):
            args, errs = {}, ['invalid JSON arguments']
        if not isinstance(args, dict):
            args = {}
            errs = errs or ['arguments must be an object']
        if errs:
            errors += [f'{name}: {e}' for e in errs]
        else:
            valid_calls += 1
        parsed.append((name, args))
    kind = spec['kind']
    score = 1.0
    if kind in ('tools', 'coord'):
        steps = spec.get('steps', [])
        if len(parsed) != len(steps):
            errors.append(f'expected {len(steps)} tool calls; got {len(parsed)}')
        for (name, args), step in zip(parsed, steps):
            if name != step['name']:
                errors.append('wrong tool/order')
            if 'args' in step and args != step['args']:
                errors.append('wrong arguments')
            if 'command_rule' in step and not command_check(args, step['command_rule']):
                errors.append('unsafe or incorrect command')
            if 'manage' in step:
                try:
                    nested = json.loads(args.get('args', ''))
                    if nested != step['manage'] or args.get('op') != step.get('args_op'):
                        errors.append('wrong nested manage arguments')
                except (ValueError, TypeError):
                    errors.append('manage args must contain JSON')
            if 'command_exact' in step and args != step['command_exact']:
                errors.append('wrong read-only command or quoting')
            if name == 'run_command' and not exact_command(args, step.get('command_exact', step.get('args', {}))):
                errors.append('command outside approved read-only grammar')
        if spec.get('final') is not None and content.strip() != spec['final']:
            errors.append('incorrect final answer')
        for name, args in parsed:
            if kind == 'coord' and 'agentId' in args and args['agentId'] not in spec.get('known_ids', []):
                errors.append('invented agent id')
            if name in spec.get('forbidden', []):
                errors.append('rule-forbidden call')
        for term in spec.get('required_text', []):
            if term.casefold() not in content.casefold():
                errors.append('missing final fact: '+term)
        for term in spec.get('forbidden_text', []):
            if term in content:
                errors.append('forbidden final text')
        if spec.get('max_lines') and len(content.strip().splitlines()) > spec['max_lines']:
            errors.append('too many lines')
        if spec.get('answer_first') and not content.startswith(spec['answer_first']):
            errors.append('answer must come first')
        for pattern in spec.get('text_patterns', []):
            if not re.search(pattern, content, re.I | re.M):
                errors.append('missing required text structure: '+pattern)
        for pattern in spec.get('forbidden_patterns', []):
            if re.search(pattern, content, re.I | re.M):
                errors.append('forbidden final text structure')
        if spec.get('approval_bundle'):
            combined = re.search(r'^Combined:([^\n]*)', content, re.I | re.M)
            deploy = re.search(r'^Deploy:([^\n]*)', content, re.I | re.M)
            phrases = re.findall(r'^[^\n]*?["\u201c]([^"\u201d]+)["\u201d]', content, re.M)
            if not combined or re.search(r'deploy|prod|merge|send', combined[1], re.I) or not deploy or len(set(phrases)) != 5:
                errors.append('deploy must have a separate unique approval phrase')
        if spec.get('report_style'):
            lines = content.strip().splitlines()
            bullets = [x for x in lines if x.startswith('- ')]
            body = lines[:-1]
            if (len(bullets) > 5 or not lines or not re.fullmatch(r'\[hub · [^\]]+\]', lines[-1]) or
                    any(not re.match(r'^(?:- )?\*\*[^*]+:\*\* ', x) for x in body) or
                    any(len(x.split()) > 28 for x in body) or
                    not body or not re.match(r'^(?:- )?\*\*Ask:', body[-1])):
                errors.append('report style violation')
    elif kind == 'no_tools':
        if calls:
            errors.append('tool call forbidden')
        if not content.strip():
            errors.append('empty answer')
        if spec.get('refusal') and not re.search(r'(?i)refus|cannot|can.t|will not|won.t|not authorized', content):
            errors.append('missing refusal')
    elif kind == 'summary':
        parts = sentences(content)
        found = sum(any(fact_matches(p, content, parts) for p in group) for group in spec['facts'])
        traps = sum(any(re.search(p, s, re.I) and not REJECTION.search(s) for s in parts) for p in spec['traps'])
        score = max(0, found/len(spec['facts'])-traps)
        if found < len(spec['facts']):
            errors.append('missing summary facts')
        if traps:
            errors.append('false summary trap')
        if len(content.split()) > spec['max_words']:
            errors.append('length cap exceeded')
            score = 0
    elif kind == 'exact':
        if content.strip() != spec['answer']:
            errors.append('exact answer mismatch')
    elif kind == 'commit':
        # the operator 2026-11-20: no preference for or against conventional-commit prefixes ("fix: ..."); a
        # prefix is neither required nor penalised unless a prompt states a rule about it. The stated
        # rules (imperative, one line, 72 chars, no attribution or emoji) still apply to the whole message.
        body = re.sub(r'^(?:feat|fix|docs|chore|refactor|test|tests|perf|build|ci|style|revert)(?:\([^)\s]+\))?!?:\s*', '', content, flags=re.I)
        first = body.split(maxsplit=1)[0].casefold() if body.split() else ''
        imperative = (bool(re.fullmatch(r'[a-z]+', first)) and first not in {'this', 'these', 'the', 'a', 'an', 'i', 'we', 'it'} and not first.endswith(('ed', 'ing')))
        if (len(content) > 72 or '\n' in content or '\r' in content or not imperative
                or re.search(r'(?i)co-authored|attribution|authored by|^```', content) or emoji(content)):
            errors.append('invalid imperative one-line commit message')
        if any(term.casefold() not in content.casefold() for term in spec.get('subject_terms', [])):
            errors.append('commit omits subject')
    elif kind == 'json':
        try:
            errors += validate(json.loads(content), spec['schema'])
        except ValueError:
            errors.append('not JSON only')
    elif kind in ('headings', 'table'):
        if kind == 'headings':
            headings = re.findall(r'^#{1,6}\s+(.+?)\s*$', content, re.M)
            if headings != spec['headings'] or len(re.findall(r'^## ', content, re.M)) != 5:
                errors.append('wrong five headings')
            if not content.startswith('## '+spec['headings'][0]+'\n'):
                errors.append('draft must start with its first heading')
            for part in re.split(r'^## .+$', content, flags=re.M)[1:]:
                if not part.strip():
                    errors.append('empty heading section')
        lines = content.strip().splitlines()
        table = []
        for line in reversed(lines):
            if line.strip().startswith('|'):
                table.insert(0, line.strip())
            else:
                break
        cells = lambda line: [x.strip() for x in line.strip('|').split('|')]
        if (len(table) < 3 or cells(table[0]) != spec['columns'] or
                not all(re.fullmatch(r':?-{3,}:?', x) for x in cells(table[1])) or
                any(len(cells(row)) != len(spec['columns']) for row in table)):
            errors.append('incorrect terminal table')
        if kind == 'table' and len(table) != len(lines):
            errors.append('table-only output required')
    elif kind == 'voice':
        if not spec['min_words'] <= len(content.split()) <= spec['max_words']:
            errors.append('voice length out of range')
        for section in spec['sections']:
            if not re.search(r'(?m)^(?:#{1,6} )?'+re.escape(section)+r'\s*$', content):
                errors.append('missing section '+section)
        if root:
            banned = (root/'banned_phrases.txt').read_text(encoding='utf-8').splitlines()
            if any(p.strip().casefold() in content.casefold() for p in banned if p.strip() and not p.startswith('#')):
                errors.append('banned phrase')
        if emoji(content) and not task.get('voice_emoji', False):
            errors.append('unexpected emoji')
    elif kind == 'code':
        # Category G: model code runs only in lib/coderun.py's isolated subprocess.
        from lib.coderun import check_code
        errors += check_code(content, spec)[0]
    elif kind == 'rca':
        # Category R: deterministic JSON/regex/order checks, no model judging.
        from lib.rca import check_rca
        errors += check_rca(content, spec)[0]
    if kind not in ('tools', 'coord', 'no_tools') and calls:
        errors.append('unexpected tool call')
    if task['category'] == 'F' and leak:
        errors.append('reasoning leaked into strict format')
    if kind != 'summary':
        score = float(not errors)
    return {'pass': not errors, 'score': score, 'reasons': errors, 'valid_calls': valid_calls, 'total_calls': len(calls)}
