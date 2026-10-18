"""E3 on the real GitLab schema: class coverage and decider run times.

Schema from gitlab_schema.base (docs source: PK id, sharding keys and loose FKs; structure source:
also columns, unique keys and foreign keys).  Pieces:
  FD   the key FDs alone (unique indexes); trivial when the docs source is used;
  U    FDs plus every unary IND (the whole schema when all INDs are unary);
  A    the class-A part: unary INDs inside a strongly connected component of the relation graph
       dropped, then INDs whose right side is not a minimal key or is a second key of its target;
  K    the class-K fragment of inferred sharding-key copies (gitlab_schema.k_fragment).
Frozen sets: pk (only primary keys frozen) and ptr (primary keys, the columns of every IND of the whole
schema and the sharding keys frozen; the same Fz for every piece but K).  Reported per piece and frozen set: size, membership, verdict, number of unstable
constraints, decider time (median of `reps`); also the declared non-PK unique keys and how many
contain a column outside Fz.
Usage: python -I x3_gitlab.py <reps> <out.json>"""
import sys, os, json, time, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deciders as D
import gitlab_schema as G


def acyclic_part(sch):
    adj = {}
    for R, X, S, Y in sch['inds']:
        adj.setdefault(R, set()).add(S)
    comp = D.tarjan(list(sch['rels']), adj)
    size = {}
    for c in comp.values():
        size[c] = size.get(c, 0) + 1
    keep = [i for i in sch['inds'] if comp[i[0]] != comp[i[2]] or (size[comp[i[0]]] == 1 and i[0] != i[2])]
    keep = [i for i in keep if not (comp[i[0]] == comp[i[2]])]
    dropped = len(sch['inds']) - len(keep)
    return dict(sch, inds=keep), dropped


def key_part(sch):
    """class A also needs every right side to be a minimal key, one key per target: drop the INDs
    that violate it (counts per reason)"""
    rels = D.index_schema(sch)
    keep, key_of, drop = [], {}, {'rhs not a key': 0, 'rhs not minimal': 0, 'second key of target': 0}
    for R, X, S, Y in sch['inds']:
        rel = rels[S]
        F = rel.fdset()
        ym = D.mask(rel.ix[y] for y in Y)
        if F.closure(D.bits(ym)) != rel.full:
            drop['rhs not a key'] += 1
        elif any(F.closure(D.bits(ym & ~(1 << y))) == rel.full for y in D.bits(ym)):
            drop['rhs not minimal'] += 1
        elif key_of.setdefault(S, ym) != ym:
            drop['second key of target'] += 1
        else:
            keep.append((R, X, S, Y))
    return dict(sch, inds=keep), drop


def unique_keys(sch, Fz):
    """declared non-PK keys (left sides of the key FDs) and those with a column outside Fz"""
    keys = sorted({(R, tuple(L)) for R, L, a in sch['fds']})
    mut = [(R, L) for R, L in keys if not set(L) <= Fz[R]]
    return len(keys), len(mut), len({R for R, L in mut})


def frozen(sch, g, which):
    Fz = {r: set(sch['pk'][r]) for r in sch['rels']}
    if which == 'ptr':
        for R, X, S, Y in sch['inds']:
            Fz[R] |= set(X)
        for t, d in g['tables'].items():
            if t in Fz:
                Fz[t] |= {c for c in d['sharding_key'] if c in sch['rels'][t]}
    return Fz


def timed(f, reps):
    ts, res = [], None
    for _ in range(reps):
        t0 = time.perf_counter()
        res = f()
        ts.append(time.perf_counter() - t0)
    return res, statistics.median(ts)


def main(reps, outp):
    g = G.load()
    sch = G.base(g)
    out = {'source': sch['source'], 'relations': len(sch['rels']),
           'attributes': sum(len(a) for a in sch['rels'].values()), 'fds': len(sch['fds']),
           'inds': len(sch['inds']), 'unary_inds': sum(len(X) == 1 for _, X, _, _ in sch['inds']),
           'pieces': []}
    unary = dict(sch, inds=[i for i in sch['inds'] if len(i[1]) == 1])
    acyc, dropped = acyclic_part(unary)
    out['inds_in_cycles'] = dropped
    acyc, out['a_dropped'] = key_part(acyc)
    for which in ('pk', 'ptr'):
        n, m, t = unique_keys(sch, frozen(sch, g, which))
        out['unique_keys'] = n
        out['keys_with_mutable_column_' + which] = m
        out['tables_with_such_keys_' + which] = t
    k, kst = G.k_fragment(g, sch)
    out['k_fragment'] = kst
    fd_only = dict(sch, inds=[])
    pieces = [('FD', fd_only, D.dec_fd), ('U', unary, D.dec_unary), ('A', acyc, D.dec_A), ('K', k, D.dec_K)]
    for name, s, dec in pieces:
        if name == 'A':
            mem, tm = timed(lambda: D.class_A(s)[0], reps)
        elif name == 'K':
            mem, tm = timed(lambda: D.parse_K(s)[0], reps)
        else:
            mem, tm = True, 0.0
        for which in ('pk', 'ptr'):
            Fz = frozen(sch, g, which) if name != 'K' else frozen(s, {'tables': {}}, which)
            (ok, bad), td = timed(lambda: dec(s, Fz), reps)
            kinds = {}
            for b in bad:
                kinds[b.split()[0]] = kinds.get(b.split()[0], 0) + 1
            out['pieces'].append(dict(piece=name, fz=which, relations=len(s['rels']), fds=len(s['fds']),
                                      inds=len(s['inds']), member=mem, t_member_ms=1000 * tm,
                                      order_sufficient=ok, unstable=kinds, examples=bad[:8],
                                      t_decide_ms=1000 * td))
    json.dump(out, open(outp, 'w'), indent=1)
    print(json.dumps(out, indent=1)[:5000])


if __name__ == '__main__':
    main(int(sys.argv[1]), sys.argv[2])
