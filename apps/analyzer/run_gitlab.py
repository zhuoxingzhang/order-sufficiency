"""Analyse GitLab and validate the analyser against the hand labels of its cross-database templates.

GitLab is a Rails application whose tables are assigned to databases and, within them, to
gitlab_schemas (db/docs, read into deciders/data/gitlab_schema.json together with the schema and the
loose foreign keys).  Three additions to the Rails pipeline:
  schema         columns, keys and foreign keys from gitlab_schema.json
  loose FKs      a reference whose child rows are deleted or nullified later by a worker
                 (async_delete, async_nullify, update_column_to): deferred writes of the parent's
                 destroy event
  exemptions     temporary_ignore_tables_in_transaction / allow_cross_database_modification_within_
                 transaction blocks delimit a transaction unit, like a transaction block
Requirements count only constraints between tables of different partitions.  Templates overall use
gitlab_schema partitions; an exemption point uses the partition the code states, its ignored tables
against the rest; a loose-FK parent uses databases.

Usage: python -I run_gitlab.py <gitlab_root> <gitlab_schema.json> <out.json>
"""
import json
import os
import re
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
import ruby
import schema as SC

LABELS = {      # hand labels of the 15 exemption points (corpus/gitlab_classify.py), by file and occurrence
    ('app/models/concerns/with_uploads.rb', 1): ('G01', 'order'),
    ('app/models/namespace/traversal_hierarchy.rb', 1): ('G02', 'order'),
    ('app/services/groups/create_service.rb', 1): ('G03', 'order'),
    ('app/services/members/creator_service.rb', 1): ('G04', 'order'),
    ('app/services/organizations/create_service.rb', 1): ('G05', 'order'),
    ('app/services/projects/create_service.rb', 1): ('G06', 'order'),
    ('app/uploaders/records_uploads.rb', 1): ('G07', 'order'),
    ('ee/app/models/ee/upload.rb', 1): ('G08', 'order'),
    ('ee/app/models/ee/upload.rb', 2): ('G09', 'order'),
    ('ee/app/services/security/orchestration/create_bot_service.rb', 1): ('G10', 'order'),
    ('ee/lib/gitlab/scim/group/deprovisioning_service.rb', 1): ('G11', 'order'),
    ('app/models/member.rb', 1): ('G12', 'atomic'),
    ('app/services/groups/transfer_service.rb', 1): ('G13', 'atomic'),
    ('app/services/projects/transfer_service.rb', 1): ('G14', 'atomic'),
    ('lib/api/notification_settings.rb', 1): ('G15', 'none'),
}
EXEMPT = re.compile(r'(temporary_ignore_tables_in_transaction|allow_cross_database_modification_within_transaction)\(')
LFK_DEP = {'async_delete': 'destroy_async', 'async_nullify': 'nullify_async', 'update_column_to': 'update_async'}


def load_schema(path):
    d = json.load(open(path, encoding='utf-8'))
    sch = SC.Schema()
    for t, v in d['structure']['tables'].items():
        sch.add_table(t, v['columns'], v['pk'] or ('id',))
        for u in v['uniques']:
            sch.add_unique(t, u)
    for f in d['structure']['fks']:
        sch.add_fk(f['child'], f['cols'], f['parent'], f['pcols'])
    return sch, d


def restrict(side, refs, copies, derived):
    """Constraints between tables of different partitions."""
    def cross(a, b):
        return side(a) is not None and side(b) is not None and side(a) != side(b)
    return ([r for r in refs if cross(r['table'], r['ref'])], [c for c in copies if cross(c['table'], c['ref'])],
            [x for x in derived if cross(x['table'], x['src'])])


