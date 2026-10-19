"""Entry through the coordinator of Citus and the default mode of Vitess (Section 6.3, run_z1.sh):
median latency with one client relative to the fastest 2PC, per block and size the geometric mean
over the three templates, then the geometric mean and 95% t-interval over the blocks, as in
fig_cost.py.  Citus: Ls (coordinator) and Ls@d (workers) against h, hA, h@d, hA@d; Vitess: ms (default
MULTI mode) and Ls against h, hA.  With 64 clients the ratio is that of throughput to the highest 2PC
throughput; rtt reads the one-client runs behind the 0.5 ms relays, hardening the pinned runs of
run_hardening.sh without and with relays, at 1 and 64 clients.
usage: python -I z1_ratios.py RESULTS_DIR [1 | 64 | rtt | hardening]"""
import collections, json, math, os, statistics, sys

T975 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78}
SIZE = {2: 2, 3: 2, 8: 8, 32: 32, 128: 128}
TWO_PC = {'citus': ('h', 'hA', 'h@d', 'hA@d'), 'vitess': ('h', 'hA')}
LINES = {'citus': ('Ls', 'Ls@d'), 'vitess': ('ms', 'Ls')}


def gm_ci(xs):
    ls = [math.log(x) for x in xs]
    m = statistics.mean(ls)
    if len(ls) < 2:
        return math.exp(m), float('nan'), float('nan')
    h = T975.get(len(ls) - 1, 1.96) * statistics.stdev(ls) / math.sqrt(len(ls))
    return math.exp(m), math.exp(m - h), math.exp(m + h)


def main():
    d = sys.argv[1]
    run = sys.argv[2] if len(sys.argv) > 2 else '1'
    # (label, file pattern, clients, behind relays)
    runs = {'1': [('C=1', 'z1_%s.jsonl', 1, False)], '64': [('C=64', 'z1_64_%s.jsonl', 64, False)],
            'rtt': [('rtt', 'z1_rtt_%s.jsonl', 1, True)],
            'hardening': [('pin C=1', 'hardening_pin_%s.jsonl', 1, False),
                          ('pin C=64', 'hardening_pin_%s.jsonl', 64, False),
                          ('rtt C=1', 'hardening_rtt_%s.jsonl', 1, True),
                          ('rtt C=64', 'hardening_rtt_%s.jsonl', 64, True)]}[run]
    for label, files, C, relayed in runs:
        ratios(d, label, files, C, relayed)


def ratios(d, label, files, C, relayed):
    metric = 'p50_ms' if C == 1 else 'tput'
    best = min if C == 1 else max
    for eng in ('citus', 'vitess'):
        p = os.path.join(d, files % eng)
        if not os.path.exists(p):
            continue
        rows = [json.loads(l) for l in open(p, encoding='utf-8')]
        rows = [r for r in rows if r['C'] == C and bool(r.get('delay_ms')) == relayed]
        by = collections.defaultdict(dict)
        for r in rows:
            by[(r['wl'], SIZE[r['k']], r['rep'])][r['method']] = r[metric]
        for line in LINES[eng]:
            out = []
            for s in (2, 8, 32, 128):
                per = collections.defaultdict(list)
                for (wl, size, blk), ms in by.items():
                    base = [ms[m] for m in TWO_PC[eng] if m in ms]
                    if size != s or line not in ms or len(base) < len(TWO_PC[eng]):
                        continue
                    per[blk].append(ms[line] / best(base))
                v = [math.exp(statistics.mean(map(math.log, x))) for x in per.values() if len(x) == 3]
                if v:
                    g, lo, hi = gm_ci(v)
                    out.append('%d: %.2f [%.2f, %.2f] n=%d' % (s, g, lo, hi, len(v)))
            print('%s %s %s: %s' % (eng, line, label, '; '.join(out)))


if __name__ == '__main__':
    main()
