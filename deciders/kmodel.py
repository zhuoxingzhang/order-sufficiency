"""Schemas of class K and its relaxations, implication by the chase, and the conditions compared.

A schema has relations with attribute lists, a primary key PK(R), optional unique keys UK(R), and
INDs in the K form  R[P D] <= S[K C]  with K = PK(S) (pointer P aligned with K, copies D aligned
with sources C).  Only keys are declared FDs.  The IND graph is acyclic.

Implication.  The chase of one tuple (resp. two tuples) with keys as EGDs and the INDs terminates
because the INDs are acyclic, so it decides finite implication of INDs (resp. FDs).
  corr(R)            maximal correspondences r[x] = u[y] between the chased tuple r of R and every
                     chased tuple u (these generate every IND of Sigma+ with left side R)
  fd_closure(R, X)   attributes A with Sigma |= R: X -> A
  keys(R)            minimal keys under Sigma+
  mstar_chase(Fz)    the frozen closure M* of Definition 3.1 over Sigma+
  stab_implies(Fz)   Sigma_stab |= Sigma (Theorem 3.5), by the chase with the stable consequences

Syntactic conditions (per IND rho = R[P D] <= S[K C], position i):
  cond_orig     the condition of Theorem 4.5:  C_i in Fz  or  (P <= Fz and D_i in Fz)
  mstar_syn     closure rule on declared INDs: if a key U of S (PK or UK) lies inside M*(S) and inside
                the right side of rho, and the R-attributes at U's positions lie in M*(R), then every
                R-attribute whose S-partner lies in M*(S) joins M*(R)
  cond_new      unique keys inside M*, and every position has C_i in M*(S), or P <= M*(R) and
                D_i in M*(R), or is pinned
  pinned        some other IND tau = T[P' D'] <= S[K C'] copies C_i into a D'_j with P' and D'_j in
                M*(T), and Sigma |= R[P] <= T[P'] (a keyed route; route_bfs or the chase)
Run with python -I.  No third-party modules.
"""
import itertools


