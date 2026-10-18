"""Benchmark client for the cost of write order on Citus 14.2 and Vitess 24 (TwoPC): leveled commits
against one distributed transaction (two-phase commit) and ordered commits of one row each.

Workloads (k = number of entities = statements per business operation):
  ins  insert a parent and m = k-1 children           required order: parent before every child
  del  delete m = k-1 children and their parent        required order: every child before the parent
  rep  insert new parent P', repoint m = k-2 children to P', delete old parent P
                                                        required order: P' before children before P
  chain  insert a path of k rows, row i referencing row i - 1, consecutive rows on different nodes
         (rounds sweep, Figure 7): every hop changes nodes, so leveled commits need k rounds;
         a node with several transactions of one update gets one connection per transaction

One distributed transaction:
  a   one distributed transaction (Citus 2PC / Vitess TwoPC), statements one by one; Vitess issues
      the statements in a random order per operation (order-oblivious), Citus in the logical order
  h   one distributed transaction sent as one message (Citus; Vitess if it accepts multi-statements)
  hA  as h, but the children are folded into one multi-row statement (ins: INSERT ... VALUES list,
      del: DELETE ... WHERE cid IN, rep: UPDATE ... WHERE cid IN), which the engine splits by shard
  A   one distributed transaction with the children batched into one multi-row statement (ins, del:
      2 statements; rep: 3)
  c   Vitess only: one TwoPC transaction whose first write lands on the shard that must commit first
      (statement order = required order)

Ordered commits:
  b   k single-shard autocommit transactions, committed one after another in the required order
  o   ordered commits with a transactional outbox: the first write and an outbox row that lists the
      remaining writes commit in one single-shard transaction (the outbox row is colocated with the
      first row), the remaining writes follow as single-shard autocommit transactions in the
      required order, and the outbox row is deleted last.  Latency includes the deletion.
  g   Citus only: ordered commits grouped by node: the rows of one worker commit in one single-node
      transaction, sent as one message 'BEGIN; ...; COMMIT', and the nodes commit in the order in
      which the required order first touches them, which is a topological order of the merged
      precedence graph for these templates (checked per operation by groups()); a group of one row
      is a plain autocommit statement
  G   as g, but BEGIN, every statement and COMMIT are sent one by one

Leveled commits (Section 5.3):
  L   the changed rows are grouped into single-node transactions by plan(): one transaction per
      node when merging the nodes keeps the number of commit rounds minimal (always the case for
      these templates, asserted), and otherwise one per node and level.  Every transaction is opened
      and executed first, all in parallel over one connection per node ('BEGIN; ...' as one message
      on Citus, statements pipelined across the connections on Vitess); then the transactions commit
      level by level, those of one level in parallel.  A logical failure surfaces before any commit
      and aborts everything.
  E   as L, but the transactions of the first level commit in their first message (one round less;
      a later logical failure would need compensation)
  Lo  L with an outbox: every transaction of the first level also inserts an outbox row that lists
      the whole update (colocated with the transaction), so the update completes after a crash; the
      client deletes its outbox rows in batches of 64 per node between operations (counted in
      throughput, not in the latency of an operation)
  Ls  as L, but within every single-node transaction the children of every shard are folded into one
      multi-row statement (Vitess: one shard per node; Citus: work/shardmap_citus.bin, since a Citus
      statement over several shards of one worker makes even a single-node transaction use 2PC)
  Los, Es  Lo and E with the set statements of Ls
  Ps  ordered two-phase commit (Citus, X7_ROUTE=direct): Ls on the shard tables, every transaction
      prepared at the end of its first message, then COMMIT PREPARED level by level
  Lt  Ps without PREPARE (the control for the cost of the prepares)

Entry points: X7_ROUTE=direct makes every method on Citus enter at the workers; leveled commits and
b send each single-node transaction to the worker that holds its rows, one 2PC goes to the worker of
its first row, which coordinates it.

Environment:
  X7_WORK        directory with the cluster data, sockets and key maps (default: ./work)
  X7_ROUTE       coordinator (default) or direct, see above
  X6_CITUS_PORT  coordinator port (a delay relay in front of the coordinator, default 29800)
  X6_DELAY_MS    one-way delay of that relay, recorded with the result
  X6_VT_MODE     if set (e.g. multi), every Vitess session runs SET transaction_mode = '<mode>';
                 method c then is one transaction in that mode with the statements in the
                 required order

Placement: entity j of an operation lives on node (s + j) mod N (N = 3 Citus workers, 2 Vitess
shards), s rotating per operation, so consecutive entities sit on different nodes and an atomic
operation spans min(k, N) nodes. For rep, child j sits on node b + j and the parent alternates
between nodes b and b - 1 (b = client id mod N), so P' and P always sit on different nodes.
Rows are private to one client process, so no two operations conflict.

Closed loop: C client processes, each with one connection, run back to back from a common start;
only operations that complete inside [start + warm, start + warm + dur] count.

usage: python -I x7bench.py ENGINE WL METHOD K C REP SEED [warm dur prepop]
prints one JSON line.
"""
import json, multiprocessing as mp, os, random, sys, time

