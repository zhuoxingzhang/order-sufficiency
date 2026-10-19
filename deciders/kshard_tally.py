"""Tally of the bounded SAT check of Theorem 5.3 (kshard.py, kshard_recheck.py; Appendix A.4).

For arbitrary modification updates: the predicted executable combinations without a counterexample,
the predicted non-executable ones with a counterexample in the first run or in a recheck, and the
largest number of changed entities in a counterexample.  For fresh updates: the agreement of the
necessary condition (every IND with a mutable copy of a mutable source carries the shard key).
Needs no SAT solver.
Usage: python -I kshard_tally.py <kshard.jsonl,...> <recheck.jsonl,...> [<fresh recheck.jsonl,...>]
       python -I kshard_tally.py results/kshard_n4d4.jsonl,results/kshard_n5d5.jsonl \\
           results/kshard_recheck_n4d4.jsonl,results/kshard_recheck_n5d5.jsonl,results/kshard_recheck2_n5d5.jsonl \\
           results/kshard_recheck_n4d4_fresh.jsonl"""
import collections, json, sys


def load(paths):
    return [json.loads(l) for p in paths.split(',') if p for l in open(p, encoding='utf-8') if l.strip()]


def key(r):
    return (r['name'], json.dumps(r['Fz'], sort_keys=True), json.dumps(r['SK'], sort_keys=True),
            json.dumps(r['grp'], sort_keys=True))


main = load(sys.argv[1])
found = {}
for r in load(sys.argv[2]):
    if r['recheck']['cex']:
        found[key(r)] = r['recheck']['cex'][0]
ffound = {}
for r in load(sys.argv[3]) if len(sys.argv) > 3 else []:
    if r['recheck']['cex']:
        ffound[key(r)] = r['recheck']['cex'][0]

print('combinations %d of %d random schemas of class K' % (len(main), len({r['name'] for r in main})))
for kind, pk, ck, extra in (('arbitrary', 'pred_arb', 'cex_arb', found), ('fresh', 'pred_fresh', 'cex_fresh', ffound)):
    c = collections.Counter()
    changed = collections.Counter()
    for r in main:
        if r[pk]:
            c['predicted executable, no counterexample' if r[ck] is None else 'predicted executable, COUNTEREXAMPLE'] += 1
        elif r[ck] is not None:
            c['predicted not executable, counterexample'] += 1
            changed[r[ck][0]] += 1
        elif key(r) in extra:
            c['predicted not executable, counterexample in a recheck'] += 1
            changed[extra[key(r)]] += 1
        else:
            c['predicted not executable, UNCONFIRMED'] += 1
    print('==', kind)
    for k, v in sorted(c.items()):
        print('   %-55s %6d' % (k, v))
    print('   changed entities in the counterexamples:', dict(sorted(changed.items())))
s = collections.Counter(r['sanity'] for r in main if 'sanity' in r)
print('sanity (one shard per entity agrees with ksat.py):', dict(s))
