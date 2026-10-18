"""Read-path audit (Section 6.2), step 2: how does each child -> parent read site handle a missing parent?

usage: python -I readpath_tolerance.py SENTRY_SRC PROBE_JSON EXTRA_JSON OUT_JSON [OVERRIDE_JSON]
  PROBE_JSON  output of readpath_probe.py (candidate sites per hybrid-cloud foreign key)
  EXTRA_JSON  sites found by hand: [{"template", "parent", "child", "col", "file", "line"}]

A first pass labels every site from the code that follows the parent fetch:
  T  tolerant: a guard on the fetched value returns, continues or answers 404 / empty
  E  explicit error: the guard raises, or the value is asserted
  B  batch fetch: the parent comes back in a list or dict, so a missing parent is skipped
     unless the caller indexes the result directly (then U)
  U  unguarded: no guard found within the window; checked by hand
Every E and U site is then read by hand, and the verdict goes into OVERRIDE.
"""
import json, re, sys

SRC, PROBE, EXTRA, OUT = sys.argv[1:5]
AFTER = 14

# hand verdicts after reading the code (optional 5th argument): {"file:line": [label, note]}
OVERRIDE = {}
if len(sys.argv) > 5:
    OVERRIDE = {k: tuple(v) for k, v in json.load(open(sys.argv[5], encoding='utf-8')).items()}

BATCH = re.compile(r'\.(get_many\w*|serialize_many|get_many_by_id|get_integrations|organization_contexts|installations_for_organization)\(')
ASSIGN = re.compile(r'^\s*(?:(\w+)(?:\s*:\s*[^=]+)?|(\w+),\s*\w+)\s*=\s*')


def label(lines, i):
    call = lines[i]
    m = ASSIGN.match(call)
    var = (m.group(1) or m.group(2)) if m else None
    if var is None and i > 0:  # `x = (` on the line before, or a comprehension `for u in svc(...)`
        m = ASSIGN.match(lines[i - 1])
        var = (m.group(1) or m.group(2)) if m else None
    window = lines[i + 1:i + 1 + AFTER]
    if BATCH.search(call):
        if var and any(re.search(r'\b' + var + r'\[', w) and not re.search(r'\b' + var + r'\.get\(', w) for w in window):
            return 'U', 'batch result indexed directly'
        return 'B', ''
    if not var:
        return 'U', 'no assignment'
    for j, w in enumerate(window):
        if re.search(r'assert\s+' + var + r'\b', w):
            return 'E', 'assert'
        if re.search(r'if\s+(not\s+' + var + r'\b|' + var + r'\s+is\s+None|' + var + r'\s*:|' + var + r'\s+and\b)', w):
            body = ' '.join(window[j:j + 4])
            if re.search(r'\braise\b', body):
                return 'E', 'raise on missing'
            return 'T', ''
        if re.search(r'\b' + var + r'\s+is\s+(not\s+)?None', w) or re.search(r'\bif\s+' + var + r'\b', w):
            return 'T', ''
    return 'U', 'no guard within window'


probe = json.load(open(PROBE, encoding='utf-8'))
extra = json.load(open(EXTRA, encoding='utf-8'))
sites = []
for e in probe['edges']:
    for h in e['hits']:
        sites.append({'parent': e['parent'], 'child': e['child'], 'col': e['col'], 'file': h['file'],
                      'line': h['line'], 'src': 'probe'})
for x in extra:
    sites.append(dict(x, src='hand'))
cache = {}
for s in sites:
    lines = cache.setdefault(s['file'], open(f"{SRC}/{s['file']}", encoding='utf-8').read().split('\n'))
    s['auto'], s['why'] = label(lines, s['line'] - 1)
    key = f"{s['file']}:{s['line']}"
    s['label'], s['note'] = OVERRIDE.get(key, (s['auto'], s['why']))
    s['text'] = lines[s['line'] - 1].strip()[:140]
json.dump(sites, open(OUT, 'w'), indent=1)
for s in sites:
    if s['label'] in ('E', 'U'):
        print(f"{s['label']} {s['parent']:24s} {s['child']}.{s['col']}  {s['file']}:{s['line']}  {s['note']}")