N_KEYS = 8_000_000
KEYS_PER_CLIENT = 1_000_000
WORK = os.environ.get('X7_WORK', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'work'))
OB_BATCH = 64
# X5_CITUS_OPTS: extra session settings for diagnostic runs only (empty in the grid)
CITUS_PORT = int(os.environ.get('X6_CITUS_PORT', '29800'))
VT_MODE = os.environ.get('X6_VT_MODE', '')
# through a relay the coordinator is reached over TCP, otherwise over its Unix socket
CITUS_HOST = '127.0.0.1' if CITUS_PORT != 29800 else f'{WORK}/sock'
CITUS_DSN = (f"host={CITUS_HOST} port={CITUS_PORT} user=postgres dbname=postgres "
             f"options='-c statement_timeout=30000 {os.environ.get('X5_CITUS_OPTS', '')}'")
CITUS_NODES = (29800, 29801, 29802, 29803)
# X7_ROUTE=direct: on Citus, every method enters at a worker (worker i at CITUS_PORT + 1 + i, the
# relay in front of it if CITUS_PORT is a relay port) instead of the coordinator
ROUTE = os.environ.get('X7_ROUTE', 'coordinator')


def worker_dsn(i):
    return CITUS_DSN.replace(f'port={CITUS_PORT} ', f'port={CITUS_PORT + 1 + i} ')
VT = dict(host='127.0.0.1', port=45306, user='root', database='ks', autocommit=True,
          read_timeout=60, write_timeout=60)
SHARDS = ('-80', '80-')


# ---------------------------------------------------------------- engine adapters
class Citus:
    N = 3
    name = 'citus'

    def __init__(self):
        import psycopg2
        self.err = psycopg2.Error
        self.c = psycopg2.connect(CITUS_DSN)
        self.c.autocommit = True
        self.cur = self.c.cursor()

    def x(self, sql, args=None):
        self.cur.execute(sql, args)

    def begin(self):
        self.cur.execute('BEGIN')

    def commit(self):
        self.cur.execute('COMMIT')

    def rollback(self):
        try:
            self.cur.execute('ROLLBACK')
        except Exception:  # noqa
            pass

    def bulk(self, parents, children):
        import io
        if parents:
            self.cur.copy_expert('COPY parent (pid) FROM STDIN', io.StringIO(''.join(f'{p}\n' for p in parents)))
        if children:
            self.cur.copy_expert('COPY child (cid, pid) FROM STDIN',
                                 io.StringIO(''.join(f'{c}\t{p}\n' for c, p in children)))


class Vitess:
    N = 2
    name = 'vitess'

    def __init__(self, multi=False):
        import pymysql
        from pymysql.constants import CLIENT
        self.err = pymysql.Error
        self.c = pymysql.connect(**VT, client_flag=CLIENT.MULTI_STATEMENTS) if multi else pymysql.connect(**VT)
        self.cur = self.c.cursor()
        if VT_MODE:
            self.cur.execute(f"set transaction_mode = '{VT_MODE}'")

    def x(self, sql, args=None):
        self.cur.execute(sql, args)
        while self.cur.nextset():  # several statements in one message (method h)
            pass

    def begin(self):
        self.cur.execute('begin')

    def commit(self):
        self.cur.execute('commit')

    def rollback(self):
        try:
            self.cur.execute('rollback')
        except Exception:  # noqa
            pass

    def bulk(self, parents, children, node_of):
        # shard-targeted multi-row inserts: preparation only, never timed
        for n, s in enumerate(SHARDS):
            ps = [p for p in parents if node_of[p] == n]
            cs = [(c, p) for c, p in children if node_of[c] == n]
            self.cur.execute(f'use `ks:{s}`')
            for i in range(0, len(ps), 2000):
                self.cur.execute('insert into parent (pid) values ' + ','.join(f'({p})' for p in ps[i:i + 2000]))
            for i in range(0, len(cs), 2000):
                self.cur.execute('insert into child (cid, pid) values ' +
                                 ','.join(f'({c},{p})' for c, p in cs[i:i + 2000]))
            self.cur.execute('use ks')


class CitusPar:
    """One asynchronous connection per worker; run() sends one message per connection, all before
    waiting for any, and returns {connection: error}."""

    def __init__(self, n, direct=False, dsns=None):
        import psycopg2
        import psycopg2.extensions as ext
        self.err, self.ext = psycopg2.Error, ext
        self.dsns = dsns or [worker_dsn(i % Citus.N) if direct else CITUS_DSN for i in range(n)]
        self.cs = [psycopg2.connect(d, async_=1) for d in self.dsns]
        self.wait(range(n))
        self.cur = [c.cursor() for c in self.cs]

    def reconnect(self):
        """Reopen every connection that the server or a relay closed; returns their indexes."""
        import psycopg2
        bad = [i for i, c in enumerate(self.cs) if c.closed]
        for i in bad:
            self.cs[i] = psycopg2.connect(self.dsns[i], async_=1)
        self.wait(bad)
        for i in bad:
            self.cur[i] = self.cs[i].cursor()
        return bad

    def wait(self, idx):
        import select
        pend, errs = list(idx), {}
        while pend:
            rd, wr = [], []
            for i in pend[:]:
                try:
                    st = self.cs[i].poll()
                except self.err as e:
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
        """jobs: [(connection, [(sql, args), ...])]; the statements of a job form one message."""
        sent, errs = [], {}
        for i, body in jobs:
            try:
                self.cur[i].execute('; '.join(s for s, _ in body), tuple(x for _, a in body if a for x in a))
                sent.append(i)
            except self.err as e:           # e.g. a connection that a lost message closed
                errs[i] = e
        errs.update(self.wait(sent))
        return errs


