#!/usr/bin/env python3
"""Answers EXP-6's remaining arm (FIND-R3-HINT-01), EXP-S6-MONITOR-RECHECK (FIND-S6-MONITOR-RECHECK-01) and EXP-O6-THREADS-SCOPE (FIND-O6-THREADS-FILTER-01).

Instrument changes are restored in a finally block. Tables are private except the read-only long-success statement.
"""
import threading, time
from countlib import CountMonitor, SERVER_WAITS_SQL, forced_plan, session_setup, split_count
from lablib import PRIMARY, connect, query
from reqlib import Results, builder, guard, reset_schema, sql_try

ID = "3b-EXP-6-monitor"
guard(ID, 4)
r = Results(ID, ["EXP-6", "FIND-R3-HINT-01", "EXP-S6-MONITOR-RECHECK", "FIND-S6-MONITOR-RECHECK-01", "EXP-O6-THREADS-SCOPE", "FIND-O6-THREADS-FILTER-01"])
INST = "wait/lock/metadata/sql/mdl"
b = builder()
reset_schema(b, "req_mon2")
query(b, "CREATE TABLE req_mon2.t (id BIGINT PRIMARY KEY, v INT NOT NULL)")
query(b, "CREATE TABLE req_mon2.other (id BIGINT PRIMARY KEY)")
query(b, "INSERT INTO req_mon2.t SELECT n, n FROM bench.seq1m WHERE n<=300000")
query(b, "INSERT INTO req_mon2.other VALUES (1)")


def plan_and_job():
    c = connect(db="req_mon2", read_timeout=60)
    session_setup(c)
    return c, forced_plan(c, "req_mon2", "t", "PRIMARY")


with r.section("long_success_rule") as d:
    # The statement outlasts the printed limit with no limit in force. The KILL timer is cancelled and joined first, so it cannot hit the next statement.
    s = connect(read_timeout=120)
    query(s, "SET SESSION max_execution_time = 0")
    t = time.monotonic()
    value = query(s, "SELECT COUNT(*) FROM order_items FORCE INDEX (`PRIMARY`)")[0][0]
    elapsed = (time.monotonic() - t) * 1000
    d["plain_statement"] = {"limit_printed_ms": 1000, "elapsed_ms": round(elapsed), "value": value, "rule_fires": elapsed > 2000}
    s.close()
    k = connect(read_timeout=10)
    s = connect(read_timeout=120)
    query(s, "SET SESSION max_execution_time = 0")
    timer = threading.Timer(4, lambda: query(k, f"KILL QUERY {s.thread_id()}"))
    timer.start()
    res = sql_try(s, "SELECT COUNT(*) FROM t_dense FORCE INDEX (`k_created`)")
    timer.cancel()
    timer.join(5)
    d["no_limit_killed_at_4s"] = {k2: res.get(k2) for k2 in ("ok", "errno", "ms")}
    d["next_statement_after_join"] = sql_try(s, "SELECT 1")["ok"]
    s.close()
    k.close()

with r.section("full_filter_statements") as d:
    c = connect(read_timeout=20)
    d["printed_filter"] = sql_try(c, SERVER_WAITS_SQL)
    d["job_mdl_statement"] = sql_try(c, """SELECT m.LOCK_TYPE, m.LOCK_STATUS FROM performance_schema.metadata_locks m
JOIN performance_schema.threads t ON t.THREAD_ID = m.OWNER_THREAD_ID
WHERE t.PROCESSLIST_ID = 1 AND m.OBJECT_SCHEMA = 'req_mon2' AND m.OBJECT_NAME = 't'""")
    c.close()

