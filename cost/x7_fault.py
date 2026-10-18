"""Fault runs on Citus: a commit message to one worker is lost, under one distributed transaction
(method a, two-phase commit) and under leveled commits with a cheap outbox (method L).

The coordinator reaches worker 29802 through an in-process relay.  Citus encrypts the connections
between nodes by default (citus.node_conninfo = sslmode=require); the run sets sslmode=disable on all
nodes so that the relay can see the commit messages, and resets it afterwards.  While the run writes, the relay is
armed every ARM s; once armed, it drops the next commit message from the coordinator to the worker
(COMMIT PREPARED under a, COMMIT under L) and closes that connection, so the worker never receives it.

Workload: W writer processes each run the reparent template on their own families (a child c whose
parent is P): insert a new parent P2, point c to P2, delete P.  The child, P and P2 lie on three
different workers, and each of them lies on the faulty worker in a third of the families.  Every
family is used once.  Under L, the transaction of P2 also inserts an outbox row with the whole
update; the writer deletes the row once the update has committed, and if a commit fails it rolls
back the open transactions and leaves the row to a relay, which applies the remaining levels in order
(every write is idempotent).  If the first level fails, nothing has committed, and the writer retries
the update (at most three attempts).  Under a, Citus answers a lost COMMIT PREPARED with a warning and commits the
prepared transaction at its next recovery (citus.recover_2pc_interval, 1 min by default).

Readers: R processes read recently written families: read the child, then, in the
next statement, the parent it points to; an absent parent is an orphan.  After the writers stop, the
orchestrator checks every used family every 0.5 s until no orphan is left (at most WAIT s), runs the
relay to completion under L, and checks that every update completed.

usage: python -I x7_fault.py METHOD BLOCK [W R T]"""
import json, multiprocessing as mp, os, random, socket, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg2  # noqa: E402
import x7bench as B  # noqa: E402

FAULT_PORT, PROXY_PORT = 29802, 49802
FAULT_NODE = 1
ARM, WARM, WAIT, F = 0.5, 1.0, 100.0, 6000
COORD = f'host={B.WORK}/sock port=29800 user=postgres dbname=postgres'
INS_P2 = 'INSERT INTO parent (pid) VALUES (%s) ON CONFLICT DO NOTHING'


# ---------------------------------------------------------------- relay that loses commit messages
class FaultProxy:
    def __init__(self, pattern):
        self.pattern, self.armed, self.lock = pattern, threading.Event(), threading.Lock()
        self.fired, self.conns = [], 0
        self.ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.ls.bind(('127.0.0.1', PROXY_PORT))
        self.ls.listen(1024)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            a, _ = self.ls.accept()
            try:
                b = socket.create_connection(('127.0.0.1', FAULT_PORT))
            except OSError:
                a.close()
                continue
            self.conns += 1
            for s in (a, b):
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=self.pipe, args=(a, b, True), daemon=True).start()
            threading.Thread(target=self.pipe, args=(b, a, False), daemon=True).start()

    def pipe(self, src, dst, up):
        while True:
            try:
                data = src.recv(65536)
            except OSError:
                data = b''
            if up and data and self.armed.is_set() and self.pattern in data:
                with self.lock:
                    hit = self.armed.is_set()
                    self.armed.clear()
                if hit:
                    self.fired.append(time.time())
                    for s in (src, dst):
                        try:
                            s.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        s.close()
                    return
            if not data:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                return
            try:
                dst.sendall(data)
            except OSError:
                return


# ---------------------------------------------------------------- cluster helpers
def sql(q, args=None, fetch=False):
    c = psycopg2.connect(COORD)
    c.autocommit = True
    cur = c.cursor()
    cur.execute(q, args)
    r = cur.fetchall() if fetch else None
    c.close()
    return r


