#!/usr/bin/env python3
"""Quiet non-InnoDB design catalog only; these scans are not compliant service-impact count candidates. Table-lock and CHECK contention comparisons excluded."""
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
    c.close()


with r.section("not_possible_here") as d:
    d["memory_one_side_restart"] = "See results/requested_70-EXP-SOL-10-restart-8.4.13.json. A script here cannot restart the compose containers."
    d["myisam_after_crash"] = "Needs a killed server, which the shared primary must not be. Unmeasured."
query(b, f"DROP DATABASE {S}")
r.finish()
