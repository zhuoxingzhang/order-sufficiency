#!/bin/bash
# Hardened cost runs of Section 6.3, the methods of run_z1.sh (Citus h, hA, h@d, hA@d, Ls, Ls@d;
# Vitess h, hA, Ls, ms), the templates at 8 and 128 rows with 1 and 64 clients, three blocks.
#   pin  every Citus node, every Vitess tablet (its mysqld and vttablet), vtgate, the control plane,
#        the relays and the clients on cores of their own (taskset)
#   rtt  as pin, with every path between nodes behind a relay (relay.py) that adds 0.5 ms in each
#        direction.  Citus: client to coordinator, coordinator to workers and client to workers (as in
#        run_cost.sh).  Vitess: client to vtgate, vtgate to the tablets (gRPC, at the host name
#        127.0.0.2 that the tablets advertise) and replica to primary (MySQL replication, every mysqld
#        bound to 127.0.0.1).
# Relays stand in for netem, which needs root.  The core sets fit the two-socket machine of the paper
# (servers on NUMA node 1, clients on node 0); override CL_CPUS, RELAY_CPUS, CTRL_CPUS, VTGATE_CPUS,
# CI_CPUS_<port> and VT_CPUS_<uid> for another machine.
# Results: results/hardening_{pin,rtt}_{citus,vitess}.jsonl; z1_ratios.py results hardening.
# Needs what run_cost.sh needs, plus taskset.
# usage: cd cost && setsid nohup bash run_hardening.sh > logs/run_hardening.log 2>&1 < /dev/null &
cd "$(dirname "$0")" || exit 1
mkdir -p logs results
PARTS=${PARTS:-"pin rtt"}
want() { [[ " $PARTS " == *" $1 "* ]]; }
B=${B:-3}
DELAY=${DELAY:-0.5}
MC=h,hA,h@d,hA@d,Ls,Ls@d
MV=h,hA,Ls,ms
CL_CPUS=${CL_CPUS:-32-63,160-191}
RELAY_CPUS=${RELAY_CPUS:-96-103,224-231}
CTRL_CPUS=${CTRL_CPUS:-104-107,232-235}
VTGATE_CPUS=${VTGATE_CPUS:-88-95,216-223}
declare -A CI_CPUS=([29800]=${CI_CPUS_29800:-64-71,192-199} [29801]=${CI_CPUS_29801:-72-79,200-207}
                    [29802]=${CI_CPUS_29802:-80-87,208-215} [29803]=${CI_CPUS_29803:-88-95,216-223})
declare -A VT_CPUS=([300]=${VT_CPUS_300:-64-67,192-195} [301]=${VT_CPUS_301:-68-71,196-199}
                    [302]=${VT_CPUS_302:-72-75,200-203} [400]=${VT_CPUS_400:-76-79,204-207}
                    [401]=${VT_CPUS_401:-80-83,208-211} [402]=${VT_CPUS_402:-84-87,212-215})
