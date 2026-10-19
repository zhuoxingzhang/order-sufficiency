"""Copies and derived values of a Rails application.

A copy is an IND R[P D] <= S[L C] with nonempty D: the row of R stores attributes of the row of S
that its pointer P references.  Evidence, strongest first:
  assigned   a model callback or association default sets self.d from assoc.c (`self.account_id =
             conversation.account_id`, `belongs_to :account, default: -> { conversation.account }`)
  created    a write site passes d: v.c for a record v of the referenced model
  triangle   R and S reference the same table T through a column of the same name, and R
             references S: R[fk, t_id] <= S[id, t_id] (the tenant columns of multi-tenant schemas)
  reverse    a method or callback of R writes an attribute c of the row of S that its belongs_to
             references from an attribute d of R itself and saves it (`a = assignment; a.title =
             title; a.save`, `user.update!(uploaded_avatar_id: gravatar_upload_id)`): the same IND,
             kept by writes that flow from R into S (flow = 'reverse')
A derived value is an attribute of S computed from the rows of R that reference it:
  counter    counter_cache / counter_culture, or a counter update in a callback of R
  callback   a callback of R that modifies attributes of the referenced row of S
"""
import re

from rails_models import call, opts, split_top, value
from schema import plural

CB_SAME_TXN = re.compile(r'^(before|after|around)_(validation|save|create|update|destroy|initialize|touch)$')


def _assoc_fk(m, name, models):
    """(fk column, target model) of a belongs_to association of m."""
    for a in m.assocs:
        if a['kind'] == 'belongs_to' and a['name'] == name and not a['opts'].get('polymorphic'):
            cn = a['opts'].get('class_name')
            from rails_models import _resolve, classify
            ns = m.name.rsplit('::', 1)[0] if '::' in m.name else ''
            tgt = _resolve(cn if isinstance(cn, str) else classify(name, False), ns, set(models))
            return str(a['opts'].get('foreign_key') or name + '_id'), tgt
    return None, None


def _attr_col(model, attr, schema_cols):
    """Column for an attribute name or a belongs_to association name."""
    if attr in schema_cols:
        return attr
    if attr + '_id' in schema_cols:
        return attr + '_id'
    return None


def copies(models, refs, sites, schema):
    out = {}

    def add(R, P, S, d, c, ev, where):
        if c in schema.pk.get(S, ('id',)):
            return          # d stores the key of the referenced row: a second reference, not a copy
        key = (R, P, S, d, c)
        if key not in out or _rank(ev) < _rank(out[key]['evidence']):
            out[key] = dict(table=R, fk=P, ref=S, d=d, c=c, evidence=ev, where=where)
            if ev == 'reverse':
                out[key]['flow'] = 'reverse'

    by_table = {}
    for m in models.values():
        by_table.setdefault(m.table, []).append(m)
    # assigned: callbacks and association defaults
    for m in models.values():
        R = m.table
        rcols = schema.tables.get(R, [])
        for a in m.assocs:
            dflt = a['opts'].get('default')
            if a['kind'] == 'belongs_to' and isinstance(dflt, str):
                mm = re.search(r'\{\s*(\w+)&?\.(\w+)\s*\}', dflt)
                if mm:
                    fk, tgt = _assoc_fk(m, mm.group(1), models)
                    if tgt:
                        S = models[tgt].table
                        c = _attr_col(tgt, mm.group(2), schema.tables.get(S, []))
                        d = str(a['opts'].get('foreign_key') or a['name'] + '_id')
                        if c and d in rcols:
                            add(R, fk, S, d, c, 'assigned', '%s:%d' % (m.file, a['line']))
        for s in sites:
            if s['model'] != m.name or s['kind'] != 'assign' or s['recv'] != 'self':
                continue
            rhs = s['args']
            mm = re.match(r'^\s*(\w+)&?\.(\w+)\b(?![(.?])', rhs) or \
                re.match(r'^\s*(\w+)\.try\(:(\w+)\)', rhs)
            if not mm:
                continue
            fk, tgt = _assoc_fk(m, mm.group(1), models)
            if not tgt:
                continue
            S = models[tgt].table
            c = _attr_col(tgt, mm.group(2), schema.tables.get(S, []))
            d = _attr_col(m.name, s['attrs'][0], rcols)
            if c and d and d != fk:
                add(R, fk, S, d, c, 'assigned', '%s:%d' % (s['file'], s['line']))
    # created: d: v.c with v of the referenced model
    for s in sites:
        if s['kind'] not in ('insert', 'modify') or not s['model'] or s['model'] not in models:
            continue
        m = models[s['model']]
        R = m.table
        rcols = schema.tables.get(R, [])
        for k, v in opts(split_top(s['args'])).items():
            if not isinstance(v, str):
                continue
            mm = re.match(r'^@?(\w+)&?\.(\w+)$', v)
            if not mm:
                continue
            d = _attr_col(m.name, k, rcols)
            if not d:
                continue
            for r in refs:
                if r['table'] != R or r['poly'] or not r['ref'] or r['cols'][0] == d:
                    continue
                if r['kind'] != 'belongs_to':
                    continue
                tgt = r['target']
                if not tgt:
                    continue
                var = mm.group(1)
                from schema import singular
                if var not in (r['assoc'], singular(models[tgt].table)) and not var.endswith('_' + r['assoc']):
                    continue
                c = _attr_col(tgt, mm.group(2), schema.tables.get(r['ref'], []))
                if c:
                    add(R, r['cols'], r['ref'], d, c, 'created', '%s:%d' % (s['file'], s['line']))
    # triangle
    fk_target = {}
    for r in refs:
        if r['kind'] == 'belongs_to' and r['ref'] and not r['poly']:
            fk_target[(r['table'], r['cols'][0])] = r['ref']
    for f in schema.fks:
        fk_target.setdefault((f['table'], f['cols'][0]), f['ref'])
    for (R, col), S in list(fk_target.items()):
        for (R2, col2), T in fk_target.items():
            if R2 != R or col2 == col:
                continue
            if fk_target.get((S, col2)) == T:
                add(R, (col,), S, col2, col2, 'triangle', '')
    for R, fk, S, d, c, where in reverse(models, sites, schema):
        add(R, fk, S, d, c, 'reverse', where)
    res = []
    for (R, P, S, d, c), v in out.items():
        v['fk'] = tuple(P) if not isinstance(P, str) else (P,)
        res.append(v)
    return res


