"""Django front end: models, schema, write sites, calls and callbacks, read with Python's ast.

It produces the structures of the Rails front end, so that rails_templates.Builder, the copy
detection of rails_constraints and classify apply unchanged:
  models   Model objects with table, assocs (one belongs_to per ForeignKey or OneToOneField),
           callbacks (an overridden save() or delete() and signal receivers), methods
  schema   tables, columns, primary keys, unique keys and foreign keys from the model fields
  refs     one reference per foreign key, plus a reverse entry with dependent = destroy or
           nullify for on_delete = CASCADE or SET_NULL (Django runs these cascades itself)
  sites    create, get_or_create, update_or_create, bulk_create, QuerySet.update, delete,
           save(update_fields=...), bulk_update, attribute assignments and plain save()
  calls    self.method(), module functions (followed through imports), Class.method(),
           Class(...).method(), and Celery tasks started with delay or apply_async (deferred)
Transactions are `with transaction.atomic():` blocks and functions decorated with atomic.
"""
import ast
import os
import re

import fsutil
from schema import Schema

SKIP = fsutil.SKIP_DIRS | {'migrations', 'management', 'fixtures', 'scripts', 'bin', 'docs', 'testing',
                           'test_utils', 'tests_utils', 'factories', 'conftest', 'e2e', 'demo',
                           'populate', 'sample_data'}
FK_TYPES = {'ForeignKey', 'OneToOneField', 'ParentalKey', 'TreeForeignKey', 'ForeignObject', 'FlexibleForeignKey',
            'OneToOneCascadeDeletes'}
M2M_TYPES = {'ManyToManyField', 'ParentalManyToManyField', 'TreeManyToManyField'}
INSERT = {'create', 'get_or_create', 'update_or_create', 'bulk_create', 'create_or_update'}
SIGNAL_EV = {'pre_save': ('before_save', ['create', 'update']), 'post_save': ('after_save', ['create', 'update']),
             'pre_delete': ('before_destroy', ['destroy']), 'post_delete': ('after_destroy', ['destroy'])}
TIMESTAMPS = {'updated_at', 'created_at', 'modified', 'created', 'updated', 'last_modified'}


class Span:
    """A code range standing in for a ruby.Node in callback_ranges."""
    __slots__ = ('line', 'end')

    def __init__(self, line, end):
        self.line, self.end = line, end


class Model:
    def __init__(self, name, file, line, app):
        self.name, self.file, self.line, self.app = name, file, line, app
        self.bases = []
        self.table = None
        self.abstract = False
        self.proxy = False
        self.fields = {}          # field name -> dict(kind, column, target, on_delete, unique, ...)
        self.uniques = []
        self.assocs = []
        self.callbacks = []
        self.methods = {}
        self.method_file = {}
        self.sti_base = None
        self.readonly = set()
        self.nested = []
        self.includes = []
        self.mro = []

    def __repr__(self):
        return 'Model(%s -> %s)' % (self.name, self.table)


