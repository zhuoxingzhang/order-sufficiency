#!/bin/bash
# Ordered two-phase commit on Citus (Section 6.3, "Atomicity on top of order costs more").
# Ps prepares every transaction of leveled commits at the end of its first message and then commits
# the prepared transactions level by level; Lt is the same on the shard tables without PREPARE;
# Ls@d are the leveled commits of Figure 5; h, hA, h@d, hA@d are one 2PC.  Three blocks, the
# templates at 2 or 3, 8, 32 and 128 rows, one client and 64 clients, as in run_cost.sh.
# The stored results (results/ordered2pc_*.jsonl) come from a run that was stopped from outside
# during its last part (one client behind 1 ms proxies), and Lt exhausted its keys in a few cells
# (deletions at 128 rows, and at 32 rows with 64 clients); analyze_ordered2pc.py lists the
# missing pairs.  The paper uses the paired ratios Ps/Lt with one client and 64 clients.
# usage: cd cost && setsid nohup bash run_ordered2pc.sh > logs/run_ordered2pc.log 2>&1 < /dev/null &
cd "$(dirname "$0")" || exit 1
mkdir -p logs results
B=${B:-3}
MC=h,hA,h@d,hA@d,Ls@d,Lt@d,Ps@d
source x7_citus_lib.sh
CITUS_UP=0
cleanup() {
  pkill -u $ME -f "x7_prox[y].py" 2>/dev/null
  [ $CITUS_UP = 1 ] && citus_down
  echo "[stopped] $(date -Is)"
}
trap cleanup EXIT
trap 'exit 1' TERM INT
echo "[start] $(date -Is)"
citus_up || exit 1
CITUS_UP=1
[ -e $WORK/keymap_citus.bin ] || python -I make_keymap.py citus || exit 1
[ -e $WORK/shardmap_citus.bin ] || python -I x7_make_shardmap.py || exit 1
python -I check_keymap.py || { echo "placement differs from keymap_citus.bin"; exit 1; }
bash probe_prepare.sh || exit 1
python -I x7_scale_run.py citus results/ordered2pc_citus.jsonl y1 $B 1 $MC || exit 1
python -I x7_scale_run.py citus results/ordered2pc64_citus.jsonl y64 $B 64 $MC 2,8,32 || exit 1
python -I x7_scale_run.py citus results/ordered2pc64_citus.jsonl y64 $B 64 $MC 128 0.5 3 || exit 1
python -I x7_proxy.py 0.5 39800:29800 39801:29801 39802:29802 39803:29803 > logs/proxy_ordered2pc.log 2>&1 &
sleep 2
echo "[delay 0.5] nodes: $(set_ports 10000)"
wait_sync || exit 1
X6_CITUS_PORT=39800 X6_DELAY_MS=0.5 python -I x7_scale_run.py citus results/ordered2pc_rtt_citus.jsonl yd $B 1 $MC || exit 1
echo "[delay 0.5] nodes back: $(set_ports 0)"
pkill -u $ME -f "x7_prox[y].py"
wait_sync
for p in 29801 29802 29803; do
  echo "prepared transactions left on $p: $(psql -h $SOCK -p $p -U postgres -At -c "SELECT count(*) FROM pg_prepared_xacts")"
done
