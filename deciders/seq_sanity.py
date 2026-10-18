"""Sanity check of fsat mode 'seq': on pairs that are executable but not commutative under fresh
updates, the reverse of the topological order (referenced relations first) must fail within the
bounds, and on non-executable pairs the topological order itself must fail.
Usage: python3 -I seq_sanity.py <results.jsonl> <limit> <N> <D>"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from selfcheck import schema as gen_schema
import fresh_k
import fsat


def spec_of(name):
    return fresh_k.NAMED[name] if name in fresh_k.NAMED else gen_schema('K', int(name[1:]))


def main():
    path, limit, N, Dv = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    cnt = {'pinned reverse fails': [0, 0], 'nonexec topo fails': [0, 0]}
    for line in open(path):
        r = json.loads(line)
        if r['pred_exec'] and not r['pred_comm']:
            key, order = 'pinned reverse fails', list(reversed(r['topo']))
        elif not r['pred_exec']:
            key, order = 'nonexec topo fails', r['topo']
        else:
            continue
        if cnt[key][1] >= limit:
            continue
        sch, parsed = fresh_k.to_sch(spec_of(r['name']))
        Fz = {k: set(v) for k, v in r['Fz'].items()}
        found = fsat.search(sch, Fz, N, N, Dv, 'seq', fresh=True, order=order)
        cnt[key][0] += found is not None
        cnt[key][1] += 1
    for k, (a, b) in cnt.items():
        print('%s: %d of %d' % (k, a, b))


if __name__ == '__main__':
    main()
