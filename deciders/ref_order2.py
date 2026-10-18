"""Order-sufficiency of modification updates (reference semantics).

Model.  Relations with a primary key (first attribute), FDs and INDs (composite, possibly
circular).  An entity is a (relation, pk value) slot.  A modification update (I, J) has legal I
and J over the same entities, and every entity keeps its frozen attributes Fz.  It is executable
if the changed entities can be flipped one at a time, old version to new version, with every
intermediate instance legal (no revisits).  A schema is order-sufficient under Fz if every
modification update is executable; it is commutative if every intermediate state of every
modification update is legal.

Condition (G |= Sigma).  G = update-stable consequences of Sigma:
  FD  X -> a in Sigma+ with X containing a frozen key, or X u {a} frozen (in M*);
  IND R[X] <= S[Y] in Sigma+ with Y frozen, or X frozen and Y containing a frozen key of S.
M* (frozen closure): start from Fz; for an IND whose RHS contains a frozen key K of S with the
K-positions of X frozen, every X_i whose Y_i is frozen becomes frozen.
Implication is approximated soundly by the IND axioms (projection, permutation, transitivity)
and the FD pullback rule  R[X] <= S[Y], S: Y_P -> Y_j  =>  R: X_P -> X_j.
"""
import itertools


def subsets(s, proper=False):
    s = list(s)
    top = len(s) if not proper else len(s) - 1
    for k in range(0, top + 1):
        for c in itertools.combinations(s, k):
            yield frozenset(c)


