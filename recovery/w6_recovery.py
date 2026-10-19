"""Recovery runs of Section 6.3 (Proposition 5.8, prop:recovery) on Citus, every transaction sent
straight to the worker that holds its rows, as leveled commits are in the cost runs (X7_ROUTE=direct).

An update U merges group P into a new group P2, the reparent template of Ex. 5.6 with one child c:
insert P2, point c to P2, delete P.  P, P2 and c sit on three different workers (all six
assignments in turn), so U commits in three rounds: P2, then c, then P.  As Sec. 5.3 prescribes, U
locks P (FOR UPDATE, in the transaction of its last round) before it reads the children of P, and
the transaction of its first round also writes an outbox row.  The row stores what U read and
writes: every changed entity with its worker, round, old and new tuple, and the children of every
changed group that U read.

A crash stops U after round 1 or after round 2: the client loses the connections of its open
transactions, so the workers abort them and release their locks.  A conflicting update K runs, and
a relay then completes U from its outbox row:
  replay    the relay of cost/x7_fault.py: commits the remaining rounds in order with idempotent
            writes that set the new tuples, and reads nothing
  validate  prop:recovery: opens one transaction per worker, locks and reads every changed entity
            (FOR UPDATE), then reads the children of every changed group, and looks for the prefix
            state of U that these tuples match.  It aborts if they match none; otherwise it executes
            the remaining rounds in the open transactions and commits them round by round, and the
            transactions that only hold locks commit after the last round.
Conflicting updates K, each as leveled commits with the locks of Sec. 5.3:
  K0  none
  K1  move c to a new group P3 (insert P3, then point c to P3)          replay loses this update
  K2  delete the group P2 if it has no child (lock P2, read its children, delete it); after round 1
                                                                       replay then orphans c
  K3  insert a child c2 into P under the fence of Sec. 5.3 (P FOR KEY SHARE in a transaction that
      commits after c2)                                                 replay then orphans c2
Modes:
  staged  K runs to completion after the crash and before the relay
  race    K starts at a random offset in [-2, +4] ms from the start of the relay, in a second thread
          with its own connections, so that it overlaps the relay
R reader processes follow assumption 5 on the latest families of the current cell: U writes P2, c,
P, so a reader reads P, then c, then the group of c (unless it is P), and it reads c2 before its
group; a child whose group the reader saw absent is an orphan.  (A reader that reads c before P is
not one of assumption 5: between its two reads, round 2 and round 3 may both commit.)  After every
trial an audit reads the final state: orphans, a lost update (K1 committed, but c does not point to
P3), and whether U completed.

usage: python -I w6_recovery.py MODE BLOCK N [R [OUT]]
  runs all cells (conflict, crash point, relay) of MODE in a random order, N trials per cell, and
  prints one JSON line per cell, or appends it to OUT and skips the cells that OUT already has.
  MODE smoke runs the staged cells with N trials and exits non-zero unless every outcome is the
  expected one.
"""
import json, multiprocessing as mp, os, random, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import w6lib as L  # noqa: E402

LOCK = {'p': 'SELECT pid, ts FROM w6_parent WHERE pid = %s FOR UPDATE',
        'c': 'SELECT cid, pid FROM w6_child WHERE cid = %s FOR UPDATE'}
FENCE = 'SELECT pid FROM w6_parent WHERE pid = %s FOR KEY SHARE'
KIDS = 'SELECT cid, pid FROM w6_child WHERE pid = ANY(%s)'
STAGED = [('K0', 1), ('K0', 2), ('K1', 1), ('K1', 2), ('K2', 1), ('K3', 1), ('K3', 2)]
RACE = [('K1', 1), ('K1', 2), ('K2', 1), ('K3', 1), ('K3', 2)]
RELAYS = ('replay', 'validate')


# ---------------------------------------------------------------- the outbox row (pure functions)
def merge_payload(f, kids_of_P):
    """What U stores in the outbox: changed entities (t = p group / c child, k key, n worker, r round,
    b old tuple, a new tuple; None = absent) and the children of every changed group that U read."""
    return {'rounds': 3, 'oid': f['oid'], 'onode': f['B'],
            'ents': [{'t': 'p', 'k': f['P2'], 'n': f['B'], 'r': 1, 'b': None, 'a': [f['P2'], 0]},
                     {'t': 'c', 'k': f['c'], 'n': f['C'], 'r': 2, 'b': [f['c'], f['P']], 'a': [f['c'], f['P2']]},
                     {'t': 'p', 'k': f['P'], 'n': f['A'], 'r': 3, 'b': [f['P'], 0], 'a': None}],
            'kids': {str(f['P']): sorted(kids_of_P), str(f['P2']): []}}


