"""Checks of the logic of w6_recovery.py and w6_deadlock.py without a database (run locally).

1. plan() on the merge template of the recovery runs and on the move template of the deadlock runs.
2. The recovery protocol on an in-memory instance: U stopped after round 1 or 2, a conflicting
   update K0..K3, then the replaying or the validating relay, which uses the pure functions of
   w6_recovery.py (merge_payload, match_prefix, kids_at, tuple_at).  The outcomes must equal
   w6_recovery.expected(), which the smoke test of run_recovery.sh also checks.
3. sccs() on small wait-for graphs, and Zipf frequencies.
4. A lock-manager simulation of the deadlock runs: clients execute two-row updates on rows of two
   shards as leveled commits, with random interleavings of their steps.  Blocking writes produce
   wait-for cycles whose edges lie on two shards; NOWAIT writes with release-and-retry never let a
   client wait while it holds a lock (lem:deadlock), and every update completes.
usage: python -I w6_mock.py
"""
import os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import w6lib as L  # noqa: E402
import w6_recovery as R  # noqa: E402


def check_plan():
    # merge: P2 (node 1) -> c (node 2) -> P (node 0)
    rounds = L.plan([1, 2, 0], [(0, 1), (1, 2)])
    assert rounds == [[(1, [0])], [(2, [1])], [(0, [2])]], rounds
    # move: c, p, q; edge c -> q
    assert L.plan([0, 1, 2], [(0, 2)]) == [[(0, [0]), (1, [1])], [(2, [2])]]
    assert L.plan([0, 2, 0], [(0, 2)]) == [[(0, [0, 2]), (2, [1])]]       # c and q on one node: one round
    assert L.plan([0, 2, 2], [(0, 2)]) == [[(0, [0])], [(2, [1, 2])]]     # p joins q's transaction
    # merging the nodes would create a cycle: one transaction per node and level
    r = L.plan([0, 1, 0], [(0, 1), (1, 2)])
    assert r == [[(0, [0])], [(1, [1])], [(0, [2])]], r
    print('[ok] plan')


# ---------------------------------------------------------------- recovery on an in-memory instance
class Mem:
    def __init__(self):
        self.groups, self.pid = set(), {}

    def kids(self, g):
        return {c for c, p in self.pid.items() if p == g}

    def orphans(self):
        return [c for c, p in self.pid.items() if p not in self.groups]


def mem_apply(db, e):
    if e['t'] == 'c':
        db.pid[e['k']] = e['a'][1]
    elif e['a'] is None:
        db.groups.discard(e['k'])
    else:
        db.groups.add(e['k'])


def mem_trial(kind, crash, relay):
    db = Mem()
    f = {'A': 0, 'B': 1, 'C': 2, 'P': 10, 'P2': 20, 'c': 30, 'P3': 40, 'c2': 50, 'oid': 60}
    db.groups.add(f['P'])
    db.pid[f['c']] = f['P']
    p = R.merge_payload(f, db.kids(f['P']))
    for e in p['ents']:                      # the rounds before the crash
        if e['r'] <= crash:
            mem_apply(db, e)
    assert not db.orphans(), 'prefix state illegal'
    kres = 'none'
    if kind == 'K1':
        db.groups.add(f['P3'])
        db.pid[f['c']] = f['P3']
        kres = 'applied'
    elif kind == 'K2':
        if f['P2'] in db.groups and not db.kids(f['P2']):
            db.groups.discard(f['P2'])
            kres = 'applied'
        else:
            kres = 'rejected'
    elif kind == 'K3':
        if f['P'] in db.groups:
            db.pid[f['c2']] = f['P']
            kres = 'applied'
        else:
            kres = 'rejected'
    if relay == 'replay':
        for e in p['ents']:
            if e['r'] >= 2:
                mem_apply(db, e)
        out = 'completed'
    else:
        obs = {}
        for e in p['ents']:
            k = e['k']
            if e['t'] == 'p':
                obs[(e['t'], k)] = [k, 0] if k in db.groups else None
            else:
                obs[(e['t'], k)] = [k, db.pid[k]] if k in db.pid else None
        okids = {e['k']: db.kids(e['k']) for e in p['ents'] if e['t'] == 'p'}
        i = R.match_prefix(p, obs, okids)
        assert i is None or i == crash, (i, crash)
        if i is None:
            out = 'aborted'
        else:
            for e in p['ents']:
                if e['r'] > i:
                    mem_apply(db, e)
            out = 'completed'
    lost = int(kind == 'K1' and db.pid[f['c']] != f['P3'])
    return {'orphans': len(db.orphans()), 'lost': lost, 'relay': out, 'kres': kres}


