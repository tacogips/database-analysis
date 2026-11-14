#!/usr/bin/env python3
"""Read-only COUNT forms under RC/RR on three index designs. Assignment, stored function/trigger, DML/CTAS, SERIALIZABLE and locking-read probes excluded; see bench/nonlocking-scope.json."""
import threading, time
import pymysql
from lablib import connect, query
from lock_factors import DESIGNS, ISOLATIONS, build, try_write
from reqlib import Results, builder, guard, sql_try

ID = "40-EXP-R11"
guard(ID, 9)
r = Results(ID, ["EXP-R11", "EXP-5", "FIND-LOCK-FORMS-01"])
DB = "bench_lock"
SETUP = ["DROP PROCEDURE IF EXISTS p_cnt", "CREATE PROCEDURE p_cnt() SELECT COUNT(*) FROM lk"]
FORMS = {
    "derived_table": ["SELECT c FROM (SELECT COUNT(*) AS c FROM lk) x"],
    "cte": ["WITH x AS (SELECT COUNT(*) AS c FROM lk) SELECT c FROM x"],
    "scalar_subquery_in_select": ["SELECT (SELECT COUNT(*) FROM lk)"],
    "explain_analyze": ["EXPLAIN ANALYZE SELECT COUNT(*) FROM lk"],
    "prepared": ["PREPARE st FROM 'SELECT COUNT(*) FROM lk'", "EXECUTE st", "DEALLOCATE PREPARE st"],
    "call_plain": ["CALL p_cnt()"],
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


query(connect(), f"DROP DATABASE IF EXISTS {DB}")
r.finish()
