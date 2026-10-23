"""Share of templates that need only order, estimated from the blind hand labels of templates sampled
from the ten further applications (Section 6.2), stratified by app and strict verdict (sample_blind.py).

Each stratum (app, strict verdict of the analyzer in <out dir>) weighs its labelled items by its size
among the templates outside test and development code, so that the estimate refers to all templates of
the ten apps, not to the sample.  An item's stratum is looked up in <out dir> by its origin, so samples
drawn from an earlier analyzer version can be pooled.  A label is read one-way (atomic with derived =
yes counts as order); items listed in --derived=I1,I2 are read as derived as well (a sensitivity check).
A 95% interval comes from resampling the items within each stratum (2,000 rounds).
Each <dir>:<part,...> names the files <dir>/key_<part>.json (the sample) and <dir>/labels_<part>.csv
(the labels).  Reads <out dir>/<app>.json or <app>.json.gz.
Usage: python -I estimate.py <out dir> <dir>:<part,...> [<dir>:<part,...> ...] [--derived=...]
       python -I estimate.py ../out sample1:a,b,c sample2:u,v,w,x,y,z"""
import csv, gzip, json, os, random, re, sys
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8')
out = sys.argv[1]
specs = [a for a in sys.argv[2:] if not a.startswith('--')]
flags = {a.split('=', 1)[0]: a.split('=', 1)[1] for a in sys.argv[2:] if a.startswith('--') and '=' in a}
as_derived = set(filter(None, flags.get('--derived', '').split(',')))
TESTDEV = re.compile(r'(^|/)(tests?|spec|testing_support|factories|fixtures|development|seeders?|seeds?|test_utils?'
                     r'|testing)(/|\.|_)|random_data|curl_param_value_generators|/demo/')


def kept(t):
    return t['kind'] == 'event' or not TESTDEV.search(t['origin'])


tmpl, size = {}, Counter()
items = []
for spec in specs:
    d, parts = spec.rsplit(':', 1)
    for x in parts.split(','):
        key = json.load(open(os.path.join(d, 'key_%s.json' % x), encoding='utf-8'))
        for r in csv.DictReader(open(os.path.join(d, 'labels_%s.csv' % x), encoding='utf-8-sig')):
            k = key[r['item']]
            app = k['app']
            if app not in tmpl:
                p = os.path.join(out, app + '.json')
                d_ = (json.load(open(p, encoding='utf-8')) if os.path.exists(p)
                      else json.load(gzip.open(p + '.gz', 'rt', encoding='utf-8')))
                ts = [t for t in d_['templates'] if kept(t)]
                tmpl[app] = {}
                for t in ts:
                    size[(app, t['strict'])] += 1
                    tmpl[app].setdefault((t['kind'], t['origin']), []).append(t)
            hits = tmpl[app].get((r['kind'], k['origin']))
            if not hits:
                continue        # test or development code, or no longer a template
            t = next((h for h in hits if h['strict'] == k['strict']), hits[0])
            lab = r['label'].strip().lower()
            der = r['derived'].strip().lower().startswith('y') or r['item'] in as_derived
            one = 'order' if lab == 'atomic' and der else lab
            items.append(dict(item=r['item'], app=app, strict=t['strict'], oneway=t['oneway'], label=lab, one=one))

by = {}
for i in items:
    by.setdefault((i['app'], i['strict']), []).append(i)


def estimate(groups):
    est, miss = Counter(), Counter()
    for h, its in groups.items():
        w = size[h] / len(its)
        for i in its:
            est[i['one']] += w
            if i['oneway'] != 'atomic':
                miss['pool'] += w
                miss['hit'] += w * (i['one'] == 'atomic')
    con = est['order'] + est['atomic']
    return est, est['order'] / con if con else 1.0, miss['hit'] / miss['pool'] if miss['pool'] else 0.0


est, share, rate = estimate(by)
rng = random.Random(20261020)
boots = sorted(estimate({h: [rng.choice(its) for _ in its] for h, its in by.items()})[1] for _ in range(2000))
tot = sum(est.values())
print('labelled items %d in %d strata, weighting %d templates' % (len(items), len(by), sum(size[h] for h in by)))
print('one-way labels, raw:', dict(Counter(i['one'] for i in items)))
print('one-way labels, weighted: ' + ', '.join('%s %.0f (%.1f%%)' % (k, est[k], 100 * est[k] / tot)
                                                for k in ('none', 'order', 'atomic')))
print('order among constrained (annotator, weighted): %.1f%%, 95%% interval %.1f-%.1f%%'
      % (100 * share, 100 * boots[50], 100 * boots[1949]))
pool = [i for i in items if i['oneway'] != 'atomic']
print('analyzer order/none labelled atomic: %d of %d items; weighted %.1f%% of those templates'
      % (sum(i['one'] == 'atomic' for i in pool), len(pool), 100 * rate))
said = [i for i in items if i['oneway'] == 'atomic']
print('analyzer atomic labelled atomic: %d of %d items' % (sum(i['one'] == 'atomic' for i in said), len(said)))
for i in items:
    if i['one'] == 'atomic' or i['oneway'] == 'atomic':
        print('   %s %-11s analyzer %s/%s annotator %s (one-way %s)  stratum %d templates / %d items'
              % (i['item'], i['app'], i['strict'], i['oneway'], i['label'], i['one'],
                 size[(i['app'], i['strict'])], len(by[(i['app'], i['strict'])])))
