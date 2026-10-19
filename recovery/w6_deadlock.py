"""Deadlock runs of Section 6.3 (Lemma A.13, lem:deadlock) on Citus.

Workload: C closed-loop clients move children between Zipf-hot groups.  There are G groups, group of
rank r on worker r mod 3; each client owns M children (child j on worker j mod 3), each in a group
drawn from a Zipf distribution with exponent THETA over the ranks.  An update takes one of the
client's children c, whose group is p, draws a new group q != p from the same distribution, points c
to q and updates a column of both groups, as Rails' touch option and counter caches do; so updates
conflict on the hot groups.  The write of q also fences q (Sec. 5.3): its transaction commits after
the transaction of c, so an update runs as leveled commits in two rounds if c and q sit on
different workers, and in one round otherwise; p joins the transaction of its worker.
Methods:
  Lnw     leveled commits whose execution phase never waits: every transaction first locks its rows
          with SELECT ... FOR NO KEY UPDATE NOWAIT; if any lock is held by another update, the update
          rolls back all its transactions and retries (lem:deadlock)
  Lb2000  leveled commits whose writes wait for locks, lock_timeout = 2 s on the workers (PostgreSQL
          itself detects deadlocks within one worker after deadlock_timeout = 1 s)
  Lb100   the same with lock_timeout = 100 ms
  Lord    leveled commits that lock their rows in one global order before writing:
          worker by worker in ascending order, one message per worker (BEGIN and SELECT ...
          FOR NO KEY UPDATE of its rows, child before groups, groups by key), waiting for locks; then
          the writes on all workers in parallel and the commits round by round; lock_timeout = 2 s
          as a backstop only, since no cycle can form
  h       one distributed transaction (two-phase commit) sent as one message to the coordinator,
          statements c, p, q; Citus detects distributed deadlocks and cancels a victim
  hd      as h, but sent to the worker of c (Citus MX), which coordinates it
After the n-th failed attempt, an update rolls back and retries after a random backoff, uniform in
[0, min(20 ms, 0.5 ms * 2^(n-1))], the same for all methods.
Under leveled commits, a monitor samples the lock waits on the workers every 25 ms
(pg_blocking_pids) and builds the wait-for graph between clients (connections are named w6:<client>).
A client holds all its locks until it has executed every transaction, so every cycle of this graph is
a deadlock; PostgreSQL detects it only if all its edges lie on one worker.  Cycles seen in at least
two consecutive samples count; a cycle with edges on two workers is a hidden cross-shard deadlock,
which only lock_timeout breaks.  (Under h and hd the backends on the workers belong to Citus, whose
own detector reports the deadlocks it breaks.)
After the run, sum(ts) over the groups must equal twice the number of committed updates, and
sum(pid) over the children must equal what the clients recorded.

usage: python -I w6_deadlock.py METHOD C BLOCK [warm dur G THETA M]   prints one JSON line
"""
import json, multiprocessing as mp, os, random, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import w6lib as L  # noqa: E402

LOCK_NW = {'c': 'SELECT 1 FROM w6_child WHERE cid = %s FOR NO KEY UPDATE NOWAIT',
           'p': 'SELECT 1 FROM w6_parent WHERE pid = %s FOR NO KEY UPDATE NOWAIT'}
LOCK_ORD = {'c': 'SELECT 1 FROM w6_child WHERE cid = %s FOR NO KEY UPDATE',
            'p': 'SELECT 1 FROM w6_parent WHERE pid = %s FOR NO KEY UPDATE'}
LOCK_TIMEOUT = {'Lnw': None, 'Lb2000': 2000, 'Lb100': 100, 'Lord': 2000}
METHODS = ('Lnw', 'Lb2000', 'Lb100', 'Lord', 'h', 'hd')
G_LO = 7_000_001
KEYS_PER_CLIENT = 30_000
MONQ = ("SELECT a.application_name, b.application_name FROM pg_stat_activity a "
        "CROSS JOIN LATERAL unnest(pg_blocking_pids(a.pid)) AS x(bpid) "
        "JOIN pg_stat_activity b ON b.pid = x.bpid "
        "WHERE a.wait_event_type = 'Lock' AND a.application_name LIKE 'w6:%'")
PERIOD = 0.025


