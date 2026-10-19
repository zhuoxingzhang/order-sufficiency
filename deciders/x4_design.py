"""E4: design gains of Theorems 5.1 and 5.2.

Part M2 (GitLab).  On the class-K fragment of the GitLab schema (inferred sharding-key copies,
gitlab_schema.k_fragment) compare the degenerate rule "freeze every copied attribute" with the M2
optimum (min-cut weighted vertex cover of the conflict graph H).  Workload model: one update
template per mutable attribute, so the frozen weight is the number of templates that lose
order-sufficiency and need atomic visibility or a re-key.  Scenarios:
  all      every pointer, copy and copied attribute is mutable (Fz = PK);
  noptr    pointers are frozen (children are never re-parented), copies and copied attrs mutable.
Weights: unit, and random template counts 1..10 (median over 200 draws).  The same comparison is
run on random class-K schemas for contrast (gen_scale.gen_K, family dag).

Part B-pi.  Feasible sharding keys of a relation: SK <= Fz n (X n Fz)+ for every unstable
left-reduced X -> A in Sigma+ (Theorem 5.2).  Compared with the containment rule SK <= X for
every left-reduced X -> A (stable or not).  Counts single-column sharding keys on random FD
relations and, when db/structure.sql was extracted, on GitLab (keys from unique indexes).
Usage: python -I x4_design.py <out.json>"""
import sys, os, json, random, itertools, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deciders as D
import m2
import gitlab_schema as G
from gen_scale import gen_K, gen_fd


# ---------------------------------------------------------------------------- M2
def h_graph(sch_k, Fz):
    ok, why, parsed = D.parse_K(sch_k)
    assert ok, why
    E = D.conflict_graph(parsed, Fz)
    verts = sorted({v for e in E for v in e})
    ix = {v: i for i, v in enumerate(verts)}
    El = sorted(tuple(sorted(ix[v] for v in e)) for e in E)    # sorted: the 2-approximation depends on edge order
    copied = {ix[(S, c)] for (R, P, Dd, S, C) in parsed for c in C if (S, c) in ix}
    return verts, El, copied


def compare(verts, E, copied, w):
    col = m2.two_colour(len(verts), E)
    if col is not None:
        opt_c = m2.vc_mincut(w, E, col)
        exact = True
    else:
        opt_c, exact = m2.vc_exact(w, E)
    deg = set(copied)
    assert m2.is_cover(E, deg) and m2.is_cover(E, opt_c)
    return dict(w_deg=m2.weight(w, deg), w_opt=m2.weight(w, opt_c), n_deg=len(deg), n_opt=len(opt_c),
                exact=exact, bipartite=col is not None)


def scenario(sch_k, which):
    Fz = {r: set(sch_k['pk'][r]) for r in sch_k['rels']}
    if which == 'noptr':
        ok, _, parsed = D.parse_K(sch_k)
        for (R, P, Dd, S, C) in parsed:
            Fz[R] |= set(P)
    return Fz


def m2_study(sch_k, label, draws=200, seed=5):
    out = {'label': label}
    rng = random.Random(seed)
    for which in ('all', 'noptr'):
        Fz = scenario(sch_k, which)
        verts, E, copied = h_graph(sch_k, Fz)
        deg = {}
        for u, v in E:
            deg[u] = deg.get(u, 0) + 1
            deg[v] = deg.get(v, 0) + 1
        comps = components(len(verts), E)
        star = sum(1 for c in comps if is_star(c, E))
        r = dict(V=len(verts), E=len(E), components=len(comps), star_components=star,
                 max_degree=max(deg.values()) if deg else 0,
                 copied_degree=sorted((deg[c] for c in copied), reverse=True)[:10])
        r['unit'] = compare(verts, E, copied, [1] * len(verts))
        ratios, wins = [], 0
        for _ in range(draws):
            w = [rng.randint(1, 10) for _ in verts]
            c = compare(verts, E, copied, w)
            ratios.append(c['w_deg'] / c['w_opt'] if c['w_opt'] else 1.0)
            wins += c['w_deg'] > c['w_opt']
        r['random_weights'] = dict(draws=draws, deg_over_opt_median=statistics.median(ratios),
                                   deg_over_opt_max=max(ratios), draws_where_m2_better=wins)
        out[which] = r
    return out


