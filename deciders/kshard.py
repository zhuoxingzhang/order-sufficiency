"""Theorem 5.3 (admissible shard keys for class K) under hash placement with co-location groups:
bounded SAT check on random schemas (Appendix A.4).

Model.  Every relation R has a shard key SK(R), a nonempty tuple of frozen attributes, and a
co-location group grp(R).  The relations of one group share the map from shard-key values to
shards, so two entities of one group lie on one shard whenever their shard-key values agree; under
hash placement this is the only guarantee.  The finest placement (one shard per group and value)
is therefore the worst case: an update executable there is executable under every placement.
An update is executable under SK if its changed entities can be written in steps that each write
entities of one shard atomically, such that the state after every step is legal (Section 5.2).

Predictions, per IND rho = R[P D] <= S[L C] of a class K schema (L = PK(S)):
  carries(rho)  grp(R) = grp(S) and Sigma |= R[P SK(R)] <= S[L SK(S)], that is, every entity of R
                shares its shard-key value with the entity it references through rho
  stable(rho)   condition (4) of Theorem 4.5: at every position C_i in Fz, or P <= Fz and D_i in Fz
  arbitrary     every modification update is executable under SK  iff  every unstable IND carries
                the shard key (Theorem 5.3)
  fresh         every fresh modification update is executable under SK  iff  every IND that has a
                position with C_i and D_i both mutable carries the shard key (the necessary condition
                of Appendix A.4; the check finds that it is not sufficient)
Check.  Random class K schemas (selfcheck.schema('K', seed)), a sample of freezes, a sample of shard
keys of one attribute and groups (half of them biased towards carrying), and a bounded SAT search
(pycosat) for an update that no sequence of single-shard steps executes under the finest placement.
A predicted 'yes' must have no counterexample within the bounds; a predicted 'no' should have one,
else it is reported as unconfirmed (kshard_recheck.py).  With 'sanity', a tenth of the tasks also
check that one shard per entity reproduces ksat.py.
Usage: python3 -I kshard.py <schemas> <seed> <procs> <N> <D> <out.jsonl> [sanity]"""
import itertools
import json
import os
import random
import sys
import time
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pycosat
import deciders as Dc
import ksat
import fsat
from kmodel import Sch
from selfcheck import schema as gen_schema, frozen_sets


def to_sch(spec):
    ok, why, parsed = Dc.parse_K(spec)
    assert ok, why
    inds = [{'R': R, 'P': P, 'D': D, 'S': S, 'C': C} for (R, P, D, S, C) in parsed]
    return Sch(spec['rels'], spec['pk'], inds), parsed


# ---------------------------------------------------------------------------- conjectures
def carries(sch, d, SK, grp):
    R, S = d['R'], d['S']
    if grp[R] != grp[S] or len(SK[R]) != len(SK[S]):
        return False
    return sch.implies_ind(R, tuple(d['P']) + tuple(SK[R]), S, tuple(sch.pk[S]) + tuple(SK[S]))


def stable(d, Fz):
    R, S = d['R'], d['S']
    return all(c in Fz[S] or (set(d['P']) <= Fz[R] and x in Fz[R]) for c, x in zip(d['C'], d['D']))


def mm(d, Fz):
    return any(c not in Fz[d['S']] and x not in Fz[d['R']] for c, x in zip(d['C'], d['D']))


def predict(sch, Fz, SK, grp):
    arb = all(stable(d, Fz) or carries(sch, d, SK, grp) for d in sch.inds)
    fr = all(not mm(d, Fz) or carries(sch, d, SK, grp) for d in sch.inds)
    return arb, fr


