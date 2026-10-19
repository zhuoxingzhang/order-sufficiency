"""Write sites of a Rails application: which code writes which attributes of which model.

A write site is a call that inserts, modifies or deletes rows (create, update, update_all,
increment!, destroy, ...), an attribute assignment on a record (`x.status = ...`, later saved), or a
mass assignment through strong parameters (`update(conversation_params)`).  The receiver is
resolved to a model through a constant, `self`, the variable name or an association name; sites
whose receiver cannot be resolved keep the attribute names only.
"""
import re

import fsutil
import ruby
from rails_models import split_top, value, opts, underscore, call
from schema import singular

INSERT = {'create', 'create!', 'insert', 'insert!', 'insert_all', 'insert_all!', 'upsert', 'upsert_all',
          'find_or_create_by', 'find_or_create_by!', 'create_or_find_by', 'create_or_find_by!',
          'first_or_create', 'first_or_create!', 'create_with'}
MODIFY_HASH = {'update', 'update!', 'update_columns', 'update_all', 'assign_attributes', 'attributes='}
MODIFY_SYM = {'update_column', 'update_attribute', 'increment!', 'decrement!', 'toggle!', 'touch',
              'increment', 'decrement', 'toggle'}
COUNTER = {'increment_counter', 'decrement_counter', 'update_counters', 'reset_counters'}
DELETE = {'destroy', 'destroy!', 'delete', 'destroy_all', 'delete_all', 'destroy_by', 'delete_by',
          'really_destroy!'}
SAVE = {'save', 'save!'}
BUILD = {'new', 'build', 'create_with'}
OPS = INSERT | MODIFY_HASH | MODIFY_SYM | COUNTER | DELETE | SAVE
QUERY = {'where', 'find', 'find_by', 'find_by!', 'first', 'first!', 'last', 'all', 'find_each', 'includes',
         'joins', 'left_joins', 'order', 'limit', 'offset', 'lock', 'reload', 'unscoped', 'select', 'not',
         'or', 'distinct', 'preload', 'eager_load', 'reorder', 'rewhere', 'merge', 'readonly', 'kept',
         'with_deleted', 'unscope', 'none', 'try', 'tap', 'then', 'presence', 'each', 'map', 'in_batches',
         'find_in_batches', 'each_with_index', 'sole', 'take', 'take!', 'second', 'third', 'to_a', 'reverse',
         'group', 'having', 'from', 'extending', 'with', 'excluding', 'without', 'lock!', 'dup',
         'becomes', 'becomes!', 'object', 'record', 'compact', 'flatten', 'uniq', 'first_or_initialize',
         'find_or_initialize_by', 'scoping', 'unscoped', 'current', 'instance', 'class'}
PREFIXES = ('current_', 'new_', 'old_', 'existing_', 'original_', 'target_', 'source_', 'parent_',
            'the_', 'primary_', 'secondary_', 'other_', 'synced_', 'pending_', 'last_', 'first_')
TIMESTAMPS = {'updated_at', 'created_at'}

_CALL = re.compile(r'(?:(?<=\.)|(?<=&\.)|^|(?<=[\s(=!|&,{]))(%s)(?=\s*\(|\s+[:\w@"\'\[{*]|\s*$|\s*\{|\s+do\b)'
                   % '|'.join(re.escape(x) for x in sorted(OPS | BUILD, key=len, reverse=True)))
_ASSIGN = re.compile(r'(?<![\w.])((?:@?[a-z_]\w*|self)(?:\.[a-z_]\w*)*)\.([a-z_]\w*)\s*(\|\||&&|\+|-)?=(?![=~>])')
_INDEX_ASSIGN = re.compile(r'(?<![\w.])((?:@?[a-z_]\w*|self))\[\s*:(\w+)\s*\]\s*=(?![=~>])')
_WRITE_ATTR = re.compile(r'(?<![\w.])(?:self\.)?write_attribute\(\s*:(\w+)')


