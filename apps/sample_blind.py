"""Draw a sample of templates from the ten further applications for blind labelling (Section 6.2).

For every app, up to PER templates per strict verdict (atomic, order, none), drawn with a fixed seed,
so that the sample covers every verdict of the analyzer.  The sheet gives only the code location, not
the tables or the verdict of the analyzer; the key holds the verdicts.
Reads <out dir>/<app>.json or <app>.json.gz.
Usage: python -I sample_blind.py <out dir> <sheet.csv> <key.json> [apps,...] [item prefix] [options]
Options (for a further sample after the first one):
  --seed=S          seed instead of 20261019
  --per=N           templates per app and verdict instead of 2
  --exclude=K1,K2   key files of earlier samples: their origins are not drawn again
  --skip-testdev    leave out templates in test and development code before drawing (as validate.py)
"""
import csv, gzip, json, os, random, re, sys

APPS = {
    'zulip': 'zulip_zulip', 'makeplane': 'makeplane_plane', 'saleor': 'saleor_saleor',
    'openedx': 'openedx_openedx-platform', 'PostHog': 'PostHog_posthog',
    'chatwoot': 'chatwoot_chatwoot', 'mastodon': 'mastodon_mastodon', 'discourse': 'discourse_discourse',
    'spree': 'spree_spree', 'instructure': 'instructure_canvas-lms',
}
TESTDEV = re.compile(r'(^|/)(tests?|spec|testing_support|factories|fixtures|development|seeders?|seeds?|test_utils?'
                     r'|testing)(/|\.|_)|random_data|curl_param_value_generators|/demo/')

flags = {a.split('=', 1)[0]: (a.split('=', 1) + [''])[1] for a in sys.argv[1:] if a.startswith('--')}
args = [a for a in sys.argv[1:] if not a.startswith('--')]
out_dir, sheet, key_path = args[:3]
only = args[3].split(',') if len(args) > 3 and args[3] else list(APPS)
prefix = args[4] if len(args) > 4 else 'N'
seed = flags.get('--seed', '20261019')
PER = int(flags.get('--per', 2))
seen = set()
for k in filter(None, flags.get('--exclude', '').split(',')):
    seen |= {(v['app'], v['origin']) for v in json.load(open(k, encoding='utf-8')).values()}
rows, key = [], {}
for app, src in APPS.items():
    if app not in only:
        continue
    rng = random.Random(seed + app)      # per app, so that the sample does not depend on the others
    p = os.path.join(out_dir, app + '.json')
    if os.path.exists(p):
        ts = json.load(open(p, encoding='utf-8'))['templates']
    elif os.path.exists(p + '.gz'):
        ts = json.load(gzip.open(p + '.gz', 'rt', encoding='utf-8'))['templates']
    else:
        print('missing', app)
        continue
    ts = [t for t in ts if (app, t['origin']) not in seen]
    if '--skip-testdev' in flags:
        ts = [t for t in ts if t['kind'] == 'event' or not TESTDEV.search(t['origin'])]
    for v in ('atomic', 'order', 'none'):
        pool = [t for t in ts if t['strict'] == v]
        for t in rng.sample(pool, min(PER, len(pool))):
            i = '%s%02d' % (prefix, len(rows) + 1)
            rows.append(dict(item=i, app=app, src=src, kind=t['kind'], origin=t['origin'],
                             label='', derived='', reason=''))
            key[i] = dict(app=app, origin=t['origin'], strict=t['strict'], oneway=t['oneway'],
                          reasons=t['reasons'])
random.Random(seed if '--seed' in flags else 20261019).shuffle(rows)
with open(sheet, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
json.dump(key, open(key_path, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
print(len(rows), 'items')
