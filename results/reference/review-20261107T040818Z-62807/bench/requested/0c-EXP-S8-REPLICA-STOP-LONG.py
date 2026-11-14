#!/usr/bin/env python3
"""Answers EXP-S8-REPLICA-STOP-FAILURE as reopened (FIND-S8-REPLICA-STOP-FAILURE-01). The local compose replica only.

A failed STOP stays pending for longer than the function watches. The earlier restore is reproduced by hand: it should report success with the applier still running, and then the applier stops.
The current one must report 'not confirmed', and the same call made again by whoever holds that result must finish the job. The row, the timeout and the applier are restored in finally."""
import threading, time
from countlib import replica_channels, replica_point_count, restore_applier
from lablib import REPLICA, connect, query
from reqlib import Results, guard, sql_try

ID = "0c-EXP-S8-REPLICA-STOP-LONG"
guard(ID, 8)
r = Results(ID, ["EXP-S8-REPLICA-STOP-FAILURE", "FIND-S8-REPLICA-STOP-FAILURE-01"])
p = connect(read_timeout=60)
rc = connect(REPLICA, read_timeout=120)
RID = 50_000_002
HOLD_SECONDS = 38  # the applier's own lock wait limit is 50 s, so it waits rather than failing
original_timeout = query(rc, "SELECT @@GLOBAL.rpl_stop_replica_timeout")[0][0]


def running():
    rows = replica_channels(rc)
    return rows[0].get("Replica_SQL_Running") if rows else None


def wait_caught_up(seconds=90):
    target = query(p, "SELECT @@GLOBAL.gtid_executed")[0][0]
    return query(rc, "SELECT WAIT_FOR_EXECUTED_GTID_SET(%s, %s)", (target, seconds))[0][0]


def block_applier():
    """A session on the replica holds the row, and the primary's update makes the applier wait on it. Waits until that is visibly so."""
    h = connect(REPLICA, autocommit=False, read_timeout=120)
    query(h, "START TRANSACTION")
    query(h, "SELECT v FROM t_repl WHERE id=%s FOR UPDATE", (RID,))
    query(p, "UPDATE t_repl SET v=v+1 WHERE id=%s", (RID,))
    t0 = time.monotonic()
    waiting = 0
    while time.monotonic() - t0 < 10 and not waiting:
        waiting = query(rc, "SELECT COUNT(*) FROM information_schema.INNODB_TRX WHERE trx_state='LOCK WAIT'")[0][0]
        time.sleep(.1)
    return h, waiting


def release_later(h, seconds):
    return threading.Timer(seconds, lambda: query(h, "ROLLBACK"))


def legacy_restore(host, grace_seconds=20.0, poll=.25):
    """The earlier restore_applier: 'running at the end of the grace' counted as restored."""
    t0, starts = time.monotonic(), 0
    own = connect(host, read_timeout=30)
    while True:
        state = replica_channels(own)[0].get("Replica_SQL_Running")
        if state != "Yes":
            query(own, "START REPLICA SQL_THREAD FOR CHANNEL ''")
            starts += 1
        elif time.monotonic() - t0 >= grace_seconds:
            own.close()
            return {"restored": True, "starts_issued": starts, "seconds": round(time.monotonic() - t0, 2)}
        time.sleep(poll)


def failing_stop():
    c = connect(REPLICA, read_timeout=30)
    t = time.monotonic()
    res = sql_try(c, "STOP REPLICA SQL_THREAD FOR CHANNEL ''")
    c.close()
    return {"ok": res["ok"], "errno": res.get("errno"), "ms": round((time.monotonic() - t) * 1000)}


def watch_until(deadline_s):
    out, t0 = [], time.monotonic()
    while time.monotonic() - t0 < deadline_s:
        out.append([round(time.monotonic() - t0, 1), running()])
        if out[-1][1] != "Yes":
            break
        time.sleep(.5)
    return out


try:
    query(p, "INSERT INTO t_repl (id,v,note) VALUES (%s,0,'s8')", (RID,))
    r.data["caught_up_before"] = wait_caught_up()
    query(rc, "SET GLOBAL rpl_stop_replica_timeout=2")
    r.data["applier_lock_wait_global_s"] = query(rc, "SELECT @@GLOBAL.innodb_lock_wait_timeout")[0][0]

    with r.section("earlier_restore") as d:
        h, d["applier_waiting"] = block_applier()
        timer = release_later(h, HOLD_SECONDS)
        timer.start()
        d["stop"] = failing_stop()
        d["returned"] = legacy_restore(REPLICA)
        d["state_when_it_returned"] = running()
        d["timeline_after_return"] = watch_until(30)
        d["stopped_after_reporting_restored"] = running() != "Yes"
        timer.join(40)
        sql_try(rc, "START REPLICA SQL_THREAD FOR CHANNEL ''")
        d["caught_up"] = wait_caught_up()

    with r.section("current_short_deadline_then_handover") as d:
        d["runs"] = []
        for trial in range(2):
            run = {}
            h, run["applier_waiting"] = block_applier()
            timer = release_later(h, HOLD_SECONDS)
            timer.start()
            c = connect(REPLICA, read_timeout=60)
            res = replica_point_count(c, "t_repl", stop_failure_grace=15)
            res.pop("gtid_at_stop", None)
            run["first_call"] = res
            run["state_when_it_returned"] = running()
            c.close()
            # The caller was told 'not confirmed', and keeps watching. This is the handover the guideline asks for.
            run["second_call"] = restore_applier(REPLICA, stop_failed=True, deadline_seconds=60)
            timer.join(40)
            run["state_at_end"] = running()
            run["caught_up"] = wait_caught_up()
            d["runs"].append(run)
            r.save()

    with r.section("current_long_deadline") as d:
        d["runs"] = []
        for trial in range(2):
            run = {}
            h, run["applier_waiting"] = block_applier()
            timer = release_later(h, HOLD_SECONDS)
            timer.start()
            c = connect(REPLICA, read_timeout=60)
            res = replica_point_count(c, "t_repl", stop_failure_grace=60)
            res.pop("gtid_at_stop", None)
            run["call"] = res
            run["two_seconds_later"] = (time.sleep(2), running())[1]
            timer.join(40)
            run["caught_up"] = wait_caught_up()
            c.close()
            d["runs"].append(run)
            r.save()

    with r.section("already_stopped_control") as d:
        query(rc, "STOP REPLICA SQL_THREAD FOR CHANNEL ''")
        c = connect(REPLICA, read_timeout=60)
        res = replica_point_count(c, "t_repl", max_execution_ms=200)
        res.pop("gtid_at_stop", None)
        d["call"] = res
        d["left_stopped"] = running() == "No"
        c.close()
        query(rc, "START REPLICA SQL_THREAD FOR CHANNEL ''")
finally:
    sql_try(rc, f"SET GLOBAL rpl_stop_replica_timeout={int(original_timeout)}")
    sql_try(rc, "START REPLICA SQL_THREAD FOR CHANNEL ''")
    sql_try(p, "DELETE FROM t_repl WHERE id>=50000000")
    sql_try(p, "ALTER TABLE t_repl AUTO_INCREMENT=20000001")
    r.data["final_running"] = running()
    r.data["final_catch_up"] = wait_caught_up(100)
r.finish()
