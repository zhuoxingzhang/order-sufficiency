"""Beyond class K: the condition G |= Sigma of Theorem 3.5 against exhaustive semantics.

Schemas.  One random generator composes four features that the final class K excludes or
restricts (gen() below): composite pointers into composite primary keys; pointers that meet
PK(R); attributes that two INDs of R share, so that one pointer and copy lead into two relations
(the mechanism of Example 4.9 when one of them is frozen); and copies of copies.  Only primary-key
FDs, acyclic INDs, at most 4 relations of at most 4 attributes, at most 8 non-key attributes;
distinct schemas only, every Fz >= PK.  The named examples of the paper (Example 4.9, the diamond
and the crossing pair of the appendix) run as well.  Each schema is classified by
deciders.parse_K (final class K) and by its features.

The condition.  G (paper Definition 3.4) and G |= Sigma are decided by the chase, which terminates
and is exact for acyclic INDs with FDs, where finite and unrestricted implication coincide:
  Sigma+ FDs   closure of X: chase two tuples that agree on X;
  Sigma+ INDs  chase one fresh tuple t of R; for every tuple u of S in the result, the pairs
               (x, y) with t[x] = u[y] form the largest implied INDs from R to S (u = t gives the
               implied INDs of R into itself);
  M* (frozen closure), G's FDs and INDs as in the paper, the INDs of G as maximal injective
  pair sets; G |= Sigma iff the chase of a fresh tuple under G finds the witness of every IND of
  Sigma (the primary keys are frozen keys and always in G).
The chase of G is capped at 400 tuples (counted as cap_hit; not reached in practice).

Semantics.
  condition holds   ref_order2.Sem (as in selfcheck.py) over every update with 4 entities and
                    domain 2 must show no non-executable and no non-commutative update;
  condition fails   in class K first the construction of k_witness.py (swap or copy pattern,
                    completed by a chase and verified by Sem); then Sem over 3, 4 and 5 entities
                    with domain 2 (work bound 20M legality checks); if no non-executable update
                    turns up, the SAT search of ksat.py
                    over 3 values: complete over 5 entities, then up to 9 entities with at most
                    4 changed, 5 or 6 changed, or 7 changed.  If it finds none either, the task is a disagreement: order-sufficient
                    within these bounds although G does not imply Sigma.  For these, ksat looks for
                    a non-commutative update (5 entities, 3 values).
  in class K        also deciders.dec_K (Theorem 4.7), which must equal G |= Sigma.
Usage (needs pycosat): python -I k_beyond.py <random seeds> <seed> <procs> <out.jsonl>"""
import sys, os, json, random, itertools, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from multiprocessing import Pool
import deciders as D
from ref_order2 import Schema
from selfcheck import BoundedSem, WorkExceeded, ref_schema, frozen_sets, BUDGET

TIERS = [(3, 2), (4, 2), (5, 2)]
WORK = 20_000_000
CAP = 400


class UF:
    def __init__(self):
        self.p = []

    def new(self):
        self.p.append(len(self.p))
        return len(self.p) - 1

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a == b:
            return False
        self.p[b] = a
        return True


def chase(rels, fds, inds, tuples, uf, cap=CAP):
    """rels: r -> attrs; fds: (r, X, a); inds: (R, X, S, Y), attribute names; tuples: list of
    (r, {attr: var}), extended in place.  Returns False if the cap is hit."""
    changed = True
    while changed:
        changed = False
        for r, X, a in fds:
            seen = {}
            for rr, t in tuples:
                if rr == r:
                    k = tuple(uf.find(t[x]) for x in X)
                    if k in seen:
                        changed |= uf.union(seen[k][a], t[a])
                    else:
                        seen[k] = t
        for R, X, S, Y in inds:
            have = {tuple(uf.find(u[y]) for y in Y) for s, u in tuples if s == S}
            for rr, t in list(tuples):
                if rr != R:
                    continue
                k = tuple(uf.find(t[x]) for x in X)
                if k not in have:
                    u = {b: uf.new() for b in rels[S]}
                    for x, y in zip(X, Y):
                        u[y] = t[x]
                    tuples.append((S, u))
                    have.add(k)
                    changed = True
                    if len(tuples) > cap:
                        return False
    return True


