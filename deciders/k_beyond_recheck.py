"""Recheck of the disagreements of k_beyond.py with a larger SAT domain.

Every task where G does not imply Sigma and k_beyond found no non-executable update (domain 3) is
searched again by ksat over 4 values: complete over 6 entities, then up to 9 entities with at most
4 changed, and 5 or 6 changed.  A non-executable update found here turns the task into an
agreement.  Usage (needs pycosat): python -I k_beyond_recheck.py <k_beyond.jsonl> <procs> <out.json>"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from multiprocessing import Pool
from k_beyond import SatSch

STAGES = [(6, 6, 4, 2), (9, 4, 4, 2), (9, 6, 4, 5)]


def job(task):
    import ksat
    sid, i, sch, fz = task
    sat = SatSch(sch)
    fz = {r: set(v) for r, v in fz.items()}
    t0 = time.time()
    for N, kmax, Dm, kmin in STAGES:
        w = ksat.search(sat, fz, N, kmax, Dm, 'nonexec', kmin=kmin)
        if w:
            return dict(sid=sid, i=i, found=[N, kmax, Dm], witness=w[1], seconds=time.time() - t0)
    return dict(sid=sid, i=i, found=None, seconds=time.time() - t0)


if __name__ == '__main__':
    path, procs, outp = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    tasks, meta = [], {}
    for line in open(path):
        r = json.loads(line)
        if r['in_K']:
            continue
        for i, row in enumerate(r['rows']):
            if not row['cond'] and not row['cycle']:
                tasks.append((r['sid'], i, r['sch'], row['fz']))
                meta[(r['sid'], i)] = dict(features=r['features'], flags=row.get('flags', {}),
                                          noncomm=bool(row['noncomm']), named=r['sid'].startswith('named:'))
    with Pool(procs) as pool:
        res = pool.map(job, tasks, chunksize=1)
    out = dict(disagreements=len(tasks), found_with_4_values=sum(bool(x['found']) for x in res), groups={})
    for x in res:
        m = meta[(x['sid'], x['i'])]
        f = m['flags']
        g = ('injective_pointer' if f.get('injective_pointer') else
             'pullback_shared' if f.get('pullback_shared') else
             'key_via_ind' if f.get('key_via_ind') else 'neither')
        s = out['groups'].setdefault(g, dict(tasks=0, found=0, remain=0, remain_noncomm=0))
        s['tasks'] += 1
        s['found'] += bool(x['found'])
        s['remain'] += not x['found']
        s['remain_noncomm'] += not x['found'] and m['noncomm']
    out['remaining_neither'] = [dict(sid=x['sid'], i=x['i'], noncomm=meta[(x['sid'], x['i'])]['noncomm'])
                                for x in res if not x['found'] and not any(meta[(x['sid'], x['i'])]['flags'].values())]
    out['found_neither'] = [dict(sid=x['sid'], i=x['i'], found=x['found'], witness=x['witness'])
                            for x in res if x['found'] and not any(meta[(x['sid'], x['i'])]['flags'].values())]
    out['seconds_max'] = max((x['seconds'] for x in res), default=0)
    json.dump(dict(out, results=res), open(outp, 'w'), indent=1)
    print(json.dumps({k: v for k, v in out.items() if k not in ('found_neither',)}, indent=1))
