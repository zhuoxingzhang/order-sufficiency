# Engines (Section 6.3, Figures 5 and 6)

| Paper | Produces the results | Numbers and figure from |
|---|---|---|
| Section 6.3, Figure 5 | `run_citus.sh`, `run_vt.sh` | `analyze.py` -> `results/summary.md` |
| Section 6.3, Vitess on four shards | `run_vt4.sh` | `analyze.py` -> `results/vitess4.md` |
| Section 6.3, injected commit delay from 0 to 10 ms | `run_delay.sh` | `analyze_delay.py` -> `results/citus_delay.md` |
| Section 6.3, Figure 6 | `run_direction.sh`, `run_direction_fill.sh` | `analyze_direction.py` -> `results/direction.md`, `results/direction_gap.md`, `results/direction_fill.md` |
| Section 6.3, Figure 6, Proposition 5.8 | `run_grace.sh` | `analyze_grace.py` -> `results/grace.md` |

The stored results reproduce the numbers of the paper without a database:

```bash
cd results && python -I ../analyze.py citus.jsonl vitess.jsonl
cd results && python -I ../analyze.py --json=vitess4.json vitess4.jsonl
python -I analyze_delay.py results/citus_delay.jsonl
python -I analyze_direction.py results/direction.jsonl && python -I analyze_direction.py results/direction_gap.jsonl
python -I analyze_direction.py results/direction_fill.jsonl
python -I analyze_grace.py results/grace.jsonl
```

## Setup

- Citus: PostgreSQL 17.11 with Citus 14.2.0 built from source, with `initdb`, `pg_ctl` and `psql` on `PATH`.
- Vitess: Vitess 24.0.5 (set `VTROOT` to the build) with MySQL 8.4.2 (set `VT_MYSQL_ROOT` to the installation), plus `etcd` and `curl` on `PATH`.
- The clusters run on one host on fixed local ports (Citus 29700-29703 and 29800-29803, Vitess the ports of the upstream examples shifted by +20000 and +30000).
  Their data go to `engines/work` and `cost/work`, or to `X2_WORK` and `X7_WORK` if set.

## Runs

The engine runs test whether one distributed transaction exposes orphans and inconsistent copies to readers.

- Citus (`run_citus.sh`): a coordinator and three workers; the foreign-key cells run with worker 29701 slowed down (fsync and synchronous commit on, `commit_delay` 10 ms), the copy cell with uniform workers.
  Cells: `e3a` and `e3x_pf` one 2PC writing the parent first, `e3x_cf` one 2PC writing the child first, `e3o` two ordered commits, `e4` a source and its copy updated in one 2PC.
  Initialise the cluster once with `bash citus_cluster.sh init`.
- Vitess (`run_vt.sh`): 2 shards with 3 tablets each, semi-synchronous durability, vtgate in TWOPC mode.
  Cells: `v2` one TwoPC writing the parent first and two ordered commits, `v2swap` one TwoPC writing the child first, `v1` a source and its copy updated in one TwoPC.
- A reader reads the child and then, in the next statement, the parent it points to; an absent parent is an orphan.
  Copy readers read the source and the copy in one statement (Citus) or one scatter query (Vitess).
- The stored results hold 10 batches per Citus cell and 8 per Vitess cell.
  `analyze.py` reports the pooled rate per cell; the interval in Figure 5 spans the pooled Wilson interval and a batch bootstrap interval, and a cell without events reports the Wilson upper bound.
- Four shards (`run_vt4.sh`): the Vitess cells on a cluster with four shards instead of two (`vitess4_up.sh`, the same ports, `VT_SHARDS=4` for `vt_exp.py`), 10 batches.
  One TwoPC exposed an orphan in 88% of the runs that wrote the child first and in none of those that wrote the parent first, and two ordered commits in none (`results/vitess4.md`).
  The fixed MySQL ports of the tablets lie in the ephemeral port range of Linux, so an outgoing connection can hold one at start-up and leave a shard without a primary; `run_vt4.sh` then restarts the cluster, up to three times.
- Commit delay (`run_delay.sh`): the Citus foreign-key cells with the `commit_delay` of worker 29701 swept over 0, 1, 2, 5 and 10 ms, three batches each.
  One 2PC exposed orphans in 34% of the runs from 1 ms on, all with the parent on the delayed worker, and in 0.1% without a delay; two ordered commits exposed none (`results/citus_delay.md`).

## Read direction (Section 6.3, Figure 6)

`run_direction.sh` runs `direction.py` on the Citus cluster above in its uniform configuration (no slow worker), 2,000 runs per configuration.

- A parent row and a child row that references it lie on different workers (64 key pairs).
  The writer inserts both rows or deletes both: `ordered` (two autocommits in the required order: parent first for insertions, child first for deletions), `reversed` (the opposite order), `atomic` (one transaction over both workers, statements in the required order), or `ordered_g1` and `ordered_g10` (ordered, with 1 or 10 ms between the two commits).
- The reader starts 0-2 ms before the writer and polls with two single-row reads per iteration, pausing 0, 1 or 10 ms between them: `c2p` reads the child and then the parent, `p2c` the parent and then the child.
  It stops at the final state (both rows present after an insertion, both absent after a deletion) or after 200 ms.
- A run counts as anomalous if some iteration sees the child but not the parent.
  `analyze_direction.py` reports the share of anomalous runs with a Wilson 95% interval (`results/direction.md`, `results/direction_gap.md`).
  Figure 6 takes the line of one two-phase commit (`atomic`, `c2p`) from the deletion runs of the first grid at 0, 1 and 10 ms and from `run_direction_fill.sh` at 2, 5 and 20 ms, the pauses of the grace grid; the protected reader (`ordered`, `p2c`) saw no orphan in its 6,000 deletion runs.
  The fill also reran 0, 1 and 10 ms: 48.8%, 86.0% and 100%, against 47.8%, 86.1% and 100% in the first grid (`results/direction_fill.md`).

## Grace period (Section 6.3, Figure 6, Proposition 5.8)

`run_grace.sh` runs `grace.py` on the same cluster and configuration, 2,000 runs per configuration.

- The writer issues the two ordered autocommits of `direction.py` and sleeps a pause of 0, 1, 2, 5, 10 or 20 ms between the return of the first and the start of the second.
- The reader reads in the direction that write order does not protect (`c2p` for deletions, `p2c` for insertions) and pauses 0, 1, 2, 5, 10 or 20 ms between its two reads.
  Every iteration records its span, from the start of the first read to the end of the second, which bounds the distance between the two snapshots from above.
- `analyze_grace.py` reports the anomalous runs per cell with a Wilson 95% interval and, as the check of Proposition 5.8, the number of anomalous iterations whose span is at most the pause between the commits (`results/grace.md`).
  It is 0 among 700,952 such iterations; Figure 6 shows the deletion cells.
