"""Read-path audit of GitLab (Section 6.2), step 1: for every loose foreign key that crosses databases, find
the association by which the child model follows the key to its parent, and the places where application
code follows it.

usage: python -I gitlab_readpath_probe.py GITLAB_SRC SCHEMA_JSON OUT_JSON
  GITLAB_SRC   checkout of gitlab-org/gitlab at commit 9d4cbde6 (app, ee/app, lib, ee/lib and db/docs are read)
  SCHEMA_JSON  ../deciders/data/gitlab_schema.json (tables with their database, loose foreign keys)
The 249 loose foreign keys whose child and parent lie in different databases (neither shared) belong to the
17 cascading deletions of corpus/gitlab_classify.py.  A site follows a key from the child to the parent if
it calls the association on a variable named after the child model (issue_link.issue), calls it inside the
child model itself, or delegates to it.  The label looks at the code right after the call:
  T  safe navigation (&.), a nil or presence check, or a guard clause on the same line or right after
  E  calls a method on the parent without a check, which raises if the parent is gone
  U  passes the parent on without a check nearby (delegations are always U)
The labels are a first pass: gitlab_readpath_review.json holds the verdicts by hand on a sample.
"""
import json, os, re, sys

SRC, SCHEMA, OUT = sys.argv[1:4]
DOCS = os.path.join(SRC, 'db', 'docs')
ROOTS = ['app', 'ee/app', 'lib', 'ee/lib']
MODEL_ROOTS = ['app/models', 'ee/app/models', 'ee/app/models/ee']
SKIP = ('/spec/', '/loose_foreign_keys/', 'loose_foreign_key')
BASES = ('ApplicationRecord', 'ActiveRecord::Base', 'Gitlab::Database::SecApplicationRecord',
         'Ci::ApplicationRecord', 'SecApplicationRecord')


def underscore(seg):
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', '_', seg).lower()


def classes(table):
    p = os.path.join(DOCS, table + '.yml')
    if not os.path.exists(p):
        return []
    out, on = [], False
    for line in open(p, encoding='utf-8'):
        if line.startswith('classes:'):
            on = True
            continue
        if on:
            m = re.match(r'-\s+(\S+)', line)
            if not m:
                break
            out.append(m.group(1).strip('\'"'))
    return out


def model_files(cls, depth=0, seen=None):
    """Files that define cls (CE model, EE model, EE prepend module) and, up to three levels, its superclass."""
    seen = seen if seen is not None else set()
    if cls in seen or depth > 3:
        return []
    seen.add(cls)
    rel = '/'.join(underscore(s) for s in cls.split('::')) + '.rb'
    files = [os.path.join(SRC, r, rel) for r in MODEL_ROOTS if os.path.exists(os.path.join(SRC, r, rel))]
    out = list(files)
    for f in files:
        m = re.search(r'^\s*class\s+[\w:]+\s*<\s*([\w:]+)', open(f, encoding='utf-8').read(), re.M)
        if m and m.group(1) not in BASES:
            sup = m.group(1).lstrip(':')
            ns = cls.split('::')[:-1]
            for cand in (['::'.join(ns + [sup])] if ns else []) + [sup]:
                out += model_files(cand, depth + 1, seen)
    return out


def associations(files, col):
    found = []
    for f in files:
        lines = open(f, encoding='utf-8').read().split('\n')
        for i, line in enumerate(lines):
            m = re.match(r'\s*belongs_to\s+:(\w+)(.*)$', line)
            if not m:
                continue
            opts = m.group(2)
            j = i
            while opts.rstrip().endswith(',') and j + 1 < len(lines):
                j += 1
                opts += ' ' + lines[j].strip()
            fk = re.search(r'foreign_key:\s*:?["\']?(\w+)', opts)
            if (fk.group(1) if fk else m.group(1) + '_id') == col:
                found.append({'name': m.group(1), 'file': os.path.relpath(f, SRC), 'line': i + 1,
                              'optional': 'optional: true' in opts})
    return found


GUARD = re.compile(r'\b(if|unless|return|next|&&|\|\||present\?|nil\?|blank\?|try\(|compact|filter_map|select)\b|&&|\|\|')