# ---------------------------------------------------------------------------- SAT with shard steps
def build(sch, Fz, chg, ucap, D, fresh, SK, grp):
    e = ksat.Enc()
    rels = list(sch.rels)
    slots = [(r, True) for r in chg] + [(r, False) for r in rels for _ in range(ucap)]
    k = len(chg)
    X, Y, act = [], [], []
    for (r, changed) in slots:
        xi = {a: e.onehot(D) for a in sch.rels[r]}
        yi = {a: (e.onehot(D) if changed and a not in Fz[r] else xi[a]) for a in sch.rels[r]}
        X.append(xi)
        Y.append(yi)
        act.append(e.true() if changed else e.var())
    N = len(slots)
    for i in range(k, N):
        for a in sch.rels[slots[i][0]]:
            e.cl.append([act[i], X[i][a][0]])
        if i + 1 < N and slots[i + 1][0] == slots[i][0]:
            e.cl.append([-act[i + 1], act[i]])
    e.atmost(act[k:], ucap)
    for i in range(k):
        r = slots[i][0]
        e.cl.append([-e.eq(X[i][a], Y[i][a]) for a in sch.rels[r] if a not in Fz[r]])
    for i, j in itertools.combinations(range(N), 2):
        if slots[i][0] == slots[j][0]:
            r = slots[i][0]
            e.cl.append([-act[i], -act[j], -e.AND([e.eq(X[i][a], X[j][a]) for a in sch.pk[r]])])
    if fresh:
        fa = fsat.fresh_attrs(sch)
        for i in range(k):
            r = slots[i][0]
            for a in sch.rels[r]:
                if a in Fz[r] or a not in fa[r]:
                    continue
                same = e.eq(X[i][a], Y[i][a])
                for j in range(N):
                    if j != i and slots[j][0] == r:
                        e.cl.append([same, -act[j], -e.eq(Y[i][a], X[j][a])])

    def legal(st):
        cons = []
        for i, j in itertools.combinations(range(N), 2):
            if slots[i][0] == slots[j][0]:
                r = slots[i][0]
                for U in sch.uk[r]:
                    cons.append(-e.AND([act[i], act[j]] + [e.eq(st[i][a], st[j][a]) for a in U]))
        for d in sch.inds:
            for i in range(N):
                if slots[i][0] != d['R']:
                    continue
                w = [e.AND([act[j]] + [e.eq(st[i][x], st[j][y]) for x, y in zip(d['X'], d['Y'])])
                     for j in range(N) if slots[j][0] == d['S']]
                cons.append(e.OR([-act[i], e.OR(w)]))
        return e.AND(cons)

    # two changed entities share a shard iff their groups agree and so do their shard-key values
    same = {}
    for i, j in itertools.combinations(range(k), 2):
        ri, rj = slots[i][0], slots[j][0]
        if grp[ri] != grp[rj] or len(SK[ri]) != len(SK[rj]):
            same[i, j] = -e.true()
        else:
            same[i, j] = e.AND([e.eq(X[i][a], X[j][b]) for a, b in zip(SK[ri], SK[rj])])

    def oneshard(T):
        idx = [b for b in range(k) if T >> b & 1]
        return e.AND([same[i, j] for i, j in itertools.combinations(idx, 2)])

    states = {}
    full = (1 << k) - 1
    for m in range(full + 1):
        st = [Y[i] if i < k and m >> i & 1 else X[i] for i in range(N)]
        states[m] = legal(st)
    e.cl.append([states[0]])
    e.cl.append([states[full]])
    reach = {0: e.true()}
    for m in sorted(range(1, full + 1), key=lambda m: bin(m).count('1')):
        pre = []
        T = m
        while T:
            pre.append(e.AND([oneshard(T), reach[m & ~T]]))
            T = (T - 1) & m
        pre = e.OR(pre)
        reach[m] = pre if m == full else e.AND([states[m], pre])
    e.cl.append([-reach[full]])
    return e, slots, X, Y, act


def search(sch, Fz, N, D, fresh, SK, grp, kmin=2):
    mut = [r for r in sch.rels if any(a not in Fz[r] for a in sch.rels[r])]
    for k in range(kmin, N + 1):
        for chg in itertools.combinations_with_replacement(mut, k):
            e, slots, X, Y, act = build(sch, Fz, list(chg), N - k, D, fresh, SK, grp)
            sol = pycosat.solve(e.cl)
            if sol != 'UNSAT':
                return (k, ksat.decode(sol, slots, X, Y, act, sch))
    return None


