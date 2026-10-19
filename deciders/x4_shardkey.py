"""E4: Theorem 5.2 on GitLab against fairer baselines.

Schema as in x4_design.bpi_gitlab: keys from the primary key and the unique constraints and
non-partial unique indexes of db/structure.sql, plus the copy FDs pointer -> sharding-key copy that
the inferred copies R[c s_R] <= S[id s_S] imply.  Elementary FDs of Sigma+: the copy FDs and K -> a
for every minimal key K (derived keys included, Lucchesi-Osborn) whenever K -> a is left-reduced.
A sharding key must be frozen (SK <= Fz).

Mutability scenarios (GitLab declares none):
  lower  PK, FK columns and sharding-key columns are frozen, every other column is mutable
         (the setting of x4_design);
  upper  in addition, every column is frozen unless a part of its name (split at '_') is one of
         name, names, username, path, paths, email, emails, token, tokens.  The columns that
         GitLab never updates cannot be estimated statically without its application source,
         which is not downloaded; this rule stands in for that estimate.

Single-column sharding keys feasible per table:
  bpi           SK <= (X n Fz)+ for every unstable elementary FD X -> a      (Theorem 5.2)
  unstable_cont SK <= X for every unstable elementary FD X -> a              (containment, but only
                for the FDs that Theorem 5.2 constrains); also reported over the declared keys
                and copy FDs alone (unstable_cont_declared), without the keys derived through
                copy FDs such as (pipeline_id, iid) from pipeline_id -> project_id; tables where this
                variant admits a column that Theorem 5.2 rejects are counted, since it ignores
                unstable FDs of Sigma+
  citus         SK <= K for the primary key and every declared unique key    (Citus rule; it does
                not depend on Fz, since PK <= Fz)

Mitigation, SK = PK against the key Theorem 5.2 chooses (and the declared GitLab sharding key):
  unstable FDs served   (X n Fz)+ >= SK.  SK = PK serves none, since (X n Fz)+ >= PK makes X n Fz
                        a superkey and the FD stable.
  templates             one modification template per mutable column c, changing c in any number
                        of rows.  It needs atomic visibility under SK iff some elementary FD that is
                        unstable under Fz_c = (all columns but c) has (X n Fz_c)+ not >= SK, i.e.
                        Theorem 5.2 applied to the template alone.
  chosen key            a feasible single column if there is one (the declared key when feasible),
                        otherwise the frozen single column serving most unstable FDs, then most
                        templates (ties: declared key, then column order).
Usage: python -I x4_shardkey.py <out.json>"""
import sys, os, json, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deciders as D
import gitlab_schema as G

MUTABLE_PARTS = {'name', 'names', 'username', 'path', 'paths', 'email', 'emails', 'token', 'tokens'}


def minimal_keys(rel, F, seeds):
    """all minimal keys reachable from the seed keys by Lucchesi-Osborn"""
    def reduce(K):
        for x in list(D.bits(K)):
            if F.closure(D.bits(K & ~(1 << x))) == rel.full:
                K &= ~(1 << x)
        return K
    keys = []
    todo = [reduce(K) for K in seeds]
    fds = [(D.mask(L), a) for L, a in zip(F.lhs, F.rhs)]
    while todo:
        K = todo.pop()
        if K in keys:
            continue
        keys.append(K)
        for L, a in fds:
            if K >> a & 1:
                K2 = reduce((K & ~(1 << a)) | L)
                if not any(k & K2 == k for k in keys) and K2 not in todo:
                    todo.append(K2)
    return [K for K in keys if not any(k != K and k & K == k for k in keys)]


def minimal_keys_of(rel, F, seeds):
    """the seed keys, reduced to minimal keys (no derivation)"""
    ks = []
    for K in seeds:
        for x in list(D.bits(K)):
            if F.closure(D.bits(K & ~(1 << x))) == rel.full:
                K &= ~(1 << x)
        if K not in ks:
            ks.append(K)
    return [K for K in ks if not any(k != K and k & K == k for k in ks)]


def elementary(rel, F, keys, copy_fds):
    out = [(D.mask([c]), a) for c, a in copy_fds]
    for K in keys:
        sub = 0
        for x in D.bits(K):
            sub |= F.closure(D.bits(K & ~(1 << x)))
        for a in range(rel.n):
            if not K >> a & 1 and not sub >> a & 1:
                out.append((K, a))
    return out


def unstable(F, full, fz, X, a):
    return F.closure(D.bits(X & fz)) != full and (X | (1 << a)) & ~fz != 0


def served(F, fz, X, sk):
    return F.closure(D.bits(X & fz)) & sk == sk


