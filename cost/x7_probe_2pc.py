"""Does a method commit with two-phase commit on Citus?  The node that coordinates a distributed
transaction records it in its pg_dist_transaction until the next recovery; with recovery paused on
every node, the growth of these tables during a short run counts the 2PC commits of the method.
Under X7_ROUTE=direct the workers coordinate, so all four nodes are counted.
usage: python -I x7_probe_2pc.py WL K METHOD [METHOD ...]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg2  # noqa: E402
import x7bench as B  # noqa: E402

wl, k, methods = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
curs = []
for p in B.CITUS_NODES:
    c = psycopg2.connect(f'host={B.WORK}/sock port={p} user=postgres dbname=postgres')
    c.autocommit = True
    curs.append(c.cursor())


def count():
    n = []
    for cur in curs:
        cur.execute('SELECT count(*) FROM pg_dist_transaction')
        n.append(cur.fetchone()[0])
    return n


for cur in curs:
    cur.execute('ALTER SYSTEM SET citus.recover_2pc_interval = -1')
    cur.execute('SELECT pg_reload_conf()')
try:
    for m in methods:
        before = count()
        r = B.run('citus', wl, m, k, 1, 0, 1, warm=0.2, dur=1.5, prepop=400 if wl == 'del' else 0)
        after = count()
        grow = [a - b for a, b in zip(after, before)]
        print(f'{wl} k={k} {m} route={B.ROUTE}: {r["ops"]} ops counted, p50 {r["p50_ms"]:.2f} ms, '
              f'2PC records +{sum(grow)} {grow}, aborts {r["aborts"]}, errors {r["errors"]}', flush=True)
finally:
    for cur in curs:
        cur.execute('ALTER SYSTEM RESET citus.recover_2pc_interval')
        cur.execute('SELECT pg_reload_conf()')
    curs[0].execute('SELECT recover_prepared_transactions()')