class Schema:
    def __init__(self, rels, fds, inds, name='', dom=3, pk=None):
        self.name = name
        self.rels = {r: tuple(a) for r, a in rels.items()}
        self.rlist = list(self.rels)
        pk = pk or {}
        self.pk = {r: tuple(pk.get(r, attrs[:1])) for r, attrs in self.rels.items()}
        st = [(r, frozenset(X), a) for (r, X, a) in fds]
        for r, attrs in self.rels.items():
            for a in attrs:
                if a not in self.pk[r]:
                    st.append((r, frozenset(self.pk[r]), a))
        self.fds = sorted(set(st), key=lambda f: (f[0], sorted(f[1]), f[2]))
        self.inds = [(R, tuple(X), S, tuple(Y)) for (R, X, S, Y) in inds]
        self.dom = dom
        self._derive()

    # ---------------------------------------------------------------- derivations
    @staticmethod
    def closure(X, F):
        c = set(X)
        ch = True
        while ch:
            ch = False
            for (L, a) in F:
                if a not in c and L <= c:
                    c.add(a)
                    ch = True
        return c

    @staticmethod
    def ind_closure(base):
        """base: iterable of (R, S, frozenset of (x, y) pairs).  Closure under projection,
        permutation (implicit in the set form) and transitivity."""
        cl = set()
        for (R, S, P) in base:
            for Q in subsets(P):
                if Q:
                    cl.add((R, S, Q))
        ch = True
        while ch:
            ch = False
            for (R, S, P1) in list(cl):
                for (S2, T, P2) in list(cl):
                    if S2 != S:
                        continue
                    m = dict(P2)
                    if len(m) != len(P2):
                        continue
                    if all(y in m for (_, y) in P1):
                        new = (R, T, frozenset((x, m[y]) for (x, y) in P1))
                        if len({z for (_, z) in new[2]}) != len(new[2]):
                            continue
                        if new not in cl:
                            cl.add(new)
                            ch = True
        return cl

    def fd_closure_with_pullback(self, F0, indcl):
        """F0: dict r -> set of (frozenset X, a).  Adds pullback FDs to a fixpoint."""
        F = {r: set(F0.get(r, ())) for r in self.rels}
        ch = True
        while ch:
            ch = False
            for (R, S, P) in indcl:
                pl = sorted(P)
                for Q in subsets(range(len(pl)), proper=True):
                    ys = {pl[i][1] for i in Q}
                    cs = self.closure(ys, F[S])
                    xs = frozenset(pl[i][0] for i in Q)
                    for j in range(len(pl)):
                        if j in Q:
                            continue
                        if pl[j][1] in cs and pl[j][0] not in xs:
                            fd = (xs, pl[j][0])
                            if not xs:
                                continue
                            if fd not in F[R]:
                                F[R].add(fd)
                                ch = True
        return F

    def _derive(self):
        self.indcl = self.ind_closure((R, S, frozenset(zip(X, Y))) for (R, X, S, Y) in self.inds)
        F0 = {r: set() for r in self.rels}
        for (r, X, a) in self.fds:
            F0[r].add((X, a))
        self.F = self.fd_closure_with_pullback(F0, self.indcl)
        self.keys = {}
        for r, attrs in self.rels.items():
            ks = []
            for X in subsets(attrs):
                if self.closure(X, self.F[r]) >= set(attrs) and not any(K < X for K in ks):
                    ks.append(X)
            self.keys[r] = ks
        self.prime = {r: frozenset().union(*ks) for r, ks in self.keys.items()}

    def cl(self, r, X):
        return self.closure(X, self.F[r])

    def superkey(self, r, X):
        return self.cl(r, X) >= set(self.rels[r])

    def fdplus(self, r):
        """All nontrivial X -> a in Sigma+ for relation r (X nonempty)."""
        attrs = self.rels[r]
        out = []
        for X in subsets(attrs):
            if not X:
                continue
            for a in self.cl(r, X) - X:
                out.append((X, a))
        return out

    # ---------------------------------------------------------------- condition
    def frozen_closure(self, Fz):
        M = {r: set(Fz[r]) for r in self.rels}
        ch = True
        while ch:
            ch = False
            for (R, S, P) in self.indcl:
                ys = {y for (_, y) in P}
                inv = {y: x for (x, y) in P}
                for K in self.keys[S]:
                    if K <= ys and K <= M[S] and all(inv[k] in M[R] for k in K):
                        for (x, y) in P:
                            if y in M[S] and x not in M[R]:
                                M[R].add(x)
                                ch = True
        return M

    def condition(self, Fz):
        """Returns (ok, reason).  ok means G |= Sigma under the approximate implication."""
        M = self.frozen_closure(Fz)
        fkeys = {r: [K for K in self.keys[r] if K <= M[r]] for r in self.rels}
        good_ind = []
        for (R, S, P) in self.indcl:
            xs = {x for (x, _) in P}
            ys = {y for (_, y) in P}
            if ys <= M[S] or (xs <= M[R] and any(K <= ys for K in fkeys[S])):
                good_ind.append((R, S, P))
        gcl = self.ind_closure(good_ind)
        G0 = {r: set() for r in self.rels}
        for r in self.rels:
            for (X, a) in self.fdplus(r):
                if any(K <= X for K in fkeys[r]) or (X | {a}) <= M[r]:
                    G0[r].add((X, a))
        GF = self.fd_closure_with_pullback(G0, gcl)
        bad = []
        for (R, X, S, Y) in self.inds:
            if (R, S, frozenset(zip(X, Y))) not in gcl:
                bad.append(f"IND {R}[{''.join(X)}]<={S}[{''.join(Y)}]")
        for (r, X, a) in self.fds:
            if a not in self.closure(X, GF[r]):
                bad.append(f"FD {r}:{''.join(sorted(X))}->{a}")
        return (not bad), bad, M

    def cstar_old(self, Fz):
        """Week-2 C** (left-unreduced FD part, projection-only IND exception), for comparison."""
        M = self.frozen_closure(Fz)
        fdok = all(self.superkey(r, X) or (X | {a}) <= M[r]
                   for r in self.rels for (X, a) in self.fdplus(r))

        def projected(R, X, S, Y):
            pr = set(zip(X, Y))
            return any(R2 == R and S2 == S and len(X2) > len(X) and pr <= set(zip(X2, Y2))
                       for (R2, X2, S2, Y2) in self.inds)
        indok = all(projected(R, X, S, Y) or frozenset(Y) <= M[S] or
                    (frozenset(X) <= M[R] and any(K <= frozenset(Y) for K in self.keys[S]))
                    for (R, X, S, Y) in self.inds)
        return fdok and indok


