"""Helpers shared by the recovery runs (w6_recovery.py) and the deadlock runs (w6_deadlock.py) on the
Citus cluster of cost/ (cost/citus_cluster.sh: coordinator 29800, workers 29801-29803, Citus MX).
W6_PORT and W6_SOCK override the coordinator port and the socket directory; X7_WORK is the data
directory of that cluster (default cost/work), which holds the socket and keymap_citus.bin.

The runs use their own tables, colocated with the table parent of cost/, so that keymap_citus.bin gives the
worker of every key (checked on a sample by node_map):
  w6_parent (pid bigint primary key, ts bigint not null default 0)   a group
  w6_child  (cid bigint primary key, pid bigint not null)            an item; pid -> w6_parent, unchecked
  w6_outbox (oid bigint primary key, payload text not null)
Node n in 0..2 is the n-th worker by port, as in the keymap.

The pure functions (plan, Zipf, classify, sccs, backoff_secs) import nothing beyond the standard
library, so that w6_mock.py can test them without a database.
"""
import bisect, io, os, random, time

WORK = os.environ.get('X7_WORK') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'cost', 'work')
PORT = int(os.environ.get('W6_PORT', '29800'))
SOCK = os.environ.get('W6_SOCK', f'{WORK}/sock')
N_KEYS = 8_000_000
BACKOFF_BASE, BACKOFF_CAP = 0.0005, 0.020


def dsn(i, app='w6', opts=''):
    """Node i of the cluster: 0 is the coordinator, 1..3 the workers."""
    return (f"host={SOCK} port={PORT + i} user=postgres dbname=postgres application_name={app} "
            f"options='-c statement_timeout=30000 {opts}'")


def connect(i=0, app='w6', opts=''):
    import psycopg2
    c = psycopg2.connect(dsn(i, app, opts))
    c.autocommit = True
    return c


# ---------------------------------------------------------------- statements
INS_P = 'INSERT INTO w6_parent (pid) VALUES (%s)'
INS_P_IDEM = 'INSERT INTO w6_parent (pid) VALUES (%s) ON CONFLICT DO NOTHING'
DEL_P = 'DELETE FROM w6_parent WHERE pid = %s'
INS_C = 'INSERT INTO w6_child (cid, pid) VALUES (%s, %s)'
UPD_C = 'UPDATE w6_child SET pid = %s WHERE cid = %s'
TOUCH = 'UPDATE w6_parent SET ts = ts + 1 WHERE pid = %s'
OB_INS = 'INSERT INTO w6_outbox (oid, payload) VALUES (%s, %s)'
OB_DEL = 'DELETE FROM w6_outbox WHERE oid = %s'


def one_message(stmts):
    return 'BEGIN; ' + '; '.join(s for s, _ in stmts) + '; COMMIT', tuple(x for _, a in stmts for x in a)


# ---------------------------------------------------------------- parallel connections
class Par:
    """One asynchronous connection per worker (index = keymap node).  run() sends one message per
    connection, all before waiting for any, and returns {node: error}; fetch() returns the rows of
    the last statement of the last message on a connection."""

    def __init__(self, app='w6', opts=''):
        import psycopg2
        import psycopg2.extensions as ext
        self.pg, self.ext = psycopg2, ext
        self.dsns = [dsn(1 + n, app, opts) for n in range(3)]
        self.cs, self.cur = [None] * 3, [None] * 3
        self.reconnect()

    def reconnect(self):
        """Opens every connection that is missing or closed; returns their indexes."""
        bad = [i for i in range(3) if self.cs[i] is None or self.cs[i].closed]
        for i in bad:
            self.cs[i] = self.pg.connect(self.dsns[i], async_=1)
        errs = self.wait(bad)
        if errs:
            raise RuntimeError(f'connect failed: {errs}')
        for i in bad:
            self.cur[i] = self.cs[i].cursor()
        return bad

    def close(self, idx):
        """A crash of the client: the server sees the connections end, aborts their open transactions
        and releases their locks."""
        for i in idx:
            self.cs[i].close()

    def wait(self, idx):
        import select
        pend, errs = list(idx), {}
        while pend:
            rd, wr = [], []
            for i in pend[:]:
                try:
                    st = self.cs[i].poll()
                except self.pg.Error as e:
                    errs[i] = e
                    pend.remove(i)
                    continue
                if st == self.ext.POLL_OK:
                    pend.remove(i)
                elif st == self.ext.POLL_READ:
                    rd.append(self.cs[i].fileno())
                elif st == self.ext.POLL_WRITE:
                    wr.append(self.cs[i].fileno())
            if rd or wr:
                select.select(rd, wr, [], 1.0)
        return errs

    def run(self, jobs):
        """jobs: [(node, [(sql, args), ...])]; the statements of a job form one message."""
        sent, errs = [], {}
        for i, body in jobs:
            try:
                self.cur[i].execute('; '.join(s for s, _ in body), tuple(x for _, a in body if a for x in a))
                sent.append(i)
            except self.pg.Error as e:
                errs[i] = e
        errs.update(self.wait(sent))
        return errs

    def fetch(self, i):
        return self.cur[i].fetchall()

    def rollback(self, idx):
        return self.run([(i, [('ROLLBACK', None)]) for i in idx])


