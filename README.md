# Artifact: order-sufficiency of modification updates on sharded SQL engines

This repository accompanies a paper on when write order, rather than atomic visibility, keeps the constraints of a schema satisfied on sharded SQL engines.
It contains the code, the inputs and the results behind the figures and numbers of the experiments (Section 6) and the checks of the appendix.

| Paper | Directory | Produces the results | Numbers and figure from |
|---|---|---|---|
| Section 6.2, Figure 4a | [`corpus/`](corpus) | `sentry_classify.py`, `gitlab_classify.py` | `summarize.py` -> `out/templates.csv` |
| Section 6.2, readers of the templates that need atomic visibility | [`corpus/`](corpus) | reading of the code | `atomic_audit.md` |
| Sections 6.1 and 6.2, static analyzer, Figure 4b | [`apps/`](apps) | `run_all.sh` | `validate.py`, `fig_apps.py` |
| Section 6.2, blind hand labels of 135 templates | [`apps/`](apps) | `sample_blind.py` | `blind/estimate.py` |
| Section 6.2, read-path audit (Sentry) | [`corpus/`](corpus) | `readpath_probe.py`, `readpath_tolerance.py` | `readpath_audit.md`, `out/readpath_sites.json` |
| Sections 6.1 and 6.2, second annotator | [`corpus/annotator/`](corpus/annotator) | `templates.csv`, `sites.csv` | `agreement.py` |
| Section 6.2, read-path audit (GitLab) | [`corpus/`](corpus) | `gitlab_readpath_probe.py`, `gitlab_readpath_sample.py` | `gitlab_readpath_audit.md`, `gitlab_readpath_review.json` |
| Section 6.3, Figure 6 | [`engines/`](engines) | `run_citus.sh`, `run_vt.sh` | `analyze.py` -> `results/summary.md` |
| Section 6.3, Vitess on four shards | [`engines/`](engines) | `run_vt4.sh` | `analyze.py` -> `results/vitess4.md` |
| Section 6.3, injected commit delay from 0 to 10 ms | [`engines/`](engines) | `run_delay.sh` | `analyze_delay.py` -> `results/citus_delay.md` |
| Section 6.3, Figure 5 | [`cost/`](cost) | `run_cost.sh` | `fig_cost.py`, `gen_fig_cost.py` |
| Section 6.3, Figure 5f | [`cost/`](cost) | `run_depth.sh` | `depth_analyze.py` -> `results/depth.md` |
| Section 6.3, fault and fence runs | [`cost/`](cost) | `run_cost.sh` | `summarize_fault_fence.py` |
| Section 6.3, ordered two-phase commit | [`cost/`](cost) | `run_ordered2pc.sh` | `analyze_ordered2pc.py` -> `results/ordered2pc.md` |
| Section 6.3, entry through the coordinator, default mode of Vitess | [`cost/`](cost) | `run_z1.sh` | `z1_ratios.py` |
| Section 6.3, every node on cores of its own, 0.5 ms on every path | [`cost/`](cost) | `run_hardening.sh` | `z1_ratios.py` |
| Section 6.3, execution and commit phases | [`cost/`](cost) | `run_hardening.sh` (`PARTS=split`) | `split_phases.py` |
| Section 6.3 and Appendix B, recovery (Proposition A.13) and deadlocks (Lemma A.14) | [`recovery/`](recovery) | `run_recovery.sh` | `w6_analyze.py` -> `results/w6_summary.md` |
| Section 6.3, Figure 7 | [`engines/`](engines) | `run_direction.sh`, `run_direction_fill.sh` | `analyze_direction.py` -> `results/direction.md`, `results/direction_gap.md`, `results/direction_fill.md` |
| Section 6.3, Figure 7, Proposition 5.8 | [`engines/`](engines) | `run_grace.sh` | `analyze_grace.py` -> `results/grace.md` |
| Section 6.4, decision time | [`deciders/`](deciders) | `run_all.sh`, steps 1-3 | `results/selfcheck_*`, `results/x3_*` |
| Section 6.4, Figure 8 and Appendix B, shard keys, unique keys and freezing on GitLab | [`deciders/`](deciders) | `run_all.sh`, step 4 | `x4_shardk.py`, `x4_unique.py`, `x4_freq.py`, `results/x4_*` |
| Appendix A.3, Proposition 4.7 | [`deciders/`](deciders) | `run_all.sh`, step 6 | `results/fresh_k*` |
| Appendix A.3, Theorem 4.9 | [`deciders/`](deciders) | `run_all.sh`, step 9 | `cind_analyze.py` |
| Appendix A.3, Proposition A.8 | [`deciders/`](deciders) | `run_all.sh`, step 7 | `uk_tally.py` |
| Appendix A.3, Proposition A.8 with overlapping pointers | [`deciders/`](deciders) | `uk_overlap.py`, `uk_overlap_big.py` | `uk_overlap_tally.py` |
| Appendix A.3, beyond the exact classes | [`deciders/`](deciders) | `run_all.sh`, step 5 | `results/k_beyond*` |
| Appendix A.4, Theorem 5.3 | [`deciders/`](deciders) | `run_all.sh`, step 8 | `kshard_tally.py` |

