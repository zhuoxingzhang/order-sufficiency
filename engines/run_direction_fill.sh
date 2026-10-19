#!/bin/bash
# Citus direction runs that complete the line of one two-phase commit in Figure 7: deletion, writer
# `atomic`, reader `c2p`, at the read pauses 2, 5 and 20 ms of the grace grid (run_grace.sh), plus
# 0, 1 and 10 ms again as a check against results/direction.jsonl.  Same cluster and uniform
# configuration as run_direction.sh, N runs per pause; the trap stops the cluster.
# Needs PostgreSQL 17 with Citus 14.2 and a Python with psycopg2 on PATH.
# usage: N=2000 bash run_direction_fill.sh   (initialise the cluster once with: bash citus_cluster.sh init)
set -u
X=$(cd "$(dirname "$0")" && pwd)
W=${X2_WORK:-$X/work}
S=$W/sock
N=${N:-2000}
mkdir -p $X/results
cd $X
bash $X/citus_cluster.sh start
trap 'bash $X/citus_cluster.sh stop' EXIT

OUT=$X/results/direction_fill.jsonl
{
  echo "[start] $(date -Is) N=$N"
  for p in 29700 29701 29702 29703; do
    echo "$p $(psql -h $S -p $p -U postgres -At -c "SELECT 'fsync='||current_setting('fsync')||' synchronous_commit='||current_setting('synchronous_commit')||' commit_delay='||current_setting('commit_delay')")"
  done
  psql -h $S -p 29700 -U postgres -At -c "SELECT citus_version()"
} > $X/results/direction_fill_config.log
: > $OUT
s=8200
for think in 2 5 20 0 1 10; do
  s=$((s + 1))
  python -I direction.py delete atomic c2p $think $N $s >> $OUT
done
python -I analyze_direction.py results/direction_fill.jsonl > results/direction_fill.md
