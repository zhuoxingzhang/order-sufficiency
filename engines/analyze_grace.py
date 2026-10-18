"""Summarize the grace-period runs (Figure 8b): anomalous trials per cell with a Wilson 95% interval,
and the check of Proposition 5.9 that no anomalous read spans at most the pause between the commits.

usage: python -I analyze_grace.py results/grace.jsonl > results/grace.md
"""
import json
import math
import sys


def wilson(x, n, z=1.96):
    p = x / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h) * 100, min(1.0, c + h) * 100


rows = [json.loads(l) for l in open(sys.argv[1]) if l.startswith('{')]
print('| op | reader | pause between commits (ms) | pause between reads (ms) | anomalous trials | rate % '
      '| Wilson 95% % | iterations | anomalous iterations | shortest anomalous span (ms) '
      '| anomalous iterations with span <= pause | iterations with span <= pause | median span (ms) '
      '| shortest pause measured (ms) | unfinished | errors |')
print('|' + '---|' * 16)
for r in rows:
    x, n = r['anomalous_trials'], r['trials']
    lo, hi = wilson(x, n)
    m = r['min_anomalous_span_ms']
    print(f"| {r['op']} | {r['reader']} | {r['delta_ms']:g} | {r['think_ms']:g} | {x:,}/{n:,} | {100 * x / n:.1f} "
          f"| [{lo:.2f}, {hi:.2f}] | {r['iterations']:,} | {r['anomalous_iterations']:,} "
          f"| {'-' if m is None else f'{m:.3f}'} | {r['anomalous_span_le_delta']} | {r['iterations_span_le_delta']:,} "
          f"| {r['span_ms_median']:.3f} | {r['gap_ms_min']:.3f} | {r['unfinished']} | {r['errors']} |")
viol = sum(r['anomalous_span_le_delta'] for r in rows)
fast = sum(r['iterations_span_le_delta'] for r in rows if r['delta_ms'] > 0)
print()
print(f"{len(rows)} cells; anomalous iterations whose span is at most the pause between the commits: {viol} "
      f"(of {fast:,} iterations with such a span and a positive pause).")
