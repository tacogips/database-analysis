#!/usr/bin/env python3
"""How far off is each way of getting a row count from metadata, without COUNT, on every table design?

Answers: if a rough number is enough, which metadata to read and how many percent it can be off. 24-EXP-R10 covers staleness after
bulk changes on a few tables; this covers every design, repeat-to-repeat spread, the sample size, and uneven row sizes.
Statistics on shared tables are refreshed by ANALYZE and the sample setting is restored; no rows change.
"""
import statistics, time
import pymysql
from lablib import connect, query, save

DB = "bench"
EXCLUDE = {"seq1m", "load_state", "count_summary", "d_trigger_log"}


def tables(c):
    return [r[0] for r in query(c, "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME", (DB,)) if r[0] not in EXCLUDE]


def timed(c, sql, args=None, pick=lambda rows: rows[0][0] if rows else None):
    t = time.monotonic()
    try:
        return pick(query(c, sql, args)), round((time.monotonic() - t) * 1000, 2)
    except pymysql.MySQLError as e:
        return f"error {e.args[0]}", None


def show_status_rows(c, t):
    with c.cursor(pymysql.cursors.DictCursor) as k:
        k.execute("SHOW TABLE STATUS WHERE Name=%s", (t,))
        return k.fetchone()["Rows"]


def primary_span(c, t):
    cols = query(c, "SELECT COLUMN_NAME,DATA_TYPE FROM information_schema.STATISTICS JOIN information_schema.COLUMNS USING (TABLE_SCHEMA,TABLE_NAME,COLUMN_NAME) WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND INDEX_NAME='PRIMARY'", (DB, t))
    if len(cols) != 1 or "int" not in cols[0][1]:
        return None
    lo, hi = query(c, f"SELECT MIN(`{cols[0][0]}`),MAX(`{cols[0][0]}`) FROM `{t}`")[0]
    return None if lo is None else int(hi) - int(lo) + 1


