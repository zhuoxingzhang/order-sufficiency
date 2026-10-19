"""Fence runs on Citus: two templates that are not legal together (Section 5.3), with and without a
lock on the shared parent row.

  merge  (the reparent template of Example 5.6 with a read): insert a new group g1 on another worker,
         move every child of a group g2 to g1, delete g2
  add    insert a child into an existing group g, after checking that g exists

Methods (merge / add):
  L   leveled commits (x7bench.plan) after reading the children of g2 / check g, then insert the child
  a   one distributed transaction, statements one by one / the same check and insert
  Lf  leveled commits that first lock g2 (SELECT ... FOR UPDATE in the transaction that deletes g2,
      which commits last) and read the children afterwards / a fence: the check is SELECT ... FOR KEY
      SHARE in a transaction on the worker of g that commits after the transaction of the child
  af  one distributed transaction that first locks g2 / the same check with FOR KEY SHARE and the
      insert in one transaction
A adders pick random groups among M active groups and pause THINK s after every add; NM mergers each own M / NM of them and replace a
merged group by its successor.  After T s, the orchestrator counts children whose parent is absent.
usage: python -I x7_fence.py METHOD BLOCK [A NM M T]"""
import json, multiprocessing as mp, os, random, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg2  # noqa: E402
import x7bench as B  # noqa: E402

CHK = 'SELECT 1 FROM parent WHERE pid = %s'
FENCE = 'SELECT 1 FROM parent WHERE pid = %s FOR KEY SHARE'
LOCK = 'SELECT 1 FROM parent WHERE pid = %s FOR UPDATE'
KIDS = 'SELECT cid FROM child WHERE pid = %s'
THINK = 0.005


