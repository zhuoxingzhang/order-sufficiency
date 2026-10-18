#!/bin/bash
# Citus cluster for the cost runs: coordinator 29800 + workers 29801-29803 on one host, data in
# $X7_WORK/citus, socket in $X7_WORK/sock (X7_WORK defaults to ./work next to this script).
# Durability is the PostgreSQL default (fsync, synchronous_commit, full_page_writes all on).
# Needs PostgreSQL 17 with Citus 14.2 on PATH (initdb, pg_ctl, psql).
# usage: citus_cluster.sh init|start|stop|show
X=${X7_WORK:-$(cd "$(dirname "$0")" && pwd)/work}
B=$X/citus
SOCK=$X/sock
PORTS="29800 29801 29802 29803"
mkdir -p $B $SOCK
case "$1" in
init)
  for p in $PORTS; do
    rm -rf $B/n$p
    initdb -D $B/n$p -U postgres --auth=trust >/dev/null
    cat >> $B/n$p/postgresql.conf <<CONF
port = $p
listen_addresses = 'localhost'
unix_socket_directories = '$SOCK'
shared_preload_libraries = 'citus'
max_prepared_transactions = 300
max_connections = 600
shared_buffers = 2GB
log_min_messages = warning
CONF
    pg_ctl -D $B/n$p -l $B/n$p.log -w start >/dev/null
  done
  for p in $PORTS; do psql -h $SOCK -p $p -U postgres -c "CREATE EXTENSION citus;" >/dev/null; done
  psql -h $SOCK -p 29800 -U postgres -c "SELECT citus_set_coordinator_host('localhost', 29800);" >/dev/null
  for p in 29801 29802 29803; do psql -h $SOCK -p 29800 -U postgres -c "SELECT citus_add_node('localhost', $p);" >/dev/null; done
  bash "$0" show
  ;;
start) for p in $PORTS; do pg_ctl -D $B/n$p -l $B/n$p.log -w start >/dev/null; done ;;
stop) for p in $PORTS; do pg_ctl -D $B/n$p -w stop -m fast >/dev/null; done ;;
show)
  psql -h $SOCK -p 29800 -U postgres -At -c "SELECT version()" -c "SELECT citus_version()" \
    -c "SELECT nodename||':'||nodeport||' '||noderole||' active='||isactive FROM pg_dist_node ORDER BY nodeport"
  for p in $PORTS; do
    echo "$p $(psql -h $SOCK -p $p -U postgres -At -c "SELECT string_agg(name||'='||setting, ' ' ORDER BY name) FROM pg_settings WHERE name IN ('fsync','synchronous_commit','full_page_writes','wal_sync_method','shared_buffers','max_connections','max_prepared_transactions','default_transaction_isolation','ssl','citus.node_conninfo','citus.shard_count')")"
  done
  ;;
esac
