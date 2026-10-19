"""Relational schema of an application: tables, columns, primary keys, unique keys, foreign keys.

Sources (read statically, never executed):
  schema.rb       Rails create_table / t.<type> / t.index / add_index / add_foreign_key
  structure.sql   CREATE TABLE, PRIMARY KEY, FOREIGN KEY ... REFERENCES, CREATE UNIQUE INDEX
  migrations      Rails db/migrate/*.rb replayed in file order (create_table, add_column,
                  remove_column, rename_column, rename_table, drop_table, add_index, add_reference,
                  add_foreign_key, remove_foreign_key)
Django models are read by django_front.py into the same structure.
"""
import os
import re


class Schema:
    def __init__(self):
        self.tables = {}          # name -> list of columns (ordered)
        self.pk = {}              # name -> tuple of columns
        self.unique = {}          # name -> list of (tuple of columns, partial: bool)
        self.fks = []             # dicts: table, cols, ref, ref_cols, source ('db'|'model'|...)

    def add_table(self, t, cols=(), pk=('id',)):
        self.tables.setdefault(t, [])
        for c in cols:
            self.add_column(t, c)
        if pk is not None:
            self.pk[t] = tuple(pk)
        self.unique.setdefault(t, [])

    def add_column(self, t, c):
        self.tables.setdefault(t, [])
        if c not in self.tables[t]:
            self.tables[t].append(c)

    def add_unique(self, t, cols, partial=False):
        cols = tuple(cols)
        u = self.unique.setdefault(t, [])
        if all(c != cols for c, _ in u):
            u.append((cols, partial))

    def add_fk(self, t, cols, ref, ref_cols=('id',), source='db', **kw):
        d = dict(table=t, cols=tuple(cols), ref=ref, ref_cols=tuple(ref_cols), source=source)
        d.update(kw)
        if not any(f['table'] == t and f['cols'] == d['cols'] and f['ref'] == ref for f in self.fks):
            self.fks.append(d)

    def drop_table(self, t):
        for m in (self.tables, self.pk, self.unique):
            m.pop(t, None)
        self.fks = [f for f in self.fks if f['table'] != t and f['ref'] != t]

    def rename_table(self, a, b):
        for m in (self.tables, self.pk, self.unique):
            if a in m:
                m[b] = m.pop(a)
        for f in self.fks:
            if f['table'] == a:
                f['table'] = b
            if f['ref'] == a:
                f['ref'] = b

    def summary(self):
        return {'tables': len(self.tables), 'columns': sum(len(c) for c in self.tables.values()),
                'unique': sum(len(u) for u in self.unique.values()),
                'fks': len(self.fks)}


# ---------------------------------------------------------------------------- Rails schema.rb
_STR = r'"([^"]+)"|\'([^\']+)\'|:(\w+)'


def _s(m, base=1):
    return next(g for g in m.groups()[base - 1:base + 2] if g is not None)


def _cols(arr):
    """['a', 'b'] or "a" or :a -> tuple of names"""
    arr = arr.strip()
    if arr.startswith('['):
        return tuple(x or y or z for x, y, z in re.findall(r'"([^"]+)"|\'([^\']+)\'|:(\w+)', arr))
    m = re.match(_STR, arr)
    return (_s(m),) if m else ()


def _opt(args, key):
    m = re.search(r'\b%s:\s*(?:"([^"]+)"|\'([^\']+)\'|:(\w+)|(\[[^\]]*\])|(\w+))' % key, args)
    if not m:
        return None
    if m.group(4):
        return _cols(m.group(4))
    return next(g for g in m.groups() if g is not None)


def _table_pk(args):
    if re.search(r'\bid:\s*false', args):
        pk = _opt(args, 'primary_key')
        if pk is None:
            return ()
        return pk if isinstance(pk, tuple) else (pk,)
    pk = _opt(args, 'primary_key')
    if pk:
        return pk if isinstance(pk, tuple) else (pk,)
    return ('id',)


def singular(t):
    if t.endswith('ies'):
        return t[:-3] + 'y'
    if re.search(r'(ss|x|ch|sh|zz)es$', t):
        return t[:-2]
    if t.endswith('ses'):
        return t[:-2]
    if t.endswith('s') and not t.endswith('ss'):
        return t[:-1]
    return t


