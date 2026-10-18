"""Extract the GitLab schema facts used by E3/E4 into data/gitlab_schema.json (no YAML library).

Sources, all at commit 9d4cbde62f81f4ceb8f0adfc6d32f6ad15872af5 of gitlab-org/gitlab:
  db/docs/*.yml                        table, gitlab_schema, sharding_key (column -> root table)
  db/database_connections/*.yaml       gitlab_schema -> database (main, ci, sec)
  config/gitlab_loose_foreign_keys.yml loose foreign keys (child column -> parent.id)
  db/structure.sql (optional)          columns, primary keys, unique indexes, foreign keys
Usage: python -I gitlab_extract.py <db/docs dir> <database_connections dir> <lfk.yml> [structure.sql]
       python -I gitlab_extract.py --add-structure <structure.sql>   (into an existing JSON)
The raw files are data: they are only parsed line by line, never executed."""
import sys, os, re, json

SHARED = {'gitlab_internal', 'gitlab_shared', 'gitlab_shared_org', 'gitlab_shared_cell_local'}


def parse_doc(path):
    d, block = {'sharding_key': {}}, None
    for line in open(path, encoding='utf-8'):
        line = line.rstrip('\n')
        if not line.strip() or line.startswith('---'):
            continue
        if not line.startswith(' ') and not line.startswith('-'):
            block = None
            k, _, v = line.partition(':')
            v = v.split('#')[0].strip().strip("'\"")
            if k == 'sharding_key' and not v:
                block = 'sharding_key'
            elif k in ('table_name', 'gitlab_schema'):
                d[k] = v
        elif block == 'sharding_key' and not line.strip().startswith('#'):
            k, _, v = line.strip().partition(':')
            d['sharding_key'][k.strip()] = v.split('#')[0].strip()
    return d


def parse_conn(path):
    name, schemas, block = None, [], None
    for line in open(path, encoding='utf-8'):
        s = line.rstrip('\n')
        if s.startswith('name:'):
            name = s.split(':', 1)[1].strip()
        elif not s.startswith(' ') and s.strip():
            block = s.split(':')[0]
        elif block == 'gitlab_schemas' and s.strip().startswith('- '):
            schemas.append(s.strip()[2:].strip())
    return name, schemas


def parse_lfk(path):
    rows, child, cur = [], None, None
    for line in open(path, encoding='utf-8'):
        s = line.rstrip('\n')
        if not s.strip() or s.startswith('---') or s.lstrip().startswith('#'):
            continue
        if not s.startswith(' '):
            child = s.split(':')[0].strip()
            continue
        ind = len(s) - len(s.lstrip())
        t = s.strip()
        if ind == 2 and t.startswith('- '):
            cur = {'child': child}
            rows.append(cur)
            t = t[2:]
        elif ind != 4:
            continue
        k, _, v = t.partition(':')
        if cur is not None and k.strip() in ('table', 'column', 'on_delete'):
            cur[k.strip()] = v.split('#')[0].strip().strip("'\"")
    return [r for r in rows if 'table' in r and 'column' in r]


IDENT = r'"?([A-Za-z0-9_]+)"?'


def unq(name):
    name = name.strip().strip('"')
    return name.split('.')[-1].strip('"')


