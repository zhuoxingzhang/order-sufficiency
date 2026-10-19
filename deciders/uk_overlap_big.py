"""Proposition A.8: search again, with more entities, every pair of uk_overlap_n5d5.jsonl that some prediction calls
'not order-insensitive' but for which the search with 5 entities found no counterexample.
Usage (server): python3 -I uk_overlap_big.py <uk_overlap_n5d5.jsonl> <N> <D> <kmax> <procs> <out.jsonl>"""
import json
import os
import sys
import time
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deciders as Dc
from kmodel import Sch
import fsat
from uk_overlap import specs

SPECS = {name: (spec, uk) for name, spec, uk in specs()}


def job(args):
    r, N, Dv, kmax = args
    spec, uk = SPECS[r['name']]
    ok, why, parsed = Dc.parse_K(spec)
    inds = [{'R': R, 'P': P, 'D': D, 'S': S, 'C': C} for (R, P, D, S, C) in parsed]
    sch = Sch(spec['rels'], spec['pk'], inds, uk)
    Fz = {k: set(v) for k, v in r['Fz'].items()}
    t = time.time()
    res = fsat.search(sch, Fz, N, kmax, Dv, 'noncomm', fresh=True)
    return {'name': r['name'], 'uk': r['uk'], 'Fz': r['Fz'], 'pred': r['pred'], 'N': N, 'D': Dv, 'kmax': kmax,
            'noncomm': res, 'sec': round(time.time() - t, 1)}


def main():
    path, N, Dv, kmax, procs, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), \
        int(sys.argv[5]), sys.argv[6]
    rows = [json.loads(l) for l in open(path)]
    todo = [r for r in rows if r['noncomm'] is None and not all(pc for pe, pc in r['pred'].values())]
    print('pairs', len(todo), flush=True)
    with Pool(procs) as pool, open(out, 'w') as f:
        for res in pool.imap_unordered(job, [(r, N, Dv, kmax) for r in todo]):
            f.write(json.dumps(res) + '\n')
            f.flush()
    found = [json.loads(l) for l in open(out)]
    for v in ('paper', 'code', 'fix'):
        no = [x for x in found if not x['pred'][v][1]]
        print('%-5s predicts no: %d, counterexample found: %d' % (v, len(no), sum(x['noncomm'] is not None for x in no)))
    yes = [x for x in found if x['pred']['fix'][1]]
    print('fix predicts yes: %d, counterexample found: %d' % (len(yes), sum(x['noncomm'] is not None for x in yes)))
    print('UK_OVERLAP_BIG_DONE', flush=True)


if __name__ == '__main__':
    main()
