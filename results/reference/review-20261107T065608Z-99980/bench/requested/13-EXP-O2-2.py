#!/usr/bin/env python3
"""Answers EXP-O2-2 (telling a timeout from a third-party KILL, FIND-R2-KILL-01), EXP-SOL-8 (where the hint applies) and FIND-STEP-01's stop paths.

Every statement reads t_dense. t_dense gets one INSTANT column in the pending-DDL section, removed again at the end.
Three classifiers are compared on the same trials: `elapsed` (old: at or past the limit means timeout), `counter`
(Max_execution_time_exceeded rose by one in this session) and `stop_always` (any 1317 stops).
"""
import threading, time
import pymysql
from countlib import CountMonitor, session_setup, split_count, split_plans
from lablib import PRIMARY, connect, query
from reqlib import Results, builder, guard, reset_schema, session_status, sql_try

ID = "13-EXP-O2-2"
guard(ID, 8)
r = Results(ID, ["EXP-O2-2", "EXP-SOL-8", "FIND-R2-KILL-01", "FIND-STEP-01", "FIND-SOL-TIMEOUT-01"])
LONG = "COUNT(*) FROM t_dense FORCE INDEX (`k_created`)"
counter_name = "Max_execution_time_exceeded"


def counter(c):
    # A KILL QUERY that arrives after the statement ended can hit this status query instead, so it gets a second try.
    for attempt in (1, 2):
        try:
            return session_status(c, counter_name).get(counter_name, 0)
        except pymysql.MySQLError:
            if attempt == 2:
                raise
            time.sleep(.05)


def trial(limit_ms, kill_at_ms=None, process_delay_ms=0, sql=None, setup=()):
    """One statement. Records when KILL was sent, when the client saw the error, and when the client handled it."""
    c = connect(read_timeout=120)
    for s in setup:
        query(c, s)
    h = f"/*+ MAX_EXECUTION_TIME({limit_ms}) */ " if limit_ms else ""
    before = counter(c)
    killed = {}
    t0 = time.monotonic()

    def killer():
        time.sleep(kill_at_ms / 1000)
        k = connect(read_timeout=10)
        killed["sent_ms"] = round((time.monotonic() - t0) * 1000, 1)
        killed["result"] = sql_try(k, f"KILL QUERY {c.thread_id()}")["ok"]
        k.close()
    if kill_at_ms is not None:
        threading.Thread(target=killer, daemon=True).start()
    res = {"limit_ms": limit_ms, "kill_at_ms": kill_at_ms}
    try:
        res["value"] = query(c, sql or f"SELECT {h}{LONG}")[0][0]
        res["errno"] = None
    except pymysql.MySQLError as e:
        res["errno"] = e.args[0]
    res["server_return_ms"] = round((time.monotonic() - t0) * 1000, 1)
    if process_delay_ms:  # a client that is rescheduled before it handles the exception
        time.sleep(process_delay_ms / 1000)
    res["handled_ms"] = round((time.monotonic() - t0) * 1000, 1)
    res["kill_sent_ms"] = killed.get("sent_ms")
    res["counter_delta"] = counter(c) - before
    time.sleep(.05)
    if kill_at_ms is not None and killed.get("sent_ms") is None:
        time.sleep(.5)
        res["kill_sent_ms"] = killed.get("sent_ms")
    # What each classifier would conclude: "timeout" lets the job go on; anything else stops it.
    truth_kill = res["kill_sent_ms"] is not None and res["errno"] is not None and limit_ms and res["kill_sent_ms"] < limit_ms
    res["killed_first"] = bool(truth_kill)
    if res["errno"] in (1317, 3024):
        res["classifier"] = {"elapsed": "timeout" if limit_ms and res["handled_ms"] >= limit_ms else "stop",
                             "counter": "timeout" if res["counter_delta"] == 1 else "stop", "stop_always": "stop"}
    c.close()
    return res


with r.section("timer_only") as d:
    for name, limit, setup in (("parallel_default_1000", 1000, ()), ("single_thread_1000", 1000, ("SET SESSION innodb_parallel_read_threads=1",)), ("500", 500, ())):
        d[name] = [trial(limit, setup=setup) for _ in range(5)]
    # The default plan may use the parallel reader, which is where the existing 1317 came from.
    d["default_plan_1000"] = [trial(1000, sql="SELECT /*+ MAX_EXECUTION_TIME(1000) */ COUNT(*) FROM t_dense") for _ in range(5)]
    d["step_shape_500"] = [trial(500, sql="SELECT /*+ MAX_EXECUTION_TIME(500) */ created_at, id FROM t_dense FORCE INDEX (`k_created`) WHERE created_at IS NOT NULL ORDER BY created_at, id LIMIT 1 OFFSET 90000000") for _ in range(5)]

