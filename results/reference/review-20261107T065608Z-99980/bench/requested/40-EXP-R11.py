#!/usr/bin/env python3
"""Answers EXP-R11 (statement forms not yet measured, FIND-LOCK-FORMS-01) and EXP-5 (an indexed-column UPDATE against FOR SHARE).

Uses lock_factors.py's private schema and designs. For each form: peak trx_rows_locked while it runs, locks still held afterward inside the
transaction, and whether an UPDATE and an INSERT in another session get through. A form that ends with zero locks is not called lock-free unless the peak was also zero.
"""
import threading, time
import pymysql
from lablib import connect, query
from lock_factors import DESIGNS, ISOLATIONS, build, try_write
from reqlib import Results, builder, guard, sql_try

ID = "40-EXP-R11"
guard(ID, 9)
r = Results(ID, ["EXP-R11", "EXP-5", "FIND-LOCK-FORMS-01"])
DB = "bench_lock"
SETUP = ["DROP PROCEDURE IF EXISTS p_cnt", "CREATE PROCEDURE p_cnt() SELECT COUNT(*) FROM lk",
         "DROP PROCEDURE IF EXISTS p_set", "CREATE PROCEDURE p_set() BEGIN DECLARE v BIGINT; SET v = (SELECT COUNT(*) FROM lk); SELECT v; END",
         "DROP PROCEDURE IF EXISTS p_into", "CREATE PROCEDURE p_into() BEGIN DECLARE v BIGINT; SELECT COUNT(*) INTO v FROM lk; SELECT v; END",
         "DROP FUNCTION IF EXISTS f_cnt", "CREATE FUNCTION f_cnt() RETURNS BIGINT READS SQL DATA RETURN (SELECT COUNT(*) FROM lk)",
         "DROP TABLE IF EXISTS trg_src", "CREATE TABLE trg_src (id INT PRIMARY KEY)",
         "CREATE TRIGGER trg_cnt BEFORE INSERT ON trg_src FOR EACH ROW SET @tc = (SELECT COUNT(*) FROM lk)"]
FORMS = {
    "do_subquery": ["DO (SELECT COUNT(*) FROM lk)"],
    "select_assign_subquery": ["SELECT @x := (SELECT COUNT(*) FROM lk)"],
    "select_assign_aggregate": ["SELECT @x := COUNT(*) FROM lk"],
    "derived_table": ["SELECT c FROM (SELECT COUNT(*) AS c FROM lk) x"],
    "cte": ["WITH x AS (SELECT COUNT(*) AS c FROM lk) SELECT c FROM x"],
    "scalar_subquery_in_select": ["SELECT (SELECT COUNT(*) FROM lk)"],
    "explain_analyze": ["EXPLAIN ANALYZE SELECT COUNT(*) FROM lk"],
    "prepared": ["PREPARE st FROM 'SELECT COUNT(*) FROM lk'", "EXECUTE st", "DEALLOCATE PREPARE st"],
    "prepared_set": ["PREPARE st FROM 'SET @c = (SELECT COUNT(*) FROM lk)'", "EXECUTE st", "DEALLOCATE PREPARE st"],
    "call_plain": ["CALL p_cnt()"],
    "call_declared_set": ["CALL p_set()"],
    "call_select_into_local": ["CALL p_into()"],
    "stored_function": ["SELECT f_cnt()"],
    "trigger_body": ["INSERT INTO trg_src VALUES (1)"],
    "replace_select": ["REPLACE INTO lk_summary (c) SELECT COUNT(*) FROM lk"],
    "ctas_permanent": ["CREATE TABLE lk_ct AS SELECT COUNT(*) AS c FROM lk"],
    "lock_in_share_mode": ["SELECT COUNT(*) FROM lk LOCK IN SHARE MODE"],
}


def measure(iso, name, statements):
    a = connect(autocommit=False, db=DB, read_timeout=120)
    obs = connect(db=DB, read_timeout=30)
    w = connect(autocommit=False, db=DB, read_timeout=30)
    query(w, "SET SESSION innodb_lock_wait_timeout=1")
    query(a, f"SET SESSION transaction_isolation='{iso}'")
    query(a, "START TRANSACTION")
    peak, halt = [0], threading.Event()

    def watch():
        while not halt.is_set():
            row = query(obs, "SELECT trx_rows_locked FROM information_schema.INNODB_TRX WHERE trx_mysql_thread_id=%s", (a.thread_id(),))
            if row:
                peak[0] = max(peak[0], int(row[0][0]))
            halt.wait(.005)
    th = threading.Thread(target=watch, daemon=True)
    th.start()
    results = []
    t = time.monotonic()
    for s in statements:
        results.append(sql_try(a, s))
    ms = round((time.monotonic() - t) * 1000)
    time.sleep(.02)
    halt.set()
    th.join(2)
    held = query(obs, "SELECT COUNT(*) FROM performance_schema.data_locks WHERE OBJECT_SCHEMA=%s AND OBJECT_NAME='lk' AND LOCK_TYPE='RECORD'", (DB,))[0][0]
    res = {"iso": iso, "form": name, "ok": all(x["ok"] for x in results), "errors": [x.get("errno") for x in results if not x["ok"]], "ms": ms, "peak_rows_locked": peak[0], "record_locks_held_after": held,
           "update_row": try_write(w, "UPDATE lk SET payload=payload+1 WHERE id=360000"), "insert_row": try_write(w, "INSERT INTO lk (id,v,payload) VALUES (360001,1,0)")}
    sql_try(a, "ROLLBACK")
    for x in (a, obs, w):
        x.close()
    s = connect(db=DB)
    sql_try(s, "DROP TABLE IF EXISTS lk_ct")
    sql_try(s, "DELETE FROM trg_src")
    s.close()
    return res


with r.section("forms") as d:
    for design, ddl in DESIGNS.items():
        build(ddl)
        setup = builder(db=DB)  # not in the binlog: the replica has no such tables for the trigger to attach to
        for s in SETUP:
            query(setup, s)
        setup.close()
        out = []
        for iso in ISOLATIONS:
            for name, st in FORMS.items():
                if r.time_left() < 60:
                    d["stopped_early"] = f"{design}/{iso}/{name}"
                    break
                out.append(measure(iso, name, st))
        d[design] = out
        r.save()
        print(design, len(out), flush=True)

with r.section("EXP5_indexed_update") as d:
    build(DESIGNS["pk_and_small_secondary"])
    holder = connect(autocommit=False, db=DB, read_timeout=60)
    query(holder, "START TRANSACTION")
    query(holder, "SELECT COUNT(*) FROM lk FOR SHARE")
    d["plan_key"] = query(connect(db=DB), "EXPLAIN SELECT COUNT(*) FROM lk FOR SHARE")[0][6]
    w = connect(autocommit=False, db=DB, read_timeout=30)
    query(w, "SET SESSION innodb_lock_wait_timeout=1")
    d["non_indexed_update"] = try_write(w, "UPDATE lk SET payload=payload+1 WHERE id=360000")
    d["indexed_update"] = try_write(w, "UPDATE lk SET v=v+1 WHERE id=360000")
    query(holder, "ROLLBACK")
    holder.close(); w.close()

query(connect(), f"DROP DATABASE IF EXISTS {DB}")
r.finish()
