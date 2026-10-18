"""Bounded SAT search for fresh modification updates on class K schemas (pycosat).

Same encoding as ksat.py (imported unchanged), plus freshness: a changed entity that writes a
new value into a fresh attribute a of its relation r (an attribute of PK(r), of a unique key of r,
or of the right side of an IND into r) must write a value that no entity of r holds in a in I.
Modes as in ksat: 'nonexec' asks for an update that no order of single-entity writes executes,
'noncomm' for an update with an illegal intermediate state.  fresh=False reproduces ksat.search.
Usage as a module (python3 -I, needs pycosat)."""
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pycosat
from ksat import Enc, decode


def fresh_attrs(sch):
    """Attributes into which a fresh update writes only new values, per relation."""
    out = {r: set(sch.pk[r]) for r in sch.rels}
    for r in sch.rels:
        for U in sch.uk[r]:
            out[r] |= set(U)
    for d in sch.inds:
        out[d['S']] |= set(d['Y'])
    return out


def build(sch, Fz, chg, ucap, D, mode, fresh):
    e = Enc()
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
        fa = fresh_attrs(sch)
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

    states = {}
    full = (1 << k) - 1
    for m in range(full + 1):
        st = [Y[i] if i < k and m >> i & 1 else X[i] for i in range(N)]
        states[m] = legal(st)
    e.cl.append([states[0]])
    e.cl.append([states[full]])
    if mode == 'noncomm':
        e.cl.append([-states[m] for m in range(1, full)])
    elif mode == 'seq':
        # the changed slots are written in the order of chg; some prefix state is illegal
        e.cl.append([-states[(1 << t) - 1] for t in range(1, k)])
    else:
        reach = {0: e.true()}
        for m in sorted(range(1, full + 1), key=lambda m: bin(m).count('1')):
            pre = e.OR([reach[m & ~(1 << b)] for b in range(k) if m >> b & 1])
            reach[m] = pre if m == full else e.AND([states[m], pre])
        e.cl.append([-reach[full]])
    return e, slots, X, Y, act


def search(sch, Fz, N, kmax, D, mode='nonexec', fresh=True, kmin=2, order=None):
    """Returns None or (k, update as [(rel, I tuple, J tuple)]).  With mode 'seq', order lists the
    relations, and the changed entities are written relation by relation in this order."""
    mut = [r for r in sch.rels if any(a not in Fz[r] for a in sch.rels[r])]
    if order is not None:
        mut = [r for r in order if r in mut]
    for k in range(kmin, min(kmax, N) + 1):
        for chg in itertools.combinations_with_replacement(mut, k):
            e, slots, X, Y, act = build(sch, Fz, list(chg), N - k, D, mode, fresh)
            sol = pycosat.solve(e.cl)
            if sol != 'UNSAT':
                return (k, decode(sol, slots, X, Y, act, sch))
    return None
