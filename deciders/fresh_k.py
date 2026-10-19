"""F1: order-sufficiency and commutativity of class K under fresh modification updates.

Conjectures (per IND R[P D] <= S[L C] of a class K schema, L = PK(S)):
  exec   every fresh modification update is executable  iff  C_i in Fz or D_i in Fz at every
         position i (no source with a mutable copy);
  comm   every fresh modification update is commutative iff  C <= Fz, or P <= Fz and C_i in Fz or
         D_i in Fz at every position i (condition (4) of Theorem 4.5, unchanged).
So under fresh updates write order matters exactly where a mutable pointer leads to a mutable
source whose copy is frozen: the referencing entities must move away before the source changes.

Check.  Random class K schemas (selfcheck.gen_K, distinct), every freeze or a sample of 48,
and the bounded SAT search of fsat.py with freshness: N entities, all of them changed at most,
D values.  A predicted 'yes' must have no counterexample within the bounds; a predicted 'no' should
have one (else it is reported as unconfirmed).  The named schemas run first.
Usage: python3 -I fresh_k.py <schemas> <seed> <procs> <N> <D> <out.jsonl>"""
import json
import os
import random
import sys
import time
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deciders as Dc
from kmodel import Sch
from selfcheck import schema as gen_schema, frozen_sets
import fsat

NAMED = {
    # pointer and source mutable, copy frozen: executable, not commutative (conjecture)
    'pin': {'rels': {'R0': ['k0', 'p', 'd'], 'R1': ['k1', 'c']}, 'pk': {'R0': ['k0'], 'R1': ['k1']},
            'fds': [], 'inds': [('R0', ['p', 'd'], 'R1', ['k1', 'c'])]},
    # two INDs share the pointer: one into a frozen-copy source, one plain
    'pin2': {'rels': {'R0': ['k0', 'p', 'd', 'e'], 'R1': ['k1', 'c', 'f']},
             'pk': {'R0': ['k0'], 'R1': ['k1']}, 'fds': [],
             'inds': [('R0', ['p', 'd', 'e'], 'R1', ['k1', 'c', 'f'])]},
    # a chain: R0 copies a source of R1, R1 copies a source of R2
    'chain': {'rels': {'R0': ['k0', 'p', 'd'], 'R1': ['k1', 'q', 'c', 'g'], 'R2': ['k2', 'h']},
              'pk': {'R0': ['k0'], 'R1': ['k1'], 'R2': ['k2']}, 'fds': [],
              'inds': [('R0', ['p', 'd'], 'R1', ['k1', 'c']), ('R1', ['q', 'g'], 'R2', ['k2', 'h'])]},
}


def to_sch(spec):
    ok, why, parsed = Dc.parse_K(spec)
    assert ok, why
    inds = [{'R': R, 'P': P, 'D': D, 'S': S, 'C': C} for (R, P, D, S, C) in parsed]
    return Sch(spec['rels'], spec['pk'], inds), parsed


def predict(parsed, Fz):
    ex = all(c in Fz[S] or d in Fz[R] for (R, P, D, S, C) in parsed for c, d in zip(C, D))
    cm = all(all(c in Fz[S] for c in C) or
             (all(p in Fz[R] for p in P) and all(c in Fz[S] or d in Fz[R] for c, d in zip(C, D)))
             for (R, P, D, S, C) in parsed)
    return ex, cm


def job(args):
    name, spec, Fz, N, Dv = args
    sch, parsed = to_sch(spec)
    pe, pc = predict(parsed, Fz)
    assert pc == Dc.dec_K(spec, Fz, parsed)[0]
    t = time.time()
    ne = fsat.search(sch, Fz, N, N, Dv, 'nonexec', fresh=True)
    nc = fsat.search(sch, Fz, N, N, Dv, 'noncomm', fresh=True) if ne is None else ne
    # referencing relations first: a topological order of the IND graph
    topo, left = [], set(spec['rels'])
    while left:
        r = min(x for x in left if not any(R in left and S == x for (R, P, D, S, C) in parsed))
        topo.append(r)
        left.remove(r)
    sq = fsat.search(sch, Fz, N, N, Dv, 'seq', fresh=True, order=topo) if pe else None
    return {'name': name, 'Fz': {r: sorted(v) for r, v in Fz.items()}, 'pred_exec': pe, 'pred_comm': pc,
            'nonexec': ne, 'noncomm': nc, 'topo': topo, 'topo_fail': sq, 'sec': round(time.time() - t, 2)}


def tasks(n, seed, N, Dv):
    out = []
    for name, spec in NAMED.items():
        for Fz in frozen_sets(spec):
            out.append((name, spec, Fz, N, Dv))
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
        fzs = list(frozen_sets(spec))
        if len(fzs) > 48:
            fzs = rng.sample(fzs, 48)
        for Fz in fzs:
            out.append(('s%d' % s, spec, Fz, N, Dv))
    return out


def main():
    n, seed, procs, N, Dv, path = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), \
        int(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
    ts = tasks(n, seed, N, Dv)
    print('tasks', len(ts), flush=True)
    agg = {}
    with Pool(procs) as pool, open(path, 'w') as f:
        for res in pool.imap_unordered(job, ts, chunksize=4):
            f.write(json.dumps(res) + '\n')
            ae, ac = res['nonexec'] is None, res['noncomm'] is None
            key = ('exec', res['pred_exec'], ae), ('comm', res['pred_comm'], ac)
            if res['pred_exec']:
                key += (('topo order', True, res['topo_fail'] is None),)
            for k in key:
                agg[k] = agg.get(k, 0) + 1
    for k in sorted(agg, key=str):
        tag = 'ok' if k[1] == k[2] else ('UNCONFIRMED' if not k[1] else 'COUNTEREXAMPLE')
        print('%s predicted=%s within-bounds=%s : %d  %s' % (k[0], k[1], k[2], agg[k], tag))


if __name__ == '__main__':
    main()
