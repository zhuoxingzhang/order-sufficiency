"""Random schemas at scale for the E3 timing curves, one generator per exact class.

Every generator takes (rng, nR relations, nA attributes per relation, nF declared FDs per relation,
nI INDs per relation) and returns a schema in the format of deciders.py together with a random
frozen set (PK plus each other attribute with probability 1/2).
  fd      FDs only; lhs of 1-3 attributes.
  unary   FDs plus unary INDs between random columns; 40% of the INDs start at a key column, which
          creates counting cycles.
  A       FDs plus foreign keys to the primary key of an earlier relation (acyclic, single key).
  K       PK-only FDs; INDs R[p D] <= S[k C] to earlier relations, |C| copies each (0-2);
          `layered` puts relations in two layers and only links across them, so the conflict graph
          of Theorem 5.1 is bipartite."""
import random


def _fds(rng, at, nF):
    out = []
    for _ in range(nF):
        L = rng.sample(at, min(len(at) - 1, rng.choice([1, 1, 2, 3])))
        a = rng.choice([x for x in at if x not in L])
        out.append((L, a))
    return out


def _fz(rng, sch):
    return {r: set(sch['pk'][r]) | {a for a in at if rng.random() < 0.5} for r, at in sch['rels'].items()}


def gen_fd(rng, nR, nA, nF, nI=0):
    rels, pk, fds = {}, {}, []
    for i in range(nR):
        r = 'R%d' % i
        rels[r] = ['a%d' % j for j in range(nA)]
        pk[r] = ['a0']
        fds += [(r, L, a) for L, a in _fds(rng, rels[r], nF)]
    sch = {'rels': rels, 'pk': pk, 'fds': fds, 'inds': []}
    return sch, _fz(rng, sch)


def gen_unary(rng, nR, nA, nF, nI):
    sch, _ = gen_fd(rng, nR, nA, nF)
    inds = set()
    names = list(sch['rels'])
    for R in names:
        for _ in range(nI):
            S = rng.choice(names)
            x = 'a0' if rng.random() < 0.4 else rng.choice(sch['rels'][R])
            y = rng.choice(sch['rels'][S])
            if (R, x) != (S, y):
                inds.add((R, x, S, y))
    sch['inds'] = [(R, [x], S, [y]) for R, x, S, y in sorted(inds)]
    return sch, _fz(rng, sch)


def gen_A(rng, nR, nA, nF, nI):
    rels, pk, fds, inds = {}, {}, [], []
    for i in range(nR):
        r = 'R%d' % i
        nfk = min(nI, i, nA - 2)
        fks = ['f%d' % j for j in range(nfk)]
        rels[r] = ['a0'] + fks + ['a%d' % j for j in range(1, nA - nfk)]
        pk[r] = ['a0']
        for f in fks:
            inds.append((r, [f], 'R%d' % rng.randrange(i), ['a0']))
        fds += [(r, L, a) for L, a in _fds(rng, rels[r], nF)]
    sch = {'rels': rels, 'pk': pk, 'fds': fds, 'inds': inds}
    return sch, _fz(rng, sch)


def gen_K(rng, nR, nA, nF=0, nI=2, layered=False):
    """nA = base attributes per relation (key and pointers come on top)"""
    rels, pk, inds = {}, {}, []
    base = {}
    for i in range(nR):
        r = 'R%d' % i
        rels[r] = ['k'] + ['b%d' % j for j in range(nA)]
        pk[r] = ['k']
        base[r] = list(rels[r][1:])
    for i in range(1, nR):
        R = 'R%d' % i
        if layered:
            cand = [j for j in range(i) if j % 2 != i % 2]
        else:
            cand = list(range(i))
        if not cand:
            continue
        for t, j in enumerate(sorted(set(rng.choice(cand) for _ in range(nI)))):
            S = 'R%d' % j
            p = 'p%d' % t
            rels[R].append(p)
            C = rng.sample(base[S], min(len(base[S]), rng.choice([0, 1, 1, 2])))
            D = []
            for c in C:
                d = 'd%d_%s' % (t, c)
                rels[R].append(d)
                D.append(d)
            inds.append((R, [p] + D, S, ['k'] + C))
        base[R] = [a for a in rels[R][1:] if not a.startswith('d')]
    sch = {'rels': rels, 'pk': pk, 'fds': [], 'inds': inds}
    return sch, _fz(rng, sch)


GEN = {'FD': gen_fd, 'U': gen_unary, 'A': gen_A, 'K': gen_K}
