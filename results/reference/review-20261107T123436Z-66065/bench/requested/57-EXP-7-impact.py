#!/usr/bin/env python3
"""Answers EXP-7's main impact test on 9.7 and 26.10, with 8.4.13 for comparison. A small server with a 256 MB buffer pool, so this only compares arms within one version.
It does not extend the 8.4.13 impact results to the other lines."""
import sys, time
from countlib import session_setup, split_count, split_plans
from lablib import connect, query
from reqlib import Results, guard, measured_run

HOST, VERSION = sys.argv[1], sys.argv[2]
ID = f"57-EXP-7-impact-{VERSION}"
guard(ID, 6)
r = Results(ID, ["EXP-7"], version=VERSION, buffer_pool_mb=256, rows=3_000_000)
query(connect(HOST, db="mysql"), "CREATE DATABASE IF NOT EXISTS bench")
a = connect(HOST, read_timeout=300)
query(a, "SET SESSION cte_max_recursion_depth=300000")
query(a, "SET SESSION sql_log_bin=0")
query(a, "CREATE TABLE seq (n INT PRIMARY KEY)")
query(a, "INSERT INTO seq WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n<200000) SELECT n FROM s")
query(a, "CREATE TABLE imp (id BIGINT PRIMARY KEY, v INT NOT NULL, created_at DATETIME NOT NULL, note VARCHAR(32) NOT NULL, KEY k_created (created_at))")
for off in range(0, 3_000_000, 200_000):
    # LPAD, not MD5: MD5 returns 1305 on the 9.7.4 and 26.10.0 servers here.
    query(a, f"INSERT INTO imp SELECT n+{off}, n, DATE_ADD('2020-01-01', INTERVAL FLOOR((n+{off})/10) SECOND), LPAD(n,32,'0') FROM seq")
query(a, "ANALYZE TABLE imp")
WL = dict(table="imp", max_id=3_000_000, write_sql="UPDATE imp SET v=v+1 WHERE id=%s")


def repeat(stmt, seconds=20):
    def body(notes):
        c = connect(HOST, read_timeout=120)
        session_setup(c, 0)
        t0, n = time.monotonic(), 0
        while time.monotonic() - t0 < seconds:
            query(c, stmt)
            n += 1
        notes["runs"] = n
        c.close()
    return body


def split_body(notes):
    c = connect(HOST, read_timeout=120)
    session_setup(c, 0)
    t0, n = time.monotonic(), 0
    p = split_plans(c, "bench", "imp")["plans"][0]
    notes["index"] = p["index"]
    while time.monotonic() - t0 < 20:
        s = split_count(c, "imp", p, width=10_000, limit_ms=500, duty=.5, db="bench")
        n += 1
        notes["last"] = {k: s.get(k) for k in ("status", "rows", "stmt_ms_max", "seconds_total")}
    notes["runs"] = n
    c.close()


ARMS = {"control": lambda notes: time.sleep(20), "repeated_count": repeat("SELECT COUNT(*) FROM imp"), "split_duty50": split_body}
with r.section("arms") as d:
    for rep in range(2):
        for name, body in ARMS.items():
            if r.time_left() < 90:
                d["stopped_early"] = f"{rep}/{name}"
                break
            res = measured_run(name, WL, body, 15, 15, host=HOST)
            res.pop("series_10s", None)
            d[f"{name}_{rep}"] = res
            r.save()
            print(name, rep, res["latency"]["during"]["write"], flush=True)
r.finish()