class Sch:
    def __init__(self, rels, pk, inds, uk=None, name=''):
        self.name = name
        self.rels = {r: tuple(a) for r, a in rels.items()}
        self.pk = {r: tuple(pk[r]) for r in self.rels}
        self.uk = {r: [tuple(u) for u in (uk or {}).get(r, [])] for r in self.rels}
        self.inds = []
        for d in inds:
            d = dict(d)
            d['P'], d['D'], d['C'] = tuple(d['P']), tuple(d.get('D', ())), tuple(d.get('C', ()))
            assert len(d['P']) == len(self.pk[d['S']]) and len(d['D']) == len(d['C'])
            d['X'] = d['P'] + d['D']
            d['Y'] = self.pk[d['S']] + d['C']
            self.inds.append(d)
        self.idx = {r: {a: i for i, a in enumerate(at)} for r, at in self.rels.items()}
        self.nonkey = {r: [a for a in at if a not in self.pk[r]] for r, at in self.rels.items()}
        self._corr = {}
        self._fdcl = {}
        self._keys = {}

    def spec(self):
        return {'rels': {r: list(a) for r, a in self.rels.items()},
                'pk': {r: list(a) for r, a in self.pk.items()},
                'uk': {r: [list(u) for u in us] for r, us in self.uk.items() if us},
                'inds': [{k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()
                          if k in ('R', 'P', 'D', 'S', 'C')} for d in self.inds],
                'name': self.name}

    @staticmethod
    def from_spec(s):
        return Sch(s['rels'], s['pk'], s['inds'], s.get('uk'), s.get('name', ''))

    def keydeps(self):
        """Declared keys as FDs (R, L, a) over attribute names."""
        out = []
        for r, at in self.rels.items():
            for K in [self.pk[r]] + self.uk[r]:
                for a in at:
                    if a not in K:
                        out.append((r, tuple(K), a))
        return out

    def ind_triples(self):
        return [(d['R'], d['X'], d['S'], d['Y']) for d in self.inds]

    # ------------------------------------------------------------------ chase
    def chase(self, start, fds, inds, limit=3000):
        """start: list of (rel, {attr: var}) with vars as ints (shared ints = equal).
        fds: (R, L, a); inds: (R, X, S, Y).  Returns (tuples, find)."""
        par = []

        def new():
            par.append(len(par))
            return len(par) - 1

        def find(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x

        def union(x, y):
            x, y = find(x), find(y)
            if x == y:
                return False
            par[max(x, y)] = min(x, y)
            return True

        names = {}
        tuples = {r: [] for r in self.rels}
        for (r, assign) in start:
            t = []
            for a in self.rels[r]:
                v = assign.get(a)
                if v is None:
                    t.append(new())
                else:
                    while v not in names:
                        names[v] = new()
                    t.append(names[v])
            tuples[r].append(t)
        fdi = [(R, [self.idx[R][x] for x in L], self.idx[R][a]) for (R, L, a) in fds]
        indi = [(R, [self.idx[R][x] for x in X], S, [self.idx[S][y] for y in Y]) for (R, X, S, Y) in inds]
        changed = True
        while changed:
            changed = False
            for (R, L, a) in fdi:
                seen = {}
                for t in tuples[R]:
                    k = tuple(find(t[i]) for i in L)
                    if k in seen:
                        if union(seen[k][a], t[a]):
                            changed = True
                    else:
                        seen[k] = t
            for r in tuples:
                ded = {}
                for t in tuples[r]:
                    ded.setdefault(tuple(find(v) for v in t), t)
                tuples[r] = list(ded.values())
            for (R, X, S, Y) in indi:
                have = {tuple(find(u[j]) for j in Y) for u in tuples[S]}
                for t in list(tuples[R]):
                    k = tuple(find(t[i]) for i in X)
                    if k not in have:
                        u = [new() for _ in self.rels[S]]
                        for i, j in zip(X, Y):
                            u[j] = t[i]
                        tuples[S].append(u)
                        have.add(k)
                        changed = True
            if sum(len(v) for v in tuples.values()) > limit:
                raise RuntimeError('chase limit')
        return tuples, find, names

    def corr(self, R):
        """Maximal correspondences of the chased tuple of R: list of (S, pairs)."""
        if R not in self._corr:
            start = [(R, {a: ('r', a) for a in self.rels[R]})]
            tuples, find, names = self.chase(start, self.keydeps(), self.ind_triples())
            rv = {a: find(names[('r', a)]) for a in self.rels[R]}
            out = []
            for S, ts in tuples.items():
                for u in ts:
                    pairs = []
                    for y, v in zip(self.rels[S], u):
                        for x in self.rels[R]:
                            if rv[x] == find(v):
                                pairs.append((x, y))
                    if pairs:
                        out.append((S, tuple(pairs)))
            self._corr[R] = out
        return self._corr[R]

    def implies_ind(self, R, X, S, Y):
        for (S2, pairs) in self.corr(R):
            if S2 == S and all((x, y) in pairs for x, y in zip(X, Y)):
                return True
        return False

    def fd_closure(self, R, X, fds=None, inds=None):
        key = (R, frozenset(X)) if fds is None else None
        if key is not None and key in self._fdcl:
            return self._fdcl[key]
        fds = self.keydeps() if fds is None else fds
        inds = self.ind_triples() if inds is None else inds
        start = [(R, {a: (('s', a) if a in X else ('1', a)) for a in self.rels[R]}),
                 (R, {a: (('s', a) if a in X else ('2', a)) for a in self.rels[R]})]
        tuples, find, names = self.chase(start, fds, inds)
        cl = set(X) | {a for a in self.rels[R] if a not in X and
                       find(names[('1', a)]) == find(names[('2', a)])}
        if key is not None:
            self._fdcl[key] = cl
        return cl

    def keys(self, R):
        if R not in self._keys:
            at = self.rels[R]
            ks = []
            for n in range(len(at) + 1):
                for X in itertools.combinations(at, n):
                    if any(set(K) <= set(X) for K in ks):
                        continue
                    if self.fd_closure(R, X) >= set(at):
                        ks.append(frozenset(X))
            self._keys[R] = ks
        return self._keys[R]

    # ------------------------------------------------------------------ M* and Sigma_stab
    def mstar_chase(self, Fz):
        M = {r: set(Fz[r]) for r in self.rels}
        ch = True
        while ch:
            ch = False
            for R in self.rels:
                for (S, pairs) in self.corr(R):
                    for K in self.keys(S):
                        if not K <= M[S]:
                            continue
                        if all(any(x in M[R] for (x, y2) in pairs if y2 == y) for y in K):
                            for (x, y) in pairs:
                                if y in M[S] and x not in M[R]:
                                    M[R].add(x)
                                    ch = True
        return M

    def mstar_det(self, Fz):
        """Definition 3.1 with the key K replaced by any Z inside M(S) at positions in M(R) that
        determines Y_i under Sigma+ (two entities of S that agree on Z agree on Y_i in every state,
        and Z, Y_i are unchanged, so X_i is unchanged).  Keyed-route pins are the case Z = P'."""
        M = {r: set(Fz[r]) for r in self.rels}
        ch = True
        while ch:
            ch = False
            for R in self.rels:
                for (S, pairs) in self.corr(R):
                    Z = {y for (x, y) in pairs if x in M[R] and y in M[S]}
                    if not Z:
                        continue
                    cl = self.fd_closure(S, sorted(Z))
                    for (x, y) in pairs:
                        if x not in M[R] and y in M[S] and y in cl:
                            M[R].add(x)
                            ch = True
        return M

    @staticmethod
    def matchings(pairs, cap=256):
        """Maximal injective sub-matchings of a correspondence (pairs share values by class)."""
        xs = sorted({x for x, _ in pairs})
        ys = sorted({y for _, y in pairs})
        # classes: x and y are in the same class iff paired (pairs come from equal variables)
        cls = []
        seen = set()
        for x in xs:
            if x in seen:
                continue
            Y = sorted({y for (x2, y) in pairs if x2 == x})
            X = sorted({x2 for (x2, y) in pairs if y in Y})
            seen |= set(X)
            cls.append((X, Y))
        opts = []
        for X, Y in cls:
            o = []
            if len(X) <= len(Y):
                for perm in itertools.permutations(Y, len(X)):
                    o.append(tuple(zip(X, perm)))
            else:
                for perm in itertools.permutations(X, len(Y)):
                    o.append(tuple(zip(perm, Y)))
            opts.append(o)
        out = []
        for combo in itertools.product(*opts):
            out.append(tuple(p for c in combo for p in c))
            if len(out) > cap:
                raise RuntimeError('matching cap')
        return out

    def stable_set(self, Fz, M=None):
        M = M or self.mstar_chase(Fz)
        fkeys = {r: [K for K in self.keys(r) if K <= M[r]] for r in self.rels}
        fds = []
        for R, at in self.rels.items():
            for n in range(1, len(at) + 1):
                for X in itertools.combinations(at, n):
                    Xs = set(X)
                    cl = self.fd_closure(R, X)
                    for a in cl - Xs:
                        if any(K <= Xs for K in fkeys[R]) or (Xs | {a}) <= M[R]:
                            fds.append((R, X, a))
        inds = set()
        for R in self.rels:
            for (S, pairs) in self.corr(R):
                pa = [(x, y) for (x, y) in pairs if y in M[S]]
                if pa:
                    for m in self.matchings(pa):
                        inds.add((R, tuple(x for x, _ in m), S, tuple(y for _, y in m)))
                pb = [(x, y) for (x, y) in pairs if x in M[R]]
                if pb:
                    for m in self.matchings(pb):
                        ys = {y for _, y in m}
                        if any(K <= ys for K in fkeys[S]):
                            inds.add((R, tuple(x for x, _ in m), S, tuple(y for _, y in m)))
        return fds, sorted(inds), M

    def stab_implies(self, Fz, M=None):
        fds, inds, M = self.stable_set(Fz, M)
        bad = []
        for R in self.rels:
            mine = [d for d in self.inds if d['R'] == R]
            if mine:
                start = [(R, {a: ('r', a) for a in self.rels[R]})]
                tuples, find, names = self.chase(start, fds, inds)
                rv = {a: find(names[('r', a)]) for a in self.rels[R]}
                for d in mine:
                    want = tuple(rv[x] for x in d['X'])
                    if not any(tuple(find(u[self.idx[d['S']][y]]) for y in d['Y']) == want
                               for u in tuples[d['S']]):
                        bad.append(('ind', R, d['X'], d['S'], d['Y']))
            for U in self.uk[R]:
                if set(U) <= M[R]:
                    continue
                if self.fd_closure(R, U, fds, inds) < set(self.rels[R]):
                    bad.append(('uk', R, U))
        return not bad, bad

    # ------------------------------------------------------------------ syntactic conditions
    def all_fz(self, Fz):
        return {r: set(Fz.get(r, ())) | set(self.pk[r]) for r in self.rels}

    def cond_orig(self, Fz):
        bad = []
        for n, d in enumerate(self.inds):
            R, S = d['R'], d['S']
            for i, (c, x) in enumerate(zip(d['C'], d['D'])):
                if not (c in Fz[S] or (set(d['P']) <= Fz[R] and x in Fz[R])):
                    bad.append((n, i))
        return not bad, bad

    def mstar_syn(self, Fz):
        M = {r: set(Fz[r]) for r in self.rels}
        ch = True
        while ch:
            ch = False
            for d in self.inds:
                R, S = d['R'], d['S']
                part = dict(zip(d['Y'], d['X']))
                for U in [self.pk[S]] + self.uk[S]:
                    if set(U) <= M[S] and set(U) <= set(part) and all(part[u] in M[R] for u in U):
                        for y, x in part.items():
                            if y in M[S] and x not in M[R]:
                                M[R].add(x)
                                ch = True
        return M

    def route_bfs(self, R, P, T, PT):
        """Sigma |= R[P] <= T[PT] through key positions (pointer values travel only via keys)."""
        start = (R, tuple(P))
        seen = {start}
        todo = [start]
        while todo:
            (U, f) = todo.pop()
            if U == T and f == tuple(PT):
                return True
            for d in self.inds:
                if d['R'] != U or not set(f) <= set(d['P']):
                    continue
                K = self.pk[d['S']]
                g = tuple(K[d['P'].index(a)] for a in f)
                nxt = (d['S'], g)
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        return False

    def route_chase(self, R, P, T, PT):
        return self.implies_ind(R, P, T, PT)

    def pinned(self, n, i, M, route='bfs'):
        d = self.inds[n]
        rt = self.route_bfs if route == 'bfs' else self.route_chase
        for m, e in enumerate(self.inds):
            if m == n or e['S'] != d['S'] or e['R'] == d['R']:
                continue
            for j, c in enumerate(e['C']):
                if c != d['C'][i]:
                    continue
                if e['D'][j] in M[e['R']] and set(e['P']) <= M[e['R']] and rt(d['R'], d['P'], e['R'], e['P']):
                    return True
        return False

    def topo(self):
        """Relations with every IND target before its source (sinks first)."""
        order, left = [], set(self.rels)
        while left:
            for r in sorted(left):
                if all(d['S'] not in left for d in self.inds if d['R'] == r):
                    order.append(r)
                    left.remove(r)
                    break
        return order

    def route_pins(self, T, P, S, M):
        """Sources of the entity of S that T[P] references, fixed by another IND U[P' D'] <= S[K C']
        with P' and D'_j in M(U) and a keyed route T[P] <= U[P']."""
        out = set()
        for e in self.inds:
            if e['S'] != S or e['R'] == T:
                continue
            U = e['R']
            if not set(e['P']) <= M[U]:
                continue
            cs = {c for c, x in zip(e['C'], e['D']) if x in M[U]}
            if cs - out and self.route_bfs(T, P, U, e['P']):
                out |= cs
        return out

    def pin_closure(self, Fz):
        """M-hat with keyed-route pins inside the closure, alternated with Definition 3.1 over Sigma+
        until nothing changes."""
        M = self.mstar_chase(Fz)
        while True:
            M2 = self.mstar_chase(self.fixed_closure(Fz, base=M, pins=True))
            if M2 == M:
                return M
            M = M2

    def pin_lite(self, Fz):
        """Definition 3.1 over Sigma+ plus one rule: a copy D_i with a pointer in M(R) whose position
        is pinned (self.pinned) joins M(R).  No contexts, so partial copies of pointers are missed."""
        M = self.mstar_chase(Fz)
        while True:
            add = False
            for n, d in enumerate(self.inds):
                R = d['R']
                if not set(d['P']) <= M[R]:
                    continue
                for i, x in enumerate(d['D']):
                    if x not in M[R] and self.pinned(n, i, M):
                        M[R].add(x)
                        add = True
            if not add:
                return M
            M = self.mstar_chase(M)

    def fixed_closure(self, Fz, base=None, pins=False):
        """M-hat: attributes that no update changes, with pins along fixed pointers.
        fixed(T, Z) closes a set Z of attributes of an entity of T that are fixed in its context:
        an IND T[P D] <= S[K C] with P fixed reaches a fixed entity of S, whose sources copied into
        fixed copies are pinned there (context of S), and whose fixed attributes make the copies
        fixed (up).  With pins, sources pinned through keyed routes (route_pins) join the context.
        Contexts are memoised by (relation, pinned set)."""
        base = base or self.mstar_chase(Fz)
        M = {}
        memo = {}

        def fixed(T, Z):
            key = (T, Z)
            if key in memo:
                return memo[key]
            F = set(Z)
            ch = True
            while ch:
                ch = False
                for d in self.inds:
                    if d['R'] != T or not set(d['P']) <= F:
                        continue
                    ZS = M[d['S']] | {c for c, x in zip(d['C'], d['D']) if x in F}
                    if pins:
                        ZS |= self.route_pins(T, d['P'], d['S'], {r: M.get(r, base[r]) for r in self.rels})
                    ZS = frozenset(ZS)
                    FS = fixed(d['S'], ZS)
                    for c, x in zip(d['C'], d['D']):
                        if c in FS and x not in F:
                            F.add(x)
                            ch = True
            memo[key] = F
            return F
        for R in self.topo():
            M[R] = fixed(R, frozenset(base[R]))
        return M

    def stable_superkey_pointers(self, Fz):
        """INDs with copies whose mutable pointer is a superkey under Sigma_stab (injective)."""
        fds, inds, M = self.stable_set(Fz)
        out = []
        for n, d in enumerate(self.inds):
            if d['D'] and not set(d['P']) <= M[d['R']]:
                if self.fd_closure(d['R'], d['P'], fds, inds) >= set(self.rels[d['R']]):
                    out.append(n)
        return out

    def cond_new(self, Fz, use_pins=True, use_mstar=True, route='bfs', mstar='syn'):
        if not use_mstar:
            M = {r: set(Fz[r]) for r in self.rels}
        elif mstar == 'syn':
            M = self.mstar_syn(Fz)
        elif mstar == 'chase':
            M = self.mstar_chase(Fz)
        elif mstar == 'pin':
            M = self.pin_closure(Fz)
        elif mstar == 'pinlite':
            M = self.pin_lite(Fz)
        elif mstar == 'det':
            M = self.mstar_det(Fz)
        else:
            M = self.fixed_closure(Fz)
        bad = []
        for R in self.rels:
            for U in self.uk[R]:
                if not set(U) <= M[R]:
                    bad.append(('uk', R, U))
        for n, d in enumerate(self.inds):
            R, S = d['R'], d['S']
            for i, (c, x) in enumerate(zip(d['C'], d['D'])):
                if c in M[S] or (set(d['P']) <= M[R] and x in M[R]):
                    continue
                if use_pins and self.pinned(n, i, M, route):
                    continue
                bad.append((n, i))
        return not bad, bad


def fz_from_set(sch, Z):
    """Z: set of (rel, attr) non-key attributes to freeze."""
    return {r: set(sch.pk[r]) | {a for a in sch.rels[r] if (r, a) in Z} for r in sch.rels}


def nonkey_attrs(sch):
    return [(r, a) for r in sch.rels for a in sch.nonkey[r]]