def table(rel, F, fz, keys, el, el_decl, decl):
    full = rel.full
    U = [(X, a) for X, a in el if unstable(F, full, fz, X, a)]
    bpi = fz
    uc = fz
    for X, a in U:
        bpi &= F.closure(D.bits(X & fz))
        uc &= X
    ucd = fz
    for X, a in el_decl:
        if unstable(F, full, fz, X, a):
            ucd &= X
    citus = fz
    for K in [D.mask(rel.pk)] + keys:
        citus &= K
    mutable = [c for c in range(rel.n) if not fz >> c & 1]
    tmpl = []                                   # per template: its unstable FDs under Fz_c
    for c in mutable:
        fzc = full & ~(1 << c)
        tmpl.append((c, [(X, a) for X, a in el if unstable(F, full, fzc, X, a)], fzc))

    def score(sk):
        s_fd = sum(served(F, fz, X, sk) for X, a in U)
        s_t = sum(all(served(F, fzc, X, sk) for X, a in us) for c, us, fzc in tmpl)
        return s_fd, s_t

    pk = D.mask(rel.pk)
    t_need = lambda sk: len(tmpl) - score(sk)[1]
    cands = [1 << i for i in D.bits(fz)]
    decl_cols = [1 << i for i in decl if fz >> i & 1]
    best = max(cands, key=lambda m: (score(m), m in decl_cols, -m.bit_length()))
    if bpi:
        feas = [m for m in cands if bpi & m]
        best = next((m for m in decl_cols if bpi & m), feas[0])
    out = dict(unstable=len(U), templates=len(tmpl),
               templates_atomic_pk=t_need(pk), templates_atomic_chosen=t_need(best),
               served_chosen=score(best)[0], served_pk=score(pk)[0],
               bpi=bin(bpi).count('1'), unstable_cont=bin(uc).count('1'), unstable_cont_declared=bin(ucd).count('1'), citus=bin(citus).count('1'),
               unstable_cont_declared_beyond_bpi=bin(ucd & ~bpi).count('1'), citus_beyond_bpi=bin(citus & ~bpi).count('1'),
               chosen=rel.attrs[best.bit_length() - 1])
    if decl_cols:
        bd = max(decl_cols, key=score)
        out.update(served_declared=score(bd)[0], templates_atomic_declared=t_need(bd))
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
        # declared unique keys (the copy FDs also sit in rel.decl, but their pointers are no keys)
        decl_keys = [D.mask(L) for L in sorted({tuple(sorted(L)) for L, a in rel.decl}) if F.closure(L) == rel.full]
        copy_fds = [(rel.ix[c], rel.ix[cR]) for c, cR in sorted(extra.get(r, ())) if c in rel.ix and cR in rel.ix]
        keys = minimal_keys(rel, F, [D.mask(rel.pk)] + decl_keys)
        el = elementary(rel, F, keys, copy_fds)
        sk = [a for a in T.get(r, {}).get('sharding_key', {}) if a in rel.ix]
        fzs = set(sch['pk'][r]) | {x for (R, x) in fkcols if R == r} | set(sk)
        if scenario == 'upper':
            fzs |= {a for a in rel.attrs if not MUTABLE_PARTS & set(a.lower().split('_'))}
        fz = D.mask(rel.ix[a] for a in fzs if a in rel.ix)
        # the Citus rule uses the declared keys only, not derived ones
        el_decl = elementary(rel, F, minimal_keys_of(rel, F, [D.mask(rel.pk)] + decl_keys), copy_fds)
        row = table(rel, F, fz, decl_keys, el, el_decl, [rel.ix[a] for a in sk])
        row['mutable_columns'] = rel.n - bin(fz).count('1')
        row['derived_keys'] = len([K for K in keys if K not in decl_keys and K != D.mask(rel.pk)])
        rows[r] = row
    return rows


def summary(rows):
    R = list(rows.values())
    s = lambda k: sum(r[k] for r in R)
    out = dict(tables=len(R), mutable_columns=s('mutable_columns'),
               tables_with_unstable_fds=sum(r['unstable'] > 0 for r in R), unstable_fds=s('unstable'),
               none_bpi=sum(r['bpi'] == 0 for r in R), none_unstable_cont=sum(r['unstable_cont'] == 0 for r in R),
               none_unstable_cont_declared=sum(r['unstable_cont_declared'] == 0 for r in R),
               none_citus=sum(r['citus'] == 0 for r in R),
               none_unstable_cont_among_unstable=sum(r['unstable_cont'] == 0 for r in R if r['unstable']),
               none_bpi_among_unstable=sum(r['bpi'] == 0 for r in R if r['unstable']),
               mean_bpi=statistics.mean(r['bpi'] for r in R), mean_unstable_cont=statistics.mean(r['unstable_cont'] for r in R),
               mean_citus=statistics.mean(r['citus'] for r in R),
               templates=s('templates'), templates_atomic_pk=s('templates_atomic_pk'),
               templates_atomic_chosen=s('templates_atomic_chosen'),
               tables_with_a_template_mitigated=sum(r['templates_atomic_pk'] > r['templates_atomic_chosen'] for r in R),
               unstable_served_chosen=s('served_chosen'), unstable_served_pk=s('served_pk'),
               derived_keys=s('derived_keys'),
               tables_where_declared_containment_admits_more=sum(r['unstable_cont_declared_beyond_bpi'] > 0 for r in R),
               tables_where_citus_admits_more=sum(r['citus_beyond_bpi'] > 0 for r in R))
    D_ = [r for r in R if 'served_declared' in r]
    out.update(tables_with_declared_key=len(D_), unstable_in_those=sum(r['unstable'] for r in D_),
               unstable_served_declared=sum(r['served_declared'] for r in D_),
               unstable_served_chosen_in_those=sum(r['served_chosen'] for r in D_),
               templates_in_those=sum(r['templates'] for r in D_),
               templates_atomic_pk_in_those=sum(r['templates_atomic_pk'] for r in D_),
               templates_atomic_declared=sum(r['templates_atomic_declared'] for r in D_),
               templates_atomic_chosen_in_those=sum(r['templates_atomic_chosen'] for r in D_))
    return out


def main(outp):
    g = G.load()
    sch = G.base(g)
    out = {'source': sch['source']}
    for sc in ('lower', 'upper'):
        rows = run(g, sch, sc)
        out[sc] = summary(rows)
        out[sc + '_examples'] = {r: v for r, v in sorted(rows.items())
                                 if v['templates_atomic_pk'] > v['templates_atomic_chosen']}
        out[sc + '_no_bpi_key'] = sorted(r for r, v in rows.items() if v['bpi'] == 0)
    json.dump(out, open(outp, 'w'), indent=1)
    print(json.dumps({k: out[k] for k in ('source', 'lower', 'upper')}, indent=1))


if __name__ == '__main__':
    main(sys.argv[1])
