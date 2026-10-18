#!/bin/bash
# Rounds sweep of Figure 7: an insertion of a path of k rows whose every hop changes nodes (x7bench.py,
# workload chain), so that leveled commits need k rounds, against one 2PC at its best.  Three blocks,
# one client, k = 2, 3, 4, 5, 6, 8.  Citus without and with relays that add 0.5 ms in each direction,
# then Vitess.  Methods as in Figure 6, without the outbox.
# Needs what run_cost.sh needs; the key maps of run_cost.sh are reused (built here if missing).
# usage: cd cost && setsid nohup bash run_depth.sh > logs/run_depth.log 2>&1 < /dev/null &
cd "$(dirname "$0")" || exit 1
mkdir -p logs results
B=${B:-3}
KS=${KS:-2,3,4,5,6,8}
MC=h,hA,h@d,hA@d,b@d,Ls@d,Es@d
MV=h,hA,b,Ls,Es
export X7_WLS=chain
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

# ---------------------------------------------------------------- Citus
citus_up || exit 1
CITUS_UP=1
[ -e $WORK/keymap_citus.bin ] || python -I make_keymap.py citus || exit 1
[ -e $WORK/shardmap_citus.bin ] || python -I x7_make_shardmap.py || exit 1
python -I check_keymap.py || { echo "placement differs from keymap_citus.bin"; exit 1; }
python -I x7_scale_run.py citus results/depth_citus.jsonl d1 $B 1 $MC $KS || exit 1
python -I x7_proxy.py 0.5 39800:29800 39801:29801 39802:29802 39803:29803 > logs/proxy_depth.log 2>&1 &
sleep 2
echo "[delay 0.5] nodes: $(set_ports 10000)"
wait_sync || exit 1
X6_CITUS_PORT=39800 X6_DELAY_MS=0.5 python -I x7_scale_run.py citus results/depth_rtt_citus.jsonl dd $B 1 $MC $KS || exit 1
echo "[delay 0.5] nodes back: $(set_ports 0)"
pkill -u $ME -f "x7_prox[y].py"
wait_sync
citus_down
CITUS_UP=0

# ---------------------------------------------------------------- Vitess
vt_up || exit 1
[ -e $WORK/keymap_vitess.bin ] || python -I make_keymap.py vitess || exit 1
# vitess_up.sh applies the schema once both shards have a primary or after a timeout; on a loaded
# machine the timeout can come first, so apply it again, idempotently, once both primaries exist
for _ in $(seq 1 150); do
  [ "$(vtctldclient --server localhost:45999 GetTablets --keyspace ks 2>/dev/null | grep -c primary)" = 2 ] && break
  sleep 2
done
cat > logs/depth_schema.sql <<'SQL'
create table if not exists parent (pid bigint not null, primary key (pid)) engine=InnoDB;
create table if not exists child (cid bigint not null, pid bigint not null, primary key (cid)) engine=InnoDB;
create table if not exists outbox (oid bigint not null, payload text not null, primary key (oid)) engine=InnoDB;
SQL
vtctldclient --server localhost:45999 ApplySchema --sql-file logs/depth_schema.sql ks || exit 1
vtctldclient --server localhost:45999 ApplyVSchema --vschema-file logs/outbox_vschema.json ks >/dev/null || exit 1
sleep 5
python -I x7_scale_run.py vitess results/depth_vitess.jsonl d1 $B 1 $MV $KS || exit 1
vt_down
python -I depth_analyze.py results/depth_citus.jsonl results/depth_rtt_citus.jsonl results/depth_vitess.jsonl > results/depth.md
echo DEPTH_DONE