def maximal_injective(Q):
    Q = sorted(Q)
    if len(Q) > 14:
        raise ValueError('pair set too large')
    out = []
    for k in range(len(Q), 0, -1):
        for sub in itertools.combinations(Q, k):
            xs, ys = [x for x, _ in sub], [y for _, y in sub]
            if len(set(xs)) < k or len(set(ys)) < k:
                continue
            s = frozenset(sub)
            if not any(s < o for o in out):
                out.append(s)
    return out


class Theory:
    """Sigma+ facts of a schema with primary-key FDs and acyclic INDs."""

    def __init__(self, sch):
        self.rels = {r: list(a) for r, a in sch['rels'].items()}
        self.pk = {r: list(p) for r, p in sch['pk'].items()}
        self.inds = [(R, list(X), S, list(Y)) for R, X, S, Y in sch['inds']]
        self.fds = [(r, self.pk[r], a) for r in self.rels for a in self.rels[r] if a not in self.pk[r]]
        self.cl = {}
        for r, at in self.rels.items():
            for k in range(1, len(at) + 1):
                for X in itertools.combinations(at, k):
                    self.cl[(r, frozenset(X))] = self.closure(r, X)
        self.keys = {}
        for r, at in self.rels.items():
            ks = []
            for k in range(1, len(at) + 1):
                for X in itertools.combinations(at, k):
                    X = frozenset(X)
                    if self.cl[(r, X)] == set(at) and not any(K <= X for K in ks):
                        ks.append(X)
            self.keys[r] = ks
        self.maxinds = []
        for R in self.rels:
            uf = UF()
            t = {a: uf.new() for a in self.rels[R]}
            tuples = [(R, t)]
            assert chase(self.rels, self.fds, self.inds, tuples, uf, cap=10 ** 4)
            for S, u in tuples:
                P = frozenset((x, y) for x in self.rels[R] for y in self.rels[S] if uf.find(t[x]) == uf.find(u[y]))
                if u is t:
                    P = P if any(x != y for x, y in P) else None
                if P:
                    self.maxinds.append((R, S, P))
        self.maxinds = list(dict.fromkeys(self.maxinds))

    def closure(self, r, X):
        uf = UF()
        t1 = {a: uf.new() for a in self.rels[r]}
        t2 = {a: (t1[a] if a in X else uf.new()) for a in self.rels[r]}
        tuples = [(r, t1), (r, t2)]
        assert chase(self.rels, self.fds, self.inds, tuples, uf, cap=10 ** 4)
        return {a for a in self.rels[r] if uf.find(t1[a]) == uf.find(t2[a])}

    def frozen_closure(self, Fz):
        M = {r: set(Fz[r]) for r in self.rels}
        ch = True
        while ch:
            ch = False
            for R, S, P in self.maxinds:
                for K in self.keys[S]:
                    if K <= M[S] and all(any(y == k and x in M[R] for x, y in P) for k in K):
                        for x, y in P:
                            if y in M[S] and x not in M[R]:
                                M[R].add(x)
                                ch = True
        return M

    def G(self, Fz):
        M = self.frozen_closure(Fz)
        fds = []
        for r, at in self.rels.items():
            for K in self.keys[r]:
                if K <= M[r]:
                    fds += [(r, sorted(K), a) for a in at if a not in K]
            for k in range(1, len(M[r]) + 1):
                for X in itertools.combinations(sorted(M[r]), k):
                    fds += [(r, list(X), a) for a in sorted(self.cl[(r, frozenset(X))] & M[r]) if a not in X]
        found = set()
        for R, S, P in self.maxinds:
            fk = [K for K in self.keys[S] if K <= M[S]]
            for Q in maximal_injective({(x, y) for x, y in P if y in M[S]}):
                found.add((R, S, Q))
            for Q in maximal_injective({(x, y) for x, y in P if x in M[R]}):
                if any(K <= {y for _, y in Q} for K in fk):
                    found.add((R, S, Q))
        inds = []
        for R, S, Q in sorted(found, key=lambda f: (f[0], f[1], sorted(f[2]))):
            if R == S and all(x == y for x, y in Q):
                continue
            pairs = sorted(Q)
            inds.append((R, [x for x, _ in pairs], S, [y for _, y in pairs]))
        return M, fds, inds

    def condition(self, Fz):
        """(G |= Sigma, failing INDs of Sigma, cap hit)"""
        M, gf, gi = self.G(Fz)
        bad, cap = [], False
        for R, X, S, Y in self.inds:
            uf = UF()
            t = {a: uf.new() for a in self.rels[R]}
            tuples = [(R, t)]
            if not chase(self.rels, gf, gi, tuples, uf):
                cap = True
            want = tuple(uf.find(t[x]) for x in X)
            if not any(s == S and tuple(uf.find(u[y]) for y in Y) == want for s, u in tuples):
                bad.append('%s[%s]<=%s[%s]' % (R, ','.join(X), S, ','.join(Y)))
        return not bad, bad, cap

    def flags(self, Fz):
        """key_via_ind: a relation has a key besides its primary key under Sigma+.
        pullback_shared: an IND of Sigma+ whose right side lies in M* and contains a key of S pulls
        an FD back onto a mutable pointer, and the pulled attribute or the pointer occurs in
        another IND of Sigma (the mechanism of Example 4.9; an approximation of the paper's
        'pull no FD back from frozen reference data')."""
        key_via_ind = any(len(ks) > 1 for ks in self.keys.values())
        M = self.frozen_closure(Fz)
        used = {}
        for i, (R, X, S, Y) in enumerate(self.inds):
            for x in X:
                used.setdefault((R, x), set()).add(i)
        pb = False
        for R, S, P in self.maxinds:
            if R == S:
                continue
            for K in self.keys[S]:
                ptr = {x for x, y in P if y in K}
                if not all(any(y == k for _, y in P) for k in K) or ptr <= M[R]:
                    continue
                for x, y in P:
                    if y not in K and y in M[S] and x not in ptr:
                        if len(used.get((R, x), set()) | set().union(*[used.get((R, p), set()) for p in ptr])) > 1:
                            pb = True
        inj = any(set(x for x, y in zip(X, Y) if y in self.pk[S]) >= set(self.pk[R]) for R, X, S, Y in self.inds)
        return dict(key_via_ind=key_via_ind, pullback_shared=pb, injective_pointer=inj)


