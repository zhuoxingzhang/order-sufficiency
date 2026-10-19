#!/bin/bash
# Vitess engine cells of Figure 6 on four shards instead of two (Section 6.3): fresh cluster from
# vitess4_up.sh (TWOPC, 4 shards x 3 tablets, semi_sync), then B batches of the cells of run_vt.sh with
# VT_SHARDS=4.  The trap stops the cluster.  Up to three attempts to bring the cluster up, since the
# fixed MySQL ports lie in the ephemeral port range and an outgoing connection may hold one of them.
# Needs VTROOT, VT_MYSQL_ROOT (see vitess4_up.sh) and a Python with pymysql on PATH.
# usage: bash run_vt4.sh
set -u
X=$(cd "$(dirname "$0")" && pwd)
W=${X2_WORK:-$X/work}
B=${B:-10}
OUT=$X/results/vitess4.jsonl
LOG=$X/results/vitess4_config.log
mkdir -p $X/results
cd $X
export VT_SHARDS=4

echo "[start] $(date -Is)" > $LOG
[ -e $W/vtdataroot4 ] && { echo "$W/vtdataroot4 exists; remove it first" >> $LOG; exit 1; }
trap 'bash $X/vitess4_down.sh; rm -rf $W/vtdataroot4; echo "[stopped] $(date -Is)" >> $LOG' EXIT
up=0
for attempt in 1 2 3; do
  bash $X/vitess4_up.sh > $X/results/vt4_up.log 2>&1
  grep -q VT4_UP_DONE $X/results/vt4_up.log && { up=1; break; }
  echo "attempt $attempt: $(grep VT4_UP_FAILED $X/results/vt4_up.log)" >> $LOG
  bash $X/vitess4_down.sh; rm -rf $W/vtdataroot4; sleep 30
done
[ $up = 1 ] || { echo "vitess4_up failed" >> $LOG; exit 1; }
for _ in $(seq 1 150); do
  python -I -c "import pymysql; pymysql.connect(host='127.0.0.1', port=35306, user='root', database='ks').close()" 2>/dev/null && break
  sleep 2
done
python -I -c "import pymysql; c = pymysql.connect(host='127.0.0.1', port=35306, user='root', database='ks'); cur = c.cursor(); cur.execute('select @@version, @@transaction_mode'); print('vtgate:', cur.fetchall()); cur.execute('show vitess_shards'); print('shards:', cur.fetchall())" >> $LOG 2>&1

: > $OUT
for b in $(seq 1 $B); do
  s=$((12000 + b))
  python -I x2_vt.py v2 2000 $b $s >> $OUT
  python -I x2_vt.py v2swap 2000 $b $s >> $OUT
  python -I x2_vt.py v1 2000 $b $s >> $OUT
done
echo VT_DONE >> $OUT
(cd $X/results && python -I ../analyze.py --json=vitess4.json vitess4.jsonl > vitess4.md)
