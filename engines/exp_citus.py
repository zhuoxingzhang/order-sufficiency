"""Anomaly reproduction on a 1+3 node Citus 14.2 cluster (coordinator port 29700).

E3  foreign key, class "order suffices": parent/child on different workers;
    writer atomic (one 2PC txn) vs ordered (parent txn, then child txn); reader reads child then parent.
E4  redundant copy across shards (FD / non-key IND), class "needs atomic visibility":
    writer updates both copies in one 2PC txn; reader reads copy-then-master or master-then-copy.
"""
import json, os, random, sys, threading, time
import psycopg2

WORK = os.environ.get('X2_WORK', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'work'))
DSN = f"host={WORK}/sock port=29700 user=postgres dbname=postgres"


def conn(iso=None):
    c = psycopg2.connect(DSN)
    c.autocommit = True
    return c


def q(c, sql, args=None):
    cur = c.cursor()
    cur.execute(sql, args)
    try:
        return cur.fetchall()
    except psycopg2.ProgrammingError:
        return None


def node_of(c, table, key):
    return q(c, "SELECT nodeport FROM citus_shards WHERE shardid = get_shard_id_for_distribution_column(%s, %s)",
             (table, key))[0][0]


def keys_on_distinct_nodes(c, table, n):
    out, seen = [], set()
    k = 1
    while len(out) < n:
        p = node_of(c, table, k)
        if p not in seen:
            seen.add(p)
            out.append(k)
        k += 1
    return out


def run_pair(f1, f2, delay2):
    errs = []
    b = threading.Barrier(2)

    def w(f, d):
        try:
            b.wait()
            if d:
                time.sleep(d)
            f()
        except Exception as e:  # noqa
            errs.append(repr(e)[:120])
    t1 = threading.Thread(target=w, args=(f1, 0))
    t2 = threading.Thread(target=w, args=(f2, delay2))
    t1.start(); t2.start(); t1.join(); t2.join()
    return errs


def e3(trials, mode):
    c = conn()
    q(c, "DROP TABLE IF EXISTS parent; DROP TABLE IF EXISTS child")
    q(c, "CREATE TABLE parent (pid int PRIMARY KEY)")
    q(c, "CREATE TABLE child (cid int PRIMARY KEY, pid int)")
    q(c, "SELECT create_distributed_table('parent', 'pid')")
    q(c, "SELECT create_distributed_table('child', 'cid', colocate_with => 'none')")
    cw, cr = conn(), conn()
    orphan = 0
    seen_child = 0
    pairs = []
    k = 1
    while len(pairs) < 64:  # parent/child keys on different workers
        if node_of(c, 'parent', k) != node_of(c, 'child', 10000 + k):
            pairs.append((k, 10000 + k))
        k += 1
    for t in range(trials):
        p, ch = pairs[t % len(pairs)]
        q(c, "DELETE FROM child WHERE cid = %s", (ch,))
        q(c, "DELETE FROM parent WHERE pid = %s", (p,))

        def w():
            cur = cw.cursor()
            if mode == 'atomic':
                cur.execute("BEGIN")
                cur.execute("INSERT INTO parent VALUES (%s)", (p,))
                cur.execute("INSERT INTO child VALUES (%s, %s)", (ch, p))
                cur.execute("COMMIT")
            else:
                cur.execute("INSERT INTO parent VALUES (%s)", (p,))
                cur.execute("INSERT INTO child VALUES (%s, %s)", (ch, p))
        found = {}

        def r():
            deadline = time.time() + 0.05
            while time.time() < deadline:
                got = q(cr, "SELECT pid FROM child WHERE cid = %s", (ch,))
                if got:
                    par = q(cr, "SELECT 1 FROM parent WHERE pid = %s", (got[0][0],))
                    found['child'] = True
                    found['orphan'] = not par
                    return
        run_pair(w, r, 0)
        if found.get('child'):
            seen_child += 1
            orphan += found['orphan']
    return {'mode': mode, 'trials': trials, 'reads_that_saw_child': seen_child, 'orphans': orphan}


def e4(trials, read_order):
    c = conn()
    q(c, "DROP TABLE IF EXISTS prof; DROP TABLE IF EXISTS ordr")
    q(c, "CREATE TABLE prof (uid int PRIMARY KEY, name int)")
    q(c, "CREATE TABLE ordr (oid int PRIMARY KEY, uid int, uname int)")
    q(c, "SELECT create_distributed_table('prof', 'uid')")
    q(c, "SELECT create_distributed_table('ordr', 'oid', colocate_with => 'none')")
    u, o = 1, None
    k = 100
    while o is None:
        if node_of(c, 'ordr', k) != node_of(c, 'prof', u):
            o = k
        k += 1
    q(c, "INSERT INTO prof VALUES (%s, 0)", (u,))
    q(c, "INSERT INTO ordr VALUES (%s, %s, 0)", (o, u))
    cw, cr = conn(), conn()
    mism = 0
    reads = 0
    stop = threading.Event()

    def reader():
        nonlocal mism, reads
        while not stop.is_set():
            if read_order == 'stmt':
                rows = q(cr, "SELECT 'c', uname FROM ordr WHERE oid = %s UNION ALL SELECT 'm', name FROM prof WHERE uid = %s", (o, u))
                d = dict(rows)
                a, b = d['c'], d['m']
            else:
                cur = cr.cursor()
                cur.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
                if read_order == 'rr_copy_then_master':
                    cur.execute("SELECT uname FROM ordr WHERE oid = %s", (o,)); a = cur.fetchone()[0]
                    cur.execute("SELECT name FROM prof WHERE uid = %s", (u,)); b = cur.fetchone()[0]
                else:
                    cur.execute("SELECT name FROM prof WHERE uid = %s", (u,)); b = cur.fetchone()[0]
                    cur.execute("SELECT uname FROM ordr WHERE oid = %s", (o,)); a = cur.fetchone()[0]
                cur.execute("COMMIT")
            reads += 1
            mism += (a != b)
    th = threading.Thread(target=reader)
    th.start()
    cur = cw.cursor()
    for t in range(1, trials + 1):
        cur.execute("BEGIN")
        cur.execute("UPDATE prof SET name = %s WHERE uid = %s", (t, u))
        cur.execute("UPDATE ordr SET uname = %s WHERE oid = %s", (t, o))
        cur.execute("COMMIT")
    stop.set()
    th.join()
    return {'read_order': read_order, 'writer_txns': trials, 'reads': reads, 'mismatched_reads': mism}


if __name__ == '__main__':
    which = sys.argv[1]
    n = int(sys.argv[2])
    random.seed(7)
    if which == 'e3a':
        r = e3(n, 'atomic')
    elif which == 'e3o':
        r = e3(n, 'ordered')
    elif which == 'e4':
        r = [e4(n, m) for m in ('stmt', 'rr_copy_then_master', 'rr_master_then_copy')]
    print(json.dumps({which: r}))
