"""Bounded SAT search for updates under conditional INDs (Theorem 4.9, pycosat).

Schema (dict):
  rels {R: [attrs]}, pk {R: [attrs]}, uk {R: [[attrs]]} (extra unique keys), fds [(R, [X], A)],
  inds [{R, P, S, L, D, C, lf, rg, opt}]:
      for every tuple t of R with t[lf.attr] in lf.vals (always if lf is None) some tuple s of S has
      s[L] = t[P], s[C] = t[D] and s[rg.attr] in rg.vals (always if rg is None).
      opt=True turns it into a denial: every s of S with s[L] = t[P] has s[C] = t[D] and rg.
  Values are 0..Dv-1.  A conditional IND (CIND) has lf and/or rg; a foreign key has neither.
Updates: changed entities k = 2..kmax, plus at most N-k unchanged ones.  kind 'mod': every changed
entity is present in I and J and changes a mutable attribute (frozen ones are shared).  kind 'mixed':
a changed entity may also be inserted (absent in I) or deleted (absent in J).
Modes:
  'nonexec'  no order of single-entity writes keeps every prefix state legal;
  'noncomm'  some intermediate state is illegal;
  'canon'    some prefix state of the canonical order is illegal.  The canonical order writes first
             the entities that become witnesses (enabled: some IND into their relation is supplied
             in J but not in I), referenced relations first; then the other changed entities; and
             last the entities that stop being witnesses, referencing relations first.  Ties in
             any order: every down-set of this weak order is tested.
fresh: a changed entity writes into an attribute of fresh_attrs only values that no entity of its
relation holds there in I (unique keys, left sides of FDs, right sides L and C of INDs; with fresh_g also the
attribute of a right condition).
Used as a module by cind_check.py and cind_recheck.py."""
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


def heights(sch):
    """h(R) = 0 if R references nothing, else 1 + max h(S) over INDs R -> S (acyclic graphs only)."""
    h = {}

    def go(r, stack):
        if r in h:
            return h[r]
        if r in stack:
            raise ValueError('cyclic')
        outs = [d['S'] for d in sch['inds'] if d['R'] == r and d['S'] != r]
        h[r] = 0 if not outs else 1 + max(go(s, stack | {r}) for s in outs)
        return h[r]

    for r in sch['rels']:
        go(r, frozenset())
    return h


def fresh_attrs(sch, fresh_g):
    out = {r: set(sch['pk'][r]) for r in sch['rels']}
    for r, us in sch.get('uk', {}).items():
        for U in us:
            out[r] |= set(U)
    for (r, Xs, a) in sch.get('fds', []):
        out[r] |= set(Xs)
    for d in sch['inds']:
        out[d['S']] |= set(d['L']) | set(d.get('C', []))
        if fresh_g and d.get('rg'):
            out[d['S']].add(d['rg'][0])
    return out


