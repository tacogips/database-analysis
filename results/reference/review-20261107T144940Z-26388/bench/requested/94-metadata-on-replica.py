#!/usr/bin/env python3
"""Row counts from metadata on a read replica, and the operational pros and cons of each method.

- Do primary and replica report the same number? Statistics are kept per server.
- How do they behave after a bulk change, and while the replica lags?
- What can a least-privilege account read, on a read_only and super_read_only replica?
- Which methods wait behind a pending DDL? This is the risk that matters most for a monitoring job.

t_repl gets extra rows that are deleted again before the end.
"""
import threading, time
import pymysql
from lablib import PRIMARY, REPLICA, connect, query, save

T = "t_repl"
BULK = 2_000_000
METHODS = {
    "table_rows": "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='{t}'",
    "innodb_table_stats": "SELECT n_rows FROM mysql.innodb_table_stats WHERE database_name='bench' AND table_name='{t}'",
    "innodb_tablestats": "SELECT NUM_ROWS FROM information_schema.INNODB_TABLESTATS WHERE NAME='bench/{t}'",
    "explain_rows": "EXPLAIN SELECT * FROM bench.{t}",
    "show_table_status": "SHOW TABLE STATUS FROM bench WHERE Name='{t}'",
}


def read(c, method, t=T):
    rows = query(c, METHODS[method].format(t=t))
    if method == "explain_rows":
        return rows[0][9]
    if method == "show_table_status":
        return rows[0][4]
    return rows[0][0] if rows else None


def attempt(c, method, t=T):
    t0 = time.monotonic()
    try:
        return {"value": str(read(c, method, t)), "ms": round((time.monotonic() - t0) * 1000, 1)}
    except pymysql.MySQLError as e:
        return {"error": f"{e.args[0]}: {str(e.args[1])[:90]}", "ms": round((time.monotonic() - t0) * 1000, 1)}


def snapshot(p, r, label, truth_primary, truth_replica):
    out = {"label": label, "truth_primary": truth_primary, "truth_replica": truth_replica}
    for name, c, truth in (("primary", p, truth_primary), ("replica", r, truth_replica)):
        for expiry in ("DEFAULT", "0"):
            query(c, f"SET SESSION information_schema_stats_expiry={expiry}")
            for m in METHODS:
                a = attempt(c, m)
                if "value" in a and a["value"] not in ("None",):
                    a["error_pct"] = round((float(a["value"]) - truth) * 100 / truth, 3)
                out[f"{name}.{m}.expiry_{expiry}"] = a
    print(label, {k: v.get("error_pct", v.get("error")) for k, v in out.items() if isinstance(v, dict) and "expiry_0" in k}, flush=True)
    return out


def counts(p, r):
    return query(p, f"SELECT COUNT(*) FROM {T}")[0][0], query(r, f"SELECT COUNT(*) FROM bench.{T}")[0][0]


def caught_up(p, r):
    gtid = query(p, "SELECT @@GLOBAL.gtid_executed")[0][0]
    result = query(r, "SELECT WAIT_FOR_EXECUTED_GTID_SET(%s, 900)", (gtid,))[0][0]
    if result != 0:
        raise RuntimeError(f"replica did not catch up: WAIT_FOR_EXECUTED_GTID_SET={result}")


def staleness(p, r):
    res = []
    caught_up(p, r)
    res.append(snapshot(p, r, "quiet", *counts(p, r)))
    # The replica is stopped, so its numbers can only reflect what it has applied.
    query(r, "STOP REPLICA SQL_THREAD")
    try:
        for off in range(0, BULK, 100_000):
            query(p, f"INSERT INTO {T} (v,note) SELECT n, 'bulk' FROM seq1m WHERE n<=100000")
        res.append(snapshot(p, r, "inserted_replica_stopped", *counts(p, r)))
    finally:
        query(r, "START REPLICA SQL_THREAD")
    caught_up(p, r)
    res.append(snapshot(p, r, "inserted_replica_caught_up", *counts(p, r)))
    time.sleep(30)
    res.append(snapshot(p, r, "inserted_30s_later", *counts(p, r)))
    return res


