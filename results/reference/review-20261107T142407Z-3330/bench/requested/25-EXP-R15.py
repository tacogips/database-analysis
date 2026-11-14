#!/usr/bin/env python3
"""Answers EXP-R15 (FIND-REPL-01): the replica procedure as printed, checked against a log of what the primary committed, and the replica's own metrics.

t_repl is the only replicated table. Rows with ids above 30,000,000 are added during the test and removed at the end, and the
AUTO_INCREMENT counter is put back. mdl_probe is removed too. The applier is always restarted.
"""
import threading, time
import pymysql
from experiments import REPL
from lablib import REPLICA, Workload, connect, query
from reqlib import Results, guard, measured_run, sql_try

ID = "25-EXP-R15"
guard(ID, 14)
r = Results(ID, ["EXP-R15", "FIND-REPL-01"])
p = connect(read_timeout=300)
rc = connect(REPLICA, read_timeout=300)
BASE = 30_000_000


def status():
    with rc.cursor(pymysql.cursors.DictCursor) as dc:
        dc.execute("SHOW REPLICA STATUS")
        row = dc.fetchone() or {}
    return {k: row.get(k) for k in ("Replica_IO_Running", "Replica_SQL_Running", "Seconds_Behind_Source", "Replicate_Do_Table", "Executed_Gtid_Set", "Retrieved_Gtid_Set", "Last_SQL_Errno")}


def cleanup():
    query(p, f"DELETE FROM t_repl WHERE id>{BASE}")
    if query(p, "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='t_repl' AND COLUMN_NAME='mdl_probe'")[0][0]:
        query(p, "ALTER TABLE t_repl DROP COLUMN mdl_probe")
    query(p, "ALTER TABLE t_repl AUTO_INCREMENT=20000001")