# ---------------------------------------------------------------------------- generator
NAMED = {
    # Example 4.9: R[A,D] <= T[P,Q] and R[A,D] <= S[C,E]
    'example_4_4': {'rels': {'T': ['P', 'Q'], 'S': ['C', 'E'], 'R': ['K', 'A', 'D']},
                    'pk': {'T': ['P'], 'S': ['C'], 'R': ['K']}, 'fds': [],
                    'inds': [('R', ['A', 'D'], 'T', ['P', 'Q']), ('R', ['A', 'D'], 'S', ['C', 'E'])]},
    # the diamond of the appendix
    'diamond': {'rels': {'T': ['K1', 'K2', 'V'], 'S': ['L', 'E'], 'R': ['K', 'A', 'B', 'D']},
                'pk': {'T': ['K1', 'K2'], 'S': ['L'], 'R': ['K']}, 'fds': [],
                'inds': [('R', ['A', 'D'], 'S', ['L', 'E']), ('R', ['A', 'B'], 'T', ['K1', 'K2']),
                         ('T', ['K1', 'V'], 'S', ['L', 'E'])]},
    # the crossing pair of the appendix
    'crossing': {'rels': {'T': ['P', 'V'], 'S': ['C', 'E', 'F'], 'R': ['K', 'A', 'D'], 'Q': ['L', 'B', 'G']},
                 'pk': {'T': ['P'], 'S': ['C'], 'R': ['K'], 'Q': ['L']}, 'fds': [],
                 'inds': [('R', ['A', 'D'], 'T', ['P', 'V']), ('Q', ['B', 'G'], 'T', ['P', 'V']),
                          ('R', ['A', 'D'], 'S', ['C', 'E']), ('Q', ['B', 'G'], 'S', ['C', 'F'])]},
}


