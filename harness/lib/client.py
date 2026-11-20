"""Loopback-only HTTP and streaming SSE. Redirects and environment proxies disabled."""
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('HTTP redirects are forbidden')

def endpoint(base):
    p = urllib.parse.urlsplit(base)
    if (p.scheme != 'http' or p.hostname not in ('127.0.0.1', 'localhost', '::1') or
            p.username or p.password or p.query or p.fragment or p.path.rstrip('/') not in ('', '/v1')):
        raise ValueError('base-url must be http loopback, optional port, and /v1 (no credentials/query/fragment)')
    _ = p.port
    # Pin localhost to numeric loopback; never resolve an arbitrary name.
    authority = ('[::1]' if p.hostname == '::1' else '127.0.0.1') + (f':{p.port}' if p.port else '')
    origin = 'http://'+authority
    return origin+'/v1', origin

class Client:
    def __init__(self, base):
        self.base, self.origin = endpoint(base)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def get(self, path):
        try:
            with self.opener.open(self.origin+path, timeout=5) as response:
                return json.load(response)
        except Exception:
            return None

    def tokenize(self, content):
        """Read-only tokenizer probe; never invokes model generation."""
        try:
            req = urllib.request.Request(self.origin+'/tokenize', data=json.dumps({'content': content}).encode(), headers={'Content-Type': 'application/json'})
            with self.opener.open(req, timeout=10) as response:
                result = json.load(response)
            tokens = result.get('tokens')
            if isinstance(tokens, list):
                return len(tokens)
            count = result.get('count', result.get('n_tokens'))
            return count if type(count) is int and count >= 0 else None
        except Exception:
            return None

    def chat(self, payload, timeout):
        start = time.perf_counter()
        record = {'ttft': None, 'total_time': None, 'prompt_tokens': None, 'completion_tokens': None,
                  'generated_tokens_s': None, 'prompt_tokens_s': None, 'finish_reason': None,
                  'tool_calls': [], 'content': '', 'reasoning_leak': False, 'error': None, 'retries': [], 'skipped_context': False}
        for attempt in range(2):
            try:
                req = urllib.request.Request(self.base+'/chat/completions', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
                content, reasoning, calls, usage, timings = [], [], {}, {}, {}
                with self.opener.open(req, timeout=timeout) as response:
                    # Mock and llama-server both emit SSE; support JSON as a fallback.
                    def events():
                        if 'text/event-stream' not in response.headers.get('Content-Type', ''):
                            yield json.load(response)
                            return
                        chunks = []
                        for raw in response:
                            line = raw.decode('utf-8').rstrip('\r\n')
                            if line.startswith('data:'):
                                chunks.append(line[5:].lstrip())
                            elif not line and chunks:
                                value = '\n'.join(chunks)
                                chunks.clear()
                                if value == '[DONE]':
                                    return
                                yield json.loads(value)
                        if chunks and '\n'.join(chunks) != '[DONE]':
                            yield json.loads('\n'.join(chunks))
                    for event in events():
                        usage.update(event.get('usage') or {})
                        timings.update(event.get('timings') or {})
                        for choice in event.get('choices', []):
                            delta = choice.get('delta', choice.get('message', {}))
                            if record['ttft'] is None and any(delta.get(k) for k in ('content', 'reasoning_content', 'tool_calls')):
                                record['ttft'] = time.perf_counter()-start
                            content.append(delta.get('content') or '')
                            reasoning.append(delta.get('reasoning_content') or '')
                            for tc in delta.get('tool_calls', []):
                                ix = tc.get('index', 0)
                                acc = calls.setdefault(ix, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                                if tc.get('id'):
                                    acc['id'] = tc['id']
                                for key in ('name', 'arguments'):
                                    acc['function'][key] += tc.get('function', {}).get(key, '')
                            if choice.get('finish_reason'):
                                record['finish_reason'] = choice['finish_reason']
                record.update(content=''.join(content), tool_calls=[calls[k] for k in sorted(calls)],
                              prompt_tokens=usage.get('prompt_tokens', timings.get('prompt_n')),
                              completion_tokens=usage.get('completion_tokens', timings.get('predicted_n')))
                record['reasoning_leak'] = bool(''.join(reasoning).strip() or '<think' in record['content'].lower())
                record['prompt_tokens_s'] = timings.get('prompt_per_second')
                record['generated_tokens_s'] = timings.get('predicted_per_second')
                break
            except Exception as exc:
                if isinstance(exc, urllib.error.HTTPError):
                    # urllib's default exception string discards the server's
                    # explanation, including context-overflow errors.
                    body = exc.read(65536).decode('utf-8', errors='replace')
                    exc.close()
                    record['http_status'] = exc.code
                    record['error_body'] = body
                    record['skipped_context'] = exc.code == 400 and bool(re.search(r'context|exceed', body, re.I))
                cause = getattr(exc, 'reason', exc)
                is_reset = isinstance(cause, ConnectionResetError) or getattr(cause, 'errno', None) in (54, 104, 10054)
                if attempt == 0 and is_reset:
                    record['retries'].append({'reason': 'connection reset', 'attempt': 1})
                    record['ttft'] = None
                    continue
                record['error'] = f'{type(exc).__name__}: {exc}'
                break
        record['total_time'] = time.perf_counter()-start
        if record['generated_tokens_s'] is None and record['completion_tokens'] is not None:
            duration = record['total_time']-(record['ttft'] or 0)
            if duration > 0:
                record['generated_tokens_s'] = record['completion_tokens']/duration
        return record
