#!/usr/bin/env python3
"""Answers EXP-O8-1205 (FIND-O8-1205): a statement with a long limit behind a pending ALTER, with SQL 0's lock_wait_timeout of 3 s. Private table."""
import threading, time
from countlib import forced_plan, session_setup, split_count
from lablib import connect, query
from reqlib import Results, builder, guard, reset_schema, session_status, sql_try

ID = "04-EXP-O8-1205"
guard(ID, 2)
r = Results(ID, ["EXP-O8-1205", "FIND-O8-1205"])
b = builder()
reset_schema(b, "req_1205")
query(b, "CREATE TABLE req_1205.t (id BIGINT PRIMARY KEY, v INT NOT NULL)")
query(b, "INSERT INTO req_1205.t SELECT n, n FROM bench.seq1m WHERE n<=100000")


def with_pending(fn):
    hold = connect(db="req_1205", autocommit=False, read_timeout=60)
    query(hold, "START TRANSACTION")
    query(hold, "SELECT * FROM t WHERE id=1")
    ddl = connect(db="req_1205", read_timeout=60)
    query(ddl, "SET SESSION lock_wait_timeout=15")
    th = threading.Thread(target=lambda: sql_try(ddl, "ALTER TABLE t ADD COLUMN p INT NULL, ALGORITHM=INSTANT"), daemon=True)
    th.start()
    time.sleep(.5)
    try:
        return fn()
    finally:
        query(hold, "ROLLBACK")
        th.join(20)
        hold.close(); ddl.close()
        if query(b, "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='req_1205' AND TABLE_NAME='t' AND COLUMN_NAME='p'")[0][0]:
            query(b, "ALTER TABLE req_1205.t DROP COLUMN p")


def statement():
    c = connect(db="req_1205", read_timeout=90)
    session_setup(c)
    before = session_status(c, "Max_execution_time_exceeded").get("Max_execution_time_exceeded", 0)
    res = sql_try(c, "SELECT /*+ MAX_EXECUTION_TIME(60000) */ COUNT(*) FROM t")
    res["timer_counter_delta"] = session_status(c, "Max_execution_time_exceeded").get("Max_execution_time_exceeded", 0) - before
    res.pop("rows", None)
    c.close()
    return res


def through_split():
    c = connect(db="req_1205", read_timeout=90)
    session_setup(c)
    plan = forced_plan(c, "req_1205", "t", "PRIMARY")
    s = split_count(c, "t", plan, width=1000, limit_ms=60_000, duty=None, db="req_1205")
    c.close()
    return {k: s.get(k) for k in ("status", "rows", "errors", "width_reductions")}


with r.section("statement") as d:
    d["runs"] = [with_pending(statement) for _ in range(5)]
with r.section("through_split_count") as d:
    d["runs"] = [with_pending(through_split) for _ in range(5)]
query(b, "DROP DATABASE req_1205")
r.finish()
