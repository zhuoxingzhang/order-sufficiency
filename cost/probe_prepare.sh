#!/bin/bash
# Probe for method Ps: a transaction that enters at a worker and writes the shard table of a key can
# be prepared with PREPARE TRANSACTION and committed later with COMMIT PREPARED.  Through the
# distributed table name, Citus refuses ("cannot use 2PC in transactions involving multiple servers").
# Needs the Citus cluster of x7_citus_lib.sh to be up.
cd "$(dirname "$0")" || exit 1
source x7_citus_lib.sh
K=990000001
read SH PORT < <(psqlc "SELECT shard_name('parent'::regclass, s.shardid), p.nodeport FROM pg_dist_shard s JOIN pg_dist_placement pl USING (shardid) JOIN pg_dist_node p USING (groupid), LATERAL (SELECT get_shard_id_for_distribution_column('parent', $K) AS sid) g WHERE s.shardid = g.sid" | tr '|' ' ')
echo "key $K -> shard $SH on port $PORT"
out=$(psql -h $SOCK -p $PORT -U postgres -At -v ON_ERROR_STOP=1 <<SQL
SET citus.override_table_visibility TO off;
BEGIN;
INSERT INTO $SH (pid) VALUES ($K);
PREPARE TRANSACTION 'probe_prepare';
COMMIT PREPARED 'probe_prepare';
SELECT count(*) FROM $SH WHERE pid = $K;
DELETE FROM $SH WHERE pid = $K;
SQL
) || { echo "PREPARE TRANSACTION on a shard table failed"; exit 1; }
echo "$out" | grep -qx 1 && echo "prepare on a shard table: ok"
