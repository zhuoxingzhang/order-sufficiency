"""Sentry silo census (static, AST only; never imports Sentry).

Usage: python -I sentry_corpus.py <sentry checkout>/src/sentry <out.json>

Collects every model class decorated with @cell_silo_model or @control_silo_model,
its table, outbox category, replication handlers, and reference columns
(FlexibleForeignKey / ForeignKey / OneToOne / HybridCloudForeignKey, plus plain
integer *_id columns that point at another silo by convention).
"""
import ast
import json
import os
import re
import sys
from collections import Counter, defaultdict

root, out_path = sys.argv[1], sys.argv[2]
SKIP_DIRS = {'testutils', 'migrations', 'new_migrations', 'tests', 'static'}
FK_CALLS = {'FlexibleForeignKey', 'ForeignKey', 'OneToOneField', 'OneToOneCascadeDeletes'}
INT_CALLS = {'BoundedBigIntegerField', 'BoundedPositiveIntegerField', 'BigIntegerField',
             'BoundedIntegerField', 'IntegerField', 'PositiveIntegerField'}


def call_name(node):
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return f.attr
    return None


def const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def kw(call, name):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


classes = {}  # (module, name) -> info
for dp, dns, fns in os.walk(root):
    dns[:] = [d for d in dns if d not in SKIP_DIRS]
    for fn in fns:
        if not fn.endswith('.py'):
            continue
        path = os.path.join(dp, fn)
        rel = os.path.relpath(path, os.path.dirname(root)).replace(os.sep, '/')
        try:
            tree = ast.parse(open(path, encoding='utf-8').read())
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            silo = None
            for d in node.decorator_list:
                n = d.id if isinstance(d, ast.Name) else (call_name(d) if isinstance(d, ast.Call) else None)
                if n == 'cell_silo_model':
                    silo = 'cell'
                elif n == 'control_silo_model':
                    silo = 'control'
            info = {'name': node.name, 'file': rel, 'line': node.lineno, 'silo': silo,
                    'bases': [const_str(b) for b in node.bases], 'db_table': None, 'abstract': False,
                    'category': None, 'refs': [], 'methods': [], 'unique_together': None}
            for st in node.body:
                if isinstance(st, ast.ClassDef) and st.name == 'Meta':
                    for m in st.body:
                        if isinstance(m, ast.Assign) and isinstance(m.targets[0], ast.Name):
                            t = m.targets[0].id
                            if t == 'db_table':
                                info['db_table'] = const_str(m.value)
                            elif t == 'abstract':
                                info['abstract'] = isinstance(m.value, ast.Constant) and m.value.value is True
                            elif t == 'unique_together':
                                info['unique_together'] = ast.unparse(m.value)
                elif isinstance(st, (ast.Assign, ast.AnnAssign)):
                    tgt = st.targets[0] if isinstance(st, ast.Assign) else st.target
                    val = st.value
                    if not isinstance(tgt, ast.Name) or val is None:
                        continue
                    if tgt.id == 'category' and isinstance(val, ast.Attribute):
                        info['category'] = val.attr
                        continue
                    cn = call_name(val)
                    if cn in FK_CALLS:
                        target = const_str(val.args[0]) if val.args else const_str(kw(val, 'to'))
                        od = kw(val, 'on_delete')
                        info['refs'].append({'col': tgt.id, 'kind': 'fk', 'call': cn, 'target': target,
                                             'on_delete': ast.unparse(od) if od is not None else 'CASCADE',
                                             'db_constraint': ast.unparse(kw(val, 'db_constraint')) if kw(val, 'db_constraint') is not None else None,
                                             'null': ast.unparse(kw(val, 'null')) if kw(val, 'null') is not None else None,
                                             'line': st.lineno})
                    elif cn == 'HybridCloudForeignKey':
                        target = const_str(val.args[0]) if val.args else None
                        od = kw(val, 'on_delete')
                        info['refs'].append({'col': tgt.id, 'kind': 'hcfk', 'call': cn, 'target': target,
                                             'on_delete': const_str(od) if od is not None else None,
                                             'null': ast.unparse(kw(val, 'null')) if kw(val, 'null') is not None else None,
                                             'line': st.lineno})
                    elif cn in INT_CALLS and tgt.id.endswith('_id'):
                        info['refs'].append({'col': tgt.id, 'kind': 'int', 'call': cn, 'target': None,
                                             'line': st.lineno})
                elif isinstance(st, ast.FunctionDef):
                    calls = sorted({ast.unparse(c.func) for c in ast.walk(st) if isinstance(c, ast.Call)
                                    and isinstance(c.func, ast.Attribute)
                                    and re.search(r'_service$|_rpc$|service$', ast.unparse(c.func.value) or '')})
                    info['methods'].append({'name': st.name, 'line': st.lineno, 'service_calls': calls})
            classes[(rel, node.name)] = info

# inherit refs / category from abstract bases defined in the tree (by class name)
by_name = defaultdict(list)
for info in classes.values():
    by_name[info['name']].append(info)


