"""Execution and commit phases of every 2PC and leveled commit on Citus, one client.
usage: python -I split_phases.py results/split_citus.jsonl
Per method, rows and template: median over the blocks of p50 latency, of the execution phase
(data_p50_ms) and of the commit phase (commit_p50_ms); then the geometric mean over the templates.
The fastest 2PC per route is the one with the lower total."""
import json
import math
import statistics as st
import sys
from collections import defaultdict


def gmean(v):
    return math.exp(sum(math.log(x) for x in v) / len(v))


rows = [json.loads(l) for l in open(sys.argv[1])]
cell = defaultdict(list)
for r in rows:
    cell[(r['method'], r['k'], r['wl'])].append(r)
med = {}
for key, rs in cell.items():
    med[key] = tuple(st.median(r[f] for r in rs) for f in ('p50_ms', 'data_p50_ms', 'commit_p50_ms'))
methods = sorted({k[0] for k in med})
ks = sorted({k[1] for k in med})
wls = sorted({k[2] for k in med})
print('blocks per cell:', sorted({len(v) for v in cell.values()}), 'templates:', wls)
agg = {}
for m in methods:
    for k in ks:
        vs = [med[(m, k, w)] for w in wls if (m, k, w) in med]
        agg[(m, k)] = tuple(gmean([v[i] for v in vs]) for i in range(3))
print('%-6s %4s %8s %8s %8s %6s' % ('method', 'rows', 'p50', 'exec', 'commit', 'exec%'))
for m in methods:
    for k in ks:
        p, d, c = agg[(m, k)]
        print('%-6s %4d %8.2f %8.2f %8.2f %5.0f%%' % (m, k, p, d, c, 100 * d / p))
for route, twopc, lev in (('direct', ('h@d', 'hA@d'), 'Ls@d'), ('coordinator', ('h', 'hA'), 'Ls')):
    for k in ks:
        best = min(twopc, key=lambda m: agg[(m, k)][0])
        bp, bd, bc = agg[(best, k)]
        lp, ld, lc = agg[(lev, k)]
        gap = bp - lp
        print('%-11s %4d rows: fastest 2PC %s %.2f (exec %.2f, commit %.2f) vs %s %.2f (exec %.2f, commit %.2f);'
              ' ratio %.2f; share of the gap in execution %.0f%%'
              % (route, k, best, bp, bd, bc, lev, lp, ld, lc, lp / bp, 100 * (bd - ld) / gap if gap else float('nan')))
