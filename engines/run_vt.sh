#!/bin/bash
# Vitess engine runs of Figure 6: fresh cluster from vitess_up.sh (TWOPC, 2 shards x 3 tablets,
# semi_sync, no injected delays), then B batches of every cell. The trap stops the cluster.
# Needs VTROOT, VT_MYSQL_ROOT (see vitess_up.sh) and a Python with pymysql on PATH.
# usage: bash run_vt.sh
set -u
X=$(cd "$(dirname "$0")" && pwd)
W=${X2_WORK:-$X/work}
B=${B:-10}
OUT=$X/results/vitess.jsonl
LOG=$X/results/vitess_config.log
mkdir -p $X/results
cd $X

: > $OUT
echo "[start] $(date -Is)" > $LOG
[ -e $W/vtdataroot ] && { echo "$W/vtdataroot exists; remove it first" >> $LOG; exit 1; }
trap 'bash $X/vitess_down.sh; sleep 3; echo "[stopped] $(date -Is) remaining: $(pgrep -u $(id -un) -fc "vtdataroot|localhost:22379")" >> $LOG' EXIT
bash $X/vitess_up.sh > $X/results/vt_up.log 2>&1
grep -q VT_UP_DONE $X/results/vt_up.log || { echo "vt_up failed" >> $LOG; exit 1; }
# vtgate's MySQL port may open some seconds after vitess_up.sh returns
for _ in $(seq 1 150); do
  python -I -c "import pymysql; pymysql.connect(host='127.0.0.1', port=35306, user='root', database='ks').close()" 2>/dev/null && break
  sleep 2
done
$VT_MYSQL_ROOT/bin/mysqld --version >> $LOG 2>&1
python -I -c "import pymysql; c = pymysql.connect(host='127.0.0.1', port=35306, user='root', database='ks'); cur = c.cursor(); cur.execute('select @@version, @@transaction_mode'); print('vtgate:', cur.fetchall())" >> $LOG 2>&1
$VTROOT/bin/vtgate --version >> $LOG 2>&1
grep -o 'durability-policy=[a-z_]*' $X/results/vt_up.log | head -1 >> $LOG

for b in $(seq 1 $B); do
  s=$((11000 + b))
  python -I x2_vt.py v2 2000 $b $s >> $OUT
  python -I x2_vt.py v2swap 2000 $b $s >> $OUT
  python -I x2_vt.py v1 2000 $b $s >> $OUT
done
echo VT_DONE >> $OUT