def group_keys(node_of, G):
    pools = L.Pools(node_of, G_LO, G_LO + 4 * G + 3000)
    return [pools.take(r % 3) for r in range(G)]


def client_kids(node_of, wid, M, G, theta, seed):
    """(child, rank of its initial group) for the M children of client wid."""
    pools = L.Pools(node_of, 1 + wid * KEYS_PER_CLIENT, 1 + (wid + 1) * KEYS_PER_CLIENT)
    z = L.Zipf(G, theta, seed * 7919 + wid)
    return [(pools.take(j % 3), z()) for j in range(M)]


# ---------------------------------------------------------------- client
def client(*args):
    try:
        client_body(*args)
    except Exception:  # noqa
        import traceback
        args[-1].put({'wid': args[1], 'error': traceback.format_exc()[-1500:]})
        raise


def client_body(method, wid, seed, G, theta, M, node_of, grp, go, t_start, t_end, t_stop, out):
    import psycopg2
    kids = client_kids(node_of, wid, M, G, theta, seed)
    parent_of = {c: grp[r] for c, r in kids}
    mine = [c for c, _ in kids]
    z = L.Zipf(G, theta, seed * 104729 + wid)
    rnd = random.Random(seed * 1000 + wid)
    app = f'w6:{wid}'
    leveled = method in LOCK_TIMEOUT
    if leveled:
        lt = LOCK_TIMEOUT[method]
        par = L.Par(app, f'-c lock_timeout={lt}' if lt else '')
    elif method == 'hd':
        par = L.Par(app)
    else:
        db = L.connect(0, app)
        cur = db.cursor()
    out.put({'wid': wid, 'ready': True})
    go.wait()
    lat, ops, attempts, fails, committed, consec = [], 0, 0, {}, 0, 0
    pc = time.perf_counter
    while time.time() < t_stop.value:
        c = mine[rnd.randrange(M)]
        p = parent_of[c]
        q = p
        while q == p:
            q = grp[z()]
        nodes = [node_of[c], node_of[p], node_of[q]]
        kinds = ('c', 'p', 'p')
        keys = (c, p, q)
        writes = [(L.UPD_C, (q, c)), (L.TOUCH, (p,)), (L.TOUCH, (q,))]
        t0 = pc()
        att, f_here = 0, {}
        while True:
            att += 1
            if method == 'Lord':
                rounds = L.plan(nodes, [(0, 2)])
                txns = [(nd, idx) for lvl in rounds for nd, idx in lvl]
                errs = {}
                for nd, idx in sorted(txns):          # one global order: worker, then child before groups
                    order = sorted(idx, key=lambda i: (kinds[i] != 'c', keys[i]))
                    errs = par.run([(nd, [('BEGIN', None)] + [(LOCK_ORD[kinds[i]], (keys[i],)) for i in order])])
                    if errs:
                        break
                if not errs:
                    errs = par.run([(nd, [writes[i] for i in idx]) for nd, idx in txns])
                if not errs:
                    for lvl in rounds:
                        L.chk(par.run([(nd, [('COMMIT', None)]) for nd, _ in lvl]), 'commit')
                    break
                L.chk(par.rollback([nd for nd, _ in txns]), 'rollback')
                es = list(errs.values())
            elif leveled:
                rounds = L.plan(nodes, [(0, 2)])
                jobs = []
                for lvl in rounds:
                    for nd, idx in lvl:
                        body = [('BEGIN', None)]
                        if method == 'Lnw':
                            body += [(LOCK_NW[kinds[i]], (keys[i],)) for i in idx]
                        jobs.append((nd, body + [writes[i] for i in idx]))
                errs = par.run(jobs)
                if not errs:
                    for lvl in rounds:
                        L.chk(par.run([(nd, [('COMMIT', None)]) for nd, _ in lvl]), 'commit')
                    break
                L.chk(par.rollback([nd for nd, _ in jobs]), 'rollback')
                es = list(errs.values())
            elif method == 'hd':
                nd = node_of[c]
                errs = par.run([(nd, [L.one_message(writes)])])
                if not errs:
                    break
                par.rollback([nd])
                es = list(errs.values())
            else:
                try:
                    cur.execute(*L.one_message(writes))
                    break
                except psycopg2.Error as e:
                    try:
                        cur.execute('ROLLBACK')
                    except psycopg2.Error:
                        pass
                    es = [e]
            for e in es:
                k = L.classify(e)
                f_here[k] = f_here.get(k, 0) + 1
                if k == 'other':
                    f_here.setdefault('other_msgs', {})
                    m = str(e).strip().splitlines()[0][:100]
                    f_here['other_msgs'][m] = f_here['other_msgs'].get(m, 0) + 1
            consec += 1
            if consec > 2000:
                raise RuntimeError(f'{method}: 2000 failed attempts in a row: {es}')
            time.sleep(L.backoff_secs(att, rnd))
        t1 = pc()
        consec = 0
        parent_of[c] = q
        committed += 1
        if t_start.value <= time.time() <= t_end.value:
            lat.append(t1 - t0)
            ops += 1
            attempts += att
            for k, v in f_here.items():
                if k == 'other_msgs':
                    om = fails.setdefault('other_msgs', {})
                    for m, n in v.items():
                        om[m] = om.get(m, 0) + n
                else:
                    fails[k] = fails.get(k, 0) + v
    out.put({'wid': wid, 'lat': lat, 'ops': ops, 'attempts': attempts, 'fails': fails,
             'committed': committed, 'pid_sum': sum(parent_of.values())})