with r.section("third_party_kill") as d:
    d["kill_early_limit_5000"] = [trial(5000, kill_at_ms=1000) for _ in range(5)]
    d["kill_no_hint"] = [trial(0, kill_at_ms=1000) for _ in range(5)]
    for at in (900, 990, 1000, 1010, 1100):
        d[f"limit_1000_kill_at_{at}"] = [trial(1000, kill_at_ms=at) for _ in range(5)]
        r.save()
    # The counterexample in FIND-R2-KILL-01: KILL long before the limit, handled by the client after the limit.
    d["kill_at_300_handled_after_limit_1000"] = [trial(1000, kill_at_ms=300, process_delay_ms=1200) for _ in range(5)]

with r.section("summary") as d:
    sec = r.data["sections"]
    rows = [t for s in ("timer_only", "third_party_kill") for k, v in sec[s].items() if isinstance(v, list) for t in v if t.get("errno") in (1317, 3024)]
    d["errno_counts"] = {str(n): sum(1 for t in rows if t["errno"] == n) for n in (1317, 3024)}
    killed = [t for t in rows if t["killed_first"]]
    timer = [t for v in sec["timer_only"].values() if isinstance(v, list) for t in v if t.get("errno") in (1317, 3024)]
    d["killed_first_trials"] = len(killed)
    d["killed_first_called_timeout"] = {name: sum(1 for t in killed if t["classifier"][name] == "timeout") for name in ("elapsed", "counter", "stop_always")}
    d["timer_trials"] = len(timer)
    d["timer_called_timeout"] = {name: sum(1 for t in timer if t["classifier"][name] == "timeout") for name in ("elapsed", "counter", "stop_always")}

with r.section("stop_flag_with_pending_ddl") as d:
    # Monitor sees a pending ALTER, raises the flag, sends KILL QUERY. Nothing may be issued after the flag.
    admin = connect(read_timeout=120)
    has_col = lambda: query(admin, "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='t_dense' AND COLUMN_NAME='mdl_probe'")[0][0]
    try:
        c = connect(read_timeout=120)
        session_setup(c)
        plan = next(p for p in split_plans(c, "bench", "t_dense")["plans"] if p["index"] == "k_created")
        stop = threading.Event()
        mon = CountMonitor(PRIMARY, "bench", "t_dense", c.thread_id(), stop, interval=.05)
        mon.start()
        issued_after_flag = []

        def before(kind, sql, args):
            if stop.is_set():
                issued_after_flag.append(kind)

        ddl_res = {}

        def ddl():
            x = connect(read_timeout=60)
            query(x, "SET SESSION lock_wait_timeout=20")
            ddl_res.update(sql_try(x, "ALTER TABLE t_dense ADD COLUMN mdl_probe INT NULL, ALGORITHM=INSTANT"))
            x.close()
        threading.Timer(1.5, ddl).start()
        s = split_count(c, "t_dense", plan, width=1_000_000, limit_ms=5000, duty=.5, stop=stop, before_statement=before)
        mon.finish()
        time.sleep(2)
        d["run"] = {k: s.get(k) for k in ("status", "steps", "errors", "partial_rows", "statements")}
        d.update(monitor_reason=mon.reason, kill_result=mon.kill_result, statements_issued_after_flag=issued_after_flag, ddl=ddl_res, counted_as_result=s["rows"])
        c.close()
    finally:
        if has_col():
            query(admin, "ALTER TABLE t_dense DROP COLUMN mdl_probe")

with r.section("other_stop_paths") as d:
    # 1205 from a pending DDL behind an open transaction, on a private table.
    b = builder()
    reset_schema(b, "req_stop")
    query(b, "CREATE TABLE req_stop.t (id BIGINT PRIMARY KEY, v INT NOT NULL)")
    query(b, "INSERT INTO req_stop.t SELECT n, n FROM bench.seq1m WHERE n<=100000")
    c = connect(read_timeout=60, db="req_stop")
    session_setup(c)
    plan = split_plans(c, "req_stop", "t")["plans"][0]  # before the pending DDL, so only the walk meets it
    hold = connect(read_timeout=60, db="req_stop", autocommit=False)
    query(hold, "START TRANSACTION")
    query(hold, "SELECT * FROM t WHERE id=1")
    ddl = connect(read_timeout=60, db="req_stop")
    query(ddl, "SET SESSION lock_wait_timeout=6")
    th = threading.Thread(target=lambda: sql_try(ddl, "ALTER TABLE t ADD COLUMN p INT NULL, ALGORITHM=INSTANT"), daemon=True)
    th.start()
    time.sleep(.5)
    s = split_count(c, "t", plan, width=1000, limit_ms=500, duty=None)
    d["pending_ddl_1205"] = {"status": s["status"], "errors": s.get("errors"), "rows": s["rows"]}
    query(hold, "ROLLBACK")
    th.join()
    hold.close(); ddl.close(); c.close()
    # Connection lost: the job's connection is killed mid-walk.
    c = connect(read_timeout=60, db="req_stop")
    session_setup(c)

    def kill_conn_at(n):
        seen = [0]

        def hook(kind, sql, args):
            seen[0] += 1
            if seen[0] == n:
                k = connect(read_timeout=10)
                query(k, f"KILL {c.thread_id()}")
                k.close()
                time.sleep(.05)
        return hook
    s = split_count(c, "t", plan, width=1000, limit_ms=500, duty=None, before_statement=kill_conn_at(5))
    d["connection_lost"] = {"status": s["status"], "errors": s.get("errors"), "rows": s["rows"]}
    # Monitor lost: its connection is killed, so the job has to stop.
    c = connect(read_timeout=60, db="req_stop")
    session_setup(c)
    stop = threading.Event()
    mon = CountMonitor(PRIMARY, "req_stop", "t", c.thread_id(), stop, interval=.02)
    mon.start()
    time.sleep(.3)

    def kill_monitor():
        k = connect(read_timeout=10)
        query(k, f"KILL {mon.conn.thread_id()}")
        k.close()
    threading.Timer(.1, kill_monitor).start()
    s = split_count(c, "t", plan, width=100, limit_ms=500, duty=None, pause_ms=20, stop=stop)
    mon.finish()
    d["monitor_lost"] = {"status": s["status"], "monitor_reason": mon.reason, "rows": s["rows"]}
    query(b, "DROP DATABASE req_stop")

