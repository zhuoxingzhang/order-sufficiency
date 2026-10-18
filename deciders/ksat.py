"""Bounded search for a non-executable or a non-commutative modification update, by SAT (pycosat).

For every multiset of k relations (the changed entities, k = 2..kmax, only relations with a mutable
attribute) one formula asks for an update with exactly these changed entities, at most N - k further
unchanged entities, values in a domain of D constants, I and J legal, and
  mode 'nonexec': no order of the k single-entity writes keeps every prefix state legal;
  mode 'noncomm': some intermediate state is illegal.
Together the formulas search every modification update with at most N entities and at most kmax
changed entities over the domain, so with kmax = N the search is complete within these bounds.
Used as a module by k_beyond*.py (needs pycosat)."""
import itertools
import pycosat


class Enc:
    def __init__(self):
        self.n = 0
        self.cl = []
        self.eqc = {}
        self._t = None

    def var(self):
        self.n += 1
        return self.n

    def true(self):
        if self._t is None:
            self._t = self.var()
            self.cl.append([self._t])
        return self._t

    def onehot(self, D):
        vs = [self.var() for _ in range(D)]
        self.cl.append(vs)
        for a, b in itertools.combinations(vs, 2):
            self.cl.append([-a, -b])
        return vs

    def AND(self, lits):
        lits = list(lits)
        if not lits:
            return self.true()
        if len(lits) == 1:
            return lits[0]
        z = self.var()
        for l in lits:
            self.cl.append([-z, l])
        self.cl.append([z] + [-l for l in lits])
        return z

    def OR(self, lits):
        lits = list(lits)
        if not lits:
            return -self.true()
        if len(lits) == 1:
            return lits[0]
        z = self.var()
        for l in lits:
            self.cl.append([z, -l])
        self.cl.append([-z] + lits)
        return z

    def eq(self, p, q):
        if p is q:
            return self.true()
        key = (id(p), id(q)) if id(p) < id(q) else (id(q), id(p))
        if key not in self.eqc:
            self.eqc[key] = self.OR([self.AND([a, b]) for a, b in zip(p, q)])
        return self.eqc[key]

    def atmost(self, lits, k):
        """Sequential counter (Sinz 2005)."""
        n = len(lits)
        if k >= n:
            return
        if k == 0:
            for l in lits:
                self.cl.append([-l])
            return
        s = [[self.var() for _ in range(k)] for _ in range(n)]
        self.cl.append([-lits[0], s[0][0]])
        for j in range(1, k):
            self.cl.append([-s[0][j]])
        for i in range(1, n):
            self.cl.append([-lits[i], s[i][0]])
            self.cl.append([-s[i - 1][0], s[i][0]])
            for j in range(1, k):
                self.cl.append([-lits[i], -s[i - 1][j - 1], s[i][j]])
                self.cl.append([-s[i - 1][j], s[i][j]])
            self.cl.append([-lits[i], -s[i - 1][k - 1]])


def build(sch, Fz, chg, ucap, D, mode):
    e = Enc()
    rels = list(sch.rels)
    slots = [(r, True) for r in chg] + [(r, False) for r in rels for _ in range(ucap)]
    k = len(chg)
    X, Y, act = [], [], []
    for (r, changed) in slots:
        xi = {a: e.onehot(D) for a in sch.rels[r]}
        yi = {a: (e.onehot(D) if changed and a not in Fz[r] else xi[a]) for a in sch.rels[r]}
        X.append(xi)
        Y.append(yi)
        act.append(e.true() if changed else e.var())
    N = len(slots)
    # symmetry: unused slots carry value 0 and are used in order
    for i in range(k, N):
        for a in sch.rels[slots[i][0]]:
            e.cl.append([act[i], X[i][a][0]])
        if i + 1 < N and slots[i + 1][0] == slots[i][0]:
            e.cl.append([-act[i + 1], act[i]])
    e.atmost(act[k:], ucap)
    # changed slots change
    for i in range(k):
        r = slots[i][0]
        e.cl.append([-e.eq(X[i][a], Y[i][a]) for a in sch.rels[r] if a not in Fz[r]])
    # primary keys (frozen) distinct
    for i, j in itertools.combinations(range(N), 2):
        if slots[i][0] == slots[j][0]:
            r = slots[i][0]
            e.cl.append([-act[i], -act[j], -e.AND([e.eq(X[i][a], X[j][a]) for a in sch.pk[r]])])

    def legal(st):
        cons = []
        for i, j in itertools.combinations(range(N), 2):
            if slots[i][0] == slots[j][0]:
                r = slots[i][0]
                for U in sch.uk[r]:
                    cons.append(-e.AND([act[i], act[j]] + [e.eq(st[i][a], st[j][a]) for a in U]))
        for d in sch.inds:
            for i in range(N):
                if slots[i][0] != d['R']:
                    continue
                w = [e.AND([act[j]] + [e.eq(st[i][x], st[j][y]) for x, y in zip(d['X'], d['Y'])])
                     for j in range(N) if slots[j][0] == d['S']]
                cons.append(e.OR([-act[i], e.OR(w)]))
        return e.AND(cons)

    states = {}
    full = (1 << k) - 1
    for m in range(full + 1):
        st = [Y[i] if i < k and m >> i & 1 else X[i] for i in range(N)]
        states[m] = legal(st)
    e.cl.append([states[0]])
    e.cl.append([states[full]])
    if mode == 'noncomm':
        e.cl.append([-states[m] for m in range(1, full)])
    else:
        reach = {0: e.true()}
        for m in sorted(range(1, full + 1), key=lambda m: bin(m).count('1')):
            pre = e.OR([reach[m & ~(1 << b)] for b in range(k) if m >> b & 1])
            reach[m] = pre if m == full else e.AND([states[m], pre])
        e.cl.append([-reach[full]])
    return e, slots, X, Y, act


def decode(sol, slots, X, Y, act, sch):
    s = set(l for l in sol if l > 0)
    val = lambda oh: next(v for v, l in enumerate(oh) if l in s)
    out = []
    for i, (r, _) in enumerate(slots):
        if act[i] in s:
            out.append((r, tuple(val(X[i][a]) for a in sch.rels[r]), tuple(val(Y[i][a]) for a in sch.rels[r])))
    return out


def search(sch, Fz, N, kmax, D, mode='nonexec', kmin=2):
    """Returns None or (k, update as [(rel, I tuple, J tuple)])."""
    mut = [r for r in sch.rels if any(a not in Fz[r] for a in sch.rels[r])]
    for k in range(kmin, min(kmax, N) + 1):
        for chg in itertools.combinations_with_replacement(mut, k):
            e, slots, X, Y, act = build(sch, Fz, list(chg), N - k, D, mode)
            sol = pycosat.solve(e.cl)
            if sol != 'UNSAT':
                return (k, decode(sol, slots, X, Y, act, sch))
    return None
