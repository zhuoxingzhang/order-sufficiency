"""Self-check of the four deciders against exhaustive semantics on small schemas.

For each class a generator draws small random schemas; for every frozen set Fz >= PK the decider's
verdict is compared with the exhaustive search of ref_order2.Sem (the reference semantics): every modification update with at most n entities over a domain of d values is
built and tested for executability.  Bounds escalate along TIERS until a cycle is found; a task the
decider calls order-sufficient is searched exhaustively at the first tier of its class (the first
three tiers when the unrestricted condition disagrees).
  mismatch    decider says OS but the search finds a non-executable update (would refute Theorem 3.5
              or the implementation);
  unresolved  decider says not OS but no cycle within the largest tier.
Also compared: ref_order2.Schema.condition (the generic sound condition with unrestricted
implication); for class U the finite-implication verdicts that differ from it are counted.
Sem.search's budget counts legal update pairs only; the combinations it skips as illegal reach
10^9 at the top tiers of class K (relations of five attributes over domain 3), so for class K each
search also stops after WORK legality checks (deterministic; the task is then marked work_cut).
The unit of parallel work is one task (schema, Fz), since the largest schemas have 1,024 frozen sets.
Finished tasks are appended to <out.jsonl>.partial, which a rerun with the same arguments resumes
from; at the end the rows are grouped by schema, in the order of frozen_sets, into <out.jsonl>.

Usage: python -I selfcheck.py <class FD|U|A|K> <schemas> <seed> <procs> [out.jsonl]"""
import sys, os, random, itertools, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from multiprocessing import Pool
import deciders as D
from ref_order2 import Schema, Sem

TIERS = {
    'FD': [(2, 3), (3, 2)],
    'U': [(3, 2), (4, 2), (5, 2), (3, 3), (4, 3), (6, 3)],
    'A': [(3, 2), (4, 2), (5, 2), (6, 2), (3, 3)],
    'K': [(4, 2), (5, 2), (6, 2), (3, 3), (4, 3), (5, 3)],
}
BUDGET = 3_000_000
WORK = {'K': 20_000_000}


class WorkExceeded(Exception):
    pass


class BoundedSem(Sem):
    """Sem whose search stops after `work` legality checks."""
    def __init__(self, sch, Fz, work):
        super().__init__(sch, Fz)
        self.work, self.calls = work, 0

    def legal(self, state):
        self.calls += 1
        if self.calls > self.work:
            raise WorkExceeded
        return super().legal(state)


# ---------------------------------------------------------------------------- generators
def gen_FD(rng):
    na = rng.choice([3, 4, 4])
    at = ['a%d' % i for i in range(na)]
    pk = at[:rng.choice([1, 1, 2])]
    fds = []
    for _ in range(rng.randint(1, 3)):
        L = rng.sample(at, rng.choice([1, 1, 2]))
        a = rng.choice([x for x in at if x not in L])
        fds.append(('R', L, a))
    return {'rels': {'R': at}, 'pk': {'R': pk}, 'fds': fds, 'inds': []}


def gen_U(rng):
    nr = rng.choice([1, 2, 2])
    rels = {('R%d' % i): ['k%d' % i] + ['%s%d' % (x, i) for x in 'ab'[:rng.choice([1, 2])]] for i in range(nr)}
    pk = {r: a[:1] for r, a in rels.items()}
    fds = []
    for r, at in rels.items():
        for _ in range(rng.choice([0, 1, 1])):
            a = rng.choice(at)
            L = [rng.choice([x for x in at if x != a])]
            fds.append((r, L, a))
    nodes = [(r, a) for r, at in rels.items() for a in at]
    inds = set()
    for _ in range(rng.randint(1, 3)):
        (R, x), (S, y) = rng.sample(nodes, 2)
        if rng.random() < 0.4:      # from a key column: produces counting cycles
            x = rels[R][0]
        if (R, x) != (S, y):
            inds.add((R, (x,), S, (y,)))
    if not inds:
        return None
    return {'rels': rels, 'pk': pk, 'fds': fds, 'inds': [(R, list(X), S, list(Y)) for R, X, S, Y in sorted(inds)]}


