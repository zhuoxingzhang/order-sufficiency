#!/bin/bash
# Recovery and deadlock runs of Section 6.3 on the Citus cluster of cost/ (cost/citus_cluster.sh,
# data in $X7_WORK, default cost/work).  Parts, chosen with PARTS (default: all):
#  smoke  w6_mock.py (no database); the staged recovery cells with 6 trials, whose outcomes must be
#         the predicted ones; the race cells with 6 trials; every deadlock method with 4 clients for 2 s
#  rec    recovery (w6_recovery.py): staged and race cells, NREC trials per cell, B blocks
#  dl     deadlocks (w6_dl_run.py): 6 methods x DL_CS clients (8, 16, 32, 64) x DL_SKEWS (Zipf 0.99
#         over 1,000 groups and 0.8 over 10,000), B blocks
# The runs create their own tables (w6_parent, w6_child, w6_outbox), colocated with the table parent of
# cost/, and drop them at the end; the trap stops the cluster.  The 64-client runs use about 30 cores.
# Needs PostgreSQL 17 with Citus 14.2 and a Python 3 with psycopg2 on PATH (matplotlib for the figure).
# usage: cd recovery && setsid nohup bash run_recovery.sh > logs/run_recovery.log 2>&1 < /dev/null &
cd "$(dirname "$0")" || exit 1
mkdir -p logs results
PARTS=${PARTS:-"smoke rec dl"}
want() { [[ " $PARTS " == *" $1 "* ]]; }
NREC=${NREC:-200}
B=${B:-3}
DL_CS=${DL_CS:-8,16,32,64}
DL_SKEWS=${DL_SKEWS:-1000:0.99,10000:0.8}
DL_WARM=${DL_WARM:-2}
DL_DUR=${DL_DUR:-10}
source ../cost/x7_citus_lib.sh
export X7_WORK=$WORK
CITUS_UP=0
cleanup() {
  if [ $CITUS_UP = 1 ]; then
    python -I w6_drop.py
    citus_down
  fi
  echo "[stopped] $(date -Is)"
}
trap cleanup EXIT
trap 'exit 1' TERM INT
echo "[start] $(date -Is) parts '$PARTS'"
citus_up || exit 1
CITUS_UP=1
if want smoke; then
  python -I w6_mock.py || { echo "mock failed"; exit 1; }
  python -I w6_recovery.py smoke 0 6 1 > results/smoke_rec.jsonl || { echo "recovery smoke failed"; exit 1; }
  python -I w6_recovery.py race 0 6 1 > results/smoke_race.jsonl || { echo "race smoke failed"; exit 1; }
  rm -f results/smoke_dl.jsonl
  for m in Lnw Lb2000 Lb100 Lord h hd; do
    python -I w6_deadlock.py $m 4 0 0.5 2 >> results/smoke_dl.jsonl || { echo "deadlock smoke $m failed"; exit 1; }
  done
  python -I w6_smoke_check.py results/smoke_dl.jsonl || exit 1
  echo "[smoke ok] $(date -Is)"
fi
if want rec; then
  for b in $(seq 1 $B); do
    python -I w6_recovery.py staged $b $NREC 2 results/rec.jsonl || { echo "rec staged $b failed"; exit 1; }
    python -I w6_recovery.py race $b $NREC 2 results/rec.jsonl || { echo "rec race $b failed"; exit 1; }
  done
  echo "[rec done] $(date -Is)"
fi
if want dl; then
  python -I w6_dl_run.py results/dl.jsonl $B $DL_CS Lnw,Lb2000,Lb100,Lord,h,hd $DL_SKEWS $DL_WARM $DL_DUR || exit 1
fi
python -I w6_analyze.py results
echo "RECOVERY_DONE $PARTS"