class VitessPar:
    """One connection per shard that accepts several statements per message; run() sends one message
    per connection, all before reading any reply, and returns {connection: error}."""

    def __init__(self, n):
        import pymysql
        from pymysql.constants import CLIENT, COMMAND
        self.err, self.Q = pymysql.Error, COMMAND.COM_QUERY
        self.cs = [pymysql.connect(**VT, client_flag=CLIENT.MULTI_STATEMENTS) for _ in range(n)]
        if VT_MODE:
            for c in self.cs:
                c.cursor().execute(f"set transaction_mode = '{VT_MODE}'")
        self.cur = [c.cursor() for c in self.cs]

    def run(self, jobs):
        """jobs: [(connection, [(sql, args), ...])]; the statements of a job form one message."""
        for i, body in jobs:
            self.cs[i]._execute_command(self.Q, '; '.join(self.cur[i].mogrify(s, a) for s, a in body))
        errs = {}
        for i, _ in jobs:
            c = self.cs[i]
            try:
                c._read_query_result()
                while c._result.has_next:
                    c.next_result()
            except self.err as e:
                errs[i] = e
        return errs


ENGINES = {'citus': Citus, 'vitess': Vitess}
PAR = {'citus': CitusPar, 'vitess': VitessPar}
LEVELED = ('L', 'E', 'Lo', 'Ls', 'Los', 'Es', 'Ps', 'Lt')
FOLDED = ('Ls', 'Los', 'Es', 'Ps', 'Lt')  # children folded into one set statement per shard
# Ordered 2PC (Citus, X7_ROUTE=direct only): as Ls, but every single-node transaction writes the
# shard tables by name and ends its first message with PREPARE TRANSACTION, so that all of them are
# prepared (durably) before any commits; then COMMIT PREPARED level by level.  Citus refuses PREPARE
# TRANSACTION after a statement on a distributed table name ("cannot use 2PC in transactions
# involving multiple servers"), hence the shard tables (probe_prepare.sh).  Lt is the same without
# PREPARE, the control that isolates the cost of the prepares.
PREPARED = ('Ps',)
SHARDTAB = ('Ps', 'Lt')
OUTBOX = ('Lo', 'Los')                # every first-round transaction also writes an outbox row
EAGER = ('E', 'Es')                   # the first round commits in its first message
ONE_MESSAGE = ('h', 'hA')


def load_keymap(engine):
    with open(f'{WORK}/keymap_{engine}.bin', 'rb') as f:
        return f.read()


# ---------------------------------------------------------------- key pools
class Pools:
    """Fresh keys per node from this client's private key range."""

    def __init__(self, node_of, lo, hi, n):
        self.q = [[] for _ in range(n)]
        for key in range(lo, hi):
            self.q[node_of[key]].append(key)
        self.i = [0] * n

    def take(self, node):
        i = self.i[node]
        if i >= len(self.q[node]):
            raise IndexError('key pool exhausted')
        self.i[node] = i + 1
        return self.q[node][i]


# ---------------------------------------------------------------- operations
INS_P = 'INSERT INTO parent (pid) VALUES (%s)'
INS_C = 'INSERT INTO child (cid, pid) VALUES (%s, %s)'
DEL_C = 'DELETE FROM child WHERE cid = %s'
DEL_P = 'DELETE FROM parent WHERE pid = %s'
UPD_C = 'UPDATE child SET pid = %s WHERE cid = %s'
OUTBOX_INS = 'INSERT INTO outbox (oid, payload) VALUES (%s, %s)'
OUTBOX_DEL = 'DELETE FROM outbox WHERE oid = %s'


def groups(stmts, node_of, wl):
    """Statements grouped by node in the order of first touch; asserts that every precedence edge
    of the template (ins: parent -> children; del: children -> parent; rep: P' -> children -> P)
    leads to the same or a later group, so that one commit per node is a legal order."""
    nodes = [node_of[args[1] if sql == UPD_C else args[0]] for sql, args in stmts]
    idx, out = {}, []
    for n, st in zip(nodes, stmts):
        if n not in idx:
            idx[n] = len(out)
            out.append([])
        out[idx[n]].append(st)
    g, last = [idx[n] for n in nodes], len(stmts) - 1
    if wl == 'ins':
        edges = [(0, i) for i in range(1, last + 1)]
    elif wl == 'del':
        edges = [(i, last) for i in range(last)]
    else:
        edges = [(0, i) for i in range(1, last + 1)] + [(i, last) for i in range(1, last)]
    assert all(g[u] <= g[v] for u, v in edges), 'first-touch order of nodes is not topological'
    return out