def dotted(node):
    """a.b.c for Name/Attribute chains, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return '.'.join(reversed(parts))
    return None


def const(node):
    if isinstance(node, ast.Constant):
        return node.value
    return None


def kwarg(call, name):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


def module_of(relp):
    return relp[:-3].replace('/', '.').replace('.__init__', '')


class Project:
    def __init__(self, root):
        self.root = root
        self.files = {}           # relp -> ast.Module
        self.modules = {}         # dotted module -> relp
        self.imports = {}         # relp -> {local name: (module, name or None)}
        self.app_label = {}       # app directory -> label
        for p in fsutil.walk(root, ('.py',), SKIP):
            relp = fsutil.rel(p, root)
            if re.search(r'(^|/)(tests?|test_\w+|\w+_tests?)(/|\.py$)', relp) or relp.endswith('conftest.py'):
                continue
            try:
                tree = ast.parse(fsutil.read(p))
            except (SyntaxError, ValueError):
                continue
            self.files[relp] = tree
            mod = module_of(relp)
            self.modules[mod] = relp
            # also register suffixes, since the source root (src/, backend/) is not on the module path
            parts = mod.split('.')
            for k in range(1, len(parts)):
                self.modules.setdefault('.'.join(parts[k:]), relp)
        for relp, tree in self.files.items():
            imp = {}
            pkg = module_of(relp).rsplit('.', 1)[0] if '.' in module_of(relp) else ''
            for n in ast.walk(tree):
                if isinstance(n, ast.ImportFrom):
                    base = n.module or ''
                    if n.level:
                        up = pkg.split('.')
                        up = up[:len(up) - (n.level - 1)] if n.level > 1 else up
                        base = '.'.join(x for x in up + ([base] if base else []) if x)
                    for a in n.names:
                        imp[a.asname or a.name] = (base, a.name)
                elif isinstance(n, ast.Import):
                    for a in n.names:
                        imp[(a.asname or a.name).split('.')[0]] = (a.name, None)
            self.imports[relp] = imp
            for n in ast.walk(tree):
                if isinstance(n, ast.ClassDef) and any(dotted(b) in ('AppConfig', 'apps.AppConfig') for b in n.bases):
                    lab = name = None
                    for st in n.body:
                        if isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Name):
                            if st.targets[0].id == 'label':
                                lab = const(st.value)
                            if st.targets[0].id == 'name':
                                name = const(st.value)
                    if lab:
                        self.app_label[os.path.dirname(relp)] = lab
                        if name:
                            self.app_label[name] = lab

    def resolve_module(self, mod):
        return self.modules.get(mod)


# ---------------------------------------------------------------------------- models
def app_label_for(project, relp):
    d = os.path.dirname(relp)
    if os.path.basename(d) == 'models':
        d = os.path.dirname(d)
    if d in project.app_label:
        return project.app_label[d]
    return os.path.basename(d) or 'app'


def scan_models(project):
    classes = {}
    for relp, tree in project.files.items():
        for n in tree.body:
            if isinstance(n, ast.ClassDef):
                key = (relp, n.name)
                classes[key] = n
    by_name = {}
    for (relp, name) in classes:
        by_name.setdefault(name, []).append(relp)

    def base_key(relp, b):
        d = dotted(b)
        if d is None:
            return None
        last = d.split('.')[-1]
        imp = project.imports.get(relp, {})
        first = d.split('.')[0]
        if first in imp and imp[first][1]:
            src = project.resolve_module(imp[first][0])
            if src and (src, last) in classes:
                return (src, last)
            src = project.resolve_module(imp[first][0] + '.' + imp[first][1])
            if src and (src, last) in classes:
                return (src, last)
        if (relp, last) in classes:
            return (relp, last)
        cands = by_name.get(last, [])
        if len(cands) == 1:
            return (cands[0], last)
        same_app = [c for c in cands if c.split('/')[:-1] == relp.split('/')[:-1]]
        if len(same_app) == 1:
            return (same_app[0], last)
        return ('?', d)

    is_model = {}

    def model_p(key, seen=()):
        if key in is_model:
            return is_model[key]
        is_model[key] = False
        node = classes[key]
        res = False
        for b in node.bases:
            d = dotted(b) or ''
            if d in ('models.Model', 'Model', 'django.db.models.Model', 'PolymorphicModel', 'MPTTModel',
                     'TimeStampedModel', 'AbstractBaseUser', 'AbstractUser', 'ClusterableModel'):
                res = True
            else:
                bk = base_key(key[0], b)
                if bk and bk[0] != '?' and bk not in seen and model_p(bk, seen + (key,)):
                    res = True
        is_model[key] = res
        return res

    models = {}
    keys = [k for k in classes if model_p(k)]
    names = {}
    for k in keys:
        names.setdefault(k[1], []).append(k)

    def mname(k):
        return k[1] if len(names[k[1]]) == 1 else '%s:%s' % (k[0], k[1])

    for k in keys:
        relp, name = k
        node = classes[k]
        m = Model(mname(k), relp, node.lineno, app_label_for(project, relp))
        m.node = node
        m.key = k
        for b in node.bases:
            bk = base_key(relp, b)
            if bk and bk in classes and bk in is_model and is_model[bk]:
                m.bases.append(bk)
        for st in node.body:
            if isinstance(st, ast.ClassDef) and st.name == 'Meta':
                for x in st.body:
                    if isinstance(x, ast.Assign) and isinstance(x.targets[0], ast.Name):
                        t = x.targets[0].id
                        if t == 'abstract':
                            m.abstract = bool(const(x.value))
                        elif t == 'proxy':
                            m.proxy = bool(const(x.value))
                        elif t == 'db_table':
                            m.table = const(x.value)
                        elif t == 'unique_together':
                            v = x.value
                            if isinstance(v, (ast.Tuple, ast.List)):
                                elts = v.elts
                                if elts and all(isinstance(e, ast.Constant) for e in elts):
                                    elts = [v]
                                for e in elts:
                                    if isinstance(e, (ast.Tuple, ast.List)):
                                        m.uniques.append((tuple(const(z) for z in e.elts if const(z)), (), False))
                        elif t == 'constraints' and isinstance(x.value, (ast.List, ast.Tuple)):
                            for c in x.value.elts:
                                if isinstance(c, ast.Call) and (dotted(c.func) or '').endswith('UniqueConstraint'):
                                    f = kwarg(c, 'fields')
                                    cond = kwarg(c, 'condition') is not None
                                    if isinstance(f, (ast.List, ast.Tuple)):
                                        m.uniques.append((tuple(const(z) for z in f.elts if const(z)), (), cond))
            elif isinstance(st, (ast.Assign, ast.AnnAssign)):
                tgt = st.targets[0] if isinstance(st, ast.Assign) else st.target
                if not isinstance(tgt, ast.Name) or not isinstance(st.value, ast.Call):
                    continue
                ftype = (dotted(st.value.func) or '').split('.')[-1]
                if not ftype.endswith(('Field', 'Key')) and ftype not in FK_TYPES | M2M_TYPES:
                    continue
                fname = tgt.id
                call = st.value
                f = dict(kind=ftype, name=fname, line=st.lineno)
                dbc = const(kwarg(call, 'db_column') or ast.Constant(None))
                if ftype in FK_TYPES:
                    to = call.args[0] if call.args else kwarg(call, 'to')
                    f['target'] = const(to) if isinstance(to, ast.Constant) else dotted(to)
                    od = kwarg(call, 'on_delete') or (call.args[1] if len(call.args) > 1 else None)
                    f['on_delete'] = (dotted(od) or '').split('.')[-1] if od is not None else 'CASCADE'
                    f['column'] = dbc or fname + '_id'
                    f['unique'] = ftype == 'OneToOneField' or bool(const(kwarg(call, 'unique') or ast.Constant(False)))
                    f['related_name'] = const(kwarg(call, 'related_name') or ast.Constant(None))
                elif ftype in M2M_TYPES:
                    to = call.args[0] if call.args else kwarg(call, 'to')
                    f['target'] = const(to) if isinstance(to, ast.Constant) else dotted(to)
                    thr = kwarg(call, 'through')
                    f['through'] = const(thr) if isinstance(thr, ast.Constant) else dotted(thr)
                    f['column'] = None
                else:
                    f['column'] = dbc or fname
                    if ftype == 'HybridCloudForeignKey':
                        # an id column that references a model of another silo (Sentry): no database
                        # constraint, cascades run later from tombstones
                        to = call.args[0] if call.args else kwarg(call, 'to')
                        f['target'] = const(to) if isinstance(to, ast.Constant) else dotted(to)
                        od = kwarg(call, 'on_delete')
                        f['on_delete'] = (const(od) if isinstance(od, ast.Constant) else (dotted(od) or '')).split('.')[-1] \
                            if od is not None else 'CASCADE'
                    f['unique'] = bool(const(kwarg(call, 'unique') or ast.Constant(False)))
                    f['pk'] = bool(const(kwarg(call, 'primary_key') or ast.Constant(False)))
                    if const(kwarg(call, 'editable') or ast.Constant(True)) is False:
                        m.readonly.add(f['column'])
                m.fields[fname] = f
            elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                m.methods[st.name] = Span(st.lineno, st.end_lineno)
                m.method_file[st.name] = relp
        models[m.name] = m
    # inherit fields of abstract bases; tables; proxies and multi-table inheritance
    by_key = {m.key: m for m in models.values()}

    def all_fields(m, seen=()):
        out = {}
        for bk in m.bases:
            b = by_key.get(bk)
            if b and b.key not in seen and (b.abstract):
                out.update(all_fields(b, seen + (m.key,)))
        out.update(m.fields)
        return out

    for m in models.values():
        m.fields = all_fields(m)
        for bk in m.bases:
            b = by_key.get(bk)
            if b and b.abstract:
                m.uniques = b.uniques + m.uniques
                for k, v in b.methods.items():
                    m.methods.setdefault(k, v)
                    m.method_file.setdefault(k, b.method_file[k])
    for m in models.values():
        if m.abstract:
            continue
        concrete_parent = next((by_key[bk] for bk in m.bases if bk in by_key and not by_key[bk].abstract), None)
        if m.proxy and concrete_parent:
            m.sti_base = concrete_parent.name
        elif concrete_parent:
            m.fields[concrete_parent.key[1].lower() + '_ptr'] = dict(
                kind='OneToOneField', name=concrete_parent.key[1].lower() + '_ptr', target=concrete_parent.name,
                on_delete='CASCADE', column=concrete_parent.key[1].lower() + '_ptr_id', unique=True, line=m.line,
                related_name=None)
    for m in models.values():
        if m.table is None and not m.abstract:
            if m.sti_base:
                continue
            m.table = '%s_%s' % (m.app, m.key[1].lower())
    for m in models.values():
        if m.sti_base:
            m.table = models[m.sti_base].table
    return models


def resolve_target(models, m, target):
    """Model name for a ForeignKey target ('self', 'app.Model', 'Model', settings.AUTH_USER_MODEL)."""
    if target is None:
        return None
    if target == 'self':
        return m.name
    t = str(target)
    if 'AUTH_USER_MODEL' in t:
        for n in models:
            if n.split(':')[-1] in ('User',) and not models[n].abstract:
                return n
        return None
    last = t.split('.')[-1]
    if last in models:
        return last
    cands = [n for n in models if n.split(':')[-1] == last]
    if len(cands) == 1:
        return cands[0]
    if '.' in t:
        app = t.split('.')[0]
        same = [n for n in cands if models[n].app == app]
        if same:
            return same[0]
    same = [n for n in cands if models[n].file.split('/')[:-1] == m.file.split('/')[:-1]]
    return same[0] if same else (cands[0] if cands else None)


def build_schema(models):
    sch = Schema()
    refs = []
    for m in models.values():
        if m.abstract or m.sti_base:
            continue
        pk = next((f['column'] for f in m.fields.values() if f.get('pk')), None)
        ptr = next((f['column'] for f in m.fields.values() if f['name'].endswith('_ptr')), None)
        pk = pk or ptr or 'id'
        cols = [pk] + [f['column'] for f in m.fields.values() if f.get('column') and f['column'] != pk]
        sch.add_table(m.table, cols, pk=(pk,))
        for f in m.fields.values():
            if f.get('unique') and f.get('column'):
                sch.add_unique(m.table, (f['column'],))
        for u, _, cond in m.uniques:
            cols_u = tuple(m.fields[x]['column'] if x in m.fields and m.fields[x].get('column') else x for x in u)
            sch.add_unique(m.table, cols_u, partial=cond)
    for m in models.values():
        if m.abstract or m.sti_base:
            continue
        for f in m.fields.values():
            if f['kind'] in FK_TYPES:
                tgt = resolve_target(models, m, f['target'])
                if not tgt or models[tgt].abstract:
                    continue
                T = models[tgt]
                m.assocs.append({'kind': 'belongs_to', 'name': f['name'], 'opts': {'foreign_key': f['column'], 'class_name': tgt},
                                 'line': f['line'], 'scope': False})
                sch.add_fk(m.table, (f['column'],), T.table, ('id',), source='model')
                refs.append(dict(table=m.table, cols=(f['column'],), ref=T.table, ref_cols=('id',), model=m.name,
                                 target=tgt, assoc=f['name'], poly=False, kind='belongs_to', opts={}, line=f['line']))
                od = f.get('on_delete')
                dep = {'CASCADE': 'destroy', 'SET_NULL': 'nullify', 'SET_DEFAULT': 'nullify', 'SET': 'nullify'}.get(od)
                if dep:
                    refs.append(dict(table=m.table, cols=(f['column'],), ref=T.table, ref_cols=('id',), model=m.name,
                                     target=tgt, assoc=f.get('related_name') or m.key[1].lower() + '_set', poly=False,
                                     kind='has_many', opts={}, line=f['line'], dependent=dep))
            elif f['kind'] in M2M_TYPES and not f.get('through'):
                tgt = resolve_target(models, m, f['target'])
                if not tgt or models[tgt].abstract:
                    continue
                jt = '%s_%s' % (m.table, f['name'])
                a, b = m.key[1].lower() + '_id', models[tgt].key[1].lower() + '_id'
                if a == b:
                    a, b = 'from_' + a, 'to_' + b
                sch.add_table(jt, ['id', a, b])
                sch.add_unique(jt, (a, b))
                for col, T in ((a, m), (b, models[tgt])):
                    sch.add_fk(jt, (col,), T.table, ('id',), source='m2m')
                    refs.append(dict(table=jt, cols=(col,), ref=T.table, ref_cols=('id',), model=None, target=T.name,
                                     assoc=None, poly=False, kind='db_fk', opts={}, line=f['line']))
    return sch, refs


# ---------------------------------------------------------------------------- write sites
class Index:
    def __init__(self, models):
        self.models = models
        self.by_class = {}
        for n, m in models.items():
            self.by_class.setdefault(m.key[1], []).append(n)
        self.by_noun = {}
        for n, m in models.items():
            for key in {_snake(m.key[1]), m.key[1].lower()}:
                self.by_noun.setdefault(key, set()).add(n)
        self.fk = {}
        self.reverse = {}
        for n, m in models.items():
            for f in m.fields.values():
                if f['kind'] in FK_TYPES:
                    tgt = resolve_target(models, m, f['target'])
                    if tgt:
                        self.fk[(n, f['name'])] = tgt
                        rn = f.get('related_name') or m.key[1].lower() + '_set'
                        self.reverse[(tgt, rn)] = n

    def cls(self, name, ctx=None):
        hits = self.by_class.get(name)
        if not hits:
            return None
        if len(hits) == 1 or ctx is None:
            return hits[0]
        same = [h for h in hits if self.models[h].file.split('/')[:-1] == self.models[ctx].file.split('/')[:-1]]
        return (same or hits)[0]

    def noun(self, word):
        w = word.lstrip('_')
        for p in ('new_', 'old_', 'existing_', 'current_', 'parent_', 'the_', 'target_', 'source_', 'db_'):
            if w.startswith(p) and w[len(p):]:
                w = w[len(p):]
        for cand in (w, w[:-1] if w.endswith('s') else w):
            hit = self.by_noun.get(cand)
            if hit:
                return sorted(hit, key=len)[0]
        parts = w.split('_')
        for k in range(1, len(parts)):
            suf = '_'.join(parts[k:])
            hit = self.by_noun.get(suf) or self.by_noun.get(suf[:-1] if suf.endswith('s') else suf)
            if hit:
                return sorted(hit, key=len)[0]
        return None


def _snake(name):
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    return re.sub(r'([a-z\d])([A-Z])', r'\1_\2', s).lower()


QUERY = {'objects', 'filter', 'exclude', 'all', 'select_related', 'prefetch_related', 'order_by', 'using',
         'select_for_update', 'only', 'defer', 'distinct', 'annotate', 'get', 'first', 'last', 'none',
         'values', 'values_list', 'iterator', 'latest', 'earliest', 'in_bulk', 'non_polymorphic', 'alias',
         'with_deleted', 'all_objects', 'unscoped', 'live', 'active', 'public', 'visible', 'published'}


class Writes(ast.NodeVisitor):
    """Collect sites and calls of one function."""

    def __init__(self, project, idx, relp, cls, ctx, meth, txn0, sites, calls):
        self.p, self.idx, self.relp, self.cls, self.ctx, self.meth = project, idx, relp, cls, ctx, meth
        self.txn = [txn0] if txn0 else []
        self.sites, self.calls = sites, calls
        self.local = {}
        self.new = set()
        self.dicts = {}           # local name -> keys of a dict literal or dict(...) call assigned to it
        self.ctor = {}            # local name -> (model, keyword arguments) of Model(...) assigned to it
        self.alias = {}           # local name -> self.attr assigned to it (u = self.user)

    def model_of(self, node):
        """Model of an expression, or None."""
        if isinstance(node, ast.Name):
            if node.id == 'self':
                return self.ctx
            if node.id in ('instance', 'obj', 'sender') and self.local.get(node.id):
                return self.local[node.id]
            if node.id in self.local:
                return self.local[node.id]
            if node.id[:1].isupper():
                return self.idx.cls(node.id, self.ctx)
            return self.idx.noun(node.id)
        if isinstance(node, ast.Attribute):
            base = self.model_of(node.value)
            if node.attr in QUERY:
                return base
            if base is not None:
                if (base, node.attr) in self.idx.fk:
                    return self.idx.fk[(base, node.attr)]
                if (base, node.attr) in self.idx.reverse:
                    return self.idx.reverse[(base, node.attr)]
            return self.idx.noun(node.attr) if not node.attr.startswith('_') else base
        if isinstance(node, ast.Call):
            return self.model_of(node.func)
        if isinstance(node, ast.Subscript):
            return self.model_of(node.value)
        return None

    def site(self, node, model, kind, attrs, op, recv, args=''):
        self.sites.append({'file': self.relp, 'line': node.lineno, 'cls': self.cls, 'meth': self.meth,
                           'model': model, 'kind': kind,
                           'attrs': [a for a in attrs if a not in TIMESTAMPS] if attrs != ['*'] else attrs,
                           'op': op, 'txn': self.txn[-1] if self.txn else None, 'recv': recv, 'args': args[:300],
                           'ctx': self.ctx, 'text': ast.unparse(node)[:200] if hasattr(ast, 'unparse') else ''})
        if recv in self.alias:
            self.sites[-1]['recv_src'] = self.alias[recv]

    def visit_With(self, node):
        atomic = any('atomic' in (dotted(i.context_expr.func if isinstance(i.context_expr, ast.Call)
                                         else i.context_expr) or '') for i in node.items)
        if atomic:
            self.txn.append('%d' % node.lineno)
        self.generic_visit(node)
        if atomic:
            self.txn.pop()

    visit_AsyncWith = visit_With

    def visit_FunctionDef(self, node):
        # a nested function (a closure such as apply_update): its writes count where it is defined,
        # since the enclosing function calls it
        self.params(node)
        for st in node.body:
            self.visit(st)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        # transaction.on_commit(lambda: service.method(...)): the calls of the lambda run here
        self.visit(node.body)

    def annotated(self, ann):
        """Model named by a type annotation (X, X | None, Optional[X], "X")."""
        if ann is None:
            return None
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            return self.idx.cls(ann.value.split('.')[-1], self.ctx) if ann.value[:1].isupper() else None
        if isinstance(ann, ast.BinOp):
            return self.annotated(ann.left) or self.annotated(ann.right)
        if isinstance(ann, ast.Subscript):
            return self.annotated(ann.slice)
        d = dotted(ann)
        if d and d.split('.')[-1][:1].isupper():
            return self.idx.cls(d.split('.')[-1], self.ctx)
        return None

    def params(self, fn):
        for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs:
            m = self.annotated(a.annotation)
            if m:
                self.local[a.arg] = m

    def visit_Assign(self, node):
        self.generic_visit(node)
        v = node.value
        for t in node.targets:
            if isinstance(t, ast.Name):
                if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) and v.value.id == 'self':
                    self.alias[t.id] = 'self.' + v.attr
                else:
                    self.alias.pop(t.id, None)
                if isinstance(v, ast.Dict):
                    self.dicts[t.id] = [const(k) for k in v.keys if k is not None and const(k)]
                elif isinstance(v, ast.Call) and dotted(v.func) == 'dict':
                    self.dicts[t.id] = [k.arg for k in v.keywords if k.arg]
                elif isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id[:1].isupper() and \
                        v.keywords:
                    cm = self.idx.cls(v.func.id, self.ctx)
                    if cm:
                        self.ctor[t.id] = (cm, [k.arg for k in v.keywords if k.arg])
                mod = self.model_of(v)
                if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr in INSERT:
                    mod = self.model_of(v.func.value)
                if mod:
                    self.local[t.id] = mod
                    if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id[:1].isupper():
                        self.new.add(t.id)
            elif isinstance(t, ast.Attribute):
                recv = dotted(t.value) or ''
                mod = self.model_of(t.value)
                if mod and not t.attr.startswith('_'):
                    rhs = ast.unparse(v) if hasattr(ast, 'unparse') else ''
                    rhs = re.sub(r'^(self|instance)\.', '', rhs)
                    self.site(node, mod, 'assign', [t.attr], '=', 'self' if recv in ('self',) or
                              (recv == 'instance' and mod == self.ctx) else recv, rhs)
                    self.sites[-1]['raw'] = ast.unparse(v) if hasattr(ast, 'unparse') else ''
            elif isinstance(t, ast.Tuple):
                pass

    def visit_AnnAssign(self, node):
        if isinstance(node.target, ast.Name):
            m = self.annotated(node.annotation)
            if m:
                self.local[node.target.id] = m
        if isinstance(node.target, ast.Name) and node.value is not None:
            v = node.value
            if isinstance(v, ast.Dict):
                self.dicts[node.target.id] = [const(k) for k in v.keys if k is not None and const(k)]
            elif isinstance(v, ast.Call) and dotted(v.func) == 'dict':
                self.dicts[node.target.id] = [k.arg for k in v.keywords if k.arg]
        self.generic_visit(node)

    def visit_Call(self, node):
        self.generic_visit(node)
        f = node.func
        # a new record passed to a function: if that function saves it, the record is written here
        for a in node.args:
            if isinstance(a, ast.Name) and a.id in self.ctor:
                cm, attrs = self.ctor.pop(a.id)
                name = dotted(f) or (f.id if isinstance(f, ast.Name) else None)
                if name:
                    self.calls.append(dict(file=self.relp, line=node.lineno, cls=self.cls, meth=self.meth,
                                           txn=self.txn[-1] if self.txn else None, name=name, async_=False,
                                           passed=(cm, attrs, a.id)))
        if isinstance(f, ast.Attribute):
            op = f.attr
            recv = dotted(f.value) or ''
            kws = {k.arg: k.value for k in node.keywords if k.arg}
            args = ', '.join('%s: %s' % (k, ast.unparse(v)) for k, v in kws.items()) if hasattr(ast, 'unparse') else ''
            args = re.sub(r'\b(self|instance)\.', '', args)
            # **d with d a local dict: its keys are keyword arguments
            for k in node.keywords:
                if k.arg is None and isinstance(k.value, ast.Name) and k.value.id in self.dicts:
                    for key in self.dicts[k.value.id]:
                        kws.setdefault(key, k.value)
            if op in INSERT:
                mod = self.model_of(f.value)
                attrs = [k for k in kws if k not in ('defaults', 'create_defaults')]
                d = kws.get('defaults')
                if isinstance(d, ast.Dict):
                    attrs += [const(k) for k in d.keys if const(k)]
                elif isinstance(d, ast.Name) and d.id in self.dicts:
                    attrs += self.dicts[d.id]
                if mod:
                    self.site(node, mod, 'insert', attrs, op, recv, args)
                if op == 'update_or_create' and mod:
                    self.site(node, mod, 'modify', attrs, op, recv, args)
            elif op == 'update' and (kws or node.args):
                mod = self.model_of(f.value)
                if mod and not isinstance(f.value, ast.Dict):
                    self.site(node, mod, 'modify', list(kws) or ['*'], op, recv, args)
            elif op == 'delete' and not node.args:
                mod = self.model_of(f.value)
                if mod:
                    self.site(node, mod, 'delete', [], op, recv)
            elif op == 'bulk_update':
                mod = self.model_of(f.value)
                flds = node.args[1] if len(node.args) > 1 else kws.get('fields')
                attrs = [const(e) for e in flds.elts if const(e)] if isinstance(flds, (ast.List, ast.Tuple)) else ['*']
                if mod:
                    self.site(node, mod, 'modify', attrs, op, recv)
            elif op == 'save':
                mod = self.model_of(f.value)
                uf = kws.get('update_fields')
                if mod:
                    if isinstance(uf, (ast.List, ast.Tuple, ast.Set)):
                        self.site(node, mod, 'modify', [const(e) for e in uf.elts if const(e)], op, recv)
                    elif recv in self.new:
                        self.site(node, mod, 'insert', [], op, recv)
                    else:
                        self.site(node, mod, 'save', [], op, 'self' if recv == 'self' else recv)
            elif op in ('delay', 'apply_async', 'send_task', 'si', 's', 'delay_on_commit'):
                fn = dotted(f.value)
                if fn:
                    self.call(node, fn, async_=True)
            elif isinstance(f.value, ast.Name) and f.value.id == 'self':
                self.calls.append(dict(file=self.relp, line=node.lineno, cls=self.cls, meth=self.meth,
                                       txn=self.txn[-1] if self.txn else None, target=(self.cls, op), async_=False))
            else:
                d = dotted(f)
                if d and d.split('.')[0][:1].isupper():
                    self.call(node, d, async_=False)
                elif isinstance(f.value, ast.Call) and isinstance(f.value.func, ast.Name) and \
                        f.value.func.id[:1].isupper():
                    self.call(node, f.value.func.id + '.' + op, async_=False)
                elif d:
                    self.call(node, d, async_=False)
        elif isinstance(f, ast.Name):
            self.call(node, f.id, async_=False)

    def call(self, node, name, async_):
        self.calls.append(dict(file=self.relp, line=node.lineno, cls=self.cls, meth=self.meth,
                               txn=self.txn[-1] if self.txn else None, name=name, async_=async_))


def _saves(project, d):
    """Whether the function at d = (file, start, end) saves or upserts a record."""
    relp, a, b = d
    tree = project.files.get(relp)
    if tree is None:
        return False
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.lineno == a:
            for x in ast.walk(n):
                if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and \
                        x.func.attr in INSERT | {'save', 'update'}:
                    return True
            return False
    return False


def scan(project, models, idx=None, resolve=None):
    """resolve(call, dotted name, defs) -> definition key: an application-specific call resolver."""
    idx = idx or Index(models)
    sites, calls, defs = [], [], {}
    receivers = []
    for relp, tree in project.files.items():
        mod = module_of(relp)
        units = []
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                units.append((None, n))
            elif isinstance(n, ast.ClassDef):
                for st in n.body:
                    if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        units.append((n.name, st))
            elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
                c = n.value
                if isinstance(c.func, ast.Attribute) and c.func.attr == 'connect' and \
                        (dotted(c.func.value) or '').split('.')[-1] in SIGNAL_EV and c.args:
                    receivers.append((relp, (dotted(c.func.value) or '').split('.')[-1], dotted(c.args[0]),
                                      dotted(kwarg(c, 'sender'))))
        for cname, fn in units:
            ctx = None
            if cname:
                key = [n for n, m in models.items() if m.key == (relp, cname)]
                ctx = key[0] if key else None
            owner = cname if cname else mod
            defs[(owner, fn.name)] = (relp, fn.lineno, fn.end_lineno)
            txn0 = None
            for dec in fn.decorator_list:
                dd = dotted(dec.func if isinstance(dec, ast.Call) else dec) or ''
                if 'atomic' in dd:
                    txn0 = 'fn%d' % fn.lineno
                if dd.split('.')[-1] == 'receiver' and isinstance(dec, ast.Call) and dec.args:
                    sigs = dec.args[0]
                    sigs = sigs.elts if isinstance(sigs, (ast.List, ast.Tuple)) else [sigs]
                    for sg in sigs:
                        if (dotted(sg) or '').split('.')[-1] not in SIGNAL_EV:
                            continue        # an application signal: its senders are ordinary calls
                        receivers.append((relp, (dotted(sg) or '').split('.')[-1], (fn.name, owner),
                                          dotted(kwarg(dec, 'sender'))))
            w = Writes(project, idx, relp, owner, ctx, fn.name, txn0, sites, calls)
            w.params(fn)
            # in signal receivers, `instance` is the sender model
            for st in fn.body:
                w.visit(st)
    # resolve calls to definitions
    out = []
    for c in calls:
        if 'target' in c:
            if c['target'] in defs:
                out.append(c)
            continue
        name = c.pop('name')
        relp = c['file']
        parts = name.split('.')
        tgt = None
        imp = project.imports.get(relp, {})
        if len(parts) == 1:
            if (module_of(relp), name) in defs:
                tgt = (module_of(relp), name)
            elif name in imp and imp[name][1]:
                src = project.resolve_module(imp[name][0])
                if src and (module_of(src), imp[name][1]) in defs:
                    tgt = (module_of(src), imp[name][1])
        elif len(parts) == 2:
            a, b = parts
            if a[:1].isupper():
                if (a, b) in defs:
                    tgt = (a, b)
                elif (a, 'perform') in defs and b in ('delay', 'apply_async'):
                    tgt = (a, 'perform')
            elif a in imp:
                modn = imp[a][0] + ('.' + imp[a][1] if imp[a][1] else '')
                src = project.resolve_module(modn) or project.resolve_module(imp[a][0])
                if src and (module_of(src), b) in defs:
                    tgt = (module_of(src), b)
        if tgt is None and resolve:
            tgt = resolve(c, name, defs)
        if tgt and c.get('passed'):
            if _saves(project, defs[tgt]):
                cm, attrs, var = c['passed']
                for kind in ('insert', 'modify'):
                    sites.append({'file': c['file'], 'line': c['line'], 'cls': c['cls'], 'meth': c['meth'],
                                  'model': cm, 'kind': kind, 'attrs': [x for x in attrs if x not in TIMESTAMPS],
                                  'op': 'passed', 'txn': c['txn'], 'recv': var, 'args': '', 'ctx': None,
                                  'text': ''})
            continue
        if tgt:
            c['target'] = tgt
            out.append(c)
    # callbacks: save()/delete() overrides and signal receivers
    for n, m in models.items():
        for meth, ev in (('save', 'around_save'), ('delete', 'before_destroy')):
            if (m.key[1], meth) in defs:
                f, a, b = defs[(m.key[1], meth)]
                m.callbacks.append({'kind': ev, 'target': None, 'node': Span(a, b), 'opts': {}, 'file': f, 'line': a})
    for relp, sig, handler, sender in receivers:
        if not sender:
            continue
        sm = idx.cls(sender.split('.')[-1])
        if not sm:
            continue
        if isinstance(handler, tuple):
            key = (handler[1], handler[0])
        else:
            hn = (handler or '').split('.')[-1]
            key = (module_of(relp), hn)
        if key in defs:
            f, a, b = defs[key]
            kind = SIGNAL_EV[sig][0]
            models[sm].callbacks.append({'kind': kind, 'target': None, 'node': Span(a, b), 'opts': {},
                                         'file': f, 'line': a})
            for s in sites:
                if s['file'] == f and a <= s['line'] <= b and s['recv'] == 'instance':
                    s['model'] = sm
                    if s['kind'] == 'assign':
                        s['recv'] = 'self'
    return sites, defs, out


def mutability(sites, models, schema):
    precise = {}
    for s in sites:
        if s['kind'] in ('insert', 'delete', 'save') or not s['model'] or s['model'] not in models:
            continue
        t = models[s['model']].table
        for a in s['attrs']:
            col = a if a in schema.tables.get(t, []) else (a + '_id' if a + '_id' in schema.tables.get(t, []) else a)
            precise.setdefault(t, set()).add(col)
    return precise