with r.section("hint_context_SOL_8") as d:
    # Direct, CALL and event. The statement would run about 20 s if nothing stops it, so an owner connection ends it at 5 s.
    b = builder()
    reset_schema(b, "req_ctx")
    query(b, "CREATE PROCEDURE req_ctx.p_hint() SELECT /*+ MAX_EXECUTION_TIME(1000) */ COUNT(*) FROM bench.t_dense FORCE INDEX (`k_created`)")
    query(b, "CREATE PROCEDURE req_ctx.p_plain() SELECT COUNT(*) FROM bench.t_dense FORCE INDEX (`k_created`)")
    query(b, "CREATE TABLE req_ctx.log (name VARCHAR(20), started DATETIME(3), finished DATETIME(3), c BIGINT)")

    def ctx(sql, session=0):
        c = connect(read_timeout=60)
        query(c, f"SET SESSION max_execution_time={session}")
        before = counter(c)
        killed = []
        tm = threading.Timer(5, lambda: (killed.append(True), query(connect(read_timeout=10), f"KILL QUERY {c.thread_id()}")))
        tm.start()
        t = time.monotonic()
        res = sql_try(c, sql)
        res["ms"] = round((time.monotonic() - t) * 1000)
        tm.cancel()
        res.update(owner_killed_it=bool(killed), counter_delta=counter(c) - before)
        res.pop("rows", None)
        c.close()
        return res
    d["direct_hint"] = ctx("SELECT /*+ MAX_EXECUTION_TIME(1000) */ COUNT(*) FROM t_dense FORCE INDEX (`k_created`)")
    d["call_hint"] = ctx("CALL req_ctx.p_hint()")
    d["call_session_1000"] = ctx("CALL req_ctx.p_plain()", 1000)
    d["event_scheduler"] = query(b, "SELECT @@event_scheduler")[0][0]
    query(b, """CREATE EVENT req_ctx.e_hint ON SCHEDULE AT CURRENT_TIMESTAMP DO
                BEGIN
                  DECLARE c BIGINT;
                  INSERT INTO req_ctx.log VALUES ('start', NOW(3), NULL, NULL);
                  SELECT /*+ MAX_EXECUTION_TIME(1000) */ COUNT(*) INTO c FROM bench.t_dense FORCE INDEX (`k_created`);
                  INSERT INTO req_ctx.log VALUES ('end', NULL, NOW(3), c);
                END""")
    time.sleep(6)
    d["event_log_at_6s"] = [[str(x) for x in row] for row in query(b, "SELECT * FROM req_ctx.log")]
    d["event_thread"] = [[str(x) for x in row] for row in query(b, "SELECT PROCESSLIST_ID, PROCESSLIST_TIME, LEFT(PROCESSLIST_INFO,80) FROM performance_schema.threads WHERE PROCESSLIST_INFO LIKE '%MAX_EXECUTION_TIME(1000)%' AND PROCESSLIST_ID<>CONNECTION_ID()")]
    for pid, _t, _i in query(b, "SELECT PROCESSLIST_ID, PROCESSLIST_TIME, LEFT(PROCESSLIST_INFO,80) FROM performance_schema.threads WHERE PROCESSLIST_INFO LIKE '%MAX_EXECUTION_TIME(1000)%' AND PROCESSLIST_ID<>CONNECTION_ID()"):
        sql_try(b, f"KILL QUERY {int(pid)}")
    time.sleep(1)
    query(b, "DROP DATABASE req_ctx")
r.finish()