Every directory holds the results of the paper, so each analysis script runs without a database:

```bash
cd corpus && python -I summarize.py
cd corpus/annotator && python -I agreement.py
cd apps && python -I validate.py out && python -I fig_apps.py out fig_apps.tex
cd apps/blind && python -I estimate.py ../out sample1:a,b,c sample2:u,v,w,x,y,z
cd engines/results && python -I ../analyze.py citus.jsonl vitess.jsonl
cd engines/results && python -I ../analyze.py --json=vitess4.json vitess4.jsonl
cd engines && python -I analyze_delay.py results/citus_delay.jsonl
cd engines && python -I analyze_direction.py results/direction.jsonl && python -I analyze_direction.py results/direction_gap.jsonl
cd engines && python -I analyze_direction.py results/direction_fill.jsonl
cd engines && python -I analyze_grace.py results/grace.jsonl
cd cost && python -I fig_cost.py results && python -I summarize_fault_fence.py
cd cost && python -I depth_analyze.py results/depth_citus.jsonl results/depth_rtt_citus.jsonl results/depth_vitess.jsonl
cd cost && python -I z1_ratios.py results && python -I z1_ratios.py results 64 && python -I z1_ratios.py results rtt
cd cost && python -I z1_ratios.py results hardening
cd cost && python -I split_phases.py results/split_citus.jsonl
cd recovery && python -I w6_mock.py && python -I w6_analyze.py results
cd deciders && python -I x4_shardk.py x4_shardk.json && python -I x4_unique.py x4_unique.json && python -I x4_freq.py x4_freq.json
cd deciders && python -I uk_tally.py results/uk_n5d5.jsonl results/uk_big_n7.jsonl
cd deciders && python -I uk_overlap_tally.py results/uk_overlap_n5d5.jsonl results/uk_overlap_big_n8.jsonl
cd deciders && python -I kshard_tally.py results/kshard_n4d4.jsonl,results/kshard_n5d5.jsonl results/kshard_recheck_n4d4.jsonl,results/kshard_recheck_n5d5.jsonl,results/kshard_recheck2_n5d5.jsonl results/kshard_recheck_n4d4_fresh.jsonl
cd deciders && python -I cind_analyze.py results/cind_random_n4.jsonl results/cind_random_n5.jsonl results/cind_recheck_random_n5.jsonl
cd deciders && python -I cind_analyze.py results/cind_named_n5.jsonl results/cind_recheck_named_n6.jsonl
```

## Setup

All experiments ran on one server with two 64-core AMD EPYC 7742 processors and 1 TB of memory under Linux.

- Python 3.11 or later; `pip install -r requirements.txt` (psycopg2 and pymysql for the engine runs, pycosat for the appendix checks).
  The scripts are run with `python -I`.
- Citus: PostgreSQL 17.11 with Citus 14.2.0 built from source, with `initdb`, `pg_ctl` and `psql` on `PATH`.
- Vitess: Vitess 24.0.5 (set `VTROOT` to the build) with MySQL 8.4.2 (set `VT_MYSQL_ROOT` to the installation), plus `etcd` and `curl` on `PATH`.
- The static analyzer (`apps/`) needs only the Python standard library and checkouts of the analyzed applications at the commits listed in `apps/run_all.sh`; it reads their code and runs none of it.
- The clusters run on one host on fixed local ports (Citus 29700-29703 and 29800-29803, Vitess the ports of the upstream examples shifted by +20000 and +30000).
  Their data go to `engines/work` and `cost/work`, or to `X2_WORK` and `X7_WORK` if set.

## Corpus (Section 6.2, Figure 4a)

The corpus consists of the cross-database write templates of GitLab (commit 9d4cbde6, 2026-10-15) and Sentry (commit 13ccaecc, 2026-10-16).
Both code bases are read statically; no application code is run.