class Index:
    """Name lookup from Ruby expressions to models."""

    def __init__(self, models):
        self.models = models
        self.by_noun = {}
        for n, m in models.items():
            for key in {underscore(n.split('::')[-1]), singular(m.table or ''), underscore(n).replace('/', '_')}:
                if key:
                    self.by_noun.setdefault(key, set()).add(n)
        self.assoc = {}
        for n, m in models.items():
            for a in m.assocs:
                self.assoc[(n, a['name'])] = a

    def noun(self, word):
        w = word.lstrip('@').rstrip('?!')
        for p in PREFIXES:
            if w.startswith(p) and w[len(p):]:
                w2 = w[len(p):]
                if self.by_noun.get(w2) or self.by_noun.get(singular(w2)):
                    w = w2
                    break
        for cand in (w, singular(w)):
            hit = self.by_noun.get(cand)
            if hit and len(hit) == 1:
                return next(iter(hit))
            if hit:
                return sorted(hit, key=len)[0]
        parts = w.split('_')
        for k in range(1, len(parts)):
            suf = '_'.join(parts[k:])
            for cand in (suf, singular(suf)):
                hit = self.by_noun.get(cand)
                if hit:
                    return sorted(hit, key=len)[0]
        return None

    def const(self, name, ns):
        name = name.lstrip(':')
        parts = ns.split('::') if ns else []
        for k in range(len(parts), -1, -1):
            cand = '::'.join(parts[:k] + [name])
            if cand in self.models:
                return cand
        return None


def segments(expr):
    """Split a receiver chain at top-level dots, dropping arguments and blocks."""
    out, depth, cur = [], 0, []
    for ch in expr:
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
            continue
        if depth:
            continue
        if ch == '.':
            out.append(''.join(cur).strip().rstrip('&'))
            cur = []
        else:
            cur.append(ch)
    out.append(''.join(cur).strip().rstrip('&'))
    return [s for s in out if s]


def resolve(expr, idx, ctx, ns, local):
    """Model of a receiver expression, or None.  ctx is the enclosing model (for self and bare calls),
    local maps variable names to models found earlier in the method."""
    if not expr or expr == 'self':
        return ctx
    segs = segments(expr)
    prev = None
    model = None
    for s in segs:
        base = re.sub(r'\W+$', '', s).lstrip(':')
        if not base:
            continue
        if base[0].isupper():
            model = idx.const(base, ns) or model
            prev = model
            continue
        if base == 'self':
            model = prev = ctx
            continue
        if base == 'class' and prev is not None:
            continue
        if base in QUERY:
            continue
        if base in local:
            model = prev = local[base]
            continue
        if prev is not None and (prev, base) in idx.assoc:
            a = idx.assoc[(prev, base)]
            cn = a['opts'].get('class_name')
            tgt = idx.const(cn, ns) if isinstance(cn, str) else None
            tgt = tgt or idx.noun(base)
            model = prev = tgt
            continue
        hit = idx.noun(base)
        if hit:
            model = prev = hit
        elif ctx is not None and (ctx, base) in idx.assoc:
            model = prev = idx.noun(base)
    return model


def permit_lists(root):
    """Strong-parameter methods of a file: method name -> permitted attribute names."""
    out = {}
    arrays = {}
    for n in root.walk():
        if isinstance(n, ruby.Node) and n.kind == 'def':
            body = ' '.join(s.text for s in n.stmts())
            m = re.search(r'\.permit\((.*)\)', body, re.S)
            if m:
                attrs = set(re.findall(r'(?<![:\w]):(\w+)', m.group(1))) | set(re.findall(r'(\w+):(?!:)', m.group(1)))
                for ident in re.findall(r'(?<![:.\w])([a-z_]\w*)\b(?!:)', m.group(1)):
                    arrays.setdefault(n.name, set()).add(ident)
                out[n.name] = attrs
            elif re.fullmatch(r'\s*(\[.*\]|%i\[.*\])\s*(\.freeze)?\s*', body, re.S):
                out[n.name] = set(re.findall(r'(?<![:\w]):(\w+)|\b(\w+)\b', body)[0:0])
                out[n.name] = set(re.findall(r':(\w+)', body)) | set(re.findall(r'%i\[([^\]]*)\]', body)[0].split()
                                                                   if '%i[' in body else [])
    for k, refs in arrays.items():
        for r in refs:
            if r in out and r != k:
                out[k] |= out[r]
    return out


