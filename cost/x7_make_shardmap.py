"""Shard of every benchmark key on Citus, for method Ls (one set statement per shard).
Writes work/shardmap_citus.bin: byte i = index of the shard of table child that holds key i, in the
order of pg_dist_shard.shardminvalue.  The hash is Citus' own (worker_hash); a sample of keys is
checked against get_shard_id_for_distribution_column.
usage: python -I x7_make_shardmap.py   (Citus must be running)"""
import bisect, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg2  # noqa: E402
import x7bench as B  # noqa: E402

N = B.N_KEYS + 1
c = psycopg2.connect(f'host={B.WORK}/sock port=29800 user=postgres dbname=postgres')
c.autocommit = True
cur = c.cursor()
B.reset('citus')
cur.execute("SELECT shardid, shardminvalue::int, shardmaxvalue::int FROM pg_dist_shard "
            "WHERE logicalrelid = 'child'::regclass ORDER BY shardminvalue::int")
shards = cur.fetchall()
assert len(shards) <= 255
ids = [s for s, _, _ in shards]
mins = [lo for _, lo, _ in shards]
out = bytearray(N)
STEP = 500_000
for lo in range(0, N, STEP):
    hi = min(N, lo + STEP) - 1
    cur.execute('SELECT worker_hash(k::bigint) FROM generate_series(%s::bigint, %s::bigint) AS k ORDER BY k', (lo, hi))
    for i, (h,) in enumerate(cur.fetchall()):
        out[lo + i] = bisect.bisect_right(mins, h) - 1
rnd = random.Random(1)
sample = [rnd.randrange(1, N) for _ in range(5000)]
cur.execute('SELECT k, get_shard_id_for_distribution_column(%s, k) FROM unnest(%s::bigint[]) AS k', ('child', sample))
bad = sum(1 for k, sid in cur.fetchall() if ids[out[k]] != sid)
assert bad == 0, f'{bad} of 5000 sampled keys disagree with get_shard_id_for_distribution_column'
open(f'{B.WORK}/shardmap_citus.bin', 'wb').write(bytes(out))
print(f'shardmap: {len(shards)} shards, {N} keys, sample of 5000 agrees')
