#!/usr/bin/env python3
"""Wide tables: does the way a count is written matter? COUNT(*), COUNT(1), COUNT(id), COUNT(col), by index design.

Tables are built in bench_wide and dropped afterwards. t_dense (100M, narrow) gives the same comparison at scale.
"""
import time
from lablib import connect, query, save

SIZES = {"cols_60": (60, 3_000_000), "cols_200": (200, 500_000)}


def create(c, name, ncols, secondary):
    cols = ["id BIGINT NOT NULL PRIMARY KEY", "nullable_col INT NULL", "notnull_col INT NOT NULL", "small_idx SMALLINT NOT NULL"]
    # Mixed types and enough width that the clustered index is much larger than a small secondary index.
    for i in range(ncols - 4):
        cols.append(f"c{i} VARCHAR(24) NOT NULL" if i % 2 == 0 else f"c{i} INT NOT NULL")
    if secondary:
        cols.append("KEY k_small (small_idx)")
    query(c, f"CREATE TABLE bench_wide.{name} ({','.join(cols)}) ENGINE=InnoDB")


def fill(c, name, ncols, rows):
    vals = ",".join("LEFT(MD5(n),24)" if i % 2 == 0 else "n" for i in range(ncols - 4))
    for off in range(0, rows, 100_000):
        query(c, f"INSERT INTO bench_wide.{name} SELECT n+{off}, IF(MOD(n,3)=0,NULL,n), n, MOD(n,1000), {vals} FROM bench.seq1m WHERE n<=100000")


def measure(c, sql, setup=()):
    for s in setup:
        query(c, s)
    runs = []
    for _ in range(3):
        query(c, "FLUSH STATUS")
        before = int(query(c, "SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_reads'")[0][1])
        t = time.monotonic()
        value = query(c, sql)[0][0]
        secs = time.monotonic() - t
        stats = dict(query(c, "SHOW SESSION STATUS WHERE Variable_name IN ('Handler_read_first','Handler_read_next','Handler_read_rnd_next')"))
        runs.append({"count": value, "seconds": round(secs, 3), "disk_page_reads": int(query(c, "SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_reads'")[0][1]) - before,
                     "handler": {k: int(v) for k, v in stats.items()}})
    plan = query(c, "EXPLAIN " + sql)[0]
    return {"sql": sql, "explain_type": plan[4], "explain_key": plan[6], "explain_extra": plan[11], "runs": runs}


def forms(c, table, indexed):
    out = {}
    cases = {"count_star": f"SELECT COUNT(*) FROM {table}", "count_1": f"SELECT COUNT(1) FROM {table}", "count_id": f"SELECT COUNT(id) FROM {table}"}
    if "t_dense" not in table:
        cases["count_notnull_unindexed"] = f"SELECT COUNT(notnull_col) FROM {table}"
        cases["count_nullable_unindexed"] = f"SELECT COUNT(nullable_col) FROM {table}"
    if indexed:
        col = "small_idx" if "t_dense" not in table else "created_at"
        idx = "k_small" if "t_dense" not in table else "k_created"
        cases["count_indexed_col"] = f"SELECT COUNT({col}) FROM {table}"
        cases["count_star_force_secondary"] = f"SELECT COUNT(*) FROM {table} FORCE INDEX ({idx})"
    cases["count_star_force_primary"] = f"SELECT COUNT(*) FROM {table} FORCE INDEX (PRIMARY)"
    for name, sql in cases.items():
        out[name] = measure(c, sql)
        out[name + "_single_thread"] = measure(c, sql, ["SET SESSION innodb_parallel_read_threads=1"])
        query(c, "SET SESSION innodb_parallel_read_threads=DEFAULT")
        print(table, name, [r["seconds"] for r in out[name]["runs"]], "single", [r["seconds"] for r in out[name + "_single_thread"]["runs"]],
              "count", out[name]["runs"][0]["count"], "key", out[name]["explain_key"], flush=True)
    return out


def main():
    c = connect(read_timeout=1800)
    query(c, "SET SESSION sql_log_bin=0")
    query(c, "DROP DATABASE IF EXISTS bench_wide")
    query(c, "CREATE DATABASE bench_wide")
    res = {}
    try:
        for label, (ncols, rows) in SIZES.items():
            for design, secondary in (("no_secondary", False), ("with_small_secondary", True)):
                name = f"{label}_{design}"
                create(c, name, ncols, secondary)
                fill(c, name, ncols, rows)
                query(c, f"ANALYZE TABLE bench_wide.{name}")
                size = query(c, "SELECT data_length,index_length,avg_row_length FROM information_schema.tables WHERE table_schema='bench_wide' AND table_name=%s", (name,))[0]
                res[name] = {"columns": ncols, "rows": rows, "data_bytes": size[0], "index_bytes": size[1], "avg_row": size[2], "forms": forms(c, f"bench_wide.{name}", secondary)}
                save("requested_wide_count_forms", res)
        res["t_dense_100m"] = {"rows": 100_000_000, "forms": forms(c, "bench.t_dense", True)}
        save("requested_wide_count_forms", res)
    finally:
        query(c, "DROP DATABASE IF EXISTS bench_wide")


if __name__ == "__main__":
    main()
