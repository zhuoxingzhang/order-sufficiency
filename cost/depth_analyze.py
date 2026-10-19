"""Summarize the rounds sweep of Figure 5f: per path length k (= rounds of leveled commits), the median latency of
every method relative to the fastest one 2PC of the same block, as a geometric mean over the blocks with
its range, plus the absolute medians.

usage: python -I depth_analyze.py results/depth_citus.jsonl [more.jsonl ...] > results/depth.md
"""
import json, math, sys
from collections import defaultdict

TWO = {'citus': ['h', 'hA', 'h@d', 'hA@d'], 'vitess': ['h', 'hA']}
for path in sys.argv[1:]:
    rows = [json.loads(x) for x in open(path) if x.strip()]
    if not rows:
        continue
    eng = rows[0]['engine']
    cell = defaultdict(dict)
    for r in rows:
        if r.get('p50_ms') is not None:
            cell[(r['k'], r['rep'])][r['method']] = r
    methods = sorted({m for c in cell.values() for m in c} - set(TWO[eng]))
    ks = sorted({k for k, _ in cell})
    print(f'## {path}\n')
    print('| k = rounds | fastest 2PC p50 (ms) | ' + ' | '.join(f'{m} p50 (ms) | {m} / 2PC' for m in methods) + ' |')
    print('|---' * (2 + 2 * len(methods)) + '|')
    for k in ks:
        blocks = sorted(b for kk, b in cell if kk == k)
        base = [min(cell[(k, b)][x]['p50_ms'] for x in TWO[eng] if x in cell[(k, b)]) for b in blocks]
        line = f'| {k} | {sum(base) / len(base):.2f} |'
        for m in methods:
            rs = [cell[(k, b)][m]['p50_ms'] / base[i] for i, b in enumerate(blocks) if m in cell[(k, b)]]
            ab = [cell[(k, b)][m]['p50_ms'] for b in blocks if m in cell[(k, b)]]
            if not rs:
                line += ' – | – |'
                continue
            g = math.exp(sum(math.log(x) for x in rs) / len(rs))
            line += f' {sum(ab) / len(ab):.2f} | {g:.2f} [{min(rs):.2f}, {max(rs):.2f}] |'
        print(line)
    print()