# ---------------------------------------------------------------- monitor of lock waits
def monitor(go, t_start, t_end, t_stop, out):
    try:
        cs = [L.connect(1 + n, 'w6mon') for n in range(3)]
        curs = [c.cursor() for c in cs]
        out.put({'monitor_ready': True})
        go.wait()
        samples = wait_samples = 0
        waiters = []
        active, done = {}, []
        while time.time() < t_stop.value:
            t = time.time()
            if t_start.value <= t <= t_end.value:
                edges = set()
                for n, cur in enumerate(curs):
                    cur.execute(MONQ)
                    for a, b in cur.fetchall():
                        if b and b.startswith('w6:') and a != b:
                            edges.add((a, b, n))
                samples += 1
                wait_samples += bool(edges)
                waiters.append(len({a for a, _, _ in edges}))
                now = set()
                for comp, ws in L.sccs(edges):
                    now.add(comp)
                    ev = active.get(comp)
                    if ev:
                        ev['last'], ev['n'] = t, ev['n'] + 1
                        ev['workers'] |= set(ws)
                    else:
                        active[comp] = {'first': t, 'last': t, 'n': 1, 'workers': set(ws), 'size': len(comp)}
                for comp in list(active):
                    if comp not in now:
                        done.append(active.pop(comp))
            time.sleep(max(0.0, PERIOD - (time.time() - t)))
        done += list(active.values())
        ev = [e for e in done if e['n'] >= 2]
        cross = [e for e in ev if len(e['workers']) >= 2]
        local = [e for e in ev if len(e['workers']) < 2]
        durs = sorted(e['last'] - e['first'] + PERIOD for e in cross)
        out.put({'monitor': {
            'samples': samples, 'samples_with_wait': wait_samples,
            'mean_waiting_clients': round(sum(waiters) / len(waiters), 3) if waiters else None,
            'cycles': len(ev), 'cross_worker_cycles': len(cross), 'one_worker_cycles': len(local),
            'cross_cycle_ms_p50': round(L.pct(durs, 0.5) * 1000, 1) if durs else None,
            'cross_cycle_ms_max': round(durs[-1] * 1000, 1) if durs else None,
            'cross_cycle_sizes': sorted(e['size'] for e in cross)[-5:],
            'cycles_seen_once': len(done) - len(ev)}})
    except Exception:  # noqa
        import traceback
        out.put({'error': 'monitor: ' + traceback.format_exc()[-1500:]})
        raise


# ---------------------------------------------------------------- orchestration
def checkpoint():
    for i in range(4):
        c = L.connect(i, 'w6setup')
        c.cursor().execute('CHECKPOINT')
        c.close()