def plural(w):
    irregular = {'person': 'people', 'child': 'children', 'man': 'men', 'woman': 'women',
                 'status': 'statuses', 'datum': 'data', 'medium': 'media', 'analysis': 'analyses',
                 'criterion': 'criteria', 'index': 'indices', 'matrix': 'matrices', 'vertex': 'vertices',
                 'quiz': 'quizzes', 'axis': 'axes', 'leaf': 'leaves', 'half': 'halves', 'news': 'news',
                 'series': 'series', 'species': 'species', 'information': 'information',
                 'equipment': 'equipment', 'feedback': 'feedback', 'metadata': 'metadata'}
    head, _, last = w.rpartition('_')
    pre = head + '_' if head else ''
    if last in irregular:
        return pre + irregular[last]
    if re.search(r'[^aeiou]y$', last):
        return pre + last[:-1] + 'ies'
    if re.search(r'(alias|status|bus)$', last) or re.search(r'(x|ch|ss|sh)$', last):
        return pre + last + 'es'
    if last.endswith('s'):
        return pre + last          # Rails keeps words that already end in s ("settings")
    return pre + last + 's'


def parse_schema_rb(path, sch=None):
    sch = sch or Schema()
    cur = None
    for line in open(path, encoding='utf-8', errors='replace'):
        s = line.strip()
        m = re.match(r'create_table\s+(?:%s)(.*)$' % _STR, s)
        if m:
            cur = _s(m)
            sch.add_table(cur, pk=_table_pk(m.group(4)))
            for c in sch.pk[cur]:
                sch.add_column(cur, c)
            continue
        if cur and re.match(r'end\b', s):
            cur = None
            continue
        if cur:
            m = re.match(r't\.(\w+)\s+(?:%s)(.*)$' % _STR, s)
            if m and m.group(1) not in ('index', 'check_constraint', 'timestamps'):
                typ, name, rest = m.group(1), _s(m, 2), m.group(5)
                if typ in ('references', 'belongs_to'):
                    sch.add_column(cur, name + '_id')
                    if re.search(r'polymorphic:\s*true', rest):
                        sch.add_column(cur, name + '_type')
                    elif re.search(r'foreign_key:\s*(true|\{)', rest):
                        to = _opt(rest, 'to_table') or plural(name)
                        sch.add_fk(cur, (name + '_id',), to)
                else:
                    sch.add_column(cur, name)
                continue
            m = re.match(r't\.index\s+(\[[^\]]*\]|"[^"]+")(.*)$', s)
            if m and re.search(r'unique:\s*true', m.group(2)):
                sch.add_unique(cur, _cols(m.group(1)), partial=bool(re.search(r'\bwhere:', m.group(2))))
            continue
        m = re.match(r'add_index\s+(?:%s),\s*(\[[^\]]*\]|"[^"]+"|:\w+)(.*)$' % _STR, s)
        if m and re.search(r'unique:\s*true', m.group(5)):
            sch.add_unique(_s(m), _cols(m.group(4)), partial=bool(re.search(r'\bwhere:', m.group(5))))
            continue
        m = re.match(r'add_foreign_key\s+(?:%s),\s*(?:%s)(.*)$' % (_STR, _STR), s)
        if m:
            t, ref, rest = _s(m, 1), _s(m, 4), m.group(7)
            col = _opt(rest, 'column') or singular(ref) + '_id'
            pk = _opt(rest, 'primary_key') or 'id'
            sch.add_fk(t, col if isinstance(col, tuple) else (col,), ref,
                       pk if isinstance(pk, tuple) else (pk,), on_delete=_opt(rest, 'on_delete'))
    return sch


# ---------------------------------------------------------------------------- structure.sql
def _ident(x):
    x = x.strip().strip('"')
    return x.split('.')[-1].strip('"')


