"""Numbers for Figure 5 from the cost runs (run_cost.sh): results/final_*.jsonl with one
client, final64_*.jsonl with 64 clients, final_rtt_citus.jsonl with one client behind relays that
add 1 ms per round trip.
usage: python -I fig_cost.py RESULTS_DIR

Baseline: the fastest one two-phase commit in each block and cell, over row and set statements and,
on Citus, over the coordinator and a worker as entry point (h, hA, h@d, hA@d; Vitess: h, hA).
One commit per row likewise takes its faster entry point (b, b@d).  Leveled commits (set statements
per shard, on Citus sent to the worker of every transaction): L = Ls@d, with an outbox Lo = Los@d,
with an eager first round E = Es@d (Vitess: Ls, Los, Es).  Ratios are taken per block and cell,
averaged geometrically over the three templates, and the 95% t-interval runs over the blocks."""
import collections, json, math, os, statistics, sys

T975 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57}
SIZE = {2: 2, 3: 2, 8: 8, 32: 32, 128: 128}   # ins and del at 2 rows, rep at 3: one size class
TWO_PC = {'citus': ('h', 'hA', 'h@d', 'hA@d'), 'vitess': ('h', 'hA')}
LINES = {'citus': {'b': ('b', 'b@d'), 'L': ('Ls@d',), 'Lo': ('Los@d',), 'E': ('Es@d',)},
         'vitess': {'b': ('b',), 'L': ('Ls',), 'Lo': ('Los',), 'E': ('Es',)}}


def load(d, name):
    p = os.path.join(d, name)
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def gm_ci(xs):
    ls = [math.log(x) for x in xs]
    m = statistics.mean(ls)
    if len(ls) < 2:
        return math.exp(m), float('nan'), float('nan')
    h = T975.get(len(ls) - 1, 1.96) * statistics.stdev(ls) / math.sqrt(len(ls))
    return math.exp(m), math.exp(m - h), math.exp(m + h)


def series(rows, engine, line, metric):
    """[(size, gm, lo, hi)] for one line; line is a key of LINES[engine]."""
    best = max if metric == 'tput' else min
    by = collections.defaultdict(dict)
    for r in rows:
        by[(r['wl'], SIZE[r['k']], r['rep'])][r['method']] = r[metric]
    out = []
    for s in (2, 8, 32, 128):
        per = collections.defaultdict(list)
        for (wl, size, blk), ms in by.items():
            if size != s:
                continue
            base = [ms[m] for m in TWO_PC[engine] if m in ms]
            mine = [ms[m] for m in LINES[engine][line] if m in ms]
            if len(base) < len(TWO_PC[engine]) or not mine:
                continue
            per[blk].append(best(mine) / best(base))
        v = [math.exp(statistics.mean(map(math.log, x))) for x in per.values() if len(x) == 3]
        if v:
            out.append((s,) + gm_ci(v))
    return out


def fastest_2pc(rows, engine, metric):
    """How often each 2PC variant is the fastest, per size."""
    best = max if metric == 'tput' else min
    by = collections.defaultdict(dict)
    for r in rows:
        by[(r['wl'], SIZE[r['k']], r['rep'])][r['method']] = r[metric]
    cnt = collections.defaultdict(collections.Counter)
    for (wl, s, blk), ms in by.items():
        base = {m: ms[m] for m in TWO_PC[engine] if m in ms}
        if base:
            cnt[s][best(base, key=base.get)] += 1
    return {s: dict(c) for s, c in sorted(cnt.items())}


FILES = [('citus', 'final_citus.jsonl', 1, 'p50_ms', 'Citus latency, one client'),
         ('vitess', 'final_vitess.jsonl', 1, 'p50_ms', 'Vitess latency, one client'),
         ('citus', 'final64_citus.jsonl', 64, 'tput', 'Citus throughput, 64 clients'),
         ('vitess', 'final64_vitess.jsonl', 64, 'tput', 'Vitess throughput, 64 clients'),
         ('citus', 'final_rtt_citus.jsonl', 1, 'p50_ms', 'Citus latency, 1 ms per round trip')]


def main():
    d = sys.argv[1]
    for engine, name, C, metric, title in FILES:
        rows = [r for r in load(d, name) if r['C'] == C]
        if not rows:
            continue
        print(f'% {title}: {len(rows)} runs, aborts {sum(r["aborts"] for r in rows)}, '
              f'exhausted {sum(1 for r in rows if r["exhausted"])}')
        for line in LINES[engine]:
            pts = series(rows, engine, line, metric)
            print(f'%   {line}: ' + '; '.join(f'{s}: {g:.2f} [{lo:.2f}, {hi:.2f}]' for s, g, lo, hi in pts))
        print(f'%   fastest 2PC: {fastest_2pc(rows, engine, metric)}')
        ab = collections.defaultdict(list)
        for r in rows:
            ab[(r['wl'], r['method'], SIZE[r['k']])].append(r[metric])
        for wl in ('ins', 'del', 'rep'):
            for m in sorted({m for (w, m, _) in ab if w == wl}):
                print(f'%   {wl} {m}: ' + ' '.join(f'{statistics.mean(ab[(wl, m, s)]):.2f}' if (wl, m, s) in ab else '-'
                                                for s in (2, 8, 32, 128)))


if __name__ == '__main__':
    main()
