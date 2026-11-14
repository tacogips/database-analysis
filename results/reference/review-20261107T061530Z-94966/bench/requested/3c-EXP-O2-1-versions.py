#!/usr/bin/env python3
"""Answers EXP-O2-1 (edge cases of the written procedure), EXP-R2, EXP-R3, EXP-R6 (float, DST, zero date, lossy cursors), EXP-7's design arms and FIND-R3-LOOPEDGE-01, on a fresh small server of each version: argv is <host> <version>.

Replaces script 11, which lost everything after R3_desc to an unbounded walk. Every walk now has a step ceiling, every arm has an alarm, and results are saved after each arm,
so an arm that loops is named by its own result. No-round-trip negative controls keep their ceiling on purpose: reaching it with a wrong sum is their finding.
"""
import json, random, sys, threading, time
import pymysql
from countlib import forced_plan, indexes, session_setup, split_count, split_plans, walk_count
from lablib import connect, query
from reqlib import Results, bounded, guard, session_status

HOST, VERSION = sys.argv[1], sys.argv[2]
ID = f"3c-EXP-O2-1-versions-{VERSION}"
guard(ID, 6)
r = Results(ID, ["EXP-O2-1", "EXP-R2", "EXP-R3", "EXP-R6", "EXP-7", "FIND-R3-LOOPEDGE-01", "FIND-R2-CURSOR-01", "FIND-KEY-TYPE-01", "FIND-O6-WALK-CEILING-01"], version=VERSION)
query(connect(HOST, db="mysql"), "CREATE DATABASE IF NOT EXISTS bench")
a = connect(HOST, read_timeout=120)
query(a, "SET SESSION cte_max_recursion_depth=300000")
query(a, "CREATE TABLE seq (n INT PRIMARY KEY)")
query(a, "INSERT INTO seq WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n<200000) SELECT n FROM s")
r.data["server"] = query(a, "SELECT VERSION()")[0][0]
ARM_SECONDS = 25
KEEP = ("status", "rows", "partial_rows", "null_rows", "steps", "statements", "plan_check", "errors", "roundtrips", "width_end", "stmt_ms_max", "seconds_total")


def build(*sql):
    for s in sql:
        query(a, s)


def truth(t):
    return query(a, f"SELECT COUNT(*) FROM `{t}`")[0][0]


def new():
    c = connect(HOST, read_timeout=20)
    session_setup(c)
    return c


