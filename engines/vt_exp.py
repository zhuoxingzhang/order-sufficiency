"""Vitess 24 (vtgate --transaction-mode TWOPC, 2 shards or 4 with VT_SHARDS=4, semi_sync) anomaly reproduction.
V1 redundant copy (FD / non-key IND) across shards: TwoPC writer, scatter or transactional reader.
V2 foreign key across shards: TwoPC atomic writer vs ordered autocommit writer; reader child-then-parent.
"""
import json, os, random, sys, threading, time
import pymysql

CFG = dict(host='127.0.0.1', port=35306, user='root', database='ks', autocommit=True)
SHARDS = ('-40', '40-80', '80-c0', 'c0-') if os.environ.get('VT_SHARDS') == '4' else ('-80', '80-')


def conn():
    return pymysql.connect(**CFG)


def q(c, sql, args=None):
    with c.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def shard_of(c, table, col, key):
    for s in SHARDS:
        q(c, f"use `ks:{s}`")
        hit = q(c, f"select 1 from {table} where {col} = %s", (key,))
        q(c, "use ks")
        if hit:
            return s
    return None


def pick_keys(c, table, col, n, mk_row):
    out, seen = [], set()
    k = 1
    while len(out) < n:
        q(c, mk_row(k))
        s = shard_of(c, table, col, k)
        if s not in seen:
            seen.add(s)
            out.append((k, s))
        k += 1
    return out


def v1(writes, mode):
    c = conn()
    for t in ('prof', 'ordr'):
        q(c, f"delete from {t}")
    (u, su), = pick_keys(c, 'prof', 'uid', 1, lambda k: f"insert into prof values ({k}, 0)")
    o = None
    k = 100
    while o is None:
        q(c, f"insert into ordr values ({k}, {u}, 0)")
        if shard_of(c, 'ordr', 'oid', k) != su:
            o = k
        else:
            q(c, f"delete from ordr where oid = {k}")
        k += 1
    q(c, "delete from ordr where oid <> %s", (o,))
    cw, cr = conn(), conn()
    stop = threading.Event()
    st = {'reads': 0, 'mism': 0}

    def reader():
        while not stop.is_set():
            if mode == 'scatter':
                rows = dict(q(cr, "select 'c', uname from ordr where oid = %s union all select 'm', name from prof where uid = %s", (o, u)))
                a, b = rows['c'], rows['m']
            else:
                q(cr, "begin")
                a = q(cr, "select uname from ordr where oid = %s", (o,))[0][0]
                b = q(cr, "select name from prof where uid = %s", (u,))[0][0]
                q(cr, "commit")
            st['reads'] += 1
            st['mism'] += (a != b)
    th = threading.Thread(target=reader)
    th.start()
    for t in range(1, writes + 1):
        q(cw, "begin")
        q(cw, "update prof set name = %s where uid = %s", (t, u))
        q(cw, "update ordr set uname = %s where oid = %s", (t, o))
        q(cw, "commit")
    stop.set()
    th.join()
    return {'mode': mode, 'writer_txns': writes, 'reads': st['reads'], 'mismatched_reads': st['mism'],
            'shards': [su, shard_of(c, 'ordr', 'oid', o)]}


def v2(trials, mode):
    c = conn()
    q(c, "delete from parent"); q(c, "delete from child")
    pairs = []
    k = 1
    while len(pairs) < 32:
        q(c, f"insert into parent values ({k})"); q(c, f"insert into child values ({10000 + k}, {k})")
        if shard_of(c, 'parent', 'pid', k) != shard_of(c, 'child', 'cid', 10000 + k):
            pairs.append((k, 10000 + k))
        k += 1
    q(c, "delete from parent"); q(c, "delete from child")
    cw, cr = conn(), conn()
    orphans = seen = 0
    for t in range(trials):
        p, ch = pairs[t % len(pairs)]
        q(c, "delete from child where cid = %s", (ch,)); q(c, "delete from parent where pid = %s", (p,))
        found = {}

        def w():
            if mode == 'atomic':
                q(cw, "begin"); q(cw, "insert into parent values (%s)", (p,)); q(cw, "insert into child values (%s, %s)", (ch, p)); q(cw, "commit")
            elif mode == 'atomic_child_first':
                q(cw, "begin"); q(cw, "insert into child values (%s, %s)", (ch, p)); q(cw, "insert into parent values (%s)", (p,)); q(cw, "commit")
            else:
                q(cw, "insert into parent values (%s)", (p,)); q(cw, "insert into child values (%s, %s)", (ch, p))

        def r():
            end = time.time() + 0.2
            while time.time() < end:
                got = q(cr, "select pid from child where cid = %s", (ch,))
                if got:
                    found['orphan'] = not q(cr, "select 1 from parent where pid = %s", (got[0][0],))
                    return
        a = threading.Thread(target=w); b = threading.Thread(target=r)
        b.start(); a.start(); a.join(); b.join()
        if 'orphan' in found:
            seen += 1
            orphans += found['orphan']
    return {'mode': mode, 'trials': trials, 'reads_that_saw_child': seen, 'orphans': orphans}


if __name__ == '__main__':
    which, n = sys.argv[1], int(sys.argv[2])
    random.seed(11)
    c = conn()
    mode = q(c, "select @@transaction_mode")
    if which == 'v1':
        r = [v1(n, 'scatter'), v1(n, 'txn')]
    elif which == 'v2':
        r = [v2(n, 'atomic'), v2(n, 'ordered')]
    elif which == 'v2swap':
        r = [v2(n, 'atomic_child_first')]
    print(json.dumps({which: r, 'transaction_mode': mode[0][0] if mode else None}))
