"""Theorem 5.1: frozen-attribute choice in class K is weighted vertex cover on the conflict graph H.

Algorithms (pure Python, no dependencies):
  vc_mincut     exact on bipartite H: max-flow / min-cut (Koenig), Dinic.
  vc_2approx    local ratio (Bar-Yehuda and Even): one pass over the edges, then a clean-up pass
                that drops cover vertices whose neighbours are all in the cover.
  vc_lp         LP lower bound: min cut on the bipartite double cover equals twice the LP optimum
                (Nemhauser and Trotter); used to bound the 2-approximation on large general H.
  vc_exact      branch and bound per connected component, for small general H.
A graph is (w, E): w is a list of positive weights, E a list of index pairs.

`python -I m2.py <graphs> <seed>` cross-checks the four on random small graphs against brute force."""
import sys, os, random, itertools, time
from collections import deque
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------- max flow
class Dinic:
    def __init__(self, n):
        self.n = n
        self.head = [-1] * n
        self.to, self.cap, self.nxt = [], [], []

    def add(self, u, v, c):
        for a, b, cc in ((u, v, c), (v, u, 0)):
            self.to.append(b)
            self.cap.append(cc)
            self.nxt.append(self.head[a])
            self.head[a] = len(self.to) - 1

    def bfs(self, s, t):
        lvl = [-1] * self.n
        lvl[s] = 0
        q = deque([s])
        to, cap, nxt, head = self.to, self.cap, self.nxt, self.head
        while q:
            u = q.popleft()
            e = head[u]
            while e != -1:
                if cap[e] > 0 and lvl[to[e]] < 0:
                    lvl[to[e]] = lvl[u] + 1
                    q.append(to[e])
                e = nxt[e]
        self.lvl = lvl
        return lvl[t] >= 0

    def dfs(self, s, t):
        """iterative blocking-flow augmentation; returns flow pushed"""
        to, cap, nxt, lvl, it = self.to, self.cap, self.nxt, self.lvl, self.it
        total = 0
        while True:
            path, u = [], s
            while u != t:
                e = it[u]
                while e != -1 and not (cap[e] > 0 and lvl[to[e]] == lvl[u] + 1):
                    e = nxt[e]
                it[u] = e
                if e == -1:
                    if u == s:
                        return total
                    lvl[u] = -1
                    back = path.pop()
                    u = to[back ^ 1]
                    it[u] = nxt[it[u]]
                    continue
                path.append(e)
                u = to[e]
            f = min(cap[e] for e in path)
            for e in path:
                cap[e] -= f
                cap[e ^ 1] += f
            total += f

    def maxflow(self, s, t):
        flow = 0
        while self.bfs(s, t):
            self.it = list(self.head)
            flow += self.dfs(s, t)
        return flow

    def reach(self, s):
        seen = [False] * self.n
        seen[s] = True
        q = deque([s])
        while q:
            u = q.popleft()
            e = self.head[u]
            while e != -1:
                if self.cap[e] > 0 and not seen[self.to[e]]:
                    seen[self.to[e]] = True
                    q.append(self.to[e])
                e = self.nxt[e]
        return seen


# ---------------------------------------------------------------------------- helpers
def two_colour(n, E):
    adj = [[] for _ in range(n)]
    for u, v in E:
        adj[u].append(v)
        adj[v].append(u)
    col = [-1] * n
    for s in range(n):
        if col[s] >= 0:
            continue
        col[s] = 0
        q = deque([s])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if col[v] < 0:
                    col[v] = 1 - col[u]
                    q.append(v)
                elif col[v] == col[u]:
                    return None
    return col


def is_cover(E, C):
    return all(u in C or v in C for u, v in E)


def weight(w, C):
    return sum(w[v] for v in C)


# ---------------------------------------------------------------------------- algorithms
def vc_mincut(w, E, col=None):
    col = col if col is not None else two_colour(len(w), E)
    assert col is not None, 'graph is not bipartite'
    n = len(w)
    s, t = n, n + 1
    G = Dinic(n + 2)
    inf = sum(w) + 1
    for v in range(n):
        if col[v] == 0:
            G.add(s, v, w[v])
        else:
            G.add(v, t, w[v])
    for u, v in E:
        if col[u] == 1:
            u, v = v, u
        G.add(u, v, inf)
    G.maxflow(s, t)
    R = G.reach(s)
    return {v for v in range(n) if (col[v] == 0 and not R[v]) or (col[v] == 1 and R[v])}


def vc_2approx(w, E, cleanup=True):
    r = list(w)
    C = set()
    for u, v in E:
        if u in C or v in C:
            continue
        e = min(r[u], r[v])
        r[u] -= e
        r[v] -= e
        if r[u] == 0:
            C.add(u)
        if r[v] == 0:
            C.add(v)
    if cleanup:
        adj = {}
        for u, v in E:
            adj.setdefault(u, []).append(v)
            adj.setdefault(v, []).append(u)
        for v in sorted(C, key=lambda x: -w[x]):
            if all(x in C for x in adj.get(v, ())):
                C.discard(v)
    return C


