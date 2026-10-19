"""Theorem 4.9 (conditional INDs): bounded SAT check on random schemas of class C and on named families.

Class C: only FDs are primary keys, acyclic IND graph, every IND is R[P; f in A] <= S[L; g in B] with
L = PK(S) (a foreign key has no conditions), where
  (a) for every S, the right conditions of the CINDs into S on one attribute have nested sets,
      and at most one of their attributes is mutable;
  (i) a CIND without a left condition but with a right condition shares its pointer and referenced
      relation with no other CIND;
  (ii) the pointer of a CIND with a right condition does not lie inside the primary key of its relation.
(a) is the chain condition of class C, (i) and (ii) are the two restrictions of part (2) (Appendix A.3).
Predictions on C (Theorem 4.9):
  exec   every update (modification, and also mixed insert/delete/modify) is executable;
  canon  the canonical order (enable top-down, others, disable bottom-up) is legal;
  comm   every modification update is order-insensitive iff every CIND with a mutable right
         attribute g has P inside Fz and its left attribute (if any) in Fz.
Freshness does not change them.  Families outside C test each restriction and each combination
(unique keys, FDs, class K copies), with the prediction stated per family.
Usage: python3 -I cind_check.py <part> <n> <seed> <procs> <N> <Dv> <out.jsonl>
  part: random | named"""
import itertools
import json
import os
import random
import sys
import time
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cind_sat as CS


def ind(R, P, S, L, lf=None, rg=None, D=(), C=(), opt=False):
    d = {'R': R, 'P': list(P), 'S': S, 'L': list(L), 'D': list(D), 'C': list(C)}
    if lf:
        d['lf'] = [lf[0], sorted(lf[1])]
    if rg:
        d['rg'] = [rg[0], sorted(rg[1])]
    if opt:
        d['opt'] = True
    return d


def schema(rels, pk, inds, uk=None, fds=None, always_frozen=None):
    return {'rels': rels, 'pk': pk, 'inds': inds, 'uk': uk or {}, 'fds': fds or [],
            'always_frozen': always_frozen or {}}


# ------------------------------------------------------------------ predictions

def pred_comm_C(sch, Fz):
    for d in sch['inds']:
        if not d.get('rg') or d['rg'][0] in Fz[d['S']]:
            continue
        if not all(p in Fz[d['R']] for p in d['P']):
            return False
        if d.get('lf') and d['lf'][0] not in Fz[d['R']]:
            return False
    return True


def k_cond4(sch, Fz):
    """Condition (4) of Theorem 4.5 for the copy INDs (those with C)."""
    for d in sch['inds']:
        if not d['C']:
            continue
        if all(c in Fz[d['S']] for c in d['C']):
            continue
        if all(p in Fz[d['R']] for p in d['P']) and all(
                c in Fz[d['S']] or x in Fz[d['R']] for x, c in zip(d['D'], d['C'])):
            continue
        return False
    return True


def k_fresh_exec(sch, Fz):
    return all(c in Fz[d['S']] or x in Fz[d['R']]
               for d in sch['inds'] if d['C'] for x, c in zip(d['D'], d['C']))


def closure(X, fds):
    X = set(X)
    ch = True
    while ch:
        ch = False
        for (L, a) in fds:
            if set(L) <= X and a not in X:
                X.add(a)
                ch = True
    return X


def fd_cond4(sch, Fz, fresh=False):
    """Condition (4) of Theorem 3.6 per relation (Proposition 3.8 if fresh), with the primary key and
    unique keys as FDs."""
    for r, attrs in sch['rels'].items():
        fds = [(sch['pk'][r], a) for a in attrs]
        for U in sch['uk'].get(r, []):
            fds += [(U, a) for a in attrs]
        fds += [(X, a) for (rr, X, a) in sch['fds'] if rr == r]
        for n in range(0, len(attrs) + 1):
            for X in itertools.combinations(attrs, n):
                cx = closure(X, fds)
                sk = set(attrs) <= cx
                for a in cx - set(X):
                    if any(a in closure(Y, fds) for m in range(n) for Y in itertools.combinations(X, m)):
                        continue  # not elementary
                    if fresh:
                        if not sk and a not in Fz[r]:
                            return False
                    else:
                        if not set(X) <= Fz[r]:
                            return False
                        if not sk and a not in Fz[r]:
                            return False
    return True


