#!/usr/bin/env python3
"""Answers EXP-SOL-10 (FIND-ENGINE-01): stored counts and table locks by engine state, on private tables. Restarts are tried separately on a disposable server by 70-EXP-SOL-10-restart."""
import threading, time
from lablib import connect, query
from reqlib import Results, builder, guard, reset_schema, sql_try

ID = "55-EXP-SOL-10"
guard(ID, 3)
r = Results(ID, ["EXP-SOL-10", "FIND-ENGINE-01"])
S = "req_eng"
b = builder()
reset_schema(b, S)
query(b, f"USE {S}")
query(b, "CREATE TABLE mi (id INT PRIMARY KEY, v INT NOT NULL) ENGINE=MyISAM")
query(b, "INSERT INTO mi SELECT n, n FROM bench.seq1m")
query(b, "CREATE TABLE cs (id INT NOT NULL, v INT NOT NULL) ENGINE=CSV")
query(b, "INSERT INTO cs SELECT n, n FROM bench.seq1m WHERE n<=500000")
query(b, "CREATE TABLE bh (id INT PRIMARY KEY) ENGINE=BLACKHOLE")
query(b, "INSERT INTO bh VALUES (1),(2)")
query(b, "CREATE TABLE mg1 (id INT NOT NULL, KEY k (id)) ENGINE=MyISAM")
query(b, "CREATE TABLE mg2 (id INT NOT NULL, KEY k (id)) ENGINE=MyISAM")
query(b, "INSERT INTO mg1 SELECT n FROM bench.seq1m WHERE n<=1000")
query(b, "INSERT INTO mg2 SELECT n FROM bench.seq1m WHERE n<=2000")
query(b, "CREATE TABLE mg (id INT NOT NULL, KEY k (id)) ENGINE=MERGE UNION=(mg1,mg2)")

with r.section("plans_and_limits") as d:
    c = connect(db=S, read_timeout=60)
    query(c, "SET SESSION max_execution_time=0")
    for t in ("mi", "cs", "bh", "mg"):
        x = query(c, f"EXPLAIN SELECT COUNT(*) FROM {t}")[0]
        res = sql_try(c, f"SELECT /*+ MAX_EXECUTION_TIME(1000) */ COUNT(*) FROM {t}")
        d[t] = {"explain_extra": x[11], "explain_type": x[4], "value": res.get("rows"), "ok": res["ok"], "errno": res.get("errno"), "ms": res["ms"]}
    # A scan long enough to reach a short limit: the CSV table has no stored count.
    d["csv_limit_50ms"] = sql_try(c, "SELECT /*+ MAX_EXECUTION_TIME(50) */ COUNT(*) FROM cs")
    # A write during a CSV scan: does the table lock stop it?
    res = {}
    th = threading.Thread(target=lambda: res.update(sql_try(c, "SELECT COUNT(*) FROM cs")), daemon=True)
    th.start()
    w = connect(db=S, read_timeout=30)
    query(w, "SET SESSION lock_wait_timeout=2")
    t = time.monotonic()
    d["csv_write_during_scan"] = sql_try(w, "INSERT INTO cs VALUES (1,1)")
    d["csv_write_ms"] = round((time.monotonic() - t) * 1000)
    th.join(30)
    d["csv_scan_ms"] = res.get("ms")
    w.close(); c.close()

with r.section("check_table_on_myisam") as d:
    # CHECK TABLE during a stream of reads and writes: how long do they wait?
    lat = {"read": [], "write": [], "count": []}
    stop = threading.Event()

    def probe(kind, sql):
        c = connect(db=S, read_timeout=60)
        while not stop.is_set():
            t = time.monotonic()
            sql_try(c, sql)
            lat[kind].append((time.monotonic() - t) * 1000)
            time.sleep(.01)
        c.close()
    ts = [threading.Thread(target=probe, args=a, daemon=True) for a in (("read", "SELECT v FROM mi WHERE id=7"), ("write", "UPDATE mi SET v=v+1 WHERE id=8"), ("count", "SELECT COUNT(*) FROM mi"))]
    for t in ts:
        t.start()
    time.sleep(2)
    base = {k: round(max(v), 1) for k, v in lat.items() if v}
    for k in lat:
        lat[k].clear()
    c = connect(db=S, read_timeout=120)
    results = {}
    for name, sql in (("check_medium", "CHECK TABLE mi MEDIUM"), ("check_extended", "CHECK TABLE mi EXTENDED")):
        t = time.monotonic()
        res = query(c, sql)
        results[name] = {"seconds": round(time.monotonic() - t, 2), "result": [list(x) for x in res]}
    time.sleep(.5)
    stop.set()
    for t in ts:
        t.join(10)
    d.update(max_ms_before=base, max_ms_during=({k: round(max(v), 1) for k, v in lat.items() if v}), checks=results)
    c.close()

with r.section("not_possible_here") as d:
    d["memory_one_side_restart"] = "See results/requested_70-EXP-SOL-10-restart-8.4.13.json. A script here cannot restart the compose containers."
    d["myisam_after_crash"] = "Needs a killed server, which the shared primary must not be. Unmeasured."
query(b, f"DROP DATABASE {S}")
r.finish()