def parse_structure_sql(path, sch=None):
    sch = sch or Schema()
    text = open(path, encoding='utf-8', errors='replace').read()
    for m in re.finditer(r'CREATE (?:UNLOGGED )?TABLE (?:IF NOT EXISTS )?([\w."]+) \((.*?)\n\)', text, re.S):
        t = _ident(m.group(1))
        cols = []
        pk = None
        for line in m.group(2).split('\n'):
            s = line.strip().rstrip(',')
            if not s:
                continue
            if s.upper().startswith('CONSTRAINT') or s.upper().startswith('PRIMARY KEY'):
                pm = re.search(r'PRIMARY KEY \(([^)]*)\)', s)
                if pm:
                    pk = tuple(_ident(c) for c in pm.group(1).split(','))
                continue
            cm = re.match(r'("?[\w]+"?)\s', s)
            if cm:
                cols.append(_ident(cm.group(1)))
        sch.add_table(t, cols, pk=None)
        if pk:
            sch.pk[t] = pk
    for m in re.finditer(r'ALTER TABLE (?:ONLY )?([\w."]+)\s+ADD CONSTRAINT [\w"]+ PRIMARY KEY \(([^)]*)\)', text):
        sch.pk[_ident(m.group(1))] = tuple(_ident(c) for c in m.group(2).split(','))
    for m in re.finditer(r'ALTER TABLE (?:ONLY )?([\w."]+)\s+ADD CONSTRAINT [\w"]+ UNIQUE \(([^)]*)\)', text):
        sch.add_unique(_ident(m.group(1)), tuple(_ident(c) for c in m.group(2).split(',')))
    for m in re.finditer(r'ALTER TABLE (?:ONLY )?([\w."]+)\s+ADD CONSTRAINT [\w"]+ FOREIGN KEY \(([^)]*)\) '
                         r'REFERENCES ([\w."]+)\(([^)]*)\)([^;]*);', text):
        od = re.search(r'ON DELETE (CASCADE|SET NULL|RESTRICT|NO ACTION)', m.group(5))
        sch.add_fk(_ident(m.group(1)), tuple(_ident(c) for c in m.group(2).split(',')), _ident(m.group(3)),
                   tuple(_ident(c) for c in m.group(4).split(',')),
                   on_delete=od.group(1).lower().replace(' ', '_') if od else None)
    for m in re.finditer(r'CREATE UNIQUE INDEX [\w"]+ ON (?:ONLY )?([\w."]+) USING \w+ \(([^;]*?)\)( WHERE [^;]*)?;', text):
        cols = [c.strip() for c in m.group(2).split(',')]
        if all(re.match(r'^"?\w+"?$', c) for c in cols):
            sch.add_unique(_ident(m.group(1)), tuple(_ident(c) for c in cols), partial=bool(m.group(3)))
    for t in sch.tables:
        sch.pk.setdefault(t, ('id',) if 'id' in sch.tables[t] else ())
    return sch


