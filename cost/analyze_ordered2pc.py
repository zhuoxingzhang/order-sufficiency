"""Ordered two-phase commit (run_ordered2pc.sh): per configuration and cell, p50 latency and throughput of each method relative to the
fastest one 2PC (h, hA, h@d, hA@d) of the same repetition, geometric mean over the templates and
the repetitions; and the paired ratios Ps/Lt (cost of the prepare) and Lt/Ls (shard tables vs
distributed table names).

usage: python -I analyze_ordered2pc.py results/ordered2pc_citus.jsonl results/ordered2pc64_citus.jsonl
       results/ordered2pc_rtt_citus.jsonl > results/ordered2pc.md
"""
import collections
import json
import math
import sys

TWOPC = ('h', 'hA', 'h@d', 'hA@d')
rows = [json.loads(l) for f in sys.argv[1:] for l in open(f) if l.strip()]
by = collections.defaultdict(dict)
for r in rows:
    by[(r['tag'], r['wl'], r['k'], r['C'], r['rep'])][r['method']] = r


def size(k):
    return '2-3' if k in (2, 3) else str(k)


def gm(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else float('nan')


lat = collections.defaultdict(list)
tput = collections.defaultdict(list)
pair = collections.defaultdict(list)
missing = collections.Counter()
for (tag, wl, k, C, rep), ms in sorted(by.items()):
    base = [m for m in TWOPC if m in ms]
    if not base:
        continue
    best_lat = min(ms[m]['p50_ms'] for m in base)
    best_tp = max(ms[m]['tput'] for m in base)
    for m, r in ms.items():
        lat[(tag, size(k), m)].append(r['p50_ms'] / best_lat)
        tput[(tag, size(k), m)].append(r['tput'] / best_tp)
    for a, b in (('Ps@d', 'Lt@d'), ('Lt@d', 'Ls@d'), ('Ps@d', 'Ls@d')):
        if a in ms and b in ms:
            pair[(tag, size(k), a + '/' + b, 'lat')].append(ms[a]['p50_ms'] / ms[b]['p50_ms'])
            pair[(tag, size(k), a + '/' + b, 'tput')].append(ms[a]['tput'] / ms[b]['tput'])
        else:
            missing[(tag, wl, size(k), a if a not in ms else b)] += 1

tags = sorted({t for t, _, _ in lat})
methods = ['h', 'hA', 'h@d', 'hA@d', 'Ls@d', 'Lt@d', 'Ps@d']
for tag in tags:
    for what, d in (('p50 relative to the fastest 2PC', lat), ('throughput relative to the fastest 2PC', tput)):
        print(f'\n## {tag}: {what} (geometric mean, n)')
        print('| size | ' + ' | '.join(methods) + ' |')
        print('|---' * (len(methods) + 1) + '|')
        for s in ('2-3', '8', '32', '128'):
            cells = []
            for m in methods:
                xs = d.get((tag, s, m), [])
                cells.append(f'{gm(xs):.2f} ({len(xs)})' if xs else '-')
            print(f'| {s} | ' + ' | '.join(cells) + ' |')
    print(f'\n## {tag}: paired ratios (geometric mean over templates and repetitions, n)')
    print('| size | Ps/Lt p50 | Ps/Lt tput | Lt/Ls p50 | Lt/Ls tput | Ps/Ls p50 | Ps/Ls tput |')
    print('|---|---|---|---|---|---|---|')
    for s in ('2-3', '8', '32', '128'):
        cells = []
        for pr in ('Ps@d/Lt@d', 'Lt@d/Ls@d', 'Ps@d/Ls@d'):
            for q in ('lat', 'tput'):
                xs = pair.get((tag, s, pr, q), [])
                cells.append(f'{gm(xs):.2f} ({len(xs)})' if xs else '-')
        print(f'| {s} | ' + ' | '.join(cells) + ' |')
print('\nmissing pairs:', dict(missing))
