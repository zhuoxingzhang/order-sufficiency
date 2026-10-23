"""E4: Theorem 5.3 (admissible shard keys for class K) on the copies of GitLab (Figure 8).

Copies are the INDs R[P D] <= S[L C] that GitLab maintains: the attribute copies that the static
analyzer of apps/ finds in the code (evidence assigned, created, pushed; 'code') and the sharding-key
copies that gitlab_schema.copies infers from the declared sharding keys ('sk'), minus the copies that
the code breaks.  The analyzer's triangle heuristic on other columns ('heur') is reported apart.
The workload is the code: an attribute is mutable if some resolved write site modifies it
(data/gitlab_mutable.json, from gl_mutable.py), and every modification is taken to be fresh.
Three scenarios:
  precise    every write counts as a modification and every sharding key as mutable
  filtered   the initialisations of new rows are left out (data/gitlab_mutable_filtered.json, gl_sites.py)
  frozen     as filtered, with the declared sharding keys frozen: GitLab changes them only in explicit
             transfers between organizations, project overwrite moves, imports and backfill migrations
             (gl_sk_sites.py)
(with_star counts writes of unknown attributes as modifications of every attribute.)

Placement is co-location by root, as GitLab's sharding keys suggest: every root table (projects,
namespaces, organizations, users) forms a group and is sharded by its id, and every other table
joins the group of the root that its declared sharding key references and is sharded by that
column; a table with sharding keys to several roots is placed by the one that is set in the row.
An IND carries the shard key if R and S share a group and the IND implies that a row and its
witness agree on their shard keys: the pointer of R is its sharding key and S is the root, or the
sharding-key copy R[P SK(R)] <= S[L SK(S)] runs along the same pointer.  This is a one-hop chase,
so 'carried' is a lower bound.

Theorem 5.3 asks every IND that violates condition (4) of Theorem 4.5 to carry the shard key
(violate_cond4, violate_cond4_carried); for fresh updates, every IND whose copy and source are both
mutable must carry it (two_mutable, Appendix A.4).
Usage: python -I x4_shardk.py <out.json>"""
import collections, gzip, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gitlab_schema as G  # noqa: E402

ROOTS = G.ROOTS
g = G.load()
sch = G.base(g)
T = g['tables']
RAW = {t: set(v) for t, v in json.load(open(os.path.join(HERE, 'data', 'gitlab_mutable.json'), encoding='utf-8'))['precise'].items()}
# gl_sites.py drops initialisations ('||=', callbacks and factories that prepare a new row)
FILT = {t: set(v) for t, v in json.load(open(os.path.join(HERE, 'data', 'gitlab_mutable_filtered.json'), encoding='utf-8'))['precise'].items()}
# GitLab's sharding keys never change by design; the code changes them only in explicit transfers
# (organizations/transfer, project overwrite moves), imports and backfill migrations (gl_sk_sites.py)
SKCOLS = {(t, c) for t, v in T.items() for c in v.get('sharding_key', {})}
FROZEN = {t: {c for c in v if (t, c) not in SKCOLS} for t, v in FILT.items()}
mut = RAW
_p = os.path.join(HERE, '..', 'apps', 'out', 'gitlab.json')
an = json.load(open(_p, encoding='utf-8')) if os.path.exists(_p) else \
    json.load(gzip.open(_p + '.gz', 'rt', encoding='utf-8'))


def is_mut(t, col, star):
    m = mut.get(t, set())
    return col in m or (star and '*' in m)


def groups(t):
    """(root, sharding-key columns to it) for every root of t; a table with sharding keys to several
    roots sets one of them per row and is placed by the one that is set"""
    if t in ROOTS:
        return [(t, ['id'])]
    sk = T.get(t, {}).get('sharding_key', {})
    return [(r, sorted(c for c, x in sk.items() if x == r)) for r in sorted(set(sk.values()))]


def group(t):
    gs = groups(t)
    return gs[0] if gs else (None, [])


skc = collections.defaultdict(set)      # (R, pointer, S) -> {(cR, cS)}
for R, c, cR, S, cS, root in G.copies(g, sch):
    skc[(R, c, S)].add((cR, cS))


def carries(R, P, S):
    if len(P) != 1:
        return False
    for gR, kR in groups(R):
        for gS, kS in groups(S):
            if gR != gS:
                continue
            if S == gR and P[0] in kR:
                return True              # R references its root through its sharding key
            if any(cR in kR and cS in kS for cR, cS in skc.get((R, P[0], S), ())):
                return True
    return False


broken = {(b['table'], tuple(b['fk']), b['ref'], b['d'], b['c']) for b in an['broken']}
inds = {}
for c in an['copies']:
    key = (c['table'], tuple(c['fk']), c['ref'], c['d'], c['c'])
    if key in broken or c['table'] == c['ref']:
        continue
    inds.setdefault(key, c['evidence'])