def check_recovery():
    for kind, crash in R.STAGED:
        for relay in R.RELAYS:
            got = mem_trial(kind, crash, relay)
            exp = R.expected(kind, crash, relay)
            assert {k: got[k] for k in exp} == exp, (kind, crash, relay, got, exp)
            assert got['kres'] in (('none',) if kind == 'K0' else ('applied',)), got
    # the prefix states are pairwise distinct, so the relay always knows where U stopped
    f = {'A': 0, 'B': 1, 'C': 2, 'P': 10, 'P2': 20, 'c': 30, 'P3': 40, 'c2': 50, 'oid': 60}
    p = R.merge_payload(f, {30})
    states = [tuple(str(R.tuple_at(e, i)) for e in p['ents']) for i in range(1, 4)]
    assert len(set(states)) == 3
    print('[ok] recovery outcomes (in memory) match expected() for all staged cells')


def check_sccs():
    e = {('a', 'b', 0), ('b', 'a', 1), ('c', 'd', 0), ('d', 'e', 0)}
    s = L.sccs(e)
    assert s == [(frozenset({'a', 'b'}), frozenset({0, 1}))], s
    e = {('a', 'b', 2), ('b', 'c', 2), ('c', 'a', 2), ('x', 'y', 1)}
    assert L.sccs(e) == [(frozenset({'a', 'b', 'c'}), frozenset({2}))]
    assert L.sccs(set()) == []
    z = L.Zipf(1000, 0.99, 1)
    n = 200_000
    cnt = [0] * 1000
    for _ in range(n):
        cnt[z()] += 1
    h = sum(1 / (i + 1) ** 0.99 for i in range(1000))
    assert abs(cnt[0] / n - 1 / h) < 0.01, (cnt[0] / n, 1 / h)
    print(f'[ok] sccs; Zipf top rank {cnt[0] / n:.3f} (expected {1 / h:.3f}), top 10 {sum(cnt[:10]) / n:.3f}')


# ---------------------------------------------------------------- lock-manager simulation
def simulate(mode, clients=8, rows=6, updates=300, seed=1):
    """Rows 0..rows-1, row r on shard r % 2.  An update locks two distinct rows (one per shard
    preferred) in its execution phase, one transaction per shard, steps interleaved at random; it
    commits once it holds both.  blocking: a lock request on a held row waits.  nowait: it fails, the
    update releases its locks and retries later.  Returns (completed, cycles across shards, waits
    while holding)."""
    rnd = random.Random(seed)
    owner = {}
    state = []          # per client: dict(rows, held, waiting)
    done = cross = hold_wait = 0
    todo = [updates // clients] * clients

    def new(ci):
        a = rnd.randrange(rows)
        b = rnd.randrange(rows)
        while b == a or b % 2 == a % 2:
            b = rnd.randrange(rows)
        return {'rows': [a, b], 'held': set(), 'waiting': None}

    state = [new(i) for i in range(clients)]
    steps = 0
    while done < clients * (updates // clients):
        steps += 1
        assert steps < 10 ** 6, 'no progress (livelock or deadlock without timeout)'
        # wait-for graph: client -> owner of the row it waits for (shard of the row)
        edges = {(i, owner[s['waiting']], s['waiting'] % 2) for i, s in enumerate(state)
                 if s['waiting'] is not None and s['waiting'] in owner}
        for comp, ws in L.sccs(edges):
            if len(ws) >= 2:
                cross += 1
            # a timeout breaks it: the first client of the cycle aborts
            victim = min(comp)
            for r in state[victim]['held']:
                del owner[r]
            state[victim]['held'], state[victim]['waiting'] = set(), None
        i = rnd.randrange(clients)
        if todo[i] == 0:
            continue
        s = state[i]
        want = [r for r in s['rows'] if r not in s['held']]
        if not want:                              # executed: commit both transactions
            for r in s['held']:
                del owner[r]
            todo[i] -= 1
            done += 1
            state[i] = new(i)
            continue
        r = rnd.choice(want)
        if r not in owner:
            owner[r] = i
            s['held'].add(r)
            s['waiting'] = None
        elif mode == 'blocking':
            if s['held']:
                hold_wait += 1
            s['waiting'] = r
        else:                                     # nowait: release everything and retry
            for h in s['held']:
                del owner[h]
            s['held'], s['waiting'] = set(), None
    return done, cross, hold_wait


def check_lemma():
    d, cross, hw = simulate('blocking')
    assert cross > 0 and hw > 0, (d, cross, hw)
    d2, cross2, hw2 = simulate('nowait')
    assert cross2 == 0 and hw2 == 0, (d2, cross2, hw2)
    print(f'[ok] lock simulation: blocking {cross} cross-shard cycles in {d} updates, '
          f'{hw} waits while holding; nowait {cross2} cycles, {hw2} waits while holding, {d2} updates')


if __name__ == '__main__':
    check_plan()
    check_recovery()
    check_sccs()
    check_lemma()
    print('W6_MOCK_OK')