- `sentry_corpus.py` and `sentry_rpc.py` scan `src/sentry` of the Sentry checkout for silo models, foreign keys, hybrid-cloud foreign keys, outbox replication and remote procedure calls (`out/sentry_models.json`, `out/sentry_rpc.json`).
- `sentry_classify.py` holds the hand classification of the 65 Sentry templates (42 outbox replications, 23 synchronous remote writes) with their constraints and code locations, and prints the census of the Sentry schema used in Section 6.2.
- `gitlab_classify.py` holds the classification of the 32 GitLab templates: the 15 transactions that GitLab exempts from its guard against cross-database writes, and the deletions of the 17 parents whose loose foreign keys cross databases (from `deciders/data/gitlab_schema.json`).
- `summarize.py` prints Figure 4a and writes `out/templates.csv`, which lists for every template the rows it writes, the constraint between them, the label and the code locations.

```bash
cd corpus
python -I sentry_corpus.py <sentry>/src/sentry out/sentry_models.json
python -I sentry_rpc.py <sentry>/src/sentry out/sentry_rpc.json
python -I sentry_classify.py out/sentry_models.json out
python -I gitlab_classify.py ../deciders/data/gitlab_schema.json out
python -I summarize.py
```

A template needs only order if some write order keeps the constraints between its rows satisfied, atomic visibility if no order does, and serializability if a constraint relies on a read from another database; it is unconstrained if no constraint relates its rows.

### Read paths of the order-only templates (Section 6.2)

Write order protects a reader only if it reads first what the update writes last.
The audit asks, for each of the 48 Sentry templates that need only order, whether some reader reads the two sides in the other direction, and what that reader does when it meets a child whose parent is gone.

- `readpath_probe.py` lists, for every hybrid-cloud foreign key of the deletion templates, the calls of the parent's RPC service whose arguments use the child's foreign-key column within four lines, in non-test files that name the child model (`out/readpath_probe.json`).
- `readpath_tolerance.py` labels how each site handles a missing parent: tolerant (T), batch fetch (B), failure (E), unguarded (U).
  The hand verdicts on all E and U sites, on the sites that are no cross-silo child-to-parent read (FP), and on every other site whose first label did not hold are in `readpath_override.json`; sites found by hand for edges without hits are in `readpath_extra.json`.
  The result is `out/readpath_sites.json`.
- `readpath_audit.md` gives the verdict for every template with its evidence (file and line in `src/sentry`): 16 templates have a reader that reads against the write order (11 deletions, 4 modifications and the insertion D10, whose reader fails closed), and 32 have none.
  Of the 88 sites that read from a child to its parent across silos, 80 tolerate a missing parent and 8 fail.

```bash
cd corpus
python -I readpath_probe.py <sentry>/src/sentry out/sentry_models.json out/readpath_probe.json
python -I readpath_tolerance.py <sentry>/src/sentry out/readpath_probe.json readpath_extra.json out/readpath_sites.json readpath_override.json
```

### Read paths of the GitLab cascading deletions (Section 6.2)

The same question for the 17 cascading deletions of GitLab (C01-C17 of `gitlab_classify.py`): a reader that loads a child and then the parent it references reads against the required order, which removes the children first.

- `gitlab_readpath_probe.py` takes the 249 loose foreign keys of these parents that cross databases (from `../deciders/data/gitlab_schema.json`), finds the `belongs_to` association of each child model for the key, and lists the places in `app`, `ee/app`, `lib` and `ee/lib` that call it, with a first label: tolerant (T), failure (E), unguarded (U) (`out/gitlab_readpath_probe.json`, file and line only).
- `gitlab_readpath_sample.py` draws 6 sites per template (seed 9; 56 sites, `out/gitlab_readpath_sample.json`) and prints them with their context; `gitlab_readpath_review.json` holds the verdict by hand on each.
- `gitlab_readpath_audit.md` gives the verdict for each of the 28 order-only templates with its evidence (file and line at commit 9d4cbde6): 13 have a reader that reads against the required order (11 of the 17 cascading deletions and the exemptions G01 and G11), and for 15 none was found.
  Of the 32 sampled sites that read from a child to its parent across databases, 24 tolerate a missing parent and 8 raise an error.

```bash
cd corpus
python -I gitlab_readpath_probe.py <gitlab> ../deciders/data/gitlab_schema.json out/gitlab_readpath_probe.json
python -I gitlab_readpath_sample.py <gitlab> out/gitlab_readpath_probe.json 6 9 sample.txt out/gitlab_readpath_sample.json
```

### Readers of the templates that need atomic visibility (Section 6.2)

