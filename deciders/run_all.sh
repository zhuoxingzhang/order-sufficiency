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
# data/gitlab_mutable.json holds the columns that the code of that commit modifies (gl_mutable.py runs
# the analyzer of ../apps on a checkout), and data/gitlab_mutable_filtered.json the same without the
# initialisations of new rows (gl_sites.py):
#   python -I gl_mutable.py <gitlab> data/gitlab_schema.json data/gitlab_mutable.json
#   python -I gl_sites.py
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
# 4. design gains on GitLab: shard keys (Theorem 5.2), frozen attributes (Theorem 5.1), fresh updates,
#    unique keys, shard keys for the copies of the code (Theorem 5.3, Figure 8), frequencies (Figure 9)
$P -I x4_design.py results/x4_design.json > /dev/null
$P -I x4_shardkey.py results/x4_shardkey.json > /dev/null
$P -I x4_fresh.py results/x4_fresh.json > /dev/null
$P -I x4_unique.py results/x4_unique.json > /dev/null
$P -I x4_shardk.py results/x4_shardk.json > /dev/null
$P -I x4_freq.py results/x4_freq.json > /dev/null
# 5. beyond the exact classes (appendix): the condition of Theorem 3.5 against exhaustive semantics
$P -I k_beyond.py 3000 7 $N results/k_beyond.jsonl > results/k_beyond.json
$P -I k_beyond_recheck.py results/k_beyond.jsonl $N results/k_beyond_recheck.json > /dev/null
$P -I k_beyond_pin.py results/k_beyond.jsonl results/k_beyond_recheck.json $N results/k_beyond_pin.json > /dev/null
# 6. fresh updates on class K (Proposition 4.7): bounded SAT search over fresh updates only
$P -I fresh_k.py 400 7 $N 5 5 results/fresh_k.jsonl > results/fresh_k.txt
$P -I seq_sanity.py results/fresh_k.jsonl 300 5 5 > results/fresh_k_seq.txt
# 7. unique keys (Proposition A.8): bounded SAT search with 5 entities, the pairs without a witness
#    searched again with 7 entities
$P -I fresh_k_uk.py 100 5000 $N 5 5 results/uk_n5d5.jsonl > /dev/null
$P -I uk_big.py results/uk_n5d5.jsonl 7 5 2 > results/uk_big_n7.jsonl
$P -I uk_tally.py results/uk_n5d5.jsonl results/uk_big_n7.jsonl
# 8. shard keys for class K (Theorem 5.3, Appendix A.4): bounded SAT search under the finest
#    placement, then larger bounds for the predicted counterexamples not found
$P -I kshard.py 200 1000 $N 4 4 results/kshard_n4d4.jsonl sanity > /dev/null
$P -I kshard.py 80 5000 $N 5 5 results/kshard_n5d5.jsonl > /dev/null
$P -I kshard_recheck.py results/kshard_n4d4.jsonl arb 4 3 6 $N results/kshard_recheck_n4d4.jsonl > /dev/null
$P -I kshard_recheck.py results/kshard_n5d5.jsonl arb 4 2 5 $N results/kshard_recheck_n5d5.jsonl > /dev/null
$P -I kshard_recheck.py results/kshard_recheck_n5d5.jsonl arb 4 3 6 $N results/kshard_recheck2_n5d5.jsonl > /dev/null
$P -I kshard_recheck.py results/kshard_n4d4.jsonl fresh 4 3 6 $N results/kshard_recheck_n4d4_fresh.jsonl > /dev/null
$P -I kshard_tally.py results/kshard_n4d4.jsonl,results/kshard_n5d5.jsonl \
  results/kshard_recheck_n4d4.jsonl,results/kshard_recheck_n5d5.jsonl,results/kshard_recheck2_n5d5.jsonl \
  results/kshard_recheck_n4d4_fresh.jsonl
# 9. conditional INDs (Theorem 4.9): random schemas of class C with 4 and 5 entities, named families
#    with 5, values 0..3; the checks that a run leaves unconfirmed are searched again with more entities
$P -I cind_check.py random 150 1000 $N 4 4 results/cind_random_n4.jsonl > /dev/null
$P -I cind_check.py random 120 2000 $N 5 4 results/cind_random_n5.jsonl > /dev/null
$P -I cind_check.py named 0 1 $N 5 4 results/cind_named_n5.jsonl > /dev/null
$P -I cind_recheck.py results/cind_random_n4.jsonl 5 4 $N results/cind_recheck_random_n5.jsonl > /dev/null
$P -I cind_recheck.py results/cind_random_n4.jsonl 6 4 $N results/cind_recheck_random_n6.jsonl > /dev/null
$P -I cind_recheck.py results/cind_named_n5.jsonl 6 4 $N results/cind_recheck_named_n6.jsonl > /dev/null
$P -I cind_analyze.py results/cind_random_n4.jsonl results/cind_random_n5.jsonl results/cind_recheck_random_n5.jsonl
$P -I cind_analyze.py results/cind_named_n5.jsonl results/cind_recheck_named_n6.jsonl
echo done
