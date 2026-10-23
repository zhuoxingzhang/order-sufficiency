# Cost of write order (Section 6.3, Figure 5, fault and fence runs)

The Citus and Vitess clusters are set up as in [`../engines/README.md`](../engines/README.md#setup), with the Citus ports 29800-29803 and the work directory `cost/work` (or `X7_WORK`).

| Paper | Produces the results | Numbers and figure from |
|---|---|---|
| Section 6.3, Figure 5 | `run_cost.sh` | `fig_cost.py`, `gen_fig_cost.py` |
| Section 6.3, Figure 5f | `run_depth.sh` | `depth_analyze.py` -> `results/depth.md` |
| Section 6.3, fault and fence runs | `run_cost.sh` | `summarize_fault_fence.py` |
| Section 6.3, ordered two-phase commit | `run_ordered2pc.sh` | `analyze_ordered2pc.py` -> `results/ordered2pc.md` |
| Section 6.3, entry through the coordinator, default mode of Vitess | `run_z1.sh` | `z1_ratios.py` |
| Section 6.3, every node on cores of its own, 0.5 ms on every path | `run_hardening.sh` | `z1_ratios.py` |
| Section 6.3, execution and commit phases | `run_hardening.sh` (`PARTS=split`) | `split_phases.py` |

The stored results reproduce the numbers of the paper without a database:

```bash
python -I fig_cost.py results && python -I summarize_fault_fence.py
python -I depth_analyze.py results/depth_citus.jsonl results/depth_rtt_citus.jsonl results/depth_vitess.jsonl
python -I z1_ratios.py results && python -I z1_ratios.py results 64 && python -I z1_ratios.py results rtt
python -I z1_ratios.py results hardening
python -I split_phases.py results/split_citus.jsonl
```

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
  `gen_fig_cost.py` writes Figure 5 as LaTeX.
- Every block of a cell that lacks a method is rerun with all methods (`x7_refill.py`).
  During our Vitess runs, vtgate was occasionally stopped by an external signal and restarted by a watchdog; the blocks that ran against a restarted vtgate were rerun the same way.
- Fault runs (`x7_fault.py`): 4 writers run the move template and 2 readers check for orphans for 12 s, while a relay in front of worker 29802 is armed every 0.5 s and then drops the next commit message to that worker.
  The run sets `citus.node_conninfo = sslmode=disable` on all nodes so that the relay sees the commit messages, and resets it afterwards.
- Fence runs (`x7_fence.py`): 4 clients add children to groups (5 ms think time) while 2 clients merge groups, over 16 active groups for 12 s, with and without a lock on the merged group.
- Before the runs, `x7_check_plan.py` checks the commit levels of every operation, `check_keymap.py` checks the key placement, and `x7_probe_2pc.py` checks that leveled commits and one commit per row commit without two-phase commit.

## Number of rounds (Section 6.3, Figure 5f)

`run_depth.sh` runs the `chain` workload of `x7bench.py`: an insertion of a path of k = 2, 3, 4, 5, 6 or 8 rows, row i referencing row i - 1, with consecutive rows on different nodes.
Every hop changes nodes, so leveled commits need k rounds, whereas one two-phase commit keeps its prepare and commit rounds; a node with several transactions of one update gets one connection per transaction.
Three blocks, one client, 2 s of warm-up and 8 s of measurement, on Citus without and with the relays above and on Vitess, with the methods of Figure 5 except the outbox; Figure 5f shows Citus without relays, and `results/depth.md` holds all three.
`depth_analyze.py` divides the median latency of each method by that of the fastest two-phase commit of the same block and reports the geometric mean over the blocks with its range (`results/depth.md`).

## Ordered two-phase commit (Section 6.3)

`run_ordered2pc.sh` compares an ordered two-phase commit with leveled commits on Citus, with the templates, sizes, blocks and windows of Figure 5 and the one 2PC baselines rerun in the same blocks.

- `Ps@d` prepares every single-node transaction of leveled commits at the end of the message that executes it, so that all of them are prepared before any commits, and then commits the prepared transactions level by level with `COMMIT PREPARED`.
- Citus refuses `PREPARE TRANSACTION` after a statement on a distributed table name, so `Ps@d` writes the shard tables by name (`probe_prepare.sh` checks this).
  `Lt@d` is the same on the shard tables without `PREPARE`, so the paired ratio `Ps@d / Lt@d` isolates the cost of the prepares; `Ls@d` are the leveled commits of Figure 5.
- `analyze_ordered2pc.py` reports every method relative to the fastest one 2PC of the same block and the paired ratios (`results/ordered2pc.md`).
  With one client, the prepares raise the median latency by 6% to 26%; with 64 clients they raise it by 34% to 102% and cut the throughput by a fifth to a half.
- The stored run was stopped from outside during its last part (one client behind 1 ms proxies, 53 of 252 runs), and `Lt@d` ran out of keys in a few cells (deletions at 128 rows, and insertions and deletions at 32 rows with 64 clients); the analysis lists the missing pairs.

## Entry through the coordinator and the default mode of Vitess (Section 6.3)

`run_z1.sh` reruns, with 1 and 64 clients, three blocks and the templates, sizes and windows of Figure 5, leveled commits against the fastest two-phase commit in two further settings.

- On Citus, `Ls` enters at the coordinator instead of the workers (`Ls@d`).
- On Vitess, `ms` is one transaction in the default mode of Vitess (`transaction_mode = 'multi'`), which commits the shards one after the other in the order in which the transaction touched them and gives up atomicity; its statements are those of `Ls` in the order of the levels, sent as one message.
- `z1_ratios.py` computes the ratios as `fig_cost.py` does.
  Through the coordinator, `Ls` takes 0.95, 0.87, 0.76 and 0.47 of the time of the fastest two-phase commit at 2 or 3, 8, 32 and 128 rows; on Vitess, `ms` takes 0.44, 0.49, 0.53 and 0.62, and `Ls` 0.40, 0.42, 0.44 and 0.49.
- With 64 clients (`z1_ratios.py results 64`) and one client behind the 0.5 ms relays of Figure 5 (`z1_ratios.py results rtt`), the results are in `results/z1_64_*.jsonl` and `results/z1_rtt_citus.jsonl`.
  These runs share cores between the servers and the clients.
  The paper takes its statements about 64 clients from the runs below, in which every node has cores of its own: there the coordinator becomes the bottleneck of `Ls` (0.61 of the throughput of the fastest two-phase commit at 8 rows), which the unpinned runs hide (1.12).

## Every node on cores of its own, 0.5 ms on every path (Section 6.3)

`run_hardening.sh` repeats the comparison of `run_z1.sh` at 8 and 128 rows with 1 and 64 clients, three blocks, in two configurations.

- `pin`: every Citus node, every Vitess tablet (its mysqld and vttablet), vtgate, the control plane and the clients run on cores of their own (`taskset`), the servers on one socket and the clients on the other.
  The core sets fit the machine of the paper; environment variables override them (see the script).
- `rtt`: as `pin`, with relays (`relay.py`) that add 0.5 ms in each direction on every path between nodes.
  On Citus: client to coordinator, coordinator to workers, client to workers.
  On Vitess: client to vtgate, vtgate to the tablets (the tablets advertise the host name 127.0.0.2, where the relays listen, via `TABLET_HOST` in `vitess_up.sh`) and replica to primary (every mysqld bound to 127.0.0.1).
  The relays stand in for `netem`, which needs root.
- `z1_ratios.py results hardening` reports both configurations.
- `PARTS=split` (not in the default) runs Citus as `pin` with one client at 8 and 128 rows and `X7_SPLIT=1`, under which `x7bench.py` sends the `COMMIT` of a one-message two-phase commit separately and records the execution phase (`data_p50_ms`) and the commit phase (`commit_p50_ms`) of every two-phase commit and leveled commit (`results/split_citus.jsonl`).
  `split_phases.py` reports both phases per method and the share of the gap to the fastest two-phase commit that lies in the execution phase: 78% at 8 rows and 95% at 128 rows for leveled commits sent to the workers, 82% and 95% through the coordinator.
  Behind the relays, direct-routed leveled commits take 0.43 and 0.19 of the time of the fastest two-phase commit on Citus at 8 and 128 rows, and reach 2.11 and 5.15 times its throughput with 64 clients; on Vitess, 0.59 and 0.60 of the time and 2.25 and 2.00 times the throughput.
