# Recovery and deadlocks (Section 6.3, Appendix B, Proposition A.13, Lemma A.14)

| Paper | Produces the results | Numbers and figure from |
|---|---|---|
| Section 6.3 and Appendix B, recovery (Proposition A.13) and deadlocks (Lemma A.14) | `run_recovery.sh` | `w6_analyze.py` -> `results/w6_summary.md` |

The stored results reproduce the numbers of the paper without a database:

```bash
python -I w6_mock.py && python -I w6_analyze.py results
```

`run_recovery.sh` runs on the Citus cluster of `cost/` (its own tables, colocated with those of `cost/`, dropped at the end), every transaction sent to the worker that holds its rows.

- Recovery (`w6_recovery.py`): an update merges a group into a new one (the reparent template, Ex. 5.6) in three rounds on three workers, and stops after its first or second round.
  A conflicting update then runs, either to completion before the relay (`staged`) or overlapping it (`race`), and a relay completes the stopped update from its outbox row.
  `replay` re-applies the new tuples of the remaining rounds, as `x7_fault.py` does; `validate` is the relay of Proposition A.13, which locks and reads the changed tuples and aborts unless they match a prefix state of the update.
  Readers check for orphans throughout, and an audit after every trial checks the final state for orphans and lost updates.
- Deadlocks (`w6_deadlock.py`, `w6_dl_run.py`): 8 to 64 clients move children between Zipf-hot groups, with writes that fail and retry (`Lnw`, NOWAIT), writes that wait with a lock timeout of 100 ms or 2 s (`Lb100`, `Lb2000`), writes that lock in one global order (`Lord`), and one two-phase commit through the coordinator or a worker (`h`, `hd`).
  A monitor samples the lock waits on the workers every 25 ms and reports the wait-for cycles whose edges span two workers, which PostgreSQL cannot detect.
- `w6_mock.py` checks the logic of both without a database; the smoke part of `run_recovery.sh` checks that the staged cells reach the predicted outcomes.
- `w6_analyze.py` writes `results/w6_summary.md`.
  In the 3,000 race trials with a conflicting update, `replay` left 653 orphans and lost 381 updates, `validate` left no orphan and lost no update.
  With 64 clients over 1,000 groups (Zipf 0.99), `Lnw` sustained 6,440 updates per second without a cycle, `Lb100` closed 303 cycles across workers, and two-phase commit reached at most 35 updates per second.
