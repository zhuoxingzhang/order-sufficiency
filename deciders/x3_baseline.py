"""E3 contrast: the polynomial FD decider against the literal N1 check that enumerates every
left-reduced FD of Sigma+ (exponential in the number of attributes), on single relations of
growing width.  The two verdicts must agree on every relation.
Usage: python -I x3_baseline.py <procs> <seeds> <out.jsonl>"""
import sys, os, time, json, random, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from multiprocessing import Pool
import deciders as D
from gen_scale import gen_fd
from x4_design import left_reduced_fds

WIDTHS = tuple(int(x) for x in os.environ.get('WIDTHS', '4,6,8,10,12,14,16').split(','))


def n1_literal(rel, F, fz):
    for X, a in left_reduced_fds(rel, F):
        if X & ~fz:
            return False
        if F.closure(D.bits(X)) != rel.full and not fz >> a & 1:
            return False
    return True


def point(args):
    nA, seed = args
    rng = random.Random(seed)
    sch, Fz = gen_fd(rng, 1, nA, nA)
    rel = D.index_schema(sch)['R0']
    t0 = time.perf_counter()
    ok = D.dec_fd(sch, Fz)[0]
    t1 = time.perf_counter()
    lit = n1_literal(rel, rel.fdset(), D.fz_mask(rel, Fz))
    t2 = time.perf_counter()
    return dict(nA=nA, seed=seed, dec=ok, literal=lit, t_dec=t1 - t0, t_literal=t2 - t1)


if __name__ == '__main__':
    procs, seeds, outp = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    J = [(a, 31 * s + a) for a in WIDTHS for s in range(seeds)]
    with Pool(procs) as pool:
        rows = list(pool.imap_unordered(point, J, chunksize=1))
    with open(outp, 'w') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')
    for a in WIDTHS:
        rs = [r for r in rows if r['nA'] == a]
        print(json.dumps(dict(nA=a, n=len(rs), disagree=sum(r['dec'] != r['literal'] for r in rs),
                              os_share=sum(r['dec'] for r in rs) / len(rs),
                              t_dec_ms=1000 * statistics.median(r['t_dec'] for r in rs),
                              t_literal_ms=1000 * statistics.median(r['t_literal'] for r in rs))))