def restore(p, r):
    while True:
        with p.cursor() as k:
            if k.execute(f"DELETE FROM {T} WHERE note='bulk' LIMIT 100000") == 0:
                break
    caught_up(p, r)


def analyze_is_local(p, r):
    """Does ANALYZE on one server change the other's number? On a replica this is the only way to refresh if the source's is filtered."""
    out = {}
    query(p, "SET SESSION information_schema_stats_expiry=0")
    query(r, "SET SESSION information_schema_stats_expiry=0")
    out["before"] = {"primary": read(p, "table_rows"), "replica": read(r, "table_rows")}
    query(p, f"ANALYZE NO_WRITE_TO_BINLOG TABLE {T}")
    out["after_primary_local_analyze"] = {"primary": read(p, "table_rows"), "replica": read(r, "table_rows")}
    query(r, f"ANALYZE NO_WRITE_TO_BINLOG TABLE bench.{T}")
    out["after_replica_local_analyze"] = {"primary": read(p, "table_rows"), "replica": read(r, "table_rows")}
    return out


def privileges(r):
    """A monitoring account with only SELECT on one schema, then with nothing but metadata visibility, on the replica."""
    out = {}
    old_read_only, old_super_read_only = query(r, "SELECT @@GLOBAL.read_only,@@GLOBAL.super_read_only")[0]
    query(r, "SET SESSION sql_log_bin=0")
    query(r, "DROP USER IF EXISTS 'lab_reader'@'%'")
    query(r, "CREATE USER 'lab_reader'@'%' IDENTIFIED BY 'lab'")
    try:
        query(r, "GRANT SELECT ON bench.t_repl TO 'lab_reader'@'%'")
        c = pymysql.connect(host=REPLICA, user="lab_reader", password="lab", autocommit=True)
        out["select_on_table_only"] = {m: attempt(c, m) for m in METHODS}
        try:
            query(c, "SET SESSION information_schema_stats_expiry=0")
            out["set_expiry_allowed"] = True
        except pymysql.MySQLError as e:
            out["set_expiry_allowed"] = str(e)
        out["count_star"] = attempt_sql(c, "SELECT COUNT(*) FROM bench.t_repl LIMIT 1")
        c.close()
        out["read_only"] = query(r, "SELECT @@read_only,@@super_read_only")[0]
        query(r, "SET GLOBAL super_read_only=ON")
        try:
            c = connect(REPLICA)
            out["super_read_only_on"] = {m: attempt(c, m) for m in METHODS}
            out["super_read_only_analyze"] = attempt_sql(c, f"ANALYZE TABLE bench.{T}")
        finally:
            query(r, f"SET GLOBAL super_read_only={int(old_super_read_only)}")
            query(r, f"SET GLOBAL read_only={int(old_read_only)}")
    finally:
        query(r, "DROP USER IF EXISTS 'lab_reader'@'%'")
    return out


def attempt_sql(c, sql):
    t0 = time.monotonic()
    try:
        query(c, sql)
        return {"ok": True, "ms": round((time.monotonic() - t0) * 1000, 1)}
    except pymysql.MySQLError as e:
        return {"error": f"{e.args[0]}: {str(e.args[1])[:90]}", "ms": round((time.monotonic() - t0) * 1000, 1)}




def main():
    p = connect(PRIMARY, read_timeout=1800)
    r = connect(REPLICA, read_timeout=1800, db="bench")
    out = {}
    try:
        out["staleness"] = staleness(p, r)
        save("requested_metadata_on_replica", out)
    finally:
        try:
            query(r, "START REPLICA SQL_THREAD")
        except pymysql.MySQLError:
            pass
        restore(p, r)
    out["analyze_is_local"] = analyze_is_local(p, r)
    out["privileges"] = privileges(r)
    out["excluded_probes"] = "waits_on_primary/waits_on_replica_applier_ddl: superseded_by_explicit_user_scope"
    caught_up(p, r)
    caught_up(p, r)
    out["final_counts"] = counts(p, r)
    save("requested_metadata_on_replica", out)
    print({k: out[k] for k in ("analyze_is_local", "privileges", "final_counts")}, flush=True)


if __name__ == "__main__":
    main()
