#!/usr/bin/env python3
"""Answers EXP-R16 (FIND-BETTER-01): keeping a final value for closed ranges and counting only the new one. Compared with a full count, then with changes that break the premise, and whether a detector catches them.

Private partitioned table and private append table. Detectors: UPDATE_TIME of each partition, the partition's TABLE_ID, and a periodic full reconcile."""
import time
from lablib import connect, query
from reqlib import Results, builder, global_status, guard, reset_schema

ID = "52-EXP-R16"
guard(ID, 4)
r = Results(ID, ["EXP-R16", "FIND-BETTER-01"])
S = "req_inc"
b = builder()
reset_schema(b, S)
query(b, f"USE {S}")
query(b, """CREATE TABLE t (id BIGINT NOT NULL, d INT NOT NULL, v INT NOT NULL, PRIMARY KEY (id,d)) PARTITION BY RANGE (d)
            (PARTITION p1 VALUES LESS THAN (10), PARTITION p2 VALUES LESS THAN (20), PARTITION p3 VALUES LESS THAN (30), PARTITION p4 VALUES LESS THAN MAXVALUE)""")
for off in range(0, 3_000_000, 1_000_000):
    query(b, f"INSERT INTO t SELECT n+{off}, MOD(n,40), n FROM bench.seq1m")
query(b, "CREATE TABLE ledger (part VARCHAR(10) PRIMARY KEY, c BIGINT NOT NULL)")
query(b, "CREATE TABLE ex LIKE t")
query(b, "ALTER TABLE ex REMOVE PARTITIONING")
query(b, "INSERT INTO ex SELECT n+9000000, 25, 1 FROM bench.seq1m WHERE n<=5000")


def measured(fn):
    before = global_status(b, "Innodb_rows_read")
    t = time.monotonic()
    value = fn()
    return {"value": value, "ms": round((time.monotonic() - t) * 1000, 1), "rows_read": global_status(b, "Innodb_rows_read")["Innodb_rows_read"] - before["Innodb_rows_read"]}


def full():
    return query(b, "SELECT COUNT(*) FROM t FORCE INDEX (PRIMARY)")[0][0]


def incremental():
    closed = query(b, "SELECT COALESCE(SUM(c),0) FROM ledger")[0][0]
    return int(closed) + query(b, "SELECT COUNT(*) FROM t PARTITION (p4)")[0][0]


def identity():
    return {x[0]: x[1] for x in query(b, f"SELECT NAME, TABLE_ID FROM information_schema.INNODB_TABLES WHERE NAME LIKE '{S}/t#p#%'")}


def detect(base_ids):
    """What each detector says. UPDATE_TIME is documented as unreliable across restarts; here it is only compared."""
    query(b, "SET SESSION information_schema_stats_expiry=0")
    times = {x[0]: str(x[1]) for x in query(b, f"SELECT PARTITION_NAME, UPDATE_TIME FROM information_schema.PARTITIONS WHERE TABLE_SCHEMA='{S}' AND TABLE_NAME='t'")}
    query(b, "SET SESSION information_schema_stats_expiry=DEFAULT")
    return {"table_id_changed": identity() != base_ids, "update_times": times}


with r.section("clean_case") as d:
    for p in ("p1", "p2", "p3"):
        query(b, f"INSERT INTO ledger VALUES ('{p}', (SELECT COUNT(*) FROM t PARTITION ({p})))")
    query(b, "ANALYZE TABLE t")
    d["full"] = measured(full)
    d["incremental"] = measured(incremental)
    d["match"] = d["full"]["value"] == d["incremental"]["value"]

with r.section("broken_premises") as d:
    base_ids = identity()
    cases = {"update_in_closed": "UPDATE t SET v=v+1 WHERE id=1 AND d=1",
             "delete_in_closed": "DELETE FROM t WHERE d=5 AND id<=5000",
             "late_arrival_in_closed": "INSERT INTO t SELECT n+8000000, 15, 1 FROM bench.seq1m WHERE n<=1000",
             "exchange_closed": "ALTER TABLE t EXCHANGE PARTITION p3 WITH TABLE ex",
             "truncate_closed": "ALTER TABLE t TRUNCATE PARTITION p2"}
    for name, sql in cases.items():
        before = detect(base_ids)
        time.sleep(1.1)
        query(b, sql)
        after = detect(base_ids)
        f, i = full(), incremental()
        d[name] = {"full": f, "incremental": i, "error": i - f, "table_id_detects": after["table_id_changed"] and not before["table_id_changed"], "update_time_changed": before["update_times"] != after["update_times"], "changed_partitions": [p for p in after["update_times"] if after["update_times"][p] != before["update_times"].get(p)]}
        # The reconcile that puts the ledger right again after each.
        for p in ("p1", "p2", "p3"):
            query(b, f"REPLACE INTO ledger VALUES ('{p}', (SELECT COUNT(*) FROM t PARTITION ({p})))")
        base_ids = identity()

with r.section("append_table") as d:
    query(b, "CREATE TABLE a (id BIGINT PRIMARY KEY AUTO_INCREMENT, v INT NOT NULL)")
    query(b, "INSERT INTO a (v) SELECT n FROM bench.seq1m")
    closed_upto = 900_000
    base = query(b, f"SELECT COUNT(*) FROM a WHERE id<={closed_upto}")[0][0]
    d["full"] = measured(lambda: query(b, "SELECT COUNT(*) FROM a FORCE INDEX (PRIMARY)")[0][0])
    d["incremental"] = measured(lambda: base + query(b, f"SELECT COUNT(*) FROM a WHERE id>{closed_upto}")[0][0])
    query(b, "DELETE FROM a WHERE id=5")
    d["after_old_delete"] = {"full": query(b, "SELECT COUNT(*) FROM a")[0][0], "incremental": base + query(b, f"SELECT COUNT(*) FROM a WHERE id>{closed_upto}")[0][0]}

query(b, f"DROP DATABASE {S}")
r.finish()
