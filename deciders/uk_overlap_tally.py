"""Tally of the overlapping-pointer check of Proposition A.8 (uk_overlap.py, uk_overlap_big.py).

For 8,498 pairs of 144 schemas whose INDs out of one relation share pointer attributes, three
predictions are scored against the bounded search: 'fix' (Fz* as the least fixpoint of Appendix A.3,
now uk_big.pinned), 'paper' (every X_rho computed under Fz, without iteration) and 'code' (uk_big.pinned
before 2026-10-20, without the attributes of L).  A pair that a prediction rejects counts as confirmed
if the search finds a counterexample, with 5 entities (uk_overlap_n5d5.jsonl) or, for the pairs left
open there, with 8 entities and 3 changed ones (uk_overlap_big_n8.jsonl).  Needs no SAT solver.
Usage: python -I uk_overlap_tally.py results/uk_overlap_n5d5.jsonl results/uk_overlap_big_n8.jsonl"""
import json
import sys


def key(r):
    return (r['name'], json.dumps(r['uk'], sort_keys=True), json.dumps(r['Fz'], sort_keys=True))


rows = [json.loads(l) for l in open(sys.argv[1], encoding='utf-8') if l.strip()]
big = {key(b): b['noncomm'] for b in (json.loads(l) for l in open(sys.argv[2], encoding='utf-8') if l.strip())}
print('pairs %d, schemas %d, searched again with 8 entities %d'
      % (len(rows), len({r['name'] for r in rows}), len(big)))
for v in ('fix', 'paper', 'code'):
    rej = acc = rej_ok = acc_bad = 0
    for r in rows:
        pe, pc = r['pred'][v]
        found = r['noncomm'] is not None or big.get(key(r)) is not None
        exec_ok = (r['nonexec'] is None) == pe
        if pc:
            acc += 1
            acc_bad += found or not exec_ok
        else:
            rej += 1
            rej_ok += found and exec_ok
    print('%-5s rejects %d pairs, %d confirmed, %d wrongly; accepts %d, %d refuted'
          % (v, rej, rej_ok, rej - rej_ok, acc, acc_bad))
