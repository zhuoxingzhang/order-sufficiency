"""Verdicts of the analyzer on the ten further applications (Section 6.1, Section 6.2, Figure 4b).

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


print('== ten further apps (multi-table templates outside test and development code)')
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
