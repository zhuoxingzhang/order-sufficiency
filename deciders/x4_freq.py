"""E4 (Figure 8b): sensitivity of optimal freezing (Theorem 5.1) to template frequencies and fan-out.

Same conflict graph H as x4_design.m2_study on the class-K fragment of GitLab (179 sharding-key
copies), with pointers frozen, which is also the graph for fresh updates (Proposition 4.7 drops the
pointer edges), so H consists of stars around the copied sources.  The degenerate rule freezes
every copied source; M2 is the minimum-weight vertex cover (min cut, H is bipartite).

Frequencies q(v) of the templates that modify an attribute, 200 draws each:
  uniform   integers 1..10 (the setting of the paper)
  zipf      q = 1 / rank, ranks a random permutation of 1..|V|
  lognorm1  exp(N(0, 1));  lognorm2  exp(N(0, 2))
Weights:
  plain     w(v) = q(v)
  fanout    re-keying moves the referencing rows along: w(source) = q * (1 + f), where f sums, over
            the INDs that copy the source, a number of referencing rows per row drawn from
            exp(N(ln 10, 1)) (median 10); w(copy) = q, since re-keying a referencing row moves
            only that row (class K has no copies of copies).
Reports the saving 1 - w(M2) / w(degenerate): median, 90th percentile, max, and the share of draws
in which M2 is strictly better.
Usage: python -I x4_freq.py <out.json>"""
import sys, os, json, math, random, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deciders as D
import gitlab_schema as G
import x4_design as X
import m2


def draw_q(rng, dist, n):
    if dist == 'uniform':
        return [rng.randint(1, 10) for _ in range(n)]
    if dist == 'zipf':
        ranks = list(range(1, n + 1))
        rng.shuffle(ranks)
        return [1.0 / r for r in ranks]
    if dist == 'lognorm1':
        return [rng.lognormvariate(0, 1) for _ in range(n)]
    if dist == 'lognorm2':
        return [rng.lognormvariate(0, 2) for _ in range(n)]
    raise ValueError(dist)


def study(sch_k, which, draws=200, seed=7):
    Fz = X.scenario(sch_k, which)
    verts, E, copied = X.h_graph(sch_k, Fz)
    col = m2.two_colour(len(verts), E)
    assert col is not None
    # leaves of every copied source: the copies that an edge joins to it
    leaves = {c: [u if v == c else v for u, v in E if c in (u, v)] for c in copied}
    out = dict(V=len(verts), E=len(E), copied=len(copied))
    for weighting in ('plain', 'fanout'):
        for dist in ('uniform', 'zipf', 'lognorm1', 'lognorm2'):
            rng = random.Random(f'{seed}-{which}-{weighting}-{dist}')
            sav, wins, share_src = [], 0, []
            for _ in range(draws):
                w = draw_q(rng, dist, len(verts))
                if weighting == 'fanout':
                    for c in copied:
                        f = sum(rng.lognormvariate(math.log(10), 1) for _ in leaves[c])
                        w[c] *= 1 + f
                opt = m2.vc_mincut(w, E, col)
                wd, wo = m2.weight(w, set(copied)), m2.weight(w, opt)
                assert m2.is_cover(E, opt) and wo <= wd + 1e-9
                sav.append(1 - wo / wd if wd else 0.0)
                wins += wo < wd - 1e-9
                share_src.append(len(set(opt) & set(copied)) / len(copied))
            sav.sort()
            out[f'{weighting}/{dist}'] = dict(
                saving_median=round(statistics.median(sav), 4), saving_p90=round(sav[int(0.9 * len(sav))], 4),
                saving_max=round(sav[-1], 4), m2_better=wins / draws,
                sources_frozen_by_m2_median=round(statistics.median(share_src), 3))
    return out


def main(outp):
    g = G.load()
    sch = G.base(g)
    k, kst = G.k_fragment(g, sch)
    res = {'k_fragment': kst}
    for which in ('noptr', 'all'):
        res[which] = study(k, which)
    json.dump(res, open(outp, 'w'), indent=1)
    for which in ('noptr', 'all'):
        print(which, {x: res[which][x] for x in ('V', 'E', 'copied')})
        for key, v in res[which].items():
            if '/' in key:
                print(f'  {key:18s}', v)


if __name__ == '__main__':
    main(sys.argv[1])
