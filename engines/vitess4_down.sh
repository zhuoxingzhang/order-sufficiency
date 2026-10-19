#!/bin/bash
# Stop the four-shard Vitess cluster of vitess4_up.sh only. Run as a file (bash vitess4_down.sh): the
# patterns would match an inline `bash -c`.  mysqld may ignore SIGTERM once its data root is gone, so
# the last step kills what is left.
VTDATAROOT=${X2_WORK:-$(cd "$(dirname "$0")" && pwd)/work}/vtdataroot4
ME=$(id -un)
for p in vtgate vtorc vttablet; do pkill -u $ME -f "^$p .*localhost:22379"; done
sleep 3
pkill -u $ME -f "mysqld_safe --defaults-file=$VTDATAROOT/"
sleep 1
pkill -TERM -u $ME -f "^$VT_MYSQL_ROOT/bin/mysqld --defaults-file=$VTDATAROOT/"
pkill -u $ME -f "^vtctld .*localhost:22379"
pkill -u $ME -f "^etcd --data-dir $VTDATAROOT/etcd"
for _ in $(seq 1 30); do [ "$(pgrep -u $ME -fc "$VTDATAROOT/")" = 0 ] && break; sleep 1; done
pkill -KILL -u $ME -f "$VTDATAROOT/"
