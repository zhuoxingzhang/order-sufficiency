"""Grace-period runs behind Figure 7: does a pause between two ordered commits protect readers
whose reads finish within the pause (Proposition 5.9)?

Claim.  Let the writer send the second commit only DELTA after the first commit has returned.  A
reader whose two reads lie less than DELTA apart then never sees the child without the parent,
whatever its read direction: if it saw the second write but not the first, the snapshots of its
reads would lie more than DELTA apart.

Set-up as in direction.py: a parent row and a child row that references it lie on different Citus
workers; the writer inserts both rows (parent first) or deletes both (child first) by two
autocommits, sleeping DELTA between the return of the first and the start of the second.  The
reader polls with two single-row reads per iteration, sleeping THINK between them, in the direction
that write order does not protect: child then parent (c2p) for deletions, parent then child (p2c)
for insertions.  Every iteration records its read span, from the start of the first read to the
end of the second, an upper bound of the distance between the two snapshots.
The cell reports anomalous trials and iterations, and among the anomalous iterations the smallest
span and the number whose span is at most DELTA (which the claim says is zero).

usage: python -I grace.py <op> <delta_ms> <think_ms> <trials> <seed>
"""
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exp_citus as E  # noqa: E402

q, conn, node_of, run_pair = E.q, E.conn, E.node_of, E.run_pair
DEADLINE = 0.25


def cell(op, delta, think, trials):
    c = conn()
    q(c, "DROP TABLE IF EXISTS parent; DROP TABLE IF EXISTS child")
    q(c, "CREATE TABLE parent (pid int PRIMARY KEY)")
    q(c, "CREATE TABLE child (cid int PRIMARY KEY, pid int)")
    q(c, "SELECT create_distributed_table('parent', 'pid')")
    q(c, "SELECT create_distributed_table('child', 'cid', colocate_with => 'none')")
    cw, cr = conn(), conn()
    pairs = []
    k = 1
    while len(pairs) < 64:
        if node_of(c, 'parent', k) != node_of(c, 'child', 10000 + k):
            pairs.append((k, 10000 + k))
        k += 1
    P_INS, C_INS = "INSERT INTO parent VALUES (%s)", "INSERT INTO child VALUES (%s, %s)"
    P_DEL, C_DEL = "DELETE FROM parent WHERE pid = %s", "DELETE FROM child WHERE cid = %s"
    reader = 'c2p' if op == 'delete' else 'p2c'
    out = {'op': op, 'reader': reader, 'delta_ms': delta * 1000, 'think_ms': think * 1000, 'trials': trials,
           'anomalous_trials': 0, 'iterations': 0, 'anomalous_iterations': 0, 'unfinished': 0, 'errors': 0,
           'min_anomalous_span_ms': None, 'anomalous_span_le_delta': 0, 'iterations_span_le_delta': 0,
           'gap_ms_min': None, 'span_ms_median': None}
    spans, gaps = [], []
    for t in range(trials):
        p, ch = pairs[t % len(pairs)]
        q(c, C_DEL, (ch,))
        q(c, P_DEL, (p,))
        if op == 'delete':
            q(c, P_INS, (p,))
            q(c, C_INS, (ch, p))
            first, second = (C_DEL, (ch,)), (P_DEL, (p,))
        else:
            first, second = (P_INS, (p,)), (C_INS, (ch, p))

        def w():
            cur = cw.cursor()
            cur.execute(*first)
            t_ack = time.perf_counter()
            if delta:
                time.sleep(delta)
            t_send = time.perf_counter()
            cur.execute(*second)
            gaps.append(t_send - t_ack)
        st = {'it': 0, 'bad': 0, 'done': False, 'bad_spans': [], 'le': 0}

        def r():
            deadline = time.time() + DEADLINE
            while time.time() < deadline:
                t1 = time.perf_counter()
                if reader == 'c2p':
                    has_c = bool(q(cr, "SELECT 1 FROM child WHERE cid = %s", (ch,)))
                    if think:
                        time.sleep(think)
                    has_p = bool(q(cr, "SELECT 1 FROM parent WHERE pid = %s", (p,)))
                else:
                    has_p = bool(q(cr, "SELECT 1 FROM parent WHERE pid = %s", (p,)))
                    if think:
                        time.sleep(think)
                    has_c = bool(q(cr, "SELECT 1 FROM child WHERE cid = %s", (ch,)))
                span = time.perf_counter() - t1
                spans.append(span)
                st['it'] += 1
                st['le'] += span <= delta
                if has_c and not has_p:
                    st['bad'] += 1
                    st['bad_spans'].append(span)
                if (op == 'insert' and has_c and has_p) or (op == 'delete' and not has_c and not has_p):
                    st['done'] = True
                    return
        out['errors'] += len(run_pair(r, w, random.uniform(0, 0.002)))
        out['iterations'] += st['it']
        out['iterations_span_le_delta'] += st['le']
        out['anomalous_iterations'] += st['bad']
        out['anomalous_trials'] += st['bad'] > 0
        out['unfinished'] += not st['done']
        for s in st['bad_spans']:
            if out['min_anomalous_span_ms'] is None or s * 1000 < out['min_anomalous_span_ms']:
                out['min_anomalous_span_ms'] = round(s * 1000, 3)
            out['anomalous_span_le_delta'] += s <= delta
    spans.sort()
    out['span_ms_median'] = round(spans[len(spans) // 2] * 1000, 3) if spans else None
    out['gap_ms_min'] = round(min(gaps) * 1000, 3) if gaps else None
    return out


if __name__ == '__main__':
    op, delta, think = sys.argv[1], float(sys.argv[2]) / 1000, float(sys.argv[3]) / 1000
    trials, seed = int(sys.argv[4]), int(sys.argv[5])
    random.seed(seed)
    t0, l0 = time.time(), os.getloadavg()[0]
    r = cell(op, delta, think, trials)
    r.update({'seed': seed, 'secs': round(time.time() - t0, 1), 'loadavg_start': l0})
    print(json.dumps(r), flush=True)
