"""E3, part 1: run time of the four order-sufficiency deciders on random schemas.

Three sweeps per class, `seeds` schemas per point, one process per point:
  rel    relations 10 .. 10,000 (10 attributes, 10 FDs, 2 INDs per relation)
  width  attributes 5 .. 80 per relation (100 relations, FDs = attributes, 2 INDs)
  fd     FDs 5 .. 160 per relation (100 relations, 20 attributes, 2 INDs)
For class K the FD sweep is replaced by an IND sweep (INDs 1 .. 16 per relation).  Reported: time of
the class-membership test and of the decider (median over seeds), schema size, verdict, and for
class U the number of rounds of the finite closure.
Usage: python -I x3_time.py <procs> <seeds> <out.jsonl> [quick]"""
import sys, os, time, json, random, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from multiprocessing import Pool
import deciders as D
from gen_scale import GEN

SWEEPS = {
    'rel': [dict(nR=n, nA=10, nF=10, nI=2) for n in (10, 30, 100, 300, 1000, 3000, 10000)],
    'width': [dict(nR=100, nA=a, nF=a, nI=2) for a in (5, 10, 20, 40, 80)],
    'fd': [dict(nR=100, nA=20, nF=f, nI=2) for f in (5, 10, 20, 40, 80, 160)],
    'ind': [dict(nR=100, nA=10, nF=0, nI=i) for i in (1, 2, 4, 8, 16)],
}
QUICK = {
    'rel': [dict(nR=n, nA=10, nF=10, nI=2) for n in (10, 100, 1000)],
    'width': [dict(nR=50, nA=a, nF=a, nI=2) for a in (5, 20)],
    'fd': [dict(nR=50, nA=20, nF=f, nI=2) for f in (5, 40)],
    'ind': [dict(nR=50, nA=10, nF=0, nI=i) for i in (1, 8)],
}


def member(cls, sch):
    if cls == 'FD':
        return not sch['inds']
    if cls == 'U':
        return all(len(X) == 1 for _, X, _, _ in sch['inds'])
    if cls == 'A':
        return D.class_A(sch)[0]
    return D.parse_K(sch)[0]


def decide(cls, sch, Fz):
    if cls == 'FD':
        return D.dec_fd(sch, Fz)[0], None
    if cls == 'U':
        info = D.finite_unary(sch)
        return D.dec_unary(sch, Fz, info=info)[0], info[4]
    if cls == 'A':
        return D.dec_A(sch, Fz)[0], None
    ok, _, parsed = D.parse_K(sch)
    return D.dec_K(sch, Fz, parsed)[0], None


def point(args):
    cls, sweep, prm, seed = args
    rng = random.Random(seed)
    sch, Fz = GEN[cls](rng, **prm)
    t0 = time.perf_counter()
    m = member(cls, sch)
    t1 = time.perf_counter()
    ok, rounds = decide(cls, sch, Fz)
    t2 = time.perf_counter()
    return dict(cls=cls, sweep=sweep, prm=prm, seed=seed, member=m, os=ok, rounds=rounds,
                attrs=sum(len(a) for a in sch['rels'].values()), fds=len(sch['fds']), inds=len(sch['inds']),
                t_member=t1 - t0, t_decide=t2 - t1)


def jobs(seeds, quick):
    sw = QUICK if quick else SWEEPS
    out = []
    for cls in ('FD', 'U', 'A', 'K'):
        for sweep, pts in sw.items():
            if (cls == 'K') != (sweep == 'ind') and sweep in ('fd', 'ind'):
                continue
            for prm in pts:
                p = dict(prm)
                if cls == 'FD':
                    p['nI'] = 0
                for s in range(seeds):
                    out.append((cls, sweep, p, 1000 * s + 17))
    return out


def summarize(rows):
    pts = {}
    for r in rows:
        key = (r['cls'], r['sweep'], json.dumps(r['prm'], sort_keys=True))
        pts.setdefault(key, []).append(r)
    out = []
    for (cls, sweep, prm), rs in sorted(pts.items()):
        out.append(dict(cls=cls, sweep=sweep, prm=json.loads(prm), n=len(rs),
                        attrs=statistics.median(r['attrs'] for r in rs),
                        constraints=statistics.median(r['fds'] + r['inds'] for r in rs),
                        member_ok=all(r['member'] for r in rs),
                        os_share=sum(r['os'] for r in rs) / len(rs),
                        rounds=max((r['rounds'] or 0) for r in rs),
                        t_member_ms=1000 * statistics.median(r['t_member'] for r in rs),
                        t_decide_ms=1000 * statistics.median(r['t_decide'] for r in rs)))
    return out


if __name__ == '__main__':
    procs, seeds, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    quick = len(sys.argv) > 4
    J = jobs(seeds, quick)
    J.sort(key=lambda j: -j[2]['nR'] * j[2]['nA'])
    with Pool(procs) as pool:
        rows = list(pool.imap_unordered(point, J, chunksize=1))
    with open(out, 'w') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')
    for s in summarize(rows):
        print(json.dumps(s))
