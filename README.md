# Artifact: order-sufficiency of modification updates on sharded SQL engines

This repository accompanies an anonymous submission.
It contains the code, the inputs and the results behind every figure and number of the experiments (Section 6) and the appendix checks.

| Paper | Directory | Produces the results | Numbers and figure from |
|---|---|---|---|
| Section 6.2, Figure 4 | [`corpus/`](corpus) | `sentry_classify.py`, `gitlab_classify.py` | `summarize.py` -> `out/templates.csv` |
| Section 6.3, Figure 5 | [`engines/`](engines) | `run_citus.sh`, `run_vt.sh` | `analyze.py` -> `results/summary.md` |
| Section 6.3, Figure 6 | [`cost/`](cost) | `run_cost.sh` | `fig_cost.py`, `gen_fig_cost.py` |
| Section 6.3, fault and fence runs | [`cost/`](cost) | `run_cost.sh` | `summarize_fault_fence.py` |
| Section 6.4 | [`deciders/`](deciders) | `run_all.sh`, steps 1-3 | `results/selfcheck_*`, `results/x3_*` |
| Section 6.5, Figure 7 | [`deciders/`](deciders) | `run_all.sh`, step 4 | `results/x4_*` |
| Appendix, beyond the exact classes | [`deciders/`](deciders) | `run_all.sh`, step 5 | `results/k_beyond*` |

Every directory holds the results of the paper, so each analysis script runs without a database:

```bash
cd corpus && python -I summarize.py
cd engines/results && python -I ../analyze.py citus.jsonl vitess.jsonl
cd cost && python -I fig_cost.py results && python -I summarize_fault_fence.py
```

## Setup

All experiments ran on one server with two 64-core AMD EPYC 7742 processors and 1 TB of memory under Linux.

- Python 3.11 or later; `pip install -r requirements.txt` (psycopg2 and pymysql for the engine runs, pycosat for the appendix checks).
  The scripts are run with `python -I`.
- Citus: PostgreSQL 17.11 with Citus 14.2.0 built from source, with `initdb`, `pg_ctl` and `psql` on `PATH`.
- Vitess: Vitess 24.0.5 (set `VTROOT` to the build) with MySQL 8.4.2 (set `VT_MYSQL_ROOT` to the installation), plus `etcd` and `curl` on `PATH`.
- The clusters run on one host on fixed local ports (Citus 29700-29703 and 29800-29803, Vitess the ports of the upstream examples shifted by +20000 and +30000).
  Their data go to `engines/work` and `cost/work`, or to `X2_WORK` and `X7_WORK` if set.

## Corpus (Section 6.2, Figure 4)

The corpus consists of the cross-database write templates of GitLab (commit 9d4cbde6, 2026-10-15) and Sentry (commit 13ccaecc, 2026-10-16).
Both code bases are read statically; no application code is run.

- `sentry_corpus.py` and `sentry_rpc.py` scan `src/sentry` of the Sentry checkout for silo models, foreign keys, hybrid-cloud foreign keys, outbox replication and remote procedure calls (`out/sentry_models.json`, `out/sentry_rpc.json`).
- `sentry_classify.py` holds the hand classification of the 65 Sentry templates (42 outbox replications, 23 synchronous remote writes) with their constraints and code locations, and prints the census of the Sentry schema used in Section 6.2.
- `gitlab_classify.py` holds the classification of the 32 GitLab templates: the 15 transactions that GitLab exempts from its guard against cross-database writes, and the deletions of the 17 parents whose loose foreign keys cross databases (from `deciders/data/gitlab_schema.json`).
- `summarize.py` prints Figure 4 and writes `out/templates.csv`, which lists for every template the rows it writes, the constraint between them, the label and the code locations.

```bash
cd corpus
python -I sentry_corpus.py <sentry>/src/sentry out/sentry_models.json
python -I sentry_rpc.py <sentry>/src/sentry out/sentry_rpc.json
python -I sentry_classify.py out/sentry_models.json out
python -I gitlab_classify.py ../deciders/data/gitlab_schema.json out
python -I summarize.py
```

A template needs only order if some write order keeps the constraints between its rows satisfied, atomic visibility if no order does, and serializability if a constraint relies on a read from another database; it is unconstrained if no constraint relates its rows.

## Engines (Section 6.3, Figure 5)

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

## Cost of write order (Section 6.3, Figure 6, fault and fence runs)

`run_cost.sh` runs every cost cell in three blocks; within each block the compared ways of writing run back to back in a random order.
`x7bench.py` is the benchmark client; its docstring defines every method.

