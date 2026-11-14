#!/usr/bin/env python3
"""Answers the restart part of EXP-SOL-10, on a disposable fresh server: argv is <host> <version>. RESTART needs a supervisor, so a refusal here means this lab cannot test it."""
import sys, time
from lablib import connect, query
from reqlib import Results, guard, sql_try

HOST, VERSION = sys.argv[1], sys.argv[2]
ID = f"70-EXP-SOL-10-restart-{VERSION}"
guard(ID, 2)
r = Results(ID, ["EXP-SOL-10", "FIND-ENGINE-01"], version=VERSION)
with r.section("restart") as d:
    query(connect(HOST, db="mysql"), "CREATE DATABASE IF NOT EXISTS bench")
    c = connect(HOST, read_timeout=30)
    query(c, "CREATE TABLE m (id INT PRIMARY KEY) ENGINE=MEMORY")
    query(c, "INSERT INTO m VALUES (1),(2),(3)")
    d["memory_before"] = query(c, "SELECT COUNT(*) FROM m")[0][0]
    d["restart"] = sql_try(c, "RESTART")
    if d["restart"].get("ok"):
        t0 = time.monotonic()
        while time.monotonic() - t0 < 90:
            try:
                x = connect(HOST, read_timeout=10)
                d["memory_after_restart"] = query(x, "SELECT COUNT(*) FROM m")[0][0]
                d["seconds_until_back"] = round(time.monotonic() - t0, 1)
                break
            except Exception:
                time.sleep(2)
r.finish()