`atomic_audit.md` asks, for each of the 16 templates that need atomic visibility (G12, G13 and G14 of GitLab and 13 of Sentry), whether some reader relies on the copy agreeing with its source.
It names, for each template, the reader with the most severe consequence and further readers by file and line (for GitLab file and line only), and what the reader decides.
Each of the 16 templates has such a reader, and for 13 of them the reader decides authentication or authorization.

### Second annotator (Sections 6.1 and 6.2)

A second annotator, who saw neither our labels nor the paper, labelled the 97 templates and the 181 read-path sites of both audits (Sentry and the GitLab sample) independently; `annotator/instructions.md` gives the instructions.
`annotator/templates.csv` and `annotator/sites.csv` hold the returned labels, one per item, with the template or the code location.
`agreement.py` compares them with our labels in `out/templates.csv`, `out/readpath_sites.json` (a batch fetch counts as tolerant) and `gitlab_readpath_review.json`.

- Templates: 88% agreement (Cohen's kappa 0.71), and 94 of the 97 agree on whether order suffices.
  The annotator labelled 8 templates `serial`, citing values read from another database that a concurrent update may change; Section 5.3 handles these conflicts with a lock.
- Read-path sites: 93% agreement (kappa 0.88) over the 181 sites, 97% (kappa 0.94) over the 125 sites of Sentry.

```bash
cd corpus/annotator
python -I agreement.py
```

## Static analyzer (Sections 6.1 and 6.2, Figure 4b)

The analyzer in `apps/analyzer` reads the code of Rails and Django applications statically.

- It collects the references (declared foreign keys and the associations that the code relies on), the copies that callbacks keep equal to a column of the referenced row, and the derived values such as counter caches (`rails_*.py`, `django_front.py`, `schema.py`).
- It groups the writes into templates: transaction blocks, methods, and the creation, modification or deletion of a row of a model with its callbacks, cascades, counter caches and queued jobs.
- `classify.py` labels every template that writes several tables, assuming that every table may lie on a shard of its own: it needs only order if it inserts or deletes rows or moves pointers along references (Proposition 5.4), and atomic visibility if it changes a source and its copy (Corollary 4.8).
  Under the strict reading, a derived value must equal its definition in every state, so changing it together with its rows needs atomic visibility; under the one-way reading, readers accept a derived value that lags behind its rows, so order suffices.
- `run_gitlab.py` and `run_sentry.py` add the partitions of GitLab (databases and gitlab_schemas, loose foreign keys, the exemptions from its guard) and the silos of Sentry (hybrid-cloud foreign keys, remote calls, outboxes), and count only constraints across partitions or silos.

`run_all.sh` runs the analyzer over the ten applications, GitLab and Sentry; the outputs of the paper are in `out/` (gzipped).
`validate.py` compares the analyzer with the 97 hand labels of `corpus/`: it finds 72 of the templates and agrees with 54 labels (57 under the one-way reading), and under the one-way reading 11 of its 12 verdicts of atomic visibility are right and it reports 11 of the 15 found templates that need atomic visibility.
On the ten applications it prints the verdicts of their 6,033 templates that write several tables outside test and development code: 5,049 of them are constrained, and 99% of these need only order under the one-way reading and 78% under the strict one.
`fig_apps.py` writes Figure 4b.

```bash
cd apps
SRC=<directory of the checkouts> sh run_all.sh
python -I validate.py out
python -I fig_apps.py out fig_apps.tex
```

### Blind hand labels (Section 6.2)

The share of order among the constrained templates is an upper bound, since the analyzer recognizes only the copies that it can parse.
Templates sampled from the ten applications were therefore labelled by hand, blind to the verdicts of the analyzer.

- `sample_blind.py` draws, per application and strict verdict of the analyzer, a fixed number of templates with a fixed seed.
  The sheet shows the repository, the commit, the kind of template and a link to its code; the key (`key_*.json`) holds the verdicts.
- Sample 1 (`blind/sample1`) draws 2 templates per application and verdict (seed 20261019) from the output of an earlier version of the analyzer, which handled copies along reversed references differently.
  On the stored output, parts a and b are drawn again identically, and part c differs in 3 templates of Canvas; `key_c.json` holds the sample as labelled.
  Of its 56 templates, the 5 in test or development code (A06, A16, B02, C11, C12) were left out before labelling, so 51 carry labels.
- Sample 2 (`blind/sample2`) draws 3 templates per application and verdict (seed 20261020) outside test and development code and outside sample 1: 84 templates.
- The labelling instructions set the premise that every table may lie on its own shard, that shards commit separately and that there is no global snapshot.
  They define the scope of the three kinds of template (including callbacks, cascades, counter caches and queued jobs), the three constraints to consider (references, copies of a column of the referenced row, and derived values such as counts, sums and flags aggregated from other rows), and the labels: `none` if no such constraint relates rows of different tables, `order` if some commit order keeps every constraint satisfied after every commit, and `atomic` if no order does.
  The column `derived` is `yes` if the label is `atomic` only because of a derived value.
  Four practice examples outside the sample illustrate the labels: creating a group and its first project (order), renaming a group whose path the projects cache (atomic, not derived), creating a comment counted in a counter of its post (atomic, derived), and writing an audit log next to an unrelated row (none).
  The instructions ask for independent work, without the paper or our labels.
- `labels_*.csv` hold the labels (columns `item`, `app`, `kind`, `origin`, `label`, `derived`).
- `estimate.py` weighs the labelled templates of each stratum (application and strict verdict of the analyzer on the stored output) by the size of the stratum, reads the labels one way (atomic and derived counts as order), and resamples within the strata (2,000 rounds) for a 95% interval.
  Both samples pooled (135 templates) put the share of order among the constrained templates at 81.9% (73.8-89.6%).

```bash
cd apps
python -I sample_blind.py out sheet_a.csv key_a.json zulip,makeplane,saleor,openedx A
python -I sample_blind.py out sheet_b.csv key_b.json PostHog,chatwoot,mastodon B
python -I sample_blind.py out sheet_c.csv key_c.json discourse,spree,instructure C
X=--exclude=blind/sample1/key_a.json,blind/sample1/key_b.json,blind/sample1/key_c.json
python -I sample_blind.py out sheet_u.csv key_u.json zulip,makeplane U --seed=20261020 --per=3 $X --skip-testdev
python -I sample_blind.py out sheet_v.csv key_v.json saleor,openedx V --seed=20261020 --per=3 $X --skip-testdev
python -I sample_blind.py out sheet_w.csv key_w.json PostHog,mastodon W --seed=20261020 --per=3 $X --skip-testdev
python -I sample_blind.py out sheet_x.csv key_x.json chatwoot,spree X --seed=20261020 --per=3 $X --skip-testdev
python -I sample_blind.py out sheet_y.csv key_y.json discourse Y --seed=20261020 --per=3 $X --skip-testdev
python -I sample_blind.py out sheet_z.csv key_z.json instructure Z --seed=20261020 --per=3 $X --skip-testdev
cd blind && python -I estimate.py ../out sample1:a,b,c sample2:u,v,w,x,y,z
```

## Engines (Section 6.3, Figure 6)

The engine runs test whether one distributed transaction exposes orphans and inconsistent copies to readers.

- Citus (`run_citus.sh`): a coordinator and three workers; the foreign-key cells run with worker 29701 slowed down (fsync and synchronous commit on, `commit_delay` 10 ms), the copy cell with uniform workers.
  Cells: `e3a` and `e3x_pf` one 2PC writing the parent first, `e3x_cf` one 2PC writing the child first, `e3o` two ordered commits, `e4` a source and its copy updated in one 2PC.
  Initialise the cluster once with `bash citus_cluster.sh init`.
- Vitess (`run_vt.sh`): 2 shards with 3 tablets each, semi-synchronous durability, vtgate in TWOPC mode.
  Cells: `v2` one TwoPC writing the parent first and two ordered commits, `v2swap` one TwoPC writing the child first, `v1` a source and its copy updated in one TwoPC.
- A reader reads the child and then, in the next statement, the parent it points to; an absent parent is an orphan.
  Copy readers read the source and the copy in one statement (Citus) or one scatter query (Vitess).
- The stored results hold 10 batches per Citus cell and 8 per Vitess cell.
  `analyze.py` reports the pooled rate per cell; the interval in Figure 6 spans the pooled Wilson interval and a batch bootstrap interval, and a cell without events reports the Wilson upper bound.
- Four shards (`run_vt4.sh`): the Vitess cells on a cluster with four shards instead of two (`vitess4_up.sh`, the same ports, `VT_SHARDS=4` for `vt_exp.py`), 10 batches.
  One TwoPC exposed an orphan in 88% of the runs that wrote the child first and in none of those that wrote the parent first, and two ordered commits in none (`results/vitess4.md`).
  The fixed MySQL ports of the tablets lie in the ephemeral port range of Linux, so an outgoing connection can hold one at start-up and leave a shard without a primary; `run_vt4.sh` then restarts the cluster, up to three times.
- Commit delay (`run_delay.sh`): the Citus foreign-key cells with the `commit_delay` of worker 29701 swept over 0, 1, 2, 5 and 10 ms, three batches each.
  One 2PC exposed orphans in 34% of the runs from 1 ms on, all with the parent on the delayed worker, and in 0.1% without a delay; two ordered commits exposed none (`results/citus_delay.md`).

### Read direction (Section 6.3, Figure 7)

`run_direction.sh` runs `direction.py` on the Citus cluster above in its uniform configuration (no slow worker), 2,000 runs per configuration.

- A parent row and a child row that references it lie on different workers (64 key pairs).
  The writer inserts both rows or deletes both: `ordered` (two autocommits in the required order: parent first for insertions, child first for deletions), `reversed` (the opposite order), `atomic` (one transaction over both workers, statements in the required order), or `ordered_g1` and `ordered_g10` (ordered, with 1 or 10 ms between the two commits).
- The reader starts 0-2 ms before the writer and polls with two single-row reads per iteration, pausing 0, 1 or 10 ms between them: `c2p` reads the child and then the parent, `p2c` the parent and then the child.
  It stops at the final state (both rows present after an insertion, both absent after a deletion) or after 200 ms.
- A run counts as anomalous if some iteration sees the child but not the parent.
  `analyze_direction.py` reports the share of anomalous runs with a Wilson 95% interval (`results/direction.md`, `results/direction_gap.md`).
  Figure 7 takes the line of one two-phase commit (`atomic`, `c2p`) from the deletion runs of the first grid at 0, 1 and 10 ms and from `run_direction_fill.sh` at 2, 5 and 20 ms, the pauses of the grace grid; the protected reader (`ordered`, `p2c`) saw no orphan in its 6,000 deletion runs.
  The fill also reran 0, 1 and 10 ms: 48.8%, 86.0% and 100%, against 47.8%, 86.1% and 100% in the first grid (`results/direction_fill.md`).

### Grace period (Section 6.3, Figure 7, Proposition 5.8)

`run_grace.sh` runs `grace.py` on the same cluster and configuration, 2,000 runs per configuration.

- The writer issues the two ordered autocommits of `direction.py` and sleeps a pause of 0, 1, 2, 5, 10 or 20 ms between the return of the first and the start of the second.
- The reader reads in the direction that write order does not protect (`c2p` for deletions, `p2c` for insertions) and pauses 0, 1, 2, 5, 10 or 20 ms between its two reads.
  Every iteration records its span, from the start of the first read to the end of the second, which bounds the distance between the two snapshots from above.
- `analyze_grace.py` reports the anomalous runs per cell with a Wilson 95% interval and, as the check of Proposition 5.8, the number of anomalous iterations whose span is at most the pause between the commits (`results/grace.md`).
  It is 0 among 700,952 such iterations; Figure 7 shows the deletion cells.

## Cost of write order (Section 6.3, Figure 5, fault and fence runs)

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

### Number of rounds (Section 6.3, Figure 5f)

`run_depth.sh` runs the `chain` workload of `x7bench.py`: an insertion of a path of k = 2, 3, 4, 5, 6 or 8 rows, row i referencing row i - 1, with consecutive rows on different nodes.
Every hop changes nodes, so leveled commits need k rounds, whereas one two-phase commit keeps its prepare and commit rounds; a node with several transactions of one update gets one connection per transaction.
Three blocks, one client, 2 s of warm-up and 8 s of measurement, on Citus without and with the relays above and on Vitess, with the methods of Figure 5 except the outbox; Figure 5f shows Citus without relays, and `results/depth.md` holds all three.
`depth_analyze.py` divides the median latency of each method by that of the fastest two-phase commit of the same block and reports the geometric mean over the blocks with its range (`results/depth.md`).

### Ordered two-phase commit (Section 6.3)

`run_ordered2pc.sh` compares an ordered two-phase commit with leveled commits on Citus, with the templates, sizes, blocks and windows of Figure 5 and the one 2PC baselines rerun in the same blocks.

- `Ps@d` prepares every single-node transaction of leveled commits at the end of the message that executes it, so that all of them are prepared before any commits, and then commits the prepared transactions level by level with `COMMIT PREPARED`.
- Citus refuses `PREPARE TRANSACTION` after a statement on a distributed table name, so `Ps@d` writes the shard tables by name (`probe_prepare.sh` checks this).
  `Lt@d` is the same on the shard tables without `PREPARE`, so the paired ratio `Ps@d / Lt@d` isolates the cost of the prepares; `Ls@d` are the leveled commits of Figure 5.
- `analyze_ordered2pc.py` reports every method relative to the fastest one 2PC of the same block and the paired ratios (`results/ordered2pc.md`).
  With one client, the prepares raise the median latency by 6% to 26%; with 64 clients they raise it by 34% to 102% and cut the throughput by a fifth to a half.
- The stored run was stopped from outside during its last part (one client behind 1 ms proxies, 53 of 252 runs), and `Lt@d` ran out of keys in a few cells (deletions at 128 rows, and insertions and deletions at 32 rows with 64 clients); the analysis lists the missing pairs.

### Entry through the coordinator and the default mode of Vitess (Section 6.3)

`run_z1.sh` reruns, with 1 and 64 clients, three blocks and the templates, sizes and windows of Figure 5, leveled commits against the fastest two-phase commit in two further settings.

- On Citus, `Ls` enters at the coordinator instead of the workers (`Ls@d`).
- On Vitess, `ms` is one transaction in the default mode of Vitess (`transaction_mode = 'multi'`), which commits the shards one after the other in the order in which the transaction touched them and gives up atomicity; its statements are those of `Ls` in the order of the levels, sent as one message.
- `z1_ratios.py` computes the ratios as `fig_cost.py` does.
  Through the coordinator, `Ls` takes 0.95, 0.87, 0.76 and 0.47 of the time of the fastest two-phase commit at 2 or 3, 8, 32 and 128 rows; on Vitess, `ms` takes 0.44, 0.49, 0.53 and 0.62, and `Ls` 0.40, 0.42, 0.44 and 0.49.
- With 64 clients (`z1_ratios.py results 64`) and one client behind the 0.5 ms relays of Figure 5 (`z1_ratios.py results rtt`), the results are in `results/z1_64_*.jsonl` and `results/z1_rtt_citus.jsonl`.
  These runs share cores between the servers and the clients.
  The paper takes its statements about 64 clients from the runs below, in which every node has cores of its own: there the coordinator becomes the bottleneck of `Ls` (0.61 of the throughput of the fastest two-phase commit at 8 rows), which the unpinned runs hide (1.12).

### Every node on cores of its own, 0.5 ms on every path (Section 6.3)

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

## Recovery and deadlocks (Section 6.3, Appendix B, Proposition A.13, Lemma A.14)

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

## Deciders and design (Section 6.4, Appendix A)

`deciders.py` implements the tests of order-sufficiency: `dec_fd` (Theorem 3.6), `dec_unary` (Theorem 4.1), `dec_A` (Theorem 4.2) and `dec_K` (Theorem 4.5).
`m2.py` solves MIN-FREEZE (Theorem 5.1; "M2" in the code) by a minimum cut on bipartite conflict graphs and by a 2-approximation otherwise.
The shard-key condition of Theorem 5.2 is "B-pi" in the code.

```bash
cd deciders && PROCS=64 sh run_all.sh
```

- Step 1 compares the deciders with an exhaustive search over small updates (`selfcheck.py`, reference semantics in `ref_order2.py`) and builds witnesses for rejected schemas (`k_witness.py`).
- Steps 2 and 3 time the deciders single-threaded on random schemas with 10 attributes, 10 FDs and 2 INDs per relation (median of 5 seeds) and on the schema of GitLab (median of 20 runs), and compare the minimum cut with the 2-approximation.
- Step 4 compares the shard keys of Theorem 5.2 with containment rules and with the keys that GitLab declares, and the optimal freeze with freezing every copied source.
  `x4_unique.py` counts the tables with a single-column shard key that makes every fresh update executable and under which a shard checks every unique key (1,040 of 1,127); the rule of Citus leaves 600 tables without such a key (`none_citus` in `results/x4_shardkey.json`), so it admits one for 527.
  `x4_shardk.py` checks Theorem 5.3 on the 903 copies that GitLab maintains, 808 of sharding keys and 95 that callbacks write, under co-location by the declared sharding keys (Figure 8a): the copies whose IND violates condition (4), and how many of them co-location carries, are 126 and 93 if every write counts as a modification, 45 and 35 without the initialisations of new rows, and 4 and 4 with frozen sharding keys.
  `x4_freq.py` draws template frequencies from four distributions and reports the weight that the optimal freeze saves over freezing every copied source (Figure 8b; medians 18%, 54%, 36% and 70%).
- Step 5 runs the checks of Appendix A.3 beyond the exact classes; it needs pycosat, as do steps 6 to 9.
- Step 6 checks Proposition 4.7 (fresh updates on class K) on 403 random schemas of class K by a bounded SAT search that admits only fresh updates (`fresh_k.py`, `fsat.py`), including the order that writes referencing relations first; `seq_sanity.py` confirms that the order check detects violations. It needs pycosat.
- Step 7 checks Proposition A.8 (unique keys that no IND references) by the same search on 4,172 pairs of random schemas of class K with one or two unique keys of one or two non-key attributes and freezes, with 5 entities and 5 values (`fresh_k_uk.py`).
  For the 6 pairs in which the prediction under Fz* finds no witness with 5 entities, `uk_big.py` finds one with 7.
  Fz* is the least fixpoint of Appendix A.3 (`uk_big.pinned`): until 2026-10-20 the function left the attributes of the referenced key out of X_rho, which changes no prediction on these 4,172 pairs, since their pointers do not overlap.
  `uk_overlap.py` checks 8,498 pairs of 144 schemas in which two INDs out of one relation share pointer attributes, with unique keys on sources, on sources together with key attributes and on copies (5 entities, 5 values); `uk_overlap_big.py` searches again, with 8 entities of which 3 change, the 487 pairs that some prediction rejects without a counterexample at 5.
  `uk_overlap_tally.py` scores three predictions: the fixpoint rejects 4,518 pairs, all confirmed, and refutes none of the others; computing every X_rho under Fz without iteration rejects 28 pairs wrongly, and the earlier `uk_big.pinned` 244.
  `uk_tally.py` scores the predictions: under Fz* none is wrong; under Fz, 157 pairs are predicted wrongly (168 checks, 12 of executability and 156 of order-insensitivity).
- Step 8 checks Theorem 5.3 under hash placement with co-location groups on 13,199 combinations of 280 random schemas of class K, freezes, shard keys of one attribute and groups (`kshard.py`), and searches again with more changed entities and unchanged rows for the predicted counterexamples that the first run did not find (`kshard_recheck.py`).
  `kshard_tally.py` prints the outcome: every combination predicted executable has no counterexample within the bounds, every combination predicted not executable has one, and for fresh updates the necessary condition of Appendix A.4 fails to be sufficient in 4 combinations.
- Step 9 checks Theorem 4.9 on 270 random schemas of class C (6,040 pairs of a schema and a freeze, 42,280 checks) and on 21 named families with 552 pairs (`cind_check.py`, `cind_sat.py`).
  For every pair it searches for a modification update without a legal order, one with an illegal intermediate state, one whose canonical order of part (1) fails, the same for updates that also insert and delete, and the same for fresh updates.
  On class C every prediction of part (1) holds, and part (2) agrees on 12,072 of 12,080 checks; the other 8 predict a violation that needs 5 entities, which `cind_recheck.py` finds.
  The named families cover the shapes of Section 6.2 (repositories of a disabled integration, SCIM flags, linked identities, the SCIM denial of GitLab, archiving in the running example), each restriction of class C (two tested attributes, sets that form no chain, a cyclic IND graph, a mutable unique key as the referenced key), the two restrictions on pointers (without them, no violation is found up to 6 entities in 7 pairs where the condition of part (2) fails), and combinations with FDs, unique keys and copies of class K, each with the prediction stated in `cind_check.py`.
  `cind_analyze.py` prints the agreement per family and check; `cind_smoke.py` runs a few families quickly.
- `data/gitlab_mutable.json` lists the columns that the code of GitLab commit 9d4cbde6 modifies, with every resolved write site by table, columns, file, line, method and operation (`gl_mutable.py`, which runs the analyzer of `apps/`); `data/gitlab_mutable_filtered.json` leaves out the initialisations of new rows (`gl_sites.py`), and `gl_sk_sites.py` lists the sites that still modify a declared sharding key.
- `data/gitlab_schema.json` holds the tables, keys, foreign keys, sharding keys and loose foreign keys of GitLab commit 9d4cbde6; `gitlab_extract.py` rebuilds it from that commit (usage in `run_all.sh`).
- The large per-task result files are stored gzipped.

## License

The code is released under the MIT License (see `LICENSE`).
`deciders/data/gitlab_schema.json` and `deciders/data/gitlab_mutable*.json` contain schema facts and code locations extracted from GitLab, `corpus/out` contains model names and code locations of Sentry and GitLab, and `apps/out` and `apps/blind` contain table and model names, identifiers and code locations of the ten applications, GitLab and Sentry (file and line, no source text); they remain subject to the licenses of these projects.