def tuple_at(e, i):
    """Tuple of a changed entity in the prefix state after round i."""
    return e['a'] if e['r'] <= i else e['b']


def kids_at(p, g, i):
    """Children of group g in the prefix state after round i: those that U read and did not change,
    and the changed children that point to g then."""
    ch = {e['k']: e for e in p['ents'] if e['t'] == 'c'}
    out = {c for c in p['kids'].get(str(g), []) if c not in ch}
    for c, e in ch.items():
        t = tuple_at(e, i)
        if t is not None and t[1] == g:
            out.add(c)
    return out


def match_prefix(p, obs, okids):
    """obs: {(t, key): tuple as a list, or None}; okids: {group: set of children}.  The round i
    (1..rounds) whose prefix state these tuples match, or None."""
    groups = [e['k'] for e in p['ents'] if e['t'] == 'p']
    for i in range(1, p['rounds'] + 1):
        if all(obs[(e['t'], e['k'])] == tuple_at(e, i) for e in p['ents']) and \
                all(okids.get(g, set()) == kids_at(p, g, i) for g in groups):
            return i
    return None


def write_of(e, idempotent=False):
    """The write that gives a changed entity its new tuple."""
    if e['t'] == 'c':
        return (L.UPD_C, (e['a'][1], e['k']))
    if e['a'] is None:
        return (L.DEL_P, (e['k'],))
    assert e['b'] is None, 'groups are only inserted or deleted here'
    return (L.INS_P_IDEM if idempotent else L.INS_P, (e['k'],))


# ---------------------------------------------------------------- database steps
def read_kids(coord, groups):
    cur = coord.cursor()
    cur.execute(KIDS, (list(groups),))
    out = {g: set() for g in groups}
    for c, g in cur.fetchall():
        out[g].add(c)
    return out


def run_update(par, coord, f, crash):
    """U as leveled commits, stopped by a crash after round `crash`; returns the outbox payload."""
    A, B, C = f['A'], f['B'], f['C']
    L.chk(par.run([(A, [('BEGIN', None), (LOCK['p'], (f['P'],))])]), 'U lock')
    assert par.fetch(A) == [(f['P'], 0)], 'P missing'
    kids = read_kids(coord, [f['P']])[f['P']]
    assert kids == {f['c']}, kids
    p = merge_payload(f, kids)
    L.chk(par.run([(B, [('BEGIN', None), (L.INS_P, (f['P2'],)), (L.OB_INS, (f['oid'], json.dumps(p)))]),
                   (C, [('BEGIN', None), (L.UPD_C, (f['P2'], f['c']))]),
                   (A, [(L.DEL_P, (f['P'],))])]), 'U execution')
    L.chk(par.run([(B, [('COMMIT', None)])]), 'U round 1')
    if crash == 1:
        par.close([A, C])
    else:
        L.chk(par.run([(C, [('COMMIT', None)])]), 'U round 2')
        par.close([A])
    par.reconnect()
    return p


def conflict(kind, par, coord, f):
    """The conflicting update; returns 'applied', 'rejected' or 'none'."""
    A, B, C = f['A'], f['B'], f['C']
    if kind == 'K0':
        return 'none'
    if kind == 'K1':           # P3 sits on A, c on C: two rounds
        L.chk(par.run([(A, [('BEGIN', None), (L.INS_P, (f['P3'],))]),
                       (C, [('BEGIN', None), (L.UPD_C, (f['P3'], f['c']))])]), 'K1 execution')
        L.chk(par.run([(A, [('COMMIT', None)])]), 'K1 round 1')
        L.chk(par.run([(C, [('COMMIT', None)])]), 'K1 round 2')
        return 'applied'
    if kind == 'K2':
        L.chk(par.run([(B, [('BEGIN', None), (LOCK['p'], (f['P2'],))])]), 'K2 lock')
        if not par.fetch(B) or read_kids(coord, [f['P2']])[f['P2']]:
            L.chk(par.rollback([B]))
            return 'rejected'
        L.chk(par.run([(B, [(L.DEL_P, (f['P2'],)), ('COMMIT', None)])]), 'K2 delete')
        return 'applied'
    # K3: the fence on P (A) and the child c2 (B) execute in parallel, the child commits first
    L.chk(par.run([(A, [('BEGIN', None), (FENCE, (f['P'],))]),
                   (B, [('BEGIN', None), (L.INS_C, (f['c2'], f['P']))])]), 'K3 execution')
    if not par.fetch(A):
        L.chk(par.rollback([A, B]))
        return 'rejected'
    L.chk(par.run([(B, [('COMMIT', None)])]), 'K3 child')
    L.chk(par.run([(A, [('COMMIT', None)])]), 'K3 fence')
    return 'applied'


