#!/bin/bash
# Citus cluster for the engine runs: coordinator 29700 + workers 29701-29703 on one host, data in
# $X2_WORK/cluster, socket in $X2_WORK/sock (X2_WORK defaults to ./work next to this script).
# fsync, synchronous_commit and full_page_writes are off; run_citus.sh turns fsync and
# synchronous_commit on for worker 29701 and delays its commits.
# Needs PostgreSQL 17 with Citus 14.2 on PATH (initdb, pg_ctl, psql).
# usage: citus_cluster.sh init|start|stop
X=${X2_WORK:-$(cd "$(dirname "$0")" && pwd)/work}
B=$X/cluster
SOCK=$X/sock
mkdir -p $B $SOCK
case "$1" in
init)
  for p in 29700 29701 29702 29703; do
    rm -rf $B/n$p
    initdb -D $B/n$p -U postgres --auth=trust >/dev/null
    cat >> $B/n$p/postgresql.conf <<CONF
port = $p
listen_addresses = 'localhost'
unix_socket_directories = '$SOCK'
shared_preload_libraries = 'citus'
max_prepared_transactions = 200
max_connections = 300
fsync = off
synchronous_commit = off
full_page_writes = off
log_min_messages = warning
CONF
    pg_ctl -D $B/n$p -l $B/n$p.log -w start >/dev/null
  done
  psql -h $SOCK -p 29700 -U postgres -c "CREATE EXTENSION citus;" >/dev/null
  for p in 29701 29702 29703; do psql -h $SOCK -p $p -U postgres -c "CREATE EXTENSION citus;" >/dev/null; done
  psql -h $SOCK -p 29700 -U postgres -c "SELECT citus_set_coordinator_host('localhost', 29700);" >/dev/null
  for p in 29701 29702 29703; do psql -h $SOCK -p 29700 -U postgres -c "SELECT citus_add_node('localhost', $p);" >/dev/null; done
  psql -h $SOCK -p 29700 -U postgres -c "SELECT nodename, nodeport, isactive FROM pg_dist_node ORDER BY nodeport;" -c "SELECT citus_version();" -c "SHOW citus.multi_shard_commit_protocol;" 2>&1
  ;;
start) for p in 29700 29701 29702 29703; do pg_ctl -D $B/n$p -l $B/n$p.log -w start >/dev/null; done ;;
stop) for p in 29700 29701 29702 29703; do pg_ctl -D $B/n$p -w stop -m fast >/dev/null; done ;;
esac
