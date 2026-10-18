#!/bin/bash
# Cost runs of Figure 6 and the fault and fence runs, three blocks each.
# The templates (ins, del, rep) run at 2 or 3, 8, 32 and 128 rows.  Within each block the compared
# ways of writing of one cell run back to back in a random order (x7_scale_run.py).
#   Citus (methods MC): one client; 64 clients; one client behind relays that add 0.5 ms in each
#   direction, i.e. 1 ms per round trip.  M@d enters at the worker that holds the rows (X7_ROUTE=direct).
#   Vitess (methods MV, entry through vtgate only): one client; 64 clients.
# Windows: 2 s of warm-up and 8 s of measurement; with 64 clients, 0.5 s and 3 s at 32 rows on Citus
# and at 128 rows on Vitess, and 0.5 s and 2 s at 128 rows on Citus, since a client owns too few keys
# for a longer window.  Every block of a cell that lacks a method is rerun with all methods
# (x7_refill.py), up to three rounds on Citus and four on Vitess.
# Fault runs: a commit message to one worker is lost every 0.5 s (x7_fault.py); fence runs: concurrent
# merges and additions with and without a lock (x7_fence.py).
# Needs: PostgreSQL 17 with Citus 14.2 on PATH, VTROOT and VT_MYSQL_ROOT for Vitess 24.0.5 and MySQL 8.4,
# and a Python 3 with psycopg2 and pymysql on PATH.  Cluster data go to $X7_WORK (default ./work).
# usage: cd cost && setsid nohup bash run_cost.sh > logs/run_cost.log 2>&1 < /dev/null &
cd "$(dirname "$0")" || exit 1
mkdir -p logs results
B=${B:-3}
MC=h,hA,h@d,hA@d,b,b@d,Ls@d,Los@d,Es@d
MV=h,hA,b,Ls,Los,Es
source x7_citus_lib.sh
source x7_vt_lib.sh
CITUS_UP=0
cleanup() {
  pkill -u $ME -f "x7_prox[y].py" 2>/dev/null
  [ $CITUS_UP = 1 ] && citus_down
  [ -e $WORK/vtdata ] && vt_down
  echo "[stopped] $(date -Is)"
}
trap cleanup EXIT
trap 'exit 1' TERM INT
echo "[start] $(date -Is)"
python -I x7_check_plan.py || { echo "plan check failed"; exit 1; }

# ---------------------------------------------------------------- Citus
citus_up || exit 1
CITUS_UP=1
[ -e $WORK/keymap_citus.bin ] || python -I make_keymap.py citus || exit 1
[ -e $WORK/shardmap_citus.bin ] || python -I x7_make_shardmap.py || exit 1
python -I check_keymap.py || { echo "placement differs from keymap_citus.bin"; exit 1; }
for wl in ins del rep; do      # leveled commits and one commit per row must not use 2PC
  X7_ROUTE=direct python -I x7_probe_2pc.py $wl 8 Ls Los Es b
  X7_ROUTE=direct python -I x7_probe_2pc.py $wl 128 Ls Los Es
done
citus_cost() {
  python -I x7_scale_run.py citus results/final_citus.jsonl f1 $B 1 $MC || return 1
  python -I x7_scale_run.py citus results/final64_citus.jsonl f64 $B 64 $MC 2,8 || return 1
  python -I x7_scale_run.py citus results/final64_citus.jsonl f64 $B 64 $MC 32 0.5 3 || return 1
  python -I x7_scale_run.py citus results/final64_citus.jsonl f64 $B 64 $MC 128 0.5 2 || return 1
  python -I x7_proxy.py 0.5 39800:29800 39801:29801 39802:29802 39803:29803 > logs/proxy.log 2>&1 &
  sleep 2
  echo "[delay 0.5] nodes: $(set_ports 10000)"
  wait_sync || return 1
  X6_CITUS_PORT=39800 X6_DELAY_MS=0.5 python -I x7_scale_run.py citus results/final_rtt_citus.jsonl fd $B 1 $MC || return 1
  echo "[delay 0.5] nodes back: $(set_ports 0)"
  pkill -u $ME -f "x7_prox[y].py"
  wait_sync
}
for round in 1 2 3; do
  citus_cost || exit 1
  n=0
  for f in final_citus final64_citus final_rtt_citus; do
    n=$((n + $(python -I x7_refill.py results/$f.jsonl $MC results/$f.partial_old.jsonl)))
  done
  echo "[citus round $round] incomplete blocks: $n"
  [ $n = 0 ] && break
done
for blk in $(seq 1 $B); do
  python -I x7_fault.py a $blk >> results/citus_fault.jsonl || echo "[fault run failed] a $blk"
  X7_ROUTE=direct python -I x7_fault.py L $blk >> results/citus_fault.jsonl || echo "[fault run failed] L $blk"
done
for blk in $(seq 1 $B); do
  for m in a L Lf af; do
    case $m in L|Lf) R=direct ;; *) R=coordinator ;; esac
    X7_ROUTE=$R python -I x7_fence.py $m $blk >> results/citus_fence.jsonl || echo "[fence run failed] $m $blk"
  done
done
citus_down
CITUS_UP=0

# ---------------------------------------------------------------- Vitess
vt_up || exit 1
[ -e $WORK/keymap_vitess.bin ] || python -I make_keymap.py vitess || exit 1
for round in 1 2 3 4; do
  python -I x7_scale_run.py vitess results/final_vitess.jsonl f1 $B 1 $MV || exit 1
  python -I x7_scale_run.py vitess results/final64_vitess.jsonl f64 $B 64 $MV 2,8,32 || exit 1
  python -I x7_scale_run.py vitess results/final64_vitess.jsonl f64 $B 64 $MV 128 0.5 3 || exit 1
  n=0
  for f in final_vitess final64_vitess; do
    n=$((n + $(python -I x7_refill.py results/$f.jsonl $MV results/$f.partial_old.jsonl)))
  done
  echo "[vitess round $round] incomplete blocks: $n"
  [ $n = 0 ] && break
done
vt_down
echo COST_DONE
