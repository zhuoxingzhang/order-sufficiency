"""Figure 4 and the list of all 97 cross-database write templates.

Usage: python -I summarize.py [out_dir]

Reads out/sentry_templates.json (sentry_classify.py) and out/gitlab_templates.json
(gitlab_classify.py), prints the templates by application, operation and class (Figure 4), and
writes out/templates.csv with the code location and the constraint behind every label.
"""
import csv
import json
import os
import sys
from collections import Counter

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
LABEL = {'order': 'need only order', 'atomic': 'need atomic visibility', 'serial': 'need serializability', 'none': 'unconstrained'}
MECH = {'R': 'outbox replication', 'D': 'synchronous remote write'}

rows = []
for app, name in (('GitLab', 'gitlab_templates.json'), ('Sentry', 'sentry_templates.json')):
    for t in json.load(open(os.path.join(out, name), encoding='utf-8'))['templates']:
        rows.append(dict(application=app, template=t['id'], mechanism=MECH.get(t['mech'], t['mech']), operation=t['op'],
                         what=t['what'], writes=t['writes'], constraint=t['constraint'], label=LABEL[t['cls']],
                         required_order=t.get('order', ''), locations='; '.join(t['locs'])))

print('Figure 4: templates by application, operation and class')
for app in ('GitLab', 'Sentry'):
    for group, ops in (('insertions, deletions', ('insert', 'delete', 'insert/delete')), ('modifications', ('modify',))):
        c = Counter(r['label'] for r in rows if r['application'] == app and r['operation'] in ops)
        print(f'  {app} {group}: {sum(c.values())}', {k: c[v] for k, v in LABEL.items() if c[v]})
for app in ('GitLab', 'Sentry'):
    c = Counter(r['label'] for r in rows if r['application'] == app)
    print(f'  {app} total: {sum(c.values())}', dict(c))

with open(os.path.join(out, 'templates.csv'), 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow(['application', 'template', 'mechanism', 'operation', 'what the template does', 'rows written (by database)',
                'constraint between these rows', 'label', 'required order', 'code locations'])
    for r in rows:
        w.writerow([r['application'], r['template'], r['mechanism'], r['operation'], r['what'], r['writes'],
                    r['constraint'], r['label'], r['required_order'], r['locations']])
print('wrote', os.path.join(out, 'templates.csv'), len(rows), 'templates')
