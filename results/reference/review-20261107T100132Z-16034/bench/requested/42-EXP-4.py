#!/usr/bin/env python3
"""Answers EXP-4 (child's COUNT and parent's DDL, LOCK TABLES on FK tables). Private FK tables; dropped at the end.

An open transaction holds the same shared metadata lock a long autocommit COUNT holds; the last section uses a real long COUNT on order_items too."""
import threading, time
from lablib import connect, query
from reqlib import Results, builder, guard, reset_schema, sql_try

ID = "42-EXP-4"
guard(ID, 4)
r = Results(ID, ["EXP-4", "FIND-R2-CASCADE-VER-01"])
b = builder()
S = "req_fk"
reset_schema(b, S)
query(b, f"CREATE TABLE {S}.gp (id BIGINT PRIMARY KEY, name VARCHAR(20) NOT NULL)")
query(b, f"CREATE TABLE {S}.parent (id BIGINT PRIMARY KEY, gp_id BIGINT NOT NULL, name VARCHAR(20) NOT NULL, FOREIGN KEY (gp_id) REFERENCES gp (id))")
query(b, f"CREATE TABLE {S}.child (id BIGINT PRIMARY KEY, parent_id BIGINT NOT NULL, v INT NOT NULL, FOREIGN KEY (parent_id) REFERENCES parent (id) ON DELETE CASCADE)")
query(b, f"CREATE TABLE {S}.other (id BIGINT PRIMARY KEY)")
query(b, f"INSERT INTO {S}.gp SELECT n, 'g' FROM bench.seq1m WHERE n<=1000")
query(b, f"INSERT INTO {S}.parent SELECT n, 1+MOD(n,1000), 'p' FROM bench.seq1m WHERE n<=10000")
query(b, f"INSERT INTO {S}.child SELECT n, 1+MOD(n,10000), n FROM bench.seq1m WHERE n<=100000")
query(b, f"INSERT INTO {S}.other VALUES (1)")


def probe(sql, wait=1):
    c = connect(db=S, read_timeout=30)
    query(c, f"SET SESSION lock_wait_timeout={wait}")
    query(c, f"SET SESSION innodb_lock_wait_timeout={wait}")
    res = sql_try(c, sql)
    res.pop("rows", None)
    c.close()
    return res


def locks():
    c = connect(read_timeout=20)
    rows = query(c, """SELECT OBJECT_NAME,LOCK_TYPE,LOCK_STATUS,COUNT(*) FROM performance_schema.metadata_locks WHERE OBJECT_SCHEMA=%s AND OBJECT_TYPE='TABLE' GROUP BY 1,2,3""", (S,))
    c.close()
    return [list(x) for x in rows]


DDL = {"instant_add_column": ("ALTER TABLE {t} ADD COLUMN probe INT NULL, ALGORITHM=INSTANT", "ALTER TABLE {t} DROP COLUMN probe"),
       "inplace_add_index": ("ALTER TABLE {t} ADD INDEX k_probe (name), ALGORITHM=INPLACE, LOCK=NONE", "ALTER TABLE {t} DROP INDEX k_probe")}

with r.section("ddl_against_a_reader") as d:
    # Who waits for whom: a reader holds each table in turn while a DDL runs on each related table.
    for reader in ("child", "parent", "gp", "other"):
        for target in ("parent", "gp"):
            for name, (do, undo) in DDL.items():
                h = connect(db=S, autocommit=False, read_timeout=30)
                query(h, "START TRANSACTION")
                query(h, f"SELECT COUNT(*) FROM {reader}")
                res = probe(do.format(t=target))
                d[f"reader_{reader}/ddl_{target}/{name}"] = {"ok": res["ok"], "errno": res.get("errno"), "ms": res["ms"]}
                query(h, "ROLLBACK")
                h.close()
                if res["ok"]:
                    probe(undo.format(t=target), 10)

with r.section("what_queues_behind_it") as d:
    # child is being read; a parent DDL waits; do new reads of parent and child queue too?
    h = connect(db=S, autocommit=False, read_timeout=60)
    query(h, "START TRANSACTION")
    query(h, "SELECT COUNT(*) FROM child")
    ddl = connect(db=S, read_timeout=60)
    query(ddl, "SET SESSION lock_wait_timeout=6")
    th = threading.Thread(target=lambda: d.update(ddl_result=sql_try(ddl, "ALTER TABLE parent ADD COLUMN probe INT NULL, ALGORITHM=INSTANT")), daemon=True)
    th.start()
    time.sleep(1)
    d["mdl_during_wait"] = locks()
    d["read_parent"] = probe("SELECT * FROM parent WHERE id=1")
    d["read_child"] = probe("SELECT * FROM child WHERE id=1")
    d["read_gp"] = probe("SELECT * FROM gp WHERE id=1")
    d["write_child"] = probe("UPDATE child SET v=v+1 WHERE id=1")
    d["read_other"] = probe("SELECT * FROM other WHERE id=1")
    query(h, "ROLLBACK")
    th.join(10)
    h.close(); ddl.close()
    if d.get("ddl_result", {}).get("ok"):
        probe("ALTER TABLE parent DROP COLUMN probe", 10)

with r.section("lock_tables") as d:
    attempts = {"read_parent": "SELECT * FROM parent WHERE id=1", "update_parent": "UPDATE parent SET name='x' WHERE id=1", "read_child": "SELECT * FROM child WHERE id=1", "update_child": "UPDATE child SET v=v+1 WHERE id=1",
                "insert_child": "INSERT INTO child VALUES (9000001,1,0)", "read_gp": "SELECT * FROM gp WHERE id=1", "update_gp": "UPDATE gp SET name='x' WHERE id=1", "read_other": "SELECT * FROM other WHERE id=1"}
    for lock in ("child READ", "child WRITE", "parent READ", "parent WRITE", "gp WRITE"):
        h = connect(db=S, read_timeout=30)
        query(h, f"LOCK TABLES {lock}")
        d[lock] = {"mdl": locks(), "attempts": {k: {"ok": x["ok"], "errno": x.get("errno")} for k, x in ((k, probe(s)) for k, s in attempts.items())}}
        query(h, "UNLOCK TABLES")
        h.close()
        probe("DELETE FROM child WHERE id=9000001", 5)

with r.section("real_long_count_on_shared_child") as d:
    # A real autocommit COUNT on order_items, and an INSTANT ALTER on its parent orders. Column removed again.
    admin = connect(read_timeout=120)
    c = connect(read_timeout=300)
    query(c, "SET SESSION innodb_parallel_read_threads=1")
    res = {}
    th = threading.Thread(target=lambda: res.update(sql_try(c, "SELECT COUNT(*) FROM order_items FORCE INDEX (PRIMARY)")), daemon=True)
    t0 = time.monotonic()
    th.start()
    time.sleep(.3)
    x = connect(read_timeout=60)
    query(x, "SET SESSION lock_wait_timeout=30")
    d["alter_parent_while_child_counted"] = sql_try(x, "ALTER TABLE orders ADD COLUMN mdl_probe INT NULL, ALGORITHM=INSTANT")
    d["alter_finished_at_s"] = round(time.monotonic() - t0, 2)
    th.join(120)
    d["count_finished_at_s"] = round(time.monotonic() - t0, 2)
    d["count"] = {k: res.get(k) for k in ("ok", "ms")}
    if query(admin, "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='orders' AND COLUMN_NAME='mdl_probe'")[0][0]:
        query(admin, "ALTER TABLE orders DROP COLUMN mdl_probe")

query(b, f"DROP DATABASE {S}")
r.finish()