def gen_A(rng):
    nr = rng.choice([2, 2, 3])
    rels, pk = {}, {}
    for i in range(nr):
        if nr == 2 and rng.random() < 0.25:
            pk['R%d' % i] = ['k%d' % i, 'l%d' % i]
        else:
            pk['R%d' % i] = ['k%d' % i]
        rels['R%d' % i] = list(pk['R%d' % i])
    inds = []
    for i in range(nr):
        for j in range(i + 1, nr):
            if rng.random() < 0.6:
                R, S = 'R%d' % i, 'R%d' % j
                X = ['f%d%d_%d' % (i, j, t) for t in range(len(pk[S]))]
                rels[R] += X
                inds.append((R, X, S, list(pk[S])))
    fds = []
    for r in rels:
        if len(rels[r]) < 3 and rng.random() < 0.7:
            rels[r].append('a' + r[1:])
        if rng.random() < 0.5 and len(rels[r]) >= 2:
            at = rels[r]
            a = rng.choice(at)
            L = rng.sample([x for x in at if x != a], 1)
            fds.append((r, L, a))
    if not inds or max(len(a) for a in rels.values()) > 4 or sum(len(a) for a in rels.values()) > 8:
        return None
    sch = {'rels': rels, 'pk': pk, 'fds': fds, 'inds': inds}
    return sch if D.class_A(sch)[0] else None


def gen_K(rng):
    n = rng.choice([2, 2, 3])
    rels = {'R%d' % i: ['k%d' % i] + ['%s%d' % (x, i) for x in 'ab'[:rng.choice([1, 2, 2])]] for i in range(n)}
    base = {r: list(a[1:]) for r, a in rels.items()}
    inds = []
    for i in range(n):
        for j in range(i + 1, n):
            for _ in range(rng.choice([0, 1, 1, 2])):
                R, S = 'R%d' % i, 'R%d' % j
                p = rng.choice(base[R])
                if any(x[0] == R and x[1][0] == p and x[2] == S for x in inds):
                    continue
                C = rng.sample(base[S], min(len(base[S]), rng.choice([0, 1, 1, 2])))
                Dd = []
                for c in C:
                    d = 'd%d%d' % (len(rels[R]), i)
                    rels[R].append(d)
                    Dd.append(d)
                inds.append((R, [p] + Dd, S, ['k%d' % j] + C))
    if not inds or max(len(a) for a in rels.values()) > 5:
        return None
    sch = {'rels': rels, 'pk': {r: a[:1] for r, a in rels.items()}, 'fds': [], 'inds': inds}
    return sch if D.parse_K(sch)[0] else None


GENS = {'FD': gen_FD, 'U': gen_U, 'A': gen_A, 'K': gen_K}


# ---------------------------------------------------------------------------- one schema
def ref_schema(sch, dom):
    return Schema({r: tuple(a) for r, a in sch['rels'].items()},
                  [(R, frozenset(L), a) for R, L, a in sch['fds']],
                  [(R, tuple(X), S, tuple(Y)) for R, X, S, Y in sch['inds']],
                  dom=dom, pk={r: tuple(p) for r, p in sch['pk'].items()})


def frozen_sets(sch):
    free = [(r, a) for r, at in sch['rels'].items() for a in at if a not in sch['pk'][r]]
    for k in range(len(free) + 1):
        for Z in itertools.combinations(free, k):
            Fz = {r: set(sch['pk'][r]) for r in sch['rels']}
            for r, a in Z:
                Fz[r].add(a)
            yield Fz


def schema(cls, seed):
    rng = random.Random(seed)
    sch = None
    while sch is None:
        sch = GENS[cls](rng)
    return sch


_cache = {}