| Paper | Code | Meaning |
|---|---|---|
| fastest two-phase commit | best of `h`, `hA`, `h@d`, `hA@d` (Citus), `h`, `hA` (Vitess) | one distributed transaction, one statement per row (`h`) or one statement per shard (`hA`), entering at the coordinator or at a worker (`@d`) |
| one commit per row | best of `b`, `b@d` (Citus), `b` (Vitess) | one single-shard transaction per row, committed in the required order |
| leveled commits | `Ls@d` (Citus), `Ls` (Vitess) | one transaction per node, executed in parallel and committed level by level |
| leveled commits with an outbox | `Los@d`, `Los` | the transactions of the first level also write an outbox row that lists the update |
| leveled commits with an eager first round | `Es@d`, `Es` | the first level commits in its first message |

- Templates: insert a parent and its children (`ins`), delete children and their parent (`del`), and move children to a new parent and delete the old one (`rep`), at 2 or 3, 8, 32 and 128 rows.
- Closed loops of 1 and 64 clients with 2 s of warm-up and 8 s of measurement; with 64 clients 0.5 s and 3 s at 32 rows (Citus) and 128 rows (Vitess), and 0.5 s and 2 s at 128 rows (Citus), since a client owns too few keys for a longer window.
  A third Citus configuration places relays (`x7_proxy.py`) that add 0.5 ms in each direction in front of every node.
- Durability is the engine default: fsync, synchronous commit and full-page writes on Citus, semi-synchronous replication on Vitess.
- `fig_cost.py` divides the median latency (one client) or the throughput (64 clients) of each way of writing by that of the fastest two-phase commit of the same block and cell, averages geometrically over the three templates, and reports a 95% t-interval over the blocks.
  `gen_fig_cost.py` writes Figure 6 as LaTeX.
- Every block of a cell that lacks a method is rerun with all methods (`x7_refill.py`).
  During our Vitess runs, vtgate was occasionally stopped by an external signal and restarted by a watchdog; the blocks that ran against a restarted vtgate were rerun the same way.
- Fault runs (`x7_fault.py`): 4 writers run the move template and 2 readers check for orphans for 12 s, while a relay in front of worker 29802 is armed every 0.5 s and then drops the next commit message to that worker.
  The run sets `citus.node_conninfo = sslmode=disable` on all nodes so that the relay sees the commit messages, and resets it afterwards.
- Fence runs (`x7_fence.py`): 4 clients add children to groups (5 ms think time) while 2 clients merge groups, over 16 active groups for 12 s, with and without a lock on the merged group.
- Before the runs, `x7_check_plan.py` checks the commit levels of every operation, `check_keymap.py` checks the key placement, and `x7_probe_2pc.py` checks that leveled commits and one commit per row commit without two-phase commit.

## Deciders and design (Sections 6.4 and 6.5, Figure 7, appendix)

`deciders.py` implements the tests of order-sufficiency: `dec_fd` (Theorem 3.6), `dec_unary` (Theorem 4.2), `dec_A` (Theorem 4.4) and `dec_K` (Theorem 4.7).
`m2.py` solves MIN-FREEZE (Theorem 5.1; "M2" in the code) by a minimum cut on bipartite conflict graphs and by a 2-approximation otherwise.
The shard-key condition of Theorem 5.3 is "B-pi" in the code.

```bash
cd deciders && PROCS=64 sh run_all.sh
```

- Step 1 compares the deciders with an exhaustive search over small updates (`selfcheck.py`, reference semantics in `ref_order2.py`) and builds witnesses for rejected schemas (`k_witness.py`).
- Steps 2 and 3 time the deciders single-threaded on random schemas with 10 attributes, 10 FDs and 2 INDs per relation (median of 5 seeds) and on the schema of GitLab (median of 20 runs), and compare the minimum cut with the 2-approximation.
- Step 4 compares the shard keys of Theorem 5.3 with containment rules and with the keys that GitLab declares, and the optimal freeze with freezing every copied source (Figure 7).
- Step 5 runs the checks of the appendix beyond the exact classes; it needs pycosat.
- `data/gitlab_schema.json` holds the tables, keys, foreign keys, sharding keys and loose foreign keys of GitLab commit 9d4cbde6; `gitlab_extract.py` rebuilds it from that commit (usage in `run_all.sh`).
- The large per-task result files are stored gzipped.

## License

The code is released under the MIT License (see `LICENSE`).
`deciders/data/gitlab_schema.json` contains schema facts extracted from GitLab, and `corpus/out` contains model names and code locations of Sentry; both remain subject to the licenses of these projects.
