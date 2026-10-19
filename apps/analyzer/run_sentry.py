"""Analyse Sentry and validate the analyser against the hand labels of its cross-silo templates.

Sentry is a Django application whose models live in a control silo or in cells; the silos exchange
data only through remote calls and outboxes.  Additions to the Django pipeline:
  silos        a model decorated with control_silo_model or cell_silo_model lies in that silo
  references   HybridCloudForeignKey columns reference a model of the other silo, and their
               cascades run later from tombstones (deferred); an integer column x_id references the
               model x of the other silo by convention (the mapping tables)
  RPC          a call <service>.<method>(...) of a variable created by create_delegation() runs the
               method of the database-backed implementation (a synchronous remote write)
  outboxes     saving or deleting a model with handle_async_replication / handle_async_deletion, or
               with an outbox category that has receivers, runs them after commit (deferred); so does
               code that creates an outbox of a category explicitly
Requirements count only constraints between tables of different silos.

Usage: python -I run_sentry.py <sentry checkout> <sentry_templates.json> <out.json>
"""
import ast
import json
import os
import re
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import classify as CL
import django_front as DF
import rails_constraints as RC
import rails_templates as RT

SILO = re.compile(r'(cell|region|control)_silo_model')
OUTBOX_MAKERS = {'as_cell_outbox', 'as_cell_outboxes', 'as_control_outboxes', 'as_control_outbox', 'CellOutbox',
                 'ControlOutbox', 'RegionOutbox'}
INT_KINDS = re.compile(r'Integer|BigInt')
PREFIX = 'src/sentry/'


def silos(models):
    out = {}
    for m in models.values():
        for d in getattr(m.node, 'decorator_list', []):
            mm = SILO.search(DF.dotted(d.func if isinstance(d, ast.Call) else d) or '')
            if mm and m.table:
                out[m.table] = 'control' if mm.group(1) == 'control' else 'cell'
    return out


def extra_refs(models, silo):
    """HybridCloudForeignKey references and conventional x_id references across silos."""
    by_lower = {}
    for n, m in models.items():
        if not m.abstract and m.table in silo:
            by_lower.setdefault(m.key[1].lower(), n)
    out = []
    for n, m in models.items():
        if m.table not in silo or m.abstract:
            continue
        for f in m.fields.values():
            col = f.get('column') or ''
            if f['kind'] == 'HybridCloudForeignKey':
                tgt = DF.resolve_target(models, m, f.get('target'))
                if tgt and models[tgt].table:
                    dep = {'CASCADE': 'destroy_async', 'SET_NULL': 'nullify_async'}.get(f.get('on_delete'))
                    out.append(dict(table=m.table, cols=(col,), ref=models[tgt].table, ref_cols=('id',), model=n,
                                    target=tgt, assoc=None, poly=False, kind='hcfk', opts={}, line=f['line'],
                                    dependent=dep))
            elif INT_KINDS.search(f['kind']) and col.endswith('_id'):
                tgt = by_lower.get(col[:-3].replace('_', ''))
                if tgt and silo.get(models[tgt].table) not in (None, silo[m.table]):
                    out.append(dict(table=m.table, cols=(col,), ref=models[tgt].table, ref_cols=('id',), model=n,
                                    target=tgt, assoc=None, poly=False, kind='conventional', opts={},
                                    line=f['line'], dependent=None))
    return out


def rpc_resolver(project):
    var2svc, impls = {}, {}
    for relp, tree in project.files.items():
        for n in tree.body:
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and \
                    isinstance(n.value.func, ast.Attribute) and n.value.func.attr == 'create_delegation':
                svc = (DF.dotted(n.value.func.value) or '').split('.')[-1]
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        var2svc[t.id] = svc
            elif isinstance(n, ast.ClassDef):
                for b in n.bases:
                    impls.setdefault((DF.dotted(b) or '').split('.')[-1], []).append(n.name)

    def resolve(c, name, defs):
        parts = name.split('.')
        if len(parts) != 2 or parts[0] not in var2svc:
            return None
        cands = sorted(impls.get(var2svc[parts[0]], []), key=lambda x: (not x.startswith('DatabaseBacked'), x))
        for impl in cands:
            if (impl, parts[1]) in defs:
                return (impl, parts[1])
        return None
    return resolve, var2svc


class _Outboxes(ast.NodeVisitor):
    """Explicit outbox creations of a function: (line, category, txn)."""

    def __init__(self):
        self.txn, self.found = [], []

    def visit_With(self, node):
        atomic = any('atomic' in (DF.dotted(i.context_expr.func if isinstance(i.context_expr, ast.Call)
                                            else i.context_expr) or '') for i in node.items)
        if atomic:
            self.txn.append('%d' % node.lineno)
        self.generic_visit(node)
        if atomic:
            self.txn.pop()

    def visit_FunctionDef(self, node):
        return

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        self.generic_visit(node)
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else '')
        if name not in OUTBOX_MAKERS:
            return
        cat = None
        if isinstance(f, ast.Attribute):
            d = DF.dotted(f.value) or ''
            if d.startswith('OutboxCategory.'):
                cat = d.split('.')[1]
        k = DF.kwarg(node, 'category')
        if k is not None and (DF.dotted(k) or '').startswith('OutboxCategory.'):
            cat = DF.dotted(k).split('.')[1]
        if cat:
            self.found.append((node.lineno, cat, self.txn[-1] if self.txn else None))