def _rank(ev):
    return {'assigned': 0, 'created': 1, 'pushed': 1, 'reverse': 1, 'triangle': 2}[ev]


REV_UPDATE = {'update', 'update!', 'update_columns', 'update_attributes', 'update_attributes!', 'assign_attributes'}
REV_SYM = {'update_column', 'update_attribute'}


def _own_col(s, rhs, m, cols):
    """Column of the enclosing model m that the value rhs of a write reads: `self.x`, or in Ruby a
    bare `x` that is no local variable of the method."""
    raw = s.get('raw')
    if raw is not None:         # Django: the unstripped value; a bare name is a local in Python
        mm = re.match(r'^self\.(\w+)$', raw.strip())
    else:
        rhs = re.split(r'\s+(?:if|unless)\s+', rhs or '', maxsplit=1)[0].strip()
        mm = re.match(r'^(self\.)?([a-z_]\w*)$', rhs)
        if mm and not mm.group(1) and mm.group(2) in s.get('lvars', ()):
            return None
        mm = mm and re.match(r'^(?:self\.)?(\w+)$', rhs)
    return _attr_col(m, mm.group(1), cols) if mm else None


def _assoc_base(s):
    """The association name that the receiver of a site stands for (`user`, `self.user`, or a local
    alias `a = assignment`)."""
    r = s.get('recv_src') or s.get('recv') or ''
    r = r[5:] if r.startswith('self.') else r.lstrip('@')
    return r if re.fullmatch(r'[a-z_]\w*', r) else None


def reverse(models, sites, schema):
    """Reverse copies (R, fk, S, d, c, where): a method or callback of model R writes S.c := R.d on the
    row of S that the belongs_to fk of R references, and saves it.  The writing site (the update, or
    the save that follows the assignments) is marked rcopy = {c: (R, d, fk)}, so that a template
    counts the write only when it changes R.d, moves fk, or inserts or deletes the row of R."""
    by_meth = {}
    for s in sites:
        by_meth.setdefault((s['file'], s['cls'], s['meth']), []).append(s)
    out = []
    for ss in by_meth.values():
        ctx = ss[0].get('ctx')
        if not ctx or ctx not in models or not models[ctx].table or ss[0]['meth'].startswith('self.'):
            continue        # outside an instance method of a model (class methods have no own row)
        m = models[ctx]
        R = m.table
        rcols = schema.tables.get(R, [])
        found = []          # (site, c, d) of writes S.c := R.d
        for s in ss:
            base = _assoc_base(s)
            if not base or s['recv'] in ('', 'self') or s['kind'] not in ('assign', 'modify'):
                continue
            fk, tgt = _assoc_fk(m, base, models)
            if not tgt or not models[tgt].table or models[tgt].table == R:
                continue
            S = models[tgt].table
            scols = schema.tables.get(S, [])
            pairs = []
            if s['kind'] == 'assign':
                pairs = [(s['attrs'][0], s.get('args', ''))]
            elif s['op'] in REV_UPDATE:
                pairs = [(k, v) for k, v in opts(split_top(s.get('args', ''))).items() if isinstance(v, str)]
            elif s['op'] in REV_SYM:
                parts = split_top(s.get('args', ''))
                if len(parts) == 2 and parts[0].startswith(':'):
                    pairs = [(parts[0][1:], parts[1])]
            for attr, rhs in pairs:
                c = _attr_col(tgt, attr, scols)
                d = _own_col(s, rhs, ctx, rcols)
                if not c or not d or d == fk or d in schema.pk.get(R, ('id',)) or c in schema.pk.get(S, ('id',)) \
                        or c in SKIP_PUSHED or d in SKIP_PUSHED:
                    continue
                found.append((s, base, R, fk, S, d, c))
        for s, base, R, fk, S, d, c in found:
            if s['kind'] == 'assign':
                # assigned on the association, then saved: mark every later save of it
                saves = [t for t in ss if t['op'] in ('save', 'save!') and t['line'] >= s['line']
                         and _assoc_base(t) == base]
                if not saves:
                    continue
            else:
                saves = [s]
            for t in saves:
                t.setdefault('rcopy', {})[c] = (R, d, (fk,))
            out.append((R, fk, S, d, c, '%s:%d' % (s['file'], s['line'])))
    return out


