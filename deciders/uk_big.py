"""Proposition A.8: re-search, with more entities, the pairs of uk_n5d5.jsonl for which the prediction
under Fz* (pinned) says 'not order-insensitive' but the search with 5 entities found no counterexample.
Usage: python3 -I uk_big.py <uk_n5d5.jsonl> <N> <D> <kmax>"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deciders as Dc
from selfcheck import schema as gen
import fsat
from fresh_k_uk import NAMED_UK, to_sch


def pinned(parsed, Fz, uk):
    """Fz closed under: if the sources at the frozen-copy positions of an IND, closed under the copy
    FDs of S (a pointer of S determines its copies), contain a unique key of S, its pointer is frozen"""
    M = {r: set(v) for r, v in Fz.items()}
    changed = True
    while changed:
        changed = False
        for (R, P, D, S, C) in parsed:
            X = {c for c, d in zip(C, D) if d in M[R]}
            grow = True
            while grow:
                grow = False
                for (R2, P2, D2, S2, C2) in parsed:
                    if R2 == S and set(P2) <= X and not set(D2) <= X:
                        X |= set(D2)
                        grow = True
            if any(U and set(U) <= X for U in uk.get(S, [])) and not set(P) <= M[R]:
                M[R] |= set(P)
                changed = True
    return M


def main():
    path, N, Dv, kmax = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    for line in open(path):
        r = json.loads(line)
        spec = NAMED_UK[r['name']][0] if r['name'] in NAMED_UK else gen('K', int(r['name'][1:]))
        sch, parsed = to_sch(spec, r['uk'])
        Fz = {k: set(v) for k, v in r['Fz'].items()}
        M = pinned(parsed, Fz, r['uk'])
        cm = all(all(c in M[S] for c in C) or (all(p in M[R] for p in P) and all(c in M[S] or d in M[R] for c, d in zip(C, D)))
                 for (R, P, D, S, C) in parsed)
        if cm or r['noncomm'] is not None:
            continue
        res = fsat.search(sch, Fz, N, kmax, Dv, 'noncomm', fresh=True)
        print(json.dumps({'name': r['name'], 'uk': r['uk'], 'Fz': r['Fz'], 'N': N, 'D': Dv, 'noncomm': res}), flush=True)


if __name__ == '__main__':
    main()
