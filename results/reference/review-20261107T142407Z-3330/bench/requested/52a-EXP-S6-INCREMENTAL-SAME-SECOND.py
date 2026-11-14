#!/usr/bin/env python3
"""Answers EXP-S6-INCREMENTAL-SAME-SECOND (FIND-S6-INCREMENTAL-DETECT-01). Can a change to a closed partition slip past TABLE_ID and UPDATE_TIME when it lands in the same second as the baseline read?

The same ledger and detectors as 52-EXP-R16, with no sleep. Trials are kept only when the trial's start and end fall in one wall-clock second; a control crosses a second. Private table."""
import time
from lablib import query
from reqlib import Results, builder, guard, reset_schema

ID = "52a-EXP-S6-INCREMENTAL-SAME-SECOND"
guard(ID, 3)
r = Results(ID, ["EXP-S6-INCREMENTAL-SAME-SECOND", "FIND-S6-INCREMENTAL-DETECT-01"])
S = "req_inc2"
b = builder()
reset_schema(b, S)
query(b, f"USE {S}")
query(b, """CREATE TABLE t (id BIGINT NOT NULL, d INT NOT NULL, v INT NOT NULL, PRIMARY KEY (id,d)) PARTITION BY RANGE (d)
            (PARTITION p1 VALUES LESS THAN (10), PARTITION p2 VALUES LESS THAN (20), PARTITION p3 VALUES LESS THAN MAXVALUE)""")
query(b, "INSERT INTO t SELECT n, MOD(n,30), n FROM bench.seq1m WHERE n<=300000")
query(b, "CREATE TABLE ledger (part VARCHAR(10) PRIMARY KEY, c BIGINT NOT NULL)")
query(b, "SET SESSION information_schema_stats_expiry=0")


def detectors():
    times = {x[0]: str(x[1]) for x in query(b, f"SELECT PARTITION_NAME, UPDATE_TIME FROM information_schema.PARTITIONS WHERE TABLE_SCHEMA='{S}' AND TABLE_NAME='t'")}
    ids = {x[0]: x[1] for x in query(b, f"SELECT NAME, TABLE_ID FROM information_schema.INNODB_TABLES WHERE NAME LIKE '{S}/t#p#%'")}
    return times, ids


def reconcile():
    for p in ("p1", "p2"):
        n = query(b, f"SELECT COUNT(*) FROM t PARTITION ({p})")[0][0]
        query(b, "REPLACE INTO ledger VALUES (%s,%s)", (p,n))


def incremental():
    return int(query(b, "SELECT COALESCE(SUM(c),0) FROM ledger")[0][0]) + query(b, "SELECT COUNT(*) FROM t PARTITION (p3)")[0][0]


def trial(sql, cross_second):
    """A change just after the baseline. If UPDATE_TIME has one-second resolution, a change within the same second leaves it unchanged."""
    # A change first, so the partition's UPDATE_TIME is this second, then settle at the very start of the next second.
    query(b, "UPDATE t SET v=v+1 WHERE id=3 AND d=3")
    time.sleep(1.05 - (time.time() % 1))
    query(b, "UPDATE t SET v=v+1 WHERE id=3 AND d=3")
    query(b, "COMMIT")
    reconcile()
    time.sleep(.02)
    t_base = time.time()
    t1, i1 = detectors()
    if cross_second:
        time.sleep(1.1)
    changed = query(b, sql)
    t2, i2 = detectors()
    t_end = time.time()
    full, inc = query(b, "SELECT COUNT(*) FROM t FORCE INDEX (PRIMARY)")[0][0], incremental()
    return {"same_wall_second": int(t_base) == int(t_end), "update_time_changed": t1 != t2, "table_id_changed": i1 != i2, "base_update_times": t1, "after_update_times": t2,
            "full": full, "incremental": inc, "error": inc - full, "silent": inc != full and t1 == t2 and i1 == i2, "statement_rows": list(changed) if changed else None}


with r.section("trials") as d:
    next_id = [8_000_000]

    def late_insert():
        next_id[0] += 1
        return f"INSERT INTO t VALUES ({next_id[0]}, 5, 1)"

    def delete_one():
        next_id[0] += 1
        row = query(b, "SELECT id,d FROM t PARTITION (p1) ORDER BY id LIMIT 1")[0]
        return f"DELETE FROM t WHERE id={row[0]} AND d={row[1]}"
    d["same_second"] = {"late_insert": [trial(late_insert(), False) for _ in range(5)], "delete": [trial(delete_one(), False) for _ in range(5)]}
    r.save()
    d["crossing_a_second"] = {"late_insert": [trial(late_insert(), True) for _ in range(3)], "delete": [trial(delete_one(), True) for _ in range(3)]}
    runs = [t for group in (d["same_second"], d["crossing_a_second"]) for v in group.values() for t in v]
    d["summary"] = {"same_second_trials": sum(1 for t in runs if t["same_wall_second"]), "silent_errors": sum(1 for t in runs if t["silent"]), "silent_in_same_second": sum(1 for t in runs if t["silent"] and t["same_wall_second"])}
query(b, f"DROP DATABASE {S}")
r.finish()
