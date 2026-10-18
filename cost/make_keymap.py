"""Key -> node map for keys 1..N_KEYS, one byte per key (index 0 unused).

citus:  node i = i-th worker by port (29801, 29802, 29803); shard of key = hashint8(key) range in
        pg_dist_shard, checked against get_shard_id_for_distribution_column on a sample.
vitess: node 0 = shard -80, node 1 = shard 80-, from the hash vindex (select ... from hash).
usage: python -I make_keymap.py citus|vitess
"""
import bisect, io, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x7bench as B  # noqa: E402

N = B.N_KEYS


def citus():
    import psycopg2
    B.reset('citus')
    c = psycopg2.connect(B.CITUS_DSN)
    c.autocommit = True
    cur = c.cursor()
    cur.execute("SELECT s.shardminvalue::int, s.shardmaxvalue::int, p.nodeport FROM pg_dist_shard s "
                "JOIN pg_dist_shard_placement p USING (shardid) WHERE s.logicalrelid = 'parent'::regclass ORDER BY 1")
    rng = cur.fetchall()
    ports = sorted({r[2] for r in rng})
    assert ports == [29801, 29802, 29803], ports
    mins = [r[0] for r in rng]
    node = [ports.index(r[2]) for r in rng]
    buf = io.StringIO()
    cur.copy_expert(f'COPY (SELECT hashint8(k::bigint) FROM generate_series(1, {N}) k ORDER BY k) TO STDOUT', buf)
    hs = buf.getvalue().split()
    assert len(hs) == N
    out = bytearray(N + 1)
    for i, h in enumerate(hs, 1):
        out[i] = node[bisect.bisect_right(mins, int(h)) - 1]
    # cross-check against Citus' own routing
    for key in random.Random(1).sample(range(1, N + 1), 2000):
        cur.execute("SELECT nodeport FROM citus_shards WHERE table_name = 'parent'::regclass "
                    "AND shardid = get_shard_id_for_distribution_column('parent', %s)", (key,))
        assert ports.index(cur.fetchone()[0]) == out[key], key
    cur.execute("SELECT count(DISTINCT nodeport) FROM citus_shards WHERE table_name = 'child'::regclass")
    # child is colocated with parent, so the same map holds for child keys
    cur.execute("SELECT count(*) FROM pg_dist_partition a JOIN pg_dist_partition b USING (colocationid) "
                "WHERE a.logicalrelid = 'parent'::regclass AND b.logicalrelid = 'child'::regclass")
    assert cur.fetchone()[0] == 1
    return out


def vitess():
    import pymysql
    c = pymysql.connect(**B.VT)
    cur = c.cursor()
    out = bytearray(N + 1)
    step = 5000
    for lo in range(1, N + 1, step):
        ids = range(lo, min(N + 1, lo + step))
        cur.execute('select id, shard from hash where id in (' + ','.join(map(str, ids)) + ')')
        rows = cur.fetchall()
        assert len(rows) == len(ids)
        for i, s in rows:
            out[int(i)] = B.SHARDS.index(s.decode() if isinstance(s, bytes) else s)
    # cross-check: insert sample keys through vtgate and find them on the predicted shard
    B.reset('vitess')
    sample = random.Random(1).sample(range(1, N + 1), 200)
    for key in sample:
        cur.execute('insert into parent (pid) values (%s)', (key,))
    for n, s in enumerate(B.SHARDS):
        cur.execute(f'use `ks:{s}`')
        cur.execute('select pid from parent')
        got = {int(r[0]) for r in cur.fetchall()}
        assert got == {key for key in sample if out[key] == n}, s
    cur.execute('use ks')
    B.reset('vitess')
    return out


if __name__ == '__main__':
    eng = sys.argv[1]
    out = {'citus': citus, 'vitess': vitess}[eng]()
    with open(f'{B.WORK}/keymap_{eng}.bin', 'wb') as f:
        f.write(bytes(out))
    print(eng, 'keymap written;', 'node shares:', [out[1:].count(n) for n in range(3 if eng == 'citus' else 2)])
