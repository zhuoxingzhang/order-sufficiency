"""E3, MIN-FREEZE part: frozen-attribute choice in class K on random schemas.

For random class-K schemas (gen_scale.gen_K; families: layered = two layers of relations, dag =
any earlier relation, dense = two base attributes and six INDs per relation) every non-key attribute is mutable (Fz = PK) and has
a weight in 1..10 (the number of update templates that modify it).  The conflict graph H of
Theorem 5.1 is built and solved by
  mincut   exact, when H is bipartite (always for the layered family);
  approx   2-approximation (local ratio with clean-up);
  lp       LP lower bound (min cut on the bipartite double cover);
  exact    branch and bound, only when H has at most `exact_max` vertices;
  degen    the degenerate rule: freeze every copied attribute (every C side).
Reported: |H|, times, weights, approx/OPT (bipartite or exact) or approx/LP (otherwise).
Usage: python -I x3_m2.py <procs> <seeds> <out.jsonl> [quick]"""
import sys, os, time, json, random, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from multiprocessing import Pool
import deciders as D
import m2
from gen_scale import gen_K

SIZES = (10, 30, 100, 300, 1000, 3000, 10000)
EXACT_MAX = 400


def graph(sch, rng):
    ok, why, parsed = D.parse_K(sch)
    assert ok, why
    Fz = {r: set(sch['pk'][r]) for r in sch['rels']}
    E = D.conflict_graph(parsed, Fz)
    verts = sorted({v for e in E for v in e})
    ix = {v: i for i, v in enumerate(verts)}
    w = [rng.randint(1, 10) for _ in verts]
    El = sorted(tuple(sorted(ix[v] for v in e)) for e in E)    # sorted: the 2-approximation depends on edge order
    copied = {(S, c) for (R, P, Dd, S, C) in parsed for c in C}
    return verts, w, El, {ix[v] for v in copied if v in ix}


FAMILIES = {'layered': dict(nA=6, nI=3, layered=True), 'dag': dict(nA=6, nI=3, layered=False),
            'dense': dict(nA=2, nI=6, layered=False)}


def point(args):
    nR, fam, seed = args
    layered = fam
    rng = random.Random(seed)
    p = FAMILIES[fam]
    sch, _ = gen_K(rng, nR, p['nA'], 0, p['nI'], layered=p['layered'])
    t0 = time.perf_counter()
    verts, w, E, copied = graph(sch, rng)
    t_graph = time.perf_counter() - t0
    out = dict(nR=nR, layered=layered, seed=seed, V=len(verts), E=len(E), t_graph=t_graph)
    t0 = time.perf_counter()
    col = m2.two_colour(len(w), E)
    out['bipartite'] = col is not None
    t0 = time.perf_counter()
    C2 = m2.vc_2approx(w, E)
    out['t_approx'] = time.perf_counter() - t0
    out['w_approx'] = m2.weight(w, C2)
    assert m2.is_cover(E, C2)
    t0 = time.perf_counter()
    lp, _ = m2.vc_lp(w, E)
    out['t_lp'] = time.perf_counter() - t0
    out['lp'] = lp
    Cd = set(copied)
    assert m2.is_cover(E, Cd)
    out['w_degen'] = m2.weight(w, Cd)
    out['n_degen'] = len(Cd)
    out['opt'] = None
    if col is not None:
        t0 = time.perf_counter()
        Cm = m2.vc_mincut(w, E, col)
        out['t_mincut'] = time.perf_counter() - t0
        assert m2.is_cover(E, Cm)
        out['opt'] = m2.weight(w, Cm)
        out['n_opt'] = len(Cm)
    elif len(w) <= EXACT_MAX:
        t0 = time.perf_counter()
        Ce, ex = m2.vc_exact(w, E)
        out['t_exact'] = time.perf_counter() - t0
        if ex:
            out['opt'] = m2.weight(w, Ce)
            out['n_opt'] = len(Ce)
    return out


def summarize(rows):
    out = []
    keys = sorted({(r['layered'], r['nR']) for r in rows})
    for lay, nR in keys:
        rs = [r for r in rows if r['layered'] == lay and r['nR'] == nR]
        med = lambda k: statistics.median(r[k] for r in rs if r.get(k) is not None) if any(r.get(k) is not None for r in rs) else None
        ratio_opt = [r['w_approx'] / r['opt'] for r in rs if r['opt']]
        ratio_lp = [r['w_approx'] / r['lp'] for r in rs if r['lp']]
        deg = [r['w_degen'] / r['opt'] for r in rs if r['opt']]
        deg_lp = [r['w_degen'] / r['lp'] for r in rs if r['lp']]
        out.append(dict(family=lay, nR=nR, n=len(rs), bipartite=sum(r['bipartite'] for r in rs),
                        V=med('V'), E=med('E'), t_graph_ms=1000 * med('t_graph'),
                        t_mincut_ms=None if med('t_mincut') is None else 1000 * med('t_mincut'),
                        t_approx_ms=1000 * med('t_approx'), t_lp_ms=1000 * med('t_lp'),
                        t_exact_ms=None if med('t_exact') is None else 1000 * med('t_exact'),
                        approx_over_opt_max=max(ratio_opt) if ratio_opt else None,
                        approx_over_opt_med=statistics.median(ratio_opt) if ratio_opt else None,
                        approx_over_lp_max=max(ratio_lp) if ratio_lp else None,
                        degen_over_opt_med=statistics.median(deg) if deg else None,
                        degen_over_lp_med=statistics.median(deg_lp) if deg_lp else None,
                        with_opt=len(ratio_opt)))
    return out


if __name__ == '__main__':
    procs, seeds, outp = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    sizes = SIZES[:4] if len(sys.argv) > 4 else SIZES
    J = [(n, fam, 7919 * s + n) for n in sizes for fam in FAMILIES for s in range(seeds)]
    J.sort(key=lambda j: -j[0])
    with Pool(procs) as pool:
        rows = list(pool.imap_unordered(point, J, chunksize=1))
    with open(outp, 'w') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')
    for s in summarize(rows):
        print(json.dumps(s))
