# Static analyzer (Sections 6.1 and 6.2, Figure 4b)

| Paper | Produces the results | Numbers and figure from |
|---|---|---|
| Sections 6.1 and 6.2, static analyzer, Figure 4b | `run_all.sh` | `validate.py`, `fig_apps.py` |
| Section 6.2, agreement with the 97 hand labels of `corpus/` | `run_all.sh` | `validate.py` |
| Section 6.2, blind hand labels of 135 templates | `sample_blind.py` | `blind/estimate.py` |

The stored results reproduce the numbers of the paper without a database:

```bash
python -I validate.py out && python -I fig_apps.py out fig_apps.tex
cd blind && python -I estimate.py ../out sample1:a,b,c sample2:u,v,w,x,y,z
```

The analyzer in `apps/analyzer` reads the code of Rails and Django applications statically.

- It collects the references (declared foreign keys and the associations that the code relies on), the copies that callbacks keep equal to a column of the referenced row, and the derived values such as counter caches (`rails_*.py`, `django_front.py`, `schema.py`).
- It groups the writes into templates: transaction blocks, methods, and the creation, modification or deletion of a row of a model with its callbacks, cascades, counter caches and queued jobs.
- `classify.py` labels every template that writes several tables, assuming that every table may lie on a shard of its own: it needs only order if it inserts or deletes rows or moves pointers along references (Proposition 5.4), and atomic visibility if it changes a source and its copy (Theorem 4.8).
  Under the strict reading, a derived value must equal its definition in every state, so changing it together with its rows needs atomic visibility; under the one-way reading, readers accept a derived value that lags behind its rows, so order suffices.
- `run_gitlab.py` and `run_sentry.py` add the partitions of GitLab (databases and gitlab_schemas, loose foreign keys, the exemptions from its guard) and the silos of Sentry (hybrid-cloud foreign keys, remote calls, outboxes), and count only constraints across partitions or silos.

`run_all.sh` runs the analyzer over the ten applications, GitLab and Sentry; the outputs of the paper are in `out/` (gzipped).
`validate.py` first compares the analyzer with the 97 hand labels of `corpus/`: it finds 72 of the templates and agrees with 57 of their labels under the one-way reading (54 under the strict one); it calls 9 order-only templates unconstrained and misses the copies of 4 templates that need atomic visibility, and 11 of its 12 verdicts of atomic visibility are right.
It then prints the verdicts of the 6,033 templates of the ten applications that write several tables outside test and development code: 5,049 of them are constrained, and 99% of these need only order under the one-way reading and 78% under the strict one.
`fig_apps.py` writes Figure 4b.

```bash
SRC=<directory of the checkouts> sh run_all.sh
python -I validate.py out
python -I fig_apps.py out fig_apps.tex
```

## Blind hand labels (Section 6.2)

The share of order among the constrained templates is an upper bound, since the analyzer recognizes only the copies that it can parse.
Templates sampled from the ten applications were therefore labelled by hand, blind to the verdicts of the analyzer.

- `sample_blind.py` draws the samples from `out/`: `sample1` (sheets A, B, C) takes up to 2 templates per application and verdict, and `sample2` (sheets U to Z) 3 per application and strict verdict, outside test and development code and disjoint from `sample1`.
  The sheet shows the repository, the commit, the kind of template and a link to its code; the key (`blind/*/key_*.json`) holds the verdicts of the analyzer.
  Of the 56 templates of `sample1`, the 5 in test or development code were left out before labelling, so 51 carry labels; `sample2` adds 84.
- The instructions define the three kinds of template (including callbacks, cascades, counter caches and queued jobs), the three constraints to consider (references, copies of a column of the referenced row, and derived values such as counts, sums and flags aggregated from other rows), and the labels: `none` if no such constraint relates rows of different tables, `order` if some commit order keeps every constraint satisfied after every commit, and `atomic` if no order does.
  The column `derived` is `yes` if the label is `atomic` only because of a derived value.
  The annotator worked without the paper or the verdicts of the analyzer.
- `blind/*/labels_*.csv` hold the labels, and `blind/estimate.py` weights them by stratum (2,000 resamples) and compares them with the analyzer.
  Both samples pooled (135 templates) put the share of order among the constrained templates at 81.9% (73.8-89.6%); the analyzer agrees with 79 of the 135 labels, calls 40 templates order-only that the labels find unconstrained, and misses 16 that need atomic visibility, whereas the labels confirm all 5 of its sampled verdicts of atomic visibility.

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
