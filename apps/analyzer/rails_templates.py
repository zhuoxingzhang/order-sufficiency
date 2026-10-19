"""Write templates of a Rails application.

  event        saving or destroying one model: its callbacks (before/after/around, in the same
               transaction; after_commit ones are marked deferred), dependent cascades and
               counter caches
  transaction  a transaction or with_lock block: its writes, each expanded by its model event
  method       a method that writes several tables without a transaction block: its writes, each
               expanded by its model event (the writes commit one after another)
Writes inside called methods count as well: methods of the same class, `Const.new(...).meth`,
`Const.meth`, and jobs started with perform_later (deferred), up to three calls deep.
Only templates that write at least two tables are kept.
"""
import bisect
import re

from rails_constraints import callback_ranges
from rails_writes import change_guards

NO_CALLBACKS = {'update_all', 'delete_all', 'insert_all', 'insert_all!', 'upsert_all', 'update_column',
                'update_columns', 'delete', 'delete_by', 'increment_counter', 'decrement_counter',
                'update_counters', 'insert', 'insert!', 'upsert'}
EVENT = {'insert': 'create', 'modify': 'update', 'delete': 'destroy'}
MAX_DEPTH = 3


def dedupe(ws):
    seen, out = set(), []
    for w in ws:
        k = (w['table'], w['op'], tuple(sorted(w['attrs'])), w.get('via'), bool(w.get('deferred')),
             w.get('trigger'), w.get('rsync'))
        if k not in seen:
            seen.add(k)
            out.append(w)
    return out


