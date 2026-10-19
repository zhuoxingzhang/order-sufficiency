"""Agreement of the analyzer with the 97 hand labels of GitLab and Sentry (corpus/), and its verdicts
on the ten further applications (Section 6.1, Section 6.2, Figure 4b).

A template is read strictly (a derived value must equal its definition in every state) or one-way
(derived values may lag behind their rows, so a template that needs atomic visibility only for a
derived value needs only order).  Templates in test and development code are left out.
Reads <out dir>/<app>.json or <app>.json.gz.
Usage: python -I validate.py <out dir>"""
import gzip, json, os, re, sys
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8')
out = sys.argv[1]


def load(app):
    p = os.path.join(out, app + '.json')
    if os.path.exists(p):
        return json.load(open(p, encoding='utf-8'))
    if os.path.exists(p + '.gz'):
        return json.load(gzip.open(p + '.gz', 'rt', encoding='utf-8'))
    return None


gl = load('gitlab')
se = load('sentry')
rows = [('GitLab-G', x['id'], x['label'], x['strict'], x['oneway']) for x in gl['exemptions']]
rows += [('GitLab-C', x['parent'], x['label'], x['strict'], x['oneway']) for x in gl['lfk_parents']]
rows += [('Sentry-' + x['mech'], x['id'], x['label'], x['strict'], x['oneway']) for x in se['validation']]

for reading in ('strict', 'oneway'):
    i = 3 if reading == 'strict' else 4
    print('==', reading)
    for grp in sorted({r[0].split('-')[0] for r in rows}) + ['all']:
        rs = [r for r in rows if grp == 'all' or r[0].startswith(grp)]
        found = [r for r in rs if r[i] != 'missed']
        agree = [r for r in found if r[i] == r[2]]
        print('%-7s labels %3d found %3d agree %3d (%.0f%%)' % (grp, len(rs), len(found), len(agree),
                                                         100 * len(agree) / max(1, len(found))))
    found = [r for r in rows if r[i] != 'missed']
    print('confusion (label, analyzer):', sorted(Counter((r[2], r[i]) for r in found).items()))
    said = [r for r in found if r[i] == 'atomic']
    print('analyzer atomic: %d, labelled atomic %d' % (len(said), sum(r[2] == 'atomic' for r in said)))
    lab = [r for r in found if r[2] == 'atomic']
    print('labelled atomic and found: %d, analyzer atomic %d' % (len(lab), sum(r[i] == 'atomic' for r in lab)))
    print('wrong:', [(r[1], r[2], r[i]) for r in found if r[i] != r[2]])
print('missed:', [r[1] for r in rows if r[3] == 'missed'])

print('\n== ten further apps (multi-table templates outside test and development code)')
TESTDEV = re.compile(r'(^|/)(tests?|spec|testing_support|factories|fixtures|development|seeders?|seeds?|test_utils?'
                     r'|testing)(/|\.|_)|random_data|curl_param_value_generators|/demo/')
tot = {'strict': Counter(), 'oneway': Counter()}
why = Counter()
for app in ('zulip', 'makeplane', 'saleor', 'openedx', 'PostHog', 'chatwoot', 'mastodon', 'discourse',
            'spree', 'instructure'):
    d = load(app)
    if d is None:
        print(app, 'missing')
        continue
    ts = [t for t in d['templates'] if t['kind'] == 'event' or not TESTDEV.search(t['origin'])]
    st = {k: Counter(t[k] for t in ts) for k in tot}
    for k in tot:
        tot[k].update(st[k])
    for t in ts:
        if t['strict'] == 'atomic' and t['oneway'] != 'atomic':
            why[tuple(sorted({r[1] for r in t['reasons'] if r[0] == 'derived'}))] += 1
        elif t['oneway'] == 'atomic':
            why[('atomic',) + tuple(sorted({r[1] for r in t['reasons'] if r[0] == 'atomic'}))] += 1
    print('%-12s %4d  strict %s  oneway %s' % (app, len(ts), dict(st['strict']), dict(st['oneway'])))
for k, c in tot.items():
    n = sum(c.values())
    con = c['order'] + c['atomic']
    print(k, n, {v: '%d (%.0f%%)' % (c[v], 100 * c[v] / n) for v in ('none', 'order', 'atomic')},
          'order among the %d constrained %.0f%%' % (con, 100 * c['order'] / max(1, con)))
print('atomic reasons:', why.most_common())