def p50(v):
    v = sorted(v)
    return v[len(v) // 2] / 1e6 if v else None


def adder(method, aid, node_of, active, out, go, t_stop):
    db = psycopg2.connect(B.CITUS_DSN)
    db.autocommit = True
    cur = db.cursor()
    direct = B.ROUTE == 'direct'
    par = B.CitusPar(3, direct=direct) if method == 'Lf' or (direct and method == 'L') else None
    pool = B.Pools(node_of, 1 + aid * 1_000_000, 1 + (aid + 1) * 1_000_000, 3)
    rnd = random.Random(aid)
    n = {'added': 0, 'rejected': 0, 'stale': 0, 'errors': 0}
    lat = []
    go.wait()
    while time.time() < t_stop.value:
        time.sleep(THINK)
        g = active[rnd.randrange(len(active))]
        c = pool.take(rnd.randrange(3))
        t0 = time.perf_counter_ns()
        try:
            if method == 'L' and direct:          # check on the worker of g, insert on that of c
                ng, nc = node_of[g], node_of[c]
                if par.run([(ng, [(CHK, (g,))])]):
                    n['errors'] += 1
                    continue
                if not par.cur[ng].fetchall():
                    n['stale'] += 1
                    continue
                if par.run([(nc, [(B.INS_C, (c, g))])]):
                    n['errors'] += 1
                    continue
            elif method in ('L', 'a'):
                cur.execute(CHK, (g,))
                if not cur.fetchall():
                    n['stale'] += 1
                    continue
                cur.execute(B.INS_C, (c, g))
            elif method == 'af':
                cur.execute('BEGIN')
                cur.execute(FENCE, (g,))
                if not cur.fetchall():
                    cur.execute('ROLLBACK')
                    n['rejected'] += 1
                    continue
                cur.execute(B.INS_C, (c, g))
                cur.execute('COMMIT')
            else:  # Lf: the fence and the child execute in parallel, the child commits first
                ng, nc = node_of[g], node_of[c]
                jobs = [(ng, [('BEGIN', None), (FENCE, (g,))])]
                if nc != ng:
                    jobs.append((nc, [('BEGIN', None), (B.INS_C, (c, g))]))
                errs = par.run(jobs)
                if errs or not par.cur[ng].fetchall():
                    par.run([(nd, [('ROLLBACK', None)]) for nd, _ in jobs])
                    n['errors' if errs else 'rejected'] += 1
                    continue
                if nc == ng:
                    if par.run([(ng, [(B.INS_C, (c, g)), ('COMMIT', None)])]):
                        raise RuntimeError('insert failed')
                else:
                    if par.run([(nc, [('COMMIT', None)])]):
                        raise RuntimeError('child commit failed')
                    par.run([(ng, [('COMMIT', None)])])
        except psycopg2.Error:
            n['errors'] += 1
            try:
                cur.execute('ROLLBACK')
            except psycopg2.Error:
                pass
            continue
        lat.append(time.perf_counter_ns() - t0)
        n['added'] += 1
    out.put({'aid': aid, 'n': n, 'lat': lat})


def merger(method, mid, NM, node_of, active, out, go, t_stop):
    db = psycopg2.connect(B.CITUS_DSN)
    db.autocommit = True
    cur = db.cursor()
    par = B.CitusPar(3, direct=B.ROUTE == 'direct') if method in ('L', 'Lf') else None
    pool = B.Pools(node_of, 5_000_001 + mid * 400_000, 5_000_001 + (mid + 1) * 400_000, 3)
    slots = [s for s in range(len(active)) if s % NM == mid]
    rnd = random.Random(100 + mid)
    n = {'merges': 0, 'moved': 0, 'errors': 0}
    lat = []
    go.wait()
    while time.time() < t_stop.value:
        s = rnd.choice(slots)
        g2 = active[s]
        g1 = pool.take((node_of[g2] + 1 + rnd.randrange(2)) % 3)
        t0 = time.perf_counter_ns()
        try:
            if method in ('a', 'af'):
                cur.execute('BEGIN')
                if method == 'af':
                    cur.execute(LOCK, (g2,))
                cur.execute(B.INS_P, (g1,))
                cur.execute(KIDS, (g2,))
                kids = [r[0] for r in cur.fetchall()]
                for c in kids:
                    cur.execute(B.UPD_C, (g1, c))
                cur.execute(B.DEL_P, (g2,))
                cur.execute('COMMIT')
            else:
                n2 = node_of[g2]
                if method == 'Lf':                      # lock g2 before reading its children
                    if par.run([(n2, [('BEGIN', None), (LOCK, (g2,))])]):
                        raise RuntimeError('lock failed')
                cur.execute(KIDS, (g2,))
                kids = [r[0] for r in cur.fetchall()]
                st = [(B.INS_P, (g1,))] + [(B.UPD_C, (g1, c)) for c in kids] + [(B.DEL_P, (g2,))]
                levels = B.plan(st, node_of, 'rep')
                jobs = []
                for lvl in levels:
                    for nd, idx in lvl:
                        body = [st[j] for j in idx]
                        if method == 'Lf' and nd == n2:
                            assert len(st) - 1 in idx, 'g2 shares its worker with another transaction'
                            jobs.append((nd, body))     # inside the locking transaction
                        else:
                            jobs.append((nd, [('BEGIN', None)] + body))
                if par.run(jobs):
                    par.run([(nd, [('ROLLBACK', None)]) for nd, _ in jobs])
                    raise RuntimeError('execution failed')
                for lvl in levels:
                    if par.run([(nd, [('COMMIT', None)]) for nd, _ in lvl]):
                        raise RuntimeError('commit failed')
        except (psycopg2.Error, RuntimeError):
            n['errors'] += 1
            try:
                cur.execute('ROLLBACK')
            except psycopg2.Error:
                pass
            if par:
                par.run([(nd, [('ROLLBACK', None)]) for nd in range(3)])
            continue
        lat.append(time.perf_counter_ns() - t0)
        active[s] = g1
        n['merges'] += 1
        n['moved'] += len(kids)
    out.put({'mid': mid, 'n': n, 'lat': lat})


def main():
    method, block = sys.argv[1], int(sys.argv[2])
    A = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    NM = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    M = int(sys.argv[5]) if len(sys.argv) > 5 else 16
    T = float(sys.argv[6]) if len(sys.argv) > 6 else 12.0
    B.reset('citus')
    node_of = B.load_keymap('citus')
    init = B.Pools(node_of, 7_000_001, 7_100_001, 3)
    groups = [init.take(i % 3) for i in range(M)]
    B.Citus().bulk(groups, [])
    ctx = mp.get_context('fork')
    go, out = ctx.Event(), ctx.Queue()
    t_stop = ctx.Value('d', 1e18)
    active = ctx.Array('q', groups)
    ps = [ctx.Process(target=adder, args=(method, a, node_of, active, out, go, t_stop)) for a in range(A)]
    ps += [ctx.Process(target=merger, args=(method, m, NM, node_of, active, out, go, t_stop)) for m in range(NM)]
    for p in ps:
        p.start()
    time.sleep(3)
    t0 = time.time()
    t_stop.value = t0 + T
    go.set()
    res = [out.get(timeout=T + 300) for _ in ps]
    for p in ps:
        p.join(timeout=30)
    c = psycopg2.connect(f'host={B.WORK}/sock port=29800 user=postgres dbname=postgres')
    c.autocommit = True
    cur = c.cursor()
    cur.execute('SELECT cid, pid FROM child')
    kids = cur.fetchall()
    cur.execute('SELECT pid FROM parent')
    present = {r[0] for r in cur.fetchall()}
    orphans = sum(1 for _, p in kids if p not in present)
    orphan_groups = len({p for _, p in kids if p not in present})
    add = {k: sum(r['n'][k] for r in res if 'aid' in r) for k in ('added', 'rejected', 'stale', 'errors')}
    mer = {k: sum(r['n'][k] for r in res if 'mid' in r) for k in ('merges', 'moved', 'errors')}
    print(json.dumps({
        'design': 'x7_fence', 'route': B.ROUTE, 'method': method, 'block': block, 'A': A, 'NM': NM, 'M': M, 'T': T, 'think': THINK,
        'start': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(t0)), 'loadavg': os.getloadavg()[0],
        'adds': add, 'merges': mer, 'children': len(kids), 'orphans': orphans,
        'merges_leaving_orphans': orphan_groups,
        'add_tput': add['added'] / T, 'merge_tput': mer['merges'] / T,
        'add_p50_ms': p50([x for r in res if 'aid' in r for x in r['lat']]),
        'merge_p50_ms': p50([x for r in res if 'mid' in r for x in r['lat']]),
    }), flush=True)


if __name__ == '__main__':
    main()