# ------------------------------------------------------------------ random schemas of class C

def gen_C(seed, Dv=4):
    rng = random.Random(seed)
    n = rng.choice([2, 2, 3, 3, 3, 4])
    names = ['R%d' % i for i in range(n)]
    rels = {r: ['k', 'g'] for r in names}
    pk = {r: ['k'] for r in names}
    chains = {}
    for r in names:
        chains[r] = rng.choice([[[0]], [[0], [0, 1]], [[0, 1]]])
    hval = {r: rng.choice([0, 1]) for r in names}
    inds = []
    always = {}
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < 0.6:
                Ri, Rj = names[i], names[j]
                nptr = sum(1 for d in inds if d['R'] == Ri and d['S'] == Rj)
                same = [d for d in inds if d['R'] == Ri and d['S'] == Rj]
                if same and rng.random() < 0.5:
                    P = same[0]['P']
                else:
                    P = ['p%d%s' % (j, '' if nptr == 0 else chr(97 + nptr))]
                    rels[Ri] += P
                rg = None if rng.random() < 0.3 else ('g', rng.choice(chains[Rj]))
                u = rng.random()
                if u < 0.3:
                    lf = None
                elif u < 0.6:
                    if 'f' not in rels[Ri]:
                        rels[Ri].append('f')
                    lf = ('f', rng.choice([[0], [1], [0, 1]]))
                elif u < 0.85:
                    lf = ('g', rng.choice([[0], [1]]))
                else:
                    lf = (P[0], [rng.randrange(Dv - 1)])
                # restriction (i)
                if rg and lf is None and same:
                    lf = ('g', [0])
                if rg and any(d['P'] == P and d.get('rg') and not d.get('lf') for d in same):
                    rg = None
                inds.append(ind(Ri, P, Rj, ['k'], lf, rg))
            if rng.random() < 0.15:
                # a second, frozen condition attribute h on Rj
                Ri, Rj = names[i], names[j]
                if 'h' not in rels[Rj]:
                    rels[Rj].append('h')
                    always.setdefault(Rj, []).append('h')
                P = ['q%d' % j]
                if P[0] not in rels[Ri]:
                    rels[Ri] += P
                inds.append(ind(Ri, P, Rj, ['k'], ('g', [0]), ('h', [hval[Rj]])))
    return schema(rels, pk, inds, always_frozen=always)


def freezes(sch, rng, cap=24):
    free = [(r, a) for r in sch['rels'] for a in sch['rels'][r]
            if a not in sch['pk'][r] and a not in sch['always_frozen'].get(r, [])]
    out = []
    subsets = range(1 << len(free))
    if len(free) > 5:
        subsets = rng.sample(range(1 << len(free)), cap)
    for m in subsets:
        Fz = {r: set(sch['pk'][r]) | set(sch['always_frozen'].get(r, [])) for r in sch['rels']}
        for b, (r, a) in enumerate(free):
            if m >> b & 1:
                Fz[r].add(a)
        out.append(Fz)
    return out


# ------------------------------------------------------------------ named families outside C