def exemption_points(app):
    """(file, occurrence, line, enclosing def range, ignored tables) of every exemption block."""
    out = []
    for p in fsutil.walk(app, ('.rb',), fsutil.SKIP_DIRS):
        relp = fsutil.rel(p, app)
        if relp.startswith('lib/gitlab/database/'):
            continue
        src = fsutil.read(p)
        if not EXEMPT.search(src):
            continue
        lines = src.split('\n')
        root, _ = ruby.read(p)
        defs = [n for n in root.walk() if isinstance(n, ruby.Node) and n.kind == 'def']
        k = 0
        for i, ln in enumerate(lines, 1):
            if not EXEMPT.search(ln) or re.match(r'\s*def\s', ln):
                continue
            k += 1
            around = ' '.join(lines[i - 1:i + 3])
            mm = re.search(r'%w\[([^\]]*)\]', around)
            tables = mm.group(1).split() if mm else []
            enc = [d for d in defs if d.line <= i <= d.end]
            d = min(enc, key=lambda d: d.end - d.line) if enc else None
            txn = [n for n in root.walk() if isinstance(n, ruby.Node) and n.kind == 'block' and n.head is not None
                   and RW._TXN.search(n.head.code) and n.line <= i <= n.end and (not d or d.line <= n.line)]
            out.append(dict(file=relp, k=k, line=i, tables=tables, txn=str(min(n.line for n in txn)) if txn else None,
                            def_name=d.name if d else None, def_range=(d.line, d.end) if d else None))
    return out


