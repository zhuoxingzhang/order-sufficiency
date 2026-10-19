"""Tally of the bounded SAT check of Proposition A.8 (fresh_k_uk.py, uk_big.py; Appendix A.3).

For every pair of a schema with unique keys and a freeze, the prediction under Fz* (Proposition A.8)
and under Fz (Proposition 4.7 as if there were no unique keys) is compared with the bounded search,
for executability (exec) and for order-insensitivity (comm).  A predicted 'no' without a
counterexample among 5 entities counts as confirmed if uk_big.py finds one among 7 entities.
Needs no SAT solver.
Usage: python -I uk_tally.py results/uk_n5d5.jsonl results/uk_big_n7.jsonl"""
import collections
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import pycosat  # noqa: F401
except ImportError:  # only the search needs it
    sys.modules['pycosat'] = types.ModuleType('pycosat')
from selfcheck import schema as gen  # noqa: E402
from fresh_k_uk import NAMED_UK, to_sch  # noqa: E402
from uk_big import pinned  # noqa: E402


def key(r):
    return (r['name'], json.dumps(r['uk'], sort_keys=True), json.dumps(r['Fz'], sort_keys=True))


rows = [json.loads(l) for l in open(sys.argv[1], encoding='utf-8') if l.strip()]
big = {key(b): b['noncomm'] for b in (json.loads(l) for l in open(sys.argv[2], encoding='utf-8') if l.strip())}

wrong = {'Fz*': collections.Counter(), 'Fz': collections.Counter()}
need7 = 0
for r in rows:
    spec = NAMED_UK[r['name']][0] if r['name'] in NAMED_UK else gen('K', int(r['name'][1:]))
    sch, parsed = to_sch(spec, r['uk'])
    M = pinned(parsed, {k: set(v) for k, v in r['Fz'].items()}, r['uk'], spec['pk'])
    ex = all(all(c in M[S] or d in M[R] for c, d in zip(C, D)) for (R, P, D, S, C) in parsed)
    cm = all(all(c in M[S] for c in C) or (all(p in M[R] for p in P) and all(c in M[S] or d in M[R] for c, d in zip(C, D)))
             for (R, P, D, S, C) in parsed)
    act_ex = r['nonexec'] is None
    act_cm = r['noncomm'] is None
    if act_cm and big.get(key(r)) is not None:
        act_cm = False
        need7 += 1
    for name, pe, pc in (('Fz*', ex, cm), ('Fz', r['pred_exec'], r['pred_comm'])):
        bad = [k for k, p, a in (('exec', pe, act_ex), ('comm', pc, act_cm)) if p != a]
        for k in bad:
            wrong[name][k] += 1
        wrong[name]['pairs'] += bool(bad)

print('pairs of a schema with unique keys and a freeze: %d (%d schemas)' % (len(rows), len({r['name'] for r in rows})))
print('pairs whose counterexample needs 7 entities: %d (of %d searched again)' % (need7, len(big)))
for name in ('Fz*', 'Fz'):
    w = wrong[name]
    print('predicted wrongly under %-3s: %d pairs (%d checks: %d exec, %d comm)'
          % (name, w['pairs'], w['exec'] + w['comm'], w['exec'], w['comm']))