def fold(stmts, shard_of=None):
    """The statements of one single-node transaction with the children of every shard folded into one
    multi-row statement per kind (method Ls): parent insert, child rows, parent delete.  shard_of maps
    a key to its shard (Citus, several shards per worker); None means one shard per node (Vitess).
    A statement on one shard is a router statement, so the transaction keeps one connection and
    commits without 2PC."""
    def shard(key):
        return 0 if shard_of is None else shard_of[key]
    out = [(s, a) for s, a in stmts if s == INS_P]
    n = len(out)
    for kind in (INS_C, UPD_C, DEL_C):
        by = {}
        for s, a in stmts:
            if s == kind:
                by.setdefault(shard(a[1] if kind == UPD_C else a[0]), []).append(a)
        for rows in by.values():
            n += len(rows)
            if kind == INS_C:
                out.append(('INSERT INTO child (cid, pid) VALUES ' + ', '.join(['(%s, %s)'] * len(rows)),
                            tuple(x for a in rows for x in a)))
            elif kind == UPD_C:
                assert len({a[0] for a in rows}) == 1, 'children moved to different parents'
                out.append(('UPDATE child SET pid = %s WHERE cid IN (' + ', '.join(['%s'] * len(rows)) + ')',
                            (rows[0][0],) + tuple(a[1] for a in rows)))
            else:
                out.append(('DELETE FROM child WHERE cid IN (' + ', '.join(['%s'] * len(rows)) + ')',
                            tuple(a[0] for a in rows)))
    dp = [(s, a) for s, a in stmts if s == DEL_P]
    assert n + len(dp) == len(stmts), 'folding lost a row'
    return out + dp


def stmt_node(sql, args, node_of):
    """Node of the first row a statement writes (the child for a repointing update)."""
    return node_of[args[1] if sql == UPD_C else args[0]]


def one_message(stmts):
    return 'BEGIN; ' + '; '.join(s for s, _ in stmts) + '; COMMIT', tuple(x for _, a in stmts for x in a)


def prec_edges(wl, n):
    """Precedence edges of a template of n statements in the required order (every edge goes from a
    lower to a higher index): ins parent -> children, del children -> parent, rep P' -> children ->
    P."""
    last = n - 1
    if wl == 'ins':
        return [(0, i) for i in range(1, n)]
    if wl == 'del':
        return [(i, last) for i in range(last)]
    if wl == 'chain':
        return [(i, i + 1) for i in range(last)]
    return [(0, i) for i in range(1, last)] + [(i, last) for i in range(1, last)]


def plan(stmts, node_of, wl):
    """Leveled commits.  The level of a row is the largest number of edges between different nodes
    on a path of the precedence graph that ends in it; 1 + the largest level is the minimum number
    of commit rounds.  Returns a list of levels, each a list of (node, [statement indices]): one
    transaction per node if merging the nodes leaves an acyclic graph of that depth, otherwise one
    per node and level."""
    n = len(stmts)
    node = [node_of[args[1] if sql == UPD_C else args[0]] for sql, args in stmts]
    E = prec_edges(wl, n)
    lev = [0] * n
    for u, v in sorted(E, key=lambda e: e[1]):
        lev[v] = max(lev[v], lev[u] + (node[u] != node[v]))
    rounds = max(lev) + 1
    nodes = sorted(set(node))
    nE = {(node[u], node[v]) for u, v in E if node[u] != node[v]}
    nlev, ok = {}, True
    for _ in range(len(nodes) + 1):  # longest path on the merged graph; diverges iff it is cyclic
        changed = False
        for a in nodes:
            x = max([nlev.get(s, 0) + 1 for s, t in nE if t == a], default=0)
            if nlev.get(a) != x:
                nlev[a], changed = x, True
        if not changed:
            break
    else:
        ok = False
    if ok and max(nlev.values()) + 1 == rounds:
        key = [(nlev[node[i]], node[i]) for i in range(n)]
    else:
        key = [(lev[i], node[i]) for i in range(n)]
    out = [[] for _ in range(rounds)]
    grp = {}
    for i in range(n):
        if key[i] not in grp:
            grp[key[i]] = (key[i][1], [])
            out[key[i][0]].append(grp[key[i]])
        grp[key[i]][1].append(i)
    assert all(lvl for lvl in out), 'empty level'
    return out