def _args_attrs(op, args, permits):
    parts = split_top(args.strip())
    if op in MODIFY_SYM:
        return [str(value(p)) for p in parts[:1] if p.startswith(':')] or (['updated_at'] if op == 'touch' else ['*'])
    if op in COUNTER:
        if op == 'update_counters':
            return [k for k in opts(parts)]
        return [str(value(p)) for p in parts[:1] if p.startswith(':')] or ['*']
    o = opts(parts)
    if o:
        return list(o)
    if len(parts) == 1:
        ident = re.match(r'^([a-z_]\w*)$', parts[0])
        if ident and ident.group(1) in permits:
            return sorted(permits[ident.group(1)]) or ['*']
        m = re.match(r'^params\.(?:require\([^)]*\)\.)?permit\((.*)\)$', parts[0])
        if m:
            return sorted(set(re.findall(r':(\w+)', m.group(1))) | set(re.findall(r'(\w+):(?!:)', m.group(1))))
    if not parts:
        return []
    return ['*']


def _call_args(code_tail, text_tail):
    """Arguments of a call starting right after its name, from the text with literals."""
    t = text_tail
    if t.lstrip().startswith('('):
        start = t.index('(') + 1
        depth = 1
        i = start
        q = None
        while i < len(t) and depth:
            ch = t[i]
            if q:
                if ch == '\\':
                    i += 2
                    continue
                if ch == q:
                    q = None
            elif ch in '"\'':
                q = ch
            elif ch in '([{':
                depth += 1
            elif ch in ')]}':
                depth -= 1
            i += 1
        return t[start:i - 1]
    t = re.split(r'\s+(?:if|unless|do)\b|\s*\{\s*\|', t, maxsplit=1)[0]
    return t.strip()


def scan(app_root, models, idx=None, dirs=None):
    """All write sites of the application."""
    return scan_all(app_root, models, idx)[0]


_CONST_CALL = re.compile(r'(?<![\w:])((?:::)?[A-Z]\w*(?:::[A-Z]\w*)*)(\.new\b(?:\((?:[^()]|\([^()]*\))*\))?)?'
                         r'(?:\.set\([^)]*\))?\.([a-z_]\w*[!?]?)')
_BARE_CALL = re.compile(r'(?<![.\w@:$])([a-z_]\w*[!?]?)(?![\w:])(?!\s*=[^=~>])')
ASYNC = {'perform_later', 'perform_async', 'perform_in', 'perform_at', 'deliver_later', 'delay'}
# Const.factory(...).meth: an instance method of Const reached through a factory method
_CONST_CHAIN = re.compile(r'(?<![\w:])((?:::)?[A-Z]\w*(?:::[A-Z]\w*)*)\.([a-z_]\w*)\((?:[^()]|\([^()]*\))*\)'
                          r'\.([a-z_]\w*[!?]?)')
# recv.meth on a receiver whose model is known: a method of that model, its base classes or concerns
_RECV_CALL = re.compile(r'(?<![\w:.@$])(@?[a-z_]\w*)&?\.([a-z_]\w*[!?]?)(?![\w])(?!\s*=[^=~>])')
_IVAR_NEW = re.compile(r'^(@[a-z_]\w*)\s*(?:\|\|)?=(?![=~>])\s*(.+)$')


