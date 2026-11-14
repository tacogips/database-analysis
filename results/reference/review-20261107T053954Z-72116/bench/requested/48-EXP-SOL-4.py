#!/usr/bin/env python3
"""Answers EXP-SOL-4 (FIND-SOL-VIEW-01): a view's count against the base table counts, and what the flow does with each view. Private schema."""
from count_preflight import analyze
from countlib import session_setup
from lablib import connect, query
from reqlib import Results, builder, guard, reset_schema

ID = "48-EXP-SOL-4"
guard(ID, 1)
r = Results(ID, ["EXP-SOL-4", "FIND-SOL-VIEW-01"])
S = "req_view"
b = builder()
reset_schema(b, S)
query(b, f"USE {S}")
for s in ("CREATE TABLE p (id INT PRIMARY KEY)",
          "CREATE TABLE ch (id INT PRIMARY KEY, p_id INT NULL, KEY k (p_id))",
          "CREATE TABLE u2 (id INT PRIMARY KEY)",
          "INSERT INTO p SELECT n FROM bench.seq1m WHERE n<=100",
          "INSERT INTO ch SELECT n, IF(MOD(n,10)=0,NULL,1+MOD(n,50)) FROM bench.seq1m WHERE n<=1000",
          "INSERT INTO u2 SELECT n FROM bench.seq1m WHERE n BETWEEN 51 AND 150",
          "CREATE VIEW v_join AS SELECT ch.id, p.id AS pid FROM ch JOIN p ON p.id=ch.p_id",
          "CREATE VIEW v_left AS SELECT p.id, ch.id AS cid FROM p LEFT JOIN ch ON p.id=ch.p_id",
          "CREATE VIEW v_filter AS SELECT id FROM ch WHERE p_id IS NOT NULL",
          "CREATE VIEW v_union AS SELECT id FROM p UNION SELECT id FROM u2",
          "CREATE VIEW v_union_all AS SELECT id FROM p UNION ALL SELECT id FROM u2",
          "CREATE VIEW v_group AS SELECT p_id, COUNT(*) AS c FROM ch GROUP BY p_id",
          "CREATE VIEW v_plain AS SELECT id FROM p"):
    query(b, s)

with r.section("views") as d:
    c = connect(db=S, read_timeout=60)
    session_setup(c)
    d["base_counts"] = {t: query(c, f"SELECT COUNT(*) FROM {t}")[0][0] for t in ("p", "ch", "u2")}
    for v in ("v_join", "v_left", "v_filter", "v_union", "v_union_all", "v_group", "v_plain"):
        rep = analyze(c, S, v)
        d[v] = {"view_count": query(c, f"SELECT /*+ MAX_EXECUTION_TIME(1000) */ COUNT(*) FROM {v}")[0][0], "flow": rep["recommendation"]["method"],
                "explain_tables": [str(x[2]) for x in query(c, f"EXPLAIN SELECT COUNT(*) FROM {v}")]}
    c.close()
query(b, f"DROP DATABASE {S}")
r.finish()
