"""Re-run, with more entities, every check whose prediction the first run did not confirm.
Usage: python3 -I cind_recheck.py <in.jsonl> <N> <Dv> <procs> <out.jsonl>"""
import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cind_check as C
import cind_sat as S

MODES = {name: (mode, kind, fresh) for (name, mode, kind, fresh) in C.CHECKS_FULL}


def job(args):
    tag, fam, Fz, name, N, Dv = args
    if fam == 'C' and tag.startswith('s') and tag[1:].isdigit():
        sch = C.gen_C(int(tag[1:]), 4)
    else:
        sch = C.named()[tag][0]
    Fz = {r: set(v) for r, v in Fz.items()}
    mode, kind, fresh = MODES[name]
    r = S.search(sch, Fz, N, N, Dv, mode, kind, fresh, fresh_g=fresh)
    return {'tag': tag, 'fam': fam, 'Fz': {k: sorted(v) for k, v in Fz.items()}, 'check': name,
            'N': N, 'found': r}


def main():
    src, N, Dv, procs, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
    todo = []
    with open(src, encoding='utf-8') as f:
        for l in f:
            r = json.loads(l)
            for name, p in r['pred'].items():
                if name in r and p is not None and p != (r[name] is None):
                    todo.append((r['tag'], r['fam'], r['Fz'], name, N, Dv))
    print('rechecks', len(todo), flush=True)
    with Pool(procs) as pool, open(out, 'w') as f:
        for res in pool.imap_unordered(job, todo):
            f.write(json.dumps(res) + '\n')
            f.flush()
            print(res['tag'], res['check'], 'N=%d' % N, 'found' if res['found'] else 'none',
                  res['found'][0] if res['found'] else '', flush=True)


if __name__ == '__main__':
    main()