def recheck_arm(recheck):
    """The instrument goes off after SQL M has passed and the monitor is running."""
    a = connect(read_timeout=30)
    original = query(a, "SELECT ENABLED FROM performance_schema.setup_instruments WHERE NAME=%s", (INST,))[0][0]
    job, plan = plan_and_job()
    stop = threading.Event()
    mon = CountMonitor(PRIMARY, "req_mon2", "t", job.thread_id(), stop, interval=.02, recheck_instrument=recheck)
    mon.start()
    mon.ready.wait(10)
    after_flag = []
    seen = [0]
    try:
        def hook(_k, _s, _a):
            seen[0] += 1
            if seen[0] == 20:
                query(a, "UPDATE performance_schema.setup_instruments SET ENABLED='NO' WHERE NAME=%s", (INST,))
                seen.append(time.monotonic())
            if stop.is_set():
                after_flag.append(_k)
        s = split_count(job, "t", plan, width=100, limit_ms=500, duty=None, pause_ms=3, stop=stop, before_statement=hook, db="req_mon2")
    finally:
        query(a, "UPDATE performance_schema.setup_instruments SET ENABLED=%s WHERE NAME=%s", (original, INST))
    mon.finish()
    res = {"recheck": recheck, "status": s["status"], "rows": s["rows"], "steps": s["steps"], "monitor_reason": mon.reason, "statements_after_flag": after_flag,
           "restored": query(a, "SELECT ENABLED FROM performance_schema.setup_instruments WHERE NAME=%s", (INST,))[0][0]}
    a.close()
    job.close()
    return res


with r.section("instrument_lost_after_start") as d:
    d["earlier_monitor_no_recheck"] = recheck_arm(False)
    d["current_monitor"] = [recheck_arm(True) for _ in range(3)]

with r.section("ddl_waiting_on_an_unrelated_table") as d:
    # A reader holds req_mon2.other, and a DDL waits on it. The job walks req_mon2.t and has nothing to do with that wait.
    hold = connect(db="req_mon2", autocommit=False, read_timeout=60)
    query(hold, "START TRANSACTION")
    query(hold, "SELECT * FROM other")
    x = connect(db="req_mon2", read_timeout=60)
    query(x, "SET SESSION lock_wait_timeout=20")
    th = threading.Thread(target=lambda: sql_try(x, "ALTER TABLE other ADD COLUMN p INT NULL, ALGORITHM=INSTANT"), daemon=True)
    th.start()
    time.sleep(.5)
    o = connect(read_timeout=20)
    d["old_filter_would_match"] = query(o, "SELECT COUNT(*) FROM performance_schema.threads WHERE PROCESSLIST_STATE LIKE 'Waiting for table%'")[0][0]
    d["new_filter_matches"] = query(o, SERVER_WAITS_SQL)[0][0]
    job, plan = plan_and_job()
    stop = threading.Event()
    mon = CountMonitor(PRIMARY, "req_mon2", "t", job.thread_id(), stop, interval=.05)
    mon.start()
    s = split_count(job, "t", plan, width=5000, limit_ms=500, duty=None, stop=stop, db="req_mon2")
    mon.finish()
    d["job"] = {"status": s["status"], "rows": s["rows"], "monitor_reason": mon.reason}
    query(hold, "ROLLBACK")
    th.join(25)
    hold.close(); x.close(); job.close(); o.close()

with r.section("flush_still_stops_the_job") as d:
    # The narrower filter must keep catching what metadata_locks does not show.
    c = connect(read_timeout=120)
    res = {}
    th = threading.Thread(target=lambda: res.update(sql_try(c, "SELECT COUNT(*) FROM t_dense FORCE INDEX (`k_created`)")), daemon=True)
    th.start()
    time.sleep(.5)
    f = connect(read_timeout=60)
    query(f, "SET SESSION lock_wait_timeout=20")
    ft = threading.Thread(target=lambda: sql_try(f, "FLUSH NO_WRITE_TO_BINLOG TABLES bench.t_dense"), daemon=True)
    ft.start()
    time.sleep(.5)
    o = connect(read_timeout=20)
    d["filter_sees_flush"] = query(o, SERVER_WAITS_SQL)[0][0]
    job, plan = plan_and_job()
    stop = threading.Event()
    mon = CountMonitor(PRIMARY, "req_mon2", "t", job.thread_id(), stop, interval=.05)
    mon.start()
    s = split_count(job, "t", plan, width=100, limit_ms=500, duty=None, pause_ms=5, stop=stop, db="req_mon2")
    mon.finish()
    d["job"] = {"status": s["status"], "monitor_reason": mon.reason}
    query(o, f"KILL QUERY {c.thread_id()}")
    th.join(20)
    ft.join(20)
    for k in (c, f, o, job):
        k.close()

query(b, "DROP DATABASE req_mon2")
r.finish()
