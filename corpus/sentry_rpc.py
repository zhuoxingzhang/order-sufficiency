"""Sentry cross-silo RPC census (static, AST only; never imports Sentry).

Usage: python -I sentry_rpc.py <sentry checkout>/src/sentry <out.json>

1. Every RpcService subclass with `local_mode = SiloMode.X` and its @rpc_method /
   @cell_rpc_method methods; the module-level delegation variable that callers use.
2. For each method, whether the database-backed implementation writes (ORM save/
   create/update/delete calls, followed one level into same-module helpers).
3. Every call site `<delegation var>.<method>(` outside tests, with the silo of the
   enclosing endpoint / function / task when it is declared.
"""
import ast
import json
import os
import re
import sys
from collections import Counter, defaultdict

root, out_path = sys.argv[1], sys.argv[2]
SKIP_DIRS = {'testutils', 'migrations', 'new_migrations', 'tests', 'static'}
WRITE_ATTRS = {'save', 'delete', 'create', 'bulk_create', 'bulk_update', 'bulk_delete', 'get_or_create',
               'update_or_create', 'create_or_update', 'update', 'upsert', 'insert', 'remove', 'clear'}


def files():
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if fn.endswith('.py'):
                p = os.path.join(dp, fn)
                yield p, os.path.relpath(p, os.path.dirname(root)).replace(os.sep, '/')


trees = {}
for p, rel in files():
    try:
        trees[rel] = ast.parse(open(p, encoding='utf-8').read())
    except SyntaxError:
        pass

# 1. services
services = {}  # class name -> {silo, file, methods}
for rel, tree in trees.items():
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        silo = None
        for st in node.body:
            if isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Name) and st.targets[0].id == 'local_mode':
                silo = ast.unparse(st.value).split('.')[-1].lower()
        if silo is None:
            continue
        methods = []
        for st in node.body:
            if isinstance(st, ast.FunctionDef):
                decs = [ast.unparse(d) for d in st.decorator_list]
                if any('rpc_method' in d for d in decs):
                    methods.append(st.name)
        services[node.name] = {'silo': silo, 'file': rel, 'methods': methods, 'var': None, 'impl': None}

# delegation vars:  x_service = XService.create_delegation()
var_to_service = {}
for rel, tree in trees.items():
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            s = ast.unparse(node.value)
            m = re.match(r'(\w+)\.create_delegation\(', s)
            if m and m.group(1) in services and isinstance(node.targets[0], ast.Name):
                var_to_service[node.targets[0].id] = m.group(1)
                services[m.group(1)]['var'] = node.targets[0].id

# 2. implementations: a class whose bases include a service class
def writes_in(fn_node, helpers, depth=0):
    hits = set()
    for c in ast.walk(fn_node):
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute):
            a = c.func.attr
            recv = ast.unparse(c.func.value)
            if a in WRITE_ATTRS and not re.search(r'(^|\.)(kwargs|kwds|options|config|data|payload|result|update_kwargs|dict|params|fields|values|metadata|attrs|tags|extra|defaults|d|args|h|hasher|sha|hash|m)$', recv) \
                    and not recv.endswith(('_service', 'cache', 'logger', 'metrics')):
                hits.add(f'{recv}.{a}')
            if depth == 0 and isinstance(c.func.value, ast.Name) and c.func.value.id == 'self' and a in helpers:
                hits |= writes_in(helpers[a], helpers, 1)
        if depth == 0 and isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in helpers:
            hits |= writes_in(helpers[c.func.id], helpers, 1)
    return hits


impl_writes = {}  # (service, method) -> sorted writes
for rel, tree in trees.items():
    module_funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = [ast.unparse(b).split('.')[-1] for b in node.bases]
        svc = next((b for b in bases if b in services), None)
        if not svc or node.name in services:
            continue
        helpers = dict(module_funcs)
        helpers.update({n.name: n for n in node.body if isinstance(n, ast.FunctionDef)})
        services[svc]['impl'] = rel
        for st in node.body:
            if isinstance(st, ast.FunctionDef) and st.name in services[svc]['methods']:
                impl_writes[(svc, st.name)] = sorted(writes_in(st, helpers))

# 3. call sites
SILO_DEC = {'cell_silo_endpoint': 'cell', 'control_silo_endpoint': 'control', 'all_silo_endpoint': 'all',
            'cell_silo_function': 'cell', 'control_silo_function': 'control', 'all_silo_function': 'all'}


def silo_of_decorators(decs):
    for d in decs:
        s = ast.unparse(d)
        for k, v in SILO_DEC.items():
            if s.startswith(k):
                return v
        m = re.search(r'silo_mode\s*=\s*SiloMode\.(\w+)', s)
        if m:
            return m.group(1).lower()
        m = re.search(r'SiloMode\.(CELL|CONTROL)', s)
        if m and ('task' in s or 'silo' in s):
            return m.group(1).lower()
    return None


calls = []


class V(ast.NodeVisitor):
    def __init__(self, rel):
        self.rel, self.stack = rel, []

    def generic_scope(self, node):
        self.stack.append(node)
        self.generic_visit(node)
        self.stack.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = generic_scope

    def visit_Call(self, node):
        f = node.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in var_to_service:
            svc = var_to_service[f.value.id]
            if f.attr in services[svc]['methods']:
                silo, scope = None, []
                for s in reversed(self.stack):
                    scope.append(s.name)
                    silo = silo or silo_of_decorators(s.decorator_list)
                calls.append({'service': svc, 'var': f.value.id, 'method': f.attr, 'target_silo': services[svc]['silo'],
                              'file': self.rel, 'line': node.lineno, 'scope': '.'.join(reversed(scope)),
                              'caller_silo': silo, 'writes': impl_writes.get((svc, f.attr), None)})
        self.generic_visit(node)


for rel, tree in trees.items():
    if '/services/' in rel and rel.endswith(('/impl.py', '/service.py')):
        # calls inside a service implementation run in that service's own silo
        pass
    V(rel).visit(tree)

for c in calls:
    if c['caller_silo'] is None:
        if c['file'].startswith('sentry/receivers/outbox/cell.py'):
            c['caller_silo'] = 'cell'
        elif c['file'].startswith('sentry/receivers/outbox/control.py'):
            c['caller_silo'] = 'control'

write_methods = {k for k, v in impl_writes.items() if v}
print('services', len(services), Counter(s['silo'] for s in services.values()))
print('rpc methods', sum(len(s['methods']) for s in services.values()), 'with writing impl', len(write_methods))
wc = [c for c in calls if (c['service'], c['method']) in write_methods]
print('call sites', len(calls), 'to writing methods', len(wc))
print('writing call sites by caller silo -> target silo', Counter((c['caller_silo'], c['target_silo']) for c in wc))
json.dump({'services': services, 'impl_writes': {f'{k[0]}.{k[1]}': v for k, v in impl_writes.items()},
           'calls': calls}, open(out_path, 'w'), indent=1)
