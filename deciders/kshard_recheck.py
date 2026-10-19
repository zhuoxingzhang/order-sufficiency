"""Re-check the unconfirmed 'no' predictions of kshard.py with looser bounds.

kshard.py bounds the changed entities by N and lets N - k unchanged entities per relation exist, so
at k = N no unchanged entity can hold a referenced row.  Here the changed entities go up to kmax and
every relation may keep ucap unchanged entities, independently of k.  The input is the output of
kshard.py or of an earlier recheck.
Usage: python3 -I kshard_recheck.py <in.jsonl> <kind arb|fresh> <kmax> <ucap> <D> <procs> <out.jsonl>"""
import itertools
import json
import os
import sys
import time
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kshard as K
from selfcheck import schema as gen_schema
import pycosat


def search(sch, Fz, kmax, ucap, D, fresh, SK, grp):
    mut = [r for r in sch.rels if any(a not in Fz[r] for a in sch.rels[r])]
    for k in range(2, kmax + 1):
        for chg in itertools.combinations_with_replacement(mut, k):
            e, slots, X, Y, act = K.build(sch, Fz, list(chg), ucap, D, fresh, SK, grp)
            sol = pycosat.solve(e.cl)
            if sol != 'UNSAT':
                return (k, K.ksat.decode(sol, slots, X, Y, act, sch))
    return None


def job(args):
    rec, kind, kmax, ucap, D = args
    spec = gen_schema('K', int(rec['name'][1:]))
    sch, _ = K.to_sch(spec)
    Fz = {r: set(v) for r, v in rec['Fz'].items()}
    SK = {r: tuple(v) for r, v in rec['SK'].items()}
    t = time.time()
    cex = search(sch, Fz, kmax, ucap, D, kind == 'fresh', SK, rec['grp'])
    return dict(rec, recheck=dict(kind=kind, kmax=kmax, ucap=ucap, D=D, cex=cex,
                                  sec=round(time.time() - t, 2)))


def main():
    src, kind, kmax, ucap, D, procs, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), \
        int(sys.argv[5]), int(sys.argv[6]), sys.argv[7]
    pk, ck = ('pred_arb', 'cex_arb') if kind == 'arb' else ('pred_fresh', 'cex_fresh')
    recs = [json.loads(l) for l in open(src, encoding='utf-8')]
    # an earlier recheck as input: only the predictions it left unconfirmed
    todo = [r for r in recs if not r[pk] and r[ck] is None and not (r.get('recheck') or {}).get('cex')]
    print('unconfirmed', len(todo), flush=True)
    found = 0
    with Pool(procs) as pool, open(out, 'w') as f:
        for c, res in enumerate(pool.imap_unordered(job, [(r, kind, kmax, ucap, D) for r in todo]), 1):
            f.write(json.dumps(res) + '\n')
            found += res['recheck']['cex'] is not None
            if c % 50 == 0:
                print('done', c, 'confirmed', found, flush=True)
    print('confirmed', found, 'of', len(todo), flush=True)


if __name__ == '__main__':
    main()
