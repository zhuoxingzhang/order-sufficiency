#!/bin/bash
# Citus foreign-key cells of Figure 6 (e3x_pf, e3x_cf, e3o) with the injected commit delay of the
# slow worker 29701 swept over 0, 1, 2, 5 and 10 ms (Section 6.3); run_citus.sh uses 10 ms only.
# For every delay, 29701 gets fsync, synchronous_commit, commit_delay = the delay and
# commit_siblings = 0, then B batches of the three cells run.  The trap resets 29701 and stops the cluster.
# Needs PostgreSQL 17 with Citus 14.2 and a Python with psycopg2 on PATH.
# usage: bash run_delay.sh   (initialise the cluster once with: bash citus_cluster.sh init)
set -u
X=$(cd "$(dirname "$0")" && pwd)
W=${X2_WORK:-$X/work}
S=$W/sock
B=${B:-3}
OUT=$X/results/citus_delay.jsonl
CFG=$X/results/citus_delay_config.log
mkdir -p $X/results
cd $X

restore() {
  psql -h $S -p 29701 -U postgres -q -c "ALTER SYSTEM RESET fsync" -c "ALTER SYSTEM RESET synchronous_commit" \
    -c "ALTER SYSTEM RESET commit_delay" -c "ALTER SYSTEM RESET commit_siblings" -c "SELECT pg_reload_conf()" >> $CFG 2>&1
  bash $X/citus_cluster.sh stop
  echo "[stopped] $(date -Is)" >> $CFG
}

: > $OUT
echo "[start] $(date -Is)" > $CFG
bash $X/citus_cluster.sh start
trap restore EXIT
for d in 0 1000 2000 5000 10000; do
  psql -h $S -p 29701 -U postgres -q -c "ALTER SYSTEM SET fsync = on" -c "ALTER SYSTEM SET synchronous_commit = on" \
    -c "ALTER SYSTEM SET commit_delay = $d" -c "ALTER SYSTEM SET commit_siblings = 0" -c "SELECT pg_reload_conf()" >> $CFG 2>&1
  sleep 1
  echo "[commit_delay $d] $(psql -h $S -p 29701 -U postgres -At -c "SELECT current_setting('commit_delay')||' '||current_setting('fsync')")" >> $CFG
  for b in $(seq 1 $B); do
    s=$((7300 + b))
    for cell in e3x_pf e3x_cf e3o; do
      python -I x2_citus.py $cell 2000 $b $s | sed "s/^{/{\"commit_delay_us\": $d, /" >> $OUT
    done
  done
done
python -I analyze_delay.py results/citus_delay.jsonl > results/citus_delay.md
