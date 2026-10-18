#!/bin/sh
# Decider experiments (E3), design experiments (E4) and the appendix checks, from scratch.
# Runs from this directory with a Python 3 (PY, default python3) and PROCS worker processes; the
# appendix checks beyond class K need pycosat.  The results of the paper are in results/; the large
# per-task files are stored gzipped there (gunzip them before running k_witness.py or the k_beyond
# follow-ups on the stored results).
# data/gitlab_schema.json holds the schema facts of GitLab commit 9d4cbde6; gitlab_extract.py rebuilds
# it from db/docs, config/database_connections, config/gitlab_loose_foreign_keys.yml and db/structure.sql
# of that commit:
#   python -I gitlab_extract.py <db/docs> <database_connections> <lfk.yml> [structure.sql]
set -e
cd "$(dirname "$0")"
P=${PY:-python3}
N=${PROCS:-64}
mkdir -p results
# 1. self-checks: deciders vs exhaustive semantics; MIN-FREEZE algorithms vs brute force
$P -I m2.py 20000 3 > results/m2_selftest.txt
for c in FD:4000 U:1500 A:800 K:800; do
  $P -I selfcheck.py ${c%%:*} ${c#*:} 7 $N results/selfcheck_${c%%:*}.jsonl > results/selfcheck_${c%%:*}.json
done
$P -I k_diamond.py > results/k_diamond.json
$P -I k_witness.py results/selfcheck_K.jsonl results/k_witness.json > /dev/null
# 2. decider timing on random schemas and on GitLab
$P -I x3_time.py $N 5 results/x3_time.jsonl > results/x3_time_summary.jsonl
$P -I x3_baseline.py $N 20 results/x3_baseline.jsonl > results/x3_baseline_summary.jsonl
$P -I x3_gitlab.py 20 results/x3_gitlab.json > /dev/null
# 3. MIN-FREEZE: minimum cut vs 2-approximation
$P -I x3_m2.py $N 5 results/x3_m2.jsonl > results/x3_m2_summary.jsonl
# 4. design gains on GitLab: shard keys (Theorem 5.3), frozen attributes (Theorem 5.1), fresh updates
$P -I x4_design.py results/x4_design.json > /dev/null
$P -I x4_shardkey.py results/x4_shardkey.json > /dev/null
$P -I x4_fresh.py results/x4_fresh.json > /dev/null
# 5. beyond the exact classes (appendix): the condition of Theorem 3.5 against exhaustive semantics
$P -I k_beyond.py 3000 7 $N results/k_beyond.jsonl > results/k_beyond.json
$P -I k_beyond_recheck.py results/k_beyond.jsonl $N results/k_beyond_recheck.json > /dev/null
$P -I k_beyond_pin.py results/k_beyond.jsonl results/k_beyond_recheck.json $N results/k_beyond_pin.json > /dev/null
# 6. fresh updates on class K (Proposition 4.9): bounded SAT search over fresh updates only
$P -I fresh_k.py 400 7 $N 5 5 results/fresh_k.jsonl > results/fresh_k.txt
$P -I seq_sanity.py results/fresh_k.jsonl 300 5 5 > results/fresh_k_seq.txt
echo done
