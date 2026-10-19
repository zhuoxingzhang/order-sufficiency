"""Summarize cind_check.py results (Theorem 4.9): per family/name and check, agreement with the
prediction, the freezes with a counterexample, and the smallest counterexample per name.  Records of
cind_recheck.py among the inputs settle the checks that the first run left unconfirmed.
Needs no SAT solver.
Usage: python -I cind_analyze.py results/<file>.jsonl [...]
       python -I cind_analyze.py results/cind_random_n4.jsonl results/cind_random_n5.jsonl \\
           results/cind_recheck_random_n5.jsonl
       python -I cind_analyze.py results/cind_named_n5.jsonl results/cind_recheck_named_n6.jsonl"""
import json
import sys
from collections import defaultdict


def fz_str(fz, pk_like=('k', 'k1', 'k2')):
    parts = []
    for r, attrs in sorted(fz.items()):
        extra = [a for a in attrs if a not in pk_like]
        if extra:
            parts.append('%s.{%s}' % (r, ','.join(extra)))
    return ' '.join(parts) or '(keys only)'


def key(tag, fz, check):
    return (tag, json.dumps(fz, sort_keys=True), check)


def main():
    rows, rechecks = [], {}
    for p in sys.argv[1:]:
        with open(p, encoding='utf-8') as f:
            for l in f:
                if not l.strip():
                    continue
                r = json.loads(l)
                if 'check' in r:
                    k = key(r['tag'], r['Fz'], r['check'])
                    rechecks[k] = rechecks.get(k) or (r['N'] if r['found'] else None)
                else:
                    rows.append(r)
    agg = defaultdict(int)
    per = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    ex = {}
    bad = []
    for r in rows:
        for name, p in r['pred'].items():
            if name not in r:
                continue
            ok = r[name] is None
            rk = key(r['tag'], r['Fz'], name)
            if p is False and ok and rechecks.get(rk):
                ok = 'found at N=%d' % rechecks[rk]
            agg[(r['fam'], name, p, ok)] += 1
            per[r['tag'] if r['fam'] != 'C' or not r['tag'].startswith('s') else 'random-C'][name][0 if ok is True else 1] += 1
            if p is not None and p != (ok is True) and not isinstance(ok, str):
                bad.append((r['tag'], name, p, fz_str(r['Fz']), r[name],
                            'recheck: none' if rk in rechecks else ''))
            if ok is not True and isinstance(r[name], list):
                k = (r['tag'], name)
                size = len(r[name][1])
                if k not in ex or size < ex[k][0]:
                    ex[k] = (size, fz_str(r['Fz']), r[name])
    print('pairs (schema, freeze): %d, checks: %d' % (len(rows), sum(agg.values())))
    print('== agreement')
    for k in sorted(agg, key=str):
        fam, name, p, ok = k
        if isinstance(ok, str):
            tag, ok = 'agree (%s)' % ok, False
        else:
            tag = 'no prediction' if p is None else ('agree' if p == ok else (
                'COUNTEREXAMPLE' if p else 'UNCONFIRMED'))
        print('%-10s %-10s predicted=%-5s within-bounds=%-5s : %5d  %s' % (fam, name, p, ok, agg[k], tag))
    print('== per name: [no violation found, violation found]')
    for tag in sorted(per):
        print(tag, dict(per[tag]))
    print('== mismatches (%d)' % len(bad))
    for b in bad[:60]:
        print(b)
    print('== smallest violation per name and check')
    for k in sorted(ex):
        if k[0].startswith('s') and k[0][1:].isdigit():
            continue
        size, fz, v = ex[k]
        print(k, 'entities=%d' % size, 'Fz:', fz)
        print('    changed=%d' % v[0], v[1])


if __name__ == '__main__':
    main()
