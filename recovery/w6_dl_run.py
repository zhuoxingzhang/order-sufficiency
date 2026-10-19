"""Deadlock grid: blocks x skews x client counts, the methods of one cell back to back in a random
order (as x7_scale_run.py).  A skew is G:THETA (number of groups, Zipf exponent).  Runs already in OUT
are skipped; a failed run is retried up to three times.
usage: python -I w6_dl_run.py OUT BLOCKS CS METHODS SKEWS [warm dur M]
       e.g. results/dl.jsonl 3 8,32,64 Lnw,Lb2000,Lb100,h,hd 1000:0.99,10000:0.8
"""
import json, os, random, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    out, blocks = sys.argv[1], int(sys.argv[2])
    Cs = [int(x) for x in sys.argv[3].split(',')]
    methods = sys.argv[4].split(',')
    skews = [(int(s.split(':')[0]), float(s.split(':')[1])) for s in sys.argv[5].split(',')]
    warm = float(sys.argv[6]) if len(sys.argv) > 6 else 2.0
    dur = float(sys.argv[7]) if len(sys.argv) > 7 else 10.0
    M = int(sys.argv[8]) if len(sys.argv) > 8 else 200
    done = set()
    if os.path.exists(out):
        for line in open(out):
            r = json.loads(line)
            done.add((r['method'], r['C'], r['block'], r['G'], r['theta']))
    for blk in range(1, blocks + 1):
        rnd = random.Random(6500 + blk)
        cells = [(G, th, C) for G, th in skews for C in Cs]
        rnd.shuffle(cells)
        for G, th, C in cells:
            ms = methods[:]
            rnd.shuffle(ms)
            for m in ms:
                if (m, C, blk, G, th) in done:
                    continue
                for attempt in range(3):
                    p = subprocess.run([sys.executable, '-I', f'{HERE}/w6_deadlock.py', m, str(C), str(blk), str(warm),
                                        str(dur), str(G), str(th), str(M)],
                                       capture_output=True, text=True, timeout=warm + dur + 900)
                    if p.returncode != 0 or not p.stdout.strip():
                        print(f'[fail] {m} C={C} blk={blk} G={G} theta={th}: {p.stderr[-1500:]}', flush=True)
                        time.sleep(5)
                        continue
                    r = json.loads(p.stdout.strip().splitlines()[-1])
                    r['design'] = 'w6_deadlock_grid'
                    with open(out, 'a') as f:
                        f.write(json.dumps(r) + '\n')
                    mon = r['monitor'] or {}
                    ab = r['abort_rate']
                    print(f'[dl blk {blk}] {m} C={C} G={G} theta={th} tput={r["tput"]:.0f} p50={r["p50_ms"]} '
                          f'p99={r["p99_ms"]} abort={"-" if ab is None else round(ab, 3)} fails={r["fails"]} '
                          f'cross_cycles={mon.get("cross_worker_cycles")} ok={r["integrity_ok"]} load={r["loadavg"]}',
                          flush=True)
                    break
                else:
                    print(f'[give up] {m} C={C} blk={blk} G={G} theta={th}', flush=True)
    print(f'W6_DL_DONE {time.strftime("%Y-%m-%dT%H:%M:%S")}', flush=True)


if __name__ == '__main__':
    main()
