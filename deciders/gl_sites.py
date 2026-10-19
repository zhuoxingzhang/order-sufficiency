"""Separate the modifications of existing rows from initialisations in data/gitlab_mutable.json
(x4_shardk.py, scenario filtered).

A site initialises rather than modifies if it fills an attribute that is still empty (op '||='),
or if it runs inside a callback or factory that prepares a new row: methods named ensure_*, set_*,
assign_*, populate_*, prevent_nil_*, prepare_for_*, build*, initialize, new, self.create*,
self.build*, find_or_initialize*, before_*, after_initialize, default_* or init_*.
Usage: python -I gl_sites.py [data/gitlab_mutable.json] [data/gitlab_mutable_filtered.json]"""
import collections, json, os, re, sys

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))

INIT_METH = re.compile(r'^(ensure_|set_|assign_|populate_|prevent_nil|prepare_for|build|initialize$|new$|'
                       r'self\.create|self\.build|self\.new|find_or_initialize|self\.find_or_initialize|'
                       r'before_|after_initialize|default_|init_)')


def is_init(s):
    if s['op'] == '||=':
        return True
    if INIT_METH.match(s['meth'] or ''):
        return True
    return False


if __name__ == '__main__':
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'data', 'gitlab_mutable.json')
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, 'data', 'gitlab_mutable_filtered.json')
    d = json.load(open(src, encoding='utf-8'))
    pre = collections.defaultdict(set)
    init_only = collections.defaultdict(set)
    for s in d['sites']:
        for c in s['cols']:
            (init_only if is_init(s) else pre)[s['table']].add(c)
    # a column stays mutable if some non-initialising site modifies it
    init_only = {t: v - pre.get(t, set()) for t, v in init_only.items()}
    allp = {t: set(v) for t, v in d['precise'].items()}
    missing = {t: sorted(v - pre.get(t, set()) - init_only.get(t, set())) for t, v in allp.items()}
    missing = {t: v for t, v in missing.items() if v}
    json.dump(dict(precise={t: sorted(v) for t, v in pre.items()},
                   init_only={t: sorted(v) for t, v in init_only.items() if v},
                   not_in_sites=missing), open(out, 'w', encoding='utf-8'), indent=1)
    n_all = sum(len(v) for v in allp.values())
    n_pre = sum(len(v) for v in pre.values())
    n_init = sum(len(v) for v in init_only.values())
    print('precise columns', n_all, '-> modified', n_pre, 'init only', n_init,
          'not in sites', sum(len(v) for v in missing.values()))
    print('init sites', sum(is_init(s) for s in d['sites']), 'of', len(d['sites']))