def inherited(info, seen=None):
    seen = seen or set()
    refs, cat, bases = list(info['refs']), info['category'], set(info['bases'])
    for b in info['bases']:
        for binfo in by_name.get(b, []):
            if binfo['silo'] is None and binfo['name'] not in seen:
                seen.add(binfo['name'])
                r, c, bb = inherited(binfo, seen)
                cols = {x['col'] for x in refs}
                refs += [x for x in r if x['col'] not in cols]
                cat = cat or c
                bases |= bb
    return refs, cat, bases


models = []
for info in classes.values():
    if info['silo'] is None:
        continue
    refs, cat, bases = inherited(info)
    m = dict(info, refs=refs, category=cat, all_bases=sorted(b for b in bases if b))
    m['db_table'] = m['db_table'] or ('?' + info['name'].lower())
    models.append(m)

silo_of = {}
for m in models:
    silo_of.setdefault(m['name'], set()).add(m['silo'])
silo_of = {k: (v.pop() if len(v) == 1 else 'ambiguous') for k, v in silo_of.items()}
canon = {k.lower(): k for k in silo_of}
canon['auth_user_model'] = 'User'  # settings.AUTH_USER_MODEL is sentry.User


def target_name(t, child):
    name = (t or '').split('.')[-1]
    if name == 'self':
        return child
    return canon.get(name.lower(), name)


def target_silo(t):
    return silo_of.get(t, '?') if t else None


print('models', len(models), Counter(m['silo'] for m in models))
REPL = {'ReplicatedCellModel', 'ReplicatedControlModel', 'CellOutboxProducingModel', 'ControlOutboxProducingModel'}
print('outbox-producing models', Counter((m['silo'], b) for m in models for b in m['all_bases'] if b in REPL))

edges = []
for m in models:
    for r in m['refs']:
        if r['kind'] == 'int':
            continue
        tn = target_name(r['target'], m['name'])
        od = r.get('on_delete')
        if r['kind'] == 'hcfk' and od:
            od = od.upper()
        edges.append({'child': m['name'], 'child_table': m['db_table'], 'child_silo': m['silo'],
                      'col': r['col'], 'kind': r['kind'], 'target': tn,
                      'target_silo': target_silo(tn), 'on_delete': od, 'file': m['file'], 'line': r['line']})
fk = [e for e in edges if e['kind'] == 'fk']
hc = [e for e in edges if e['kind'] == 'hcfk']
print('fk edges', len(fk), Counter((e['child_silo'], e['target_silo']) for e in fk))
print('hcfk edges', len(hc), Counter((e['child_silo'], e['target_silo']) for e in hc))
print('hcfk on_delete', Counter(e['on_delete'] for e in hc))
xhc = [e for e in hc if e['child_silo'] != e['target_silo'] and e['target_silo'] in ('cell', 'control')]
print('cross-silo hcfk', len(xhc), 'child tables', len({e['child'] for e in xhc}),
      'parent models', Counter(e['target'] for e in xhc).most_common())
print('cross-silo hcfk on_delete', Counter(e['on_delete'] for e in xhc))
fan = defaultdict(lambda: Counter())
for e in xhc:
    fan[e['target']][e['on_delete']] += 1
print('parents whose delete reaches the other silo', len(fan))
for p, c in sorted(fan.items(), key=lambda x: -sum(x[1].values())):
    print('  ', p, dict(c))

# cell tables whose org placement is (a) a direct org FK, (b) organization_id integer, (c) via project
org_direct = [m for m in models if m['silo'] == 'cell' and any(r['col'] in ('organization', 'organization_id') for r in m['refs'])]
proj = [m for m in models if m['silo'] == 'cell' and m not in org_direct and any(r['col'] in ('project', 'project_id') for r in m['refs'])]
print('cell tables with organization ref', len(org_direct), 'with project ref only', len(proj))

# cycles in the reference graph (fk + hcfk), table level
g = defaultdict(set)
for e in edges:
    if e['target_silo'] in ('cell', 'control'):
        g[e['child']].add(e['target'])
idx, low, st, on, sccs, cnt = {}, {}, [], set(), [], [0]
sys.setrecursionlimit(100000)


def dfs(v):
    idx[v] = low[v] = cnt[0]; cnt[0] += 1; st.append(v); on.add(v)
    for w in g.get(v, ()):
        if w not in idx:
            dfs(w); low[v] = min(low[v], low[w])
        elif w in on:
            low[v] = min(low[v], idx[w])
    if low[v] == idx[v]:
        comp = []
        while True:
            w = st.pop(); on.discard(w); comp.append(w)
            if w == v:
                break
        sccs.append(comp)


for v in list(g):
    if v not in idx:
        dfs(v)
cyc = [sorted(s) for s in sccs if len(s) > 1]
selfloops = sorted(v for v in g if v in g[v])
print('reference graph: non-trivial SCCs', len(cyc), cyc, 'self-loops', len(selfloops), selfloops)

json.dump({'models': models, 'edges': edges, 'cycles': cyc, 'self_loops': selfloops},
          open(out_path, 'w'), indent=1)
