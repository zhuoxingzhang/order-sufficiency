#!/bin/bash
# Citus engine runs of Figure 5: B batches of the copy cell in the uniform configuration, then B
# batches of the foreign-key cells with a slow worker (29701: fsync, synchronous_commit,
# commit_delay = 10 ms, commit_siblings = 0). The trap resets 29701 and stops the cluster.
# Needs PostgreSQL 17 with Citus 14.2 and a Python with psycopg2 on PATH.
# usage: bash run_citus.sh   (initialise the cluster once with: bash citus_cluster.sh init)
set -u
X=$(cd "$(dirname "$0")" && pwd)
W=${X2_WORK:-$X/work}
S=$W/sock
B=${B:-10}
OUT=$X/results/citus.jsonl
CFG=$X/results/citus_config.log
mkdir -p $X/results
cd $X

show() {
  for p in 29700 29701 29702 29703; do
    echo "[$1] $p $(psql -h $S -p $p -U postgres -At -c "SELECT 'fsync='||current_setting('fsync')||' synchronous_commit='||current_setting('synchronous_commit')||' commit_delay='||current_setting('commit_delay')||' commit_siblings='||current_setting('commit_siblings')")"
  done >> $CFG
}
restore() {
  psql -h $S -p 29701 -U postgres -q -c "ALTER SYSTEM RESET fsync" -c "ALTER SYSTEM RESET synchronous_commit" \
    -c "ALTER SYSTEM RESET commit_delay" -c "ALTER SYSTEM RESET commit_siblings" -c "SELECT pg_reload_conf()" >> $CFG 2>&1
  sleep 1
  show after_reset
  bash $X/citus_cluster.sh stop
  echo "[stopped] $(date -Is)" >> $CFG
}

: > $OUT
echo "[start] $(date -Is)" > $CFG
bash $X/citus_cluster.sh start
trap restore EXIT
psql -h $S -p 29700 -U postgres -At -c "SELECT version()" -c "SELECT citus_version()" \
  -c "SELECT nodename||':'||nodeport||' '||noderole||' active='||isactive FROM pg_dist_node ORDER BY nodeport" >> $CFG
show initial

for b in $(seq 1 $B); do
  python -I x2_citus.py e4 2000 $b $((7000 + b)) >> $OUT
done

psql -h $S -p 29701 -U postgres -q -c "ALTER SYSTEM SET fsync = on" -c "ALTER SYSTEM SET synchronous_commit = on" \
  -c "ALTER SYSTEM SET commit_delay = 10000" -c "ALTER SYSTEM SET commit_siblings = 0" -c "SELECT pg_reload_conf()" >> $CFG 2>&1
sleep 1
show slow

for b in $(seq 1 $B); do
  s=$((7100 + b))
  python -I x2_citus.py e3a 2000 $b $s >> $OUT
  python -I x2_citus.py e3o 2000 $b $s >> $OUT
  python -I x2_citus.py e3x_pf 2000 $b $s >> $OUT
  python -I x2_citus.py e3x_cf 2000 $b $s >> $OUT
done
show slow_end
echo CITUS_DONE >> $OUT
