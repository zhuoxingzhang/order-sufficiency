"""Read-path audit of GitLab (Section 6.2), step 2: draw the sites of the probe for review by hand and
print them with their context.

usage: python -I gitlab_readpath_sample.py GITLAB_SRC PROBE_JSON PER_TEMPLATE SEED OUT_TXT OUT_JSON
  (the audit used PER_TEMPLATE = 6 and SEED = 9, which draws the 56 sites of out/gitlab_readpath_sample.json)
All sites of a template with at most PER_TEMPLATE sites, otherwise PER_TEMPLATE drawn at random.  Every
site is printed with the enclosing method header and the lines around it; gitlab_readpath_review.json
holds the verdicts by hand, keyed by the position of the site in OUT_JSON.
"""
import json, os, random, sys

SRC, PROBE, K, SEED, OUT_TXT, OUT_JSON = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], sys.argv[6]
probe = json.load(open(PROBE, encoding='utf-8'))
by = {}
for r in probe:
    for s in r['sites']:
        by.setdefault(r['parent'], []).append(dict(s, child=r['child'], col=r['col'], assoc=[a['name'] for a in r['assoc']]))
rnd = random.Random(SEED)
sample = []
for p in sorted(by):
    sites = sorted({(s['file'], s['line']): s for s in by[p]}.values(), key=lambda s: (s['file'], s['line']))
    pick = sites if len(sites) <= K else rnd.sample(sites, K)
    for s in sorted(pick, key=lambda s: (s['file'], s['line'])):
        sample.append(dict(s, parent=p, n_sites=len(sites)))
with open(OUT_TXT, 'w', encoding='utf-8') as f:
    for n, s in enumerate(sample):
        lines = open(os.path.join(SRC, s['file']), encoding='utf-8', errors='replace').read().split('\n')
        i = s['line'] - 1
        d = i
        while d > 0 and not lines[d].lstrip().startswith(('def ', 'class ', 'module ', '- ', '= ')):
            d -= 1
        f.write(f"#{n} [{s['parent']} <- {s['child']}.{s['col']} via {s['assoc']}] {s['file']}:{s['line']} "
                f"auto={s['label']} how={s['how']} (template has {s['n_sites']} sites)\n")
        if d < i - 8:
            f.write(f'  {d + 1:5d}| {lines[d]}\n  ...\n')
        for j in range(max(0, i - 8), min(len(lines), i + 7)):
            f.write(f"  {j + 1:5d}{'>' if j == i else '|'} {lines[j]}\n")
        f.write('\n')
json.dump(sample, open(OUT_JSON, 'w', encoding='utf-8'), indent=1)
print(len(sample), 'sites drawn')