def job(args):
    """one task: the schema of `seed` under its i-th frozen set"""
    cls, seed, i = args
    if _cache.get('key') != (cls, seed):
        sch = schema(cls, seed)
        _cache.clear()
        _cache.update(key=(cls, seed), sch=sch, fzs=list(frozen_sets(sch)), refs={})
    sch, refs, Fz = _cache['sch'], _cache['refs'], _cache['fzs'][i]
    t0 = time.perf_counter()
    ok, why = {'FD': D.dec_fd, 'U': D.dec_unary, 'A': D.dec_A, 'K': D.dec_K}[cls](sch, Fz)
    dt = time.perf_counter() - t0
    ref_ok = ref_schema(sch, 2).condition({r: set(v) for r, v in Fz.items()})[0]
    row = {'fz': {r: sorted(v) for r, v in Fz.items()}, 'dec': ok, 'ref': ref_ok, 'us': dt * 1e6}
    # not OS: escalate until a cycle; OS: exhaust the first tier, or the first three tiers
    # when only finite implication makes it OS (the unrestricted condition says not OS)
    tiers = TIERS[cls] if not ok else TIERS[cls][:3 if not ref_ok else 1]
    row['sem'] = None
    for n, d in tiers:
        if d not in refs:
            refs[d] = ref_schema(sch, d)
        fz = {r: set(v) for r, v in Fz.items()}
        sem = BoundedSem(refs[d], fz, WORK[cls]) if cls in WORK else Sem(refs[d], fz)
        try:
            out = sem.search(n, stop_cycle=True, want_noncomm=False, budget=BUDGET)
        except WorkExceeded:
            out = {'cycle': None, 'complete': False}
            row['work_cut'] = row.get('work_cut', 0) + 1
        if out['cycle']:
            row['sem'] = 'cycle'
            row['tier'] = [n, d]
            row['witness'] = out['cycle']
            break
        row['complete'] = out['complete']
    if row['sem'] is None:
        row['sem'] = 'none'
    return {'seed': seed, 'i': i, 'row': row}


def summarize(res):
    tot = mis = unres = ref_diff = cyc = osn = incomplete = finite_only = cut = unres_cut = os_cut = 0
    tiers = {}
    for r in res:
        for row in r['rows']:
            tot += 1
            cut += bool(row.get('work_cut'))
            if row.get('work_cut'):
                if row['dec']:
                    os_cut += 1
                elif row['sem'] != 'cycle':
                    unres_cut += 1
            if row['dec']:
                osn += 1
                if row['sem'] == 'cycle':
                    mis += 1
                if not row.get('complete', True):
                    incomplete += 1
            else:
                if row['sem'] == 'cycle':
                    cyc += 1
                    t = tuple(row['tier'])
                    tiers[t] = tiers.get(t, 0) + 1
                else:
                    unres += 1
            if row['dec'] != row['ref']:
                ref_diff += 1
                finite_only += row['dec'] and not row['ref']
    return dict(schemas=len(res), tasks=tot, dec_os=osn, dec_not_os_cycle_found=cyc, mismatch=mis,
                unresolved=unres, os_search_budget_hit=incomplete, differs_from_unrestricted_condition=ref_diff,
                os_only_under_finite_implication=finite_only, tasks_with_a_work_cut_search=cut,
                os_tasks_work_cut=os_cut, unresolved_work_cut=unres_cut,
                cycle_tiers={'%d entities, domain %d' % k: v for k, v in sorted(tiers.items())})


if __name__ == '__main__':
    cls, ns, seed, procs = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    out = sys.argv[5] if len(sys.argv) > 5 else None
    t0 = time.time()
    seeds = [seed * 100000 + i for i in range(ns)]
    schs = {s: schema(cls, s) for s in seeds}
    nfz = {s: sum(1 for _ in frozen_sets(schs[s])) for s in seeds}
    done = {}
    if out and os.path.exists(out + '.partial'):
        for line in open(out + '.partial'):
            try:
                r = json.loads(line)
            except ValueError:      # a line cut short when the run was stopped
                continue
            done[(r['seed'], r['i'])] = r['row']
    todo = [(cls, s, i) for s in seeds for i in range(nfz[s]) if (s, i) not in done]
    part = open(out + '.partial', 'a') if out else None
    with Pool(procs) as pool:
        for r in pool.imap_unordered(job, todo, chunksize=1):
            done[(r['seed'], r['i'])] = r['row']
            if part:
                part.write(json.dumps(r) + '\n')
                part.flush()
    res = [{'cls': cls, 'seed': s, 'sch': schs[s], 'rows': [done[(s, i)] for i in range(nfz[s])]} for s in seeds]
    if out:
        with open(out, 'w') as f:
            for r in res:
                f.write(json.dumps(r) + '\n')
        part.close()
        os.remove(out + '.partial')
    s = summarize(res)
    s['class'] = cls
    s['seconds'] = round(time.time() - t0, 1)
    print(json.dumps(s))