source x7_citus_lib.sh
source x7_vt_lib.sh
CITUS_UP=0
RELAY_PIDS=()
stop_relays() {
  [ ${#RELAY_PIDS[@]} -gt 0 ] && kill ${RELAY_PIDS[@]} 2>/dev/null
  RELAY_PIDS=()
}
cleanup() {
  stop_relays
  [ $CITUS_UP = 1 ] && citus_down
  [ -e $WORK/vtdata ] && vt_down
  echo "[stopped] $(date -Is)"
}
trap cleanup EXIT
trap 'exit 1' TERM INT
echo "[start] $(date -Is) parts '$PARTS'"
python -I x7_check_plan.py || { echo "plan check failed"; exit 1; }

pin_tree() {  # $1 = cpus, then pids: pin every thread of each pid and of its children
  local cpus=$1; shift
  for p in "$@"; do
    [ -n "$p" ] || continue
    for q in $p $(pgrep -P $p); do taskset -a -p -c $cpus $q >/dev/null || echo "[pin] failed for $q"; done
  done
}
pin_citus() {
  for p in 29800 29801 29802 29803; do
    pm=$(head -1 $WORK/citus/n$p/postmaster.pid)
    pin_tree ${CI_CPUS[$p]} $pm
    echo "[pin] citus $p postmaster $pm: $(taskset -c -p $pm | sed 's/.*: //'), $(pgrep -P $pm | wc -l) children"
  done
}
pin_vitess() {
  local D=$WORK/vtdata uid tdir m t
  for uid in 300 301 302 400 401 402; do
    printf -v tdir 'vt_%010d' $uid
    m=$(pgrep -u $ME -f "^$VT_MYSQL_ROOT/bin/mysqld --defaults-file=$D/$tdir/")
    t=$(cat $D/$tdir/vttablet.pid 2>/dev/null)
    pin_tree ${VT_CPUS[$uid]} $m $t
    echo "[pin] tablet $uid mysqld $m vttablet $t: $(taskset -c -p $m | sed 's/.*: //') / $(taskset -c -p $t | sed 's/.*: //')"
  done
  pin_tree $VTGATE_CPUS $(cat $D/tmp/vtgate.pid)
  pin_tree $CTRL_CPUS $(pgrep -u $ME -f "^etcd --data-dir $D/etcd") $(cat $D/tmp/vtctld.pid) \
    $(pgrep -u $ME -f "^vtorc .*localhost:32379")
}
relay() {  # $1 = log name, then LHOST:LPORT:THOST:TPORT specs; one process per call
  local name=$1; shift
  taskset -c $RELAY_CPUS python -I relay.py $DELAY "$@" > logs/relay_$name.log 2>&1 &
  RELAY_PIDS+=($!)
}
grid() {  # $1 = engine, $2 = out, $3 = tag, $4 = methods; clients on their own cores
  local e=$1 out=$2 tag=$3 ms=$4
  taskset -c $CL_CPUS python -I x7_scale_run.py $e $out $tag $B 1,64 $ms 8 || return 1
  taskset -c $CL_CPUS python -I x7_scale_run.py $e $out $tag $B 1 $ms 128 || return 1
  taskset -c $CL_CPUS python -I x7_scale_run.py $e $out $tag $B 64 $ms 128 0.5 3 || return 1
}
citus_ready() {
  citus_up && CITUS_UP=1
  [ $CITUS_UP = 1 ] || return 1
  [ -e $WORK/keymap_citus.bin ] || python -I make_keymap.py citus || return 1
  [ -e $WORK/shardmap_citus.bin ] || python -I x7_make_shardmap.py || return 1
  python -I check_keymap.py
}

# ---------------------------------------------------------------- pinned cores, no relays
if want pin; then
  if vt_up; then
    [ -e $WORK/keymap_vitess.bin ] || python -I make_keymap.py vitess || exit 1
    pin_vitess
    grid vitess results/hardening_pin_vitess.jsonl pin $MV || echo "[pin] vitess grid failed"
  else
    echo "[pin] vitess did not start"
  fi
  vt_down
  if citus_ready; then
    pin_citus
    grid citus results/hardening_pin_citus.jsonl pin $MC || echo "[pin] citus grid failed"
  else
    echo "[pin] citus did not start or its placement differs from the key map"
  fi
  [ $CITUS_UP = 1 ] && citus_down
  CITUS_UP=0
fi

# ---------------------------------------------------------------- pinned cores behind relays
if want rtt; then
  printf '[mysqld]\nbind-address = 127.0.0.1\n' > logs/bind_local.cnf
  relay vtgate 127.0.0.1:39306:127.0.0.1:45306
  for uid in 300 301 302 400 401 402; do
    relay g$uid 127.0.0.2:$((46000 + uid)):127.0.0.1:$((46000 + uid))
    relay m$uid 127.0.0.2:$((47000 + uid)):127.0.0.1:$((47000 + uid))
  done
  sleep 2
  if EXTRA_MY_CNF=$PWD/logs/bind_local.cnf TABLET_HOST=127.0.0.2 vt_up; then
    [ -e $WORK/keymap_vitess.bin ] || python -I make_keymap.py vitess || exit 1
    pin_vitess
    echo "[rtt] connections through the relays: vtgate->tablets $(cat logs/relay_g*.log | grep -c accepted), replication $(cat logs/relay_m*.log | grep -c accepted)"
    X6_VT_PORT=39306 X6_DELAY_MS=$DELAY grid vitess results/hardening_rtt_vitess.jsonl rtt $MV || echo "[rtt] vitess grid failed"
  else
    echo "[rtt] vitess did not start behind the relays"
  fi
  vt_down
  stop_relays
  if citus_ready; then
    pin_citus
    for p in 29800 29801 29802 29803; do relay c$p 127.0.0.1:$((p + 10000)):127.0.0.1:$p; done
    sleep 2
    echo "[rtt] nodes: $(set_ports 10000)"
    if wait_sync; then
      X6_CITUS_PORT=39800 X6_DELAY_MS=$DELAY grid citus results/hardening_rtt_citus.jsonl rtt $MC || echo "[rtt] citus grid failed"
    fi
    echo "[rtt] nodes back: $(set_ports 0)"
    stop_relays
    wait_sync
  else
    echo "[rtt] citus did not start or its placement differs from the key map"
  fi
  [ $CITUS_UP = 1 ] && citus_down
  CITUS_UP=0
fi
echo HARDENING_DONE
