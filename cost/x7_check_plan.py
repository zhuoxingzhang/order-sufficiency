"""Offline check of x7bench.plan() on the operations of the benchmark templates: every precedence
edge stays inside one transaction or goes to a later level, the number of levels equals the minimum
number of commit rounds (1 + most edges between nodes on a path), and every node commits once.
usage: python -I x7_check_plan.py [KEYMAP]   (a random placement if no keymap is given)"""
import collections, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x7bench as B  # noqa: E402


class Fake:
    def __init__(self, n):
        self.N = n


def check(node_of, n_nodes, label):
    stats = collections.Counter()
    for wl, k in [(w, k) for w in ('ins', 'del', 'rep') for k in ((3 if w == 'rep' else 2), 8, 32, 128)]:
        for wid in range(8):
            R = 20_000
            pools = B.Pools(node_of, 1 + wid * R, 1 + (wid + 1) * R, n_nodes)
            W = B.Workload(wl, k, Fake(n_nodes), pools, wid, 40 if wl == 'del' else 0)
            for _ in range(40):
                st = W.next_op()
                levels = B.plan(st, node_of, wl)
                node = [node_of[a[1] if s == B.UPD_C else a[0]] for s, a in st]
                where = {}
                for li, lvl in enumerate(levels):
                    for gi, (nd, idx) in enumerate(lvl):
                        for j in idx:
                            assert node[j] == nd
                            where[j] = (li, nd)
                        B.fold([st[j] for j in idx])  # asserts that folding keeps every row
                assert sorted(where) == list(range(len(st)))
                lev = [0] * len(st)
                for u, v in sorted(B.prec_edges(wl, len(st)), key=lambda e: e[1]):
                    lev[v] = max(lev[v], lev[u] + (node[u] != node[v]))
                    assert where[u] == where[v] or where[u][0] < where[v][0], (wl, k, st, levels)
                assert len(levels) == max(lev) + 1
                txns = sum(len(l) for l in levels)
                assert txns == len(set(node)), 'a node commits more than once'
                stats[(wl, k, len(levels), txns)] += 1
    print(label)
    for key in sorted(stats):
        print('  %s k=%d: %d levels, %d transactions in %d operations' % (key + (stats[key],)))


if __name__ == '__main__':
    if len(sys.argv) > 1:
        km = open(sys.argv[1], 'rb').read()
        n = max(km[1:200_000]) + 1
        check(km, n, f'keymap {sys.argv[1]} ({n} nodes)')
    else:
        rnd = random.Random(1)
        for n in (2, 3):
            check(bytes(rnd.randrange(n) for _ in range(200_001)), n, f'random placement, {n} nodes')
