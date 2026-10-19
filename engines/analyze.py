"""Analysis of the engine runs (Figure 6): per-cell pooled rate with Wilson 95% CI, batch-level t interval and
batch (cluster) bootstrap interval.  usage: python -I analyze.py citus.jsonl vitess.jsonl > summary.md
"""
import json, math, random, sys

Z = 1.959963984540054
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
        10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 19: 2.093}


def wilson(x, n):
    p = x / n
    d = 1 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / d
    h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def batch_t(rates):
    b = len(rates)
    m = sum(rates) / b
    sd = math.sqrt(sum((r - m) ** 2 for r in rates) / (b - 1))
    h = T975[b - 1] * sd / math.sqrt(b)
    return m, sd, max(0.0, m - h), min(1.0, m + h)


def boot(xs, ns, reps=10000, seed=2026):
    rng = random.Random(seed)
    b = len(xs)
    vals = []
    for _ in range(reps):
        idx = [rng.randrange(b) for _ in range(b)]
        vals.append(sum(xs[i] for i in idx) / sum(ns[i] for i in idx))
    vals.sort()
    return vals[int(0.025 * reps)], vals[int(0.975 * reps) - 1]


def load(path):
    rows = []
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if line.startswith('{'):
            rows.append(json.loads(line))
    return rows


CELLS = [
    # (key, engine, cell, extractor -> (x, n), label)
    ('citus_fk_2pc_pf', 'citus', 'e3a', lambda r: (r['orphans'], r['reads_that_saw_child']), 'Citus FK, one 2PC, parent first (slow worker)'),
    ('citus_fk_ordered', 'citus', 'e3o', lambda r: (r['orphans'], r['reads_that_saw_child']), 'Citus FK, two ordered commits (slow worker)'),
    ('citus_fk_2pc_pf_instr', 'citus', 'e3x_pf', lambda r: (r['orphans'], r['reads_that_saw_child']), '[extra] Citus FK, one 2PC, parent first, instrumented copy'),
    ('citus_fk_2pc_cf', 'citus', 'e3x_cf', lambda r: (r['orphans'], r['reads_that_saw_child']), '[extra] Citus FK, one 2PC, child first'),
    ('citus_copy_stmt', 'citus', 'e4', lambda r: (r[0]['mismatched_reads'], r[0]['reads']), 'Citus copy, single-statement reader (uniform)'),
    ('citus_copy_rr_cm', 'citus', 'e4', lambda r: (r[1]['mismatched_reads'], r[1]['reads']), 'Citus copy, RR reader copy then source (uniform)'),
    ('citus_copy_rr_mc', 'citus', 'e4', lambda r: (r[2]['mismatched_reads'], r[2]['reads']), 'Citus copy, RR reader source then copy (uniform)'),
    ('vt_fk_2pc_pf', 'vitess', 'v2', lambda r: (r[0]['orphans'], r[0]['reads_that_saw_child']), 'Vitess FK, one TwoPC, parent first'),
    ('vt_fk_2pc_cf', 'vitess', 'v2swap', lambda r: (r[0]['orphans'], r[0]['reads_that_saw_child']), 'Vitess FK, one TwoPC, child first'),
    ('vt_fk_ordered', 'vitess', 'v2', lambda r: (r[1]['orphans'], r[1]['reads_that_saw_child']), 'Vitess FK, two ordered autocommits'),
    ('vt_copy_scatter', 'vitess', 'v1', lambda r: (r[0]['mismatched_reads'], r[0]['reads']), 'Vitess copy, scatter reader'),
    ('vt_copy_txn', 'vitess', 'v1', lambda r: (r[1]['mismatched_reads'], r[1]['reads']), 'Vitess copy, transactional reader'),
]


def pct(v):
    return f"{100 * v:.3f}" if 0 < v < 0.01 else f"{100 * v:.1f}"


def main():
    rows = []
    for p in sys.argv[1:]:
        rows += load(p)
    out = {}
    print('| cell | batches | pooled x/N | rate % | Wilson 95% CI % | batch mean % (sd) | batch t 95% CI % | batch bootstrap 95% CI % | reported CI % | dispersion | per-batch x/n |')
    print('|---|---|---|---|---|---|---|---|---|---|---|')
    for key, eng, cell, ext, label in CELLS:
        bs = sorted((r for r in rows if r['engine'] == eng and r['cell'] == cell), key=lambda r: r['batch'])
        if not bs:
            continue
        xn = [ext(r['result']) for r in bs]
        xs, ns = [x for x, _ in xn], [n for _, n in xn]
        X, N = sum(xs), sum(ns)
        lo, hi = wilson(X, N)
        m, sd, tlo, thi = batch_t([x / n for x, n in xn])
        blo, bhi = boot(xs, ns)
        # reported CI: span of Wilson (pooled runs) and batch bootstrap (batches as clusters)
        rlo, rhi = min(lo, blo), max(hi, bhi)
        # dispersion: between-batch variance of rates over the binomial variance at the mean batch size
        pq = (X / N) * (1 - X / N)
        disp = (sd * sd) / (pq / (N / len(bs))) if pq > 0 else float('nan')
        out[key] = dict(label=label, batches=len(bs), x=X, n=N, rate=X / N, wilson=[lo, hi], batch_mean=m, batch_sd=sd,
                        batch_t=[tlo, thi], batch_boot=[blo, bhi], reported=[rlo, rhi], dispersion=disp, per_batch=xn)
        print(f"| {label} | {len(bs)} | {X:,}/{N:,} | {pct(X / N)} | [{pct(lo)}, {pct(hi)}] | {pct(m)} ({pct(sd)}) | "
              f"[{pct(tlo)}, {pct(thi)}] | [{pct(blo)}, {pct(bhi)}] | [{pct(rlo)}, {pct(rhi)}] | {disp:.1f} | "
              f"{', '.join(f'{x}/{n}' for x, n in xn)} |")
    # Citus FK placement breakdown from the instrumented runs
    for cell in ('e3x_pf', 'e3x_cf'):
        agg = {}
        for r in rows:
            if r['engine'] == 'citus' and r['cell'] == cell:
                for k, v in r['result']['by_parent_child_node'].items():
                    a = agg.setdefault(k, [0, 0, 0])
                    for i in range(3):
                        a[i] += v[i]
        if agg:
            print(f"\n{cell} by parent->child worker (runs, reads that saw child, orphans): "
                  + '; '.join(f"{k}: {v[0]}, {v[1]}, {v[2]}" for k, v in sorted(agg.items())))
            out[cell + '_by_node'] = agg
            for name, pred in (('parent on slow worker 29701', lambda k: k.startswith('29701->')),
                               ('parent on a fast worker', lambda k: not k.startswith('29701->'))):
                x = sum(v[2] for k, v in agg.items() if pred(k))
                n = sum(v[1] for k, v in agg.items() if pred(k))
                lo, hi = wilson(x, n)
                print(f"  {name}: {x:,}/{n:,} = {pct(x / n)}% [{pct(lo)}, {pct(hi)}]")
                out[f"{cell}_{name}"] = dict(x=x, n=n, wilson=[lo, hi])
    json.dump(out, open('summary.json', 'w'), indent=1)


if __name__ == '__main__':
    main()
