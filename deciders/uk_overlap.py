"""Proposition A.8 (unique keys) when the pointers of two INDs overlap.

An earlier version of Appendix A.3 defined Fz* without iteration ("adding pointers changes no set X_rho"), which fails when
an IND rho' shares part of its pointer with an IND rho whose pointer Fz* adds: the shared attributes
are then frozen, so their counterparts in L' enter X_rho', which may now contain a unique key.
Three predictions of Prop. 4.7 with a freeze M in place of Fz are compared with the bounded search:
  paper  M = Fz plus the pointer of every IND whose X_rho, computed under Fz, holds a unique key of S
  code   uk_big.pinned as it was before 2026-10-20 (iterated, but X_rho holds only the sources with
         frozen copies, not L), kept here as pinned_without_l; uk_big.pinned now equals 'fix'
  fix    least fixpoint: X_rho computed under M itself (L attributes with frozen or pinned
         counterparts, sources with frozen copies, closed under the copies of the INDs out of S
         whose pointer lies in X_rho), iterated until no pointer is added
Schemas: R0(k0, a, b, d1, d2) with rho1 R0[a (d1)] <= S1[l1 (c1)] and rho2 R0[a b (d2)] <= S2[m1 m2
(c2)], S2 with composite key (m1, m2), optionally S2[m1 c2 (e)] <= T[t1 t2 (f)]; unique keys on S1 and
S2 from {c1}, {c2}, {m1 c2}, {m2 c2}, {e}; every combination, every freeze on which the three
predictions differ and a sample of 64 of the others.
Usage (server): python3 -I uk_overlap.py <seed> <procs> <N> <D> <out.jsonl>"""
import itertools
import json
import os
import random
import sys
import time
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deciders as Dc
from kmodel import Sch
from selfcheck import frozen_sets
import fsat


def specs():
    out = []
    for cp1, cp2, tee, swap in itertools.product((True, False), (True, False), (None, 'm1', 'm2'), (False, True)):
        rels = {'R0': ['k0', 'a', 'b'] + (['d1'] if cp1 else []) + (['d2'] if cp2 else []),
                'S1': ['l1', 'c1'], 'S2': ['m1', 'm2', 'c2'] + (['e'] if tee else [])}
        pk = {'R0': ['k0'], 'S1': ['l1'], 'S2': ['m1', 'm2']}
        p2 = ['b', 'a'] if swap else ['a', 'b']
        inds = [('R0', ['a'] + (['d1'] if cp1 else []), 'S1', ['l1'] + (['c1'] if cp1 else [])),
                ('R0', p2 + (['d2'] if cp2 else []), 'S2', ['m1', 'm2'] + (['c2'] if cp2 else []))]
        if tee:
            rels['T'] = ['t1', 't2', 'f']
            pk['T'] = ['t1', 't2']
            inds.append(('S2', [tee, 'c2', 'e'], 'T', ['t1', 't2', 'f']))
        spec = {'rels': rels, 'pk': pk, 'fds': [], 'inds': inds}
        ok, why, _ = Dc.parse_K(spec)
        if not ok:
            continue
        cand1 = [[]] + ([['c1']] if cp1 else [])
        cand2 = [[], ['c2'], ['m1', 'c2'], ['m2', 'c2']] + ([['e']] if tee else [])
        for u1, u2 in itertools.product(cand1, cand2):
            uk = {}
            if u1:
                uk['S1'] = [u1]
            if u2:
                uk['S2'] = [u2]
            if not uk:
                continue
            name = 'cp%d%d_%s_%s_%s_%s' % (cp1, cp2, tee or 'noT', 'ba' if swap else 'ab',
                                           ''.join(u1) or '-', ''.join(u2) or '-')
            out.append((name, spec, uk))
    return out


def pinned_without_l(parsed, Fz, uk):
    M = {r: set(v) for r, v in Fz.items()}
    changed = True
    while changed:
        changed = False
        for (R, P, D, S, C) in parsed:
            X = {c for c, d in zip(C, D) if d in M[R]}
            grow = True
            while grow:
                grow = False
                for (R2, P2, D2, S2, C2) in parsed:
                    if R2 == S and set(P2) <= X and not set(D2) <= X:
                        X |= set(D2)
                        grow = True
            if any(U and set(U) <= X for U in uk.get(S, [])) and not set(P) <= M[R]:
                M[R] |= set(P)
                changed = True
    return M


