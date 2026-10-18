#!/bin/bash
# Citus grace-period runs of Figure 7, in the uniform configuration (no slow worker).
# Grid: insert or delete, pause between the two commits 0, 1, 2, 5, 10 or 20 ms, pause between
# the two reads 0, 1, 2, 5, 10 or 20 ms, reader in the direction that write order does not protect.
# N runs per configuration; the trap stops the cluster.
# Needs PostgreSQL 17 with Citus 14.2 and a Python with psycopg2 on PATH.
# usage: N=2000 bash run_grace.sh   (initialise the cluster once with: bash citus_cluster.sh init)
set -u
X=$(cd "$(dirname "$0")" && pwd)
W=${X2_WORK:-$X/work}
S=$W/sock
N=${N:-2000}
mkdir -p $X/results
cd $X
bash $X/citus_cluster.sh start
trap 'bash $X/citus_cluster.sh stop' EXIT

OUT=$X/results/grace.jsonl
{
  echo "[start] $(date -Is) N=$N"
  for p in 29700 29701 29702 29703; do
    echo "$p $(psql -h $S -p $p -U postgres -At -c "SELECT 'fsync='||current_setting('fsync')||' synchronous_commit='||current_setting('synchronous_commit')||' commit_delay='||current_setting('commit_delay')")"
  done
  psql -h $S -p 29700 -U postgres -At -c "SELECT citus_version()"
} > $X/results/grace_config.log
: > $OUT
s=9100
for op in delete insert; do
  for delta in 0 1 2 5 10 20; do
    for think in 0 1 2 5 10 20; do
      s=$((s + 1))
      python -I grace.py $op $delta $think $N $s >> $OUT
    done
  done
done
python -I analyze_grace.py results/grace.jsonl > results/grace.md
