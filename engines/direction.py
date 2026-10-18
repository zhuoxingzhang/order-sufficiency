"""Direction runs behind Figure 8: does the required write order protect a reader in both read directions?

A parent row and a child row that references it live on different Citus workers.  The writer
inserts both rows or deletes both, in one of three ways:
  ordered   two autocommits in the required order (insert parent first, delete child first)
  reversed  two autocommits in the opposite order (Sentry deletes the parent first)
  atomic    one transaction over both workers (Citus 2PC), statements in the required order
  ordered_gN  ordered, with N ms between the two autocommits (a relay that applies the second
            write later, as an outbox would)
The reader polls with two single-row reads per iteration and sleeps THINK seconds between them:
  c2p  read the child, then the parent it references (follow the foreign key)
  p2c  read the parent, then its child (list the children of a parent)
An iteration is an anomaly when it sees the child but not the parent.  The theory predicts
zero anomalies for ordered commits exactly when the reader reads the later-written side first:
p2c for deletion and c2p for insertion.  The other direction is protected by no order; the
order only narrows the window to the gap between the two reads.

usage: python -I direction.py <op> <writer> <reader> <think_ms> <trials> <seed>
"""
import json, os, random, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exp_citus as E  # noqa: E402

q, conn, node_of, run_pair = E.q, E.conn, E.node_of, E.run_pair
DEADLINE = 0.2


def cell(op, writer, reader, think, trials):
    c = conn()
    q(c, "DROP TABLE IF EXISTS parent; DROP TABLE IF EXISTS child")
    q(c, "CREATE TABLE parent (pid int PRIMARY KEY)")
    q(c, "CREATE TABLE child (cid int PRIMARY KEY, pid int)")
    q(c, "SELECT create_distributed_table('parent', 'pid')")
    q(c, "SELECT create_distributed_table('child', 'cid', colocate_with => 'none')")
    cw, cr = conn(), conn()
    pairs = []
    k = 1
    while len(pairs) < 64:  # parent and child keys on different workers
        if node_of(c, 'parent', k) != node_of(c, 'child', 10000 + k):
            pairs.append((k, 10000 + k))
        k += 1
    P_INS, C_INS = "INSERT INTO parent VALUES (%s)", "INSERT INTO child VALUES (%s, %s)"
    P_DEL, C_DEL = "DELETE FROM parent WHERE pid = %s", "DELETE FROM child WHERE cid = %s"
    gap = float(writer.split('_g')[1]) / 1000 if writer.startswith('ordered_g') else 0.0
    out = {'op': op, 'writer': writer, 'reader': reader, 'think_ms': think * 1000, 'trials': trials,
           'anomalous_trials': 0, 'iterations': 0, 'anomalous_iterations': 0, 'unfinished': 0, 'errors': 0}
    for t in range(trials):
        p, ch = pairs[t % len(pairs)]
        q(c, C_DEL, (ch,))
        q(c, P_DEL, (p,))
        if op == 'delete':
            q(c, P_INS, (p,))
            q(c, C_INS, (ch, p))
        if op == 'insert':
            first, second = (P_INS, (p,)), (C_INS, (ch, p))
        else:
            first, second = (C_DEL, (ch,)), (P_DEL, (p,))
        if writer == 'reversed':
            first, second = second, first

        def w():
            cur = cw.cursor()
            if writer == 'atomic':
                cur.execute("BEGIN")
            cur.execute(*first)
            if gap:
                time.sleep(gap)
            cur.execute(*second)
            if writer == 'atomic':
                cur.execute("COMMIT")
        st = {'it': 0, 'bad': 0, 'done': False}

        def r():
            deadline = time.time() + DEADLINE
            while time.time() < deadline:
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
                st['it'] += 1
                st['bad'] += has_c and not has_p
                if (op == 'insert' and has_c and has_p) or (op == 'delete' and not has_c and not has_p):
                    st['done'] = True
                    return
        # the reader starts first; the writer starts 0-2 ms later, so every trial sees the old state
        out['errors'] += len(run_pair(r, w, random.uniform(0, 0.002)))
        out['iterations'] += st['it']
        out['anomalous_iterations'] += st['bad']
        out['anomalous_trials'] += st['bad'] > 0
        out['unfinished'] += not st['done']
    return out


if __name__ == '__main__':
    op, writer, reader = sys.argv[1], sys.argv[2], sys.argv[3]
    think, trials, seed = float(sys.argv[4]) / 1000, int(sys.argv[5]), int(sys.argv[6])
    random.seed(seed)
    t0, l0 = time.time(), os.getloadavg()[0]
    r = cell(op, writer, reader, think, trials)
    r.update({'seed': seed, 'secs': round(time.time() - t0, 1), 'loadavg_start': l0})
    print(json.dumps(r), flush=True)