# -------------------------------------------------------------------- semantics
class Sem:
    def __init__(self, sch, Fz):
        self.sch = sch
        self.ri = {r: i for i, r in enumerate(sch.rlist)}
        self.fdc = []
        for (r, X, a) in sch.fds:
            if X == frozenset(sch.pk[r]):
                continue
            at = sch.rels[r]
            self.fdc.append((self.ri[r], tuple(at.index(x) for x in sorted(X)), at.index(a)))
        self.indc = []
        for (R, X, S, Y) in sch.inds:
            self.indc.append((self.ri[R], tuple(sch.rels[R].index(x) for x in X),
                              self.ri[S], tuple(sch.rels[S].index(y) for y in Y)))
        d = range(sch.dom)
        self.byk = []
        self.opts = []
        self.pkvals = []
        for r in sch.rlist:
            at = sch.rels[r]
            allv = list(itertools.product(d, repeat=len(at)))
            pki = [at.index(a) for a in sch.pk[r]]
            pv = list(itertools.product(d, repeat=len(pki)))
            self.pkvals.append(pv)
            self.byk.append({v: [t for t in allv if tuple(t[i] for i in pki) == v] for v in pv})
            fzi = [i for i, a in enumerate(at) if a in Fz[r]]
            grp = {}
            for t in allv:
                grp.setdefault(tuple(t[i] for i in fzi), []).append(t)
            self.opts.append({t: grp[tuple(t[i] for i in fzi)] for t in allv})
        self.pk_in_ind = [any(set(sch.pk[r]) & set(X) for (R, X, _, _) in sch.inds if R == r) or
                          any(set(sch.pk[r]) & set(Y) for (_, _, S, Y) in sch.inds if S == r) or
                          len(sch.pk[r]) > 1 for r in sch.rlist]

    def legal(self, state):
        """state: list of (rel index, tuple)."""
        nrel = len(self.byk)
        by = [[] for _ in range(nrel)]
        for (r, t) in state:
            by[r].append(t)
        for (r, xi, ai) in self.fdc:
            d = {}
            for t in by[r]:
                k = tuple(t[i] for i in xi)
                if d.setdefault(k, t[ai]) != t[ai]:
                    return False
        for (R, xi, S, yi) in self.indc:
            have = {tuple(t[i] for i in yi) for t in by[S]}
            for t in by[R]:
                if tuple(t[i] for i in xi) not in have:
                    return False
        return True

    def analyse(self, rels, I, J):
        """Returns (executable, commutative)."""
        chg = [i for i in range(len(I)) if I[i] != J[i]]
        full = (1 << len(chg)) - 1
        legal_mask = {0: True, full: True}

        def lg(mask):
            if mask not in legal_mask:
                st = list(zip(rels, I))
                for b, i in enumerate(chg):
                    if mask >> b & 1:
                        st[i] = (rels[i], J[i])
                legal_mask[mask] = self.legal(st)
            return legal_mask[mask]
        reach = {0}
        frontier = [0]
        while frontier:
            m = frontier.pop()
            for b in range(len(chg)):
                if not m >> b & 1:
                    m2 = m | (1 << b)
                    if m2 not in reach and lg(m2):
                        reach.add(m2)
                        frontier.append(m2)
        exe = full in reach
        comm = all(lg(m) for m in range(1, full))
        return exe, comm

    def configs(self, n):
        sch = self.sch
        d = sch.dom
        R = len(sch.rlist)
        for counts in itertools.product(*[range(min(n, len(self.pkvals[r])) + 1) for r in range(R)]):
            if sum(counts) != n:
                continue
            ch = []
            for r in range(R):
                if self.pk_in_ind[r]:
                    ch.append(list(itertools.combinations(self.pkvals[r], counts[r])))
                else:
                    ch.append([tuple(self.pkvals[r][:counts[r]])])
            for pkc in itertools.product(*ch):
                yield [(r, v) for r in range(R) for v in pkc[r]]

    def search(self, nmax, nmin=1, stop_cycle=True, want_noncomm=True, budget=None):
        """Exhaustive over all modification updates with nmin..nmax entities."""
        out = {'cycle': None, 'noncomm': None, 'pairs': 0, 'complete': True}
        for n in range(max(nmin, 1), nmax + 1):
            for slots in self.configs(n):
                rels = [r for (r, _) in slots]
                vl = [self.byk[r][v] for (r, v) in slots]
                for I in itertools.product(*vl):
                    if not self.legal(list(zip(rels, I))):
                        continue
                    ol = [self.opts[r][t] for r, t in zip(rels, I)]
                    for J in itertools.product(*ol):
                        nch = sum(1 for a, b in zip(I, J) if a != b)
                        if nch < 2:
                            continue
                        if not self.legal(list(zip(rels, J))):
                            continue
                        out['pairs'] += 1
                        exe, comm = self.analyse(rels, I, J)
                        if not exe and out['cycle'] is None:
                            out['cycle'] = (n, nch, [(self.sch.rlist[r], i, j) for r, i, j in zip(rels, I, J)])
                            if stop_cycle:
                                return out
                        if not comm and out['noncomm'] is None:
                            out['noncomm'] = (n, nch, [(self.sch.rlist[r], i, j) for r, i, j in zip(rels, I, J)])
                            if not want_noncomm and out['cycle'] is not None:
                                return out
                        if budget is not None and out['pairs'] > budget:
                            out['complete'] = False
                            return out
        return out


def prime_fz(sch):
    return {r: set(sch.prime[r]) for r in sch.rels}
