"""Isolated runner for model-written Python (category G, check kind `code`).

Model code is never imported or executed in the harness process. The flow is:

1. extract_python(): take the first ```python (or ```py) fenced block.
2. precheck(): AST gate. Imports outside ALLOWED_IMPORTS, dangerous builtins,
   dunder attribute access (except a small list of ordinary method names),
   frame/code introspection attributes and task-specific forbidden names are
   rejected before anything runs.
3. run_tests(): a fresh `python -I -c RUNNER` subprocess with an EMPTY
   environment, cwd set to a fresh temp dir, stdin carrying the code and the
   hidden test expressions (never the expected values), a wall-clock timeout
   (default 5 s), a stdout cap, and on Windows a Job Object that caps memory,
   forbids child processes and kills the process tree when closed. The temp
   dir is always removed. Expected values stay in the parent, which compares.

The test expressions are harness-authored (trusted); only the model's code is
AST-gated. Standard library only.
"""
import ast
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading

ALLOWED_IMPORTS = {'math', 're', 'json', 'itertools', 'collections', 'functools', 'heapq', 'bisect',
                   'dataclasses', 'typing', 'string', 'datetime', 'decimal', 'fractions', 'textwrap',
                   'operator', 'enum', 'copy', 'statistics', 'unicodedata'}
FORBIDDEN_CALLS = {'open', 'exec', 'eval', 'compile', '__import__', 'globals', 'locals', 'vars',
                   'breakpoint', 'input', 'help', 'exit', 'quit', 'memoryview'}
FORBIDDEN_NAMES = {'__builtins__', '__loader__', '__spec__', '__import__', '__file__', '__cached__'}
# Ordinary protocol methods a solution may define or call (super().__init__()).
SAFE_DUNDERS = {'__init__', '__post_init__', '__repr__', '__str__', '__eq__', '__ne__', '__lt__', '__le__',
                '__gt__', '__ge__', '__hash__', '__len__', '__iter__', '__next__', '__contains__',
                '__getitem__', '__setitem__', '__delitem__', '__bool__', '__name__', '__call__',
                '__enter__', '__exit__', '__add__', '__sub__', '__mul__', '__slots__'}
# Frame, traceback, generator and code-object introspection: the route from a
# running function back to the runner's globals.
INTROSPECTION_ATTRS = {'gi_frame', 'gi_code', 'cr_frame', 'cr_code', 'ag_frame', 'ag_code', 'f_back',
                       'f_globals', 'f_locals', 'f_builtins', 'f_code', 'tb_frame', 'tb_next',
                       'co_code', 'co_consts', 'func_globals', 'mro'}
ATTR_BUILTINS = {'getattr', 'setattr', 'delattr', 'hasattr'}

DEFAULT_TIMEOUT = 5.0
OUTPUT_CAP = 256*1024
MEMORY_CAP = 512*1024*1024

FENCE = re.compile(r'```[ \t]*(?:python|py)[ \t]*\r?\n(.*?)```', re.S | re.I)

def extract_python(content):
    match = FENCE.search(content or '')
    return match.group(1) if match else None

