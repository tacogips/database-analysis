#!/usr/bin/env python3
"""Answers EXP-SOL-6 (FIND-SOL-MONITOR-01): can the monitor tell 'no waits' from 'cannot see'? Turns the MDL instrument off in the lab, restores it, and uses a private table."""
import threading, time
from countlib import CountMonitor, monitor_ready, session_setup, split_count, split_plans
from lablib import PRIMARY, connect, query
from reqlib import Results, builder, guard, reset_schema, sql_try

ID = "45-EXP-SOL-6"
guard(ID, 2)
r = Results(ID, ["EXP-SOL-6", "FIND-SOL-MONITOR-01"])
b = builder()
reset_schema(b, "req_mon")
query(b, "CREATE TABLE req_mon.t (id BIGINT PRIMARY KEY, v INT NOT NULL)")
query(b, "INSERT INTO req_mon.t SELECT n, n FROM bench.seq1m WHERE n<=200000")
INST = "wait/lock/metadata/sql/mdl"


def pending_visible():
    """A real pending DDL behind an open transaction. Returns what metadata_locks shows while it waits."""
    h = connect(db="req_mon", autocommit=False, read_timeout=60)
    query(h, "START TRANSACTION")
    query(h, "SELECT * FROM t WHERE id=1")
    x = connect(db="req_mon", read_timeout=60)
    query(x, "SET SESSION lock_wait_timeout=3")
    res = {}
    th = threading.Thread(target=lambda: res.update(sql_try(x, "ALTER TABLE t ADD COLUMN probe INT NULL, ALGORITHM=INSTANT")), daemon=True)
    th.start()
    time.sleep(1)
    o = connect(read_timeout=20)
    seen = query(o, "SELECT COUNT(*) FROM performance_schema.metadata_locks WHERE OBJECT_SCHEMA='req_mon' AND OBJECT_NAME='t' AND LOCK_STATUS='PENDING'")[0][0]
    state = query(o, "SELECT COUNT(*) FROM performance_schema.threads WHERE PROCESSLIST_STATE='Waiting for table metadata lock'")[0][0]
    query(h, "ROLLBACK")
    th.join(10)
    if res.get("ok"):
        query(x, "ALTER TABLE t DROP COLUMN probe")
    for k in (h, x, o):
        k.close()
    return {"pending_rows_seen": seen, "threads_in_waiting_state": state, "ddl_ok": res.get("ok")}


with r.section("instrument") as d:
    a = connect(read_timeout=30)
    d["original"] = query(a, "SELECT ENABLED FROM performance_schema.setup_instruments WHERE NAME=%s", (INST,))[0][0]
    d["enabled_view"] = pending_visible()
    try:
        d["sqlm_when_enabled"] = monitor_ready(a)
        query(a, "UPDATE performance_schema.setup_instruments SET ENABLED='NO' WHERE NAME=%s", (INST,))
        d["disabled_view"] = pending_visible()
        try:
            monitor_ready(a)
            d["sqlm_when_disabled"] = "passed: the check missed it"
        except RuntimeError as e:
            d["sqlm_when_disabled"] = "refused: " + str(e)[:120]
        # A monitor started when it was fine, while the instrument goes off underneath it.
        c = connect(db="req_mon", read_timeout=60)
        session_setup(c)
        stop = threading.Event()
        mon = CountMonitor(PRIMARY, "req_mon", "t", c.thread_id(), stop, interval=.05)
        mon.start()
        s = split_count(c, "t", split_plans(c, "req_mon", "t")["plans"][0], width=1000, limit_ms=500, duty=None, stop=stop, pause_ms=5)
        mon.finish()
        d["monitor_started_while_disabled"] = {"reason": mon.reason, "status": s["status"]}
        c.close()
    finally:
        query(a, "UPDATE performance_schema.setup_instruments SET ENABLED=%s WHERE NAME=%s", (d["original"], INST))
    d["restored"] = query(a, "SELECT ENABLED FROM performance_schema.setup_instruments WHERE NAME=%s", (INST,))[0][0]

with r.section("empty_list_between_steps_is_not_a_stop") as d:
    c = connect(db="req_mon", read_timeout=60)
    session_setup(c)
    stop = threading.Event()
    mon = CountMonitor(PRIMARY, "req_mon", "t", c.thread_id(), stop, interval=.01)
    mon.start()
    s = split_count(c, "t", split_plans(c, "req_mon", "t")["plans"][0], width=1000, limit_ms=500, duty=.5, stop=stop)
    mon.finish()
    d["run"] = {"status": s["status"], "rows": s["rows"], "steps": s["steps"], "monitor_reason": mon.reason}
    c.close()

query(b, "DROP DATABASE req_mon")
r.finish()