SKIP_PUSHED = {'updated_at', 'created_at', 'date_updated', 'date_added', 'modified', 'created', 'updated',
               'last_modified', 'lock_version'}


def pushed(templates, refs, schema):
    """Copies kept by their source: a callback of S, run when a row of S is updated, writes an
    attribute a of a row of R that references S, and S has a column a as well, so R[P a] <= S[L a]
    (the replicas of Sentry, which the outbox of the source keeps)."""
    ptr, pointers = {}, {}
    for r in refs:
        if r['ref'] and r['table'] != r['ref']:
            ptr.setdefault((r['table'], r['ref']), tuple(r['cols']))
        pointers.setdefault(r['table'], set()).update(r['cols'])
    out = {}
    for tp in templates:
        for w in tp['writes']:
            trig = w.get('trigger')
            if w['op'] not in ('insert', 'modify') or not trig or trig[1] != 'update':
                continue
            S, R = trig[0], w['table']
            P = ptr.get((R, S))
            if not P:
                continue
            scols = set(schema.tables.get(S, []))
            prefix = P[0][:-3] + '_' if P[0].endswith('_id') else None
            for a in w['attrs']:
                if a in pointers.get(R, set()) or a in SKIP_PUSHED:
                    continue
                # the same column, or the column prefixed by the reference (user_email <- user.email)
                c = a if a in scols else (a[len(prefix):] if prefix and a.startswith(prefix) else None)
                if c and c in scols and c not in schema.pk.get(S, ('id',)) and c not in SKIP_PUSHED:
                    out.setdefault((R, P, S, a), dict(table=R, fk=P, ref=S, d=a, c=c, evidence='pushed',
                                                      where=w.get('via') or ''))
    return list(out.values())


def derived(models, refs, sites, schema):
    """Derived attributes: holder table S, columns, source table R and its pointer."""
    out = []
    for r in refs:
        if r['kind'] != 'belongs_to' or not r['ref']:
            continue
        cc = r['opts'].get('counter_cache')
        if cc:
            col = cc if isinstance(cc, str) else r['table'] + '_count'
            if isinstance(cc, dict):
                col = cc.get('column', r['table'] + '_count')
            out.append(dict(table=r['ref'], cols=(str(col),), src=r['table'], fk=r['cols'], kind='counter',
                            where='%s' % r['model']))
    for m in models.values():
        if m.abstract:
            continue
        for a in m.assocs:
            if a['kind'] == 'counter_culture':
                fk, tgt = _assoc_fk(m, a['name'], models)
                if tgt:
                    cc = a['opts'].get('column_name')
                    col = cc if isinstance(cc, str) else m.table + '_count'
                    out.append(dict(table=models[tgt].table, cols=(col,), src=m.table, fk=(fk,),
                                    kind='counter', where=m.name))
    return out


def callback_ranges(m):
    """(event, deferred, file, start, end) of the callbacks of model m, following one level of calls."""
    out = []
    for cb in m.callbacks:
        kind = cb['kind']
        ev = re.match(r'^(before|after|around)_(\w+)$', kind).group(2)
        on = cb['opts'].get('on')
        if ev in ('validation', 'save', 'commit', 'rollback', 'touch', 'initialize', 'find'):
            if ev == 'commit' and on:
                events = [on] if isinstance(on, str) else list(on)
            elif ev == 'commit':
                events = ['create', 'update', 'destroy']
            elif ev in ('validation', 'save'):
                events = [on] if isinstance(on, str) else (list(on) if on else ['create', 'update'])
            else:
                continue
        elif ev.endswith('_commit'):
            events = [ev[:-7]]
        else:
            events = [ev]
        deferred = 'commit' in ev
        if cb.get('node') is not None:
            n = cb['node']
            out.append((events, deferred, cb['file'], n.line, n.end, kind))
        elif cb.get('target') and cb['target'] in m.methods:
            for name in _closure(m, cb['target']):
                n = m.methods[name]
                out.append((events, deferred, m.method_file.get(name), n.line, n.end, kind))
    return out


def _closure(m, name, depth=3):
    """A method and the methods of the same model that it calls, up to depth."""
    seen = [name]
    frontier = [name]
    for _ in range(depth):
        nxt = []
        for f in frontier:
            body = ' '.join(s.code for s in m.methods[f].stmts())
            for w in set(re.findall(r'(?<![.\w:@])([a-z_]\w*[?!]?)', body)):
                if w in m.methods and w not in seen and not w.startswith('self.'):
                    seen.append(w)
                    nxt.append(w)
        frontier = nxt
    return seen
