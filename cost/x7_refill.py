"""Move every block of a cell (template, rows, block) that lacks one of the given methods out of a
result file into OLD (appended), so that x7_scale_run.py reruns all methods of that block and cell
back to back.  For Vitess result files, a block also counts as incomplete if one of its runs started
within 30 s after vtgate was restarted (as listed in logs/vtgate_restarts.txt or
logs/vtgate_guard.log), since such a run meets a cold vtgate, or while vtgate ran under strace
(logs/vtgate_strace.<time>.txt).  These logs exist only if vtgate was restarted during the runs.
Prints the number of moved blocks.
usage: python -I x7_refill.py RESULTS.jsonl METHODS OLD.jsonl"""
import collections, datetime as dt, json, os, re, sys

path, methods, old = sys.argv[1], set(sys.argv[2].split(',')), sys.argv[3]
if not os.path.exists(path):
    print(0)
    sys.exit(0)
DAY0 = dt.date(2026, 10, 18)
restarts, traced = [], []
if 'vitess' in os.path.basename(path):
    for name in ('logs/vtgate_restarts.txt', 'logs/vtgate_guard.log'):
        if os.path.exists(name):
            day, prev = DAY0, None
            for line in open(name):
                m = re.match(r'(\d\d:\d\d:\d\d)( vtgate down; restarting)?\s*$', line)
                if m and (name.endswith('.txt') or m.group(2)):
                    t = dt.time.fromisoformat(m.group(1))
                    if prev is not None and t < prev:      # the log runs past midnight
                        day += dt.timedelta(days=1)
                    prev = t
                    restarts.append(dt.datetime.combine(day, t))
    # a traced vtgate runs from the time in the name of its strace file until the terminating signal
    # that strace printed last into it (the file's modification time); without such a signal, it
    # still runs or was killed outright, and the window stays open
    for name in sorted(os.listdir('logs')):
        m = re.match(r'vtgate_strace\.(\d\d)(\d\d)(\d\d)\.txt$', name)
        if not m:
            continue
        p = os.path.join('logs', name)
        mt = dt.datetime.fromtimestamp(os.path.getmtime(p))
        t = dt.time(*map(int, m.groups()))
        start = dt.datetime.combine(mt.date() - dt.timedelta(days=1 if t > mt.time() else 0), t)
        end = mt if '--- SIG' in open(p).read() else None
        traced.append([start, end])


def cold(r):
    t = dt.datetime.fromisoformat(r['start']).replace(tzinfo=None)
    return any(dt.timedelta(seconds=-1) <= t - x <= dt.timedelta(seconds=30) for x in restarts) \
        or any(a - dt.timedelta(seconds=60) <= t and (b is None or t <= b + dt.timedelta(seconds=30))
               for a, b in traced)


rows = [l for l in open(path)]
have = collections.defaultdict(set)
suspect = set()
for l in rows:
    r = json.loads(l)
    c = (r['wl'], r['k'], r['C'], r['rep'])
    have[c].add(r['method'])
    if cold(r):
        suspect.add(c)
bad = {c for c, ms in have.items() if not methods <= ms} | suspect
keep, move = [], []
for l in rows:
    r = json.loads(l)
    (move if (r['wl'], r['k'], r['C'], r['rep']) in bad else keep).append(l)
if move:
    with open(old, 'a') as f:
        f.writelines(move)
    with open(path, 'w') as f:
        f.writelines(keep)
print(len(bad))
for c in sorted(bad):
    print('  incomplete' if c not in suspect else '  after a vtgate restart or under strace', c,
          file=sys.stderr)