def precheck(source, forbid_names=(), require_defs=(), max_lines=None):
    """Return a list of rejection reasons; empty means the code may run."""
    if max_lines and len(source.splitlines()) > max_lines:
        return [f'code longer than {max_lines} lines']
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        return [f'syntax error: {exc.__class__.__name__}']
    errors = []
    forbid = set(forbid_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] not in ALLOWED_IMPORTS:
                    errors.append('import not allowed: '+alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or '').split('.')[0] not in ALLOWED_IMPORTS:
                errors.append('import not allowed: '+str(node.module))
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_CALLS or node.id in FORBIDDEN_NAMES:
                errors.append('forbidden name: '+node.id)
            if node.id in forbid:
                errors.append('task-forbidden name: '+node.id)
            if node.id in ATTR_BUILTINS:
                parent_ok = False
                for call in ast.walk(tree):
                    if isinstance(call, ast.Call) and call.func is node and len(call.args) >= 2:
                        attr = call.args[1]
                        parent_ok = isinstance(attr, ast.Constant) and isinstance(attr.value, str) and not attr.value.startswith('_')
                if not parent_ok:
                    errors.append(node.id+' only allowed with a literal non-underscore attribute name')
        elif isinstance(node, ast.Attribute):
            name = node.attr
            if (name.startswith('__') and name.endswith('__') and name not in SAFE_DUNDERS) or name in INTROSPECTION_ATTRS:
                errors.append('forbidden attribute: '+name)
            if name in forbid:
                errors.append('task-forbidden name: '+name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.fullmatch(r'__\w+__', node.value) and node.value not in SAFE_DUNDERS | {'__main__'}:
                errors.append('forbidden dunder string')
    top = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    for name in require_defs:
        if name not in top:
            errors.append('missing top-level definition: '+name)
    return sorted(set(errors))

# The runner is trusted harness code. It reads one JSON payload from stdin,
# executes the model code once per test in a fresh namespace, evaluates the
# harness-written test expression and prints one nonce-tagged result line.
# It uses its own encoder so a solution that monkeypatches the json module
# cannot rewrite results.
RUNNER = r'''
import sys, builtins
_raw = sys.stdin.buffer.read().decode('utf-8')
import json as _json
_p = _json.loads(_raw)
del _raw
_nonce, _code, _tests = _p['nonce'], _p['code'], _p['tests']
_w, _fl = sys.__stdout__.write, sys.__stdout__.flush
_isinstance, _type, _repr, _str, _len, _list, _sorted, _float = isinstance, type, repr, str, len, list, sorted, float
class _Cap:
    def __init__(self): self.n = 0
    def write(self, s):
        self.n += _len(s)
        return _len(s)
    def flush(self): pass
sys.stdout = sys.stderr = _Cap()
def _esc(s):
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"': out.append('\\"')
        elif ch == '\\': out.append('\\\\')
        elif o < 0x20 or o > 0x7e: out.append('\\u%04x' % o if o < 0x10000 else '\\u%04x\\u%04x' % (0xd800 + ((o - 0x10000) >> 10), 0xdc00 + ((o - 0x10000) & 0x3ff)))
        else: out.append(ch)
    out.append('"')
    return ''.join(out)
def _enc(v, depth=0):
    if depth > 60: raise ValueError('too deep')
    t = _type(v)
    if v is None: return 'null'
    if t is bool: return 'true' if v else 'false'
    if t is int: return _str(v)
    if t is float:
        if v != v or v in (_float('inf'), -_float('inf')): raise ValueError('non-finite float')
        return _repr(v)
    if t is str: return _esc(v)
    if t in (list, tuple): return '[' + ','.join(_enc(x, depth+1) for x in v) + ']'
    if t is dict:
        parts = []
        for k, x in v.items():
            if _type(k) is not str: raise ValueError('non-string key')
            parts.append(_esc(k) + ':' + _enc(x, depth+1))
        return '{' + ','.join(parts) + '}'
    raise ValueError('unsupported result type ' + t.__name__)
_rows = []
for _t in _tests:
    try:
        _g = {'__name__': 'solution', '__builtins__': builtins}
        exec(compile(_code, 'solution.py', 'exec'), _g)
        if _t.get('setup'):
            exec(compile(_t['setup'], 'test_setup', 'exec'), _g)
        _v = eval(compile(_t['expr'], 'test_expr', 'eval'), _g)
        _rows.append('{"ok":true,"value":' + _enc(_v) + '}')
    except BaseException as _e:
        try:
            _names = [c.__name__ for c in _type(_e).__mro__ if _type(c.__name__) is str]
        except BaseException:
            _names = ['BaseException']
        _rows.append('{"ok":false,"exc":' + _enc(_names) + '}')
_w(_nonce + '[' + ','.join(_rows) + ']\n')
_fl()
'''

def _windows_job(proc):
    """Cap memory, forbid child processes, kill on close. Returns handle or None."""
    if os.name != 'nt':
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ('r', 'w', 'o', 'rb', 'wb', 'ob')]

        class Extended(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', IoCounters), ('ProcessMemoryLimit', ctypes.c_size_t),
                        ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t),
                        ('PeakJobMemoryUsed', ctypes.c_size_t)]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = Extended()
        # ACTIVE_PROCESS | PROCESS_MEMORY | JOB_MEMORY | DIE_ON_UNHANDLED_EXCEPTION | KILL_ON_JOB_CLOSE
        info.BasicLimitInformation.LimitFlags = 0x8 | 0x100 | 0x200 | 0x400 | 0x2000
        info.BasicLimitInformation.ActiveProcessLimit = 1
        info.ProcessMemoryLimit = info.JobMemoryLimit = MEMORY_CAP
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(job)
            return None
        if not k32.AssignProcessToJobObject(job, int(proc._handle)):
            k32.CloseHandle(job)
            return None
        return (k32, job)
    except (OSError, AttributeError, ValueError):
        return None

def _posix_limits():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_CAP, MEMORY_CAP))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))

