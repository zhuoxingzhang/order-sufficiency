"""The GitLab schema as a dependency schema (input: data/gitlab_schema.json from gitlab_extract.py).

Two sources, chosen automatically:
  docs       db/docs + loose foreign keys only: every table gets PK id, its sharding-key columns
             (each an IND to the root table's id) and its loose-FK columns (IND to parent.id).
  structure  db/structure.sql when extracted: real columns, primary keys, non-partial unique
             indexes (keys, hence FDs), and foreign keys; loose FKs and sharding keys are added.
Derived pieces:
  base()      relations, PKs, key FDs, unary/composite INDs (FKs to referenced columns);
  copies()    the inferred sharding-key copies R[c s_R] <= S[id s_S]: R references S through
              column c and both carry a sharding key to the same root, so R's key is a copy of S's;
  k_fragment  the class-K schema made of these copies (each child key copied from one parent,
              parents whose key is itself a copy excluded), plus plain FKs."""
import os, json
from deciders import parse_K
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOTS = ('projects', 'namespaces', 'organizations', 'users')


def load(path=None):
    return json.load(open(path or os.path.join(HERE, 'data', 'gitlab_schema.json')))


def base(g):
    T = g['tables']
    st = g.get('structure')
    rels, pk, fds, inds = {}, {}, [], []
    src = 'structure' if st else 'docs'

    def add_attr(r, a):
        if a not in rels[r]:
            rels[r].append(a)

    for t in sorted(T):
        if st and t in st['tables'] and st['tables'][t]['columns']:
            s = st['tables'][t]
            rels[t] = list(s['columns'])
            pk[t] = list(s['pk']) or (['id'] if 'id' in s['columns'] else list(s['columns'][:1]))
            for u in s['uniques']:
                if set(u) <= set(rels[t]) and set(u) != set(pk[t]):
                    for a in rels[t]:
                        if a not in u:
                            fds.append((t, list(u), a))
        else:
            rels[t] = ['id']
            pk[t] = ['id']
    for t in sorted(T):
        for col, root in sorted(T[t]['sharding_key'].items()):
            if root in rels and col:
                add_attr(t, col)
                if (t, col) != (root, pk[root][0]):
                    inds.append((t, [col], root, [pk[root][0]]))
    seen = {(R, tuple(X), S) for R, X, S, Y in inds}
    for r in g['lfk']:
        c, p = r['child'], r['table']
        if c in rels and p in rels:
            add_attr(c, r['column'])
            if (c, (r['column'],), p) not in seen:
                inds.append((c, [r['column']], p, ['id' if 'id' in rels[p] else pk[p][0]]))
                seen.add((c, (r['column'],), p))
    if st:
        for f in st['fks']:
            c, p = f['child'], f['parent']
            if c in rels and p in rels and all(x in rels[c] for x in f['cols']) and all(y in rels[p] for y in f['pcols']):
                if (c, tuple(f['cols']), p) not in seen:
                    inds.append((c, list(f['cols']), p, list(f['pcols'])))
                    seen.add((c, tuple(f['cols']), p))
    return {'rels': rels, 'pk': pk, 'fds': fds, 'inds': inds, 'source': src}


def copies(g, sch):
    """inferred sharding-key copies: (child, pointer col, child key col, parent, parent key col, root)"""
    T = g['tables']
    out = []
    for R, X, S, Y in sch['inds']:
        if len(X) != 1 or S in ROOTS and Y == ['id'] and X[0] in T.get(R, {}).get('sharding_key', {}):
            continue
        skR, skS = T.get(R, {}).get('sharding_key', {}), T.get(S, {}).get('sharding_key', {})
        for cR, rootR in sorted(skR.items()):
            for cS, rootS in sorted(skS.items()):
                if rootR == rootS and cR != X[0]:
                    out.append((R, X[0], cR, S, cS, rootR))
    return out


def k_fragment(g, sch):
    """class-K schema of the copies; returns (schema, stats).  With the structure source the real
    primary keys are used and the copies that leave the final class K are excluded: the pointer
    must reference PK(S) (a single column), the copy must lie outside PK(R), and the pointer of a
    copy IND must neither contain PK(R) nor lie within it (one-to-one extension tables)."""
    cp = copies(g, sch)
    st = g.get('structure')
    real = lambda t: (st['tables'][t]['pk'] if st and t in st['tables'] and st['tables'][t]['pk'] else ['id'])
    target = defaultdict(set)
    for R, X, S, Y in sch['inds']:
        if len(X) == 1:
            target[(R, X[0], S)].add(Y[0])
    copy_attr = {}
    for R, c, cR, S, cS, root in cp:
        copy_attr.setdefault((R, cR), (R, c, cR, S, cS, root))
    kept, chain = [], 0
    excl = {'target_not_pk': 0, 'copy_in_pk': 0, 'pointer_vs_pk': 0}
    for key, (R, c, cR, S, cS, root) in sorted(copy_attr.items()):
        if (S, cS) in copy_attr:
            chain += 1
            continue
        pkR, pkS = real(R), real(S)
        if len(pkS) != 1 or pkS[0] not in target[(R, c, S)] or cS in pkS:
            excl['target_not_pk'] += 1
        elif cR in pkR:
            excl['copy_in_pk'] += 1
        elif c in pkR:
            excl['pointer_vs_pk'] += 1
        else:
            kept.append((R, c, cR, S, cS, root))
    rels = defaultdict(list)
    pk = {}
    inds, used = [], set()

    def add(r, a):
        if r not in pk:
            pk[r] = list(real(r))
            rels[r].extend(pk[r])
        if a not in rels[r]:
            rels[r].append(a)

    by_ptr = defaultdict(list)
    for R, c, cR, S, cS, root in kept:
        by_ptr[(R, c, S)].append((cR, cS))
    for (R, c, S), pairs in sorted(by_ptr.items()):
        if R == S:
            continue
        k = real(S)[0]
        add(R, c)
        add(S, k)
        D, C = [], []
        for cR, cS in pairs:
            if cS in C:
                continue
            add(R, cR)
            add(S, cS)
            D.append(cR)
            C.append(cS)
        inds.append((R, [c] + D, S, [k] + C))
    sch_k = {'rels': dict(rels), 'pk': pk, 'fds': [], 'inds': inds}
    ok, why, _ = parse_K(sch_k)
    stats = dict(copies_inferred=len(cp), copy_attributes=len(copy_attr), chains_excluded=chain,
                 excluded_target_not_pk=excl['target_not_pk'], excluded_copy_in_pk=excl['copy_in_pk'],
                 excluded_pointer_in_pk=excl['pointer_vs_pk'], k_inds=len(inds), k_relations=len(rels),
                 in_final_class=ok, class_reason=why)
    return sch_k, stats
