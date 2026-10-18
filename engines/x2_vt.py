"""Repeated batches of the Vitess engine experiments behind Figure 5.

Every cell calls the functions of vt_exp.py; only the seed differs per batch.

usage: python -I x2_vt.py <cell> <n> <batch> <seed>
cells: v2 (parent first, ordered), v2swap (child first), v1 (scatter, txn)
"""
import json, os, random, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vt_exp as V  # noqa: E402

if __name__ == '__main__':
    cell, n, batch, seed = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    random.seed(seed)
    t0, l0 = time.time(), os.getloadavg()[0]
    mode = V.q(V.conn(), "select @@transaction_mode")[0][0]
    if cell == 'v2':
        r = [V.v2(n, 'atomic'), V.v2(n, 'ordered')]
    elif cell == 'v2swap':
        r = [V.v2(n, 'atomic_child_first')]
    elif cell == 'v1':
        r = [V.v1(n, 'scatter'), V.v1(n, 'txn')]
    else:
        raise SystemExit('unknown cell ' + cell)
    print(json.dumps({'engine': 'vitess', 'cell': cell, 'batch': batch, 'seed': seed, 'n': n,
                      'transaction_mode': mode,
                      'start': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(t0)),
                      'secs': round(time.time() - t0, 1), 'loadavg_start': l0,
                      'result': r}), flush=True)