def named():
    F = {}
    R2 = {'R': ['k', 'p', 'f1', 'f2'], 'S': ['k', 'g1', 'g2']}
    PK = {'R': ['k'], 'S': ['k']}
    F['B1_two_attrs_same_R'] = (schema(R2, PK, [
        ind('R', ['p'], 'S', ['k'], ('f1', [0]), ('g1', [0])),
        ind('R', ['p'], 'S', ['k'], ('f2', [0]), ('g2', [0]))]), 'two_attrs')
    F['B1_two_attrs_triangle'] = (schema(
        {'R1': ['k', 'p', 'f', 'h'], 'R2': ['k', 'p', 'q', 'f', 'f3'], 'S': ['k', 'g1', 'g2']},
        {'R1': ['k'], 'R2': ['k'], 'S': ['k']}, [
            ind('R1', ['p'], 'S', ['k'], ('f', [0]), ('g1', [0])),
            ind('R2', ['p'], 'S', ['k'], ('f', [0]), ('g2', [0])),
            ind('R2', ['q'], 'R1', ['k'], ('f3', [0]), ('h', [0]))]), 'two_attrs')
    F['B1_two_attrs_unlinked'] = (schema(
        {'R1': ['k', 'p', 'f'], 'R2': ['k', 'p', 'f'], 'S': ['k', 'g1', 'g2']},
        {'R1': ['k'], 'R2': ['k'], 'S': ['k']}, [
            ind('R1', ['p'], 'S', ['k'], ('f', [0]), ('g1', [0])),
            ind('R2', ['p'], 'S', ['k'], ('f', [0]), ('g2', [0]))]), 'two_attrs')
    F['B2_nonnested'] = (schema({'R': ['k', 'p', 'f'], 'S': ['k', 'g']}, PK, [
        ind('R', ['p'], 'S', ['k'], ('f', [0]), ('g', [0])),
        ind('R', ['p'], 'S', ['k'], ('f', [1]), ('g', [1]))]), 'nonnested')
    F['B2_nested'] = (schema({'R': ['k', 'p', 'f'], 'S': ['k', 'g']}, PK, [
        ind('R', ['p'], 'S', ['k'], ('f', [0]), ('g', [0])),
        ind('R', ['p'], 'S', ['k'], ('f', [1]), ('g', [0, 1]))]), 'C')
    F['B3_cyclic_self'] = (schema({'R': ['k', 'p', 'g']}, {'R': ['k']}, [
        ind('R', ['p'], 'R', ['k'], ('g', [0]), ('g', [0]))]), 'cyclic')
    F['B4_mutable_uk_target'] = (schema({'R': ['k', 'p', 'f'], 'S': ['k', 'u', 'g']}, PK, [
        ind('R', ['p'], 'S', ['u'], ('f', [0]), ('g', [0]))], uk={'S': [['u']]}), 'uk_target')
    F['B4_mutable_uk_target_fk'] = (schema({'R': ['k', 'p'], 'S': ['k', 'u']}, PK, [
        ind('R', ['p'], 'S', ['u'])], uk={'S': [['u']]}), 'uk_target')
    F['B6_mutable_uk_elsewhere'] = (schema({'R': ['k', 'p', 'f', 'e'], 'S': ['k', 'g']}, PK, [
        ind('R', ['p'], 'S', ['k'], ('f', [0]), ('g', [0]))], uk={'R': [['e']]}), 'uk_other')
    F['B7_K_plus_C_shared_ptr'] = (schema({'R': ['k', 'p', 'd', 'f'], 'S': ['k', 'c', 'g']}, PK, [
        ind('R', ['p'], 'S', ['k'], D=['d'], C=['c']),
        ind('R', ['p'], 'S', ['k'], ('f', [0]), ('g', [0]))]), 'K')
    F['B7_K_plus_C_two_ptrs'] = (schema({'R': ['k', 'p', 'd', 'q', 'f'], 'S': ['k', 'c', 'g']}, PK, [
        ind('R', ['p'], 'S', ['k'], D=['d'], C=['c']),
        ind('R', ['q'], 'S', ['k'], ('f', [0]), ('g', [0]))]), 'K')
    F['B7_K_plus_C_chain'] = (schema(
        {'R': ['k', 'p', 'f'], 'S': ['k', 'q', 'd', 'g'], 'T': ['k', 'c']},
        {'R': ['k'], 'S': ['k'], 'T': ['k']}, [
            ind('R', ['p'], 'S', ['k'], ('f', [0]), ('g', [0])),
            ind('S', ['q'], 'T', ['k'], D=['d'], C=['c'])]), 'K')
    F['B9_fd_on_R'] = (schema({'R': ['k', 'a', 'b', 'p', 'f'], 'S': ['k', 'g']}, PK, [
        ind('R', ['p'], 'S', ['k'], ('f', [0]), ('g', [0]))], fds=[('R', ['a'], 'b')]), 'FD')
    F['B9_fd_on_g'] = (schema({'R': ['k', 'p', 'f'], 'S': ['k', 'x', 'g']}, PK, [
        ind('R', ['p'], 'S', ['k'], ('f', [0]), ('g', [0]))], fds=[('S', ['x'], 'g')]), 'FD')
    F['B10_dominated'] = (schema({'R': ['k', 'p', 'f'], 'S': ['k', 'g']}, PK, [
        ind('R', ['p'], 'S', ['k'], None, ('g', [0])),
        ind('R', ['p'], 'S', ['k'], ('f', [1]), ('g', [0, 1]))]), 'restr_i')
    F['B11_pointer_in_pk'] = (schema(
        {'R': ['k', 'a', 'b', 'f'], 'M': ['k1', 'k2', 'h'], 'O': ['k', 'g']},
        {'R': ['k'], 'M': ['k1', 'k2'], 'O': ['k']}, [
            ind('R', ['a', 'b'], 'M', ['k1', 'k2'], ('f', [0]), ('h', [0])),
            ind('M', ['k1'], 'O', ['k'], ('h', [0]), ('g', [0])),
            ind('R', ['a'], 'O', ['k'], ('f', [0]), ('g', [0]))]), 'restr_ii')
    # real shapes
    F['T_sentry_repo'] = (schema({'Repository': ['k', 'integ', 'status'], 'Integration': ['k', 'status']},
                                 {'Repository': ['k'], 'Integration': ['k']}, [
        ind('Repository', ['integ'], 'Integration', ['k'], ('status', [0]), ('status', [0]))]), 'C')
    F['T_sentry_scim'] = (schema(
        {'Member': ['k', 'org', 'idp'], 'Team': ['k', 'org', 'idp'], 'AuthProvider': ['k', 'scim']},
        {'Member': ['k'], 'Team': ['k'], 'AuthProvider': ['k']}, [
            ind('Member', ['org'], 'AuthProvider', ['k'], ('idp', [1]), ('scim', [1])),
            ind('Team', ['org'], 'AuthProvider', ['k'], ('idp', [1]), ('scim', [1]))]), 'C')
    F['T_sentry_ssolinked'] = (schema({'Member': ['k', 'ptr', 'linked'], 'Identity': ['k', 'x']},
                                      {'Member': ['k'], 'Identity': ['k']}, [
        ind('Member', ['ptr'], 'Identity', ['k'], ('linked', [1]), None)]), 'C')
    F['T_gitlab_scim_denial'] = (schema({'Member': ['k', 'ptr', 'x'], 'ScimIdentity': ['k', 'active']},
                                        {'Member': ['k'], 'ScimIdentity': ['k']}, [
        ind('Member', ['ptr'], 'ScimIdentity', ['k'], None, ('active', [1]), opt=True)]), 'denial')
    F['T_running_archive'] = (schema(
        {'Project': ['k', 'gid', 'gpath', 'arch'], 'Group': ['k', 'path', 'arch']},
        {'Project': ['k'], 'Group': ['k']}, [
            ind('Project', ['gid'], 'Group', ['k'], ('arch', [0]), ('arch', [0]))]), 'C')
    return F


