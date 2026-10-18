"""E4 under fresh-value modifications (Proposition 3.8).

A modification update is fresh if every value that it writes into a left-side attribute (an
attribute of the primary key or of the left side of a declared FD) is new in its column.  For FDs
only, a fresh update can violate an elementary FD X -> a in an intermediate state only if X is no
superkey and a is mutable, and then exactly when the two tuples agree on X in I.  Hence:
  fresh order-sufficiency   every elementary FD X -> a with X no superkey has a frozen a;
  fresh shard keys          SK <= X+ for every elementary FD X -> a with X no superkey, a mutable;
  fresh templates           the template that modifies column c needs atomic visibility under SK
                            iff some elementary FD X -> c with X no superkey has X+ not >= SK.
Schema, elementary FDs and scenarios exactly as in x4_shardkey.py (lower / upper).
Usage: python -I x4_fresh.py <out.json>"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deciders as D
import gitlab_schema as G
import x4_shardkey as X4


def table(rel, F, fz, el, decl):
    full = rel.full
    nonkey = [(X, a) for X, a in el if F.closure(D.bits(X)) != full]
    viol = [(X, a) for X, a in nonkey if not fz >> a & 1]          # violable by fresh updates
    feas = fz
    for X, a in viol:
        feas &= F.closure(D.bits(X))
    mutable = [c for c in range(rel.n) if not fz >> c & 1]
    tm = {c: [X for X, a in nonkey if a == c] for c in mutable}

    def need(sk):
        return sum(any(F.closure(D.bits(X)) & sk != sk for X in tm[c]) for c in mutable)

    pk = D.mask(rel.pk)
    decl_cols = [1 << i for i in decl if fz >> i & 1]
    cands = [1 << i for i in D.bits(fz)]
    best = next((m for m in decl_cols if feas & m), None) or next((m for m in cands if feas & m), None)
    if best is None:
        best = min(cands, key=need)
    out = dict(violable=len(viol), nonkey_fds=len(nonkey), feasible=bin(feas).count('1'),
               templates=len(mutable), atomic_pk=need(pk), atomic_chosen=need(best),
               violable_rhs=sorted({rel.attrs[a] for X, a in viol}))
    if decl_cols:
        out['atomic_declared'] = min(need(m) for m in decl_cols)
        out['declared_feasible'] = any(feas & m for m in decl_cols)
    return out


def run(g, sch, scenario):
    T = g['tables']
    extra = {}
    for R, c, cR, S, cS, root in G.copies(g, sch):
        if R != S and c != cR:
            extra.setdefault(R, set()).add((c, cR))
    sch = dict(sch, fds=sch['fds'] + [(R, [c], cR) for R in sorted(extra) for c, cR in sorted(extra[R])])
    rels = D.index_schema(sch)
    fkcols = {(R, x) for R, X, S, Y in sch['inds'] for x in X}
    rows = {}
    for r, rel in rels.items():
        F = rel.fdset()
        decl_keys = [D.mask(L) for L in sorted({tuple(sorted(L)) for L, a in rel.decl}) if F.closure(L) == rel.full]
        copy_fds = [(rel.ix[c], rel.ix[cR]) for c, cR in sorted(extra.get(r, ())) if c in rel.ix and cR in rel.ix]
        keys = X4.minimal_keys(rel, F, [D.mask(rel.pk)] + decl_keys)
        el = X4.elementary(rel, F, keys, copy_fds)
        sk = [a for a in T.get(r, {}).get('sharding_key', {}) if a in rel.ix]
        fzs = set(sch['pk'][r]) | {x for (R, x) in fkcols if R == r} | set(sk)
        if scenario == 'upper':
            fzs |= {a for a in rel.attrs if not X4.MUTABLE_PARTS & set(a.lower().split('_'))}
        fz = D.mask(rel.ix[a] for a in fzs if a in rel.ix)
        rows[r] = table(rel, F, fz, el, [rel.ix[a] for a in sk])
    return rows


def summary(rows):
    R = list(rows.values())
    Dd = [r for r in R if 'atomic_declared' in r]
    return dict(tables=len(R), tables_with_violable_fd=sum(r['violable'] > 0 for r in R),
                violable_fds=sum(r['violable'] for r in R), nonkey_fds=sum(r['nonkey_fds'] for r in R),
                none_feasible=sum(r['feasible'] == 0 for r in R),
                templates=sum(r['templates'] for r in R), atomic_pk=sum(r['atomic_pk'] for r in R),
                atomic_chosen=sum(r['atomic_chosen'] for r in R),
                tables_with_declared_key=len(Dd), declared_infeasible=sum(not r['declared_feasible'] for r in Dd),
                atomic_pk_in_declared=sum(r['atomic_pk'] for r in Dd),
                atomic_declared=sum(r['atomic_declared'] for r in Dd),
                atomic_chosen_in_declared=sum(r['atomic_chosen'] for r in Dd))


def main(outp):
    g = G.load()
    sch = G.base(g)
    out = {'source': sch['source']}
    for sc in ('lower', 'upper'):
        rows = run(g, sch, sc)
        out[sc] = summary(rows)
        out[sc + '_violable'] = {r: v['violable_rhs'] for r, v in sorted(rows.items()) if v['violable']}
    json.dump(out, open(outp, 'w'), indent=1)
    print(json.dumps({k: out[k] for k in ('source', 'lower', 'upper')}, indent=1))
    print('tables with violable FDs (lower):', list(out['lower_violable'].items())[:20])


if __name__ == '__main__':
    main(sys.argv[1])
