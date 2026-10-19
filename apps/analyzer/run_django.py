"""Analyse one Django application: constraints, write sites, templates and their requirements.

Usage: python -I run_django.py <app_root> <out.json>
"""
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify as CL
import django_front as DF
import rails_constraints as RC
import rails_templates as RT


def analyse(app):
    t0 = time.time()
    prj = DF.Project(app)
    models = DF.scan_models(prj)
    sch, refs = DF.build_schema(models)
    sites, defs, calls = DF.scan(prj, models)
    pre = DF.mutability(sites, models, sch)
    cps = RC.copies(models, refs, sites, sch)
    der = RC.derived(models, refs, sites, sch)
    tb = RT.Builder(models, refs, sites, sch, defs, calls)
    tps = tb.templates()
    cps += RC.pushed(tps, refs, sch)
    kept, broken = CL.maintained(tps, [x for x in cps if x['evidence'] != 'triangle'])
    res = []
    for tp in tps:
        c = CL.classify(tp['writes'], [r for r in refs if r['ref']], kept, der, mutable=pre)
        if c is None:
            continue
        res.append(dict(kind=tp['kind'], origin=tp['origin'], strict=c['strict'], oneway=c['oneway'],
                        reasons=c['reasons'], tables=c['tables'],
                        deferred=sorted({w['table'] for w in tp['writes'] if w.get('deferred')}),
                        writes=[dict(table=w['table'], op=w['op'], attrs=sorted(w['attrs']),
                                     deferred=bool(w.get('deferred')), via=w.get('via')) for w in tp['writes']]))
    stats = dict(schema='models', files=len(prj.files), tables=len(sch.tables),
                 models=sum(1 for m in models.values() if not m.abstract), refs=len(refs),
                 copies=Counter(c['evidence'] for c in cps), copies_kept=len(kept), copies_broken=len(broken),
                 sites=len(sites),
                 resolved=sum(1 for s in sites if s['model']), calls=len(calls), templates=len(res),
                 by_kind=Counter(r['kind'] for r in res),
                 strict=Counter(r['strict'] for r in res), oneway=Counter(r['oneway'] for r in res),
                 sec=round(time.time() - t0, 1))
    return dict(stats=stats, templates=res, copies=cps, broken=broken, derived=der,
                mutable={t: sorted(v) for t, v in pre.items()})


if __name__ == '__main__':
    out = analyse(sys.argv[1])
    with open(sys.argv[2], 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
    print(json.dumps(out['stats'], default=str))
