#!/usr/bin/env python3
"""Answers EXP-R13 (FIND-PRIV-01): the permission table in §8, run by real accounts with stepwise privileges. Users are not written to the binlog and are dropped at the end."""
import threading
import pymysql
from countlib import CountMonitor, session_setup, split_count, split_plans
from lablib import PRIMARY, REPLICA, connect, query
from reqlib import Results, guard, sql_try

ID = "44-EXP-R13"
guard(ID, 2)
r = Results(ID, ["EXP-R13", "FIND-PRIV-01"])
USERS = {"req_count": ["GRANT SELECT ON bench.t_composite TO req_count@'%'"],
         "req_stats": ["GRANT SELECT ON bench.t_composite TO req_stats@'%'", "GRANT SELECT ON mysql.innodb_index_stats TO req_stats@'%'"],
         "req_monitor": ["GRANT PROCESS ON *.* TO req_monitor@'%'", "GRANT SELECT ON performance_schema.* TO req_monitor@'%'"],
         "req_killer": ["GRANT CONNECTION_ADMIN ON *.* TO req_killer@'%'"],
         "req_repl": ["GRANT REPLICATION CLIENT ON *.* TO req_repl@'%'"],
         "req_replstop": ["GRANT REPLICATION_SLAVE_ADMIN ON *.* TO req_replstop@'%'"]}


def admin_each(host, fn):
    """Accounts with table-level grants exist only on the primary: those tables are not on the replica."""
    c = connect(host, read_timeout=30)
    query(c, "SET SESSION sql_log_bin=0")
    for u in USERS if host == "primary" else ("req_repl", "req_replstop"):
        fn(c, u)
    c.close()


def create(c, u):
    query(c, f"DROP USER IF EXISTS {u}@'%'")
    query(c, f"CREATE USER {u}@'%' IDENTIFIED BY 'lab'")
    for g in USERS[u]:
        query(c, g)


def login(user, host="primary", db="bench"):
    return pymysql.connect(host=host, user=user, password="lab", database=db, autocommit=True, read_timeout=30)


def same_user_target():
    """An idle connection of an account without SYSTEM_USER. Kept referenced so it stays open for the KILL."""
    conn = login("req_count")
    TARGETS.append(conn)
    return conn


TARGETS = []


STATEMENTS = {
    "sql0": "SET SESSION max_execution_time = 1000",
    "count": "SELECT /*+ MAX_EXECUTION_TIME(1000) */ COUNT(*) FROM t_composite",
    "sql3_statistics": "SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='t_composite'",
    "sql3_pages": "SELECT index_name, stat_value FROM mysql.innodb_index_stats WHERE database_name='bench' AND table_name='t_composite' AND stat_name='size'",
    "innodb_trx": "SELECT COUNT(*) FROM information_schema.INNODB_TRX",
    "innodb_metrics": "SELECT `count` FROM information_schema.INNODB_METRICS WHERE name='trx_rseg_history_len'",
    "innodb_tables": "SELECT COUNT(*) FROM information_schema.INNODB_TABLES WHERE NAME='bench/t_composite'",
    "metadata_locks": "SELECT COUNT(*) FROM performance_schema.metadata_locks",
    "threads": "SELECT COUNT(*) FROM performance_schema.threads WHERE PROCESSLIST_STATE LIKE 'Waiting for table%'",
    "setup_instruments": "SELECT ENABLED FROM performance_schema.setup_instruments WHERE NAME='wait/lock/metadata/sql/mdl'",
    "global_status": "SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='Threads_running'",
    "show_session_status": "SHOW SESSION STATUS LIKE 'Handler_read_next'",
    "table_rows": "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='t_composite'",
    "explain": "EXPLAIN SELECT COUNT(*) FROM t_composite",
    "routines": "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_DEFINITION LIKE '%COUNT(%'",
    "show_replica_status": "SHOW REPLICA STATUS",
}


def keep(res):
    return {"ok": res["ok"], "errno": res.get("errno"), "rows_returned": len(res.get("rows") or []), "first": (res.get("rows") or [[None]])[0][0] if res["ok"] and res.get("rows") else None}