class Workload:
    """Generates, per operation, the statements in the required order."""

    def __init__(self, wl, k, eng_cls, pools, wid, prepop):
        self.wl, self.k, self.N, self.pools = wl, k, eng_cls.N, pools
        self.t = wid  # rotation offset
        self.prepared = ([], [])  # (parents, children) rows to bulk-load before the start
        if wl == 'del':
            self.chains = []
            for _ in range(prepop):
                ch = self._new_chain()
                self.chains.append(ch)
            self.ci = 0
            self.prepared = ([p for p, _ in self.chains], [(c, p) for p, cs in self.chains for c in cs])
        elif wl == 'rep':
            # client-private children, child j on node b + j; the parent alternates between
            # nodes b and b - 1, so P' and P always sit on different nodes
            b = wid % self.N
            self.homes = (b, (b - 1) % self.N)
            self.kids = [pools.take((b + j) % self.N) for j in range(1, k - 1)]
            self.flip = 0
            self.cur_parent = pools.take(self.homes[1])
            self.prepared = ([self.cur_parent], [(c, self.cur_parent) for c in self.kids])

    def _new_chain(self):
        s = self.t % self.N
        self.t += 1
        p = self.pools.take(s)
        return p, [self.pools.take((s + j) % self.N) for j in range(1, self.k)]

    def next_op(self, batched=False):
        """List of (sql, args): one statement per entity in the required order, or, if batched,
        the children folded into one multi-row statement."""
        if self.wl == 'ins':
            p, cs = self._new_chain()
            if batched:
                return [(INS_P, (p,)), ('INSERT INTO child (cid, pid) VALUES ' + ', '.join(['(%s, %s)'] * len(cs)),
                                        tuple(x for c in cs for x in (c, p)))]
            return [(INS_P, (p,))] + [(INS_C, (c, p)) for c in cs]
        if self.wl == 'chain':
            s = self.t % self.N
            self.t += 1
            ks = [self.pools.take((s + i) % self.N) for i in range(self.k)]
            if batched:
                return [(INS_P, (ks[0],)), ('INSERT INTO child (cid, pid) VALUES ' + ', '.join(['(%s, %s)'] * (self.k - 1)),
                                            tuple(x for i in range(1, self.k) for x in (ks[i], ks[i - 1])))]
            return [(INS_P, (ks[0],))] + [(INS_C, (ks[i], ks[i - 1])) for i in range(1, self.k)]
        if self.wl == 'del':
            if self.ci >= len(self.chains):
                raise IndexError('prepopulated chains exhausted')
            p, cs = self.chains[self.ci]
            self.ci += 1
            if batched:
                return [('DELETE FROM child WHERE cid IN (' + ', '.join(['%s'] * len(cs)) + ')', tuple(cs)),
                        (DEL_P, (p,))]
            return [(DEL_C, (c,)) for c in cs] + [(DEL_P, (p,))]
        # rep
        old = self.cur_parent
        new = self.pools.take(self.homes[self.flip])
        self.flip ^= 1
        self.cur_parent = new
        if batched:
            return [(INS_P, (new,)),
                    ('UPDATE child SET pid = %s WHERE cid IN (' + ', '.join(['%s'] * len(self.kids)) + ')',
                     (new,) + tuple(self.kids)),
                    (DEL_P, (old,))]
        return [(INS_P, (new,))] + [(UPD_C, (new, c)) for c in self.kids] + [(DEL_P, (old,))]