class Builder:
    def __init__(self, models, refs, sites, schema, defs=None, calls=None):
        self.models, self.refs, self.sites, self.schema = models, refs, sites, schema
        self.defs = defs or {}
        self.by_file = {}
        for s in sites:
            self.by_file.setdefault(s['file'], []).append(s)
        for v in self.by_file.values():
            v.sort(key=lambda s: s['line'])
        self.lines = {f: [s['line'] for s in v] for f, v in self.by_file.items()}
        self.calls_by_file = {}
        for c in calls or []:
            self.calls_by_file.setdefault(c['file'], []).append(c)
        for v in self.calls_by_file.values():
            v.sort(key=lambda c: c['line'])
        self.call_lines = {f: [c['line'] for c in v] for f, v in self.calls_by_file.items()}
        self.calls = calls or []
        self.colmap = {}
        for n, m in models.items():
            cols = set(schema.tables.get(m.table, []))
            mp = {}
            for a in m.assocs:
                if a['kind'] == 'belongs_to':
                    fk = str(a['opts'].get('foreign_key') or a['name'] + '_id')
                    mp[a['name']] = fk
            for c in cols:
                mp[c] = c
            self.colmap[n] = mp
        self._event_cache = {}
        self._range_cache = {}

    # ------------------------------------------------------------------ sites in code ranges
    def in_range(self, f, a, b):
        if f not in self.by_file:
            return []
        L = self.lines[f]
        i, j = bisect.bisect_left(L, a), bisect.bisect_right(L, b)
        return self.by_file[f][i:j]

    def calls_in_range(self, f, a, b):
        if f not in self.calls_by_file:
            return []
        L = self.call_lines[f]
        i, j = bisect.bisect_left(L, a), bisect.bisect_right(L, b)
        return self.calls_by_file[f][i:j]

    def range_sites(self, f, a, b, depth=0, seen=()):
        """Sites in lines a..b of file f and in the methods called from there, in program order,
        as (site, deferred)."""
        key = (f, a, b, depth)      # deeper calls follow fewer methods: cache per depth
        if key in self._range_cache:
            return self._range_cache[key]
        out = [(s, False) for s in self.in_range(f, a, b)]
        if depth < MAX_DEPTH:
            for c in self.calls_in_range(f, a, b):
                t = c['target']
                if t in seen or t not in self.defs:
                    continue
                cf, ca, cb = self.defs[t]
                for s, dfr in self.range_sites(cf, ca, cb, depth + 1, seen + (t,)):
                    out.append((s, dfr or c['async_']))
        out.sort(key=lambda x: (x[0]['file'] != f, x[0]['line']))
        self._range_cache[key] = out
        return out

    # ------------------------------------------------------------------ writes
    def column(self, model, attr):
        if attr in ('*', '?'):
            return attr
        mp = self.colmap.get(model, {})
        return mp.get(attr) or mp.get(attr + '_id') or attr

    def sync_source(self, model, rhs):
        """(table, column, pointer) if rhs reads a column of a belongs_to association of model:
        the assignment re-copies that column and changes nothing unless it changes."""
        m = re.match(r'^\s*(\w+)&?\.(\w+)\b(?![(.?!])', rhs or '') or re.match(r'^\s*(\w+)\.try\(:(\w+)\)', rhs or '')
        if not m:
            return None
        for a in self.models[model].assocs:
            if a['kind'] == 'belongs_to' and a['name'] == m.group(1) and not a['opts'].get('polymorphic'):
                fk = str(a['opts'].get('foreign_key') or a['name'] + '_id')
                for r in self.refs:
                    if r['model'] == model and r['assoc'] == a['name'] and r['ref'] and r['target']:
                        return (r['ref'], self.column(r['target'], m.group(2)), fk)
        return None

    def write_of(self, s, pending):
        """Turn a site into a write (table, op, attrs) or None."""
        model = s['model']
        if not model or model not in self.models or self.models[model].abstract:
            return None
        t = self.models[model].table
        kind = s['kind']
        attrs = {self.column(model, a) for a in s['attrs']}
        if kind == 'assign':
            if s['recv'] != 'self':
                pending.setdefault((s['recv'], model), set()).update(attrs)
                return None
            return dict(table=t, op='modify', attrs=attrs, model=model)
        if kind == 'save':
            got = pending.pop((s['recv'], model), set())
            return dict(table=t, op='modify', attrs=got or {'*'}, model=model)
        if kind == 'insert':
            return dict(table=t, op='insert', attrs=attrs, model=model)
        if kind == 'delete':
            return dict(table=t, op='delete', attrs=set(), model=model)
        return dict(table=t, op='modify', attrs=attrs, model=model)

    def expand(self, ss, deferred_all, trigger=None, depth=0, skip_self=None, async_all=False):
        """Writes of a list of (site, deferred), each followed by the event it triggers.  A write is
        deferred if it commits apart from the writes before it, and also async if it runs later, from a
        job, an after-commit callback or an outbox, rather than in the same request."""
        pending = {}
        out = []
        pguard = {}
        for s, dfr in ss:
            if s['kind'] == 'assign' and s['recv'] not in ('', 'self') and s.get('guard'):
                pguard.setdefault((s['recv'], s['model']), set()).update(s['guard'])
            if skip_self and s['model'] == skip_self[0] and s['recv'] in ('', 'self'):
                # before-callback assignments of the saved row: part of the insertion on create,
                # modifications on update unless they only fill a missing value (||=)
                if s['kind'] == 'assign' and skip_self[1] and trigger and trigger[1] == 'update' \
                        and s['op'] != '||=':
                    w = dict(table=self.models[skip_self[0]].table, op='modify',
                             attrs={self.column(skip_self[0], a) for a in s['attrs']},
                             model=skip_self[0], deferred=False, via='%s %s' % (skip_self[2], s['file']),
                             trigger=trigger)
                    sync = self.sync_source(skip_self[0], s.get('args', ''))
                    if sync:
                        w['sync'] = sync
                    out.append(w)
                if s['kind'] != 'delete':
                    continue
            w = self.write_of(s, pending)
            if w is None:
                continue
            w['deferred'] = dfr or deferred_all
            w['async'] = dfr or async_all
            w['via'] = '%s:%d' % (s['file'], s['line'])
            if s['kind'] == 'assign' and s['recv'] in ('', 'self'):
                sync = self.sync_source(s['model'], s.get('args', ''))
                if sync:
                    w['sync'] = sync
            gm = s.get('ctx') if s.get('ctx') in self.models else (skip_self[0] if skip_self else None)
            if s.get('guard') and gm:
                w['guard'] = (self.models[gm].table, self.guard_cols(gm, s['guard']))
            if trigger:
                w['trigger'] = trigger
            rc = s.get('rcopy')
            split = rc and w['op'] == 'modify' and set(w['attrs']) & set(rc)
            if split:
                # reverse copies S.c := R.d: each counts only if the template changes R.d (rsync)
                for c in sorted(split):
                    out.append(dict(w, attrs={c}, rsync=rc[c]))
                w['attrs'] = set(w['attrs']) - split
            if not split or w['attrs']:
                out.append(w)
            if s['op'] not in NO_CALLBACKS and depth < MAX_DEPTH:
                for x in self.event(w['model'], EVENT[w['op']], depth + 1):
                    y = dict(x)
                    y['deferred'] = x.get('deferred') or w['deferred']
                    y['async'] = x.get('async') or w['async']
                    out.append(y)
        if skip_self and skip_self[1]:
            # a before-save callback that assigns attributes of an autosave association: the
            # association row is saved with the row of the callback
            auto = {a['name'] for a in self.models[skip_self[0]].assocs if a['opts'].get('autosave') is True
                    or str(a['opts'].get('autosave')) == 'true'}
            for (recv, model), attrs in pending.items():
                if recv in auto and model in self.models and attrs:
                    op = 'insert' if trigger and trigger[1] == 'create' else 'modify'
                    w = dict(table=self.models[model].table, op=op, attrs=set(attrs), model=model,
                             deferred=False, via='autosave %s %s' % (recv, skip_self[2]), trigger=trigger)
                    g = pguard.get((recv, model))
                    if g:
                        w['guard'] = (self.models[skip_self[0]].table, self.guard_cols(skip_self[0], g))
                    out.append(w)
        return dedupe(out)

    def guard_cols(self, model, attrs, depth=0):
        """Columns whose change a guard tests; a predicate x_changed? defined by the model (or a
        concern) stands for the columns its body tests (`path_changed? || parent_changed?`)."""
        out = set()
        m = self.models[model]
        for a in attrs:
            meth = m.methods.get(a + '_changed?')
            if meth is not None and depth < 2:
                body = ' '.join(st.code for st in meth.stmts())
                inner = change_guards(body)
                if inner:
                    out |= set(self.guard_cols(model, inner, depth + 1))
                    continue
            out.add(self.column(model, a))
        return tuple(sorted(out))

    def event(self, name, ev, depth=0):
        """Writes caused by saving (create or update) or destroying one row of model name."""
        key = (name, ev, depth)     # deeper events expand fewer callbacks: cache per depth
        if key in self._event_cache:
            return self._event_cache[key]
        self._event_cache[key] = []
        m = self.models[name]
        out = []
        for events, deferred, f, a, b, kind in callback_ranges(m):
            if ev not in events or f is None:
                continue
            ss = self.range_sites(f, a, b)
            before = kind.startswith('before') or kind.startswith('around')
            out.extend(self.expand(ss, deferred, (m.table, ev), depth, skip_self=(name, before, kind),
                                   async_all=deferred))
        if ev == 'destroy':
            for r in self.refs:
                dep = r.get('dependent')
                if r['ref'] != m.table or not dep or r['kind'] == 'belongs_to':
                    continue
                child = r['model']
                if child not in self.models:
                    continue
                dep = str(dep)
                if dep in ('destroy', 'delete_all', 'destroy_async'):
                    out.append(dict(table=r['table'], op='delete', attrs=set(), model=child,
                                    deferred=dep == 'destroy_async', **{'async': dep == 'destroy_async'},
                                    via='dependent: %s' % dep))
                    if dep == 'destroy' and depth < MAX_DEPTH:
                        for x in self.event(child, 'destroy', depth + 1):
                            out.append(dict(x))
                elif dep in ('nullify', 'nullify_async', 'update_async'):
                    # loose foreign keys (GitLab) nullify or rewrite the column later, in a worker
                    out.append(dict(table=r['table'], op='modify', attrs=set(r['cols']), model=child,
                                    deferred=dep != 'nullify', **{'async': dep != 'nullify'},
                                    via='dependent: nullify' if dep != 'update_async' else 'dependent: update'))
        for r in self.refs:
            if r['kind'] == 'belongs_to' and r['model'] == name and r['ref'] and r['opts'].get('counter_cache') \
                    and ev in ('create', 'destroy'):
                cc = r['opts']['counter_cache']
                col = cc if isinstance(cc, str) else m.table + '_count'
                out.append(dict(table=r['ref'], op='modify', attrs={str(col)}, model=r['target'],
                                deferred=False, via='counter_cache', trigger=(m.table, ev)))
        self._event_cache[key] = out
        return out

    # ------------------------------------------------------------------ templates
    def templates(self):
        out = []
        own = {'create': 'insert', 'update': 'modify', 'destroy': 'delete'}
        for name, m in self.models.items():
            if m.abstract:
                continue
            for ev in ('create', 'update', 'destroy'):
                ws = self.event(name, ev)
                if not ws:
                    continue
                base = dict(table=m.table, op=own[ev], attrs={'?'} if ev == 'update' else set(), model=name,
                            deferred=False, via='self')
                out.append(dict(kind='event', origin='%s#%s' % (name, ev), file=m.file, writes=dedupe([base] + ws)))
        units = {}
        for s in self.sites:
            units.setdefault(self._key(s), {'sites': [], 'calls': []})['sites'].append(s)
        for c in self.calls:
            units.setdefault(self._key(c), {'sites': [], 'calls': []})['calls'].append(c)
        called = {c['target'] for c in self.calls}
        called_str = {('%s' % a, b) for a, b in called}
        # callback bodies: the event templates of their models include their writes
        cb_defs = set()
        for m in self.models.values():
            for _, _, f, a, _, _ in callback_ranges(m):
                if f is not None:
                    cb_defs.add((f, a))
        for key, u in units.items():
            kind, f, where = key
            if kind == 'method' and tuple(where.split('#', 1)) in called_str \
                    and not u['calls'] and len(u['sites']) < 2:
                continue
            if kind == 'method':
                cls, meth = where.split('#', 1)
                if (None if cls == 'None' else cls, meth) in called:
                    continue        # a helper: the templates of its callers include its writes
                d = self.defs.get((None if cls == 'None' else cls, meth))
                if d and (d[0], d[1]) in cb_defs:
                    continue
            ss = [(s, False) for s in u['sites']]
            for c in u['calls']:
                t = c['target']
                if t not in self.defs:
                    continue
                cf, ca, cb = self.defs[t]
                for s, dfr in self.range_sites(cf, ca, cb, 1, (t,)):
                    ss.append((s, dfr or c['async_']))
            if not ss:
                continue
            writes = self.expand(ss, kind == 'method')
            if len({w['table'] for w in writes}) < 2:
                continue
            if kind == 'method':
                direct = {w['table'] for w in writes if not w['via'].startswith(('before', 'after', 'around',
                                                                               'dependent', 'counter'))}
                if len(direct) < 2:
                    continue        # one direct write: its event template covers it
            lines = [s['line'] for s in u['sites']] + [c['line'] for c in u['calls']]
            out.append(dict(kind=kind, origin='%s:%d' % (f, min(lines)), file=f, writes=writes, where=where))
        return out

    @staticmethod
    def _key(x):
        if x['txn']:
            return ('transaction', x['file'], x['txn'])
        return ('method', x['file'], '%s#%s' % (x['cls'], x['meth']))