def vc_lp(w, E):
    """LP optimum of weighted vertex cover via the bipartite double cover (value, half-integral x)."""
    n = len(w)
    w2 = list(w) + list(w)
    E2 = [(u, n + v) for u, v in E] + [(v, n + u) for u, v in E]
    col = [0] * n + [1] * n
    C = vc_mincut(w2, E2, col)
    x = [((v in C) + (n + v in C)) / 2 for v in range(n)]
    return sum(w[v] * x[v] for v in range(n)), x


def vc_exact(w, E, limit=10 ** 7):
    """branch and bound per component; returns (cover, exact flag)"""
    n = len(w)
    adj = [set() for _ in range(n)]
    for u, v in E:
        adj[u].add(v)
        adj[v].add(u)
    seen = [False] * n
    cover, exact = set(), True
    nodes_used = [0]
    for s in range(n):
        if seen[s] or not adj[s]:
            continue
        comp, q = [], [s]
        seen[s] = True
        while q:
            u = q.pop()
            comp.append(u)
            for v in adj[u]:
                if not seen[v]:
                    seen[v] = True
                    q.append(v)
        best = [vc_2approx(w, [(u, v) for u in comp for v in adj[u] if u < v])]
        bestw = [weight(w, best[0])]

        def rec(alive, chosen, cw):
            nodes_used[0] += 1
            if nodes_used[0] > limit:
                return
            if cw >= bestw[0]:
                return
            deg = {v: len(adj[v] & alive) for v in alive}
            act = [v for v in alive if deg[v] > 0]
            if not act:
                bestw[0], best[0] = cw, set(chosen)
                return
            # degree-one rule: take the neighbour when it is no heavier than the leaf
            for v in act:
                if deg[v] == 1:
                    (u,) = adj[v] & alive
                    if w[u] <= w[v]:
                        rec(alive - {u, v}, chosen | {u}, cw + w[u])
                        return
            # lower bound: maximal matching weight bound (sum of min weights over a greedy matching)
            lb, used = 0, set()
            for v in act:
                if v in used:
                    continue
                for u in adj[v] & alive:
                    if u not in used:
                        used |= {u, v}
                        lb += min(w[u], w[v])
                        break
            if cw + lb >= bestw[0]:
                return
            v = max(act, key=lambda x: deg[x])
            rec(alive - {v}, chosen | {v}, cw + w[v])
            N = adj[v] & alive
            rec(alive - N - {v}, chosen | N, cw + weight(w, N))

        rec(set(comp), set(), 0)
        if nodes_used[0] > limit:
            exact = False
        cover |= best[0]
    return cover, exact


def brute(w, E):
    n = len(w)
    best, bw = None, None
    for k in range(n + 1):
        for C in itertools.combinations(range(n), k):
            Cs = set(C)
            if is_cover(E, Cs):
                cw = weight(w, Cs)
                if bw is None or cw < bw:
                    best, bw = Cs, cw
    return best, bw


def selftest(count, seed):
    rng = random.Random(seed)
    bad = 0
    stats = {'bip': 0, 'gen': 0, 'ratio_max': 1.0}
    for _ in range(count):
        n = rng.randint(1, 10)
        w = [rng.randint(1, 9) for _ in range(n)]
        E = list({tuple(sorted(rng.sample(range(n), 2))) for _ in range(rng.randint(0, 2 * n))}) if n > 1 else []
        _, opt = brute(w, E)
        C2 = vc_2approx(w, E)
        lp, _ = vc_lp(w, E)
        Ce, ex = vc_exact(w, E)
        ok = is_cover(E, C2) and weight(w, C2) <= 2 * opt and lp <= opt + 1e-9 and ex and weight(w, Ce) == opt \
            and is_cover(E, Ce)
        if two_colour(n, E) is not None:
            stats['bip'] += 1
            Cm = vc_mincut(w, E)
            ok = ok and is_cover(E, Cm) and weight(w, Cm) == opt and abs(lp - opt) < 1e-9
        else:
            stats['gen'] += 1
        if opt:
            stats['ratio_max'] = max(stats['ratio_max'], weight(w, C2) / opt)
        if not ok:
            bad += 1
            if bad <= 3:
                print('FAIL', w, E, opt)
    print('m2 selftest: %d graphs (%d bipartite, %d not), failures %d, worst 2-approx ratio %.3f'
          % (count, stats['bip'], stats['gen'], bad, stats['ratio_max']))
    return bad


if __name__ == '__main__':
    selftest(int(sys.argv[1]), int(sys.argv[2]))