# ---------------------------------------------------------------------------- tasks
def placements(sch, Fz, rng, n):
    """Up to n (SK, grp) pairs: half uniform, half biased so that some INDs carry the key."""
    rels = sorted(sch.rels)
    out, seen = [], set()
    tries = 0
    while len(out) < n and tries < 20 * n:
        tries += 1
        SK, grp = {}, {}
        if len(out) % 2 == 0:
            for r in rels:
                SK[r] = (rng.choice(sorted(Fz[r])),)
                grp[r] = rng.randrange(len(rels))
        else:
            # pick one IND and make it carry through a frozen aligned position, if any
            for r in rels:
                SK[r] = (rng.choice(sorted(Fz[r])),)
                grp[r] = 0 if rng.random() < 0.7 else rng.randrange(1, len(rels) + 1)
            d = rng.choice(sch.inds)
            pos = [(x, y) for x, y in zip(d['X'], d['Y']) if x in Fz[d['R']] and y in Fz[d['S']]]
            if pos:
                x, y = rng.choice(pos)
                SK[d['R']], SK[d['S']] = (x,), (y,)
                grp[d['R']] = grp[d['S']]
        key = json.dumps([sorted(SK.items()), sorted(grp.items())])
        if key not in seen:
            seen.add(key)
            out.append((SK, grp))
    return out


def job(args):
    name, spec, Fz, SK, grp, N, Dv, sanity = args
    sch, parsed = to_sch(spec)
    pa, pf = predict(sch, Fz, SK, grp)
    t = time.time()
    ca = search(sch, Fz, N, Dv, False, SK, grp)
    cf = search(sch, Fz, N, Dv, True, SK, grp)
    res = {'name': name, 'Fz': {r: sorted(v) for r, v in Fz.items()}, 'SK': SK, 'grp': grp,
           'pred_arb': pa, 'pred_fresh': pf, 'cex_arb': ca, 'cex_fresh': cf}
    if sanity:
        # one shard per entity must reproduce ksat / fsat
        SK1 = {r: tuple(sch.pk[r]) for r in sch.rels}
        g1 = {r: i for i, r in enumerate(sorted(sch.rels))}
        a1 = search(sch, Fz, N, Dv, False, SK1, g1) is None
        b1 = ksat.search(sch, Fz, N, N, Dv, 'nonexec') is None
        res['sanity'] = (a1 == b1)
    res['sec'] = round(time.time() - t, 2)
    return res


def tasks(n, seed, N, Dv, sanity):
    out = []
    rng = random.Random(seed)
    seen = set()
    s = seed
    while len(seen) < n:
        s += 1
        spec = gen_schema('K', s)
        key = json.dumps(spec, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        sch, _ = to_sch(spec)
        fzs = list(frozen_sets(spec))
        if len(fzs) > 12:
            fzs = rng.sample(fzs, 12)
        for Fz in fzs:
            for SK, grp in placements(sch, Fz, rng, 4):
                out.append(('s%d' % s, spec, Fz, SK, grp, N, Dv, sanity and rng.random() < 0.1))
    return out


def main():
    n, seed, procs, N, Dv, path = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), \
        int(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
    sanity = len(sys.argv) > 7
    ts = tasks(n, seed, N, Dv, sanity)
    print('tasks', len(ts), flush=True)
    agg = {}
    t0 = time.time()
    with Pool(procs) as pool, open(path, 'w') as f:
        for c, res in enumerate(pool.imap_unordered(job, ts, chunksize=2), 1):
            f.write(json.dumps(res) + '\n')
            f.flush()
            for kind, p, cex in (('arbitrary', res['pred_arb'], res['cex_arb']),
                                 ('fresh', res['pred_fresh'], res['cex_fresh'])):
                key = (kind, p, cex is None)
                agg[key] = agg.get(key, 0) + 1
            if 'sanity' in res:
                agg[('sanity', True, res['sanity'])] = agg.get(('sanity', True, res['sanity']), 0) + 1
            if c % 200 == 0:
                print('done', c, round(time.time() - t0), flush=True)
    for k in sorted(agg, key=str):
        tag = 'ok' if k[1] == k[2] else ('UNCONFIRMED' if not k[1] else 'COUNTEREXAMPLE')
        print('%s predicted=%s within-bounds=%s : %d  %s' % (k[0], k[1], k[2], agg[k], tag), flush=True)


if __name__ == '__main__':
    main()
