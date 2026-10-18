#!/bin/bash
# Citus direction runs of Figure 8, in the uniform configuration (no slow worker).
# First grid: insert or delete, writer ordered / reversed / atomic, reader c2p / p2c, pause 0, 1
# or 10 ms between the two reads.  Second grid: ordered commits with 1 or 10 ms between the two
# commits.  N runs per configuration; the trap stops the cluster.
# Needs PostgreSQL 17 with Citus 14.2 and a Python with psycopg2 on PATH.
# usage: N=2000 bash run_direction.sh   (initialise the cluster once with: bash citus_cluster.sh init)
set -u
X=$(cd "$(dirname "$0")" && pwd)
W=${X2_WORK:-$X/work}
S=$W/sock
N=${N:-2000}
mkdir -p $X/results
cd $X
bash $X/citus_cluster.sh start
trap 'bash $X/citus_cluster.sh stop' EXIT

config() {
  echo "[start] $(date -Is) N=$N"
  for p in 29700 29701 29702 29703; do
    echo "$p $(psql -h $S -p $p -U postgres -At -c "SELECT 'fsync='||current_setting('fsync')||' synchronous_commit='||current_setting('synchronous_commit')||' commit_delay='||current_setting('commit_delay')")"
  done
  psql -h $S -p 29700 -U postgres -At -c "SELECT citus_version()"
}

OUT=$X/results/direction.jsonl
config > $X/results/direction_config.log
: > $OUT
s=8000
for op in delete insert; do
  for writer in ordered reversed atomic; do
    for reader in c2p p2c; do
      for think in 0 1 10; do
        s=$((s + 1))
        python -I direction.py $op $writer $reader $think $N $s >> $OUT
      done
    done
  done
done

OUT=$X/results/direction_gap.jsonl
config > $X/results/direction_gap_config.log
: > $OUT
s=8100
for op in delete insert; do
  for gap in 1 10; do
    for reader in c2p p2c; do
      for think in 0 1 10; do
        s=$((s + 1))
        python -I direction.py $op ordered_g$gap $reader $think $N $s >> $OUT
      done
    done
  done
done
python -I analyze_direction.py results/direction.jsonl > results/direction.md
python -I analyze_direction.py results/direction_gap.jsonl > results/direction_gap.md
