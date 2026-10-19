"""Summarize the delay sweep (run_delay.sh): orphan share per cell and injected commit delay, split
by whether the parent lies on the delayed worker 29701.

usage: python -I analyze_delay.py results/citus_delay.jsonl > results/citus_delay.md
"""
import collections, json, sys

agg = collections.defaultdict(lambda: [0, 0, 0, 0, 0])   # (cell, delay) -> trials, orphans, trials and orphans with the parent on 29701, batches
for l in open(sys.argv[1], encoding='utf-8'):
    if not l.startswith('{'):
        continue
    r = json.loads(l)
    a = agg[(r['cell'], r['commit_delay_us'])]
    a[4] += 1
    # e3o (ordered autocommits) does not record the nodes of its pairs
    by = r['result'].get('by_parent_child_node') or {'?->?': [r['result']['trials'], 0, r['result']['orphans']]}
    for pc, (n, saw, orph) in by.items():
        a[0] += n
        a[1] += orph
        if pc.split('->')[0] == '29701':
            a[2] += n
            a[3] += orph
print('| cell | commit delay (ms) | batches | orphans | % | parent on 29701: % | other parents: orphans |')
print('|---|---|---|---|---|---|---|')
for (cell, d), (n, o, nd, od, b) in sorted(agg.items()):
    print(f'| {cell} | {d / 1000:g} | {b} | {o:,}/{n:,} | {100 * o / n:.1f} | {100 * od / max(nd, 1):.1f} '
          f'| {o - od:,}/{n - nd:,} |')
