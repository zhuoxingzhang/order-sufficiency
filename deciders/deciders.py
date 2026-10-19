"""Order-sufficiency deciders for the four exact classes (experiment E3).

Schema (plain JSON-able dicts):
  rels  {R: [attr, ...]}            attribute names are local to a relation
  pk    {R: [attr, ...]}
  fds   [(R, [lhs ...], rhs)]       declared FDs with nonempty lhs; the PK FD is implicit
  inds  [(R, [X ...], S, [Y ...])]  declared INDs R[X] <= S[Y]
A frozen set Fz is {R: set(attrs)} with Fz(R) >= PK(R).

Deciders (each returns (ok, reasons); ok means order-sufficient = commutative):
  dec_fd     FDs only, Theorem 3.6 (G |= Sigma), equivalently N1.  For a declared FD
             X -> a the G-closure of X is U if (X n Fz)+ = U, else X u ((X n Fz)+ n Fz).
  dec_unary  FDs plus unary INDs, Theorem 4.1: (U1) every nontrivial unary IND of the finite
             closure has a frozen right side, (U2) N1 for the finite FD closure.  The finite
             closure reverses the unary FDs and unary INDs inside each cycle of the counting graph
             (edge a => b when |b| <= |a| holds in every finite model) and repeats to a fixpoint.
  dec_A      class A (acyclic INDs, each right side exactly a minimal key of the target under its
             declared FDs, one key per target), Theorem 4.2: N1 relation by relation.
  dec_K      class K (PK-only FDs, acyclic INDs R[P D] <= S[K C]), Theorem 4.5: every IND stable,
             i.e. C frozen, or P frozen and C_i or D_i frozen for every i.
All four run in polynomial time: dec_fd and dec_A in O(sum_R |F_R|^2), dec_unary in
O(iterations * sum_R |U_R| |F_R| + |INDs|), dec_K in O(|INDs| * arity)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------- FD closure
class FDSet:
    """FDs of one relation over attribute indices; linear-time closure (Beeri-Bernstein)."""

    def __init__(self, n, fds=()):
        self.n = n
        self.full = (1 << n) - 1
        self.lhs, self.rhs = [], []
        self.by = [[] for _ in range(n)]
        for L, a in fds:
            self.add(L, a)

    def add(self, L, a):
        L = tuple(sorted(set(L)))
        f = len(self.lhs)
        self.lhs.append(L)
        self.rhs.append(a)
        for x in L:
            self.by[x].append(f)

    def closure(self, X):
        """X: iterable of indices; returns a bitmask."""
        cnt = [len(L) for L in self.lhs]
        res = 0
        todo = []
        for x in X:
            if not res >> x & 1:
                res |= 1 << x
                todo.append(x)
        by, rhs = self.by, self.rhs
        while todo:
            x = todo.pop()
            for f in by[x]:
                cnt[f] -= 1
                if cnt[f] == 0:
                    a = rhs[f]
                    if not res >> a & 1:
                        res |= 1 << a
                        todo.append(a)
        return res


def mask(idx):
    m = 0
    for i in idx:
        m |= 1 << i
    return m


def bits(m):
    while m:
        low = m & -m
        yield low.bit_length() - 1
        m ^= low


class Rel:
    """Index view of one relation: attribute positions, PK, declared FDs."""

    def __init__(self, name, attrs, pk, fds):
        self.name = name
        self.attrs = list(attrs)
        self.ix = {a: i for i, a in enumerate(self.attrs)}
        self.pk = [self.ix[a] for a in pk]
        self.decl = [(tuple(self.ix[x] for x in L), self.ix[a]) for L, a in fds]
        self.n = len(self.attrs)
        self.full = (1 << self.n) - 1

    def fdset(self, extra=()):
        F = FDSet(self.n)
        for a in range(self.n):
            F.add(self.pk, a)
        for L, a in self.decl:
            F.add(L, a)
        for L, a in extra:
            F.add(L, a)
        return F


def index_schema(sch):
    by = {r: [] for r in sch['rels']}
    for R, L, a in sch['fds']:
        by[R].append((L, a))
    return {r: Rel(r, sch['rels'][r], sch['pk'][r], by[r]) for r in sch['rels']}


def fz_mask(rel, Fz):
    return mask(rel.ix[a] for a in Fz[rel.name])


# ---------------------------------------------------------------------------- Theorem 3.6
def g_implied(F, full, fz, L, a):
    """Is the FD L -> a (L a bitmask) implied by the update-stable FDs G of the FD set F?"""
    if L >> a & 1:
        return True
    c = F.closure(bits(L & fz))
    if c == full:
        return True
    return bool(fz >> a & 1 and c >> a & 1)


def check_rel(rel, F, fz, fds):
    bad = []
    for L, a in fds:
        if not g_implied(F, rel.full, fz, mask(L), a):
            bad.append('FD %s:%s->%s' % (rel.name, ''.join(rel.attrs[x] + ',' for x in L)[:-1], rel.attrs[a]))
    return bad


def dec_fd(sch, Fz, rels=None):
    """FD part only: INDs, if any, are ignored."""
    rels = rels or index_schema(sch)
    bad = []
    for rel in rels.values():
        bad += check_rel(rel, rel.fdset(), fz_mask(rel, Fz), rel.decl)
    return not bad, bad


# ---------------------------------------------------------------------------- Theorem 4.1
def tarjan(nodes, adj):
    """SCC id per node; adj: dict node -> iterable.  Iterative."""
    index, low, comp, onst, st = {}, {}, {}, set(), []
    c = [0]
    ncomp = 0
    for v0 in nodes:
        if v0 in index:
            continue
        work = [(v0, iter(adj.get(v0, ())))]
        index[v0] = low[v0] = c[0]
        c[0] += 1
        st.append(v0)
        onst.add(v0)
        while work:
            v, it = work[-1]
            pushed = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = c[0]
                    c[0] += 1
                    st.append(w)
                    onst.add(w)
                    work.append((w, iter(adj.get(w, ()))))
                    pushed = True
                    break
                elif w in onst:
                    low[v] = min(low[v], index[w])
            if pushed:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                while True:
                    w = st.pop()
                    onst.discard(w)
                    comp[w] = ncomp
                    if w == v:
                        break
                ncomp += 1
    return comp


def finite_unary(sch, rels=None):
    """Finite closure for FDs plus unary INDs.  Returns (rels, extra, uinds, comp, rounds):
    extra[R] = reversed unary FDs (list of ((b,), a)), uinds = set of (u, v) node pairs u <= v
    (declared plus reversed), comp = SCC id of each node (R, attr index)."""
    rels = rels or index_schema(sch)
    decl_uind = set()
    for R, X, S, Y in sch['inds']:
        assert len(X) == 1 and len(Y) == 1, 'non-unary IND'
        u, v = (R, rels[R].ix[X[0]]), (S, rels[S].ix[Y[0]])
        if u != v:
            decl_uind.add((u, v))
    extra = {r: set() for r in rels}
    uinds = set(decl_uind)
    nodes = [(r, i) for r, rel in rels.items() for i in range(rel.n)]
    rounds = 0
    while True:
        rounds += 1
        adj = {}
        ufd = []
        Fs = {}
        for r, rel in rels.items():
            F = rel.fdset(((b,), a) for (b, a) in extra[r])
            Fs[r] = F
            for a in range(rel.n):
                c = F.closure((a,)) & ~(1 << a)
                for b in bits(c):
                    ufd.append((r, a, b))
                    adj.setdefault((r, a), []).append((r, b))
        for (u, v) in uinds:
            adj.setdefault(v, []).append(u)
        comp = tarjan(nodes, adj)
        new = False
        for (r, a, b) in ufd:
            if comp[(r, a)] == comp[(r, b)] and (b, a) not in extra[r]:
                if not Fs[r].closure((b,)) >> a & 1:
                    extra[r].add((b, a))
                    new = True
        for (u, v) in list(uinds):
            if comp[u] == comp[v] and (v, u) not in uinds:
                uinds.add((v, u))
        if not new:
            return rels, {r: [((b,), a) for (b, a) in sorted(e)] for r, e in extra.items()}, uinds, comp, rounds


def dec_unary(sch, Fz, rels=None, info=None):
    rels, extra, uinds, comp, rounds = info or finite_unary(sch, rels)
    bad = []
    fzm = {r: fz_mask(rel, Fz) for r, rel in rels.items()}
    tgt = {v for (u, v) in uinds}
    for (r, i) in sorted(tgt):
        if not fzm[r] >> i & 1:
            bad.append('IND ->%s.%s' % (r, rels[r].attrs[i]))
    for r, rel in rels.items():
        F = rel.fdset(extra[r])
        bad += check_rel(rel, F, fzm[r], rel.decl + extra[r])
    return not bad, bad


# ---------------------------------------------------------------------------- class A
def rel_graph_acyclic(sch):
    adj = {}
    for R, X, S, Y in sch['inds']:
        adj.setdefault(R, set()).add(S)
    comp = tarjan(list(sch['rels']), adj)
    if any(R == S for R, X, S, Y in sch['inds']):
        return False
    size = {}
    for c in comp.values():
        size[c] = size.get(c, 0) + 1
    return max(size.values()) == 1


def class_A(sch, rels=None):
    rels = rels or index_schema(sch)
    if not rel_graph_acyclic(sch):
        return False, 'cyclic'
    key_of = {}
    Fs = {}
    for R, X, S, Y in sch['inds']:
        rel = rels[S]
        if len(set(Y)) != len(Y) or len(X) != len(Y):
            return False, 'repeated attribute'
        F = Fs.setdefault(S, rel.fdset())
        ym = mask(rel.ix[y] for y in Y)
        if F.closure(bits(ym)) != rel.full:
            return False, 'rhs not a key'
        for y in bits(ym):
            if F.closure(bits(ym & ~(1 << y))) == rel.full:
                return False, 'rhs not minimal'
        if key_of.setdefault(S, ym) != ym:
            return False, 'two keys referenced'
    return True, ''


def dec_A(sch, Fz, rels=None):
    return dec_fd(sch, Fz, rels)


# ---------------------------------------------------------------------------- class K
def parse_K(sch, final=True):
    """Class K: only primary-key FDs; acyclic INDs R[P D] <= S[K C] with K = PK(S).  A pointer P may
    be shared by several INDs; a copy D lies outside PK(R) and occurs in no other IND; a source C
    lies outside K and is not a copy; an IND with copies has a pointer that neither contains PK(R)
    nor lies within PK(R) (copy-free foreign keys are unrestricted, e.g. P >= PK(R) for a one-to-one
    extension table).  final=False drops the last condition (the earlier definition, which admits
    the diamond of k_diamond.py).
    Returns (ok, reason, list of (R, P, D, S, C)) with P aligned to PK(S) and D aligned to C."""
    if sch['fds']:
        return False, 'non-key FD', None
    if not rel_graph_acyclic(sch):
        return False, 'cyclic', None
    out = []
    copy_owner = {}
    for n, (R, X, S, Y) in enumerate(sch['inds']):
        if len(X) != len(Y) or len(set(X)) != len(X) or len(set(Y)) != len(Y):
            return False, 'repeated attribute', None
        pkS = list(sch['pk'][S])
        pos = {y: i for i, y in enumerate(Y)}
        if not all(k in pos for k in pkS):
            return False, 'rhs misses the primary key', None
        P = tuple(X[pos[k]] for k in pkS)
        rest = [i for i, y in enumerate(Y) if y not in pkS]
        D = tuple(X[i] for i in rest)
        C = tuple(Y[i] for i in rest)
        pkR = set(sch['pk'][R])
        if final and D and (pkR <= set(P) or set(P) <= pkR):
            return False, 'copy IND whose pointer contains or lies within PK(R)', None
        for d in D:
            if (R, d) in copy_owner or d in pkR:
                return False, 'copy attribute reused or in the primary key', None
            copy_owner[(R, d)] = n
        out.append((R, P, D, S, C))
    for n, (R, X, S, Y) in enumerate(sch['inds']):
        if any(copy_owner.get((R, x), n) != n for x in X) or any((S, y) in copy_owner for y in Y):
            return False, 'copy attribute occurs in another IND', None
    return True, '', out


def dec_K(sch, Fz, parsed=None):
    if parsed is None:
        ok, why, parsed = parse_K(sch)
        assert ok, why
    bad = []
    for (R, P, D, S, C) in parsed:
        fR, fS = Fz[R], Fz[S]
        if all(c in fS for c in C):
            continue
        if all(p in fR for p in P) and all(c in fS or d in fR for c, d in zip(C, D)):
            continue
        bad.append('IND %s[%s]<=%s' % (R, ','.join(P + D), S))
    return not bad, bad


def conflict_graph(parsed, Fz):
    """Theorem 5.1: vertices are mutable attributes (R, a); edges {p, c} and {C_i, D_i}."""
    E = set()
    for (R, P, D, S, C) in parsed:
        for p in P:
            if p in Fz[R]:
                continue
            for c in C:
                if c not in Fz[S]:
                    E.add(frozenset({(R, p), (S, c)}))
        for c, d in zip(C, D):
            if c not in Fz[S] and d not in Fz[R]:
                E.add(frozenset({(S, c), (R, d)}))
    return E


# ---------------------------------------------------------------------------- dispatch
def classify(sch):
    """Smallest exact class that contains the schema: 'FD', 'K', 'A', 'U', or None."""
    if not sch['inds']:
        return 'FD'
    if parse_K(sch)[0]:
        return 'K'
    if class_A(sch)[0]:
        return 'A'
    if all(len(X) == 1 for _, X, _, _ in sch['inds']):
        return 'U'
    return None


def decide(sch, Fz, cls=None):
    cls = cls or classify(sch)
    if cls == 'FD':
        return dec_fd(sch, Fz)
    if cls == 'K':
        return dec_K(sch, Fz)
    if cls == 'A':
        return dec_A(sch, Fz)
    if cls == 'U':
        return dec_unary(sch, Fz)
    raise ValueError('schema outside the exact classes')