def gen(rng):
    """Relations R0..R(n-1); INDs only from Ri to Rj with j < i.  Every IND R[P D] <= S[PK(S) C]:
    each pointer position reuses a primary-key attribute of R (prob. 0.25), reuses an attribute
    that R already uses in another IND (0.15), or is a fresh column; each copy reuses an attribute
    of R outside its primary key (0.3) or is fresh.  Sources C are any non-key attributes of S,
    copies included.  At most 4 attributes per relation and 8 non-key attributes."""
    n = rng.choice([2, 3, 3, 4])
    rels, pk = {}, {}
    for i in range(n):
        r = 'R%d' % i
        pk[r] = ['k%d' % i] if rng.random() < 0.7 else ['k%d' % i, 'l%d' % i]
        rels[r] = list(pk[r]) + (['a%d' % i] if rng.random() < 0.7 else [])
    inds, used = [], {r: [] for r in rels}
    fresh = [0]

    def new(r, pre):
        fresh[0] += 1
        a = '%s%d' % (pre, fresh[0])
        rels[r].append(a)
        return a
    for i in range(1, n):
        R = 'R%d' % i
        for j in rng.sample(range(i), min(i, rng.choice([1, 1, 2]))):
            S = 'R%d' % j
            X = []
            for _ in pk[S]:
                u = rng.random()
                opts_pk = [a for a in pk[R] if a not in X]
                opts_used = [a for a in used[R] if a not in X]
                if u < 0.25 and opts_pk:
                    X.append(rng.choice(opts_pk))
                elif u < 0.4 and opts_used:
                    X.append(rng.choice(opts_used))
                else:
                    X.append(new(R, 'p'))
            src = [a for a in rels[S] if a not in pk[S]]
            C = rng.sample(src, min(len(src), rng.choice([0, 1, 1, 2])))
            Y = list(pk[S]) + C
            for _ in C:
                opts = [a for a in rels[R] if a not in X and a not in pk[R]]
                if rng.random() < 0.3 and opts:
                    X.append(rng.choice(opts))
                else:
                    X.append(new(R, 'd'))
            inds.append((R, X, S, Y))
            used[R] += [a for a in X if a not in used[R]]
    if not inds or max(len(a) for a in rels.values()) > 4 or sum(len(a) - len(pk[r]) for r, a in rels.items()) > 8:
        return None
    return {'rels': rels, 'pk': pk, 'fds': [], 'inds': inds}


def features(sch):
    pk = sch['pk']
    cnt = {}
    for R, X, S, Y in sch['inds']:
        for x in X:
            cnt[(R, x)] = cnt.get((R, x), 0) + 1
    copies = {(R, x) for R, X, S, Y in sch['inds'] for x, y in zip(X, Y) if y not in pk[S]}
    return dict(
        composite=any(len(pk[S]) > 1 for R, X, S, Y in sch['inds']),
        pk_overlap=any(set(x for x, y in zip(X, Y) if y in pk[S]) & set(pk[R]) for R, X, S, Y in sch['inds']),
        shared=any(c > 1 for c in cnt.values()),
        chain=any((S, y) in copies for R, X, S, Y in sch['inds'] for y in Y if y not in pk[S]))


