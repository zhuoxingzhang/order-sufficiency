"""Numbers of the fault and fence runs (Section 6.3) from results/citus_fault.jsonl and
results/citus_fence.jsonl.

Fault runs: method a = one two-phase commit through the coordinator, L = leveled commits with an
outbox sent to the workers.  Per method, summed over the blocks: lost commit messages (fired),
orphan reads, orphans left when the writes ended and the seconds until none was left, and for L the
updates retried by the client and completed by the relay.
Fence runs: merges that left an orphan (with a Wilson 95% interval) and the median latency of
merges and additions (mean over the blocks with a 95% t-interval), for one two-phase commit (a),
leveled commits (L) and both with the lock (af, Lf).
usage: python -I summarize_fault_fence.py [RESULTS_DIR]"""
import json, math, os, statistics, sys

R = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
T975 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57}


def rows(name):
    return [json.loads(l) for l in open(os.path.join(R, name))]


def wilson(k, n, z=1.96):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * (c - h), 100 * (c + h)


def mean_ci(xs):
    m = statistics.mean(xs)
    if len(xs) < 2:
        return m, float('nan'), float('nan')
    h = T975.get(len(xs) - 1, 1.96) * statistics.stdev(xs) / math.sqrt(len(xs))
    return m, m - h, m + h


print('fault runs (citus_fault.jsonl)')
fault = rows('citus_fault.jsonl')
for m in ('a', 'L'):
    rs = [r for r in fault if r['method'] == m]
    if not rs:
        continue
    st = {}
    for r in rs:
        for k, v in r['status'].items():
            st[k] = st.get(k, 0) + v
    print(f"  {m}: blocks {len(rs)}, lost commit messages {sum(r['fired'] for r in rs)}, "
          f"orphan reads {sum(r['orphan_reads'] for r in rs)} of {sum(r['reads'] for r in rs)}, "
          f"orphans when the writes ended {sum(r['orphans_after_writes'] for r in rs)}, "
          f"seconds until none left {[r['secs_until_no_orphan'] for r in rs]}, "
          f"retried {st.get('retried', 0)}, relayed {sum(r['relayed'] for r in rs)}, "
          f"incomplete updates {sum(r['updates'] - r['complete'] for r in rs)}")

print('fence runs (citus_fence.jsonl)')
fence = rows('citus_fence.jsonl')
for m in ('a', 'L', 'af', 'Lf'):
    rs = [r for r in fence if r['method'] == m]
    if not rs:
        continue
    k = sum(r['merges_leaving_orphans'] for r in rs)
    n = sum(r['merges']['merges'] for r in rs)
    lo, hi = wilson(k, n)
    share = mean_ci([100 * r['merges_leaving_orphans'] / r['merges']['merges'] for r in rs])
    add = mean_ci([r['add_p50_ms'] for r in rs])
    mer = mean_ci([r['merge_p50_ms'] for r in rs])
    print(f"  {m}: blocks {len(rs)}, merges leaving orphans {k:,} of {n:,} ({100 * k / n:.1f}%, Wilson [{lo:.1f}, {hi:.1f}]; "
          f"over blocks {share[0]:.1f}% [{share[1]:.1f}, {share[2]:.1f}]), "
          f"merge p50 {mer[0]:.1f} ms, add p50 {add[0]:.2f} [{add[1]:.2f}, {add[2]:.2f}] ms")