def run(method, C, block, warm=2.0, dur=10.0, G=1000, theta=0.99, M=200):
    assert method in METHODS, method
    L.setup_tables()
    node_of = L.node_map()
    seed = 31 * block + C
    grp = group_keys(node_of, G)
    kids = [(ch, grp[r]) for w in range(C) for ch, r in client_kids(node_of, w, M, G, theta, seed)]
    c = L.connect(0, 'w6setup')
    cur = c.cursor()
    settings = {}
    for s in ('deadlock_timeout', 'citus.distributed_deadlock_detection_factor', 'lock_timeout',
              'default_transaction_isolation'):
        cur.execute(f'SHOW {s}')
        settings[s] = cur.fetchone()[0]
    L.copy_rows(cur, 'w6_parent', ['pid'], [(g,) for g in grp])
    L.copy_rows(cur, 'w6_child', ['cid', 'pid'], kids)
    checkpoint()
    ctx = mp.get_context('fork')
    go = ctx.Event()
    t_start, t_end, t_stop = ctx.Value('d', 0.0), ctx.Value('d', 0.0), ctx.Value('d', 1e18)
    out = ctx.Queue()
    ps = [ctx.Process(target=client, args=(method, w, seed, G, theta, M, node_of, grp, go, t_start, t_end,
                                           t_stop, out)) for w in range(C)]
    mon = method in LOCK_TIMEOUT
    if mon:
        ps.append(ctx.Process(target=monitor, args=(go, t_start, t_end, t_stop, out)))
    for p in ps:
        p.start()
    for _ in ps:
        m = out.get(timeout=600)
        if 'error' in m:
            for p in ps:
                p.terminate()
            raise SystemExit('setup failed: ' + m['error'])
    load0 = os.getloadavg()[0]
    t0 = time.time() + 0.5
    t_start.value, t_end.value, t_stop.value = t0 + warm, t0 + warm + dur, t0 + warm + dur
    while time.time() < t0:
        time.sleep(0.01)
    go.set()
    res, monres = [], None
    for _ in ps:
        m = out.get(timeout=warm + dur + 600)
        if 'error' in m:
            for p in ps:
                p.terminate()
            raise SystemExit('run failed: ' + m['error'])
        if 'monitor' in m:
            monres = m['monitor']
        else:
            res.append(m)
    for p in ps:
        p.join(timeout=60)
    cur.execute('SELECT sum(ts) FROM w6_parent')
    ts_sum = int(cur.fetchone()[0] or 0)          # numeric
    cur.execute('SELECT sum(pid) FROM w6_child')
    pid_sum = int(cur.fetchone()[0] or 0)
    c.close()
    committed = sum(r['committed'] for r in res)
    lat = sorted(x for r in res for x in r['lat'])
    ops = sum(r['ops'] for r in res)
    attempts = sum(r['attempts'] for r in res)
    fails = {}
    for r in res:
        for k, v in r['fails'].items():
            if k == 'other_msgs':
                om = fails.setdefault('other_msgs', {})
                for mm, n in v.items():
                    om[mm] = om.get(mm, 0) + n
            else:
                fails[k] = fails.get(k, 0) + v
    ms = lambda q: round(L.pct(lat, q) * 1000, 3) if lat else None  # noqa: E731
    return {
        'design': 'w6_deadlock', 'method': method, 'C': C, 'block': block, 'seed': seed,
        'warm': warm, 'dur': dur, 'G': G, 'theta': theta, 'M': M, 'port': L.PORT,
        'start': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(t0)), 'loadavg': round(load0, 2),
        'ops': ops, 'tput': ops / dur, 'attempts': attempts,
        'failed_attempts': attempts - ops, 'abort_rate': (attempts - ops) / attempts if attempts else None,
        'fails': fails,
        'p50_ms': ms(0.5), 'p95_ms': ms(0.95), 'p99_ms': ms(0.99), 'max_ms': ms(1.0),
        'mean_ms': round(sum(lat) / len(lat) * 1000, 3) if lat else None,
        'monitor': monres,
        'committed_total': committed,
        'integrity_ok': ts_sum == 2 * committed and pid_sum == sum(r['pid_sum'] for r in res),
        'ts_sum': ts_sum, 'settings': settings,
    }


if __name__ == '__main__':
    a = sys.argv[1:]
    kw = {}
    if len(a) > 3:
        kw['warm'] = float(a[3])
    if len(a) > 4:
        kw['dur'] = float(a[4])
    if len(a) > 5:
        kw['G'] = int(a[5])
    if len(a) > 6:
        kw['theta'] = float(a[6])
    if len(a) > 7:
        kw['M'] = int(a[7])
    print(json.dumps(run(a[0], int(a[1]), int(a[2]), **kw)), flush=True)
