"""Analyse one Rails application: constraints, write sites, templates and their requirements.

Usage: python -I run_rails.py <app_root> <out.json> [schema.rb|structure.sql|migrate]
"""
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify as CL
import fsutil
import rails_constraints as RC
import rails_models as RM
import rails_templates as RT
import rails_writes as RW
import schema as SC


def load_schema(app, how=None):
    rb, sql = os.path.join(app, 'db', 'schema.rb'), os.path.join(app, 'db', 'structure.sql')
    if how in (None, 'schema.rb') and os.path.exists(fsutil.lp(rb)):
        return SC.parse_schema_rb(fsutil.lp(rb)), 'schema.rb'
    if how in (None, 'structure.sql') and os.path.exists(fsutil.lp(sql)):
        return SC.parse_structure_sql(fsutil.lp(sql)), 'structure.sql'
    dirs = []
    for d, ds, _ in os.walk(fsutil.lp(app)):
        ds[:] = [x for x in ds if x not in fsutil.SKIP_DIRS]
        if d.replace('\\', '/').endswith('db/migrate'):
            dirs.append(d)
    return SC.replay_migrations(dirs), 'migrations(%d dirs)' % len(dirs)


def analyse(app, how=None):
    t0 = time.time()
    sch, src = load_schema(app, how)
    models, concerns, prefix, bad = RM.scan(app)
    RM.assign_tables(models, prefix, sch.tables)
    refs = RM.references(models)
    for f in sch.fks:
        if not any(r['table'] == f['table'] and r['cols'] == f['cols'] and r['ref'] == f['ref'] for r in refs):
            refs.append(dict(table=f['table'], cols=f['cols'], ref=f['ref'], ref_cols=f['ref_cols'],
                             model=None, target=None, assoc=None, poly=False, kind='db_fk', opts={},
                             line=0, dependent=None))
    sites, defs, calls = RW.scan_all(app, models)
    pre, cand = RW.mutability(sites, models, sch)
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
    stats = dict(schema=src, tables=len(sch.tables), models=len(models), unbalanced=len(bad),
                 refs=len(refs), poly=sum(r['poly'] for r in refs), copies=Counter(c['evidence'] for c in cps),
                 copies_kept=len(kept), copies_broken=len(broken),
                 derived=Counter(d['kind'] for d in der), sites=len(sites),
                 resolved=sum(1 for s in sites if s['model']), calls=len(calls), templates=len(res),
                 by_kind=Counter(r['kind'] for r in res),
                 strict=Counter(r['strict'] for r in res), oneway=Counter(r['oneway'] for r in res),
                 sec=round(time.time() - t0, 1))
    return dict(stats=stats, templates=res, copies=cps, broken=broken, derived=der,
                mutable={t: sorted(v) for t, v in pre.items()})


if __name__ == '__main__':
    out = analyse(sys.argv[1], sys.argv[3] if len(sys.argv) > 3 else None)
    with open(sys.argv[2], 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
    print(json.dumps(out['stats'], default=str))
