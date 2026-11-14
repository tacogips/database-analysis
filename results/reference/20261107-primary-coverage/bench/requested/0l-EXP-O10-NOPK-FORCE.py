#!/usr/bin/env python3
"""Answers EXP-O10-NOPK-FORCE (FIND-O10-NOPK-FORCE): what reads the clustered index on a table with no index named PRIMARY? Private copies of the t_nopk and t_uniq_notnull designs, under scattered writes at 100/s.

For each design: FORCE INDEX (PRIMARY), then IGNORE INDEX naming every secondary index, then the default plan, each with EXPLAIN and a 60 s limit, three repeats. On the promoted design also FORCE INDEX on the unique key itself."""
import time
from lablib import METHOD_CPU, Workload, connect, pin, query
from reqlib import Results, builder, guard, reset_schema, sql_try

ID = "0l-EXP-O10-NOPK-FORCE"
guard(ID, 10)
r = Results(ID, ["EXP-O10-NOPK-FORCE", "FIND-O10-NOPK-FORCE"], rows=5_000_000, writes_per_s=100, statement_limit_ms=60_000)
S = "req_nopk"
b = builder()
reset_schema(b, S)
query(b, f"USE {S}")
DESIGNS = {
    "hidden_clustered": {"ddl": "CREATE TABLE t (a INT NOT NULL, b INT NOT NULL, KEY k_a (a))", "fill": "INSERT INTO t SELECT MOD(n+{off},1000), n FROM bench.seq1m WHERE n<=500000",
                         "forms": {"force_primary": "FORCE INDEX (PRIMARY)", "ignore_secondary": "IGNORE INDEX (k_a)", "default": "", "force_secondary": "FORCE INDEX (k_a)"},
                         "loads": [("INSERT INTO req_nopk.t VALUES (MOD(%s,1000), 0)", 2, 25), ("DELETE FROM req_nopk.t WHERE a=MOD(%s,1000) LIMIT 1", 2, 25)], "read": "SELECT * FROM req_nopk.t WHERE a=MOD(%s,1000) LIMIT 1"},
    "promoted_unique": {"ddl": "CREATE TABLE t (code BIGINT NOT NULL, v INT NOT NULL, UNIQUE KEY uk_code (code), KEY k_v (v))", "fill": "INSERT INTO t SELECT n+{off}, MOD(n,1000) FROM bench.seq1m WHERE n<=500000",
                        "forms": {"force_primary": "FORCE INDEX (PRIMARY)", "ignore_secondary": "IGNORE INDEX (k_v)", "default": "", "force_promoted_key": "FORCE INDEX (uk_code)", "force_secondary": "FORCE INDEX (k_v)"},
                        "loads": [("UPDATE req_nopk.t SET v=v+1 WHERE code=%s", 4, 25)], "read": "SELECT * FROM req_nopk.t WHERE code=%s"},
}

for design, cfg in DESIGNS.items():
    with r.section(design) as d:
        query(b, "DROP TABLE IF EXISTS t")
        query(b, cfg["ddl"])
        for off in range(0, 5_000_000, 500_000):
            query(b, cfg["fill"].format(off=off))
        query(b, "ANALYZE TABLE t")
        d["sizes"] = [list(x) for x in query(b, "SELECT DATA_LENGTH, INDEX_LENGTH FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME='t'", (S,))]
        loads = [Workload("req_nopk.t", max_id=5_000_000, head=5_000_000, readers=1 if i == 0 else 0, writers=w, rate=rate, write_sql=sql, read_sql=cfg["read"]) for i, (sql, w, rate) in enumerate(cfg["loads"])]
        for w in loads:
            w.start()
        pin(METHOD_CPU)
        time.sleep(5)
        c = connect(db=S, read_timeout=90)
        query(c, "SET SESSION max_execution_time=0")
        for name, clause in cfg["forms"].items():
            sql = f"SELECT /*+ MAX_EXECUTION_TIME(60000) */ COUNT(*) FROM t {clause}"
            plan = sql_try(c, "EXPLAIN " + sql)
            runs = []
            for _ in range(3):
                res = sql_try(c, sql)
                runs.append({"ok": res["ok"], "errno": res.get("errno"), "ms": res["ms"], "rows": (res.get("rows") or [[None]])[0][0]})
                if not res["ok"]:
                    break
            d[name] = {"sql": sql, "explain_ok": plan["ok"], "explain_errno": plan.get("errno"), "explain_type_key": [str(x) for x in (plan.get("rows") or [[None] * 12])[0][4:8]], "runs": runs}
            r.save()
            print(design, name, runs, flush=True)
        c.close()
        for w in loads:
            w.finish()
query(b, f"DROP DATABASE {S}")
r.finish()