SKCOPY = set()
for R, c, cR, S, cS, root in G.copies(g, sch):
    inds.setdefault((R, (c,), S, cR, cS), 'triangle')
    SKCOPY.add((R, (c,), S, cR, cS))


def source(key, ev):
    # code: the analyzer saw the code write the copy; sk: GitLab's sharding-key copy;
    # heur: the analyzer's triangle heuristic on other columns, kept out of the headline
    return 'code' if ev in ('assigned', 'created', 'pushed') else 'sk' if key in SKCOPY else 'heur'


def assess(star):
    rows = []
    for (R, P, S, D, C), ev in sorted(inds.items()):
        Cm, Dm = is_mut(S, C, star), is_mut(R, D, star)
        Pm = any(is_mut(R, p, star) for p in P)
        viol = Cm and (Pm or Dm)               # condition (4) of Theorem 4.5 fails
        rows.append(dict(R=R, P=list(P), S=S, D=D, C=C, ev=ev, src=source((R, P, S, D, C), ev),
                         Cm=Cm, Dm=Dm, Pm=Pm, viol=viol,
                         two=Cm and Dm, carries=carries(R, P, S), group=[group(R)[0], group(S)[0]]))
    return rows


def summary(rows):
    n = len(rows)
    ev = collections.Counter(r['ev'] for r in rows)
    viol = [r for r in rows if r['viol']]
    two = [r for r in rows if r['two']]
    by_src = {k: dict(copies=sum(r['src'] == k for r in rows),
                      violate_cond4=sum(r['viol'] and r['src'] == k for r in rows),
                      violate_cond4_carried=sum(r['viol'] and r['carries'] and r['src'] == k for r in rows),
                      two_mutable=sum(r['two'] and r['src'] == k for r in rows),
                      two_mutable_carried=sum(r['two'] and r['carries'] and r['src'] == k for r in rows))
              for k in ('code', 'sk', 'heur')}
    return dict(copies=n, by_evidence=dict(ev), by_source=by_src,
                source_mutable=sum(r['Cm'] for r in rows), copy_mutable=sum(r['Dm'] for r in rows),
                pointer_mutable=sum(r['Pm'] for r in rows),
                violate_cond4=len(viol), violate_cond4_carried=sum(r['carries'] for r in viol),
                two_mutable=len(two), two_mutable_carried=sum(r['carries'] for r in two),
                gap_only=sum(1 for r in viol if not r['two']),
                two_mutable_by_pair=collections.Counter(f"{r['R']}->{r['S']}" for r in two).most_common(40),
                not_carried=[f"{r['R']}[{','.join(r['P'])},{r['D']}] <= {r['S']}[{r['C']}] ({r['ev']})"
                             for r in two if not r['carries']][:60],
                carried=[f"{r['R']}[{','.join(r['P'])},{r['D']}] <= {r['S']}[{r['C']}] ({r['ev']})"
                         for r in two if r['carries']][:60])


# declared sharding keys that the code modifies: such a table would move its rows between shards
sk_mut = sorted(f'{t}.{c}' for t, v in T.items() for c in v.get('sharding_key', {}) if is_mut(t, c, False))
res = dict(precise=summary(assess(False)), with_star=summary(assess(True)), sharding_keys_modified=sk_mut,
           declared_sharding_keys=sum(len(v.get('sharding_key', {})) for v in T.values()))
mut = FILT
res['filtered'] = summary(assess(False))
res['filtered_sharding_keys_modified'] = sorted(f'{t}.{c}' for t, c in SKCOLS if is_mut(t, c, False))
mut = FROZEN
res['frozen'] = summary(assess(False))
res['frozen_rows'] = [r for r in assess(False) if r['two'] or r['viol']]
json.dump(res, open(sys.argv[1], 'w', encoding='utf-8'), indent=1)
for k in ('precise', 'with_star', 'filtered', 'frozen'):
    s = res[k]
    print(k, 'by source', s['by_source'])
    print(k, {x: s[x] for x in ('copies', 'by_evidence', 'source_mutable', 'copy_mutable', 'pointer_mutable',
                                 'violate_cond4', 'violate_cond4_carried', 'two_mutable', 'two_mutable_carried',
                                 'gap_only')})
print('sharding keys modified by code:', len(sk_mut), 'of', res['declared_sharding_keys'], sk_mut[:20])
print('Figure 8 (code + sk copies): violating, carried')
for k, name in (('precise', 'every write'), ('filtered', 'no initial writes'), ('frozen', 'frozen sharding keys')):
    b = res[k]['by_source']
    print('  %-22s %4d %4d' % (name, b['code']['violate_cond4'] + b['sk']['violate_cond4'],
                               b['code']['violate_cond4_carried'] + b['sk']['violate_cond4_carried']))
