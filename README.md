# Atomic Visibility Is the Price of Mutable Redundancy: Schema, Placement and Write Order on Sharded SQL Engines

This repository is the artifact of the paper.
[`order-sufficiency-full.pdf`](order-sufficiency-full.pdf) is its full version, whose appendix holds the proofs (Appendix A) and further experimental results (Appendix B).
The repository holds the code, the inputs and the stored results behind the figures and numbers of Section 6 and the checks of the appendix.

| Directory | Paper | Contents |
|---|---|---|
| [`corpus/`](corpus) | Sections 6.1 and 6.2, Figure 3a | Cross-database write templates of GitLab and Sentry, classified by hand and by a second annotator, and audits of their readers |
| [`apps/`](apps) | Sections 6.1 and 6.2, Figure 3b | Static analyzer for Rails and Django applications, run on ten applications, GitLab and Sentry; its agreement with the hand labels; blind hand labels of 135 sampled templates |
| [`engines/`](engines) | Section 6.3, Figures 5 and 6 | Orphans and inconsistent copies on Citus and Vitess under one two-phase commit and under ordered commits, by read direction and pause |
| [`cost/`](cost) | Section 6.3, Figure 4 | Latency and throughput of leveled commits relative to the fastest two-phase commit |
| [`recovery/`](recovery) | Section 6.3, Appendix B | Recovery of a stopped update (Proposition A.13) and deadlocks across workers (Lemma A.14) |
| [`deciders/`](deciders) | Section 6.4, Figure 7, Appendices A and B | Deciders of order-sufficiency, freezing and shard keys on GitLab, and the bounded searches that check the appendix |

The README of each directory describes its runs and lists the commands that recompute the numbers of the paper from the stored results, without a database.

## Requirements

- Python 3.11 or later and `pip install -r requirements.txt`; every script runs with `python -I`.
- For `engines/`, `cost/` and `recovery/`: PostgreSQL 17.11 with Citus 14.2.0, and Vitess 24.0.5 with MySQL 8.4.2 (see [`engines/README.md`](engines/README.md#setup)).
- For new runs of `corpus/` and `apps/`: checkouts of the analyzed applications at the commits given there, whose code is read but never run.

All experiments ran on one server with two 64-core AMD EPYC 7742 processors and 1 TB of memory under Linux.

## License

The code is released under the MIT License (see [`LICENSE`](LICENSE)).
The schema facts, model names, identifiers and code locations extracted from GitLab, Sentry and the ten applications (`deciders/data`, `corpus/out`, `apps/out`) remain subject to the licenses of these projects.