def run_tests(source, tests, timeout=DEFAULT_TIMEOUT):
    """Run hidden tests in an isolated subprocess.

    `tests` is a list of dicts with `expr` and optional `setup`; expected
    values are NOT passed here. Returns {'rows': [...]} or {'error': str},
    plus 'sandbox' describing which limits were applied.
    """
    nonce = 'R'+secrets.token_hex(16)+':'
    payload = json.dumps({'nonce': nonce, 'code': source,
                          'tests': [{'expr': t['expr'], 'setup': t.get('setup', '')} for t in tests]}).encode('utf-8')
    workdir = tempfile.mkdtemp(prefix='gr-code-')
    env = {}
    if os.name == 'nt' and os.environ.get('SYSTEMROOT') and os.environ.get('GR_CODE_NEEDS_SYSTEMROOT'):
        env['SYSTEMROOT'] = os.environ['SYSTEMROOT']  # opt-in only; empty by default
    sandbox = {'empty_env': not env, 'timeout_s': timeout, 'output_cap': OUTPUT_CAP, 'memory_cap': False}
    job = None
    proc = None
    try:
        kwargs = dict(cwd=workdir, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if os.name == 'nt':
            kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        else:
            kwargs['preexec_fn'] = _posix_limits
            kwargs['start_new_session'] = True
        proc = subprocess.Popen([sys.executable, '-I', '-S', '-c', RUNNER], **kwargs)
        job = _windows_job(proc)
        sandbox['memory_cap'] = bool(job) or os.name != 'nt'
        chunks, size = [], [0]
        def reader():
            while True:
                data = proc.stdout.read(8192)
                if not data:
                    break
                if size[0] < OUTPUT_CAP:
                    chunks.append(data)
                size[0] += len(data)
                if size[0] > OUTPUT_CAP:
                    try:
                        proc.kill()
                    except OSError:
                        pass
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        try:
            proc.stdin.write(payload)
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            thread.join(timeout=2)
            return {'error': f'timeout after {timeout:g}s', 'sandbox': sandbox}
        thread.join(timeout=2)
        if size[0] > OUTPUT_CAP:
            return {'error': 'output cap exceeded', 'sandbox': sandbox}
        out = b''.join(chunks).decode('utf-8', errors='replace')
        lines = [line for line in out.splitlines() if line.startswith(nonce)]
        if proc.returncode != 0 or len(lines) != 1:
            return {'error': f'runner exited {proc.returncode} without a result', 'sandbox': sandbox}
        try:
            rows = json.loads(lines[0][len(nonce):])
        except ValueError:
            return {'error': 'malformed runner result', 'sandbox': sandbox}
        if not isinstance(rows, list) or len(rows) != len(tests):
            return {'error': 'result count mismatch', 'sandbox': sandbox}
        return {'rows': rows, 'sandbox': sandbox}
    finally:
        if proc and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if proc and proc.stdout:
            proc.stdout.close()
        if job:
            job[0].CloseHandle(job[1])
        shutil.rmtree(workdir, ignore_errors=True)

def _equal(got, want, tol):
    if tol is not None and isinstance(want, (int, float)) and isinstance(got, (int, float)) and not isinstance(got, bool):
        return abs(got-want) <= tol
    if isinstance(want, list) and isinstance(got, list):
        return len(got) == len(want) and all(_equal(g, w, tol) for g, w in zip(got, want))
    if isinstance(want, dict) and isinstance(got, dict):
        return got.keys() == want.keys() and all(_equal(got[k], want[k], tol) for k in want)
    return type(got) is type(want) and got == want if isinstance(want, (bool, int)) else got == want

def check_code(content, spec):
    """Return (errors, detail). errors empty means pass."""
    source = extract_python(content)
    if source is None:
        return ['no ```python block'], {}
    errors = precheck(source, spec.get('forbid_names', []), spec.get('require_defs', []), spec.get('max_lines'))
    if errors:
        return errors, {}
    tests = spec['tests']
    result = run_tests(source, tests, spec.get('timeout_s', DEFAULT_TIMEOUT))
    if 'error' in result:
        return [result['error']], {'sandbox': result.get('sandbox')}
    failed = []
    for index, (test, row) in enumerate(zip(tests, result['rows'])):
        if 'raises' in test:
            ok = isinstance(row, dict) and row.get('ok') is False and test['raises'] in row.get('exc', [])
        else:
            ok = isinstance(row, dict) and row.get('ok') is True and _equal(row.get('value'), test['expect'], test.get('tol'))
        if not ok:
            failed.append(index)
    if failed:
        return [f'hidden tests failed: {len(failed)}/{len(tests)} (indices {failed[:8]})'], {'sandbox': result['sandbox'], 'failed': failed}
    return [], {'sandbox': result['sandbox'], 'failed': []}