# ---------------------------------------------------------------------------- migrations
def replay_migrations(dirs, sch=None):
    """Replay Rails migrations of the given directories in timestamp order."""
    sch = sch or Schema()
    files = []
    for d in dirs:
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith('.rb') and re.match(r'\d+_', f):
                    files.append((f.split('_')[0], os.path.join(d, f)))
    for _, path in sorted(files):
        cur = None
        depth = 0
        for line in open(path, encoding='utf-8', errors='replace'):
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            m = re.match(r'create_table\s+(?:%s)(.*)$' % _STR, s)
            if m:
                cur = _s(m)
                sch.drop_table(cur) if re.search(r'force:', m.group(4)) else None
                sch.add_table(cur, pk=_table_pk(m.group(4)))
                for c in sch.pk[cur]:
                    sch.add_column(cur, c)
                depth = 1 if re.search(r'\bdo\b', s) else 0
                if depth == 0:
                    cur = None
                continue
            if cur:
                if re.match(r'.*\bdo\b(\s*\|.*\|)?\s*$', s) or re.match(r'(if|unless|case|begin)\b', s):
                    depth += 1
                if re.match(r'end\b', s):
                    depth -= 1
                    if depth <= 0:
                        cur = None
                    continue
                m = re.match(r't\.(\w+)\s+(?:%s)(.*)$' % _STR, s)
                if m and m.group(1) not in ('index', 'timestamps', 'check_constraint'):
                    typ, name, rest = m.group(1), _s(m, 2), m.group(5)
                    if typ in ('references', 'belongs_to'):
                        sch.add_column(cur, name + '_id')
                        if re.search(r'polymorphic:\s*true', rest):
                            sch.add_column(cur, name + '_type')
                        elif re.search(r'foreign_key:\s*(true|\{)', rest):
                            sch.add_fk(cur, (name + '_id',), _opt(rest, 'to_table') or plural(name))
                    else:
                        for extra in re.findall(r',\s*(?:%s)' % _STR, rest.split(',', 1)[0] if False else ''):
                            pass
                        sch.add_column(cur, name)
                    continue
                if m and m.group(1) == 'timestamps':
                    sch.add_column(cur, 'created_at')
                    sch.add_column(cur, 'updated_at')
                    continue
                m = re.match(r't\.timestamps\b', s)
                if m:
                    sch.add_column(cur, 'created_at')
                    sch.add_column(cur, 'updated_at')
                    continue
                m = re.match(r't\.index\s+(\[[^\]]*\]|"[^"]+"|:\w+)(.*)$', s)
                if m and re.search(r'unique:\s*true', m.group(2)):
                    sch.add_unique(cur, _cols(m.group(1)), partial=bool(re.search(r'\bwhere:', m.group(2))))
                continue
            m = re.match(r'(add_column|remove_column)\s+(?:%s),\s*(?:%s)(.*)$' % (_STR, _STR), s)
            if m:
                t, c = _s(m, 2), _s(m, 5)
                if m.group(1) == 'add_column':
                    sch.add_column(t, c)
                elif t in sch.tables and c in sch.tables[t]:
                    sch.tables[t].remove(c)
                continue
            m = re.match(r'rename_column\s+(?:%s),\s*(?:%s),\s*(?:%s)' % (_STR, _STR, _STR), s)
            if m:
                t, a, b = _s(m, 1), _s(m, 4), _s(m, 7)
                if t in sch.tables and a in sch.tables[t]:
                    sch.tables[t][sch.tables[t].index(a)] = b
                continue
            m = re.match(r'rename_table\s+(?:%s),\s*(?:%s)' % (_STR, _STR), s)
            if m:
                sch.rename_table(_s(m, 1), _s(m, 4))
                continue
            m = re.match(r'drop_table\s+(?:%s)' % _STR, s)
            if m:
                sch.drop_table(_s(m))
                continue
            m = re.match(r'add_reference\s+(?:%s),\s*(?:%s)(.*)$' % (_STR, _STR), s)
            if m:
                t, name, rest = _s(m, 1), _s(m, 4), m.group(7)
                sch.add_column(t, name + '_id')
                if re.search(r'polymorphic:\s*true', rest):
                    sch.add_column(t, name + '_type')
                elif re.search(r'foreign_key:\s*(true|\{)', rest):
                    sch.add_fk(t, (name + '_id',), _opt(rest, 'to_table') or plural(name))
                continue
            m = re.match(r'add_index\s+(?:%s),\s*(\[[^\]]*\]|"[^"]+"|:\w+)(.*)$' % _STR, s)
            if m and re.search(r'unique:\s*true', m.group(5)):
                sch.add_unique(_s(m), _cols(m.group(4)), partial=bool(re.search(r'\bwhere:', m.group(5))))
                continue
            m = re.match(r'add_foreign_key\s+(?:%s),\s*(?:%s)(.*)$' % (_STR, _STR), s)
            if m:
                t, ref, rest = _s(m, 1), _s(m, 4), m.group(7)
                col = _opt(rest, 'column') or singular(ref) + '_id'
                sch.add_fk(t, col if isinstance(col, tuple) else (col,), ref)
                continue
            m = re.match(r'remove_foreign_key\s+(?:%s),\s*(?:%s)' % (_STR, _STR), s)
            if m:
                t, ref = _s(m, 1), _s(m, 4)
                sch.fks = [f for f in sch.fks if not (f['table'] == t and f['ref'] == ref)]
    return sch