def read_all(c, t):
    """Every method. The same session setting is named in each key, because the cache changes the answer."""
    out = {}
    q = "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s"
    query(c, "SET SESSION information_schema_stats_expiry=DEFAULT")
    out["table_rows_default_cache"] = timed(c, q, (DB, t))
    query(c, "SET SESSION information_schema_stats_expiry=0")
    out["table_rows_expiry0"] = timed(c, q, (DB, t))
    out["show_table_status_expiry0"] = (show_status_rows(c, t), None)
    out["innodb_table_stats_n_rows"] = timed(c, "SELECT n_rows FROM mysql.innodb_table_stats WHERE database_name=%s AND table_name=%s", (DB, t))
    stats, ms = timed(c, "SELECT STATS_INITIALIZED,NUM_ROWS FROM information_schema.INNODB_TABLESTATS WHERE NAME=%s",
                      (f"{DB}/{t}",), pick=lambda rows: rows[0] if rows else None)
    if isinstance(stats, (tuple, list)):
        out["innodb_tablestats_state"] = (stats[0], ms)
        out["innodb_tablestats_num_rows_raw"] = (stats[1], ms)
        # ANALYZE invalidates in-memory statistics. An uninitialized zero is not an estimate of an empty table.
        out["innodb_tablestats_num_rows"] = (stats[1] if stats[0] == "Initialized" else None, ms)
    else:
        out["innodb_tablestats_state"] = (stats, ms)
        out["innodb_tablestats_num_rows"] = (None, ms)
    out["explain_rows"] = timed(c, f"EXPLAIN SELECT * FROM `{t}`", pick=lambda r: r[0][9])
    out["explain_count_rows"] = timed(c, f"EXPLAIN SELECT COUNT(*) FROM `{t}`", pick=lambda r: r[0][9])
    out["partitions_sum"] = timed(c, "SELECT SUM(TABLE_ROWS) FROM information_schema.PARTITIONS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (DB, t))
    auto = query(c, "SELECT AUTO_INCREMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (DB, t))[0][0]
    out["auto_increment_minus_1"] = (None if auto is None else int(auto) - 1, None)
    out["pk_span"] = (primary_span(c, t), None)
    query(c, "SET SESSION information_schema_stats_expiry=DEFAULT")
    return out


def error_pct(value, truth):
    if not isinstance(value, (int, float)) and not hasattr(value, "__float__"):
        return None
    return round((float(value) - truth) * 100 / truth, 3) if truth else None


def summarize(per_table, method):
    errs = [abs(v[method]["error_pct"]) for v in per_table.values() if v.get(method, {}).get("error_pct") is not None]
    if not errs:
        return None
    errs.sort()
    return {"tables": len(errs), "median_abs_pct": round(statistics.median(errs), 3), "p90_abs_pct": errs[int(len(errs) * .9)], "max_abs_pct": errs[-1],
            "within_1pct": sum(e <= 1 for e in errs), "within_5pct": sum(e <= 5 for e in errs), "within_10pct": sum(e <= 10 for e in errs)}


def analyze(c, t):
    try:
        query(c, f"ANALYZE TABLE `{t}`")
    except pymysql.MySQLError:
        pass


def uneven_rows(c):
    """Half the rows tiny, half near the page limit: sampled pages give different rows per page."""
    query(c, "SET SESSION sql_log_bin=0")
    query(c, "DROP DATABASE IF EXISTS req_uneven")
    query(c, "CREATE DATABASE req_uneven")
    query(c, "CREATE TABLE req_uneven.t (id BIGINT PRIMARY KEY, body VARCHAR(7000) NOT NULL)")
    for off in range(0, 2_000_000, 100_000):
        query(c, f"INSERT INTO req_uneven.t SELECT n+{off}, 'x' FROM bench.seq1m WHERE n<=100000")
    for off in range(0, 100_000, 10_000):
        query(c, f"INSERT INTO req_uneven.t SELECT n+{off}+2000000, REPEAT('y',6000) FROM bench.seq1m WHERE n<=10000")
    truth = query(c, "SELECT COUNT(*) FROM req_uneven.t")[0][0]
    res = []
    for pages in (20, 200, 2000):
        query(c, f"SET GLOBAL innodb_stats_persistent_sample_pages={pages}")
        for _ in range(5):
            query(c, "ANALYZE TABLE req_uneven.t")
            query(c, "SET SESSION information_schema_stats_expiry=0")
            est = query(c, "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='req_uneven' AND TABLE_NAME='t'")[0][0]
            res.append({"sample_pages": pages, "estimate": est, "error_pct": error_pct(est, truth)})
    query(c, "SET GLOBAL innodb_stats_persistent_sample_pages=20")
    query(c, "DROP DATABASE req_uneven")
    return {"truth": truth, "runs": res}


def main():
    c = connect(read_timeout=3600)
    default_pages = query(c, "SELECT @@innodb_stats_persistent_sample_pages")[0][0]
    out = {"default_sample_pages": default_pages, "before_analyze": {}, "after_analyze": {}, "spread_by_sample_pages": {}}
    names = tables(c)
    truth = {}
    for t in names:
        truth[t] = query(c, f"SELECT COUNT(*) FROM `{t}`")[0][0]
    # 1. As found: statistics as the server keeps them, without any ANALYZE from us.
    for t in names:
        vals = read_all(c, t)
        out["before_analyze"][t] = {m: {"value": str(v[0]), "ms": v[1], "error_pct": error_pct(v[0], truth[t])} for m, v in vals.items()}
        out["before_analyze"][t]["truth"] = truth[t]
    # 2. After ANALYZE at the default sample size.
    for t in names:
        analyze(c, t)
    for t in names:
        vals = read_all(c, t)
        out["after_analyze"][t] = {m: {"value": str(v[0]), "ms": v[1], "error_pct": error_pct(v[0], truth[t])} for m, v in vals.items()}
    methods = ["table_rows_default_cache", "table_rows_expiry0", "show_table_status_expiry0", "innodb_table_stats_n_rows", "innodb_tablestats_num_rows",
               "explain_rows", "explain_count_rows", "partitions_sum", "auto_increment_minus_1", "pk_span"]
    out["summary_before_analyze"] = {m: summarize(out["before_analyze"], m) for m in methods}
    out["summary_after_analyze"] = {m: summarize(out["after_analyze"], m) for m in methods}
    save("requested_metadata_accuracy", out)
    # 3. ANALYZE samples random pages: spread between repeats, and whether more pages help. Largest and most varied tables only.
    try:
        for pages in (20, 200, 2000):
            query(c, f"SET GLOBAL innodb_stats_persistent_sample_pages={pages}")
            for t in ("t_dense", "t_uuid", "t_composite", "t_lob", "t_part", "d_soft_delete", "orders", "d_compressed"):
                errs = []
                t0 = time.monotonic()
                for _ in range(5):
                    analyze(c, t)
                    query(c, "SET SESSION information_schema_stats_expiry=0")
                    errs.append(error_pct(query(c, "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (DB, t))[0][0], truth[t]))
                out["spread_by_sample_pages"].setdefault(t, {})[str(pages)] = {"error_pct": errs, "analyze_seconds_each": round((time.monotonic() - t0) / 5, 3)}
                print(t, pages, errs, flush=True)
        out["uneven_row_sizes"] = uneven_rows(c)
    finally:
        query(c, f"SET GLOBAL innodb_stats_persistent_sample_pages={default_pages}")
        for t in names:
            analyze(c, t)
    save("requested_metadata_accuracy", out)
    for m, v in out["summary_before_analyze"].items():
        print("before", m, v, flush=True)
    for m, v in out["summary_after_analyze"].items():
        print("after", m, v, flush=True)
    print("uneven", out["uneven_row_sizes"], flush=True)


if __name__ == "__main__":
    main()