# ---------------------------------------------------------------- client process
def make_workload(engine, wl, k, C, wid, prepop, node_of):
    E = ENGINES[engine]
    R = min(N_KEYS // C, KEYS_PER_CLIENT)
    return Workload(wl, k, E, Pools(node_of, 1 + wid * R, 1 + (wid + 1) * R, E.N), wid, prepop)


def preload(engine, wl, k, C, prepop):
    """Delete chains of all clients, loaded through one connection in one bulk operation (the
    clients rebuild the same chains deterministically and do not load them again)."""
    if wl != 'del':
        return 0
    node_of = load_keymap(engine)
    ps, cs = [], []
    for w in range(C):
        p, c = make_workload(engine, wl, k, C, w, prepop, node_of).prepared
        ps += p
        cs += c
    db = ENGINES[engine]()
    if engine == 'vitess':
        db.bulk(ps, cs, node_of)
    else:
        db.bulk(ps, cs)
    db.c.close()
    return len(ps) + len(cs)


class SetupError(Exception):
    pass


def client(*args):
    """Runs client_body and reports any exception to the orchestrator, which then stops at once."""
    try:
        client_body(*args)
    except Exception as e:  # noqa
        import traceback
        phase = 'setup' if isinstance(e, SetupError) else 'run'
        args[-1].put({'wid': args[5], 'error': phase + ': ' + traceback.format_exc()[-1500:]})
        raise


def client_body(engine, wl, method, k, C, wid, seed, prepop, bulk_lock, go, t_start, t_end, t_stop, out):
    try:
        E = ENGINES[engine]
        node_of = load_keymap(engine)
        W = make_workload(engine, wl, k, C, wid, prepop, node_of)
        db = E(multi=True) if (engine == 'vitess' and method in ONE_MESSAGE) else E()
        if wl == 'rep':
            # one loader at a time: a Citus COPY opens a connection and a prepared transaction per
            # shard, so 64 concurrent loads exhaust max_connections and max_prepared_transactions
            with bulk_lock:
                if engine == 'vitess':
                    db.bulk(*W.prepared, node_of)
                else:
                    db.bulk(*W.prepared)
        rnd = random.Random(seed * 1000 + wid)
        direct = ROUTE == 'direct' and engine == 'citus'
        S = -(-k // E.N) if wl == 'chain' else 1    # connections per node
        if direct:
            par = CitusPar(E.N * S, direct=True)       # every method enters at the workers
        else:
            par = PAR[engine](E.N * S) if method in LEVELED else None
        if method in OUTBOX and engine == 'citus':
            # outbox cleanup deletes rows of several shards of one worker: one connection per worker
            db.x("SET citus.multi_shard_modify_mode TO 'sequential'")
            if direct:
                par.run([(i, [("SET citus.multi_shard_modify_mode TO 'sequential'", None)]) for i in range(E.N)])
        shard_of = None
        if (method in FOLDED or (method in OUTBOX and direct)) and engine == 'citus':
            # one set statement per shard (a router statement), so that every single-node
            # transaction keeps one connection and commits without 2PC (x7_probe_2pc.py)
            shard_of = open(f'{WORK}/shardmap_citus.bin', 'rb').read()
        if method in SHARDTAB:
            assert engine == 'citus' and direct and wl != 'chain', 'Ps and Lt need Citus with X7_ROUTE=direct'
            db.x("SELECT logicalrelid::text, shard_name(logicalrelid, shardid) FROM pg_dist_shard "
                 "WHERE logicalrelid IN ('parent'::regclass, 'child'::regclass) ORDER BY shardminvalue::int")
            shard_names = {'parent': [], 'child': []}
            for rel, name in db.cur.fetchall():
                shard_names[rel].append(name)
            assert len(shard_names['parent']) == len(shard_names['child']), 'parent and child not colocated'
            par.run([(i, [('SET citus.override_table_visibility TO off', None)]) for i in range(E.N)])

            def on_shards(body):
                """Rewrite every statement onto the shard table of its rows (one shard per statement)."""
                out = []
                for sql, args in body:
                    if args is None:
                        out.append((sql, args))
                        continue
                    rel = 'parent' if ' parent ' in sql else 'child'
                    key = args[1] if sql.startswith('UPDATE child') else args[0]
                    out.append((sql.replace(f' {rel} ', f' {shard_names[rel][shard_of[key]]} ', 1), args))
                return out
    except Exception as e:  # noqa
        raise SetupError(repr(e)[:300]) from e
    out.put({'wid': wid, 'ready': True})
    go.wait()
    lat, clat, dlat, aborts, attempts, ops_win = [], [], [], 0, 0, 0
    rounds_sum = txns_sum = 0
    ob_pending = [[] for _ in range(E.N)]
    errors = {}
    exhausted = False
    consec = 0
    now = time.time
    pc = time.perf_counter_ns
    opn = 0
    while now() < t_stop.value:
        try:
            stmts = W.next_op(batched=(method in ('A', 'hA')))
        except IndexError:
            exhausted = True
            break
        opn += 1
        t0 = pc()
        a_here = att_here = 0
        tc = td = 0
        rounds_here = txns_here = 0
        if direct and method in ('b',) + ONE_MESSAGE:
            if method == 'b':
                todo = [(stmt_node(sql, args, node_of), [(sql, args)]) for sql, args in stmts]
            else:                                   # one 2PC, coordinated by the worker of its first row
                todo = [(stmt_node(*stmts[0], node_of), [one_message(stmts)])]
            for nd, body in todo:
                while True:
                    att_here += 1
                    errs = par.run([(nd, body)])
                    if not errs:
                        break
                    if method != 'b':
                        par.run([(nd, [('ROLLBACK', None)])])
                    a_here += 1
                    for e in errs.values():
                        key = type(e).__name__ + ':' + str(e)[:80]
                        errors[key] = errors.get(key, 0) + 1
                    consec += 1
                    if consec > 1000:
                        raise RuntimeError(f'{method} keeps failing: {errs}')
        elif method == 'b':
            for sql, args in stmts:
                while True:
                    att_here += 1
                    try:
                        db.x(sql, args)
                        break
                    except db.err as e:
                        a_here += 1
                        key = type(e).__name__ + ':' + str(e)[:80]
                        errors[key] = errors.get(key, 0) + 1
                        consec += 1
                        if consec > 1000:
                            raise
        elif method == 'o':
            okey = stmts[0][1][0]          # key of the first row; the outbox row shares its shard
            payload = json.dumps(stmts[1:])
            while True:
                att_here += 1
                try:
                    db.begin()
                    db.x(*stmts[0])
                    db.x(OUTBOX_INS, (okey, payload))
                    db.commit()
                    break
                except db.err as e:
                    db.rollback()
                    a_here += 1
                    key = type(e).__name__ + ':' + str(e)[:80]
                    errors[key] = errors.get(key, 0) + 1
                    consec += 1
                    if consec > 1000:
                        raise
            for sql, args in stmts[1:] + [(OUTBOX_DEL, (okey,))]:
                if sql is OUTBOX_DEL:
                    td = pc()                  # every data write is visible here
                while True:
                    att_here += 1
                    try:
                        db.x(sql, args)
                        break
                    except db.err as e:
                        a_here += 1
                        key = type(e).__name__ + ':' + str(e)[:80]
                        errors[key] = errors.get(key, 0) + 1
                        consec += 1
                        if consec > 1000:
                            raise
        elif method in LEVELED:
            levels = plan(stmts, node_of, wl)
            slot, conn = {}, {}
            for li, lvl in enumerate(levels):         # one connection per transaction of a node
                for nd, _ in lvl:
                    conn[li, nd] = nd + E.N * slot.get(nd, 0)
                    slot[nd] = slot.get(nd, 0) + 1
            payload = json.dumps(stmts) if method in OUTBOX else None
            while True:
                att_here += 1
                jobs, obs = [], []
                for li, lvl in enumerate(levels):
                    for nd, idx in lvl:
                        rows = [stmts[j] for j in idx]
                        body = [('BEGIN', None)] + (fold(rows, shard_of) if method in FOLDED else rows)
                        if method in OUTBOX and li == 0:
                            okey = W.pools.take(nd)          # a fresh key on this node
                            body.append((OUTBOX_INS, (okey, payload)))
                            obs.append((nd, okey))
                        if method in EAGER and li == 0:
                            body.append(('COMMIT', None))
                        if method in SHARDTAB:
                            body = on_shards(body)
                        if method in PREPARED:
                            body = body + [(f"PREPARE TRANSACTION 'y4_{wid}_{opn}_{conn[li, nd]}'", None)]
                        jobs.append((conn[li, nd], body))
                errs = par.run(jobs)                          # execute everything, in parallel
                if not errs:
                    for li, lvl in enumerate(levels):         # then commit level by level
                        if method in EAGER and li == 0:
                            continue
                        fin = (lambda c: f"COMMIT PREPARED 'y4_{wid}_{opn}_{c}'") if method in PREPARED \
                            else (lambda c: 'COMMIT')
                        errs = par.run([(conn[li, nd], [(fin(conn[li, nd]), None)]) for nd, _ in lvl])
                        if errs:
                            raise RuntimeError(f'commit of level {li} failed: {errs}')
                    break
                par.run([(nd, [('ROLLBACK', None)]) for nd, _ in jobs])
                if method in PREPARED:                        # undo the transactions already prepared
                    for c, _ in jobs:
                        par.run([(c, [(f"ROLLBACK PREPARED 'y4_{wid}_{opn}_{c}'", None)])])
                a_here += 1
                for e in errs.values():
                    key = type(e).__name__ + ':' + str(e)[:80]
                    errors[key] = errors.get(key, 0) + 1
                consec += 1
                if consec > 1000:
                    raise RuntimeError(f'leveled commit keeps failing: {errs}')
            rounds_here, txns_here = len(levels), sum(len(l) for l in levels)
            for nd, okey in obs:
                ob_pending[nd].append(okey)
        elif method in ('g', 'G') + ONE_MESSAGE:
            for grp in (groups(stmts, node_of, wl) if method not in ONE_MESSAGE else [stmts]):
                while True:
                    att_here += 1
                    try:
                        if len(grp) == 1 and method not in ONE_MESSAGE:
                            db.x(*grp[0])          # a single-row group is a single-shard autocommit
                        elif method == 'G':
                            db.begin()
                            for sql, args in grp:
                                db.x(sql, args)
                            db.commit()
                        else:
                            db.x(*one_message(grp))
                        break
                    except db.err as e:
                        db.rollback()
                        a_here += 1
                        key = type(e).__name__ + ':' + str(e)[:80]
                        errors[key] = errors.get(key, 0) + 1
                        consec += 1
                        if consec > 1000:
                            raise
        else:
            if method == 'a' and engine == 'vitess':
                stmts = stmts[:]
                rnd.shuffle(stmts)
            while True:
                att_here += 1
                try:
                    db.begin()
                    for sql, args in stmts:
                        db.x(sql, args)
                    tc = pc()
                    db.commit()
                    break
                except db.err as e:
                    db.rollback()
                    a_here += 1
                    key = type(e).__name__ + ':' + str(e)[:80]
                    errors[key] = errors.get(key, 0) + 1
                    consec += 1
                    if consec > 1000:
                        raise
        t1 = pc()
        consec = 0
        for nd in range(E.N):                     # outbox cleanup, between operations
            if len(ob_pending[nd]) >= OB_BATCH:
                ks = ob_pending[nd]
                sql = 'DELETE FROM outbox WHERE oid IN (' + ', '.join(['%s'] * len(ks)) + ')'
                if direct:                        # one router statement per shard, one message
                    by = {}
                    for key in ks:
                        by.setdefault(shard_of[key], []).append(key)
                    body = [('DELETE FROM outbox WHERE oid IN (' + ', '.join(['%s'] * len(g)) + ')', tuple(g))
                            for g in by.values()]
                    errs = par.run([(nd, body)])
                    if errs:
                        raise RuntimeError(f'outbox cleanup failed: {errs}')
                else:
                    db.x(sql, tuple(ks))
                ob_pending[nd] = []
        tw = now()
        if t_start.value <= tw <= t_end.value:
            lat.append(t1 - t0)
            rounds_sum += rounds_here
            txns_sum += txns_here
            if tc:
                clat.append(t1 - tc)
            if td:
                dlat.append(td - t0)
            ops_win += 1
            aborts += a_here
            attempts += att_here
    out.put({'wid': wid, 'lat': lat, 'clat': clat, 'dlat': dlat, 'ops': ops_win, 'aborts': aborts, 'attempts': attempts,
             'exhausted': exhausted, 'errors': errors, 'rounds': rounds_sum, 'txns': txns_sum})


# ---------------------------------------------------------------- orchestration
def reset(engine):
    if engine == 'citus':
        import psycopg2
        c = psycopg2.connect(CITUS_DSN)
        c.autocommit = True
        cur = c.cursor()
        if not table_exists(cur):
            cur.execute('DROP TABLE IF EXISTS parent, child')
            cur.execute('CREATE TABLE parent (pid bigint PRIMARY KEY)')
            cur.execute('CREATE TABLE child (cid bigint PRIMARY KEY, pid bigint NOT NULL)')
            cur.execute("SELECT create_distributed_table('parent', 'pid')")
            cur.execute("SELECT create_distributed_table('child', 'cid', colocate_with => 'parent')")
        cur.execute("SELECT to_regclass('outbox') IS NOT NULL")
        if not cur.fetchone()[0]:
            cur.execute('CREATE TABLE outbox (oid bigint PRIMARY KEY, payload text NOT NULL)')
            cur.execute("SELECT create_distributed_table('outbox', 'oid', colocate_with => 'parent')")
        cur.execute('TRUNCATE parent, child, outbox')
        c.close()
    else:
        import pymysql
        c = pymysql.connect(**VT)
        cur = c.cursor()
        for s in SHARDS:
            cur.execute(f'use `ks:{s}`')
            cur.execute('truncate table parent')
            cur.execute('truncate table child')
            cur.execute('truncate table outbox')
        c.close()


def table_exists(cur):
    cur.execute("SELECT to_regclass('parent') IS NOT NULL AND to_regclass('child') IS NOT NULL")
    return cur.fetchone()[0]


def checkpoint(engine):
    if engine != 'citus':
        return
    import psycopg2
    for p in CITUS_NODES:
        c = psycopg2.connect(f'host={WORK}/sock port={p} user=postgres dbname=postgres')
        c.autocommit = True
        c.cursor().execute('CHECKPOINT')
        c.close()


def pct(sorted_v, q):
    if not sorted_v:
        return None
    i = min(len(sorted_v) - 1, max(0, int(round(q * len(sorted_v) + 0.5)) - 1))
    return sorted_v[i]


def run(engine, wl, method, k, C, rep, seed, warm=3.0, dur=10.0, prepop=0):
    reset(engine)
    tl = time.time()
    loaded = preload(engine, wl, k, C, prepop)
    load_secs = time.time() - tl
    ctx = mp.get_context('fork')
    go, bulk_lock = ctx.Event(), ctx.Lock()
    t_start, t_end, t_stop = ctx.Value('d', 0.0), ctx.Value('d', 0.0), ctx.Value('d', 1e18)
    out = ctx.Queue()
    procs = [ctx.Process(target=client, args=(engine, wl, method, k, C, w, seed, prepop, bulk_lock, go,
                                               t_start, t_end, t_stop, out)) for w in range(C)]
    for p in procs:
        p.start()
    errs = []
    for _ in range(C):
        m = out.get(timeout=600)
        if 'error' in m:
            errs.append(m['error'])
    if errs:
        for p in procs:
            p.terminate()
        raise SystemExit('client setup failed: ' + errs[0])
    checkpoint(engine)
    load0 = os.getloadavg()[0]
    t0 = time.time() + 0.5
    t_start.value, t_end.value, t_stop.value = t0 + warm, t0 + warm + dur, t0 + warm + dur
    while time.time() < t0:
        time.sleep(0.01)
    go.set()
    res = []
    for _ in range(C):
        m = out.get(timeout=dur + warm + 600)
        if 'error' in m:
            for p in procs:
                p.terminate()
            raise SystemExit('client failed: ' + m['error'])
        res.append(m)
    for p in procs:
        p.join(timeout=60)
    lat = sorted(x for r in res for x in r['lat'])
    clat = sorted(x for r in res for x in r['clat'])
    dlat = sorted(x for r in res for x in r['dlat'])
    ops = sum(r['ops'] for r in res)
    aborts = sum(r['aborts'] for r in res)
    attempts = sum(r['attempts'] for r in res)
    txn_attempts = attempts  # method b: one attempt per single-shard transaction
    errors = {}
    for r in res:
        for e, n in r['errors'].items():
            errors[e] = errors.get(e, 0) + n
    return {
        'engine': engine, 'wl': wl, 'method': method, 'k': k, 'C': C, 'rep': rep, 'seed': seed,
        'citus_opts': os.environ.get('X5_CITUS_OPTS', '') if engine == 'citus' else '',
        'citus_port': CITUS_PORT if engine == 'citus' else None, 'vt_mode': VT_MODE if engine == 'vitess' else None,
        'route': ROUTE if engine == 'citus' else None,
        'delay_ms': float(os.environ.get('X6_DELAY_MS', '0')),
        'warm': warm, 'dur': dur, 'prepop': prepop, 'preloaded_rows': loaded, 'preload_secs': round(load_secs, 1),
        'start': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(t0)), 'loadavg': round(load0, 2),
        'ops': ops, 'tput': ops / dur,
        'p50_ms': pct(lat, 0.50) / 1e6 if lat else None,
        'p95_ms': pct(lat, 0.95) / 1e6 if lat else None,
        'p99_ms': pct(lat, 0.99) / 1e6 if lat else None,
        'mean_ms': sum(lat) / len(lat) / 1e6 if lat else None,
        'max_ms': lat[-1] / 1e6 if lat else None,
        'commit_p50_ms': pct(clat, 0.50) / 1e6 if clat else None,
        'data_p50_ms': pct(dlat, 0.50) / 1e6 if dlat else None,
        'commit_mean_ms': sum(clat) / len(clat) / 1e6 if clat else None,
        'aborts': aborts, 'txn_attempts': txn_attempts,
        'rounds_per_op': sum(r['rounds'] for r in res) / ops if ops else None,
        'txns_per_op': sum(r['txns'] for r in res) / ops if ops else None,
        'abort_rate': aborts / txn_attempts if txn_attempts else None,
        'exhausted': sum(1 for r in res if r['exhausted']),
        'errors': errors,
    }


if __name__ == '__main__':
    a = sys.argv[1:]
    engine, wl, method, k, C, rep, seed = a[0], a[1], a[2], int(a[3]), int(a[4]), int(a[5]), int(a[6])
    warm = float(a[7]) if len(a) > 7 else 3.0
    dur = float(a[8]) if len(a) > 8 else 10.0
    prepop = int(a[9]) if len(a) > 9 else 0
    print(json.dumps(run(engine, wl, method, k, C, rep, seed, warm, dur, prepop)), flush=True)
