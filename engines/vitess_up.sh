#!/bin/bash
# Vitess 24.0.5 local cluster: 2 shards (-80, 80-) x 3 tablets, durability semi_sync, vtgate TWOPC.
# Ports shifted by +20000 from the upstream examples to avoid collisions on a shared host.
# Needs VTROOT (a Vitess 24.0.5 build) and VT_MYSQL_ROOT (a MySQL 8.4 installation) in the environment;
# data root $X2_WORK/vtdataroot.
set -x
: "${VTROOT:?set VTROOT to the Vitess 24.0.5 build}"
: "${VT_MYSQL_ROOT:?set VT_MYSQL_ROOT to the MySQL 8.4 installation}"
export PATH=$VTROOT/bin:$PATH
export VT_MYSQL_ROOT
export VTDATAROOT=${X2_WORK:-$(cd "$(dirname "$0")" && pwd)/work}/vtdataroot
mkdir -p $VTDATAROOT/tmp $VTDATAROOT/etcd $VTDATAROOT/backups
ETCD=localhost:22379
TOPO="--topo-implementation etcd2 --topo-global-server-address $ETCD --topo-global-root /vitess/global"
CELL=test
VTCTLD=localhost:35999
CFG=--config-file-not-found-handling=ignore

etcd --data-dir $VTDATAROOT/etcd --listen-client-urls http://$ETCD --advertise-client-urls http://$ETCD \
  --listen-peer-urls http://localhost:22380 --initial-advertise-peer-urls http://localhost:22380 \
  --initial-cluster default=http://localhost:22380 > $VTDATAROOT/tmp/etcd.out 2>&1 &
sleep 5
vtctldclient --server internal $TOPO AddCellInfo --root /vitess/$CELL --server-address $ETCD $CELL

vtctld $CFG $TOPO --service-map 'grpc-vtctl,grpc-vtctld' --backup-storage-implementation file \
  --file-backup-storage-root $VTDATAROOT/backups --port 35000 --grpc-port 35999 \
  --pid-file $VTDATAROOT/tmp/vtctld.pid --log-format text > $VTDATAROOT/tmp/vtctld.out 2>&1 &
for _ in $(seq 0 300); do curl -s -I http://localhost:35000/debug/status >/dev/null && break; sleep 0.2; done

vtctldclient --server $VTCTLD CreateKeyspace --durability-policy=semi_sync ks

start_tablet() {
  uid=$1; shard=$2
  printf -v alias '%s-%010d' $CELL $uid
  printf -v tdir 'vt_%010d' $uid
  mysqlctl $CFG --tablet-uid $uid --mysql-port $((37000 + uid)) --log-format text init
  vttablet $CFG $TOPO --tablet-path $alias --tablet-hostname localhost --init-keyspace ks --init-shard $shard \
    --init-tablet-type replica --health-check-interval 5s --backup-storage-implementation file \
    --file-backup-storage-root $VTDATAROOT/backups --port $((35000 + uid)) --grpc-port $((36000 + uid)) \
    --service-map 'grpc-queryservice,grpc-tabletmanager,grpc-updatestream' \
    --pid-file $VTDATAROOT/$tdir/vttablet.pid --heartbeat-on-demand-duration=5s --log-format text \
    > $VTDATAROOT/$tdir/vttablet.out 2>&1 &
}
for uid in 100 101 102; do start_tablet $uid -80; done
for uid in 200 201 202; do start_tablet $uid 80-; done
sleep 10

vtorc $CFG $TOPO --logtostderr --alsologtostderr --config-path=$VTROOT/examples/common/vtorc/ \
  --config-name=config.yaml --config-type=yml --cell $CELL --port 36500 --log-format text \
  > $VTDATAROOT/tmp/vtorc.out 2>&1 &

for _ in $(seq 0 120); do
  n=$(vtctldclient --server $VTCTLD GetTablets --keyspace ks 2>/dev/null | grep -c primary)
  [ "$n" = "2" ] && break
  sleep 2
done
vtctldclient --server $VTCTLD GetTablets --keyspace ks

cat > $VTDATAROOT/tmp/schema.sql <<'SQL'
create table prof (uid bigint not null, name bigint not null, primary key (uid)) engine=InnoDB;
create table ordr (oid bigint not null, uid bigint not null, uname bigint not null, primary key (oid)) engine=InnoDB;
create table parent (pid bigint not null, primary key (pid)) engine=InnoDB;
create table child (cid bigint not null, pid bigint not null, primary key (cid)) engine=InnoDB;
SQL
cat > $VTDATAROOT/tmp/vschema.json <<'JSON'
{"sharded": true,
 "vindexes": {"hash": {"type": "hash"}},
 "tables": {"prof": {"column_vindexes": [{"column": "uid", "name": "hash"}]},
            "ordr": {"column_vindexes": [{"column": "oid", "name": "hash"}]},
            "parent": {"column_vindexes": [{"column": "pid", "name": "hash"}]},
            "child": {"column_vindexes": [{"column": "cid", "name": "hash"}]}}}
JSON
vtctldclient --server $VTCTLD ApplySchema --sql-file $VTDATAROOT/tmp/schema.sql ks
vtctldclient --server $VTCTLD ApplyVSchema --vschema-file $VTDATAROOT/tmp/vschema.json ks

vtgate $CFG $TOPO --port 35001 --grpc-port 35991 --mysql-server-port 35306 \
  --mysql-server-socket-path $VTDATAROOT/tmp/vtgate.sock --cell $CELL --cells-to-watch $CELL \
  --tablet-types-to-wait PRIMARY,REPLICA --service-map 'grpc-vtgateservice' --pid-file $VTDATAROOT/tmp/vtgate.pid \
  --mysql-auth-server-impl none --transaction-mode TWOPC --log-format text > $VTDATAROOT/tmp/vtgate.out 2>&1 &
for _ in $(seq 0 300); do curl -s -I http://localhost:35001/debug/status >/dev/null && break; sleep 0.2; done
echo VT_UP_DONE