def chk(errs, what='statement'):
    if errs:
        raise RuntimeError(f'{what} failed: ' + '; '.join(f'node {i}: {str(e).strip()[:200]}' for i, e in errs.items()))


# ---------------------------------------------------------------- leveled commits
def plan(nodes, edges):
    """Leveled commits (Prop. rounds) for entities on the given nodes, with precedence edges (u, v):
    v commits no earlier than u, and in a later round if it sits on another node.  The level of an
    entity is the largest number of edges between nodes on a path that ends in it.  Returns the
    rounds, each a list of (node, [entity indices]): one transaction per node if merging the
    entities of every node keeps the number of rounds minimal, otherwise one per node and level."""
    n = len(nodes)
    lev = [0] * n
    for _ in range(n + 1):
        changed = False
        for u, v in edges:
            x = lev[u] + (nodes[u] != nodes[v])
            if x > lev[v]:
                lev[v], changed = x, True
        if not changed:
            break
    else:
        raise ValueError('a cycle of the precedence graph crosses nodes')
    rounds = max(lev) + 1
    ns = sorted(set(nodes))
    ne = {(nodes[u], nodes[v]) for u, v in edges if nodes[u] != nodes[v]}
    nlev, ok = {a: 0 for a in ns}, True
    for _ in range(len(ns) + 1):
        changed = False
        for s, t in ne:
            if nlev[s] + 1 > nlev[t]:
                nlev[t], changed = nlev[s] + 1, True
        if not changed:
            break
    else:
        ok = False
    if ok and max(nlev.values()) + 1 == rounds:
        key = [(nlev[nodes[i]], nodes[i]) for i in range(n)]
    else:
        key = [(lev[i], nodes[i]) for i in range(n)]
    out, grp = [[] for _ in range(rounds)], {}
    for i in range(n):
        if key[i] not in grp:
            grp[key[i]] = (key[i][1], [])
            out[key[i][0]].append(grp[key[i]])
        grp[key[i]][1].append(i)
    assert all(out), 'empty round'
    return out


# ---------------------------------------------------------------- workload helpers
class Zipf:
    """Ranks 0..n-1 with probability proportional to 1 / (rank + 1) ** theta."""

    def __init__(self, n, theta, seed):
        self.rnd = random.Random(seed)
        acc, self.cdf = 0.0, []
        for i in range(n):
            acc += 1.0 / (i + 1) ** theta
            self.cdf.append(acc)

    def __call__(self):
        return bisect.bisect_left(self.cdf, self.rnd.random() * self.cdf[-1])


class Pools:
    """Fresh keys per node from the key range [lo, hi)."""

    def __init__(self, node_of, lo, hi):
        self.q = [[], [], []]
        for key in range(lo, hi):
            self.q[node_of[key]].append(key)
        self.i = [0, 0, 0]

    def take(self, node):
        i = self.i[node]
        if i >= len(self.q[node]):
            raise IndexError('key pool exhausted')
        self.i[node] = i + 1
        return self.q[node][i]


def classify(e):
    """Kind of a failed attempt, from the PostgreSQL or Citus error message."""
    m = str(e).lower()
    if 'could not obtain lock' in m:
        return 'nowait'
    if 'lock timeout' in m:
        return 'lock_timeout'
    if 'distributed deadlock' in m:
        return 'deadlock_dist'
    if 'deadlock detected' in m:
        return 'deadlock_local'
    if 'statement timeout' in m:
        return 'stmt_timeout'
    return 'other'


def backoff_secs(attempt, rnd):
    """Randomized exponential backoff after the attempt-th failure (full jitter)."""
    return rnd.uniform(0.0, min(BACKOFF_CAP, BACKOFF_BASE * 2 ** (attempt - 1)))


def sccs(edges):
    """Strongly connected components with at least two clients of the wait-for graph given as edges
    (waiter, holder, worker); returns [(frozenset of clients, frozenset of workers of its internal
    edges)].  Tarjan's algorithm, iterative."""
    adj = {}
    for a, b, _ in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set())
    index, low, on, stack, comps, cnt = {}, {}, set(), [], [], [0]
    for root in adj:
        if root in index:
            continue
        work = [(root, iter(adj[root]))]
        index[root] = low[root] = cnt[0]
        cnt[0] += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            nxt = next(it, None)
            if nxt is not None:
                if nxt not in index:
                    index[nxt] = low[nxt] = cnt[0]
                    cnt[0] += 1
                    stack.append(nxt)
                    on.add(nxt)
                    work.append((nxt, iter(adj[nxt])))
                elif nxt in on:
                    low[v] = min(low[v], index[nxt])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = set()
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.add(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    comps.append(comp)
    out = []
    for comp in comps:
        ws = frozenset(w for a, b, w in edges if a in comp and b in comp)
        out.append((frozenset(comp), ws))
    return out


def pct(v, q):
    if not v:
        return None
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(round(q * len(v) + 0.5)) - 1))]


