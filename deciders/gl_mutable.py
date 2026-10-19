"""The columns that GitLab's code modifies, per table, from the static analyzer of apps/ (x4_shardk.py).

Runs the scanning part of apps/analyzer/run_gitlab.py (models, write sites) and dumps
RW.mutability(): 'precise' = attributes that resolved write sites modify, 'candidate' = attributes
that unresolved sites write by name only.  Inserts, deletes and saves of whole rows are not
modifications.  Every resolved modification site is listed by table, columns, file and line, method
and operation, so that initialisations can be filtered afterwards (gl_sites.py); no source text.
Usage: python -I gl_mutable.py <gitlab_root> <gitlab_schema.json> <out.json>
       python -I gl_mutable.py <gitlab> data/gitlab_schema.json data/gitlab_mutable.json"""
import json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'apps', 'analyzer'))
import rails_models as RM  # noqa: E402
import rails_writes as RW  # noqa: E402
import run_gitlab as RG  # noqa: E402

app, schema_json, out = sys.argv[1], sys.argv[2], sys.argv[3]
t0 = time.time()
sch, gs = RG.load_schema(schema_json)
models, concerns, prefix, bad = RM.scan(app)
RM.assign_tables(models, prefix, sch.tables)
sites, defs, calls = RW.scan_all(app, models)
pre, cand = RW.mutability(sites, models, sch)
# the sites behind every precise modification, so that the result can be checked by hand
where = {}
for s in sites:
    if s['kind'] in ('insert', 'delete', 'save') or not (s['model'] and s['model'] in models):
        continue
    t = models[s['model']].table
    for a in s['attrs']:
        col = a if (t in sch.tables and a in sch.tables[t]) else a + '_id'
        if t in pre and col in pre[t]:
            where.setdefault(f'{t}.{col}', []).append(f"{s['file']}:{s['line']}")
# every resolved modification site, so that initialisations can be filtered afterwards
msites = []
for s in sites:
    if s['kind'] in ('insert', 'delete', 'save') or not (s['model'] and s['model'] in models):
        continue
    t = models[s['model']].table
    cols = sorted({a if (t in sch.tables and a in sch.tables[t]) else a + '_id' for a in s['attrs']} &
                  (set(sch.tables.get(t, ())) | {'*'}))
    if cols:
        msites.append(dict(table=t, cols=cols, file=s['file'], line=s['line'], meth=s['meth'], op=s['op'],
                           kind=s['kind']))
json.dump(dict(precise={t: sorted(v) for t, v in pre.items()}, candidate={t: sorted(v) for t, v in cand.items()},
               where={k: v[:5] for k, v in where.items()}, sites=msites),
          open(out, 'w', encoding='utf-8'), indent=1)
print('tables with modified columns:', len(pre), 'secs', round(time.time() - t0, 1))