def predict_named(fam, sch, Fz, fresh):
    """(exec, comm) predictions; None = no prediction (boundary family, report what is found)."""
    if fam in ('C', 'denial', 'restr_i', 'restr_ii'):
        return True, pred_comm_C(sch, Fz)
    if fam == 'K':
        if fresh:
            # restriction: a K-IND violating (4) into a relation with a mutable condition attribute
            bad = any(d['C'] and not k_cond4({'inds': [d]}, Fz) and any(
                e.get('rg') and e['S'] == d['S'] and e['rg'][0] not in Fz[e['S']] for e in sch['inds'])
                for d in sch['inds'])
            return (None if bad else k_fresh_exec(sch, Fz)), (k_cond4(sch, Fz) and pred_comm_C(sch, Fz))
        ok = k_cond4(sch, Fz)
        return ok, ok and pred_comm_C(sch, Fz)
    if fam in ('FD', 'uk_other'):
        ok = fd_cond4(sch, Fz, fresh)
        return ok, ok and pred_comm_C(sch, Fz)
    return None, None


# ------------------------------------------------------------------ jobs

def job(args):
    tag, fam, sch, Fz, N, Dv, checks = args
    t = time.time()
    res = {'tag': tag, 'fam': fam, 'Fz': {r: sorted(v) for r, v in Fz.items()}}
    for (name, mode, kind, fresh) in checks:
        try:
            r = CS.search(sch, Fz, N, N, Dv, mode, kind, fresh, fresh_g=fresh)
        except ValueError as ex:
            r = 'n/a:%s' % ex
        res[name] = r
    if fam == 'C':
        pe, pc = True, pred_comm_C(sch, Fz)
        res['pred'] = {'mod_exec': pe, 'mod_comm': pc, 'mod_canon': True, 'mix_exec': True,
                       'mix_canon': True, 'fr_exec': True, 'fr_comm': pc}
    else:
        pe, pc = predict_named(fam, sch, Fz, False)
        fe, fc = predict_named(fam, sch, Fz, True)
        res['pred'] = {'mod_exec': pe, 'mod_comm': pc, 'fr_exec': fe, 'fr_comm': fc}
        if fam in ('C', 'denial'):
            res['pred'].update({'mod_canon': True, 'mix_exec': True, 'mix_canon': True})
    res['sec'] = round(time.time() - t, 2)
    return res


