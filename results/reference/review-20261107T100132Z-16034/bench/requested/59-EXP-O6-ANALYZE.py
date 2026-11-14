#!/usr/bin/env python3
"""Answers EXP-O6-ANALYZE (FIND-O6-ANALYZE-01): does ANALYZE TABLE during a long count queue later reads of that table? Fresh small server: argv is <host> <version>.
The control runs the same count and probe with no ANALYZE."""
import sys, threading, time
from lablib import connect, query
from reqlib import Results, guard, sql_try

HOST, VERSION = sys.argv[1], sys.argv[2]
ID = f"59-EXP-O6-ANALYZE-{VERSION}"
guard(ID, 4)
r = Results(ID, ["EXP-O6-ANALYZE", "FIND-O6-ANALYZE-01"], version=VERSION)
query(connect(HOST, db="mysql"), "CREATE DATABASE IF NOT EXISTS bench")
a = connect(HOST, read_timeout=300)
query(a, "SET SESSION cte_max_recursion_depth=300000")
query(a, "CREATE TABLE seq (n INT PRIMARY KEY)")
query(a, "INSERT INTO seq WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n<200000) SELECT n FROM s")
query(a, "CREATE TABLE t (id BIGINT PRIMARY KEY, pad CHAR(200) NOT NULL DEFAULT 'x')")
for off in range(0, 4_000_000, 200_000):
    query(a, f"INSERT INTO t (id) SELECT n+{off} FROM seq")


def arm(with_analyze):
    c = connect(HOST, read_timeout=120)
    query(c, "SET SESSION innodb_parallel_read_threads=1")
    count = {}
    th = threading.Thread(target=lambda: count.update(sql_try(c, "SELECT COUNT(*) FROM t FORCE INDEX (PRIMARY)")), daemon=True)
    lat, seen, halt = [], [], threading.Event()

    def probe():
        p = connect(HOST, read_timeout=60)
        while not halt.is_set():
            t = time.monotonic()
            sql_try(p, "SELECT id FROM t WHERE id=1")
            lat.append((time.monotonic() - t) * 1000)
            time.sleep(.02)
        p.close()

    def watch():
        o = connect(HOST, read_timeout=20)
        while not halt.is_set():
            seen.append({"pending": query(o, "SELECT COUNT(*) FROM performance_schema.metadata_locks WHERE OBJECT_NAME='t' AND LOCK_STATUS='PENDING'")[0][0],
                         "states": [x[0] for x in query(o, "SELECT PROCESSLIST_STATE FROM performance_schema.threads WHERE PROCESSLIST_STATE LIKE 'Waiting for%' AND TYPE='FOREGROUND'")]})
            halt.wait(.05)
        o.close()
    pt, wt = threading.Thread(target=probe, daemon=True), threading.Thread(target=watch, daemon=True)
    pt.start()
    wt.start()
    time.sleep(.3)
    t0 = time.monotonic()
    th.start()
    time.sleep(.3)
    res = {}
    if with_analyze:
        x = connect(HOST, read_timeout=120)
        query(x, "SET SESSION lock_wait_timeout=30")
        t = time.monotonic()
        out = sql_try(x, "ANALYZE TABLE t")
        res["analyze"] = {"ok": out["ok"], "ms": round((time.monotonic() - t) * 1000, 1), "result": out.get("rows")}
        x.close()
    th.join(120)
    res["count_ms"] = round((time.monotonic() - t0) * 1000, 1)
    time.sleep(.3)
    halt.set()
    pt.join(30)
    wt.join(5)
    c.close()
    res.update(probe_n=len(lat), probe_max_ms=round(max(lat), 1) if lat else None, probe_p50_ms=round(sorted(lat)[len(lat) // 2], 2) if lat else None,
               pending_samples=sum(1 for s in seen if s["pending"]), wait_states=sorted({st for s in seen for st in s["states"]}))
    return res


with r.section("arms") as d:
    d["control"] = [arm(False) for _ in range(3)]
    r.save()
    d["with_analyze"] = [arm(True) for _ in range(3)]
r.finish()
