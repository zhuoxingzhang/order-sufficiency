"""Summarize the direction runs (Figure 8): anomalous trials per cell with a Wilson 95% interval.

usage: python -I analyze_direction.py results/direction.jsonl > results/direction.md
"""
import json, math, sys


def wilson(x, n, z=1.96):
    if n == 0:
        return float('nan'), float('nan')
    p = x / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h) * 100, min(1.0, c + h) * 100


rows = [json.loads(l) for l in open(sys.argv[1]) if l.startswith('{')]
protected = {('delete', 'p2c'), ('insert', 'c2p')}
print('| op | reader | protected by the order | writer | think (ms) | anomalous trials | rate % | Wilson 95% % | iterations | unfinished | errors |')
print('|---|---|---|---|---|---|---|---|---|---|---|')
for r in rows:
    x, n = r['anomalous_trials'], r['trials']
    lo, hi = wilson(x, n)
    print(f"| {r['op']} | {r['reader']} | {'yes' if (r['op'], r['reader']) in protected else 'no'} | {r['writer']} "
          f"| {r['think_ms']:g} | {x:,}/{n:,} | {100 * x / n:.1f} | [{lo:.2f}, {hi:.2f}] | {r['iterations']:,} "
          f"| {r['unfinished']} | {r['errors']} |")