def run(t, plan=None, width=100, **kw):
    """The procedure on one table with the plan SQL 3 chooses, or a forced one. The step ceiling is three times what the table needs."""
    c = new()
    plans = split_plans(c, "bench", t)
    p = plan or (plans["plans"][0] if plans["plans"] else None)
    want = truth(t)
    kw.setdefault("duty", None)
    kw.setdefault("max_steps", max(want // width * 3, 30))
    res = {"truth": want, "rejected": plans["rejected"], "chosen": p and p["index"], "form": p and p["form"], "ceiling": kw["max_steps"]}
    if not p:
        res["status"] = "no_eligible_index"
        return res
    s = split_count(c, t, p, width=width, keep_sql=True, db="bench", **kw)
    res.update({k: s.get(k) for k in KEEP})
    res["correct"] = s["rows"] == want if s["complete"] else None
    res["every_statement_has_hint"] = all("MAX_EXECUTION_TIME(500)" in q for q in s.get("sql", {}).values())
    c.close()
    return res


def arm(d, name, fn, *args, **kw):
    """One arm, one alarm, one save."""
    d[name] = bounded(ARM_SECONDS, fn, *args, **kw)
    r.save()
    print(name, (d[name] or {}).get("status") if isinstance(d[name], dict) else d[name], flush=True)


with r.section("sizes_and_nulls") as d:
    build("CREATE TABLE e_empty (id BIGINT PRIMARY KEY)",
          "CREATE TABLE e_lt (id BIGINT PRIMARY KEY)", "INSERT INTO e_lt VALUES (1),(2),(3),(4),(5)",
          "CREATE TABLE e_exact (id BIGINT PRIMARY KEY)", "INSERT INTO e_exact SELECT n FROM seq WHERE n<=300",
          "CREATE TABLE e_neg (id BIGINT PRIMARY KEY)", "INSERT INTO e_neg SELECT n-5000 FROM seq WHERE n<=10000",
          "CREATE TABLE e_null_few (id BIGINT PRIMARY KEY, a INT NULL, KEY k_a (a))", "INSERT INTO e_null_few SELECT n, IF(MOD(n,1000)=0,NULL,n) FROM seq WHERE n<=10000",
          "CREATE TABLE e_null_many (id BIGINT PRIMARY KEY, a INT NULL, KEY k_a (a))", "INSERT INTO e_null_many SELECT n, IF(n<=8000,NULL,n) FROM seq WHERE n<=10000",
          "CREATE TABLE e_nopk_null (a INT NULL, b INT NOT NULL, KEY k_a (a))", "INSERT INTO e_nopk_null SELECT IF(MOD(n,3)=0,NULL,MOD(n,50)), n FROM seq WHERE n<=10000",
          "CREATE TABLE e_uniq_null (code BIGINT NULL, v INT NOT NULL, UNIQUE KEY uk (code))", "INSERT INTO e_uniq_null SELECT IF(MOD(n,3)=0,NULL,n), n FROM seq WHERE n<=10000",
          "CREATE TABLE e_tie (a INT NOT NULL, b INT NOT NULL, KEY k_a (a))", "INSERT INTO e_tie SELECT IF(n<=50000,1,n), n FROM seq WHERE n<=60000",
          "ANALYZE TABLE e_lt, e_exact, e_neg, e_null_few, e_null_many, e_nopk_null, e_uniq_null, e_tie")
    for t in ("e_empty", "e_lt", "e_exact", "e_neg", "e_nopk_null", "e_uniq_null", "e_tie"):
        arm(d, t, run, t)
    c = new()
    for t in ("e_null_few", "e_null_many"):
        arm(d, t + "_primary", run, t)
        arm(d, t + "_forced_nullable_index", run, t, plan=forced_plan(c, "bench", t, "k_a"))
    c.close()

with r.section("R2_nullable_composite") as d:
    build("CREATE TABLE e_r2 (id BIGINT PRIMARY KEY, tenant_id INT NOT NULL, deleted_at DATETIME NULL, KEY k (tenant_id, deleted_at))",
          "INSERT INTO e_r2 SELECT n, MOD(n,5), IF(MOD(n,2)=0,NULL,'2026-01-01') FROM seq WHERE n<=20000", "ANALYZE TABLE e_r2")
    arm(d, "guideline", run, "e_r2")

    def old_walker():
        c = connect(HOST, read_timeout=20)
        idx = indexes(c, "bench", "e_r2")
        old = walk_count(c, "bench", "e_r2", idx["k"], idx, width=100)
        c.close()
        return {"rows": old["rows"], "truth": truth("e_r2"), "correct": old["rows"] == truth("e_r2")}
    arm(d, "old_walker_on_k", old_walker)

with r.section("R3_desc") as d:
    designs = {"k_asc_pk_asc": "CREATE TABLE {t} (id BIGINT NOT NULL, a INT NOT NULL, PRIMARY KEY (id), KEY k (a))",
               "k_desc_pk_asc": "CREATE TABLE {t} (id BIGINT NOT NULL, a INT NOT NULL, PRIMARY KEY (id), KEY k (a DESC))",
               "k_asc_pk_desc": "CREATE TABLE {t} (id BIGINT NOT NULL, a INT NOT NULL, PRIMARY KEY (id DESC), KEY k (a))",
               "k_desc_pk_desc": "CREATE TABLE {t} (id BIGINT NOT NULL, a INT NOT NULL, PRIMARY KEY (id DESC), KEY k (a DESC))",
               "mixed_explicit": "CREATE TABLE {t} (id BIGINT NOT NULL, a INT NOT NULL, PRIMARY KEY (id), KEY k (a DESC, id ASC))"}

    def desc_arm(name, ddl):
        t = "e_d_" + name
        build(ddl.format(t=t), f"INSERT INTO {t} SELECT n, MOD(n,500) FROM seq WHERE n<=50000", f"ANALYZE TABLE {t}")
        c = new()
        res = {"guideline": run(t), "forced": {}}
        walks = {"asc_predicate": (f"SELECT a,id FROM {t} FORCE INDEX (k) WHERE a > %s OR (a = %s AND id > %s) ORDER BY a,id LIMIT 1 OFFSET 999", (250, 250, 100)),
                 "desc_predicate": (f"SELECT a,id FROM {t} FORCE INDEX (k) WHERE a < %s OR (a = %s AND id < %s) ORDER BY a DESC,id DESC LIMIT 1 OFFSET 999", (250, 250, 49000)),
                 "mixed_predicate": (f"SELECT a,id FROM {t} FORCE INDEX (k) WHERE a < %s OR (a = %s AND id > %s) ORDER BY a DESC,id ASC LIMIT 1 OFFSET 999", (250, 250, 100))}
        for label, (sql, args) in walks.items():
            query(c, "SET SESSION explain_json_format_version = 1")
            plan = json.loads(query(c, "EXPLAIN FORMAT=JSON " + sql, args)[0][0])["query_block"]
            order = plan.get("ordering_operation") or {}
            tab = order.get("table") or plan.get("table") or {}
            before = session_status(c, "Handler_%")
            sorts = session_status(c, "Sort_rows")
            t0 = time.monotonic()
            query(c, sql, args)
            ms = (time.monotonic() - t0) * 1000
            after = session_status(c, "Handler_%")
            res["forced"][label] = {"filesort": bool(order.get("using_filesort")), "access": tab.get("access_type"), "used_key_parts": tab.get("used_key_parts"), "ms": round(ms, 2),
                                    "handler_read": {k: after[k] - before.get(k, 0) for k in ("Handler_read_next", "Handler_read_prev", "Handler_read_rnd_next", "Handler_read_key", "Handler_read_rnd") if k in after},
                                    "sort_rows": session_status(c, "Sort_rows").get("Sort_rows", 0) - sorts.get("Sort_rows", 0)}
        c.close()
        res["status"] = "measured"
        return res
    for name, ddl in designs.items():
        arm(d, name, desc_arm, name, ddl)

with r.section("R6_types") as d:
    build("CREATE TABLE e_float (f FLOAT NOT NULL, id INT NOT NULL, PRIMARY KEY (f, id))",
          "INSERT INTO e_float SELECT MOD(n,300)/10.0+0.1, n FROM seq WHERE n<=10000",
          "CREATE TABLE e_double (f DOUBLE NOT NULL, id INT NOT NULL, PRIMARY KEY (f, id))",
          "INSERT INTO e_double SELECT MOD(n,300)/10.0+0.1, n FROM seq WHERE n<=10000",
          "CREATE TABLE e_bit (b BIT(16) NOT NULL, id INT NOT NULL, PRIMARY KEY (b, id))", "INSERT INTO e_bit SELECT MOD(n,300), n FROM seq WHERE n<=10000",
          "CREATE TABLE e_set (s SET('a','b','c') NOT NULL, id INT NOT NULL, PRIMARY KEY (s, id))", "INSERT INTO e_set SELECT ELT(1+MOD(n,3),'a','b','a,c'), n FROM seq WHERE n<=10000",
          "CREATE TABLE e_ts (ts TIMESTAMP(0) NOT NULL, id INT NOT NULL, PRIMARY KEY (ts, id))",
          "SET SESSION time_zone='+00:00'",
          "INSERT INTO e_ts SELECT TIMESTAMPADD(SECOND, n*6, '2026-11-01 04:00:00'), n FROM seq WHERE n<=6000",
          "SET SESSION sql_mode=''",
          "CREATE TABLE e_zero (d DATETIME NOT NULL, id INT NOT NULL, PRIMARY KEY (d, id))",
          "INSERT INTO e_zero SELECT IF(MOD(n,10)=0,'0000-00-00 00:00:00','2026-01-01 00:00:00'), n FROM seq WHERE n<=2000",
          "SET SESSION sql_mode=DEFAULT",
          "CREATE TABLE e_utf (code VARCHAR(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL PRIMARY KEY, v INT NOT NULL)",
          "INSERT INTO e_utf SELECT CONCAT(ELT(1+MOD(n,3),'あ','😀','z'), n), n FROM seq WHERE n<=3000",
          "CREATE TABLE e_big (id BIGINT PRIMARY KEY)",
          "INSERT INTO e_big SELECT 9007199254740992+n FROM seq WHERE n<=2000",
          "CREATE TABLE e_dec (id DECIMAL(30,6) NOT NULL PRIMARY KEY)",
          # n*0.000001 has an exact scale of 6. The earlier n/1000000 depended on division precision and gave duplicates on 9.7 and 26.10.
          "INSERT INTO e_dec SELECT CAST(9007199254740992 AS DECIMAL(30,6)) + n*0.000001 FROM seq WHERE n<=2000",
          "CREATE TABLE e_date (d DATE NOT NULL, id INT NOT NULL, PRIMARY KEY (d, id))", "INSERT INTO e_date SELECT DATE_ADD('2020-01-01', INTERVAL MOD(n,500) DAY), n FROM seq WHERE n<=10000",
          "CREATE TABLE e_time (t TIME NOT NULL, id INT NOT NULL, PRIMARY KEY (t, id))", "INSERT INTO e_time SELECT SEC_TO_TIME(MOD(n,80000)), n FROM seq WHERE n<=10000",
          "CREATE TABLE e_year (y YEAR NOT NULL, id INT NOT NULL, PRIMARY KEY (y, id))", "INSERT INTO e_year SELECT 1901+MOD(n,200), n FROM seq WHERE n<=10000",
          "CREATE TABLE e_char (c CHAR(10) NOT NULL, id INT NOT NULL, PRIMARY KEY (c, id))", "INSERT INTO e_char SELECT CONCAT('a',MOD(n,300)), n FROM seq WHERE n<=10000",
          # PAD SPACE: 'x' and 'x ' compare equal but are different stored values.
          "CREATE TABLE e_pad (c VARCHAR(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL, id INT NOT NULL, PRIMARY KEY (c, id))", "INSERT INTO e_pad SELECT IF(MOD(n,2)=0,'x','x '), n FROM seq WHERE n<=5000",
          "ANALYZE TABLE e_float, e_double, e_bit, e_set, e_ts, e_zero, e_utf, e_big, e_dec, e_date, e_time, e_year, e_char, e_pad")
    d["e_dec_distinct"] = query(a, "SELECT COUNT(DISTINCT id), COUNT(*), MIN(id), MAX(id) FROM e_dec")[0]
    for t in ("e_date", "e_time", "e_year", "e_char", "e_pad"):
        arm(d, t, run, t)
    c = new()
    arm(d, "float_guideline", run, "e_float")
    arm(d, "float_forced_no_roundtrip", run, "e_float", plan=forced_plan(c, "bench", "e_float", "PRIMARY"), enforce_plan=False, check_roundtrip=False, check_advance=False)
    arm(d, "float_forced_with_roundtrip", run, "e_float", plan=forced_plan(c, "bench", "e_float", "PRIMARY"), enforce_plan=False)
    arm(d, "double_guideline", run, "e_double")
    arm(d, "bit_guideline", run, "e_bit")
    arm(d, "bit_forced", run, "e_bit", plan=forced_plan(c, "bench", "e_bit", "PRIMARY"), enforce_plan=False)
    arm(d, "set_guideline", run, "e_set")
    arm(d, "set_forced", run, "e_set", plan=forced_plan(c, "bench", "e_set", "PRIMARY"), enforce_plan=False)
    arm(d, "zero_date_guideline", run, "e_zero")
    arm(d, "timestamp_utc", run, "e_ts")
    zones = query(a, "SELECT COUNT(*) FROM mysql.time_zone_name WHERE Name='America/New_York'")[0][0]
    d["time_zone_tables_loaded"] = bool(zones)

    def zone_arm(**kw):
        z = new()
        query(z, "SET SESSION time_zone='America/New_York'")
        p = split_plans(z, "bench", "e_ts")["plans"][0]
        s = split_count(z, "e_ts", p, width=50, limit_ms=500, duty=None, max_steps=400, db="bench", **kw)
        z.close()
        return {"status": s["status"], "rows": s["rows"], "partial_rows": s["partial_rows"], "steps": s["steps"], "truth": truth("e_ts"), "correct": s["rows"] == truth("e_ts"), "errors": s.get("errors")}
    if zones:
        arm(d, "timestamp_dst_zone", zone_arm)
        arm(d, "timestamp_dst_zone_no_roundtrip", zone_arm, check_roundtrip=False, check_advance=False)

    def lossy_arm(**kw):
        lossy = pymysql.connect(host=HOST, user="root", password="lab", database="bench", autocommit=True, charset="latin1", read_timeout=20)
        query(lossy, "SET SESSION max_execution_time = 1000")
        p = split_plans(c, "bench", "e_utf")["plans"][0]
        s = split_count(lossy, "e_utf", p, width=50, limit_ms=500, duty=None, max_steps=200, db="bench", **kw)
        lossy.close()
        return {"status": s["status"], "rows": s["rows"], "partial_rows": s["partial_rows"], "steps": s["steps"], "truth": truth("e_utf"), "correct": s["rows"] == truth("e_utf"), "errors": s.get("errors")}
    arm(d, "utf8mb4_guideline", run, "e_utf")
    arm(d, "latin1_with_roundtrip", lossy_arm)
    arm(d, "latin1_no_roundtrip", lossy_arm, check_roundtrip=False, check_advance=False)

    def codec_arm(table, width, codec, **kw):
        p = split_plans(c, "bench", table)["plans"][0]
        s = split_count(c, table, p, width=width, limit_ms=500, duty=None, cursor_codec=codec, max_steps=truth(table) // width * 3, db="bench", **kw)
        return {"status": s["status"], "rows": s["rows"], "partial_rows": s["partial_rows"], "steps": s["steps"], "truth": truth(table), "correct": s["rows"] == truth(table), "plan_check": s["plan_check"], "errors": s.get("errors")}
    arm(d, "bigint_exact", run, "e_big")
    arm(d, "bigint_through_double", codec_arm, "e_big", 7, lambda v: int(float(v)))
    arm(d, "decimal_literal", codec_arm, "e_dec", 7, None)
    arm(d, "decimal_quoted_string", codec_arm, "e_dec", 7, str)
    arm(d, "decimal_through_double", codec_arm, "e_dec", 7, float)
    probe = query(c, "SELECT 9007199254740993 AS probe")[0][0]
    d["probe_exact"] = query(c, "SELECT CAST(%s AS CHAR) = '9007199254740993' AS ok", (probe,))[0][0]
    d["probe_through_double"] = query(c, "SELECT CAST(%s AS CHAR) = '9007199254740993' AS ok", (int(float(probe)),))[0][0]
    c.close()

with r.section("boundary_deletes_and_errors") as d:
    # FIND-R3-LOOPEDGE-01: how often does the round trip abort under uniform deletes, and how much does it read?
    build("CREATE TABLE e_del (code VARCHAR(20) NOT NULL PRIMARY KEY, v INT NOT NULL)")

    def delete_trial(trial):
        query(a, "TRUNCATE e_del")
        query(a, "INSERT INTO e_del SELECT CONCAT('k',LPAD(n,8,'0')), n FROM seq WHERE n<=100000")
        c = new()
        p = split_plans(c, "bench", "e_del")["plans"][0]
        done = threading.Event()
        dels = [0]

        def deleter():
            w = connect(HOST, read_timeout=10)
            rng = random.Random(trial)
            while not done.is_set():
                query(w, "DELETE FROM e_del WHERE code=%s", (f"k{rng.randint(1, 100000):08d}",))
                dels[0] += 1
                time.sleep(.002)
            w.close()
        th = threading.Thread(target=deleter, daemon=True)
        th.start()
        try:
            s = split_count(c, "e_del", p, width=100, limit_ms=500, duty=None, max_steps=3000, db="bench")
        finally:
            done.set()
            th.join(5)
        c.close()
        return {"status": s["status"], "steps": s["steps"], "roundtrips": s["roundtrips"], "delete_statements": dels[0], "seconds": s["seconds_total"], "statements": s["statements"]}
    d["abort_under_deletes"] = [bounded(ARM_SECONDS, delete_trial, t) for t in range(5)]
    r.save()
    build("CREATE TABLE e_drop (id BIGINT PRIMARY KEY, a INT NOT NULL, KEY k (a))", "INSERT INTO e_drop SELECT n, n FROM seq WHERE n<=5000")

    def drop_after(n, sql):
        seen = [0]

        def hook(_k, _s, _a):
            seen[0] += 1
            if seen[0] == n:
                query(a, sql)
        return hook

    def dropped(index, sql):
        c = new()
        p = forced_plan(c, "bench", "e_drop", index)
        # The plan rule is off: last round it refused this small table first, so the drop never happened and the arm tested nothing.
        s = split_count(c, "e_drop", p, width=100, limit_ms=500, duty=None, before_statement=drop_after(4, sql), max_steps=200, db="bench", enforce_plan=False)
        c.close()
        return {"status": s["status"], "errors": s.get("errors"), "rows": s["rows"], "plan_check": s["plan_check"]}
    arm(d, "index_dropped_midway", dropped, "k", "ALTER TABLE e_drop DROP INDEX k")
    arm(d, "table_dropped_midway", dropped, "PRIMARY", "DROP TABLE e_drop")

with r.section("EXP7_designs") as d:
    build("CREATE TABLE e_3c (a INT NOT NULL, b INT NOT NULL, c INT NOT NULL, v INT NOT NULL, PRIMARY KEY (a,b,c))", "INSERT INTO e_3c SELECT MOD(n,7), MOD(n,11), n, n FROM seq WHERE n<=20000",
          "CREATE TABLE e_4c (a INT NOT NULL, b VARCHAR(8) NOT NULL, c DATETIME NOT NULL, e BIGINT NOT NULL, PRIMARY KEY (a,b,c,e))", "INSERT INTO e_4c SELECT MOD(n,3), CONCAT('s',MOD(n,5)), '2026-01-01', n FROM seq WHERE n<=20000",
          # A composite secondary index plus a composite primary key: the cursor is both, with no column in common.
          "CREATE TABLE e_sec_comp (p1 INT NOT NULL, p2 INT NOT NULL, s1 INT NOT NULL, s2 INT NOT NULL, v INT NOT NULL, PRIMARY KEY (p1,p2), KEY k_s (s1,s2))", "INSERT INTO e_sec_comp SELECT MOD(n,100), n, MOD(n,7), MOD(n,13), n FROM seq WHERE n<=30000",
          "CREATE TABLE e_func (id BIGINT PRIMARY KEY, a INT NOT NULL, KEY kf ((a+1)))", "INSERT INTO e_func SELECT n, n FROM seq WHERE n<=10000",
          "CREATE TABLE e_mv (id BIGINT PRIMARY KEY, j JSON NOT NULL, KEY km ((CAST(j->'$.t' AS UNSIGNED ARRAY))))", "INSERT INTO e_mv SELECT n, JSON_OBJECT('t', JSON_ARRAY(MOD(n,5), MOD(n,7)+10)) FROM seq WHERE n<=5000",
          "CREATE TABLE e_inv (id BIGINT PRIMARY KEY, a INT NOT NULL, KEY ki (a) INVISIBLE)", "INSERT INTO e_inv SELECT n, n FROM seq WHERE n<=5000",
          "CREATE TABLE e_gen (id BIGINT PRIMARY KEY, a INT NOT NULL, g INT GENERATED ALWAYS AS (a+1) STORED NOT NULL, KEY kg (g))", "INSERT INTO e_gen (id,a) SELECT n, n FROM seq WHERE n<=5000",
          "CREATE TABLE e_onupd (id BIGINT PRIMARY KEY, u DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6), KEY ku (u))", "INSERT INTO e_onupd (id) SELECT n FROM seq WHERE n<=5000",
          "CREATE TABLE e_defgen (id BIGINT PRIMARY KEY, u DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), KEY ku (u))", "INSERT INTO e_defgen (id) SELECT n FROM seq WHERE n<=5000",
          "ANALYZE TABLE e_3c, e_4c, e_sec_comp, e_func, e_mv, e_inv, e_gen, e_onupd, e_defgen",
          """CREATE TABLE e_part (id BIGINT NOT NULL, d DATE NOT NULL, v INT NOT NULL, PRIMARY KEY (id,d)) PARTITION BY RANGE (TO_DAYS(d))
             (PARTITION p1 VALUES LESS THAN (TO_DAYS('2026-02-01')), PARTITION p2 VALUES LESS THAN (TO_DAYS('2026-03-01')), PARTITION p3 VALUES LESS THAN MAXVALUE)""",
          "INSERT INTO e_part SELECT n, DATE_ADD('2026-01-01', INTERVAL MOD(n,90) DAY), n FROM seq WHERE n<=30000",
          """CREATE TABLE e_sub (id BIGINT NOT NULL, d DATE NOT NULL, PRIMARY KEY (id,d)) PARTITION BY RANGE (TO_DAYS(d)) SUBPARTITION BY HASH (id) SUBPARTITIONS 2
             (PARTITION p1 VALUES LESS THAN (TO_DAYS('2026-02-01')), PARTITION p2 VALUES LESS THAN MAXVALUE)""",
          "INSERT INTO e_sub SELECT n, DATE_ADD('2026-01-01', INTERVAL MOD(n,60) DAY) FROM seq WHERE n<=20000",
          "ANALYZE TABLE e_part, e_sub")
    for t in ("e_3c", "e_4c", "e_func", "e_mv", "e_inv", "e_gen", "e_onupd", "e_defgen"):
        arm(d, t, run, t)

    def sec_comp():
        # The cursor (s1,s2,p1,p2) through k_s: does the plan use all four parts, and does one step read about its width?
        c = new()
        p = forced_plan(c, "bench", "e_sec_comp", "k_s")
        res = run("e_sec_comp", plan=p, width=1000)
        mid = "SELECT s1,s2,p1,p2 FROM e_sec_comp FORCE INDEX (k_s) WHERE s1 > %s OR (s1 = %s AND (s2 > %s OR (s2 = %s AND (p1 > %s OR (p1 = %s AND p2 > %s))))) ORDER BY s1,s2,p1,p2 LIMIT 1 OFFSET 999"
        args = (3, 3, 5, 5, 50, 50, 100)
        before = session_status(c, "Handler_read_next")["Handler_read_next"]
        query(c, mid, args)
        res["handler_read_next_one_step"] = session_status(c, "Handler_read_next")["Handler_read_next"] - before
        c.close()
        return res
    arm(d, "composite_secondary_plus_composite_pk", sec_comp)

    def partitions():
        c = new()
        names = [x[0] for x in query(c, "SELECT DISTINCT PARTITION_NAME FROM information_schema.PARTITIONS WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='e_part'")]
        stats = [x[0] for x in query(c, "SELECT DISTINCT table_name FROM mysql.innodb_index_stats WHERE database_name='bench' AND table_name LIKE 'e\\_part%'")]
        p = split_plans(c, "bench", "e_part")["plans"][0]
        parts = {}
        for name in names:
            s = split_count(c, "e_part", p, width=500, limit_ms=500, duty=None, partition=name, keep_sql=True, max_steps=200, db="bench")
            parts[name] = {"status": s["status"], "rows": s["rows"], "plan_check": s["plan_check"], "truth": query(c, f"SELECT COUNT(*) FROM e_part PARTITION ({name})")[0][0], "sql_mid": s.get("sql", {}).get("mid")}
        c.close()
        return {"stats_names": stats, "parts": parts, "sum": sum(v["rows"] or 0 for v in parts.values()), "truth": truth("e_part"), "whole_table_split": run("e_part", width=500)}
    arm(d, "e_part", partitions)

    def subpartitions():
        c = new()
        subs = [x[0] for x in query(c, "SELECT SUBPARTITION_NAME FROM information_schema.PARTITIONS WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='e_sub'")]
        p = split_plans(c, "bench", "e_sub")["plans"][0]
        sp = {}
        for name in subs:
            s = split_count(c, "e_sub", p, width=500, limit_ms=500, duty=None, partition=name, max_steps=200, db="bench")
            sp[name] = {"status": s["status"], "rows": s["rows"]}
        c.close()
        return {"subpartitions": sp, "sum": sum(v["rows"] or 0 for v in sp.values()), "truth": truth("e_sub")}
    arm(d, "e_sub", subpartitions)

    def tenants():
        total = 0
        for i in range(1, 6):
            query(a, f"CREATE DATABASE tenant_{i}")
            query(a, f"CREATE TABLE tenant_{i}.orders (id BIGINT PRIMARY KEY)")
            query(a, f"INSERT INTO tenant_{i}.orders SELECT n FROM bench.seq WHERE n<={i * 1000}")
            total += i * 1000
        got = 0
        for i in range(1, 6):
            tc = connect(HOST, db=f"tenant_{i}", read_timeout=20)
            session_setup(tc)
            pl = split_plans(tc, f"tenant_{i}", "orders")["plans"][0]
            got += split_count(tc, "orders", pl, width=300, limit_ms=500, duty=None, max_steps=100, db=f"tenant_{i}")["rows"]
            tc.close()
        return {"sum": got, "truth": total, "correct": got == total}
    arm(d, "tenants", tenants)
r.finish()
