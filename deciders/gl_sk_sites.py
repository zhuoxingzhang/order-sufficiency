"""The sites that still modify a declared sharding key of GitLab after gl_sites.py, for reading by hand
(file, line, method and operation; x4_shardk.py, scenario frozen).
Usage: python -I gl_sk_sites.py"""
import collections, json, os, sys

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gitlab_schema as G  # noqa: E402
from gl_sites import is_init  # noqa: E402

g = G.load()
T = g['tables']
d = json.load(open(os.path.join(HERE, 'data', 'gitlab_mutable.json'), encoding='utf-8'))
f = json.load(open(os.path.join(HERE, 'data', 'gitlab_mutable_filtered.json'), encoding='utf-8'))
sk = {(t, c) for t, v in T.items() for c in v.get('sharding_key', {})}
S = collections.defaultdict(list)
for s in d['sites']:
    if is_init(s):
        continue
    for c in s['cols']:
        if (s['table'], c) in sk:
            S[(s['table'], c)].append(s)
for (t, c), v in sorted(S.items()):
    print(f'== {t}.{c} ({len(v)})')
    for s in v[:6]:
        print(f"   {s['file']}:{s['line']} [{s['meth']}] {s['op']}")
print('not in sites (sample):', list(f['not_in_sites'].items())[:15])
