# Helpers for the Citus cluster of citus_cluster.sh (sourced): start, metadata sync, worker ports, stop.
# Needs PostgreSQL 17 with Citus 14.2 and a Python with psycopg2 on PATH.
export no_proxy=localhost,127.0.0.1 NO_PROXY=localhost,127.0.0.1
WORK=${X7_WORK:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/work}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SOCK=$WORK/sock
ME=$(id -un)
psqlc() { psql -h $SOCK -p 29800 -U postgres -At -c "$1"; }
wait_sync() {  # port changes leave the workers' metadata to be resynced
  for i in $(seq 1 90); do
    s=$(psqlc "SELECT bool_and(metadatasynced) FROM pg_dist_node WHERE isactive AND groupid <> 0" 2>/dev/null)
    [ "$s" = t ] && { echo "[metadata synced after $((2 * i)) s]"; return 0; }
    psqlc "SELECT start_metadata_sync_to_all_nodes()" >/dev/null 2>&1
    sleep 2
  done
  echo "[metadata not synced]"; return 1
}
set_port() {  # $1 = worker base port (29801..29803), $2 = port the coordinator should use for it
  cur=$(psqlc "SELECT nodeport FROM pg_dist_node WHERE nodeport IN ($1, $(($1 + 10000)), $(($1 + 20000)))")
  psqlc "SELECT citus_update_node(nodeid, 'localhost', $2) FROM pg_dist_node WHERE nodeport = $cur" >/dev/null
}
set_ports() {  # $1 = offset added to 29801..29803
  for p in 29801 29802 29803; do set_port $p $((p + $1)); done
  psqlc "SELECT string_agg(nodeport::text, ' ' ORDER BY nodeport) FROM pg_dist_node"
}
citus_up() {
  if [ -d $WORK/citus/n29800 ]; then bash $HERE/citus_cluster.sh start; else bash $HERE/citus_cluster.sh init; fi
  wait_sync
}
citus_down() {
  pkill -u $ME -f "x7_prox[y].py" 2>/dev/null
  set_ports 0 >/dev/null 2>&1
  wait_sync >/dev/null 2>&1
  bash $HERE/citus_cluster.sh stop
}
