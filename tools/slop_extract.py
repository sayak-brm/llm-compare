"""Extract the model dataset embedded in the slopalytics.com JS bundle into JSON (no code from the bundle is executed).
Usage: python -I slop_extract_2026_11_20.py <bundle.js> <out.json>
The records are a JS array literal of plain objects (backtick strings, !0/!1, unquoted keys, null, numbers)."""
import json, re, sys

src = open(sys.argv[1], encoding='utf-8', errors='replace').read()
i = src.find('{id:`claude-sonnet-5-5`')
if i < 0:
    raise SystemExit('anchor record not found')
# walk back to the array start: the "[" that opens the list of records
depth = 0; j = i
while j > 0:
    j -= 1
    c = src[j]
    if c == ']': depth += 1
    elif c == '[':
        if depth == 0: break
        depth -= 1
start = j
# walk forward to the matching "]"
depth = 0; k = start; in_str = False
while k < len(src):
    c = src[k]
    if c == '`': in_str = not in_str
    elif not in_str:
        if c == '[': depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0: break
    k += 1
lit = src[start:k + 1]

def js_to_json(t):
    out, buf, in_str = [], [], False
    for ch in t:
        if ch == '`':
            if in_str:
                s = ''.join(buf).replace('\\', '\\\\').replace('"', '\\"')
                out.append('"' + s + '"'); buf = []
            in_str = not in_str
        elif in_str: buf.append(ch)
        else: out.append(ch)
    t = ''.join(out)
    t = t.replace('!0', 'true').replace('!1', 'false')
    t = re.sub(r'(?<=[{,])([A-Za-z_$][\w$]*):', r'"\1":', t)
    t = re.sub(r'\bvoid 0\b', 'null', t)
    t = re.sub(r'(?<=[:,\[])(-?)\.(\d)', r'\g<1>0.\2', t)   # JS allows .5, JSON needs 0.5
    return t

data = json.loads(js_to_json(lit))
json.dump(data, open(sys.argv[2], 'w', encoding='utf-8'), indent=1, ensure_ascii=True)
print(len(data), 'records;', 'fields:', sorted(data[0].keys()))