def build(sch, Fz, chg, ucap, Dv, mode, kind, fresh, fresh_g=False):
    e = Enc()
    rels = list(sch['rels'])
    A = sch['rels']
    slots = [(r, True) for r in chg] + [(r, False) for r in rels for _ in range(ucap)]
    k = len(chg)
    N = len(slots)
    X, Y, pI, pJ, act = [], [], [], [], []
    for i, (r, changed) in enumerate(slots):
        xi = {a: e.onehot(Dv) for a in A[r]}
        if changed:
            yi = {a: (e.onehot(Dv) if (kind == 'mixed' or a not in Fz[r]) else xi[a]) for a in A[r]}
            if kind == 'mixed':
                a_, b_ = e.var(), e.var()
                e.cl.append([a_, b_])
                # an entity present in both versions changes a mutable attribute; frozen ones agree
                for a in A[r]:
                    if a in Fz[r]:
                        z = e.eq(xi[a], yi[a])
                        e.cl.append([-a_, -b_, z])
                e.cl.append([-a_, -b_] + [-e.eq(xi[a], yi[a]) for a in A[r] if a not in Fz[r]])
                # the key of an inserted or deleted entity is its only identity: share it
                for a in sch['pk'][r]:
                    e.cl.append([e.eq(xi[a], yi[a])])
            else:
                a_ = b_ = e.true()
                e.cl.append([-e.eq(xi[a], yi[a]) for a in A[r] if a not in Fz[r]])
            ac = e.true()
        else:
            yi = xi
            ac = e.var()
            a_ = b_ = ac
        X.append(xi)
        Y.append(yi)
        pI.append(a_)
        pJ.append(b_)
        act.append(ac)
    # symmetry: unused slots carry value 0 and are used in order
    for i in range(k, N):
        for a in A[slots[i][0]]:
            e.cl.append([act[i], X[i][a][0]])
        if i + 1 < N and slots[i + 1][0] == slots[i][0]:
            e.cl.append([-act[i + 1], act[i]])
    e.atmost(act[k:], ucap)
    # distinct entities have distinct primary keys (keys are frozen)
    for i, j in itertools.combinations(range(N), 2):
        if slots[i][0] == slots[j][0]:
            r = slots[i][0]
            e.cl.append([-act[i], -act[j], -e.AND([e.eq(X[i][a], X[j][a]) for a in sch['pk'][r]])])
    if fresh:
        fa = fresh_attrs(sch, fresh_g)
        for i in range(k):
            r = slots[i][0]
            for a in A[r]:
                if a not in fa[r] or (kind == 'mod' and a in Fz[r]):
                    continue
                if a in sch['pk'][r]:
                    continue
                same = e.AND([pI[i], e.eq(X[i][a], Y[i][a])])
                for j in range(N):
                    if j != i and slots[j][0] == r:
                        e.cl.append([same, -pJ[i], -pI[j], -e.eq(Y[i][a], X[j][a])])

    def inset(vals, vs):
        return e.OR([vals[v] for v in vs])

    def legal(st):
        cons = []
        for i, j in itertools.combinations(range(N), 2):
            if slots[i][0] != slots[j][0]:
                continue
            r = slots[i][0]
            pi, vi = st[i]
            pj, vj = st[j]
            for U in sch.get('uk', {}).get(r, []):
                cons.append(-e.AND([pi, pj] + [e.eq(vi[a], vj[a]) for a in U]))
            for (fr, Xs, a) in sch.get('fds', []):
                if fr == r:
                    lhs = e.AND([pi, pj] + [e.eq(vi[x], vj[x]) for x in Xs])
                    cons.append(e.OR([-lhs, e.eq(vi[a], vj[a])]))
        for d in sch['inds']:
            for i in range(N):
                if slots[i][0] != d['R']:
                    continue
                pi, vi = st[i]
                need = [pi] + ([inset(vi[d['lf'][0]], d['lf'][1])] if d.get('lf') else [])
                need = e.AND(need)
                Ds, Cs = d.get('D', []), d.get('C', [])
                if d.get('opt'):
                    for j in range(N):
                        if slots[j][0] != d['S']:
                            continue
                        pj, vj = st[j]
                        hit = e.AND([need, pj] + [e.eq(vi[x], vj[y]) for x, y in zip(d['P'], d['L'])])
                        ok = e.AND([e.eq(vi[x], vj[y]) for x, y in zip(Ds, Cs)]
                                   + ([inset(vj[d['rg'][0]], d['rg'][1])] if d.get('rg') else []))
                        cons.append(e.OR([-hit, ok]))
                    continue
                w = []
                for j in range(N):
                    if slots[j][0] != d['S']:
                        continue
                    pj, vj = st[j]
                    w.append(e.AND([pj] + [e.eq(vi[x], vj[y]) for x, y in zip(d['P'] + Ds, d['L'] + Cs)]
                                   + ([inset(vj[d['rg'][0]], d['rg'][1])] if d.get('rg') else [])))
                cons.append(e.OR([-need, e.OR(w)]))
        return e.AND(cons)

    full = (1 << k) - 1

    def state(m):
        return [((pJ[i], Y[i]) if i < k and m >> i & 1 else (pI[i], X[i])) for i in range(N)]

    states = {m: legal(state(m)) for m in range(full + 1)}
    e.cl.append([states[0]])
    e.cl.append([states[full]])
    if mode == 'noncomm':
        e.cl.append([-states[m] for m in range(1, full)])
    elif mode == 'nonexec':
        reach = {0: e.true()}
        for m in sorted(range(1, full + 1), key=lambda m: bin(m).count('1')):
            pre = e.OR([reach[m & ~(1 << b)] for b in range(k) if m >> b & 1])
            reach[m] = pre if m == full else e.AND([states[m], pre])
        e.cl.append([-reach[full]])
    elif mode == 'canon':
        h = heights(sch)
        en, di = [], []
        for i in range(k):
            r = slots[i][0]
            ups, downs = [], []
            for d in sch['inds']:
                if d['S'] != r or d.get('opt'):
                    continue
                sI = e.AND([pI[i]] + ([inset(X[i][d['rg'][0]], d['rg'][1])] if d.get('rg') else []))
                sJ = e.AND([pJ[i]] + ([inset(Y[i][d['rg'][0]], d['rg'][1])] if d.get('rg') else []))
                ups.append(e.AND([sJ, -sI]))
                downs.append(e.AND([sI, -sJ]))
            for d in sch['inds']:
                # denials: absent or supplying versions are harmless
                if d['S'] != r or not d.get('opt'):
                    continue
                bI = e.AND([pI[i], -inset(X[i][d['rg'][0]], d['rg'][1])])
                bJ = e.AND([pJ[i], -inset(Y[i][d['rg'][0]], d['rg'][1])])
                ups.append(e.AND([bI, -bJ]))
                downs.append(e.AND([bJ, -bI]))
            en.append(e.OR(ups))
            di.append(e.AND([e.OR(downs), -en[-1]]))
        mid = [e.AND([-en[i], -di[i]]) for i in range(k)]

        def lt(i, j):
            hi, hj = h[slots[i][0]], h[slots[j][0]]
            terms = [e.AND([en[i], -en[j]]), e.AND([mid[i], di[j]])]
            if hi < hj:
                terms.append(e.AND([en[i], en[j]]))
            if hi > hj:
                terms.append(e.AND([di[i], di[j]]))
            return e.OR(terms)

        LT = {(i, j): lt(i, j) for i in range(k) for j in range(k) if i != j}
        bad = []
        for m in range(1, full):
            down = [-LT[(i, j)] for i in range(k) if not m >> i & 1 for j in range(k) if m >> j & 1]
            bad.append(e.AND(down + [-states[m]]))
        e.cl.append(bad)
    return e, slots, X, Y, pI, pJ, act


