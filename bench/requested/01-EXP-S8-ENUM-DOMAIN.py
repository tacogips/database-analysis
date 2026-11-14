#!/usr/bin/env python3
"""Answers EXP-S8-ENUM-DOMAIN (FIND-S8-ENUM-DOMAIN-01): does a value-by-value ENUM walk lose rows whose internal number is 0, and does the guard catch them?

Fresh small server. Invalid values are stored under a non-strict session, then strict mode returns. A second table defines '' as a real label, so a real '' (number 1) and the error '' (number 0) sit side by side.
Argv is <host> <version>."""
import sys
from countlib import enum_split_count, session_setup
from lablib import connect, query
from reqlib import Results, bounded, guard, sql_try

HOST, VERSION = sys.argv[1], sys.argv[2]
ID = f"01-EXP-S8-ENUM-DOMAIN-{VERSION}"
guard(ID, 2)
r = Results(ID, ["EXP-S8-ENUM-DOMAIN", "FIND-S8-ENUM-DOMAIN-01"], version=VERSION)
query(connect(HOST, db="mysql"), "CREATE DATABASE IF NOT EXISTS bench")
a = connect(HOST, read_timeout=60)
query(a, "SET SESSION cte_max_recursion_depth=300000")
query(a, "CREATE TABLE seq (n INT PRIMARY KEY)")
query(a, "INSERT INTO seq WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n<100000) SELECT n FROM s")


def facts(t):
    return {"truth": query(a, f"SELECT COUNT(*) FROM {t}")[0][0],
            "by_internal_number": {int(k): v for k, v in query(a, f"SELECT status+0, COUNT(*) FROM {t} GROUP BY 1")},
            "explain_first_entry": [str(x) for x in query(a, f"EXPLAIN SELECT status+0 FROM {t} FORCE INDEX (`PRIMARY`) ORDER BY status, id LIMIT 1")[0][4:8]],
            "first_entry_number": query(a, f"SELECT status+0 FROM {t} FORCE INDEX (`PRIMARY`) ORDER BY status, id LIMIT 1")[0][0]}


def walk(t, **kw):
    c = connect(HOST, read_timeout=20)
    session_setup(c)
    s = enum_split_count(c, "bench", t, "status", ["id"], width=500, limit_ms=500, max_steps=2000, **kw)
    c.close()
    want = truth_of[t]
    return {**s, "truth": want, "correct": s["rows"] == want if s["rows"] is not None else None, "short_by": want - s["rows"] if s["rows"] is not None else None}


truth_of = {}
query(a, "CREATE TABLE clean (status ENUM('z','a','m') NOT NULL, id BIGINT NOT NULL, PRIMARY KEY (status, id))")
query(a, "INSERT INTO clean SELECT ELT(1+MOD(n,3),'z','a','m'), n FROM seq WHERE n<=30000")
query(a, "CREATE TABLE dirty LIKE clean")
query(a, "INSERT INTO dirty SELECT ELT(1+MOD(n,3),'z','a','m'), n FROM seq WHERE n<=30000")
query(a, "CREATE TABLE real_empty (status ENUM('','a','m') NOT NULL, id BIGINT NOT NULL, PRIMARY KEY (status, id))")
query(a, "INSERT INTO real_empty SELECT ELT(1+MOD(n,3),'','a','m'), n FROM seq WHERE n<=30000")
original_mode = query(a, "SELECT @@SESSION.sql_mode")[0][0]
query(a, "SET SESSION sql_mode=''")
# The largest ids, on purpose: if the guard only worked because of id order, these would not sort first.
for t in ("dirty", "real_empty"):
    query(a, f"INSERT INTO {t} SELECT 'not a label', n+1000000 FROM seq WHERE n<=700")
query(a, "SET SESSION sql_mode=%s", (original_mode,))
r.data["strict_mode_restored"] = query(a, "SELECT @@SESSION.sql_mode")[0][0] == original_mode
r.data["strict_rejects_it_now"] = sql_try(a, "INSERT INTO dirty VALUES ('not a label', 9000000)")

for t in ("clean", "dirty", "real_empty"):
    with r.section(t) as d:
        d["facts"] = facts(t)
        truth_of[t] = d["facts"]["truth"]
        d["walk_without_guard"] = bounded(20, walk, t, check_zero=False)
        d["walk_with_guard"] = bounded(20, walk, t, check_zero=True)
        # What each way of asking finds, so the text can say what is safe to use for the number-0 rows.
        d["equals_empty_string"] = query(a, f"SELECT COUNT(*) FROM {t} WHERE status = ''")[0][0]
        d["equals_zero"] = query(a, f"SELECT COUNT(*) FROM {t} WHERE status = 0")[0][0]
        d["explain_equals_zero"] = [str(x) for x in query(a, f"EXPLAIN SELECT COUNT(*) FROM {t} WHERE status = 0")[0][4:10]]
        d["explain_equals_empty"] = [str(x) for x in query(a, f"EXPLAIN SELECT COUNT(*) FROM {t} WHERE status = ''")[0][4:10]]
r.finish()
