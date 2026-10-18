"""Constructed witnesses for the class-K tasks the bounded exhaustive search leaves unresolved.

A task is unresolved when the decider says "not order-sufficient" but the search of selfcheck.py
finds no non-executable update within its tiers (at most 6 entities, or the work bound).  Their
counterexamples need more rows than the tiers allow: every row must have its targets under every
IND, and a frozen copy forces a target that the update does not touch.  Here the counterexample is
built directly from the unstable IND R[P D] <= S[K C] that the decider reports:
  swap     some pointer attribute and some source c are mutable: rows r1, r2 of R point to s1, s2 of
           S and swap targets while s1.c and s2.c swap their values (four updated rows);
  copy     P is frozen and some C_i, D_i are both mutable: s1.C_i and r1.D_i change together.
The other rows come from a chase over the two states: each row gets a target row under every IND in
both states (reused when the pointer values coincide, fresh otherwise), copies equal their sources,
and unchanged rows are equal in both states.  Values are union-find classes; distinct classes get
distinct constants.  The witness is checked with ref_order2.Sem.legal / Sem.analyse (the engine of
the self-check): both states legal, at least two rows changed, and no order of the single-row
writes keeps every intermediate state legal.
It is applied to every task the decider calls not OS, so it also re-confirms the verdicts the search
already confirmed.
Usage: python -I k_witness.py <selfcheck_K.jsonl> [out.json]"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deciders as D
from selfcheck import ref_schema
from ref_order2 import Sem


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
        if a != b:
            self.p[b] = a


class LightSem(Sem):
    """Sem.legal and Sem.analyse without the tuple enumeration of Sem.__init__"""
    def __init__(self, ref):
        self.sch = ref
        self.ri = {r: i for i, r in enumerate(ref.rlist)}
        self.fdc = []
        self.indc = [(self.ri[R], tuple(ref.rels[R].index(x) for x in X), self.ri[S],
                      tuple(ref.rels[S].index(y) for y in Y)) for (R, X, S, Y) in ref.inds]
        self.byk = [None] * len(ref.rlist)


def chase(sch, Fz, rows, uf, limit=200):
    """rows: list of dicts {rel, I: {a: var}, J: {a: var}}; adds target rows until every IND holds"""
    inds = [(R, list(X), S, list(Y)) for R, X, S, Y in sch['inds']]
    pk = sch['pk']
    i = 0
    while i < len(rows):
        if len(rows) > limit:
            return False
        row = rows[i]
        for R, X, S, Y in inds:
            if R != row['rel']:
                continue
            pos = {y: j for j, y in enumerate(Y)}
            for st in ('I', 'J'):
                ptr = [row[st][X[pos[k]]] for k in pk[S]]
                tgt = None
                for u in rows:
                    if u['rel'] == S and all(uf.find(u[st][k]) == uf.find(p) for k, p in zip(pk[S], ptr)):
                        tgt = u
                        break
                if tgt is None:
                    vals = {a: uf.new() for a in sch['rels'][S]}
                    for k, p in zip(pk[S], ptr):
                        vals[k] = p
                    tgt = {'rel': S, 'I': vals, 'J': vals, 'core': False}
                    rows.append(tgt)
                for x, y in zip(X, Y):
                    if y not in pk[S]:
                        uf.union(tgt[st][y], row[st][x])
        i += 1
    return True


def merge_same_keys(sch, rows, uf):
    """rows of one relation whose keys coincide are one row: unify them (False if both changed)"""
    pk = sch['pk']
    changed = True
    while changed:
        changed = False
        for a in range(len(rows)):
            for b in range(a + 1, len(rows)):
                ra, rb = rows[a], rows[b]
                if ra['rel'] != rb['rel'] or not all(uf.find(ra['I'][k]) == uf.find(rb['I'][k]) for k in pk[ra['rel']]):
                    continue
                if ra['core'] and rb['core']:
                    return False
                for st in ('I', 'J'):
                    for x in sch['rels'][ra['rel']]:
                        uf.union(ra[st][x], rb[st][x])
                keep = ra if ra['core'] else rb
                rows[:] = [r for r in rows if r is not (rb if keep is ra else ra)]
                changed = True
                break
            if changed:
                break
    return True


def build(sch, Fz, ind, kind):
    R, P, Dd, S, C = ind
    uf = UF()
    pkS, pkR = sch['pk'][S], sch['pk'][R]
    fresh_row = lambda rel: {a: uf.new() for a in sch['rels'][rel]}
    if kind == 'swap':
        mp = [j for j, p in enumerate(P) if p not in Fz[R]]
        mc = [c for c in C if c not in Fz[S]]
        if not mp or not mc:
            return None
        c = mc[0]
        k1 = [uf.new() for _ in pkS]
        k2 = [k1[j] if j not in mp else uf.new() for j in range(len(pkS))]
        alpha, beta = uf.new(), uf.new()
        s = []
        for kk, a, b in ((k1, alpha, beta), (k2, beta, alpha)):
            I = fresh_row(S)
            for k, v in zip(pkS, kk):
                I[k] = v
            J = dict(I)
            I[c], J[c] = a, b
            s.append({'rel': S, 'I': I, 'J': J, 'core': True})
        r = []
        for ka, kb in ((k1, k2), (k2, k1)):
            I = fresh_row(R)
            J = dict(I)
            for p, va, vb in zip(P, ka, kb):
                I[p], J[p] = va, vb
            r.append({'rel': R, 'I': I, 'J': J, 'core': True})
        rows = s + r
        diff = [(alpha, beta)]
    else:
        if any(p not in Fz[R] for p in P):
            return None
        mi = [j for j, (c, d) in enumerate(zip(C, Dd)) if c not in Fz[S] and d not in Fz[R]]
        if not mi:
            return None
        c, d = C[mi[0]], Dd[mi[0]]
        alpha, beta = uf.new(), uf.new()
        I = fresh_row(S)
        J = dict(I)
        I[c], J[c] = alpha, beta
        s1 = {'rel': S, 'I': I, 'J': J, 'core': True}
        I = fresh_row(R)
        for p, k in zip(P, pkS):
            I[p] = s1['I'][k]
        J = dict(I)
        I[d], J[d] = alpha, beta
        rows = [s1, {'rel': R, 'I': I, 'J': J, 'core': True}]
        diff = [(alpha, beta)]
    for _ in range(4):
        if not chase(sch, Fz, rows, uf) or not merge_same_keys(sch, rows, uf):
            return None
    if any(uf.find(a) == uf.find(b) for a, b in diff):
        return None
    for row in rows:
        for x in sch['rels'][row['rel']]:
            if x in Fz[row['rel']] and uf.find(row['I'][x]) != uf.find(row['J'][x]):
                return None
    val = {}
    for row in rows:
        for st in ('I', 'J'):
            for x in sch['rels'][row['rel']]:
                val.setdefault(uf.find(row[st][x]), len(val))
    ref = ref_schema(sch, max(2, len(val)))
    sem = LightSem(ref)
    rels = [sem.ri[row['rel']] for row in rows]
    It = [tuple(val[uf.find(row['I'][x])] for x in sch['rels'][row['rel']]) for row in rows]
    Jt = [tuple(val[uf.find(row['J'][x])] for x in sch['rels'][row['rel']]) for row in rows]
    keys = {}
    for row, t in zip(rows, It):
        k = (row['rel'], tuple(t[sch['rels'][row['rel']].index(a)] for a in sch['pk'][row['rel']]))
        if k in keys:
            return None
        keys[k] = True
    if sum(a != b for a, b in zip(It, Jt)) < 2:
        return None
    if not sem.legal(list(zip(rels, It))) or not sem.legal(list(zip(rels, Jt))):
        return None
    exe, _ = sem.analyse(rels, It, Jt)
    if exe:
        return None
    return {'kind': kind, 'rows': len(rows), 'changed': sum(a != b for a, b in zip(It, Jt)), 'values': len(val),
            'I': [[row['rel'], list(t)] for row, t in zip(rows, It)],
            'J': [[row['rel'], list(t)] for row, t in zip(rows, Jt)]}


def witness(sch, Fz):
    ok, why, parsed = D.parse_K(sch)
    assert ok, why
    for ind in parsed:
        R, P, Dd, S, C = ind
        stable = all(c in Fz[S] for c in C) or (all(p in Fz[R] for p in P) and
                                                 all(c in Fz[S] or d in Fz[R] for c, d in zip(C, Dd)))
        if stable:
            continue
        for kind in ('swap', 'copy'):
            w = build(sch, Fz, ind, kind)
            if w:
                w['ind'] = '%s[%s] <= %s[%s]' % (R, ','.join(P + Dd), S, ','.join(sch['pk'][S] + list(C)))
                return w
    return None


def main(path, outp=None):
    """every task the decider calls not OS gets a constructed witness; reported separately for the
    tasks the bounded search left unresolved"""
    res = [json.loads(l) for l in open(path) if l.strip()]
    out = {'not_os_tasks': 0, 'constructed': 0, 'unresolved_by_search': 0, 'resolved_by_construction': 0,
           'not_constructed': [], 'kinds': {}, 'rows': {}, 'rows_unresolved': {}, 'values_max': 0, 'examples': []}
    for r in res:
        sch = r['sch']
        for row in r['rows']:
            if row['dec']:
                continue
            out['not_os_tasks'] += 1
            unres = row['sem'] != 'cycle'
            out['unresolved_by_search'] += unres
            w = witness(sch, {k: set(v) for k, v in row['fz'].items()})
            if w is None:
                out['not_constructed'].append({'seed': r['seed'], 'fz': row['fz'], 'unresolved_by_search': unres})
                continue
            out['constructed'] += 1
            out['resolved_by_construction'] += unres
            out['kinds'][w['kind']] = out['kinds'].get(w['kind'], 0) + 1
            out['rows'][w['rows']] = out['rows'].get(w['rows'], 0) + 1
            if unres:
                out['rows_unresolved'][w['rows']] = out['rows_unresolved'].get(w['rows'], 0) + 1
            out['values_max'] = max(out['values_max'], w['values'])
            if unres and len(out['examples']) < 3:
                out['examples'].append(dict(w, seed=r['seed'], fz=row['fz']))
    out['rows'] = dict(sorted(out['rows'].items()))
    out['rows_unresolved'] = dict(sorted(out['rows_unresolved'].items()))
    s = json.dumps(out, indent=1)
    if outp:
        open(outp, 'w').write(s)
    print(json.dumps({k: v for k, v in out.items() if k != 'examples'}))


if __name__ == '__main__':
    main(*sys.argv[1:3])