def relay_replay(par, coord, p):
    """The relay of x7_fault.py: the remaining rounds (2..d) in order, idempotent writes, nothing read."""
    for r in range(2, p['rounds'] + 1):
        by = {}
        for e in p['ents']:
            if e['r'] == r:
                by.setdefault(e['n'], []).append(write_of(e, idempotent=True))
        L.chk(par.run([(n, [('BEGIN', None)] + ws) for n, ws in by.items()]), f'replay round {r}')
        L.chk(par.run([(n, [('COMMIT', None)]) for n in by]), f'replay commit {r}')
    L.chk(par.run([(p['onode'], [(L.OB_DEL, (p['oid'],))])]), 'outbox delete')
    return 'completed', None


def relay_validate(par, coord, p):
    """prop:recovery: lock and read, find the prefix state, abort or complete."""
    ents = p['ents']
    by = {}
    for e in ents:
        by.setdefault(e['n'], []).append(e)
    nodes = sorted(by)
    obs, step = {}, 0
    while True:                      # one lock per worker and message, all workers in parallel
        jobs = [(n, ([('BEGIN', None)] if step == 0 else []) + [(LOCK[es[step]['t']], (es[step]['k'],))])
                for n, es in by.items() if step < len(es)]
        if not jobs:
            break
        errs = par.run(jobs)
        if errs:
            par.rollback(nodes)
            L.chk(errs, 'validate lock')
        for n, _ in jobs:
            e, rows = by[n][step], par.fetch(n)
            obs[(e['t'], e['k'])] = list(rows[0]) if rows else None
        step += 1
    okids = read_kids(coord, [e['k'] for e in ents if e['t'] == 'p'])
    i = match_prefix(p, obs, okids)
    if i is None or i == p['rounds']:
        L.chk(par.run([(n, [('ROLLBACK' if i is None else 'COMMIT', None)]) for n in nodes]), 'validate end')
        L.chk(par.run([(p['onode'], [(L.OB_DEL, (p['oid'],))])]), 'outbox delete')
        return ('aborted' if i is None else 'done_before'), i
    rem = [e for e in ents if e['r'] > i]
    cr = {}
    for e in rem:
        cr.setdefault(e['n'], set()).add(e['r'])
    assert all(len(rs) == 1 for rs in cr.values()), 'a worker holds two remaining rounds'
    cr = {n: rs.pop() for n, rs in cr.items()}
    L.chk(par.run([(n, [write_of(e) for e in rem if e['n'] == n]) for n in cr]), 'validate execution')
    for r in range(i + 1, p['rounds'] + 1):
        ns = [n for n in cr if cr[n] == r]
        if ns:
            L.chk(par.run([(n, [('COMMIT', None)]) for n in ns]), f'validate round {r}')
    lock_only = [n for n in nodes if n not in cr]
    if lock_only:                    # their locks fence the groups that the remaining rounds point to
        L.chk(par.run([(n, [('COMMIT', None)]) for n in lock_only]), 'validate fences')
    L.chk(par.run([(p['onode'], [(L.OB_DEL, (p['oid'],))])]), 'outbox delete')
    return 'completed', i


RELAY = {'replay': relay_replay, 'validate': relay_validate}


def audit(coord, f, kind, kres):
    cur = coord.cursor()
    cur.execute('SELECT pid FROM w6_parent WHERE pid = ANY(%s)', ([f['P'], f['P2'], f['P3']],))
    present = {r[0] for r in cur.fetchall()}
    cur.execute('SELECT cid, pid FROM w6_child WHERE cid = ANY(%s)', ([f['c'], f['c2']],))
    pid_of = dict(cur.fetchall())
    orphans = [x for x in (f['c'], f['c2']) if x in pid_of and pid_of[x] not in present]
    return {'orphans': len(orphans),
            'lost': int(kind == 'K1' and kres == 'applied' and pid_of.get(f['c']) != f['P3']),
            'u_complete': int(pid_of.get(f['c']) == f['P2'] and f['P2'] in present and f['P'] not in present)}


# ---------------------------------------------------------------- families and readers
PERMS = [(0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)]


