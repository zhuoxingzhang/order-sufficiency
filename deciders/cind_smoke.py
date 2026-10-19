"""Smoke test of cind_check.py: a few named families with every attribute but the keys mutable.
Usage: python3 -I cind_smoke.py <name,...> [N] [R.a ...]"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cind_check as C
import cind_sat as S

F = C.named()
names = sys.argv[1].split(',')
N = int(sys.argv[2]) if len(sys.argv) > 2 else 4
for name in names:
    sch, fam = F[name]
    Fz = {r: set(sch['pk'][r]) for r in sch['rels']}
    for extra in sys.argv[3:]:
        r, a = extra.split('.')
        Fz[r].add(a)
    t = time.time()
    out = {}
    for tag, mode, kind, fresh in C.CHECKS_FULL:
        if mode == 'canon' and fam not in ('C', 'denial'):
            continue
        try:
            out[tag] = S.search(sch, Fz, N, N, 4, mode, kind, fresh, fresh)
        except ValueError as ex:
            out[tag] = str(ex)
    print('==', name, round(time.time() - t, 1), 's')
    for tag, v in out.items():
        print('  ', tag, v)