def scan_all(app_root, models, idx=None):
    """Write sites, method definitions (cls, name) -> (file, start, end), and calls between them.
    A call is a dict with the caller's file, line, cls, meth and txn, and the callee (cls, name, async)."""
    idx = idx or Index(models)
    sites, defs, calls = [], {}, []
    files = list(fsutil.walk(app_root, ('.rb',), fsutil.SKIP_DIRS | {'db', 'config', 'script', 'bin', 'seeders', 'seeds',
                                                       'tasks', 'generators', 'dev', 'benchmarks', 'samples'}))
    for p in files:
        relp = fsutil.rel(p, app_root)
        root, ok = ruby.read(p)
        permits = permit_lists(root) if '/controllers/' in '/' + relp else {}
        # instance variables that some method of the class sets to a new record (@group = Group.new)
        class_new = {}
        for d in [n for n in root.walk() if isinstance(n, ruby.Node) and n.kind == 'def']:
            cls0 = _enclosing_class(d)
            ns0 = cls0.rsplit('::', 1)[0] if cls0 and '::' in cls0 else (cls0 or '')
            for st in d.stmts():
                mm = _IVAR_NEW.match(st.text)
                if mm and re.search(r'\.(new|build)\b', mm.group(2)):
                    mod = resolve(re.split(r'\s*(?:\|\||&&|\?|\bif\b|\bunless\b)\s*', mm.group(2), maxsplit=1)[0],
                                  idx, cls0 if cls0 in models else None, ns0, {})
                    if mod:
                        class_new.setdefault(cls0, {})[mm.group(1)] = mod
                        class_new[cls0]['__new__' + mm.group(1)] = True
        for d in [n for n in root.walk() if isinstance(n, ruby.Node) and n.kind in ('def', 'block')]:
            if d.kind == 'block' and (_enclosing(d, 'def') is not None or d.parent.kind == 'block'):
                continue      # blocks inside methods are handled with the method
            cls = _enclosing_class(d)
            ctx = cls if cls in models else None
            ns = cls.rsplit('::', 1)[0] if cls and '::' in cls else (cls or '')
            meth = d.name if d.kind == 'def' else ('block:' + d.head.text[:40])
            if d.kind == 'def':
                if (cls, d.name) in defs and defs[(cls, d.name)][0] != relp:
                    # a prepended override and the method it wraps: the first calls the second
                    f0, a0, _ = defs[(cls, d.name)]
                    k = 1
                    while (cls, d.name + '^' * k) in defs:
                        k += 1
                    defs[(cls, d.name + '^' * k)] = (relp, d.line, d.end)
                    calls.append(dict(file=f0, line=a0, cls=cls, meth=d.name, txn=None, ns=ns,
                                      callee_const=None, callee=d.name + '^' * k, inst=True, async_=False))
                else:
                    defs.setdefault((cls, d.name), (relp, d.line, d.end))
            local = dict(class_new.get(cls, {}))
            lvars = _params(d.head.code) if d.kind == 'def' else set()
            for st, txn, cb in _stmts_with_context(d):
                found = _sites_in(st, idx, ctx, ns, local, permits, relp, cls, meth, txn, cb)
                mod = re.search(r'\s(?:if)\s+(.+)$', st.code)
                g = set(cb or ()) | (change_guards(mod.group(1)) if mod else set())
                if g:
                    for x in found:
                        x['guard'] = sorted(g)
                for x in found:
                    if x['recv'] not in ('', 'self') and x['kind'] in ('assign', 'modify'):
                        x['lvars'] = frozenset(lvars)     # a bare name in its value may be a local
                lvars |= _locals(st.code)
                sites.extend(found)
                if d.kind == 'def' and st is d.head:
                    continue
                for cm in _CONST_CALL.finditer(st.code):
                    const, new, m2 = cm.group(1).lstrip(':'), cm.group(2), cm.group(3)
                    if m2 in ASYNC:
                        calls.append(dict(file=relp, line=st.line, cls=cls, meth=meth, txn=txn, ns=ns,
                                          callee_const=const, callee='perform', inst=True, async_=True))
                    elif m2 not in OPS and m2 not in QUERY and m2 not in BUILD:
                        calls.append(dict(file=relp, line=st.line, cls=cls, meth=meth, txn=txn, ns=ns,
                                          callee_const=const, callee=m2 if new else 'self.' + m2,
                                          inst=bool(new), async_=False))
                for bm in _BARE_CALL.finditer(st.code):
                    calls.append(dict(file=relp, line=st.line, cls=cls, meth=meth, txn=txn, ns=ns,
                                      callee_const=None, callee=bm.group(1), inst=True, async_=False))
                for cm in _CONST_CHAIN.finditer(st.code):
                    if cm.group(2) != 'new' and cm.group(3) not in OPS and cm.group(3) not in QUERY:
                        calls.append(dict(file=relp, line=st.line, cls=cls, meth=meth, txn=txn, ns=ns,
                                          callee_const=cm.group(1).lstrip(':'), callee=cm.group(3), inst=True,
                                          async_=False))
                for rm in _RECV_CALL.finditer(st.code):
                    recv, m2 = rm.group(1), rm.group(2)
                    if m2 in OPS or m2 in QUERY or m2 in BUILD or m2 in ASYNC:
                        continue
                    if recv == 'self':
                        calls.append(dict(file=relp, line=st.line, cls=cls, meth=meth, txn=txn, ns=ns,
                                          callee_const=None, callee=m2, inst=True, async_=False))
                        continue
                    rmod = resolve(recv, idx, ctx, ns, local)
                    if rmod:
                        calls.append(dict(file=relp, line=st.line, cls=cls, meth=meth, txn=txn, ns=ns,
                                          callee_const=None, callee=m2, recv_model=rmod, inst=True, async_=False))
    # keep only calls whose callee is a known definition
    known = {}
    for (c, n) in defs:
        known.setdefault(n, set()).add(c)
    out = []
    for c in calls:
        if c.get('recv_model'):
            for k in _ancestry(c['recv_model'], models):
                if (k, c['callee']) in defs:
                    c['target'] = (k, c['callee'])
                    out.append(c)
                    break
            continue
        if c['callee_const'] is None:
            if (c['cls'], c['callee']) in defs and (c['callee'] != c['meth'] or c['callee'].endswith('^')):
                c['target'] = (c['cls'], c['callee'])
                out.append(c)
            continue
        parts = c['ns'].split('::') if c['ns'] else []
        for k in range(len(parts), -1, -1):
            cand = '::'.join(parts[:k] + [c['callee_const']])
            if (cand, c['callee']) in defs:
                c['target'] = (cand, c['callee'])
                out.append(c)
                break
            if c['callee'] == 'self.call' and (cand, 'call') in defs:
                c['target'] = (cand, 'call')
                out.append(c)
                break
    return sites, defs, out