try:
    with r.section("setup_facts") as d:
        d["status"] = status()
        d["workers"] = query(rc, "SELECT @@replica_parallel_workers, @@replica_preserve_commit_order, @@read_only")[0]
        d["primary_uuid"] = query(p, "SELECT @@server_uuid")[0][0]
        d["replica_uuid"] = query(rc, "SELECT @@server_uuid")[0][0]
        d["filter_effect"] = {"t_dense_on_replica": sql_try(rc, "SELECT COUNT(*) FROM bench.t_dense LIMIT 1"), "t_hot_on_replica_rows": sql_try(rc, "SELECT COUNT(*) FROM bench.t_hot")}

    with r.section("stop_count_resume") as d:
        # One writer, one row per transaction, recording the primary's GTID set after each commit. Three stops at different moments.
        d["trials"] = []
        for trial in range(3):
            done = threading.Event()
            log = []

            def writer(start):
                w = connect(read_timeout=60)
                i = start
                while not done.is_set():
                    i += 1
                    query(w, "INSERT INTO t_repl (id,v,note) VALUES (%s,0,'r15')", (i,))
                    log.append((i, query(w, "SELECT @@GLOBAL.gtid_executed")[0][0]))
                    time.sleep(.02)
                w.close()
            start = BASE + trial * 100_000
            before = query(p, f"SELECT COUNT(*) FROM t_repl WHERE id>{BASE}")[0][0]
            th = threading.Thread(target=writer, args=(start,), daemon=True)
            th.start()
            time.sleep(3)
            t0 = time.monotonic()
            query(rc, "STOP REPLICA SQL_THREAD")
            stop_ms = round((time.monotonic() - t0) * 1000)
            st = status()
            gtid_at_stop = query(rc, "SELECT @@GLOBAL.gtid_executed")[0][0]
            time.sleep(1)
            t0 = time.monotonic()
            n = query(rc, "SELECT /*+ MAX_EXECUTION_TIME(120000) */ COUNT(*) FROM t_repl")[0][0]
            count_s = round(time.monotonic() - t0, 2)
            gtid_after_count = query(rc, "SELECT @@GLOBAL.gtid_executed")[0][0]
            maxid = query(rc, f"SELECT COALESCE(MAX(id),0) FROM t_repl WHERE id>{start}")[0][0]
            t0 = time.monotonic()
            query(rc, "START REPLICA SQL_THREAD")
            time.sleep(1)
            done.set()
            th.join()
            target = query(p, "SELECT @@GLOBAL.gtid_executed")[0][0]
            waited = query(rc, "SELECT WAIT_FOR_EXECUTED_GTID_SET(%s, 120)", (target,))[0][0]
            catch_up = round(time.monotonic() - t0, 2)
            # The primary's own record: the last write whose whole GTID set is contained in what the replica had executed when it stopped.
            # Set containment, not string equality: the replica's set also holds transactions of its own server UUID.
            subset = [i for i, g in log if query(rc, "SELECT GTID_SUBSET(%s, %s)", (g, gtid_at_stop))[0][0] == 1]
            match = max(subset) if subset else None
            next_ids = [i for i, g in log if i not in subset]
            expected = 20_000_000 + before + (maxid - start if maxid else 0)
            d["trials"].append({"stop_ms": stop_ms, "sql_thread_after_stop": st["Replica_SQL_Running"], "seconds_behind_at_stop": st["Seconds_Behind_Source"], "count": n, "count_seconds": count_s,
                                "expected_from_rows_applied": expected, "count_matches_applied_rows": n == expected, "gtid_stable_during_count": gtid_at_stop == gtid_after_count,
                                "max_id_applied": maxid, "id_primary_logged_at_that_gtid": match, "gtid_maps_to_applied_id": match == maxid if match is not None else None,
                                "contiguous_prefix": subset == [i for i, _g in log][:len(subset)], "first_id_not_contained": min(next_ids) if next_ids else None,
                                "primary_rows_written": len(log), "catch_up_seconds": catch_up, "wait_result": waited})
            r.save()
        cleanup()

    def replica_read(notes, sql="SELECT COUNT(*) FROM t_repl FORCE INDEX (PRIMARY)"):
        c = connect(REPLICA, read_timeout=300)
        query(c, "SET SESSION innodb_parallel_read_threads=1")
        t = time.monotonic()
        notes["rows"] = query(c, sql)[0][0]
        notes["seconds"] = round(time.monotonic() - t, 2)
        c.close()

    with r.section("replica_own_metrics") as d:
        # Reads and metrics on the replica itself; the primary's traffic drives the applier.
        read_on_replica = dict(table="t_repl", max_id=20_000_000, readers=4, writers=0, rate=25, read_sql="SELECT * FROM t_repl WHERE id=%s", host=REPLICA)

        def two_sided(label, method):
            """Primary writes (Workload in a thread of its own) and replica reads, measured on the replica."""
            pw = Workload(**REPL)
            pw.start()
            t0 = time.time()
            try:
                wl = {k: v for k, v in read_on_replica.items() if k != "host"}
                res = measured_run(label, wl, method, 20, 20, host=REPLICA, replica_host=REPLICA)
            finally:
                t1 = time.time()
                pw.finish()
            res["primary_write_whole_run"] = pw.window(t0, t1)["write"]
            res.pop("series_10s", None)
            return res
        d["control"] = two_sided("control", lambda n: time.sleep(20))
        r.save()
        d["count"] = two_sided("count", replica_read)
        r.save()

        d["excluded_count_with_primary_alter"] = "superseded_by_explicit_user_scope"
        r.save()
finally:
    sql_try(rc, "START REPLICA SQL_THREAD")
    cleanup()
    target = query(p, "SELECT @@GLOBAL.gtid_executed")[0][0]
    r.data["final_wait_result"] = query(rc, "SELECT WAIT_FOR_EXECUTED_GTID_SET(%s, 300)", (target,))[0][0]
    r.data["final_status"] = status()
r.finish()
