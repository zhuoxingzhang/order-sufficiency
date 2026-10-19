#!/bin/bash
# Entry through the coordinator of Citus and the default mode of Vitess (Section 6.3), one client,
# three blocks, the templates at 2 or 3, 8, 32 and 128 rows, as in run_cost.sh.
#   Vitess (methods MV): leveled commits through vtgate (Ls) against one 2PC (h, hA) and against one
#     transaction in the default MULTI mode of Vitess with the statements in level order (ms), which
#     commits the shards one after the other without atomicity.
#   Citus (methods MC): leveled commits entering at the coordinator (Ls) and at the workers (Ls@d)
#     against the four 2PC variants (h, hA, h@d, hA@d).
# The paper uses the one-client runs only (results/z1_vitess.jsonl, results/z1_citus.jsonl).  The same
# comparison with 64 clients has not been run, and a run behind 0.5 ms relays did not complete, so
# neither is part of this script or of the stored results.
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
citus_down
CITUS_UP=0
echo Z1_DONE