try:
    admin_each("primary", create)
    with r.section("matrix") as d:
        for u in USERS:
            db = "bench" if u in ("req_count", "req_stats") else None
            c = login(u, db=db)
            if db is None:
                d[u] = {k: keep(sql_try(c, s.replace("t_composite", "bench.t_composite") if " FROM t_composite" in s else s)) for k, s in STATEMENTS.items()}
            else:
                d[u] = {k: keep(sql_try(c, s)) for k, s in STATEMENTS.items()}
            c.close()

    with r.section("count_user_runs_the_procedure") as d:
        c = login("req_count")
        d["session"] = {k: str(v) for k, v in session_setup(c).items()}
        plans = split_plans(c, "bench", "t_composite")
        d["plans"] = plans["plans"]
        d["pages_available"] = bool(plans["pages"])
        plan = plans["plans"][0]
        s = split_count(c, "t_composite", plan, width=10_000, limit_ms=500, duty=None, max_steps=20)
        d["run"] = {k: s.get(k) for k in ("status", "steps", "errors", "plan_check")}
        c.close()

    with r.section("kill") as d:
        victim_user = login("req_count")
        victim = connect(read_timeout=30)
        for u in ("req_count", "req_killer", "req_monitor"):
            c = login(u, db="bench" if u == "req_count" else None)
            d[u + "_kills_other_user"] = keep(sql_try(c, f"KILL QUERY {victim.thread_id()}"))
            c.close()
        same = login("req_count")
        d["req_count_kills_own_other_connection"] = keep(sql_try(same, f"KILL QUERY {victim_user.thread_id()}"))
        # root holds SYSTEM_USER, which CONNECTION_ADMIN alone cannot affect (documented). The target here has neither.
        k2 = login("req_killer", db=None)
        d["req_killer_kills_non_system_user"] = keep(sql_try(k2, f"KILL QUERY {same_user_target().thread_id()}"))
        k2.close()
        same.close(); victim_user.close(); victim.close()

    with r.section("monitor_lost_privilege") as d:
        # The monitor account loses PROCESS while running: the job has to stop, through the monitor itself.
        a = connect(read_timeout=30)
        query(a, "SET SESSION sql_log_bin=0")
        query(a, "GRANT CONNECTION_ADMIN ON *.* TO req_monitor@'%'")
        c = login("req_monitor", db=None)
        d["before"] = keep(sql_try(c, "SELECT COUNT(*) FROM information_schema.INNODB_TRX"))
        job = login("req_count")
        session_setup(job)
        plan = split_plans(job, "bench", "t_composite")["plans"][0]
        stop = threading.Event()
        mon = CountMonitor(PRIMARY, "bench", "t_composite", job.thread_id(), stop, interval=.02, connect_fn=lambda: login("req_monitor", db=None))
        mon.start()
        mon.ready.wait(10)
        seen = [0]

        def revoke_on_twentieth(_k, _s, _a):
            seen[0] += 1
            if seen[0] == 20:
                query(a, "REVOKE PROCESS ON *.* FROM req_monitor@'%'")
        # The ceiling matters: global privilege changes may reach only new connections (documented), in which case the monitor never notices.
        run = split_count(job, "t_composite", plan, width=1000, limit_ms=500, duty=None, pause_ms=3, stop=stop, before_statement=revoke_on_twentieth, pending_check=lambda: 0, max_steps=1500)
        mon.finish()
        d["run"] = {"status": run["status"], "rows": run["rows"], "steps": run["steps"], "monitor_reason": mon.reason, "kill_result": mon.kill_result}
        d["same_connection_after_revoke"] = keep(sql_try(c, "SELECT COUNT(*) FROM information_schema.INNODB_TRX"))
        c.close()
        c = login("req_monitor", db=None)
        d["new_connection_after_revoke"] = keep(sql_try(c, "SELECT COUNT(*) FROM information_schema.INNODB_TRX"))
        c.close()
        job.close()
        a.close()

    with r.section("replica") as d:
        admin_each(REPLICA, create)
        c = login("req_repl", host=REPLICA, db=None)
        d["req_repl_status"] = keep(sql_try(c, "SHOW REPLICA STATUS"))
        d["req_repl_stop_denied"] = keep(sql_try(c, "STOP REPLICA SQL_THREAD"))
        c.close()
        c = login("req_replstop", host=REPLICA, db=None)
        d["req_replstop_status"] = keep(sql_try(c, "SHOW REPLICA STATUS"))
        d["req_replstop_stop"] = keep(sql_try(c, "STOP REPLICA SQL_THREAD"))
        d["req_replstop_start"] = keep(sql_try(c, "START REPLICA SQL_THREAD"))
        c.close()
finally:
    sql_try(connect(REPLICA, read_timeout=30), "START REPLICA SQL_THREAD")
    admin_each("primary", lambda c, u: query(c, f"DROP USER IF EXISTS {u}@'%'"))
    try:
        admin_each(REPLICA, lambda c, u: query(c, f"DROP USER IF EXISTS {u}@'%'"))
    except Exception as e:
        r.data["replica_cleanup_error"] = repr(e)[:200]
r.finish()