def parse_structure(lines, keep):
    tables, fks = {}, []
    cur = None
    for line in lines:
        s = line.rstrip('\n')
        m = re.match(r'CREATE TABLE ([^\s(]+) \($', s)
        if m:
            name = m.group(1)
            schema_part = name.split('.')[0] if '.' in name else 'public'
            t = unq(name)
            cur = t if (schema_part == 'public' and t in keep) else None
            if cur:
                tables[cur] = {'columns': [], 'pk': [], 'uniques': []}
            continue
        if cur:
            if s.startswith(')'):
                cur = None
                continue
            m = re.match(r'\s+' + IDENT + r'\s', s)
            if m and not s.strip().upper().startswith('CONSTRAINT'):
                tables[cur]['columns'].append(m.group(1))
            m = re.match(r'\s+CONSTRAINT \S+ PRIMARY KEY \(([^)]*)\)', s)
            if m:
                tables[cur]['pk'] = [unq(c) for c in m.group(1).split(',')]
            continue
        m = re.match(r'ALTER TABLE (?:ONLY )?(\S+)\s+ADD CONSTRAINT \S+ PRIMARY KEY \(([^)]*)\);', s)
        if m and unq(m.group(1)) in tables:
            tables[unq(m.group(1))]['pk'] = [unq(c) for c in m.group(2).split(',')]
            continue
        m = re.match(r'ALTER TABLE (?:ONLY )?(\S+)\s+ADD CONSTRAINT \S+ UNIQUE \(([^)]*)\)[^;]*;', s)
        if m and unq(m.group(1)) in tables:
            tables[unq(m.group(1))]['uniques'].append([unq(c) for c in m.group(2).split(',')])
            continue
        m = re.match(r'CREATE UNIQUE INDEX \S+ ON (?:ONLY )?(\S+) USING \w+ \(([^()]*)\)(.*);$', s)
        if m and unq(m.group(1)) in tables:
            cols, rest = m.group(2), m.group(3)
            if 'WHERE' in rest:
                continue
            tables[unq(m.group(1))]['uniques'].append([unq(c.split()[0]) for c in cols.split(',')])
            continue
        m = re.match(r'ALTER TABLE (?:ONLY )?(\S+)\s+ADD CONSTRAINT \S+ FOREIGN KEY \(([^)]*)\) REFERENCES (\S+?)\(([^)]*)\)', s)
        if m:
            c, p = unq(m.group(1)), unq(m.group(3))
            if c in keep and p in keep:
                fks.append({'child': c, 'cols': [unq(x) for x in m.group(2).split(',')],
                            'parent': p, 'pcols': [unq(x) for x in m.group(4).split(',')]})
    # FOREIGN KEY lines can also follow "ALTER TABLE ..." on the previous line
    return tables, fks


def parse_structure_multiline(path, keep):
    """structure.sql writes 'ALTER TABLE ONLY t\\n    ADD CONSTRAINT ...;' over two lines; join them."""
    text = open(path, encoding='utf-8').read()
    text = re.sub(r'\n\s+ADD CONSTRAINT', ' ADD CONSTRAINT', text)
    return parse_structure(text.splitlines(), keep)


def main(docs, conns, lfk, structure=None):
    db_of = {}
    for f in sorted(os.listdir(conns)):
        if f.endswith('.yaml') or f.endswith('.yml'):
            name, schemas = parse_conn(os.path.join(conns, f))
            for s in schemas:
                db_of.setdefault(s, set()).add(name)
    tables = {}
    for f in sorted(os.listdir(docs)):
        if not f.endswith('.yml'):
            continue
        d = parse_doc(os.path.join(docs, f))
        sch = d.get('gitlab_schema', '')
        dbs = db_of.get(sch, set())
        db = 'shared' if sch in SHARED else (sorted(dbs)[0] if len(dbs) == 1 else '?')
        tables[d['table_name']] = {'schema': sch, 'db': db, 'sharding_key': d['sharding_key']}
    out = {'tables': tables, 'lfk': parse_lfk(lfk), 'structure': None}
    if structure:
        st, fks = parse_structure_multiline(structure, set(tables))
        out['structure'] = {'tables': st, 'fks': fks}
    here = os.path.dirname(os.path.abspath(__file__))
    json.dump(out, open(os.path.join(here, 'data', 'gitlab_schema.json'), 'w'), indent=0, sort_keys=True)
    print('tables %d, with sharding key %d, loose FKs %d, structure %s'
          % (len(tables), sum(1 for t in tables.values() if t['sharding_key']), len(out['lfk']),
             'none' if not structure else '%d tables, %d FKs' % (len(out['structure']['tables']),
                                                                 len(out['structure']['fks']))))


def add_structure(structure):
    """add db/structure.sql to an existing data/gitlab_schema.json (docs, connections and loose FKs
    already extracted)"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'gitlab_schema.json')
    out = json.load(open(path))
    st, fks = parse_structure_multiline(structure, set(out['tables']))
    out['structure'] = {'tables': st, 'fks': fks}
    json.dump(out, open(path, 'w'), indent=0, sort_keys=True)
    print('structure: %d tables, %d FKs' % (len(st), len(fks)))


if __name__ == '__main__':
    if sys.argv[1] == '--add-structure':
        add_structure(sys.argv[2])
    else:
        main(*sys.argv[1:5])