def outboxes(project, models, defs, calls):
    """Callbacks and calls for outbox processing (deferred)."""
    receivers = {}
    for relp, tree in project.files.items():
        mod = DF.module_of(relp)
        for n in tree.body:
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in n.decorator_list:
                if isinstance(d, ast.Call) and (DF.dotted(d.func) or '').endswith('receiver') and d.args and \
                        'outbox' in (DF.dotted(d.args[0]) or ''):
                    s = DF.dotted(DF.kwarg(d, 'sender')) or ''
                    if s.startswith('OutboxCategory.'):
                        receivers.setdefault(s.split('.')[1], []).append((mod, n.name))
    category = {}
    for n, m in models.items():
        for st in m.node.body:
            if isinstance(st, (ast.Assign, ast.AnnAssign)):
                tgt = st.targets[0] if isinstance(st, ast.Assign) else st.target
                if isinstance(tgt, ast.Name) and tgt.id == 'category' and st.value is not None and \
                        (DF.dotted(st.value) or '').startswith('OutboxCategory.'):
                    category[n] = DF.dotted(st.value).split('.')[1]
    handlers = {}
    for n, m in models.items():
        for meth, events in (('handle_async_replication', ['create', 'update']), ('handle_async_deletion', ['destroy'])):
            if meth in m.methods and m.method_file.get(meth):
                sp = m.methods[meth]
                m.callbacks.append({'kind': 'after_commit', 'target': None, 'node': DF.Span(sp.line, sp.end),
                                    'opts': {'on': events}, 'file': m.method_file[meth], 'line': sp.line})
                handlers.setdefault(category.get(n), []).append((m.key[1], meth))
        for key in receivers.get(category.get(n), []):
            if key in defs:
                f, a, b = defs[key]
                m.callbacks.append({'kind': 'after_commit', 'target': None, 'node': DF.Span(a, b),
                                    'opts': {'on': ['create', 'update', 'destroy']}, 'file': f, 'line': a})
    added = 0
    for relp, tree in project.files.items():
        mod = DF.module_of(relp)
        units = [(None, n) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for n in tree.body:
            if isinstance(n, ast.ClassDef):
                units += [(n.name, st) for st in n.body if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for cname, fn in units:
            v = _Outboxes()
            for st in fn.body:
                v.visit(st)
            for line, cat, txn in v.found:
                for key in receivers.get(cat, []) + [k for k in handlers.get(cat, []) if k in defs]:
                    if key in defs:
                        calls.append(dict(file=relp, line=line, cls=cname if cname else mod, meth=fn.name, txn=txn,
                                          target=key, async_=True))
                        added += 1
    return dict(receivers=sum(len(v) for v in receivers.values()), categories=len(category), outbox_calls=added)


def enclosing_def(tree, line):
    best = None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.lineno <= line <= (n.end_lineno or n.lineno):
            if best is None or n.lineno >= best.lineno:
                best = n
    return best


def analyse(root, labels_path):
    t0 = time.time()
    prj = DF.Project(root)
    models = DF.scan_models(prj)
    sch, refs = DF.build_schema(models)
    silo = silos(models)
    refs += extra_refs(models, silo)
    resolve, var2svc = rpc_resolver(prj)
    sites, defs, calls = DF.scan(prj, models, resolve=resolve)
    pre = DF.mutability(sites, models, sch)
    ob = outboxes(prj, models, defs, calls)
    cps = RC.copies(models, refs, sites, sch)
    der = RC.derived(models, refs, sites, sch)
    tb = RT.Builder(models, refs, sites, sch, defs, calls)
    tps = tb.templates()
    cps += RC.pushed(tps, refs, sch)
    kept, broken = CL.maintained(tps, [x for x in cps if x['evidence'] != 'triangle'])

    def cross(a, b):
        return silo.get(a) and silo.get(b) and silo[a] != silo[b]
    rr = [r for r in refs if r['ref'] and cross(r['table'], r['ref'])]
    cc = [c for c in kept if cross(c['table'], c['ref'])]
    dd = [x for x in der if cross(x['table'], x['src'])]

    res, by_origin = [], {}
    for tp in tps:
        if len({silo.get(w['table']) for w in tp['writes']} - {None}) < 2:
            continue
        c = CL.classify(tp['writes'], rr, cc, dd, mutable=pre)
        if c is None:
            continue
        r = dict(kind=tp['kind'], origin=tp['origin'], file=tp['file'], where=tp.get('where'), strict=c['strict'],
                 oneway=c['oneway'], reasons=c['reasons'], tables=c['tables'],
                 writes=[dict(table=w['table'], op=w['op'], attrs=sorted(w['attrs']), deferred=bool(w.get('deferred')),
                              via=w.get('via')) for w in tp['writes']])
        res.append(r)
        by_origin[tp['origin']] = r

    # ---------------------------------------------------------------- the hand-labelled templates
    def site_line(w):
        mm = re.match(r'^(.+):(\d+)$', w.get('via') or '')
        return (mm.group(1), int(mm.group(2))) if mm else ('', 0)

    full = {tp['origin']: tp for tp in tps}
    val = []
    for t in json.load(open(labels_path, encoding='utf-8'))['templates']:
        # the analyser's templates of a hand template: the units at its code locations and, for a
        # location in a model callback, the event of the hand template's operation; only if no
        # location has one, the templates that run code at a location (callers of a helper)
        evs = {'insert': ['create'], 'delete': ['destroy'], 'modify': ['update']}.get(t['op'], ['create', 'destroy'])
        primary, fallback = {}, {}
        for loc in t['locs']:
            mm = re.match(r'^([\w/.]+\.py):(\d+)', loc)
            if not mm:
                continue
            f, line = PREFIX + mm.group(1), int(mm.group(2))
            tree = prj.files.get(f)
            fn = enclosing_def(tree, line) if tree else None
            a, b = (fn.lineno, fn.end_lineno) if fn else (line, line)
            for r in res:
                if r['file'] == f and fn is not None and r['kind'] == 'method' and \
                        (r.get('where') or '').split('#')[-1] == fn.name:
                    primary[r['origin']] = r
                elif r['file'] == f and r['kind'] == 'transaction' and a <= int(r['origin'].rsplit(':', 1)[1]) <= b:
                    primary[r['origin']] = r
                elif any(site_line(w)[0] == f and a <= site_line(w)[1] <= b for w in r['writes']):
                    fallback[r['origin']] = r
            for n, m in models.items():
                hit = any(cb.get('node') is not None and cb.get('file') == f and cb['node'].line <= line <= cb['node'].end
                          for cb in m.callbacks)
                hit = hit or (m.file == f and fn is not None and fn.name in ('save', 'delete'))
                if hit:
                    for ev in evs:
                        if '%s#%s' % (n, ev) in by_origin:
                            primary['%s#%s' % (n, ev)] = by_origin['%s#%s' % (n, ev)]
        def labels(matched):
            out = []
            for o, r in matched.items():
                ws = full[o]['writes']
                if t['mech'] == 'D':
                    # a synchronous remote write: the outbox replication it triggers is a template of its own
                    ws = [w for w in ws if not w.get('async')]
                c = CL.classify(ws, rr, cc, dd, mutable=pre)
                if c is not None and len({silo.get(w['table']) for w in ws} - {None}) >= 2:
                    out.append(dict(origin=o, strict=c['strict'], oneway=c['oneway'], reasons=c['reasons'][:6]))
            return out
        labs, how = labels(primary), 'primary'
        if not labs:
            labs, how = labels(fallback), 'fallback'
        best = {}
        for reading in ('strict', 'oneway'):
            vals = [x[reading] for x in labs]
            best[reading] = max(vals, key=CL.RANK.get) if vals else 'missed'
        val.append(dict(id=t['id'], mech=t['mech'], label=t['cls'], strict=best['strict'], oneway=best['oneway'],
                        what=t['what'], locs=t['locs'], how=how,
                        templates=labs[:12]))

    stats = dict(files=len(prj.files), models=sum(1 for m in models.values() if m.table in silo),
                 silos=Counter(silo.values()), refs=len(refs), cross_refs=len(rr),
                 refs_by_kind=Counter(r['kind'] for r in rr), rpc_services=len(var2svc), outbox=ob,
                 copies=Counter(c['evidence'] for c in cps), copies_kept=len(kept), cross_copies=len(cc),
                 sites=len(sites), calls=len(calls), templates=len(res), by_kind=Counter(r['kind'] for r in res),
                 strict=Counter(r['strict'] for r in res), oneway=Counter(r['oneway'] for r in res),
                 sec=round(time.time() - t0, 1))
    return dict(stats=stats, validation=val, templates=res, copies=cps, broken=broken)


if __name__ == '__main__':
    out = analyse(sys.argv[1], sys.argv[2])
    with open(sys.argv[3], 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1, default=lambda o: sorted(o) if isinstance(o, set) else str(o))
    print(json.dumps(out['stats'], default=str))
    print(Counter((v['label'], v['strict']) for v in out['validation']))
    print(Counter((v['label'], v['oneway']) for v in out['validation']))
