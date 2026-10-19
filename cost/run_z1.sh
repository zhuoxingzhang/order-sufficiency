#!/bin/bash
# Entry through the coordinator of Citus and the default mode of Vitess (Section 6.3), three blocks,
# the templates at 2 or 3, 8, 32 and 128 rows, as in run_cost.sh: one client, 64 clients, and (Citus)
# one client behind relays that add 0.5 ms in each direction.
#   Vitess (methods MV): leveled commits through vtgate (Ls) against one 2PC (h, hA) and against one
#     transaction in the default MULTI mode of Vitess with the statements in level order (ms), which
#     commits the shards one after the other without atomicity.
#   Citus (methods MC): leveled commits entering at the coordinator (Ls) and at the workers (Ls@d)
#     against the four 2PC variants (h, hA, h@d, hA@d).
# Results: results/z1_{citus,vitess}.jsonl (one client), results/z1_64_{citus,vitess}.jsonl (64 clients),
# results/z1_rtt_citus.jsonl (relays); z1_ratios.py RESULTS_DIR {1,64,rtt} gives the ratios.  These runs
# share the machine between servers and clients; the claims about 64 clients rest on the pinned runs
# of run_hardening.sh, since without pinning the clients take cores from the coordinator.
# Needs what run_cost.sh needs.  Cluster data go to $X7_WORK (default ./work).
# usage: cd cost && setsid nohup bash run_z1.sh > logs/run_z1.log 2>&1 < /dev/null &
cd "$(dirname "$0")" || exit 1
mkdir -p logs results
B=${B:-3}
MC=h,hA,h@d,hA@d,Ls,Ls@d
MV=h,hA,Ls,ms
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

# ---------------------------------------------------------------- Vitess
vt_up || exit 1
[ -e $WORK/keymap_vitess.bin ] || python -I make_keymap.py vitess || exit 1
python -I x7bench.py vitess ins ms 8 1 0 1 0.5 2 0 || { echo "ms smoke test failed"; exit 1; }
python -I x7bench.py vitess rep ms 3 1 0 1 0.5 2 0 || { echo "ms smoke test failed"; exit 1; }
python -I x7_scale_run.py vitess results/z1_vitess.jsonl z1 $B 1 $MV || exit 1
python -I x7_scale_run.py vitess results/z1_64_vitess.jsonl z64 $B 64 $MV 2,8,32 || exit 1
python -I x7_scale_run.py vitess results/z1_64_vitess.jsonl z64 $B 64 $MV 128 0.5 3 || exit 1
vt_down

# ---------------------------------------------------------------- Citus
citus_up || exit 1
CITUS_UP=1
[ -e $WORK/keymap_citus.bin ] || python -I make_keymap.py citus || exit 1
[ -e $WORK/shardmap_citus.bin ] || python -I x7_make_shardmap.py || exit 1
python -I check_keymap.py || { echo "placement differs from keymap_citus.bin"; exit 1; }
for wl in ins del rep; do      # leveled commits must not use 2PC, at either entry
  python -I x7_probe_2pc.py $wl 8 Ls || true
  X7_ROUTE=direct python -I x7_probe_2pc.py $wl 8 Ls || true
done
python -I x7_scale_run.py citus results/z1_citus.jsonl z1 $B 1 $MC || exit 1
python -I x7_scale_run.py citus results/z1_64_citus.jsonl z64 $B 64 $MC 2,8,32 || exit 1
python -I x7_scale_run.py citus results/z1_64_citus.jsonl z64 $B 64 $MC 128 0.5 3 || exit 1
python -I x7_proxy.py 0.5 39800:29800 39801:29801 39802:29802 39803:29803 > logs/proxy_z1.log 2>&1 &
sleep 2
echo "[delay 0.5] nodes: $(set_ports 10000)"
wait_sync || exit 1
X6_CITUS_PORT=39800 X6_DELAY_MS=0.5 python -I x7_scale_run.py citus results/z1_rtt_citus.jsonl zd $B 1 $MC || exit 1
echo "[delay 0.5] nodes back: $(set_ports 0)"
pkill -u $ME -f "x7_prox[y].py"
wait_sync
citus_down
CITUS_UP=0
echo Z1_DONE
