#!/usr/bin/env python3
"""Answers EXP-S6-REPLICA-FAILURE-CLEANUP (FIND-S6-REPLICA-CLEANUP-01). The local compose replica only.

The count fails three ways after the applier has been stopped: its limit expires, KILL QUERY, and its connection is cut. Each time the owner must restart the applier it stopped, and the replica must catch up.
The control starts with the applier already stopped by someone else, and it must stay stopped."""
import threading, time
from countlib import replica_point_count
from lablib import REPLICA, connect, query
from reqlib import Results, guard, sql_try
import pymysql

ID = "3i-EXP-S6-REPLICA-FAILURE-CLEANUP"
guard(ID, 3)
r = Results(ID, ["EXP-S6-REPLICA-FAILURE-CLEANUP", "FIND-S6-REPLICA-CLEANUP-01"])
admin = connect(REPLICA, read_timeout=120)
primary = connect(read_timeout=120)


def status():
    with admin.cursor(pymysql.cursors.DictCursor) as dc:
        dc.execute("SHOW REPLICA STATUS")
        row = dc.fetchone() or {}
    return {k: row.get(k) for k in ("Replica_SQL_Running", "Replica_IO_Running", "Seconds_Behind_Source", "Last_SQL_Errno")}


def caught_up():
    target = query(primary, "SELECT @@GLOBAL.gtid_executed")[0][0]
    t = time.monotonic()
    return {"wait_result": query(admin, "SELECT WAIT_FOR_EXECUTED_GTID_SET(%s, 60)", (target,))[0][0], "seconds": round(time.monotonic() - t, 2)}


def trial(max_ms, hook_factory=None):
    c = connect(REPLICA, read_timeout=120)
    hook = hook_factory(c) if hook_factory else None
    res = replica_point_count(c, "t_repl", max_execution_ms=max_ms, kill_hook=hook)
    res.pop("gtid_at_stop", None)
    time.sleep(.3)
    res["after"] = status()
    res["catch_up"] = caught_up()
    return res


def kill_query_after(ms):
    def factory(c):
        def hook():
            k = connect(REPLICA, read_timeout=10)
            threading.Timer(ms / 1000, lambda: query(k, f"KILL QUERY {c.thread_id()}")).start()
        return hook
    return factory


def kill_connection_after(ms):
    def factory(c):
        def hook():
            k = connect(REPLICA, read_timeout=10)
            threading.Timer(ms / 1000, lambda: query(k, f"KILL {c.thread_id()}")).start()
        return hook
    return factory


try:
    with r.section("failures") as d:
        d["start"] = status()
        d["limit_expires"] = trial(200)
        r.save()
        d["kill_query"] = trial(60_000, kill_query_after(300))
        r.save()
        d["connection_cut"] = trial(60_000, kill_connection_after(300))
        r.save()
    with r.section("control_applier_already_stopped") as d:
        query(admin, "STOP REPLICA SQL_THREAD")
        c = connect(REPLICA, read_timeout=120)
        res = replica_point_count(c, "t_repl", max_execution_ms=200)
        res.pop("gtid_at_stop", None)
        d["run"] = res
        d["after"] = status()
        d["left_stopped"] = d["after"]["Replica_SQL_Running"] == "No"
finally:
    sql_try(admin, "START REPLICA SQL_THREAD")
    r.data["final"] = status()
    r.data["final_catch_up"] = caught_up()
r.finish()