def components(n, E):
    adj = [[] for _ in range(n)]
    for u, v in E:
        adj[u].append(v)
        adj[v].append(u)
    seen, comps = [False] * n, []
    for s in range(n):
        if seen[s] or not adj[s]:
            continue
        st, comp = [s], []
        seen[s] = True
        while st:
            u = st.pop()
            comp.append(u)
            for v in adj[u]:
                if not seen[v]:
                    seen[v] = True
                    st.append(v)
        comps.append(comp)
    return comps


def is_star(comp, E):
    cs = set(comp)
    ed = [(u, v) for u, v in E if u in cs]
    if len(ed) != len(comp) - 1:
        return False
    deg = {}
    for u, v in ed:
        deg[u] = deg.get(u, 0) + 1
        deg[v] = deg.get(v, 0) + 1
    return max(deg.values()) == len(comp) - 1


# ---------------------------------------------------------------------------- B-pi
def left_reduced_fds(rel, F):
    """all nontrivial left-reduced X -> a in Sigma+ of a relation (exponential; small relations)"""
    n = rel.n
    out = []
    cl = {}
    for k in range(1, n + 1):
        for X in itertools.combinations(range(n), k):
            m = D.mask(X)
            c = F.closure(X)
            cl[m] = c
            for a in D.bits(c & ~m):
                if all(not (cl[m & ~(1 << x)] >> a & 1) for x in X if k > 1):
                    out.append((m, a))
    return out


def key_only_lr_fds(rel, F, keys):
    out = []
    for K in keys:
        for a in range(rel.n):
            if not K >> a & 1:
                out.append((K, a))
    return out


def minimal_keys_from(rel, F, cands):
    ks = []
    for K in sorted(set(cands), key=lambda m: bin(m).count('1')):
        if F.closure(D.bits(K)) == rel.full and not any(k & K == k for k in ks):
            ks.append(K)
    return ks


def bpi(rel, F, fz, lr):
    """single-column sharding keys allowed by B-pi and by the containment rule"""
    feas = fz
    cont = fz
    keys_frozen = []
    for X, a in lr:
        cont &= X
        stable = (F.closure(D.bits(X & fz)) == rel.full) or ((X | (1 << a)) & ~fz) == 0
        if not stable:
            feas &= F.closure(D.bits(X & fz))
    return bin(feas).count('1'), bin(cont).count('1')


def bpi_random(count=2000, seed=11):
    rng = random.Random(seed)
    rows = []
    for _ in range(count):
        nA = rng.randint(4, 8)
        sch, Fz = gen_fd(rng, 1, nA, rng.randint(1, 4))
        rel = D.index_schema(sch)['R0']
        F = rel.fdset()
        lr = left_reduced_fds(rel, F)
        fz = D.fz_mask(rel, Fz)
        b, c = bpi(rel, F, fz, lr)
        rows.append((b, c, bin(fz).count('1'), D.dec_fd(sch, Fz)[0]))
    return dict(relations=count, frozen_cols_mean=statistics.mean(r[2] for r in rows),
                bpi_mean=statistics.mean(r[0] for r in rows), contain_mean=statistics.mean(r[1] for r in rows),
                bpi_none=sum(r[0] == 0 for r in rows), contain_none=sum(r[1] == 0 for r in rows),
                bpi_more=sum(r[0] > r[1] for r in rows), contain_more=sum(r[1] > r[0] for r in rows),
                bpi_when_not_os_mean=statistics.mean([r[0] for r in rows if not r[3]] or [0]))


