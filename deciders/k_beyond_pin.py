"""The disagreements of k_beyond.py under the pin closure.

For every task outside the final class K, Sigma_stab |= Sigma is decided again by kmodel.py, once over M* (stab, a cross-check of k_beyond's condition) and
once over the pin closure M-hat (stabpin), in which a frozen copy pins its source in every
referenced entity.  The semantic verdict comes from k_beyond.jsonl: the condition holds (executable
by Theorem 3.5), a non-executable update was found, or neither (a disagreement).  Disagreements that
stabpin does not resolve and where no non-commutative update was found are searched again for one
by ksat over 6 entities and 4 values, then 9 entities with at most 4 changed.

Scope of the conjecture of the paper (Appendix A.3), per task: key_via_ind (a relation has a key
besides its primary key under Sigma+), pullback (an IND of Sigma+ references a key K of S and a
further attribute of S, both inside M*(S), through a pointer outside M*(R): frozen reference data
fix a function of a mutable pointer, as in Example A.9), and injective (some IND has a pointer that
contains the primary key of its own relation, as in an example of the appendix).
Usage: python -I k_beyond_pin.py <k_beyond.jsonl> <k_beyond_recheck.json> <procs> <out.json>"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from multiprocessing import Pool
from kmodel import Sch
from k_beyond import SatSch, Theory

NONCOMM_STAGES = [(6, 6, 4, 2), (9, 4, 3, 2)]


def scope(th, sch, fz):
    M = th.frozen_closure(fz)
    pull = False
    for R, S, P in th.maxinds:
        if R == S:
            continue
        ys = {y for _, y in P}
        for K in th.keys[S]:
            ptr = {x for x, y in P if y in K}
            if K <= ys and K <= M[S] and not ptr <= M[R] and any(y not in K and y in M[S] for y in ys):
                pull = True
    inj = any(set(x for x, y in zip(X, Y) if y in sch['pk'][S]) >= set(sch['pk'][R]) for R, X, S, Y in sch['inds'])
    return dict(key_via_ind=any(len(ks) > 1 for ks in th.keys.values()), pullback=pull, injective=inj)


def to_kmodel(sch):
    inds = []
    for R, X, S, Y in sch['inds']:
        pk = sch['pk'][S]
        if not set(pk) <= set(Y):
            return None
        pos = [Y.index(k) for k in pk]
        rest = [j for j in range(len(Y)) if j not in pos]
        inds.append({'R': R, 'P': [X[j] for j in pos], 'S': S,
                     'D': [X[j] for j in rest], 'C': [Y[j] for j in rest]})
    return Sch(sch['rels'], sch['pk'], inds)


def job(args):
    r, found4 = args
    import ksat
    m = to_kmodel(r['sch'])
    th = Theory(r['sch'])
    out = []
    for i, row in enumerate(r['rows']):
        fz = {R: set(v) for R, v in row['fz'].items()}
        verdict = ('exec' if row['cond'] else 'nonexec' if row['cycle'] or (r['sid'], i) in found4 else
                   'noncomm' if row['noncomm'] else 'comm_in_bounds')
        x = dict(i=i, verdict=verdict, scope=scope(th, r['sch'], fz))
        if m is not None:
            x['stab'] = m.stab_implies(fz)[0]
            x['stabpin'] = m.stab_implies(fz, m.pin_closure(fz))[0]
        if verdict == 'comm_in_bounds' and not x.get('stabpin'):
            sat = SatSch(r['sch'])
            x['noncomm_found'] = None
            for N, kmax, Dm, kmin in NONCOMM_STAGES:
                w = ksat.search(sat, fz, N, kmax, Dm, 'noncomm', kmin=kmin)
                if w:
                    x['noncomm_found'] = [N, kmax, Dm]
                    x['noncomm_witness'] = w[1]
                    break
        out.append(x)
    return dict(sid=r['sid'], features=r['features'], rows=out, converted=m is not None)


if __name__ == '__main__':
    path, rpath, procs, outp = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
    found4 = {(x['sid'], x['i']) for x in json.load(open(rpath))['results'] if x['found']}
    recs = [r for r in map(json.loads, open(path)) if not r['in_K']]
    t0 = time.time()
    with Pool(procs) as pool:
        res = pool.map(job, [(r, {k for k in found4 if k[0] == r['sid']}) for r in recs], chunksize=1)
    S = dict(schemas=len(res), not_converted=sum(not r['converted'] for r in res), tasks=0, by_verdict={},
             stab_differs_from_condition=0, stabpin_true_on_nonexec=0, seconds=0)
    cond = {(r['sid'], i): row['cond'] for r in recs for i, row in enumerate(r['rows'])}
    examples = {}
    for r in res:
        named = r['sid'].startswith('named:')
        for x in r['rows']:
            S['tasks'] += 1
            v = S['by_verdict'].setdefault(x['verdict'], dict(tasks=0, named=0, stab=0, stabpin=0, noncomm_found=0))
            v['tasks'] += 1
            v['named'] += named
            v['stab'] += bool(x.get('stab'))
            v['stabpin'] += bool(x.get('stabpin'))
            v['noncomm_found'] += bool(x.get('noncomm_found'))
            S['stab_differs_from_condition'] += 'stab' in x and x['stab'] != cond[(r['sid'], x['i'])]
            S['stabpin_true_on_nonexec'] += x['verdict'] == 'nonexec' and bool(x.get('stabpin'))
            sc = x['scope']
            for name, inside in (('scope', not sc['key_via_ind'] and not sc['pullback']),
                                 ('scope_without_injective', not any(sc.values()))):
                if inside and not named:
                    v = S.setdefault(name, {})
                    v[x['verdict']] = v.get(x['verdict'], 0) + 1
                    v['injective'] = v.get('injective', 0) + (x['verdict'] in ('noncomm', 'comm_in_bounds') and sc['injective'])
            key = (x['verdict'], bool(x.get('stabpin')), bool(x.get('noncomm_found')))
            if x['verdict'] != 'exec' and key not in examples:
                examples[key] = (r['sid'], x['i'])
    S['seconds'] = time.time() - t0
    S['examples'] = {'/'.join(map(str, k)): v for k, v in examples.items()}
    json.dump(dict(S, results=res), open(outp, 'w'))
    print(json.dumps(S, indent=1))
