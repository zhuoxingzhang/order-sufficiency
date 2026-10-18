"""Repeated batches of the Citus engine experiments behind Figure 5.

The measured cells call the functions of exp_citus.py; only the seed differs per batch.
e3x is an instrumented copy of exp_citus.e3 (same keys, same reader) that also
logs per-run placement and can write the child first; it backs the supplementary
cells only.

usage: python -I x2_citus.py <cell> <n> <batch> <seed>
cells: e4 (uniform copy), e3a, e3o, e3x_pf, e3x_cf (slow worker)
"""
import json, os, random, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exp_citus as E  # noqa: E402

q, conn, node_of, run_pair = E.q, E.conn, E.node_of, E.run_pair


def e3x(trials, order):
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
        pn, cn = node_of(c, 'parent', k), node_of(c, 'child', 10000 + k)
        if pn != cn:
            pairs.append((k, 10000 + k, pn, cn))
        k += 1
    by = {}  # (parent node, child node) -> [runs, saw_child, orphans]
    for t in range(trials):
        p, ch, pn, cn = pairs[t % len(pairs)]
        q(c, "DELETE FROM child WHERE cid = %s", (ch,))
        q(c, "DELETE FROM parent WHERE pid = %s", (p,))

        def w():
            cur = cw.cursor()
            cur.execute("BEGIN")
            if order == 'parent_first':
                cur.execute("INSERT INTO parent VALUES (%s)", (p,))
                cur.execute("INSERT INTO child VALUES (%s, %s)", (ch, p))
            else:
                cur.execute("INSERT INTO child VALUES (%s, %s)", (ch, p))
                cur.execute("INSERT INTO parent VALUES (%s)", (p,))
            cur.execute("COMMIT")
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
        s = by.setdefault(f"{pn}->{cn}", [0, 0, 0])
        s[0] += 1
        if found.get('child'):
            s[1] += 1
            s[2] += found['orphan']
    return {'mode': 'atomic_' + order, 'trials': trials,
            'reads_that_saw_child': sum(v[1] for v in by.values()),
            'orphans': sum(v[2] for v in by.values()),
            'by_parent_child_node': by}


if __name__ == '__main__':
    cell, n, batch, seed = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    random.seed(seed)
    t0, l0 = time.time(), os.getloadavg()[0]
    if cell == 'e4':
        r = [E.e4(n, m) for m in ('stmt', 'rr_copy_then_master', 'rr_master_then_copy')]
    elif cell == 'e3a':
        r = E.e3(n, 'atomic')
    elif cell == 'e3o':
        r = E.e3(n, 'ordered')
    elif cell == 'e3x_pf':
        r = e3x(n, 'parent_first')
    elif cell == 'e3x_cf':
        r = e3x(n, 'child_first')
    else:
        raise SystemExit('unknown cell ' + cell)
    print(json.dumps({'engine': 'citus', 'cell': cell, 'batch': batch, 'seed': seed, 'n': n,
                      'start': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(t0)),
                      'secs': round(time.time() - t0, 1), 'loadavg_start': l0,
                      'result': r}), flush=True)
