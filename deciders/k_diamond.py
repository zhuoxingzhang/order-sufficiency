"""Regression check for the final definition of class K: the diamond.

R(KR, A, B, D) with B, D frozen; S(K, C) with only K frozen; T(K1, K2, Dt) all frozen;
INDs R[A D] <= S[K C], R[A B] <= T[K1 K2], T[K1 Dt] <= S[K C].  The copy IND of T has a pointer
K1 inside PK(T), so the final definition rejects the schema; the earlier definition (no condition
on the pointer of a copy IND) admits it.  There condition (3) fails (the IND of R is unstable), yet
the exhaustive semantics finds no non-executable update: outside the final class the condition is
not exact.
Usage: python -I k_diamond.py"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deciders as D
from selfcheck import ref_schema
from ref_order2 import Sem

SCH = {'rels': {'R': ['KR', 'A', 'B', 'D'], 'S': ['K', 'C'], 'T': ['K1', 'K2', 'Dt']},
       'pk': {'R': ['KR'], 'S': ['K'], 'T': ['K1', 'K2']}, 'fds': [],
       'inds': [('R', ['A', 'D'], 'S', ['K', 'C']), ('R', ['A', 'B'], 'T', ['K1', 'K2']),
                ('T', ['K1', 'Dt'], 'S', ['K', 'C'])]}
FZ = {'R': {'KR', 'B', 'D'}, 'S': {'K'}, 'T': {'K1', 'K2', 'Dt'}}
TIERS = [(3, 2), (4, 2), (5, 2), (3, 3), (4, 3)]


def main():
    final = D.parse_K(SCH)
    earlier = D.parse_K(SCH, final=False)
    ok, bad = D.dec_K(SCH, FZ, earlier[2])
    out = {'final_definition_accepts': final[0], 'final_reason': final[1],
           'earlier_definition_accepts': earlier[0], 'condition_3_holds': ok, 'unstable': bad, 'search': []}
    for n, d in TIERS:
        r = Sem(ref_schema(SCH, d), FZ).search(n, stop_cycle=True, want_noncomm=False, budget=3_000_000)
        out['search'].append({'entities': n, 'domain': d, 'non_executable_found': bool(r['cycle']),
                              'complete': r['complete']})
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
