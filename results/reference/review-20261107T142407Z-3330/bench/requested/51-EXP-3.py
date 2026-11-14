#!/usr/bin/env python3
"""Answers EXP-3: a snapshot older than a wide update, counting for itself, and a count after bulk delete or index churn, against a control.

The earlier result (impact_count_under_history.json) only had another connection holding the old snapshot. Private table of 3M rows."""
import time
from countlib import forced_plan, session_setup, split_count
from lablib import connect, query
from reqlib import Results, builder, global_status, guard, reset_schema

ID = "51-EXP-3"
guard(ID, 7)
r = Results(ID, ["EXP-3", "FIND-SOL-TX-01"])
S = "req_hist"
b = builder()
reset_schema(b, S)
query(b, f"USE {S}")


def fresh():
    query(b, "DROP TABLE IF EXISTS t")
    query(b, "CREATE TABLE t (id BIGINT PRIMARY KEY, k INT NOT NULL, v INT NOT NULL, KEY k_k (k))")
    for off in range(0, 3_000_000, 1_000_000):
        query(b, f"INSERT INTO t SELECT n+{off}, MOD(n,1000), n FROM bench.seq1m")
    query(b, "ANALYZE TABLE t")


def hll():
    return query(b, "SELECT `count` FROM information_schema.INNODB_METRICS WHERE name='trx_rseg_history_len'")[0][0]


def timed(conn, sql):
    before = global_status(b, "Innodb_rows_read", "Innodb_buffer_pool_read_requests", "Innodb_buffer_pool_reads")
    t = time.monotonic()
    value = query(conn, sql)[0][0]
    secs = time.monotonic() - t
    after = global_status(b, "Innodb_rows_read", "Innodb_buffer_pool_read_requests", "Innodb_buffer_pool_reads")
    return {"value": value, "seconds": round(secs, 3), "hll": hll(), "pool_read_requests": after["Innodb_buffer_pool_read_requests"] - before["Innodb_buffer_pool_read_requests"], "disk_reads": after["Innodb_buffer_pool_reads"] - before["Innodb_buffer_pool_reads"]}


PK = "SELECT COUNT(*) FROM t FORCE INDEX (PRIMARY)"
SEC = "SELECT COUNT(*) FROM t FORCE INDEX (k_k)"

with r.section("own_old_snapshot") as d:
    fresh()
    c = connect(db=S, read_timeout=300)
    query(c, "SET SESSION innodb_parallel_read_threads=1")
    d["control_repeats"] = {"primary": [timed(c, PK) for _ in range(3)], "secondary": [timed(c, SEC) for _ in range(3)]}
    snap = connect(db=S, autocommit=False, read_timeout=300)
    query(snap, "SET SESSION innodb_parallel_read_threads=1")
    query(snap, "START TRANSACTION WITH CONSISTENT SNAPSHOT")
    query(snap, "SELECT COUNT(*) FROM t WHERE id=1")
    t = time.monotonic()
    query(b, "UPDATE t SET v=v+1 WHERE id<=1500000")
    query(b, "UPDATE t SET k=k+1 WHERE id<=1500000")
    d["update_seconds"] = round(time.monotonic() - t, 1)
    d["hll_after_updates"] = hll()
    d["old_snapshot_itself"] = {"primary": [timed(snap, PK) for _ in range(3)], "secondary": [timed(snap, SEC) for _ in range(3)]}
    d["new_snapshot_while_old_is_held"] = {"primary": [timed(c, PK) for _ in range(3)], "secondary": [timed(c, SEC) for _ in range(3)]}
    query(snap, "ROLLBACK")
    snap.close()
    # HLL counts transactions, so one huge update looks small here. This wait does not show purge finished: the label says only that the snapshot was released.
    t0 = time.monotonic()
    while time.monotonic() - t0 < 60 and hll() > 1000:
        time.sleep(2)
    d["after_release"] = {"purge_finished_is_unknown": True, "waited_s": round(time.monotonic() - t0), "hll": hll(), "primary": [timed(c, PK) for _ in range(3)], "secondary": [timed(c, SEC) for _ in range(3)]}
    c.close()

with r.section("after_bulk_delete") as d:
    fresh()
    c = connect(db=S, read_timeout=300)
    query(c, "SET SESSION innodb_parallel_read_threads=1")
    session_setup(c, 0)
    plan_pk, plan_k = forced_plan(c, S, "t", "PRIMARY"), forced_plan(c, S, "t", "k_k")

    def split_stats(plan):
        s = split_count(c, "t", plan, width=10_000, limit_ms=5000, duty=None, enforce_plan=False)
        return {k: s.get(k) for k in ("status", "rows", "seconds_total", "stmt_ms_p50", "stmt_ms_p99", "stmt_ms_max")}
    d["control"] = {"count_primary": timed(c, PK), "count_secondary": timed(c, SEC), "split_primary": split_stats(plan_pk), "split_secondary": split_stats(plan_k)}
    # A snapshot holds purge back, so delete marks stay in the indexes while they are read.
    snap = connect(db=S, autocommit=False, read_timeout=300)
    query(snap, "START TRANSACTION WITH CONSISTENT SNAPSHOT")
    query(snap, "SELECT COUNT(*) FROM t WHERE id=1")
    query(b, "DELETE FROM t WHERE id<=1500000")
    d["hll_after_delete"] = hll()
    d["marks_kept_by_snapshot"] = {"count_primary": timed(c, PK), "count_secondary": timed(c, SEC), "split_primary": split_stats(plan_pk), "split_secondary": split_stats(plan_k)}
    query(snap, "ROLLBACK")
    snap.close()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 60 and hll() > 1000:
        time.sleep(2)
    d["after_release"] = {"purge_finished_is_unknown": True, "waited_s": round(time.monotonic() - t0), "hll": hll(), "count_primary": timed(c, PK), "count_secondary": timed(c, SEC), "split_primary": split_stats(plan_pk), "split_secondary": split_stats(plan_k)}
    c.close()

query(b, f"DROP DATABASE {S}")
r.finish()