def _enclosing(node, kind):
    n = node.parent
    while n is not None:
        if n.kind == kind:
            return n
        n = n.parent
    return None


def _ancestry(name, models):
    out, seen = [], set()
    while name and name in models and name not in seen:
        seen.add(name)
        out.append(name)
        out.extend(i.lstrip(':') for i in models[name].includes)
        name = models[name].sti_base
    return out


def _enclosing_class(node):
    n = node.parent
    names = []
    while n is not None and n.kind != 'file':
        if n.kind in ('class', 'module'):
            names.append(n.name.lstrip(':'))
        n = n.parent
    names = list(reversed(names))
    # EE::X and Enterprise::X are prepended to X: their methods are methods of X
    if len(names) > 1 and names[0] in PREPENDED:
        names = names[1:]
    elif names and '::' in names[0] and names[0].split('::', 1)[0] in PREPENDED:
        names[0] = names[0].split('::', 1)[1]
    return '::'.join(names) or None


PREPENDED = {'EE', 'Enterprise', 'JH'}


_TXN = re.compile(r'(?:^|[\s.])(transaction|with_lock|lock!|with_advisory_lock\w*|atomic|'
                  r'temporary_ignore_tables_in_transaction|allow_cross_database_modification_within_transaction)\b')


_CHANGE = re.compile(r'(?:\bsaved_change_to_|\bwill_save_change_to_)(\w+?)\?|\b(\w+?)_(?:previously_)?changed\?'
                     r'|\b(?:saved_change_to_attribute|attribute_changed|will_save_change_to_attribute)\?\(\s*:(\w+)')


