# Static analyzer (Sections 6.1 and 6.2, Figure 3b)

| Paper | Produces the results | Numbers and figure from |
|---|---|---|
| Sections 6.1 and 6.2, static analyzer, Figure 3b | `run_all.sh` | `validate.py`, `fig_apps.py` |

The stored results reproduce the numbers of the paper without a database:

```bash
python -I validate.py out && python -I fig_apps.py out fig_apps.tex
```

The analyzer in `apps/analyzer` reads the code of Rails and Django applications statically.

- It collects the references (declared foreign keys and the associations that the code relies on), the copies that callbacks keep equal to a column of the referenced row, and the derived values such as counter caches (`rails_*.py`, `django_front.py`, `schema.py`).
- It groups the writes into templates: transaction blocks, methods, and the creation, modification or deletion of a row of a model with its callbacks, cascades, counter caches and queued jobs.
- `classify.py` labels every template that writes several tables, assuming that every table may lie on a shard of its own: it needs only order if it inserts or deletes rows or moves pointers along references (Proposition 5.4), and atomic visibility if it changes a source and its copy (Theorem 4.8).
  Under the strict reading, a derived value must equal its definition in every state, so changing it together with its rows needs atomic visibility; under the one-way reading, readers accept a derived value that lags behind its rows, so order suffices.
- `run_gitlab.py` and `run_sentry.py` add the partitions of GitLab (databases and gitlab_schemas, loose foreign keys, the exemptions from its guard) and the silos of Sentry (hybrid-cloud foreign keys, remote calls, outboxes), and count only constraints across partitions or silos.

`run_all.sh` runs the analyzer over the ten applications, GitLab and Sentry; the outputs of the paper are in `out/` (gzipped).
`validate.py` prints the verdicts of the 6,033 templates of the ten applications that write several tables outside test and development code: 5,049 of them are constrained, and 99% of these need only order under the one-way reading and 78% under the strict one.
`fig_apps.py` writes Figure 3b.

```bash
SRC=<directory of the checkouts> sh run_all.sh
python -I validate.py out
python -I fig_apps.py out fig_apps.tex
```
