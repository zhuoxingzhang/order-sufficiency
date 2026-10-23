# Corpus (Sections 6.1 and 6.2, Figure 3a)

| Paper | Produces the results | Numbers and figure from |
|---|---|---|
| Section 6.2, Figure 3a | `sentry_classify.py`, `gitlab_classify.py` | `summarize.py` -> `out/templates.csv` |
| Section 6.2, readers of the templates that need atomic visibility | reading of the code | `atomic_audit.md` |
| Section 6.2, read-path audit (Sentry) | `readpath_probe.py`, `readpath_tolerance.py` | `readpath_audit.md`, `out/readpath_sites.json` |
| Section 6.2, read-path audit (GitLab) | `gitlab_readpath_probe.py`, `gitlab_readpath_sample.py` | `gitlab_readpath_audit.md`, `gitlab_readpath_review.json` |
| Sections 6.1 and 6.2, second annotator | `annotator/templates.csv`, `annotator/sites.csv` | `annotator/agreement.py` |

The stored results reproduce the numbers of the paper without a database:

```bash
python -I summarize.py
cd annotator && python -I agreement.py
```

The corpus consists of the cross-database write templates of GitLab (commit 9d4cbde6, 2026-10-15) and Sentry (commit 13ccaecc, 2026-10-16).
Both code bases are read statically; no application code is run.

- `sentry_corpus.py` and `sentry_rpc.py` scan `src/sentry` of the Sentry checkout for silo models, foreign keys, hybrid-cloud foreign keys, outbox replication and remote procedure calls (`out/sentry_models.json`, `out/sentry_rpc.json`).
- `sentry_classify.py` holds the hand classification of the 65 Sentry templates (42 outbox replications, 23 synchronous remote writes) with their constraints and code locations, and prints the census of the Sentry schema used in Section 6.2.
- `gitlab_classify.py` holds the classification of the 32 GitLab templates: the 15 transactions that GitLab exempts from its guard against cross-database writes, and the deletions of the 17 parents whose loose foreign keys cross databases (from `deciders/data/gitlab_schema.json`).
- `summarize.py` prints Figure 3a and writes `out/templates.csv`, which lists for every template the rows it writes, the constraint between them, the label and the code locations.

```bash
python -I sentry_corpus.py <sentry>/src/sentry out/sentry_models.json
python -I sentry_rpc.py <sentry>/src/sentry out/sentry_rpc.json
python -I sentry_classify.py out/sentry_models.json out
python -I gitlab_classify.py ../deciders/data/gitlab_schema.json out
python -I summarize.py
```

A template needs only order if some write order keeps the constraints between its rows satisfied, atomic visibility if no order does, and serializability if a constraint relies on a read from another database; it is unconstrained if no constraint relates its rows.

## Read paths of the order-only templates (Section 6.2)

Write order protects a reader only if it reads first what the update writes last.
The audit asks, for each of the 48 Sentry templates that need only order, whether some reader reads the two sides in the other direction, and what that reader does when it meets a child whose parent is gone.

- `readpath_probe.py` lists, for every hybrid-cloud foreign key of the deletion templates, the calls of the parent's RPC service whose arguments use the child's foreign-key column within four lines, in non-test files that name the child model (`out/readpath_probe.json`).
- `readpath_tolerance.py` labels how each site handles a missing parent: tolerant (T), batch fetch (B), failure (E), unguarded (U).
  The hand verdicts on all E and U sites, on the sites that are no cross-silo child-to-parent read (FP), and on every other site whose first label did not hold are in `readpath_override.json`; sites found by hand for edges without hits are in `readpath_extra.json`.
  The result is `out/readpath_sites.json`.
- `readpath_audit.md` gives the verdict for every template with its evidence (file and line in `src/sentry`): 16 templates have a reader that reads against the write order (11 deletions, 4 modifications and the insertion D10, whose reader fails closed), and 32 have none.
  Of the 88 sites that read from a child to its parent across silos, 80 tolerate a missing parent and 8 fail.

```bash
python -I readpath_probe.py <sentry>/src/sentry out/sentry_models.json out/readpath_probe.json
python -I readpath_tolerance.py <sentry>/src/sentry out/readpath_probe.json readpath_extra.json out/readpath_sites.json readpath_override.json
```

## Read paths of the GitLab cascading deletions (Section 6.2)

The same question for the 17 cascading deletions of GitLab (C01-C17 of `gitlab_classify.py`): a reader that loads a child and then the parent it references reads against the required order, which removes the children first.

- `gitlab_readpath_probe.py` takes the 249 loose foreign keys of these parents that cross databases (from `../deciders/data/gitlab_schema.json`), finds the `belongs_to` association of each child model for the key, and lists the places in `app`, `ee/app`, `lib` and `ee/lib` that call it, with a first label: tolerant (T), failure (E), unguarded (U) (`out/gitlab_readpath_probe.json`, file and line only).
- `gitlab_readpath_sample.py` draws 6 sites per template (seed 9; 56 sites, `out/gitlab_readpath_sample.json`) and prints them with their context; `gitlab_readpath_review.json` holds the verdict by hand on each.
- `gitlab_readpath_audit.md` gives the verdict for each of the 28 order-only templates with its evidence (file and line at commit 9d4cbde6): 13 have a reader that reads against the required order (11 of the 17 cascading deletions and the exemptions G01 and G11), and for 15 none was found.
  Of the 32 sampled sites that read from a child to its parent across databases, 24 tolerate a missing parent and 8 raise an error.

```bash
python -I gitlab_readpath_probe.py <gitlab> ../deciders/data/gitlab_schema.json out/gitlab_readpath_probe.json
python -I gitlab_readpath_sample.py <gitlab> out/gitlab_readpath_probe.json 6 9 sample.txt out/gitlab_readpath_sample.json
```

## Readers of the templates that need atomic visibility (Section 6.2)

`atomic_audit.md` asks, for each of the 16 templates that need atomic visibility (G12, G13 and G14 of GitLab and 13 of Sentry), whether some reader relies on the copy agreeing with its source.
It names, for each template, the reader with the most severe consequence and further readers by file and line (for GitLab file and line only), and what the reader decides.
Each of the 16 templates has such a reader, and for 13 of them the reader decides authentication or authorization.

## Second annotator (Sections 6.1 and 6.2)

A second annotator, who saw neither our labels nor the paper, labelled the 97 templates and the 181 read-path sites of both audits (Sentry and the GitLab sample) independently from the instructions in `annotator/instructions.md`.
`annotator/templates.csv` and `annotator/sites.csv` hold the returned labels, one per item, with the template or the code location.
`annotator/agreement.py` compares them with our labels in `out/templates.csv`, `out/readpath_sites.json` (a batch fetch counts as tolerant) and `gitlab_readpath_review.json`: the annotator agrees with 85 of the 97 template labels (88%, kappa 0.71), on whether order suffices for 94 of the 97 (kappa 0.90), and with 93% of the verdicts on the 181 sites (kappa 0.88).
The annotator labelled 8 templates `serial`, citing values read from another database that a concurrent update may change; Section 5.3 handles these conflicts with a lock, and the paper keeps our labels.

```bash
cd annotator
python -I agreement.py
```