def change_guards(cond):
    """Attributes whose change a condition tests (`if root_account_id_changed?`)."""
    out = set()
    for m in _CHANGE.finditer(cond):
        a = m.group(1) or m.group(2) or m.group(3)
        if a and a not in ('attribute', 'saved', 'will_save'):
            out.add(a)
    return out


def _stmts_with_context(node, txn=None, guards=frozenset()):
    """Statements of a method or block with the id of the innermost enclosing transaction block
    and the attributes whose change an enclosing `if` tests."""
    if txn is None and node.kind == 'block' and node.head is not None and _TXN.search(node.head.code):
        txn = '%d' % node.line      # nested transaction blocks join the outermost one
    if node.kind == 'ctrl' and node.head is not None and re.match(r'^(?:\w+\s*=\s*)?if\b', node.head.code):
        g = change_guards(node.head.code)
        if g:
            guards = guards | g
    for c in node.children:
        if isinstance(c, ruby.Stmt):
            yield c, txn, guards
            # `return unless x_changed?`: the rest of the body runs only if x changed
            mm = re.match(r'^return\s+unless\s+(.+)$', c.code.strip())
            if mm and node.kind == 'def':
                g = change_guards(mm.group(1))
                if g:
                    guards = guards | g
        else:
            if c.head is not None:
                yield c.head, txn, guards
            yield from _stmts_with_context(c, txn, guards)


_DEF_PARAMS = re.compile(r'^\s*(?:\w+\s+)*def\s+(?:self\.)?[\w?!=\[\]+\-*/<>~%]+(.*)$')
_LOCAL_ASSIGN = re.compile(r'^\s*([a-z_]\w*(?:\s*,\s*\*?[a-z_]\w*)*)\s*(?:\|\||&&|\+|-|\*)?=(?![=~>])')
_BLOCK_PARAMS = re.compile(r'(?:\bdo|\{)\s*\|([^|]*)\|')


def _params(head):
    """Parameter names of a def head (over-approximated: names inside default values count too)."""
    m = _DEF_PARAMS.match(head)
    return set(re.findall(r'(?:^|[(,])\s*[*&]{0,2}([a-z_]\w*)', m.group(1).strip())) if m else set()


def _locals(code):
    """Local variables that a statement introduces: assignments and block parameters."""
    out = set()
    m = _LOCAL_ASSIGN.match(code)
    if m:
        out |= set(re.findall(r'[a-z_]\w*', m.group(1)))
    for bm in _BLOCK_PARAMS.finditer(code):
        out |= set(re.findall(r'[a-z_]\w*', bm.group(1)))
    return out


