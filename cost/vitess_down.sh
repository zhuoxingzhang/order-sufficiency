#!/bin/bash
# Stop the Vitess cluster of vitess_up.sh only (etcd localhost:32379, data root $X7_WORK/vtdata).
# Run as a file (bash vitess_down.sh): the patterns would match an inline `bash -c`.
VTDATAROOT=${X7_WORK:-$(cd "$(dirname "$0")" && pwd)/work}/vtdata
ME=$(id -un)
for p in vtgate vtorc vttablet; do pkill -u $ME -f "^$p .*localhost:32379"; done
sleep 3
pkill -u $ME -f "mysqld_safe --defaults-file=$VTDATAROOT/"
sleep 1
pkill -TERM -u $ME -f "^$VT_MYSQL_ROOT/bin/mysqld --defaults-file=$VTDATAROOT/"
pkill -u $ME -f "^vtctld .*localhost:32379"
pkill -u $ME -f "^etcd --data-dir $VTDATAROOT/etcd"
sleep 3
echo "remaining: $(pgrep -u $ME -fc "$VTDATAROOT|localhost:32379")"