CHECKS_FULL = [('mod_exec', 'nonexec', 'mod', False), ('mod_comm', 'noncomm', 'mod', False),
               ('mod_canon', 'canon', 'mod', False), ('mix_exec', 'nonexec', 'mixed', False),
               ('mix_canon', 'canon', 'mixed', False), ('fr_exec', 'nonexec', 'mod', True),
               ('fr_comm', 'noncomm', 'mod', True)]
CHECKS_BASIC = [('mod_exec', 'nonexec', 'mod', False), ('mod_comm', 'noncomm', 'mod', False),
                ('fr_exec', 'nonexec', 'mod', True), ('fr_comm', 'noncomm', 'mod', True)]


def tasks(part, n, seed, N, Dv):
    rng = random.Random(seed)
    out = []
    if part == 'random':
        s = seed
        seen = set()
        while len(seen) < n:
            s += 1
            sch = gen_C(s, Dv)
            key = json.dumps(sch, sort_keys=True)
            if key in seen or not sch['inds']:
                continue
            seen.add(key)
            for Fz in freezes(sch, rng):
                out.append(('s%d' % s, 'C', sch, Fz, N, Dv, CHECKS_FULL))
    else:
        for name, (sch, fam) in named().items():
            checks = CHECKS_FULL if fam in ('C', 'denial') else CHECKS_BASIC
            if fam == 'cyclic':
                checks = [c for c in CHECKS_BASIC] + [('mix_exec', 'nonexec', 'mixed', False)]
            for Fz in freezes(sch, rng, cap=64):
                out.append((name, fam, sch, Fz, N, Dv, checks))
    return out


def main():
    part, n, seed, procs, N, Dv, path = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), \
        int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]), sys.argv[7]
    ts = tasks(part, n, seed, N, Dv)
    print('tasks', len(ts), flush=True)
    agg = {}
    with Pool(procs) as pool, open(path, 'w') as f:
        for res in pool.imap_unordered(job, ts, chunksize=2):
            f.write(json.dumps(res) + '\n')
            f.flush()
            for name, p in res['pred'].items():
                if name not in res:
                    continue
                found = res[name] is None
                key = (res['fam'] if part == 'named' else 'C', name, p, found)
                agg[key] = agg.get(key, 0) + 1
    for key in sorted(agg, key=str):
        fam, name, p, ok = key
        if p is None:
            tag = 'no prediction'
        elif p == ok:
            tag = 'agree'
        elif p and not ok:
            tag = 'COUNTEREXAMPLE'
        else:
            tag = 'UNCONFIRMED'
        print('%-10s %-10s predicted=%-5s within-bounds=%-5s : %5d  %s' % (fam, name, p, ok, agg[key], tag))


if __name__ == '__main__':
    main()