def decode(sol, sch, slots, X, Y, pI, pJ, act):
    s = set(l for l in sol if l > 0)

    def val(oh):
        return [v for v, x in enumerate(oh) if x in s][0]

    out = []
    for i, (r, changed) in enumerate(slots):
        if act[i] not in s:
            continue
        old = {a: val(X[i][a]) for a in sch['rels'][r]} if pI[i] in s else None
        new = {a: val(Y[i][a]) for a in sch['rels'][r]} if pJ[i] in s else None
        out.append((r, old, new))
    return out


def search(sch, Fz, N, kmax, Dv, mode='nonexec', kind='mod', fresh=False, fresh_g=False, kmin=2):
    """Returns None or (k, [(rel, old tuple or None, new tuple or None)])."""
    if kind == 'mod':
        cand = [r for r in sch['rels'] if any(a not in Fz[r] for a in sch['rels'][r])]
    else:
        cand = list(sch['rels'])
    for k in range(kmin, min(kmax, N) + 1):
        for chg in itertools.combinations_with_replacement(cand, k):
            enc = build(sch, Fz, list(chg), N - k, Dv, mode, kind, fresh, fresh_g)
            sol = pycosat.solve(enc[0].cl)
            if sol != 'UNSAT':
                return (k, decode(sol, sch, *enc[1:]))
    return None
