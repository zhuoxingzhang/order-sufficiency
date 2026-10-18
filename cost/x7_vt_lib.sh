# Helpers for the Vitess cluster of vitess_up.sh (sourced): start with the outbox table, stop and
# remove data.  Needs VTROOT, VT_MYSQL_ROOT and a Python with pymysql on PATH.
export no_proxy=localhost,127.0.0.1 NO_PROXY=localhost,127.0.0.1
WORK=${X7_WORK:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/work}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ME=$(id -un)
vt_up() {
  if [ -e $WORK/vtdata ]; then echo "$WORK/vtdata already exists; not starting"; return 1; fi
  bash $HERE/vitess_up.sh > logs/vt_up.log 2>&1
  ok=0
  for _ in $(seq 0 300); do
    curl --noproxy '*' -sf -o /dev/null http://localhost:45001/debug/status && { ok=1; break; }
    sleep 1
  done
  [ $ok = 1 ] || { echo "vtgate not ready"; return 1; }
  sleep 20
  export PATH=$VTROOT/bin:$PATH
  cat > logs/outbox_schema.sql <<'SQL'
create table outbox (oid bigint not null, payload text not null, primary key (oid)) engine=InnoDB;
SQL
  cat > logs/outbox_vschema.json <<'JSON'
{"sharded": true,
 "vindexes": {"hash": {"type": "hash"}},
 "tables": {"parent": {"column_vindexes": [{"column": "pid", "name": "hash"}]},
            "child": {"column_vindexes": [{"column": "cid", "name": "hash"}]},
            "outbox": {"column_vindexes": [{"column": "oid", "name": "hash"}]}}}
JSON
  vtctldclient --server localhost:45999 ApplySchema --sql-file logs/outbox_schema.sql ks
  vtctldclient --server localhost:45999 ApplyVSchema --vschema-file logs/outbox_vschema.json ks >/dev/null
  sleep 5
  python -I -c "import pymysql; c = pymysql.connect(host='127.0.0.1', port=45306, user='root', database='ks'); cur = c.cursor(); cur.execute('select @@version, @@transaction_mode'); print('vtgate:', cur.fetchall())"
}
vt_down() {
  bash $HERE/vitess_down.sh
  for _ in $(seq 1 120); do [ "$(pgrep -u $ME -fc "$WORK/vtdat[a]")" = 0 ] && break; sleep 1; done
  pkill -KILL -u $ME -f "$WORK/vtdat[a]"
  rm -rf $WORK/vtdata
}