def families(node_of, n, seed):
    """Per trial: P on A, P2 and the outbox row on B, c on C (A, B, C distinct, all six assignments
    in turn), P3 on A (K1), c2 on B (K3)."""
    pools = L.Pools(node_of, 1, 1 + 8 * n + 3000)
    out = []
    for j in range(n):
        A, B, C = PERMS[(j + seed) % 6]
        out.append({'A': A, 'B': B, 'C': C, 'P': pools.take(A), 'P2': pools.take(B), 'c': pools.take(C),
                    'P3': pools.take(A), 'c2': pools.take(B), 'oid': pools.take(B)})
    return out


def reader(rid, keys, nkeys, stop, out):
    """Assumption 5 on one of the latest families: U writes P2, c, P, so the reader reads P, then c,
    then the group of c unless it is P; c2 (written by K3 under the fence on P) is read before its
    group.  An orphan is a child whose group the reader saw absent."""
    coord = L.connect(0, f'w6r{rid}')
    cur = coord.cursor()
    rnd = random.Random(rid)
    reads = orphan_reads = 0
    seen = set()

    def present(g):
        cur.execute('SELECT 1 FROM w6_parent WHERE pid = %s', (g,))
        return bool(cur.fetchall())

    def group_of(x):
        cur.execute('SELECT pid FROM w6_child WHERE cid = %s', (x,))
        r = cur.fetchall()
        return r[0][0] if r else None

    while not stop.is_set():
        k = nkeys.value
        if k == 0:
            time.sleep(0.001)
            continue
        j = k - 1 - rnd.randrange(min(k, 25))
        P, c, c2 = keys[3 * j], keys[3 * j + 1], keys[3 * j + 2]
        p_here = present(P)
        g = group_of(c)
        if g is not None:
            reads += 1
            if not (p_here if g == P else present(g)):
                orphan_reads += 1
                seen.add(c)
        g2 = group_of(c2)
        if g2 is not None:
            reads += 1
            if not present(g2):
                orphan_reads += 1
                seen.add(c2)
    out.put({'reads': reads, 'orphan_reads': orphan_reads, 'orphan_children_seen': len(seen)})


# ---------------------------------------------------------------- one cell
def run_cell(mode, kind, crash, relay, n, R, seed, node_of):
    L.setup_tables()
    fams = families(node_of, n, seed)
    coord = L.connect(0, 'w6rec')
    cur = coord.cursor()
    L.copy_rows(cur, 'w6_parent', ['pid'], [(f['P'],) for f in fams])
    L.copy_rows(cur, 'w6_child', ['cid', 'pid'], [(f['c'], f['P']) for f in fams])
    coord.close()
    ctx = mp.get_context('fork')
    keys, nkeys, stop, q = ctx.Array('q', 3 * n), ctx.Value('i', 0), ctx.Event(), ctx.Queue()
    rs = [ctx.Process(target=reader, args=(r, keys, nkeys, stop, q)) for r in range(R)]
    for p in rs:
        p.start()
    coord, coord_k = L.connect(0, 'w6rec'), L.connect(0, 'w6k')
    par_u, par_k, par_r = L.Par('w6u'), L.Par('w6k'), L.Par('w6relay')
    rnd = random.Random(seed)
    res = {'relay': {}, 'conflict': {}, 'prefix_found': {}, 'orphan_trials': 0, 'orphans': 0, 'lost': 0,
           'u_complete': 0}
    lat_u, lat_k, lat_r, deltas = [], [], [], []
    for j, f in enumerate(fams):
        keys[3 * j], keys[3 * j + 1], keys[3 * j + 2] = f['P'], f['c'], f['c2']
        nkeys.value = j + 1
        t0 = time.perf_counter()
        p = run_update(par_u, coord, f, crash)
        t1 = time.perf_counter()
        lat_u.append(t1 - t0)
        if mode == 'race':
            box = {}
            delta = rnd.uniform(-0.002, 0.004)
            go = time.perf_counter() + 0.002

            def kthread():
                L.wait_until(go + delta)
                tk = time.perf_counter()
                box['k'] = conflict(kind, par_k, coord_k, f)
                box['lat'] = time.perf_counter() - tk
            th = threading.Thread(target=kthread)
            th.start()
            L.wait_until(go)
            t2 = time.perf_counter()
            out, i = RELAY[relay](par_r, coord, p)
            t3 = time.perf_counter()
            th.join()
            kres = box['k']
            lat_k.append(box['lat'])
            deltas.append(delta)
        else:
            tk = time.perf_counter()
            kres = conflict(kind, par_k, coord_k, f)
            lat_k.append(time.perf_counter() - tk)
            t2 = time.perf_counter()
            out, i = RELAY[relay](par_r, coord, p)
            t3 = time.perf_counter()
        lat_r.append(t3 - t2)
        a = audit(coord, f, kind, kres)
        res['relay'][out] = res['relay'].get(out, 0) + 1
        res['conflict'][kres] = res['conflict'].get(kres, 0) + 1
        res['prefix_found'][str(i)] = res['prefix_found'].get(str(i), 0) + 1
        res['orphan_trials'] += a['orphans'] > 0
        res['orphans'] += a['orphans']
        res['lost'] += a['lost']
        res['u_complete'] += a['u_complete']
    time.sleep(0.2)                       # readers see the last family
    stop.set()
    rr = [q.get(timeout=60) for _ in rs]
    for p in rs:
        p.join(timeout=10)
    cur = coord.cursor()
    cur.execute('SELECT count(*) FROM w6_outbox')
    outbox_left = cur.fetchone()[0]
    ms = lambda v, qq: round(L.pct(v, qq) * 1000, 3) if v else None  # noqa: E731
    res.update({'design': 'w6_recovery', 'mode': mode, 'kind': kind, 'crash': crash, 'relay': relay,
                'relay_outcome': res.pop('relay'), 'trials': n, 'R': R, 'seed': seed,
                'start': time.strftime('%Y-%m-%dT%H:%M:%S'), 'loadavg': os.getloadavg()[0],
                'reads': sum(r['reads'] for r in rr), 'orphan_reads': sum(r['orphan_reads'] for r in rr),
                'orphan_children_seen': sum(r['orphan_children_seen'] for r in rr),
                'relay_p50_ms': ms(lat_r, 0.5), 'relay_p95_ms': ms(lat_r, 0.95), 'relay_max_ms': ms(lat_r, 1.0),
                'relay_mean_ms': round(sum(lat_r) / len(lat_r) * 1000, 3),
                'update_p50_ms': ms(lat_u, 0.5), 'conflict_p50_ms': ms(lat_k, 0.5),
                'delta_ms_range': [round(min(deltas) * 1000, 2), round(max(deltas) * 1000, 2)] if deltas else None,
                'outbox_left': outbox_left})
    for c in (coord, coord_k):
        c.close()
    for par in (par_u, par_k, par_r):
        par.close(range(3))
    return res