def schemas(n, seed):
    """the named examples, then the distinct random schemas of the seeds seed*100000 + j, j < n"""
    out = [('named:' + k, v) for k, v in NAMED.items()]
    seen = set()
    for j in range(n):
        rng = random.Random(seed * 100000 + j)
        sch = None
        while sch is None:
            sch = gen(rng)
        key = json.dumps(sch, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(('seed:%d' % (seed * 100000 + j), sch))
    return out


# ---------------------------------------------------------------------------- tasks
_cache = {}
_schs = {}


def setup(sid):
    if _cache.get('key') != sid:
        sch = _schs[sid]
        _cache.clear()
        _cache.update(key=sid, sch=sch, th=Theory(sch), fzs=list(frozen_sets(sch)), refs={},
                      in_K=D.parse_K(sch)[0])
    return _cache


def search(c, Fz, tiers, noncomm=False):
    """first tier with a non-executable update, or None; and a non-commutative update if asked"""
    out = {'cycle': None, 'noncomm': None, 'work_cut': 0, 'complete': True}
    for n, d in tiers:
        if d not in c['refs']:
            c['refs'][d] = ref_schema(c['sch'], d)
        sem = BoundedSem(c['refs'][d], {r: set(v) for r, v in Fz.items()}, WORK)
        try:
            o = sem.search(n, stop_cycle=True, want_noncomm=noncomm, budget=BUDGET)
        except WorkExceeded:
            out['work_cut'] += 1
            out['complete'] = False
            continue
        out['complete'] &= o['complete']
        if o.get('noncomm') and not out['noncomm']:
            out['noncomm'] = [n, d]
        if o['cycle']:
            out['cycle'] = [n, d]
            out['witness'] = o['cycle']
            break
    return out


SAT_STAGES = [(5, 5, 3, 2), (9, 4, 3, 2), (9, 6, 3, 5), (9, 7, 3, 7)]     # (N, kmax, D, kmin)


class SatSch:
    """the schema object that ksat.search reads"""
    def __init__(self, sch):
        self.rels = {r: list(a) for r, a in sch['rels'].items()}
        self.pk = {r: list(p) for r, p in sch['pk'].items()}
        self.uk = {r: [] for r in sch['rels']}
        self.inds = [{'R': R, 'X': list(X), 'S': S, 'Y': list(Y)} for R, X, S, Y in sch['inds']]


def job(args):
    sid, i = args
    c = setup(sid)
    Fz = c['fzs'][i]
    t0 = time.perf_counter()
    ok, bad, cap = c['th'].condition(Fz)
    row = {'fz': {r: sorted(v) for r, v in Fz.items()}, 'cond': ok, 'bad': bad, 'cap': cap,
           'us': 1e6 * (time.perf_counter() - t0)}
    row['ref_cond'] = ref_schema(c['sch'], 2).condition({r: set(v) for r, v in Fz.items()})[0]
    if c['in_K']:
        row['dec_K'] = D.dec_K(c['sch'], Fz)[0]
    w = None
    if not ok and c['in_K']:
        import k_witness
        w = k_witness.witness(c['sch'], {r: set(v) for r, v in Fz.items()})
    if ok:
        s = search(c, Fz, TIERS[1:2], noncomm=True)
    elif w:
        s = {'cycle': ['construction', w['rows'], w['changed']], 'noncomm': None, 'work_cut': 0, 'complete': True,
             'witness': [w['I'], w['J']]}
    else:
        s = search(c, Fz, TIERS)
        if not s['cycle']:
            import ksat
            sat = SatSch(c['sch'])
            fz = {r: set(v) for r, v in Fz.items()}
            for N, kmax, Dm, kmin in SAT_STAGES:
                w = ksat.search(sat, fz, N, kmax, Dm, 'nonexec', kmin=kmin)
                if w:
                    s['cycle'] = ['sat', N, kmax]
                    s['witness'] = w[1]
                    break
            if not s['cycle']:
                nc = ksat.search(sat, fz, 5, 5, 3, 'noncomm')
                s['noncomm'] = ['sat', 5, 5] if nc else None
                s['noncomm_witness'] = nc[1] if nc else None
                row['flags'] = c['th'].flags(Fz)
    row.update(s)
    return {'sid': sid, 'i': i, 'row': row}


GROUPS = ['in_K', 'outside_K', 'composite', 'pk_overlap', 'shared', 'chain', 'none_of_the_four']


def summarize(res):
    out = {}
    for g in GROUPS:
        sel = []
        for r in res:
            if r['sid'].startswith('named:'):
                continue
            f = r['features']
            if (g == 'in_K' and r['in_K']) or (g == 'outside_K' and not r['in_K']) or \
               (f.get(g) and not r['in_K']) or (g == 'none_of_the_four' and not r['in_K'] and not any(f.values())):
                sel.append(r)
        s = dict(schemas=len(sel), tasks=0, cond=0, cond_violated=0, cond_noncomm=0, not_cond_cycle=0, sat_found=0,
                 constructed=0, disagree=0, disagree_noncomm=0, disagree_pullback_shared=0, disagree_key_via_ind=0,
                 disagree_injective_pointer=0, disagree_neither=0,
                 ref_cond_differs=0, ref_says_ok_cond_not=0, cap_hit=0, work_cut_tasks=0, dec_K_differs=0)
        for r in sel:
            for row in r['rows']:
                s['tasks'] += 1
                s['cap_hit'] += row['cap']
                s['dec_K_differs'] += 'dec_K' in row and row['dec_K'] != row['cond']
                s['work_cut_tasks'] += row['work_cut'] > 0
                s['ref_cond_differs'] += row['ref_cond'] != row['cond']
                s['ref_says_ok_cond_not'] += row['ref_cond'] and not row['cond']
                if row['cond']:
                    s['cond'] += 1
                    s['cond_violated'] += bool(row['cycle'])
                    s['cond_noncomm'] += bool(row['noncomm'])
                elif row['cycle']:
                    s['not_cond_cycle'] += 1
                    s['sat_found'] += row['cycle'][0] == 'sat'
                    s['constructed'] += row['cycle'][0] == 'construction'
                else:
                    s['disagree'] += 1
                    s['disagree_noncomm'] += bool(row['noncomm'])
                    f = row.get('flags', {})
                    s['disagree_pullback_shared'] += f.get('pullback_shared', False)
                    s['disagree_key_via_ind'] += f.get('key_via_ind', False)
                    s['disagree_injective_pointer'] += f.get('injective_pointer', False)
                    s['disagree_neither'] += not (f.get('key_via_ind') or f.get('pullback_shared') or f.get('injective_pointer'))
        out[g] = s
    return out


def named(res):
    out = {}
    for r in res:
        if r['sid'].startswith('named:'):
            rows = r['rows']
            out[r['sid'][6:]] = dict(tasks=len(rows), cond=sum(x['cond'] for x in rows),
                                     cycle=sum(bool(x['cycle']) for x in rows),
                                     disagree=sum(not x['cond'] and not x['cycle'] for x in rows),
                                     disagree_noncomm=sum(not x['cond'] and not x['cycle'] and bool(x['noncomm']) for x in rows))
    return out


def minimal_disagreements(res, k=6):
    cands = []
    for r in res:
        if r['in_K']:
            continue
        sch = r['sch']
        size = (len(sch['rels']), sum(len(a) for a in sch['rels'].values()), len(sch['inds']))
        for i, row in enumerate(r['rows']):
            if not row['cond'] and not row['cycle']:
                f = row.get('flags', {})
                neither = not (f.get('key_via_ind') or f.get('pullback_shared') or f.get('injective_pointer'))
                cands.append((not neither, r['sid'].startswith('named:'), size,
                              -sum(len(v) for v in row['fz'].values()), r['sid'], i))
    cands.sort()
    out = []
    for c in cands[:k]:
        r = next(x for x in res if x['sid'] == c[4])
        row = r['rows'][c[5]]
        out.append(dict(sid=c[4], sch=r['sch'], features=r['features'], fz=row['fz'], bad=row['bad'],
                        noncomm=row['noncomm'], noncomm_witness=row.get('noncomm_witness'), flags=row.get('flags')))
    return out


if __name__ == '__main__':
    ns, seed, procs, outp = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    t0 = time.time()
    lst = schemas(ns, seed)
    _schs.update(lst)
    nfz = {sid: sum(1 for _ in frozen_sets(sch)) for sid, sch in lst}
    done = {}
    if os.path.exists(outp + '.partial'):
        for line in open(outp + '.partial'):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            done[(r['sid'], r['i'])] = r['row']
    todo = [(sid, i) for sid, _ in lst for i in range(nfz[sid]) if (sid, i) not in done]
    with open(outp + '.partial', 'a') as part, Pool(procs, initializer=_schs.update, initargs=(dict(lst),)) as pool:
        for r in pool.imap_unordered(job, todo, chunksize=4):
            done[(r['sid'], r['i'])] = r['row']
            part.write(json.dumps(r) + '\n')
            part.flush()
    res = [{'sid': sid, 'sch': sch, 'in_K': D.parse_K(sch)[0], 'features': features(sch),
            'rows': [done[(sid, i)] for i in range(nfz[sid])]} for sid, sch in lst]
    with open(outp, 'w') as f:
        for r in res:
            f.write(json.dumps(r) + '\n')
    os.remove(outp + '.partial')
    out = {'schemas': len(lst), 'tasks': sum(nfz.values()), 'groups': summarize(res), 'named': named(res),
           'minimal_disagreements': minimal_disagreements(res), 'seconds': round(time.time() - t0, 1)}
    print(json.dumps(out, indent=1))