def wait_until(t):
    """Sleeps until time.perf_counter() reaches t, spinning for the last 0.2 ms."""
    while True:
        d = t - time.perf_counter()
        if d <= 0:
            return
        if d > 0.0003:
            time.sleep(d - 0.0002)


# ---------------------------------------------------------------- tables and placement
def setup_tables():
    """Creates the tables of these runs if missing (colocated with the table parent of cost/, which is
    created first on a fresh cluster) and empties them."""
    c = connect(0, 'w6setup')
    cur = c.cursor()
    cur.execute("SELECT to_regclass('parent') IS NOT NULL")
    if not cur.fetchone()[0]:
        cur.execute('CREATE TABLE parent (pid bigint PRIMARY KEY)')
        cur.execute("SELECT create_distributed_table('parent', 'pid')")
    cur.execute("SELECT to_regclass('w6_parent') IS NOT NULL")
    if not cur.fetchone()[0]:
        cur.execute('CREATE TABLE w6_parent (pid bigint PRIMARY KEY, ts bigint NOT NULL DEFAULT 0)')
        cur.execute('CREATE TABLE w6_child (cid bigint PRIMARY KEY, pid bigint NOT NULL)')
        cur.execute('CREATE TABLE w6_outbox (oid bigint PRIMARY KEY, payload text NOT NULL)')
        cur.execute("SELECT create_distributed_table('w6_parent', 'pid', colocate_with => 'parent')")
        cur.execute("SELECT create_distributed_table('w6_child', 'cid', colocate_with => 'parent')")
        cur.execute("SELECT create_distributed_table('w6_outbox', 'oid', colocate_with => 'parent')")
    cur.execute('TRUNCATE w6_parent, w6_child, w6_outbox')
    c.close()


def drop_tables():
    c = connect(0, 'w6setup')
    c.cursor().execute('DROP TABLE IF EXISTS w6_parent, w6_child, w6_outbox')
    c.close()


def node_map(sample=300):
    """Node of every key 1..N_KEYS: keymap_citus.bin of cost/ if it agrees with Citus on a random
    sample of keys, otherwise built from the shard ranges of w6_parent."""
    c = connect(0, 'w6setup')
    cur = c.cursor()
    cur.execute("SELECT DISTINCT nodeport FROM citus_shards WHERE table_name = 'w6_parent'::regclass ORDER BY 1")
    ports = [r[0] for r in cur.fetchall()]
    assert ports == [PORT + 1, PORT + 2, PORT + 3], ports
    kp = f'{WORK}/keymap_citus.bin'
    km = open(kp, 'rb').read() if os.path.exists(kp) else b''
    bad = 0
    for key in random.Random(11).sample(range(1, N_KEYS + 1), sample):
        cur.execute("SELECT nodeport FROM citus_shards WHERE table_name = 'w6_parent'::regclass "
                    "AND shardid = get_shard_id_for_distribution_column('w6_parent', %s)", (key,))
        bad += not km or ports.index(cur.fetchone()[0]) != km[key]
    if not bad:
        c.close()
        return km
    import sys
    print(f'[node_map] keymap_citus.bin disagrees on {bad} of {sample} keys; building the map',
          file=sys.stderr, flush=True)
    cur.execute("SELECT s.shardminvalue::int, p.nodeport FROM pg_dist_shard s JOIN pg_dist_shard_placement p "
                "USING (shardid) WHERE s.logicalrelid = 'w6_parent'::regclass ORDER BY 1")
    rng = cur.fetchall()
    mins, node = [r[0] for r in rng], [ports.index(r[1]) for r in rng]
    buf = io.StringIO()
    cur.copy_expert(f'COPY (SELECT hashint8(k::bigint) FROM generate_series(1, {N_KEYS}) k ORDER BY k) TO STDOUT', buf)
    out = bytearray(N_KEYS + 1)
    for i, h in enumerate(buf.getvalue().split(), 1):
        out[i] = node[bisect.bisect_right(mins, int(h)) - 1]
    for key in random.Random(12).sample(range(1, N_KEYS + 1), sample):
        cur.execute("SELECT nodeport FROM citus_shards WHERE table_name = 'w6_parent'::regclass "
                    "AND shardid = get_shard_id_for_distribution_column('w6_parent', %s)", (key,))
        assert ports.index(cur.fetchone()[0]) == out[key], key
    c.close()
    return bytes(out)


def copy_rows(cur, table, cols, rows):
    if rows:
        cur.copy_expert(f'COPY {table} ({", ".join(cols)}) FROM STDIN',
                        io.StringIO(''.join('\t'.join(map(str, r)) + '\n' for r in rows)))
