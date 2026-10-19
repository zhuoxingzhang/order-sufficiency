"""E4: shard keys that make fresh updates executable AND keep every unique
key checkable within a shard.

Schema, scenarios and fresh feasibility exactly as in x4_fresh.py: a frozen single column SK is
feasible if SK <= X+ for every elementary FD X -> a with X no superkey and a mutable.  A unique key
is checkable within a shard if rows that agree on it lie in one shard even when they violate it,
that is, if the closure of K under the copy FDs alone (pointer -> sharding-key copy, which the INDs
enforce independently of K) contains SK; a local unique index on K u SK then enforces K.  Containment K >= SK (the Citus rule) is reported as well.
The primary key is excluded, since GitLab draws ids from sequences, which are unique across shards
by construction, and so is every unique key that contains the primary key.
A unique key that misses SK needs a check across shards unless its values are generated: it
contains the sequence column id or a column whose name has a part token, digest, uuid, guid, sha, fingerprint, nonce, secret
or ulid (such values are unique by construction, as ids are).
Per table, the chosen key is the declared sharding key if it is feasible, else the feasible column
that the most unique keys contain.
Usage: python -I x4_unique.py <out.json>"""
import sys, os, json, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deciders as D
import gitlab_schema as G
import x4_shardkey as X4

GEN = {'token', 'digest', 'uuid', 'guid', 'sha', 'fingerprint', 'nonce', 'secret', 'ulid'}


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
        pk = D.mask(rel.pk)
        decl_keys = [D.mask(L) for L in sorted({tuple(sorted(L)) for L, a in rel.decl}) if F.closure(L) == rel.full]
        uniq = [K for K in decl_keys if K & pk != pk]
        copy_fds = [(rel.ix[c], rel.ix[cR]) for c, cR in sorted(extra.get(r, ())) if c in rel.ix and cR in rel.ix]
        keys = X4.minimal_keys(rel, F, [pk] + decl_keys)
        el = X4.elementary(rel, F, keys, copy_fds)
        sk = [a for a in T.get(r, {}).get('sharding_key', {}) if a in rel.ix]
        fzs = set(sch['pk'][r]) | {x for (R, x) in fkcols if R == r} | set(sk)
        if scenario == 'upper':
            fzs |= {a for a in rel.attrs if not X4.MUTABLE_PARTS & set(a.lower().split('_'))}
        fz = D.mask(rel.ix[a] for a in fzs if a in rel.ix)
        feas = fz
        for X, a in el:
            if F.closure(D.bits(X)) != rel.full and not fz >> a & 1:
                feas &= F.closure(D.bits(X))
        cands = [1 << i for i in D.bits(feas)]
        decl = [1 << rel.ix[a] for a in sk]
        gen = lambda K: any(rel.attrs[i] == 'id' or GEN & set(rel.attrs[i].lower().split('_')) for i in D.bits(K))
        row = dict(unique_keys=len(uniq), feasible=len(cands), declared=bool(decl))
        def cclose(K):
            changed = True
            while changed:
                changed = False
                for c, cR in copy_fds:
                    if K >> c & 1 and not K >> cR & 1:
                        K |= 1 << cR
                        changed = True
            return K
        cl = {K: cclose(K) for K in uniq}
        if cands:
            n_in = lambda m: sum(cl[K] & m == m for K in uniq)
            best = next((m for m in decl if m in cands), None) or max(cands, key=lambda m: (n_in(m), -m.bit_length()))
            miss = [K for K in uniq if cl[K] & best != best]
            row.update(chosen=rel.attrs[best.bit_length() - 1], chosen_is_declared=best in decl,
                       checkable_all=not miss, missing=len(miss), missing_generated=sum(gen(K) for K in miss),
                       missing_keys=[[rel.attrs[i] for i in D.bits(K)] for K in miss],
                       contained_all=all(K & best == best for K in uniq),
                       some_column_checkable=any(all(cl[K] & m == m for K in uniq) for m in cands),
                       some_column_contained=any(all(K & m == m for K in uniq) for m in cands),
                       best_missing=min(([[rel.attrs[i] for i in D.bits(K)] for K in uniq if cl[K] & m != m]
                                         for m in cands), key=len),
                       best_missing_generated=min(sum(gen(K) == 0 for K in uniq if cl[K] & m != m) for m in cands) == 0)
        rows[r] = row
    return rows


def summary(rows):
    R = list(rows.values())
    F_ = [r for r in R if r['feasible']]
    miss = [r for r in F_ if r['missing']]
    return dict(tables=len(R), tables_with_unique_keys=sum(r['unique_keys'] > 0 for r in R),
                unique_keys=sum(r['unique_keys'] for r in R),
                no_feasible=len(R) - len(F_),
                chosen_checkable_all=sum(r['checkable_all'] for r in F_),
                chosen_contained_all=sum(r['contained_all'] for r in F_),
                some_feasible_column_checkable=sum(r['some_column_checkable'] for r in F_),
                some_feasible_column_contained=sum(r['some_column_contained'] for r in F_),
                no_column_checkable_but_generated=sum(not r['some_column_checkable'] and r['best_missing_generated'] for r in F_),
                tables_missing=len(miss),
                tables_missing_only_generated=sum(r['missing'] == r['missing_generated'] for r in miss),
                tables_missing_chosen_values=sum(r['missing'] > r['missing_generated'] for r in miss),
                keys_missing=sum(r['missing'] for r in miss),
                keys_missing_generated=sum(r['missing_generated'] for r in miss))


def main(outp):
    g = G.load()
    sch = G.base(g)
    out = {'source': sch['source']}
    for sc in ('lower', 'upper'):
        rows = run(g, sch, sc)
        out[sc] = summary(rows)
        out[sc + '_missing'] = {r: v['missing_keys'] for r, v in sorted(rows.items())
                                if v.get('missing') and v['missing'] > v['missing_generated']}
        out[sc + '_no_column'] = {r: v['best_missing'] for r, v in sorted(rows.items())
                                  if v.get('feasible') and not v['some_column_checkable']}
        out[sc + '_cols_missing'] = collections.Counter(
            c for v in rows.values() for K in v.get('missing_keys', ()) for c in K).most_common(30)
    json.dump(out, open(outp, 'w'), indent=1)
    print(json.dumps({k: out[k] for k in ('source', 'lower', 'upper')}, indent=1))
    print('columns in unique keys that miss the shard key (lower):', out['lower_cols_missing'][:20])


if __name__ == '__main__':
    main(sys.argv[1])
