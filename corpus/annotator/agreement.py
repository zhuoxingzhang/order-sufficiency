"""Agreement between our labels and a second annotator who did not see them (Sections 6.1 and 6.2).

templates.csv holds the annotator's label for each of the 97 templates of GitLab and Sentry (order, atomic,
serial, none); our labels are those of ../out/templates.csv.  sites.csv holds the annotator's label for
each of the 181 read-path sites (FP, T, E); our verdicts are those of ../out/readpath_sites.json (Sentry,
where a batch fetch B counts as tolerant) and ../gitlab_readpath_review.json (GitLab).
Prints the raw agreement and Cohen's kappa, overall and per application.
Usage: python -I agreement.py"""
import collections
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.dirname(HERE)
OURS = {'need only order': 'order', 'need atomic visibility': 'atomic', 'unconstrained': 'none'}


def kappa(pairs, labels):
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    ca = collections.Counter(a for a, _ in pairs)
    cb = collections.Counter(b for _, b in pairs)
    pe = sum(ca[l] * cb[l] for l in labels) / n / n
    return po, (po - pe) / (1 - pe)


def read_csv(path):
    with open(path, encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def templates():
    ours = {(r['application'], r['template']): OURS[r['label']] for r in read_csv(os.path.join(CORPUS, 'out', 'templates.csv'))}
    rows = [(r['application'], ours[(r['application'], r['template'])], r['label']) for r in read_csv(os.path.join(HERE, 'templates.csv'))]
    print('Templates (rows: ours, columns: annotator; order, atomic, serial, none)')
    labels = ['order', 'atomic', 'serial', 'none']
    for app in ('all', 'GitLab', 'Sentry'):
        ps = [(a, b) for x, a, b in rows if app in ('all', x)]
        po, k = kappa(ps, labels)
        print('  %-6s n=%3d  agreement %.3f  kappa %.3f' % (app, len(ps), po, k))
    suff = [('yes' if a in ('order', 'none') else 'no', 'yes' if b in ('order', 'none') else 'no') for _, a, b in rows]
    po, k = kappa(suff, ['yes', 'no'])
    print('  order suffices: %d of %d agree, kappa %.3f' % (sum(a == b for a, b in suff), len(suff), k))
    m = collections.Counter((a, b) for _, a, b in rows)
    for a in labels:
        print('    %-6s' % a, ' '.join('%4d' % m[(a, b)] for b in labels))


def sites():
    ours = {}
    for s in json.load(open(os.path.join(CORPUS, 'out', 'readpath_sites.json'), encoding='utf-8')):
        lab = 'T' if s['label'] == 'B' else s['label']
        ours.setdefault(('Sentry', s['file'].replace('src/sentry/', ''), int(s['line'])), set()).add(lab)
    sample = json.load(open(os.path.join(CORPUS, 'out', 'gitlab_readpath_sample.json'), encoding='utf-8'))
    verdicts = json.load(open(os.path.join(CORPUS, 'gitlab_readpath_review.json'), encoding='utf-8'))['verdicts']
    for i, s in enumerate(sample):
        ours.setdefault(('GitLab', s['file'], int(s['line'])), set()).add(verdicts[str(i)][0])
    rows = []
    for r in read_csv(os.path.join(HERE, 'sites.csv')):
        f = r['file'].replace('src/sentry/', '') if r['application'] == 'Sentry' else r['file']
        mine = ours[(r['application'], f, int(r['line']))]
        assert len(mine) == 1, r['item']
        rows.append((r['application'], next(iter(mine)), r['label']))
    print('Read-path sites (FP: no read from a child to its parent, T: tolerates a missing parent, E: fails)')
    for app in ('all', 'GitLab', 'Sentry'):
        sel = [(a, b) for x, a, b in rows if app in ('all', x)]
        po, k = kappa(sel, ['FP', 'T', 'E'])
        g = [('G' if a != 'FP' else 'FP', 'G' if b != 'FP' else 'FP') for a, b in sel]
        pg, kg = kappa(g, ['G', 'FP'])
        tol = collections.Counter(a for a, _ in sel)
        print('  %-6s n=%3d  agreement %.3f  kappa %.3f  (genuine vs FP %.3f, kappa %.3f)  ours: %d of %d genuine sites tolerate'
              % (app, len(sel), po, k, pg, kg, tol['T'], tol['T'] + tol['E']))


if __name__ == '__main__':
    templates()
    sites()
