"""Check that the Citus cluster places keys as work/keymap_citus.bin says.
Creates the benchmark tables if missing (x7bench.reset), then compares 3,000 random keys."""
import os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x7bench as B  # noqa: E402
import psycopg2  # noqa: E402

B.reset('citus')
km = B.load_keymap('citus')
c = psycopg2.connect(f'host={B.WORK}/sock port=29800 user=postgres dbname=postgres')
c.autocommit = True
cur = c.cursor()
cur.execute("SELECT DISTINCT nodeport FROM citus_shards WHERE table_name = 'parent'::regclass ORDER BY 1")
ports = [r[0] for r in cur.fetchall()]
assert ports == [29801, 29802, 29803], ports
bad = 0
for key in random.Random(7).sample(range(1, B.N_KEYS + 1), 3000):
    cur.execute("SELECT nodeport FROM citus_shards WHERE table_name = 'parent'::regclass "
                "AND shardid = get_shard_id_for_distribution_column('parent', %s)", (key,))
    bad += ports.index(cur.fetchone()[0]) != km[key]
print(f'keymap check: {bad} mismatches in 3000 keys')
sys.exit(1 if bad else 0)