def plain_conninfo(on):
    for port in (29800, 29801, 29802, 29803):
        c = psycopg2.connect(f'host={B.WORK}/sock port={port} user=postgres dbname=postgres')
        c.autocommit = True
        cur = c.cursor()
        cur.execute("ALTER SYSTEM SET citus.node_conninfo = 'sslmode=disable'" if on else
                    'ALTER SYSTEM RESET citus.node_conninfo')
        cur.execute('SELECT pg_reload_conf()')
        c.close()
    time.sleep(1)


def route_fault_node(port):
    cur = sql('SELECT nodeport FROM pg_dist_node WHERE nodeport IN (%s, %s)', (FAULT_PORT, PROXY_PORT), True)[0][0]
    sql("SELECT citus_update_node(nodeid, 'localhost', %s) FROM pg_dist_node WHERE nodeport = %s", (port, cur))
    for _ in range(90):
        if sql('SELECT bool_and(metadatasynced) FROM pg_dist_node WHERE isactive AND groupid <> 0', fetch=True)[0][0]:
            return
        try:
            sql('SELECT start_metadata_sync_to_all_nodes()')
        except psycopg2.Error:
            pass
        time.sleep(2)
    raise SystemExit('metadata not synced')


def prepared_on_worker():
    c = psycopg2.connect(f'host={B.WORK}/sock port={FAULT_PORT} user=postgres dbname=postgres')
    cur = c.cursor()
    cur.execute('SELECT count(*) FROM pg_prepared_xacts')
    n = cur.fetchone()[0]
    c.close()
    return n


def families(node_of, W):
    """Per writer: F families (c, P, P2), child on node j mod 3, P on the next node, P2 on the one after."""
    out = []
    for w in range(W):
        pools = B.Pools(node_of, 1 + w * 1_000_000, 1 + (w + 1) * 1_000_000, 3)
        fam = []
        for j in range(F):
            cn = j % 3
            fam.append((pools.take(cn), pools.take((cn + 1) % 3), pools.take((cn + 2) % 3)))
        out.append(fam)
    return out


# ---------------------------------------------------------------- writers, readers, relay
def note(msgs, errs):
    for e in errs.values():
        m = str(e).splitlines()[0][:90] if str(e) else type(e).__name__
        msgs[m] = msgs.get(m, 0) + 1


def writer(method, wid, fam, node_of, recent, out, go, t_stop):
    db = psycopg2.connect(B.CITUS_DSN)
    db.autocommit = True
    cur = db.cursor()
    direct = B.ROUTE == 'direct'
    if method != 'L':
        par = None
    elif direct:      # each transaction goes to its worker, the faulty one through the relay
        # without sslmode=disable, libpq encrypts the TCP connection to the relay, which then never
        # sees no commit message
        opts = "options='-c statement_timeout=30000'"
        par = B.CitusPar(3, dsns=[f'host={B.WORK}/sock port=29801 user=postgres dbname=postgres {opts}',
                                  f'host=127.0.0.1 port={PROXY_PORT} user=postgres dbname=postgres sslmode=disable {opts}',
                                  f'host={B.WORK}/sock port=29803 user=postgres dbname=postgres {opts}'])
    else:
        par = B.CitusPar(3)
    ob_keys = B.Pools(node_of, 6_000_000 + wid * 200_000, 6_000_000 + (wid + 1) * 200_000, 3)
    status, msgs = {}, {}
    go.wait()
    for i, (c, P, P2) in enumerate(fam):
        if time.time() > t_stop.value:
            break
        st = [(INS_P2, (P2,)), (B.UPD_C, (P2, c)), (B.DEL_P, (P,))]
        if method == 'a':
            del db.notices[:]
            try:
                cur.execute('BEGIN')
                for s, a in st:
                    cur.execute(s, a)
                cur.execute('COMMIT')
                s_ = 'warned' if any('commit' in n.lower() or 'connection' in n.lower() for n in db.notices) else 'ok'
            except psycopg2.Error:
                try:
                    cur.execute('ROLLBACK')
                except psycopg2.Error:
                    pass
                s_ = 'error'
        else:
            levels = B.plan([(B.INS_P, (P2,)), st[1], st[2]], node_of, 'rep')
            s_ = 'failed'
            for attempt in (1, 2, 3):      # nothing has committed when an attempt is retried
                okey = ob_keys.take(levels[0][0][0])   # colocated with the first transaction
                payload = json.dumps({'ts': time.time(), 'levels': [[[st[j] for j in idx] for _, idx in lvl]
                                                                     for lvl in levels]})
                jobs = []
                for li, lvl in enumerate(levels):
                    for nd, idx in lvl:
                        body = [('BEGIN', None)] + [st[j] for j in idx]
                        if li == 0:
                            body.append((B.OUTBOX_INS, (okey, payload)))
                        jobs.append((nd, body))
                errs = par.run(jobs)
                if errs:
                    par.run([(nd, [('ROLLBACK', None)]) for nd, _ in jobs])
                    note(msgs, errs)
                    if direct:
                        par.reconnect()
                    continue
                failed = None
                for li, lvl in enumerate(levels):
                    errs = par.run([(nd, [('COMMIT', None)]) for nd, _ in lvl])
                    if errs:
                        failed = li
                        par.run([(nd, [('ROLLBACK', None)]) for l2 in levels[li:] for nd, _ in l2])
                        note(msgs, errs)
                        if direct:
                            par.reconnect()
                        break
                if failed is None:
                    cur.execute(B.OUTBOX_DEL, (okey,))
                    s_ = 'ok' if attempt == 1 else 'retried'
                    break
                if failed > 0:
                    s_ = f'interrupted_l{failed}'   # the relay completes it from the outbox
                    break
        status[i] = s_
        recent[wid] = i
    out.put({'wid': wid, 'status': status, 'msgs': msgs})