def _sites_in(st, idx, ctx, ns, local, permits, relp, cls, meth, txn, cb):
    out = []
    code, text = st.code, st.text
    # local variable types: x = Model.new / assoc.build / Model.find(...) / Model.create
    m = re.match(r'^(@?[a-z_]\w*)\s*(?:\|\|)?=(?![=~>])\s*(.+)$', text)
    if m:
        rhs = m.group(2)
        mod = resolve(re.split(r'\s*(?:\|\||&&|\?|\bif\b|\bunless\b)\s*', rhs, maxsplit=1)[0], idx, ctx, ns, local)
        if mod:
            local[m.group(1)] = mod
            if re.search(r'\.(new|build)\b', rhs):
                local['__new__' + m.group(1)] = True
        # x = assoc: x stands for the association (`a = assignment`)
        am = re.match(r'^(?:self\.)?([a-z_]\w*)\s*$', rhs)
        if am:
            local['__src__' + m.group(1)] = am.group(1)
        else:
            local.pop('__src__' + m.group(1), None)
    for mm in _CALL.finditer(code):
        op = mm.group(1)
        pre = text[:mm.start()].rstrip()
        recv = ''
        if pre.endswith('.'):
            rm = re.search(r'((?:[@\w:]+(?:\([^()]*(?:\([^()]*\)[^()]*)*\))?|\]|\))(?:&?\.[\w!?]+(?:\([^()]*(?:\([^()]*\)[^()]*)*\))?)*)\s*&?\.$', pre)
            recv = rm.group(1) if rm else '?'
        elif op in BUILD:
            continue
        args = _call_args(code[mm.end():], text[mm.end():])
        if op in BUILD:
            continue
        model = resolve(recv, idx, ctx, ns, local) if recv != '?' else None
        if op in SAVE:
            var = recv.lstrip('@') if recv else 'self'
            kind = 'insert' if local.get('__new__' + recv) else 'save'
            out.append(_site(relp, st, cls, meth, model, kind, [], 'save', txn, recv, ctx=ctx))
            continue
        if op in INSERT:
            kind, attrs = 'insert', _args_attrs('create', args, permits) if args else []
        elif op in DELETE:
            kind, attrs = 'delete', []
        elif op in COUNTER:
            kind, attrs = 'modify', _args_attrs(op, args, permits)
        else:
            kind, attrs = 'modify', _args_attrs(op, args, permits)
        if not recv and op in ('update', 'update!', 'destroy', 'delete', 'touch', 'toggle!', 'increment!',
                               'decrement!', 'update_column', 'update_columns', 'update_attribute'):
            model = ctx
        if not recv and ctx is None and op not in COUNTER:
            continue       # bare call outside a model: a helper of the same name
        out.append(_site(relp, st, cls, meth, model, kind, attrs, op, txn, recv, args, ctx))
    for am in _ASSIGN.finditer(code):
        recv, attr = am.group(1), am.group(2)
        if attr in ('id',) or recv in ('params', 'session', 'response', 'request', 'headers', 'cookies'):
            continue
        model = resolve(recv, idx, ctx, ns, local)
        rhs = code[am.end():]
        out.append(_site(relp, st, cls, meth, model, 'assign', [attr], (am.group(3) or '') + '=', txn, recv,
                         text[am.end():am.end() + 200].strip(), ctx))
    for am in _INDEX_ASSIGN.finditer(code):
        recv, attr = am.group(1), am.group(2)
        if recv in ('params', 'session', 'cookies', 'headers'):
            continue
        out.append(_site(relp, st, cls, meth, resolve(recv, idx, ctx, ns, local), 'assign', [attr], '[]=',
                         txn, recv, ctx=ctx))
    for am in _WRITE_ATTR.finditer(code):
        out.append(_site(relp, st, cls, meth, ctx, 'assign', [am.group(1)], 'write_attribute', txn, 'self',
                         ctx=ctx))
    for x in out:
        if '__src__' + x['recv'] in local:
            x['recv_src'] = local['__src__' + x['recv']]
    return out


def _site(relp, st, cls, meth, model, kind, attrs, op, txn, recv, args='', ctx=None):
    return {'file': relp, 'line': st.line, 'cls': cls, 'meth': meth, 'model': model, 'kind': kind,
            'args': args[:300], 'ctx': ctx,
            'attrs': [a for a in attrs if a not in TIMESTAMPS] if attrs != ['*'] else attrs,
            'op': op, 'txn': txn, 'recv': recv, 'text': st.text[:200]}


def mutability(sites, models, schema):
    """Per table: attributes written by resolved sites (precise) and by unresolved sites with the
    attribute name only (candidates)."""
    precise, candidate = {}, {}
    cols_by_name = {}
    for t, cols in schema.tables.items():
        for c in cols:
            cols_by_name.setdefault(c, set()).add(t)
    for s in sites:
        if s['kind'] in ('insert', 'delete', 'save'):
            continue
        attrs = s['attrs']
        if s['model'] and s['model'] in models:
            t = models[s['model']].table
            for a in attrs:
                if a == '*':
                    precise.setdefault(t, set()).add('*')
                elif t in schema.tables and a in schema.tables[t]:
                    precise.setdefault(t, set()).add(a)
                elif t in schema.tables and a + '_id' in schema.tables[t]:
                    precise.setdefault(t, set()).add(a + '_id')
        elif s['recv'] not in ('self', '') and not s['recv'].startswith('?'):
            for a in attrs:
                for col in (a, a + '_id'):
                    for t in cols_by_name.get(col, ()):
                        candidate.setdefault(t, set()).add(col)
    return precise, candidate