def expected(kind, crash, relay):
    """Outcome of a staged cell that prop:recovery and the relay of x7_fault.py predict (smoke test)."""
    if relay == 'validate':
        return {'orphans': 0, 'lost': 0, 'relay': 'completed' if kind == 'K0' else 'aborted'}
    # the relay of x7_fault.py does not know where U stopped and replays rounds 2..d, so it also rewrites c after
    # a crash after round 2
    return {'orphans': int(kind in ('K2', 'K3')), 'lost': int(kind == 'K1'), 'relay': 'completed'}


def main():
    mode, block, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    R = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    out = sys.argv[5] if len(sys.argv) > 5 else None     # append there, skipping the cells done
    m = 'race' if mode == 'race' else 'staged'
    done = set()
    if out and os.path.exists(out):
        for line in open(out):
            r = json.loads(line)
            done.add((r['mode'], r['kind'], r['crash'], r['relay'], r['block']))
    L.setup_tables()
    node_of = L.node_map()
    cells = [(k, c, r) for k, c in (RACE if mode == 'race' else STAGED) for r in RELAYS]
    random.Random(9000 + block).shuffle(cells)
    bad = []
    for ci, (kind, crash, relay) in enumerate(cells):
        if (m, kind, crash, relay, block) in done:
            continue
        seed = 1000 * block + ci
        r = run_cell(m, kind, crash, relay, n, R, seed, node_of)
        r['block'] = block
        if mode == 'smoke':
            e = expected(kind, crash, relay)
            ok = (r['orphans'] == e['orphans'] * n and r['lost'] == e['lost'] * n and
                  r['relay_outcome'] == {e['relay']: n} and r['outbox_left'] == 0 and
                  (relay == 'replay' or r['orphan_reads'] == 0))
            if not ok:
                bad.append((kind, crash, relay, e, r))
            r['smoke_ok'] = ok
        if out:
            with open(out, 'a') as f:
                f.write(json.dumps(r) + '\n')
            print(f'[rec {m} blk {block}] {kind} crash {crash} {relay}: relay {r["relay_outcome"]} '
                  f'orphans {r["orphans"]} lost {r["lost"]} orphan reads {r["orphan_reads"]}/{r["reads"]} '
                  f'relay p50 {r["relay_p50_ms"]} ms', flush=True)
        else:
            print(json.dumps(r), flush=True)
    if bad:
        for b in bad:
            print('[smoke] unexpected outcome', b[:4], json.dumps(b[4])[:600], file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
