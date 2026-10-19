"""ActiveRecord models of a Rails application, read statically.

For every model class: its table, STI base, associations (with dependent, counter_cache, touch,
polymorphic, through, foreign_key, class_name), uniqueness validations, attr_readonly, callbacks
and methods.  Concerns (modules that extend ActiveSupport::Concern) contribute the DSL of their
`included do` block and their methods to every model that includes them.
"""
import os
import re

import fsutil
import ruby
from schema import plural, singular

MODEL_BASES = {'ActiveRecord::Base', 'ApplicationRecord', '::ApplicationRecord', 'ActiveRecord::Base'}
ASSOC = ('belongs_to', 'has_many', 'has_one', 'has_and_belongs_to_many')
CALLBACK = re.compile(r'^(before|after|around)_(validation|save|create|update|destroy|commit|rollback|'
                      r'create_commit|update_commit|destroy_commit|save_commit|initialize|find|touch)$')


# ---------------------------------------------------------------------------- argument lists
def split_top(s, sep=','):
    out, depth, cur, q = [], 0, [], None
    i = 0
    while i < len(s):
        ch = s[i]
        if q:
            cur.append(ch)
            if ch == '\\' and i + 1 < len(s):
                cur.append(s[i + 1])
                i += 2
                continue
            if ch == q:
                q = None
        elif ch in '"\'':
            q = ch
            cur.append(ch)
        elif ch in '([{':
            depth += 1
            cur.append(ch)
        elif ch in ')]}':
            depth -= 1
            cur.append(ch)
        elif ch == sep and depth == 0:
            out.append(''.join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    if ''.join(cur).strip():
        out.append(''.join(cur).strip())
    return out


def value(v):
    """Rough Ruby literal -> Python: symbols and strings to str, arrays to list, true/false, else raw."""
    v = v.strip()
    m = re.fullmatch(r':(\w+[?!]?)|"([^"]*)"|\'([^\']*)\'', v)
    if m:
        return next(g for g in m.groups() if g is not None)
    if v in ('true', 'false', 'nil'):
        return {'true': True, 'false': False, 'nil': None}[v]
    if v.startswith('[') and v.endswith(']'):
        return [value(x) for x in split_top(v[1:-1])]
    m = re.fullmatch(r'%[wi][\[(]([^\])]*)[\])]', v)
    if m:
        return m.group(1).split()
    if v.startswith('{') and v.endswith('}'):
        return opts(split_top(v[1:-1]))
    return v


def opts(parts):
    d = {}
    for p in parts:
        m = re.match(r'^(?::(\w+)\s*=>|(\w+):(?!:))\s*(.*)$', p, re.S)
        if m:
            d[m.group(1) or m.group(2)] = value(m.group(3))
    return d


def call(text):
    """'name args' or 'name(args)' -> (name, positional list, options dict, raw args)."""
    m = re.match(r'^(?:self\.)?([a-z_]\w*[?!]?)(?:\s*\(|\s+|$)(.*)$', text, re.S)
    if not m:
        return None
    name, rest = m.group(1), m.group(2).strip()
    if text[m.end(1):].lstrip().startswith('('):
        rest = rest[:-1] if rest.endswith(')') else rest
    rest = re.sub(r'\s+do(\s*\|[^|]*\|)?\s*$', '', rest)
    parts = split_top(rest)
    pos = [value(p) for p in parts if not re.match(r'^(?::\w+\s*=>|\w+:(?!:))', p)]
    return name, pos, opts(parts), rest


# ---------------------------------------------------------------------------- names
def underscore(name):
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    s = re.sub(r'([a-z\d])([A-Z])', r'\1_\2', s)
    return s.replace('::', '/').lower()


def camelize(s):
    return ''.join(w[:1].upper() + w[1:] for w in s.split('_'))


def classify(assoc, many):
    return camelize(singular(assoc) if many else assoc)


class Model:
    def __init__(self, name, file, line):
        self.name, self.file, self.line = name, file, line
        self.superclass = None
        self.table = None
        self.table_explicit = False
        self.abstract = False
        self.assocs = []          # dicts: kind, name, target, fk, pk, opts
        self.uniques = []         # (cols, scope cols, conditional)
        self.readonly = set()
        self.callbacks = []       # dicts: kind, event, target (method name) or node, opts
        self.methods = {}         # name -> ruby.Node
        self.method_file = {}     # name -> file of the definition
        self.includes = []
        self.nested = []          # accepts_nested_attributes_for
        self.stmts = []           # class-body statements (for later passes)
        self.sti_base = None
        self.node = None

    def __repr__(self):
        return 'Model(%s -> %s)' % (self.name, self.table)


class Concern:
    def __init__(self, name):
        self.name = name
        self.dsl = []             # (Stmt or Node) inside included do
        self.file = None
        self.method_file = {}
        self.methods = {}


def _resolve(name, ns, known):
    """Resolve a constant name written inside namespace ns against the known class names."""
    if name is None:
        return None
    name = name.lstrip(':')
    parts = ns.split('::') if ns else []
    for k in range(len(parts), -1, -1):
        cand = '::'.join(parts[:k] + [name])
        if cand in known:
            return cand
    return None


def _class_name(node):
    """Fully qualified name of a class or module node."""
    names = []
    n = node
    while n is not None and n.kind != 'file':
        if n.kind in ('class', 'module'):
            names.append(n.name.lstrip(':'))
        n = n.parent
    return '::'.join(reversed(names))


def _superclass(node):
    m = re.match(r'.*?\bclass\s+[\w:]+\s*<\s*([\w:]+)(\.base_class\b)?', node.head.text)
    if not m:
        return None
    # Spree.base_class and similar configurable bases resolve to <Namespace>::Base
    return m.group(1) + '::Base' if m.group(2) else m.group(1)


def _body(node):
    """Direct children of a class node, flattening private/protected sections."""
    for c in node.children:
        yield c


def scan(app_root, model_dirs=None):
    """Return (models by name, concerns by name, unbalanced files)."""
    if model_dirs is None:
        model_dirs = []
        for d, dirs, _ in os.walk(fsutil.lp(app_root)):
            dirs[:] = [x for x in dirs if x not in fsutil.SKIP_DIRS]
            if d.replace('\\', '/').endswith('/app/models'):
                model_dirs.append(d)
    classes, concerns, bad = {}, {}, []
    module_prefix = {}
    for md in model_dirs:
        for p in fsutil.walk(md, ('.rb',)):
            root, ok = ruby.read(p)
            if not ok:
                bad.append(fsutil.rel(p, app_root))
            for node in [root] + [n for n in root.walk() if isinstance(n, ruby.Node)]:
                if node.kind == 'module':
                    full = _class_name(node)
                    stmts = [c for c in node.children if isinstance(c, ruby.Stmt)]
                    is_concern = any(re.match(r'extend\s+ActiveSupport::Concern', s.text) for s in stmts)
                    for c in node.children:
                        if isinstance(c, ruby.Node) and c.kind == 'def' and c.name in ('self.table_name_prefix',):
                            body = [s.text for s in c.stmts()]
                            m = re.search(r'["\'](\w+)["\']', ' '.join(body))
                            if m:
                                module_prefix[full] = m.group(1)
                    for s in stmts:
                        m = re.match(r'self\.table_name_prefix\s*=\s*["\'](\w+)["\']', s.text)
                        if m:
                            module_prefix[full] = m.group(1)
                    if is_concern:
                        cc = concerns.setdefault(full, Concern(full))
                        for c in node.children:
                            if isinstance(c, ruby.Node) and c.kind == 'block' and \
                                    re.match(r'(?:included|prepended)\b', c.head.text):
                                cc.dsl.extend(c.children)
                                cc.file = fsutil.rel(p, app_root)
                            elif isinstance(c, ruby.Node) and c.kind == 'def':
                                cc.methods[c.name] = c
                                cc.method_file[c.name] = fsutil.rel(p, app_root)
                if node.kind != 'class':
                    continue
                full = _class_name(node)
                m = classes.get(full)
                if m is None:
                    m = classes[full] = Model(full, fsutil.rel(p, app_root), node.line)
                    m.node = node
                sup = _superclass(node)
                if sup and not m.superclass:
                    m.superclass = sup
                _read_body(m, node.children, fsutil.rel(p, app_root))
    # which classes are models
    known = set(classes)
    is_model = {}

    def model_p(name, seen=()):
        if name in is_model:
            return is_model[name]
        c = classes[name]
        sup = c.superclass
        res = False
        if sup in MODEL_BASES or (sup and sup.lstrip(':') in ('ActiveRecord::Base', 'ApplicationRecord')):
            res = True
        elif sup:
            ns = name.rsplit('::', 1)[0] if '::' in name else ''
            r = _resolve(sup, ns, known)
            if r and r not in seen and r != name:
                res = model_p(r, seen + (name,))
                if res:
                    c.sti_base = r
        is_model[name] = res
        return res

    models = {n: classes[n] for n in classes if model_p(n)}
    for name, m in models.items():
        ns = name.rsplit('::', 1)[0] if '::' in name else ''
        # prepend_mod / prepend_mod_with: EE::X (or Enterprise::X) extends X
        for pre in ('EE::', 'Enterprise::', 'JH::'):
            if pre + name in concerns and '::' + pre + name not in m.includes:
                m.includes.append('::' + pre + name)
        for inc in m.includes:
            r = _resolve(inc, ns, set(concerns))
            if r:
                cc = concerns[r]
                _read_body(m, cc.dsl, cc.file)
                for k, v in cc.methods.items():
                    if k not in m.methods:
                        m.methods[k] = v
                        m.method_file[k] = cc.method_file[k]
    # subclasses inherit the callbacks, associations and methods of their model base classes
    own = {n: (list(m.callbacks), list(m.assocs), dict(m.methods), dict(m.method_file)) for n, m in models.items()}
    for name, m in models.items():
        b, seen = m.sti_base, {name}
        while b and b in models and b not in seen:
            seen.add(b)
            cbs, assocs, meths, mfile = own[b]
            m.callbacks.extend(cb for cb in cbs if cb not in m.callbacks)
            names = {a['name'] for a in m.assocs}
            m.assocs.extend(a for a in assocs if a['name'] not in names)
            for k, v in meths.items():
                if k not in m.methods:
                    m.methods[k] = v
                    m.method_file[k] = mfile[k]
            b = models[b].sti_base
    return models, concerns, module_prefix, bad


def _read_body(m, children, src):
    for c in children:
        if isinstance(c, ruby.Node):
            if c.kind == 'def':
                m.methods[c.name] = c
                m.method_file[c.name] = src
                continue
            if c.kind == 'block' and c.head is not None:
                cl = call(c.head.text)
                if cl and CALLBACK.match(cl[0]):
                    m.callbacks.append({'kind': cl[0], 'target': None, 'node': c, 'opts': cl[2], 'file': src,
                                        'line': c.line})
                elif cl and cl[0] in ('included', 'class_methods'):
                    pass
                continue
            if c.kind == 'sclass':
                for d in c.children:
                    if isinstance(d, ruby.Node) and d.kind == 'def':
                        m.methods['self.' + d.name] = d
                        m.method_file['self.' + d.name] = src
            continue
        s = c
        m.stmts.append(s)
        t = s.text
        mm = re.match(r'self\.table_name\s*=\s*(?:["\']([\w.]+)["\']|:(\w+))', t)
        if mm:
            m.table, m.table_explicit = (mm.group(1) or mm.group(2)).split('.')[-1], True
            continue
        if re.match(r'self\.abstract_class\s*=\s*true', t):
            m.abstract = True
            continue
        cl = call(t)
        if not cl:
            continue
        name, pos, op, raw = cl
        if name in ASSOC and pos:
            m.assocs.append({'kind': name, 'name': str(pos[0]), 'opts': op, 'line': s.line,
                             'scope': len(pos) > 1 or '->' in raw.split(',', 1)[-1][:6]})
        elif name in ('include', 'prepend'):
            m.includes.extend(str(x) for x in pos if isinstance(x, str))
            m.includes.extend(re.findall(r'[A-Z][\w:]*', raw) if not pos else [])
        elif name == 'attr_readonly':
            m.readonly.update(str(x) for x in pos)
        elif name == 'validates' and 'uniqueness' in op:
            u = op['uniqueness']
            scope = u.get('scope', []) if isinstance(u, dict) else []
            scope = [scope] if isinstance(scope, str) else list(scope or [])
            cond = isinstance(u, dict) and ('conditions' in u or 'if' in u) or 'if' in op or 'unless' in op
            for col in pos:
                m.uniques.append(((str(col),), tuple(str(x) for x in scope), bool(cond)))
        elif name == 'validates_uniqueness_of':
            scope = op.get('scope', [])
            scope = [scope] if isinstance(scope, str) else list(scope or [])
            for col in pos:
                m.uniques.append(((str(col),), tuple(str(x) for x in scope), 'if' in op or 'conditions' in op))
        elif name == 'accepts_nested_attributes_for':
            m.nested.extend(str(x) for x in pos)
        elif CALLBACK.match(name):
            targets = [x for x in pos if isinstance(x, str) and re.fullmatch(r'\w+[?!]?', x)]
            inline = None if targets else raw
            for tg in targets or [None]:
                m.callbacks.append({'kind': name, 'target': tg, 'node': None, 'inline': inline, 'file': src,
                                    'opts': op, 'line': s.line})
        elif name == 'counter_culture' and pos:
            m.assocs.append({'kind': 'counter_culture', 'name': str(pos[0]), 'opts': op, 'line': s.line,
                             'scope': False})


# ---------------------------------------------------------------------------- tables and references
def assign_tables(models, module_prefix, schema_tables):
    """Table of every model, following STI to the base class."""
    tabs = set(schema_tables)

    def default(name):
        parts = name.split('::')
        base = plural(underscore(parts[-1]))
        cands = []
        for k in range(len(parts) - 1, 0, -1):
            ns = '::'.join(parts[:k])
            if ns in module_prefix:
                cands.append(module_prefix[ns] + base)
        cands.append(base)
        if len(parts) > 1:
            cands.append(plural(underscore('_'.join(parts[-2:]))))
            cands.append(underscore(parts[-2]) + '_' + base)
        for c in cands:
            if c in tabs:
                return c
        return cands[0]

    for name, m in models.items():
        if m.table_explicit:
            continue
        b = m
        seen = set()
        while b.sti_base and b.sti_base in models and b.sti_base not in seen:
            seen.add(b.sti_base)
            if models[b.sti_base].abstract:
                break
            b = models[b.sti_base]
        if b is not m:
            m.table = b.table if b.table_explicit else None
            m.sti_base_table = True
        if m.table is None:
            m.table = (b.table if b.table_explicit else default(b.name))


def references(models):
    """Logical foreign keys from belongs_to and the owning side of has_many/has_one.
    Returns dicts: table, cols, ref, ref_cols, model, assoc, poly, opts, dependent (if any)."""
    by_name = models
    out = []

    def target(m, a, many):
        cn = a['opts'].get('class_name')
        ns = m.name.rsplit('::', 1)[0] if '::' in m.name else ''
        cand = cn if isinstance(cn, str) else classify(a['name'], many)
        return _resolve(cand, ns, set(by_name))

    for m in models.values():
        if m.abstract:
            continue
        for a in m.assocs:
            o = a['opts']
            if a['kind'] == 'belongs_to':
                poly = bool(o.get('polymorphic'))
                tgt = None if poly else target(m, a, False)
                fk = o.get('foreign_key') or a['name'] + '_id'
                pk = o.get('primary_key') or 'id'
                out.append(dict(table=m.table, cols=(str(fk),), ref=by_name[tgt].table if tgt else None,
                                ref_cols=(str(pk),), model=m.name, target=tgt, assoc=a['name'], poly=poly,
                                kind='belongs_to', opts=o, line=a['line']))
            elif a['kind'] in ('has_many', 'has_one') and not o.get('through'):
                tgt = target(m, a, a['kind'] == 'has_many')
                if not tgt:
                    continue
                if o.get('as'):
                    fk = o.get('foreign_key') or str(o['as']) + '_id'
                    poly = True
                else:
                    fk = o.get('foreign_key') or underscore(m.name.split('::')[-1]) + '_id'
                    if m.sti_base and not o.get('foreign_key'):
                        b = m
                        while b.sti_base and b.sti_base in by_name and not by_name[b.sti_base].abstract:
                            b = by_name[b.sti_base]
                        fk = underscore(b.name.split('::')[-1]) + '_id'
                    poly = False
                out.append(dict(table=by_name[tgt].table, cols=(str(fk),), ref=m.table,
                                ref_cols=(str(o.get('primary_key') or 'id'),), model=tgt, target=m.name,
                                assoc=a['name'], poly=poly, kind=a['kind'], opts=o, line=a['line'],
                                dependent=o.get('dependent')))
    return out