def reader(rid, W, fams, recent, out, go, t_stop):
    db = psycopg2.connect(COORD)
    db.autocommit = True
    cur = db.cursor()
    rnd = random.Random(rid)
    reads = orphans = 0
    seen = []
    go.wait()
    while time.time() < t_stop.value:
        w = rnd.randrange(W)
        top = recent[w]
        if top < 0:
            continue
        j = max(0, top - rnd.randrange(20))
        c = fams[w][j][0]
        cur.execute('SELECT pid FROM child WHERE cid = %s', (c,))
        r = cur.fetchall()
        if not r:
            continue
        cur.execute('SELECT 1 FROM parent WHERE pid = %s', (r[0][0],))
        reads += 1
        if not cur.fetchall():
            orphans += 1
            seen.append((w, j))
    out.put({'rid': rid, 'reads': reads, 'orphans': orphans, 'orphan_families': sorted(set(seen))})


def relay_once(cur, min_age):
    """Applies the remaining levels of every outbox row older than min_age seconds, in order, and
    deletes the row; returns the number of updates completed."""
    cur.execute('SELECT oid, payload FROM outbox')
    done = 0
    for oid, payload in cur.fetchall():
        p = json.loads(payload)
        if time.time() - p['ts'] < min_age:
            continue
        for lvl in p['levels'][1:]:
            for grp in lvl:
                for s, a in grp:
                    cur.execute(s, a)
        cur.execute(B.OUTBOX_DEL, (oid,))
        done += 1
    return done


def check(cur, used):
    """Families whose child points to an absent parent (child read first, then the parents)."""
    cs = [f[0] for f in used]
    cur.execute('SELECT cid, pid FROM child WHERE cid = ANY(%s)', (cs,))
    pid_of = dict(cur.fetchall())
    cur.execute('SELECT pid FROM parent WHERE pid = ANY(%s)', (list(set(pid_of.values())),))
    present = {r[0] for r in cur.fetchall()}
    return sum(1 for c in cs if pid_of.get(c) not in present)


