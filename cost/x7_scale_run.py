"""Scaling runs: the templates at 2 or 3, 8, 32 and 128 rows.  Within each block the methods of one
cell run back to back in a random order.

Delete chains are preloaded; the number per client is estimated from the rate of ordered commits at
8 rows (preload_rates.json) scaled by 8 / k with a margin of 3, doubled if a run exhausts it, and
capped by the client's key range.
usage: python -I x7_scale_run.py ENGINE OUT TAG BLOCKS CS METHODS [KS] [warm dur]
       KS = comma-separated row counts (default 8,32,128 plus 2 for ins/del and 3 for rep)
       a method written M@d runs M with X7_ROUTE=direct (on Citus every method enters at the
       workers) and is recorded as M@d
"""
import json, os, random, subprocess, sys, time, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
N_KEYS, KEYS_PER_CLIENT = 8_000_000, 1_000_000


def cells(ks):
    out = []
    for wl in os.environ.get('X7_WLS', 'ins,del,rep').split(','):
        for k in ks:
            if wl in ('ins', 'del', 'rep'):
                k = (3 if wl == 'rep' else 2) if k in (2, 3) else k
            out.append((wl, k))
    return out


def main():
    engine, out, tag, blocks = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    Cs = [int(x) for x in sys.argv[5].split(',')]
    methods = sys.argv[6].split(',')
    ks = [int(x) for x in sys.argv[7].split(',')] if len(sys.argv) > 7 else [2, 8, 32, 128]
    warm = float(sys.argv[8]) if len(sys.argv) > 8 else 2.0
    dur = float(sys.argv[9]) if len(sys.argv) > 9 else 8.0
    done = set()
    if os.path.exists(out):
        for line in open(out):
            r = json.loads(line)
            done.add((r['tag'], r['wl'], r['method'], r['k'], r['C'], r['rep']))
    # per-client rate of ordered commits at 8 rows by number of clients, from earlier runs
    rate8 = {int(c): v for c, v in json.load(open(os.path.join(HERE, 'preload_rates.json')))[engine].items()}
    py = sys.executable
    for blk in range(1, blocks + 1):
        rnd = random.Random(8000 + blk)
        cl = [(wl, k, C) for wl, k in cells(ks) for C in Cs]
        rnd.shuffle(cl)
        for wl, k, C in cl:
            ms = methods[:]
            rnd.shuffle(ms)
            for m in ms:
                if (tag, wl, m, k, C, blk) in done:
                    continue
                # 70% of the client's keys at most below 32 rows, where the outbox variants run many
                # operations and take their outbox keys from the same range; 90% at 32 rows and 95% at
                # 128, where they run few (the first 64-client runs at 32 rows, and those on Vitess at
                # 2 and 8 rows, used 90% throughout); 95% at 128 rows exceeds the keys of the Citus
                # worker with the fewest shards (10 of 32), so 90% there too
                frac = 0.7 if k < 32 else 0.9
                cap = int(frac * min(N_KEYS // C, KEYS_PER_CLIENT) / k)
                est = rate8.get(C, 200.0) * 8 / k * 3
                prepop = min(cap, int(2.5 * est * (warm + dur + 1.5)) + 100) if wl == 'del' else 0
                seed = zlib.crc32(f'x7s {tag} {engine} {wl} {m} {k} {C} {blk}'.encode()) % 100000
                for attempt in range(4):
                    base, _, route = m.partition('@')
                    env = dict(os.environ, X7_ROUTE='direct' if route == 'd' else 'coordinator')
                    p = subprocess.run([py, '-I', os.path.join(HERE, 'x7bench.py'), engine, wl, base, str(k), str(C), str(blk),
                                        str(seed), str(warm), str(dur), str(prepop)],
                                       capture_output=True, text=True, timeout=1800, env=env)
                    if p.returncode != 0 or not p.stdout.strip():
                        print(f'[fail] {tag} {wl} {m} k={k} C={C} blk={blk}: {p.stderr[-1500:]}', flush=True)
                        time.sleep(5)
                        continue
                    r = json.loads(p.stdout.strip().splitlines()[-1])
                    if r.get('p50_ms') is None or not r.get('tput'):
                        # no operation completed, as when vtgate stopped during the run: run it again
                        print(f'[fail] {tag} {wl} {m} k={k} C={C} blk={blk}: no operation completed', flush=True)
                        r = None
                        time.sleep(5)
                        continue
                    if r['exhausted']:
                        if prepop >= cap:
                            # a client that runs out of work stops early: the run is not recorded
                            print(f'[exhausted at the cap] {tag} {wl} {m} k={k} C={C} blk={blk}', flush=True)
                            r = None
                            break
                        prepop = min(cap, prepop * 2)
                        print(f'[exhausted] {tag} {wl} {m} k={k} C={C} blk={blk}; prepop {prepop}', flush=True)
                        continue
                    break
                else:
                    print(f'[give up] {tag} {wl} {m} k={k} C={C} blk={blk}', flush=True)
                    continue
                if r is None:
                    continue
                r.update(design='x7_scale', tag=tag, method=m)
                with open(out, 'a') as f:
                    f.write(json.dumps(r) + '\n')
                print(f'[{tag} blk {blk}] {engine} {wl} {m} k={k} C={C} p50={r["p50_ms"]:.2f} '
                      f'tput={r["tput"]:.0f} rounds={r["rounds_per_op"]} txns={r["txns_per_op"]} '
                      f'aborts={r["aborts"]} load={r["loadavg"]}', flush=True)
    print(f'X7_SCALE_DONE {tag} {engine} {time.strftime("%Y-%m-%dT%H:%M:%S")}', flush=True)


if __name__ == '__main__':
    main()