def x_rho(parsed, pk, M, ind):
    R, P, D, S, C = ind
    X = {l for l, p in zip(pk[S], P) if p in M[R]} | {c for c, d in zip(C, D) if d in M[R]}
    grow = True
    while grow:
        grow = False
        for (R2, P2, D2, S2, C2) in parsed:
            if R2 == S and set(P2) <= X and not set(D2) <= X:
                X |= set(D2)
                grow = True
    return X


def star(parsed, pk, Fz, uk, iterate):
    M = {r: set(v) for r, v in Fz.items()}
    while True:
        base = M if iterate else Fz
        add = [(R, P) for (R, P, D, S, C) in parsed
               if any(set(U) <= x_rho(parsed, pk, base, (R, P, D, S, C)) for U in uk.get(S, []))
               and not set(P) <= M[R]]
        for R, P in add:
            M[R] |= set(P)
        if not add or not iterate:
            return M


def predict(parsed, M):
    ex = all(all(c in M[S] or d in M[R] for c, d in zip(C, D)) for (R, P, D, S, C) in parsed)
    cm = all(all(c in M[S] for c in C) or
             (all(p in M[R] for p in P) and all(c in M[S] or d in M[R] for c, d in zip(C, D)))
             for (R, P, D, S, C) in parsed)
    return ex, cm


def job(args):
    name, spec, uk, Fz, N, Dv = args
    ok, why, parsed = Dc.parse_K(spec)
    inds = [{'R': R, 'P': P, 'D': D, 'S': S, 'C': C} for (R, P, D, S, C) in parsed]
    sch = Sch(spec['rels'], spec['pk'], inds, uk)
    preds = {'paper': predict(parsed, star(parsed, spec['pk'], Fz, uk, False)),
             'code': predict(parsed, pinned_without_l(parsed, Fz, uk)),
             'fix': predict(parsed, star(parsed, spec['pk'], Fz, uk, True))}
    t = time.time()
    ne = fsat.search(sch, Fz, N, N, Dv, 'nonexec', fresh=True)
    nc = fsat.search(sch, Fz, N, N, Dv, 'noncomm', fresh=True) if ne is None else ne
    return {'name': name, 'uk': uk, 'Fz': {r: sorted(v) for r, v in Fz.items()}, 'pred': preds,
            'nonexec': ne, 'noncomm': nc, 'sec': round(time.time() - t, 2)}


def tasks(seed, N, Dv):
    rng = random.Random(seed)
    out = []
    for name, spec, uk in specs():
        ok, why, parsed = Dc.parse_K(spec)
        fzs, split = [], []
        for Fz in frozen_sets(spec):
            p = [predict(parsed, M) for M in (star(parsed, spec['pk'], Fz, uk, False), pinned_without_l(parsed, Fz, uk),
                                              star(parsed, spec['pk'], Fz, uk, True))]
            (split if len(set(p)) > 1 else fzs).append(Fz)
        if len(fzs) > 64:                      # every freeze on which the predictions differ, 64 others
            fzs = rng.sample(fzs, 64)
        out += [(name, spec, uk, Fz, N, Dv) for Fz in split + fzs]
    return out


def main():
    seed, procs, N, Dv, path = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
    ts = tasks(seed, N, Dv)
    print('schemas', len({t[0] for t in ts}), 'tasks', len(ts), flush=True)
    wrong = {v: {'exec': 0, 'comm': 0, 'pairs': 0} for v in ('paper', 'code', 'fix')}
    with Pool(procs) as pool, open(path, 'w') as f:
        for res in pool.imap_unordered(job, ts, chunksize=2):
            f.write(json.dumps(res) + '\n')
            f.flush()
            ae, ac = res['nonexec'] is None, res['noncomm'] is None
            for v, (pe, pc) in res['pred'].items():
                bad = [k for k, p, a in (('exec', pe, ae), ('comm', pc, ac)) if p != a]
                for k in bad:
                    wrong[v][k] += 1
                wrong[v]['pairs'] += bool(bad)
    for v, w in wrong.items():
        print('%-5s predicted wrongly: %d pairs (exec %d, comm %d)' % (v, w['pairs'], w['exec'], w['comm']))
    print('UK_OVERLAP_DONE', flush=True)


if __name__ == '__main__':
    main()