def bpi_gitlab(g, sch, copy_fds=False):
    """GitLab with keys from unique indexes; Fz = PK, FK columns and sharding-key columns
    (pointers frozen), every other column mutable.  Per table: is the declared sharding key
    allowed by B-pi, and how many single columns are.  copy_fds: also the FDs c -> s_R that the
    inferred sharding-key copies R[c s_R] <= S[id s_S] imply (S.id is a key, so the pointer
    determines the copy); they join the left-reduced FDs and the closures."""
    if not sch['fds']:
        return None
    extra = {}
    if copy_fds:
        for R, c, cR, S, cS, root in G.copies(g, sch):
            if R != S and c != cR:
                extra.setdefault(R, set()).add((c, cR))
        sch = dict(sch, fds=sch['fds'] + [(R, [c], cR) for R in sorted(extra) for c, cR in sorted(extra[R])])
    rels = D.index_schema(sch)
    fkcols = {(R, x) for R, X, S, Y in sch['inds'] for x in X}
    T = g['tables']
    res = dict(tables=0, with_mutable_key=0, declared_sk_ok=0, declared_sk_bad=0, declared_sk_bad_no_column=0,
               bpi_cols=[], contain_cols=[], bad_examples=[])
    for r, rel in rels.items():
        F = rel.fdset()
        keys = minimal_keys_from(rel, F, [D.mask(rel.pk)] + [D.mask(L) for L, a in rel.decl])
        lr = key_only_lr_fds(rel, F, keys)
        lr += [(D.mask([rel.ix[c]]), rel.ix[cR]) for c, cR in sorted(extra.get(r, ()))
               if c in rel.ix and cR in rel.ix]
        fzs = set(sch['pk'][r]) | {x for (R, x) in fkcols if R == r} | set(T.get(r, {}).get('sharding_key', {}))
        fz = D.mask(rel.ix[a] for a in fzs if a in rel.ix)
        b, c = bpi(rel, F, fz, lr)
        res['tables'] += 1
        res['bpi_cols'].append(b)
        res['contain_cols'].append(c)
        if any(K & ~fz for K in keys):
            res['with_mutable_key'] += 1
        sk = [a for a in T.get(r, {}).get('sharding_key', {}) if a in rel.ix]
        if sk:
            m = D.mask(rel.ix[a] for a in sk)
            feas = fz
            for X, a in lr:
                if not ((F.closure(D.bits(X & fz)) == rel.full) or ((X | (1 << a)) & ~fz) == 0):
                    feas &= F.closure(D.bits(X & fz))
            if any(feas >> rel.ix[a] & 1 for a in sk):
                res['declared_sk_ok'] += 1
            else:
                res['declared_sk_bad'] += 1
                res['declared_sk_bad_no_column'] += feas == 0
                res['bad_examples'].append((r, sk, [[rel.attrs[i] for i in D.bits(K)] for K in keys if K & ~fz],
                                            [rel.attrs[i] for i in D.bits(feas)]))
    res['bpi_cols_mean'] = statistics.mean(res['bpi_cols'])
    res['contain_cols_mean'] = statistics.mean(res['contain_cols'])
    res['bpi_zero'] = sum(1 for x in res['bpi_cols'] if x == 0)
    res['contain_zero'] = sum(1 for x in res['contain_cols'] if x == 0)
    del res['bpi_cols'], res['contain_cols']
    return res


def main(outp):
    g = G.load()
    sch = G.base(g)
    k, kst = G.k_fragment(g, sch)
    out = {'source': sch['source'], 'gitlab_k_fragment': kst}
    out['m2_gitlab'] = m2_study(k, 'GitLab')
    rnd = []
    for s in range(20):
        rng = random.Random(100 + s)
        sk, _ = gen_K(rng, 72, 6, 0, 1)
        rnd.append(m2_study(sk, 'random', draws=50, seed=s))
    out['m2_random_dag_72'] = dict(
        unit_deg_over_opt_median=statistics.median(r['all']['unit']['w_deg'] / max(1, r['all']['unit']['w_opt']) for r in rnd),
        random_deg_over_opt_median=statistics.median(r['all']['random_weights']['deg_over_opt_median'] for r in rnd),
        star_share_median=statistics.median(r['all']['star_components'] / max(1, r['all']['components']) for r in rnd))
    out['bpi_random'] = bpi_random()
    out['bpi_gitlab'] = bpi_gitlab(g, sch)
    out['bpi_gitlab_copy_fds'] = bpi_gitlab(g, sch, copy_fds=True)
    json.dump(out, open(outp, 'w'), indent=1)
    print(json.dumps(out, indent=1)[:6000])


if __name__ == '__main__':
    main(sys.argv[1])
