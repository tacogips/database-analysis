#!/usr/bin/env python3
"""Answers EXP-SOL-1 (an already open transaction, FIND-SOL-TX-01) and EXP-SOL-2 (READ UNCOMMITTED, FIND-SOL-ISO-01). Private schema, dropped at the end."""
import threading, time
from countlib import session_setup, split_count, split_plans
from lablib import connect, query
from reqlib import Results, builder, guard, reset_schema, sql_try

ID = "14-EXP-SOL-1"
guard(ID, 4)
r = Results(ID, ["EXP-SOL-1", "EXP-SOL-2", "FIND-SOL-TX-01", "FIND-SOL-ISO-01"])
b = builder()
reset_schema(b, "req_tx")
query(b, "CREATE TABLE req_tx.t (id BIGINT PRIMARY KEY, v INT NOT NULL)")
query(b, "INSERT INTO req_tx.t SELECT n, n FROM bench.seq1m WHERE n<=100000")
obs = connect(db="req_tx", read_timeout=30)


def observe(job):
    """Between two steps of a job: what does it hold, and do other sessions get through?"""
    mdl = query(obs, """SELECT m.LOCK_TYPE, m.LOCK_STATUS FROM performance_schema.metadata_locks m
                        JOIN performance_schema.threads t ON t.THREAD_ID=m.OWNER_THREAD_ID
                        WHERE t.PROCESSLIST_ID=%s AND m.OBJECT_SCHEMA='req_tx' AND m.OBJECT_NAME='t'""", (job.thread_id(),))
    trx = query(obs, "SELECT trx_rows_locked, trx_isolation_level FROM information_schema.INNODB_TRX WHERE trx_mysql_thread_id=%s", (job.thread_id(),))
    w = connect(db="req_tx", read_timeout=30)
    query(w, "SET SESSION lock_wait_timeout=1")
    query(w, "SET SESSION innodb_lock_wait_timeout=1")
    update = sql_try(w, "UPDATE t SET v=v+1 WHERE id=500")
    alter = sql_try(w, "ALTER TABLE t ADD COLUMN probe INT NULL, ALGORITHM=INSTANT")
    if alter["ok"]:
        query(w, "ALTER TABLE t DROP COLUMN probe")
    w.close()
    return {"job_mdl": [list(x) for x in mdl], "job_trx": [list(x) for x in trx], "update": {"ok": update["ok"], "errno": update.get("errno")}, "alter": {"ok": alter["ok"], "errno": alter.get("errno"), "ms": alter["ms"]}}


def between_steps(job):
    seen = []

    def on_step(_n, _ms, _cursor):
        if len(seen) < 1:
            seen.append(observe(job))
    return seen, on_step


with r.section("paths") as d:
    cases = {
        "fresh_connection": lambda c: None,
        "start_transaction_before_sql0": lambda c: query(c, "START TRANSACTION"),
        "start_transaction_then_read_before_sql0": lambda c: (query(c, "START TRANSACTION"), query(c, "SELECT COUNT(*) FROM t WHERE id<10")),
        "serializable_transaction_before_sql0": lambda c: (query(c, "SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE"), query(c, "START TRANSACTION"), query(c, "SELECT COUNT(*) FROM t WHERE id<10")),
    }
    for name, prepare in cases.items():
        c = connect(db="req_tx", read_timeout=60)
        prepare(c)
        session = session_setup(c)
        plan = split_plans(c, "req_tx", "t")["plans"][0]
        seen, on_step = between_steps(c)
        s = split_count(c, "t", plan, width=1000, limit_ms=500, duty=None, on_step=on_step, max_steps=3)
        d[name] = {"session": {k: session[k] for k in ("autocommit", "isolation")}, "between_steps": seen, "status": s["status"]}
        query(c, "ROLLBACK")
        c.close()
    # A driver that starts with autocommit off: SET SESSION autocommit = 1 commits whatever it had open.
    c = connect(db="req_tx", read_timeout=60, autocommit=False)
    query(c, "SELECT COUNT(*) FROM t WHERE id<10")
    open_before = query(obs, "SELECT COUNT(*) FROM information_schema.INNODB_TRX WHERE trx_mysql_thread_id=%s", (c.thread_id(),))[0][0]
    session_setup(c)
    plan = split_plans(c, "req_tx", "t")["plans"][0]
    seen, on_step = between_steps(c)
    s = split_count(c, "t", plan, width=1000, limit_ms=500, duty=None, on_step=on_step, max_steps=3)
    d["driver_autocommit_off"] = {"open_before_sql0": open_before, "between_steps": seen, "status": s["status"]}
    c.close()
    # A driver that re-disables autocommit after SQL 0, as a pool's connection reset could. This is what the between-steps check must catch.
    c = connect(db="req_tx", read_timeout=60)
    session_setup(c)
    query(c, "SET SESSION autocommit = 0")
    plan = split_plans(c, "req_tx", "t")["plans"][0]
    seen, on_step = between_steps(c)
    s = split_count(c, "t", plan, width=1000, limit_ms=500, duty=None, on_step=on_step, max_steps=3)
    d["autocommit_disabled_after_sql0"] = {"between_steps": seen, "status": s["status"]}
    query(c, "ROLLBACK")
    c.close()

with r.section("SOL_2_read_uncommitted") as d:
    w = connect(db="req_tx", autocommit=False, read_timeout=30)
    query(w, "INSERT INTO t SELECT n+1000000, n FROM bench.seq1m WHERE n<=1000")
    arms = {"read_uncommitted": "SET SESSION TRANSACTION ISOLATION LEVEL READ UNCOMMITTED", "read_committed": "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED", "repeatable_read": "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ"}
    for name, sql in arms.items():
        c = connect(db="req_tx", read_timeout=30)
        query(c, sql)
        d[name] = query(c, "SELECT COUNT(*) FROM t")[0][0]
        c.close()
    # A session that inherited READ UNCOMMITTED, then ran SQL 0.
    c = connect(db="req_tx", read_timeout=30)
    query(c, "SET SESSION TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
    d["inherited_ru_before_sql0"] = query(c, "SELECT COUNT(*) FROM t")[0][0]
    session_setup(c)
    d["after_sql0"] = query(c, "SELECT COUNT(*) FROM t")[0][0]
    c.close()
    query(w, "ROLLBACK")
    w.close()
    c = connect(db="req_tx", read_timeout=30)
    d["after_rollback"] = query(c, "SELECT COUNT(*) FROM t")[0][0]
    c.close()

query(b, "DROP DATABASE req_tx")
r.finish()