def analyse(app, schema_json):
    t0 = time.time()
    sch, gs = load_schema(schema_json)
    part = {t: v['schema'] for t, v in gs['tables'].items() if v['db'] != 'shared'}
    db = {t: v['db'] for t, v in gs['tables'].items() if v['db'] != 'shared'}
    models, concerns, prefix, bad = RM.scan(app)
    RM.assign_tables(models, prefix, sch.tables)
    refs = RM.references(models)
    for f in sch.fks:
        if not any(r['table'] == f['table'] and r['cols'] == f['cols'] and r['ref'] == f['ref'] for r in refs):
            refs.append(dict(table=f['table'], cols=f['cols'], ref=f['ref'], ref_cols=f['ref_cols'],
                             model=None, target=None, assoc=None, poly=False, kind='db_fk', opts={},
                             line=0, dependent=None))
    model_of = {}
    for n, m in sorted(models.items()):
        if not m.abstract and m.table and not m.sti_base:
            model_of.setdefault(m.table, n)
    for n, m in sorted(models.items()):
        if not m.abstract and m.table:
            model_of.setdefault(m.table, n)
    for f in gs['lfk']:
        refs.append(dict(table=f['child'], cols=(f['column'],), ref=f['table'], ref_cols=('id',),
                         model=model_of.get(f['child']), target=model_of.get(f['table']), assoc=None, poly=False,
                         kind='lfk', opts={}, line=0, dependent=LFK_DEP[f['on_delete']]))
    sites, defs, calls = RW.scan_all(app, models)
    pre, _ = RW.mutability(sites, models, sch)
    cps = RC.copies(models, refs, sites, sch)
    der = RC.derived(models, refs, sites, sch)
    tb = RT.Builder(models, refs, sites, sch, defs, calls)
    tps = tb.templates()
    cps += RC.pushed(tps, refs, sch)
    kept, broken = CL.maintained(tps, [x for x in cps if x['evidence'] != 'triangle'])
    live = [r for r in refs if r['ref']]
    restricted = {}

    def run(tp, side):
        if side not in restricted:
            restricted[side] = restrict(side, live, kept, der)
        rr, cc, dd = restricted[side]
        return CL.classify(tp['writes'], rr, cc, dd, mutable=pre)

    res = []
    for tp in tps:
        if len({part.get(w['table']) for w in tp['writes']} - {None}) < 2:
            continue
        c = run(tp, part.get)
        if c is None:
            continue
        res.append(dict(kind=tp['kind'], origin=tp['origin'], strict=c['strict'], oneway=c['oneway'],
                        reasons=c['reasons'], tables=c['tables'],
                        writes=[dict(table=w['table'], op=w['op'], attrs=sorted(w['attrs']),
                                     deferred=bool(w.get('deferred')), via=w.get('via')) for w in tp['writes']]))

    # ---------------------------------------------------------------- exemption points
    def site_line(w):
        mm = re.match(r'^(.+):(\d+)$', w.get('via') or '')
        return (mm.group(1), int(mm.group(2))) if mm else None

    by_origin = {tp['origin']: tp for tp in tps}
    gl = []
    for pt in exemption_points(app):
        tid, label = LABELS.get((pt['file'], pt['k']), (None, None))
        # the unit of the exemption: its transaction block, or the method around it
        matched = [tp for tp in tps if tp['file'] == pt['file'] and
                   ((tp['kind'] == 'transaction' and tp.get('where') == pt['txn']) or
                    (tp['kind'] == 'method' and pt['def_name'] and
                     (tp.get('where') or '').split('#')[-1] == pt['def_name']))]
        if pt['def_range']:
            a, b = pt['def_range']
            for tp in tps:
                if any((site_line(w) or ('', 0))[0] == pt['file'] and a <= site_line(w)[1] <= b
                       for w in tp['writes']):
                    matched.append(tp)
            # an exemption inside a callback method: the events of the models that run it
            for n, m in models.items():
                for events, _, f, ca, cb, kind in RC.callback_ranges(m):
                    if f == pt['file'] and ca <= pt['line'] <= cb:
                        for ev in events:
                            if ev in ('create', 'update', 'destroy') and '%s#%s' % (n, ev) in by_origin:
                                matched.append(by_origin['%s#%s' % (n, ev)])
        seen, uniq = set(), []
        for tp in matched:
            if tp['origin'] not in seen:
                seen.add(tp['origin'])
                uniq.append(tp)
        ign = set(pt['tables'])
        side = (lambda t, ign=ign: 'ignored' if t in ign else 'rest') if ign else part.get
        labs = []
        for tp in uniq:
            c = run(tp, side)
            if c is None:
                continue
            if ign and not ({w['table'] for w in tp['writes']} & ign):
                continue
            labs.append(dict(origin=tp['origin'], kind=tp['kind'], strict=c['strict'], oneway=c['oneway'],
                             reasons=c['reasons'][:8], tables=c['tables']))
        best = {}
        for reading in ('strict', 'oneway'):
            vals = [x[reading] for x in labs]
            best[reading] = max(vals, key=CL.RANK.get) if vals else 'missed'
        gl.append(dict(id=tid, label=label, file=pt['file'], line=pt['line'], tables=pt['tables'],
                       strict=best['strict'], oneway=best['oneway'], templates=labs))

    # ---------------------------------------------------------------- loose-FK parents across databases
    lfk = []
    parents = sorted({f['table'] for f in gs['lfk']
                      if f['child'] in db and f['table'] in db and db[f['child']] != db[f['table']]})
    for p in parents:
        names = [n for n, m in models.items() if m.table == p and not m.abstract]
        labs = []
        for n in names:
            tp = by_origin.get('%s#destroy' % n)
            if tp is None:
                continue
            c = run(tp, db.get)
            if c is not None:
                labs.append(dict(origin=tp['origin'], strict=c['strict'], oneway=c['oneway'],
                                 reasons=c['reasons'][:6]))
        best = {}
        for reading in ('strict', 'oneway'):
            vals = [x[reading] for x in labs]
            best[reading] = max(vals, key=CL.RANK.get) if vals else 'missed'
        lfk.append(dict(parent=p, label='order', strict=best['strict'], oneway=best['oneway'], templates=labs))

    stats = dict(schema='gitlab_schema.json', tables=len(sch.tables), models=len(models), unbalanced=len(bad),
                 refs=len(refs), lfk=len(gs['lfk']), copies=Counter(c['evidence'] for c in cps),
                 copies_kept=len(kept), copies_broken=len(broken), sites=len(sites),
                 resolved=sum(1 for s in sites if s['model']), calls=len(calls), templates=len(res),
                 by_kind=Counter(r['kind'] for r in res),
                 strict=Counter(r['strict'] for r in res), oneway=Counter(r['oneway'] for r in res),
                 sec=round(time.time() - t0, 1))
    if os.environ.get('OSC_DUMP'):
        with open(os.environ['OSC_DUMP'], 'w', encoding='utf-8') as f:
            json.dump(dict(templates=tps, points=exemption_points(app), kept=kept,
                           refs=[r for r in refs if r['ref']], derived=der), f,
                      default=lambda o: sorted(o) if isinstance(o, set) else str(o))
    return dict(stats=stats, exemptions=gl, lfk_parents=lfk, templates=res, copies=cps, broken=broken)


if __name__ == '__main__':
    out = analyse(sys.argv[1], sys.argv[2])
    with open(sys.argv[3], 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
    print(json.dumps(out['stats'], default=str))
    for g in out['exemptions']:
        print(g['id'], g['label'], g['strict'], g['oneway'], g['file'], g['line'], len(g['templates']))
    print('lfk', Counter((x['strict'], x['oneway']) for x in out['lfk_parents']))
