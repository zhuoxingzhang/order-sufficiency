"""Proposition A.8 (unique keys): class K with unique keys that no IND references, under fresh
modification updates, bounded SAT search.

Prediction (recorded as pred_exec and pred_comm): Proposition 4.7 with the freeze Fz itself, that is,
exec iff C_i in Fz or D_i in Fz at every position, comm iff condition (4) of Theorem 4.5.  Proposition
A.8 replaces Fz by Fz*, which also freezes the pointer of every IND whose frozen copies pin a unique
key of the referenced relation; uk_tally.py scores both predictions against the search.

Check.  Random class K schemas (selfcheck.gen_K), each with 1 or 2 random unique keys of 1 or
2 non-key attributes (pointers and copies included), every freeze or a sample of 48, and the
bounded SAT search of fsat.py (which encodes unique keys and freshness).  Named schemas first.
Usage: python3 -I fresh_k_uk.py <schemas> <seed> <procs> <N> <D> <out.jsonl>"""
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
from fresh_k import NAMED, predict

NAMED_UK = {
    'pin/uk_p': (NAMED['pin'], {'R0': [['p']]}),          # one referencing entity per source
    'pin/uk_d': (NAMED['pin'], {'R0': [['d']]}),          # copies pairwise distinct
    'pin/uk_c': (NAMED['pin'], {'R1': [['c']]}),          # sources pairwise distinct
    'pin2/uk_pd': (NAMED['pin2'], {'R0': [['p', 'd']], 'R1': [['f']]}),
    'chain/uk_q': (NAMED['chain'], {'R1': [['q'], ['c']]}),
}


def to_sch(spec, uk):
    ok, why, parsed = Dc.parse_K(spec)
    assert ok, why
    inds = [{'R': R, 'P': P, 'D': D, 'S': S, 'C': C} for (R, P, D, S, C) in parsed]
    return Sch(spec['rels'], spec['pk'], inds, uk), parsed


def add_uk(rng, spec):
    uk = {}
    rels = [r for r in spec['rels'] if len(spec['rels'][r]) > len(spec['pk'][r])]
    for _ in range(rng.randint(1, 2)):
        r = rng.choice(rels)
        nonkey = [a for a in spec['rels'][r] if a not in spec['pk'][r]]
        U = sorted(rng.sample(nonkey, min(len(nonkey), rng.randint(1, 2))))
        if U not in uk.get(r, []):
            uk.setdefault(r, []).append(U)
    return uk


def job(args):
    name, spec, uk, Fz, N, Dv = args
    sch, parsed = to_sch(spec, uk)
    pe, pc = predict(parsed, Fz)
    t = time.time()
    ne = fsat.search(sch, Fz, N, N, Dv, 'nonexec', fresh=True)
    nc = fsat.search(sch, Fz, N, N, Dv, 'noncomm', fresh=True) if ne is None else ne
    return {'name': name, 'uk': uk, 'Fz': {r: sorted(v) for r, v in Fz.items()}, 'pred_exec': pe,
            'pred_comm': pc, 'nonexec': ne, 'noncomm': nc, 'sec': round(time.time() - t, 2)}


def tasks(n, seed, N, Dv):
    out = []
    for name, (spec, uk) in NAMED_UK.items():
        for Fz in frozen_sets(spec):
            out.append((name, spec, uk, Fz, N, Dv))
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
        uk = add_uk(rng, spec)
        fzs = list(frozen_sets(spec))
        if len(fzs) > 48:
            fzs = rng.sample(fzs, 48)
        for Fz in fzs:
            out.append(('s%d' % s, spec, uk, Fz, N, Dv))
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
            for k in (('exec', res['pred_exec'], ae), ('comm', res['pred_comm'], ac)):
                agg[k] = agg.get(k, 0) + 1
    for k in sorted(agg, key=str):
        tag = 'ok' if k[1] == k[2] else ('UNCONFIRMED' if not k[1] else 'COUNTEREXAMPLE')
        print('%s predicted=%s within-bounds=%s : %d  %s' % (k[0], k[1], k[2], agg[k], tag))


if __name__ == '__main__':
    main()