def main():
    method, block = sys.argv[1], int(sys.argv[2])
    W = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    R = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    T = float(sys.argv[5]) if len(sys.argv) > 5 else 12.0
    B.reset('citus')
    node_of = B.load_keymap('citus')
    fams = families(node_of, W)
    loader = B.Citus()
    loader.bulk([P for fam in fams for _, P, _ in fam], [(c, P) for fam in fams for c, P, _ in fam])
    proxy = FaultProxy(b'COMMIT PREPARED' if method == 'a' else b'COMMIT')
    plain_conninfo(True)
    route_fault_node(PROXY_PORT)
    try:
        ctx = mp.get_context('fork')
        go, out = ctx.Event(), ctx.Queue()
        t_stop = ctx.Value('d', 1e18)
        recent = ctx.Array('i', [-1] * W)
        ps = [ctx.Process(target=writer, args=(method, w, fams[w], node_of, recent, out, go, t_stop)) for w in range(W)]
        ps += [ctx.Process(target=reader, args=(r, W, fams, recent, out, go, t_stop)) for r in range(R)]
        for p in ps:
            p.start()
        time.sleep(3)
        t0 = time.time()
        t_stop.value = t0 + T
        go.set()
        relay_db = psycopg2.connect(COORD)
        relay_db.autocommit = True
        rcur = relay_db.cursor()
        armed = relayed = 0
        nxt = t0 + WARM
        while time.time() < t0 + T:
            if time.time() >= nxt:
                proxy.armed.set()
                armed += 1
                nxt += ARM
            if method == 'L':
                relayed += relay_once(rcur, 2.0)
            time.sleep(0.05)
        proxy.armed.clear()
        res = [out.get(timeout=120) for _ in range(W + R)]
        for p in ps:
            p.join(timeout=30)
        status = {}
        for r in res:
            if 'wid' in r:
                for i, s_ in r['status'].items():
                    status[(r['wid'], int(i))] = s_
        used = [fams[w][i] for (w, i) in status]
        series = []
        t1 = time.time()
        while True:
            n = check(rcur, used)
            series.append((round(time.time() - t1, 1), n))
            if n == 0 or time.time() - t1 > WAIT:
                break
            time.sleep(0.5)
        if method == 'L':
            relayed += relay_once(rcur, 0.0)
        cur_par = set(r[0] for r in sql('SELECT pid FROM parent WHERE pid = ANY(%s)',
                                        ([x for f in used for x in (f[1], f[2])],), True))
        pid_of = dict(sql('SELECT cid, pid FROM child WHERE cid = ANY(%s)', ([f[0] for f in used],), True))
        complete = sum(1 for c, P, P2 in used if pid_of.get(c) == P2 and P2 in cur_par and P not in cur_par)
        untouched = sum(1 for c, P, P2 in used if pid_of.get(c) == P and P in cur_par and P2 not in cur_par)
        counts = {}
        for s_ in status.values():
            counts[s_] = counts.get(s_, 0) + 1
        print(json.dumps({
            'design': 'x7_fault', 'method': method, 'block': block, 'W': W, 'R': R, 'T': T, 'route': B.ROUTE,
            'start': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(t0)), 'loadavg': os.getloadavg()[0],
            'updates': len(status), 'status': counts, 'armed': armed, 'fired': len(proxy.fired),
            'reads': sum(r.get('reads', 0) for r in res), 'orphan_reads': sum(r.get('orphans', 0) for r in res),
            'orphan_families_seen_by_readers': len({tuple(x) for r in res for x in r.get('orphan_families', [])}),
            'orphans_after_writes': series[0][1], 'orphan_series': series[:5] + series[-2:],
            'secs_until_no_orphan': series[-1][0] if series[-1][1] == 0 else None,
            'relayed': relayed, 'complete': complete, 'untouched': untouched,
            'other_state': len(used) - complete - untouched,
            'outbox_left': sql('SELECT count(*) FROM outbox', fetch=True)[0][0],
            'errors': {m: sum(r.get('msgs', {}).get(m, 0) for r in res) for r in res for m in r.get('msgs', {})},
            'prepared_left_on_faulty_worker': prepared_on_worker(),
        }), flush=True)
    finally:
        route_fault_node(FAULT_PORT)
        plain_conninfo(False)


if __name__ == '__main__':
    main()