def label(lines, i, end):
    line = lines[i]
    rest = line[end:]
    if rest.startswith('&.'):
        return 'T'
    if GUARD.search(line):
        return 'T'
    if re.match(r'\.\w', rest):
        return 'E'
    m = re.match(r'\s*(@?\w+)\s*=[^=]', line)
    if m:
        v = re.escape(m.group(1))
        for nxt in lines[i + 1:i + 7]:
            if re.search(rf'(return|next)\b.*\b(unless|if)\b.*{v}|\b(if|unless)\s+!?{v}\b|{v}&\.|{v}\.(present|nil|blank)\?', nxt):
                return 'T'
            if re.search(rf'{v}\.\w', nxt):
                return 'E'
    return 'U'


def source_files():
    for r in ROOTS:
        for d, _, fs in os.walk(os.path.join(SRC, r), followlinks=True):
            for f in fs:
                if f.endswith(('.rb', '.haml', '.erb')):
                    p = os.path.join(d, f)
                    if not any(s in p for s in SKIP):
                        yield p


schema = json.load(open(SCHEMA, encoding='utf-8'))
tabs = schema['tables']
edges = []
for f in schema['lfk']:
    c, p = f['child'], f['table']
    if c in tabs and p in tabs and tabs[c]['db'] != tabs[p]['db'] and 'shared' not in (tabs[c]['db'], tabs[p]['db']):
        edges.append({'child': c, 'parent': p, 'col': f['column'], 'on_delete': f.get('on_delete'),
                      'cdb': tabs[c]['db'], 'pdb': tabs[p]['db']})
edges.sort(key=lambda e: (e['child'], e['parent'], e['col']))
files = {p: open(p, encoding='utf-8', errors='replace').read().split('\n') for p in source_files()}
print('edges', len(edges), 'files', len(files), file=sys.stderr)
out = []
for e in edges:
    cls = classes(e['child'])
    mf = []
    for c in cls:
        mf += model_files(c)
    asc = associations(sorted(set(mf)), e['col'])
    rec = dict(e, classes=cls, model_files=[os.path.relpath(f, SRC) for f in sorted(set(mf))], assoc=asc, sites=[])
    names = sorted({a['name'] for a in asc})
    if names:
        var = {underscore(c.split('::')[-1]) for c in cls} | {e['child'][:-1] if e['child'].endswith('s') else e['child']}
        var = {v[2:] if v.startswith('p_') else v for v in var}
        alt = '|'.join(sorted(re.escape(n) for n in names))
        pv = re.compile(rf'\b@?\w*(?:{"|".join(sorted(re.escape(v) for v in var))})(?:&?\.)({alt})\b(?![?!\w(]|\s*=[^=>])')
        pin = re.compile(r'(?<![:.\w@])(?:self&?\.)?(' + alt + r')(?=&?\.\w)')
        pdel = re.compile(rf'^\s*delegate\b.*\bto:\s*:({alt})\b')
        own = {os.path.join(SRC, f) for f in rec['model_files']}
        for p, lines in files.items():
            for i, line in enumerate(lines):
                if line.lstrip().startswith('#'):
                    continue
                hits = [(m, 'var') for m in pv.finditer(line)]
                if p in own:
                    hits += [(m, 'own') for m in pin.finditer(line)] + [(m, 'delegate') for m in pdel.finditer(line)]
                for m, how in hits:
                    rec['sites'].append({'file': os.path.relpath(p, SRC), 'line': i + 1, 'how': how,
                                         'label': 'U' if how == 'delegate' else label(lines, i, m.end())})
    out.append(rec)
json.dump(out, open(OUT, 'w', encoding='utf-8'), indent=1)
by = {}
for r in out:
    t = by.setdefault(r['parent'], {'edges': 0, 'assoc': 0, 'T': 0, 'E': 0, 'U': 0})
    t['edges'] += 1
    t['assoc'] += bool(r['assoc'])
    for s in r['sites']:
        t[s['label']] += 1
for p, t in sorted(by.items()):
    print(p, t)
