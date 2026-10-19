#!/bin/bash
# Vitess 24.0.5 cluster for the cost runs: 2 shards (-80, 80-) x 3 tablets, durability semi_sync,
# vtgate TWOPC, on the ports of the upstream examples +30000 (etcd 32379) and the data root
# $X7_WORK/vtdata. Tablet uids 300-302 and 400-402. Tablet pools are raised to 300 so that client
# concurrency, not the default pool caps (16 / 20), bounds the load.  TABLET_HOST (default localhost)
# is the host name that the tablets advertise; run_hardening.sh sets it to 127.0.0.2, where relays wait.
# Needs VTROOT (a Vitess 24.0.5 build) and VT_MYSQL_ROOT (a MySQL 8.4 installation) in the environment.
set -x
: "${VTROOT:?set VTROOT to the Vitess 24.0.5 build}"
: "${VT_MYSQL_ROOT:?set VT_MYSQL_ROOT to the MySQL 8.4 installation}"
export PATH=$VTROOT/bin:$PATH
export VT_MYSQL_ROOT
export VTDATAROOT=${X7_WORK:-$(cd "$(dirname "$0")" && pwd)/work}/vtdata
mkdir -p $VTDATAROOT/tmp $VTDATAROOT/etcd $VTDATAROOT/backups
ETCD=localhost:32379
TOPO="--topo-implementation etcd2 --topo-global-server-address $ETCD --topo-global-root /vitess/global"
CELL=test
VTCTLD=localhost:45999
CFG=--config-file-not-found-handling=ignore
# every server listens on localhost only (vtgate's MySQL port has no authentication)
LOCAL='--bind-address 127.0.0.1 --grpc-bind-address 127.0.0.1'

etcd --data-dir $VTDATAROOT/etcd --listen-client-urls http://$ETCD --advertise-client-urls http://$ETCD \
  --listen-peer-urls http://localhost:32380 --initial-advertise-peer-urls http://localhost:32380 \
  --initial-cluster default=http://localhost:32380 > $VTDATAROOT/tmp/etcd.out 2>&1 &
sleep 5
vtctldclient --server internal $TOPO AddCellInfo --root /vitess/$CELL --server-address $ETCD $CELL

vtctld $CFG $TOPO $LOCAL --service-map 'grpc-vtctl,grpc-vtctld' --backup-storage-implementation file \
  --file-backup-storage-root $VTDATAROOT/backups --port 45000 --grpc-port 45999 \
  --pid-file $VTDATAROOT/tmp/vtctld.pid --log-format text > $VTDATAROOT/tmp/vtctld.out 2>&1 &
for _ in $(seq 0 300); do curl -s -I http://localhost:45000/debug/status >/dev/null && break; sleep 0.2; done

vtctldclient --server $VTCTLD CreateKeyspace --durability-policy=semi_sync ks

start_tablet() {
  uid=$1; shard=$2
  printf -v alias '%s-%010d' $CELL $uid
  printf -v tdir 'vt_%010d' $uid
  mysqlctl $CFG --tablet-uid $uid --mysql-port $((47000 + uid)) --log-format text init
  vttablet $CFG $TOPO $LOCAL --tablet-path $alias --tablet-hostname ${TABLET_HOST:-localhost} --init-keyspace ks --init-shard $shard \
    --init-tablet-type replica --health-check-interval 5s --backup-storage-implementation file \
    --file-backup-storage-root $VTDATAROOT/backups --port $((45000 + uid)) --grpc-port $((46000 + uid)) \
    --service-map 'grpc-queryservice,grpc-tabletmanager,grpc-updatestream' \
    --queryserver-config-pool-size 300 --queryserver-config-transaction-cap 300 \
    --pid-file $VTDATAROOT/$tdir/vttablet.pid --heartbeat-on-demand-duration=5s --log-format text \
    > $VTDATAROOT/$tdir/vttablet.out 2>&1 &
}
for uid in 300 301 302; do start_tablet $uid -80; done
for uid in 400 401 402; do start_tablet $uid 80-; done
sleep 10

vtorc $CFG $TOPO --bind-address 127.0.0.1 --logtostderr --alsologtostderr --config-path=$VTROOT/examples/common/vtorc/ \
  --config-name=config.yaml --config-type=yml --cell $CELL --port ${VTORC_PORT:-46500} --log-format text \
  > $VTDATAROOT/tmp/vtorc.out 2>&1 &

for _ in $(seq 0 120); do
  n=$(vtctldclient --server $VTCTLD GetTablets --keyspace ks 2>/dev/null | grep -c primary)
  [ "$n" = "2" ] && break
  sleep 2
done
vtctldclient --server $VTCTLD GetTablets --keyspace ks

cat > $VTDATAROOT/tmp/schema.sql <<'SQL'
create table parent (pid bigint not null, primary key (pid)) engine=InnoDB;
create table child (cid bigint not null, pid bigint not null, primary key (cid)) engine=InnoDB;
SQL
cat > $VTDATAROOT/tmp/vschema.json <<'JSON'
{"sharded": true,
 "vindexes": {"hash": {"type": "hash"}},
 "tables": {"parent": {"column_vindexes": [{"column": "pid", "name": "hash"}]},
            "child": {"column_vindexes": [{"column": "cid", "name": "hash"}]}}}
JSON
vtctldclient --server $VTCTLD ApplySchema --sql-file $VTDATAROOT/tmp/schema.sql ks
vtctldclient --server $VTCTLD ApplyVSchema --vschema-file $VTDATAROOT/tmp/vschema.json ks

vtgate $CFG $TOPO $LOCAL --mysql-server-bind-address 127.0.0.1 --port 45001 --grpc-port 45991 --mysql-server-port 45306 \
  --mysql-server-socket-path $VTDATAROOT/tmp/vtgate.sock --cell $CELL --cells-to-watch $CELL \
  --tablet-types-to-wait PRIMARY,REPLICA --service-map 'grpc-vtgateservice' --pid-file $VTDATAROOT/tmp/vtgate.pid \
  --mysql-auth-server-impl none --transaction-mode TWOPC --log-format text > $VTDATAROOT/tmp/vtgate.out 2>&1 &
for _ in $(seq 0 300); do curl -s -I http://localhost:45001/debug/status >/dev/null && break; sleep 0.2; done
echo VT_UP_DONE
