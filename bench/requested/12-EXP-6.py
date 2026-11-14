#!/usr/bin/env python3
"""Answers EXP-6 and FIND-R3-HINT-01: the guideline's statements exactly as printed, with db/t/columns substituted.

The statements here are copies of the guideline's text (the bench container cannot read docs/). Each is reported with its
result, so a typo or a wrong column shows up as an error. Read only, except the session settings.
"""
import re, threading, time
import pymysql
from lablib import REPLICA, connect, query
from reqlib import Results, guard, session_status, sql_try

ID = "12-EXP-6"
guard(ID, 6)
r = Results(ID, ["EXP-6", "EXP-O2-1", "FIND-STEP-01", "FIND-R3-HINT-01", "FIND-SOL-ISO-01"])
c = connect(read_timeout=120)


def literal(d, name, sql, args=None, conn=None):
    d[name] = {"sql": sql, **sql_try(conn or c, sql, args)}
    return d[name]


with r.section("SQL0") as d:
    for s in ("SET SESSION autocommit = 1", "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ", "SET SESSION lock_wait_timeout = 3",
              "SET SESSION time_zone = '+00:00'", "SET NAMES utf8mb4", "SET SESSION max_execution_time = 1000"):
        literal(d, s, s)
    literal(d, "check", "SELECT VERSION(), @@server_uuid, @@read_only, @@autocommit, @@transaction_isolation, @@time_zone, @@character_set_results")

with r.section("SQLM") as d:
    m = connect(read_timeout=20)
    literal(d, "mdl", "SELECT ENABLED FROM performance_schema.setup_instruments WHERE NAME = 'wait/lock/metadata/sql/mdl'", conn=m)
    literal(d, "hll", "SELECT `count` FROM information_schema.INNODB_METRICS WHERE name = 'trx_rseg_history_len'", conn=m)
    literal(d, "trx", "SELECT COUNT(*) FROM information_schema.INNODB_TRX", conn=m)

with r.section("SQL1") as d:
    literal(d, "expiry", "SET SESSION information_schema_stats_expiry = 0")
    for t in ("t_dense", "d_soft_delete"):
        literal(d, "tables_" + t, f"""SELECT TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH, ENGINE, TABLE_TYPE
FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'bench' AND TABLE_NAME = '{t}'""")
        literal(d, "explain_" + t, f"EXPLAIN SELECT COUNT(*) FROM {t}")
    query(c, "SET SESSION information_schema_stats_expiry = DEFAULT")

with r.section("SQL2") as d:
    literal(d, "small_table", "SELECT /*+ MAX_EXECUTION_TIME(1000) */ COUNT(*) FROM d_gipk")
    d["t_dense_1000ms"] = []
    for _ in range(5):
        d["t_dense_1000ms"].append(sql_try(c, "SELECT /*+ MAX_EXECUTION_TIME(1000) */ COUNT(*) FROM t_dense"))
    d["t_dense_500ms"] = []
    for _ in range(5):
        d["t_dense_500ms"].append(sql_try(c, "SELECT /*+ MAX_EXECUTION_TIME(500) */ COUNT(*) FROM t_dense"))

with r.section("SQL3") as d:
    q3 = """SELECT s.INDEX_NAME, s.SEQ_IN_INDEX, s.COLUMN_NAME, s.COLLATION, s.NON_UNIQUE, s.NULLABLE,
       s.INDEX_TYPE, s.SUB_PART, s.IS_VISIBLE, s.EXPRESSION, c.DATA_TYPE, c.EXTRA
FROM information_schema.STATISTICS s
LEFT JOIN information_schema.COLUMNS c
  ON c.TABLE_SCHEMA = s.TABLE_SCHEMA AND c.TABLE_NAME = s.TABLE_NAME AND c.COLUMN_NAME = s.COLUMN_NAME
WHERE s.TABLE_SCHEMA = 'bench' AND s.TABLE_NAME = '{t}' ORDER BY s.INDEX_NAME, s.SEQ_IN_INDEX"""
    for t in ("t_dense", "t_composite", "t_nopk", "t_part", "d_gipk"):
        res = sql_try(c, q3.format(t=t))
        res["rows"] = [list(x) for x in query(c, q3.format(t=t))]
        d["indexes_" + t] = res
        literal(d, "pages_" + t, f"""SELECT index_name, stat_value AS pages FROM mysql.innodb_index_stats
WHERE database_name = 'bench' AND table_name = '{t}' AND stat_name = 'size'""")
    literal(d, "pages_partition_names", "SELECT DISTINCT table_name FROM mysql.innodb_index_stats WHERE database_name = 'bench' AND table_name LIKE 't\\_part%'")

with r.section("SQL4_form_A") as d:
    # The printed form with a=tenant_id, b=seq, N=10000, i=PRIMARY.
    first = literal(d, "first", """SELECT /*+ MAX_EXECUTION_TIME(500) */ tenant_id, seq FROM t_composite FORCE INDEX (`PRIMARY`)
WHERE tenant_id IS NOT NULL ORDER BY tenant_id, seq LIMIT 1 OFFSET 9999""")
    a, b = query(c, """SELECT /*+ MAX_EXECUTION_TIME(500) */ tenant_id, seq FROM t_composite FORCE INDEX (`PRIMARY`)
WHERE tenant_id IS NOT NULL ORDER BY tenant_id, seq LIMIT 1 OFFSET 9999""")[0]
    literal(d, "mid", """SELECT /*+ MAX_EXECUTION_TIME(500) */ tenant_id, seq FROM t_composite FORCE INDEX (`PRIMARY`)
WHERE tenant_id > %s OR (tenant_id = %s AND seq > %s) ORDER BY tenant_id, seq LIMIT 1 OFFSET 9999""", (a, a, b))
    literal(d, "tail_near_end", """SELECT /*+ MAX_EXECUTION_TIME(500) */ COUNT(*) FROM t_composite FORCE INDEX (`PRIMARY`)
WHERE tenant_id > %s OR (tenant_id = %s AND seq > %s)""", (990, 990, 0))
    query(c, "SET SESSION explain_json_format_version = 1")
    literal(d, "rule2_explain", """EXPLAIN FORMAT=JSON SELECT tenant_id, seq FROM t_composite FORCE INDEX (`PRIMARY`)
WHERE tenant_id > %s OR (tenant_id = %s AND seq > %s) ORDER BY tenant_id, seq LIMIT 1 OFFSET 9999""", (a, a, b))
    literal(d, "rule3_roundtrip", "SELECT /*+ MAX_EXECUTION_TIME(500) */ 1 FROM t_composite FORCE INDEX (`PRIMARY`) WHERE tenant_id = %s AND seq = %s LIMIT 1", (a, b))
    literal(d, "probe", "SELECT 9007199254740993 AS probe")
    literal(d, "probe_back", "SELECT CAST(%s AS CHAR) = '9007199254740993' AS ok", (9007199254740993,))
    literal(d, "probe_unsigned", "SELECT 18446744073709551615 AS probe")
    literal(d, "secondary_mid", """SELECT /*+ MAX_EXECUTION_TIME(500) */ created_at, id FROM t_events FORCE INDEX (`k_created`)
WHERE created_at > %s OR (created_at = %s AND id > %s) ORDER BY created_at, id LIMIT 1 OFFSET 9999""", ("2026-01-01 00:10:00", "2026-01-01 00:10:00", 0))

with r.section("SQL4_form_B") as d:
    upper = query(c, "SELECT /*+ MAX_EXECUTION_TIME(500) */ a FROM t_nopk FORCE INDEX (`k_a`) WHERE a IS NOT NULL ORDER BY a LIMIT 1 OFFSET 9999")[0][0]
    literal(d, "upper_first", "SELECT /*+ MAX_EXECUTION_TIME(500) */ a FROM t_nopk FORCE INDEX (`k_a`) WHERE a IS NOT NULL ORDER BY a LIMIT 1 OFFSET 9999")
    literal(d, "upper_mid", "SELECT /*+ MAX_EXECUTION_TIME(500) */ a FROM t_nopk FORCE INDEX (`k_a`) WHERE a > %s ORDER BY a LIMIT 1 OFFSET 9999", (upper,))
    literal(d, "range_first", "SELECT /*+ MAX_EXECUTION_TIME(500) */ COUNT(*) FROM t_nopk FORCE INDEX (`k_a`) WHERE a IS NOT NULL AND a <= %s", (upper,))
    literal(d, "range_mid", "SELECT /*+ MAX_EXECUTION_TIME(500) */ COUNT(*) FROM t_nopk FORCE INDEX (`k_a`) WHERE a > %s AND a <= %s", (upper, upper + 2))
    literal(d, "tail", "SELECT /*+ MAX_EXECUTION_TIME(500) */ COUNT(*) FROM t_nopk FORCE INDEX (`k_a`) WHERE a > %s", (997,))
    literal(d, "null_count", "SELECT /*+ MAX_EXECUTION_TIME(500) */ COUNT(*) FROM t_uniq_nullable FORCE INDEX (`uk_code`) WHERE code IS NULL")

with r.section("limits_on_every_statement_kind") as d:
    # Statement kinds, each forced to run long enough to reach its limit: 1000 ms and 500 ms, five times each.
    kinds = {"boundary": "SELECT /*+ MAX_EXECUTION_TIME({ms}) */ created_at, id FROM t_dense FORCE INDEX (`k_created`) WHERE created_at IS NOT NULL ORDER BY created_at, id LIMIT 1 OFFSET 90000000",
             "null_count_shape": "SELECT /*+ MAX_EXECUTION_TIME({ms}) */ COUNT(*) FROM t_dense FORCE INDEX (`k_created`) WHERE created_at IS NOT NULL",
             "tail": "SELECT /*+ MAX_EXECUTION_TIME({ms}) */ COUNT(*) FROM t_dense FORCE INDEX (`k_created`) WHERE created_at > '2020-01-01'",
             "roundtrip_shape": "SELECT /*+ MAX_EXECUTION_TIME({ms}) */ 1 FROM t_dense WHERE note = 'no such value' LIMIT 1"}
    for name, sql in kinds.items():
        for ms in (1000, 500):
            runs = []
            for _ in range(5):
                before = session_status(c, "Max_execution_time_exceeded").get("Max_execution_time_exceeded", 0)
                res = sql_try(c, sql.format(ms=ms))
                res["timer_counter_delta"] = session_status(c, "Max_execution_time_exceeded").get("Max_execution_time_exceeded", 0) - before
                res.pop("rows", None)
                runs.append(res)
            d[f"{name}_{ms}"] = runs
            r.save()

with r.section("session_limit_and_lost_hint") as d:
    # What a statement does when the hint is missing, present, or larger than the session limit. Slowest shape: full index count.
    long_sql = "COUNT(*) FROM t_dense FORCE INDEX (`k_created`)"
    arms = {"hint_only_session_0": (0, f"SELECT /*+ MAX_EXECUTION_TIME(1000) */ {long_sql}"),
            "session_1000_hint_lost": (1000, f"SELECT {long_sql}"),
            "session_1000_hint_3000": (1000, f"SELECT /*+ MAX_EXECUTION_TIME(3000) */ {long_sql}"),
            "session_3000_hint_1000": (3000, f"SELECT /*+ MAX_EXECUTION_TIME(1000) */ {long_sql}")}
    for name, (session, sql) in arms.items():
        s = connect(read_timeout=120)
        query(s, f"SET SESSION max_execution_time = {session}")
        d[name] = [sql_try(s, sql) for _ in range(5)]
        for x in d[name]:
            x.pop("rows", None)
        s.close()
    # No limit at all: this is the state the "long success" rule must catch. A killer ends it after 4 s; the statement is otherwise unlimited.
    s = connect(read_timeout=120)
    target_id = s.thread_id()
    kill_record = {"target_id": target_id, "fired": False}
    def kill_fixed_target(thread_id=target_id):
        admin = connect(read_timeout=10)
        try:
            kill_record["fired"] = True
            kill_record["result"] = sql_try(admin, f"KILL QUERY {thread_id}")
        finally:
            admin.close()
    timer = threading.Timer(4, kill_fixed_target)
    timer.start()
    try:
        d["no_limit_killed_at_4s"] = sql_try(s, f"SELECT {long_sql}")
    finally:
        timer.cancel()
        timer.join()
        s.close()
    d["no_limit_killed_at_4s"]["kill_timer"] = kill_record
    # A statement that succeeds well past the printed limit, and the check a job can run on it.
    s = connect(read_timeout=120)
    t = time.monotonic()
    value = query(s, "SELECT COUNT(*) FROM order_items FORCE INDEX (`PRIMARY`)")[0][0]
    elapsed = (time.monotonic() - t) * 1000
    d["long_success_rule"] = {"limit_printed_ms": 1000, "elapsed_ms": round(elapsed), "value": value, "rule_fires": elapsed > 2000}

with r.section("monitoring_statements") as d:
    m = connect(read_timeout=20)
    literal(d, "metadata_locks", """SELECT OBJECT_SCHEMA, OBJECT_NAME, LOCK_TYPE, LOCK_STATUS, OWNER_THREAD_ID
FROM performance_schema.metadata_locks
WHERE OBJECT_TYPE = 'TABLE' AND OBJECT_SCHEMA = 'bench' AND OBJECT_NAME = 't_dense'""", conn=m)
    literal(d, "threads", """SELECT PROCESSLIST_ID, PROCESSLIST_STATE, PROCESSLIST_TIME, LEFT(PROCESSLIST_INFO, 120)
FROM performance_schema.threads
WHERE PROCESSLIST_STATE LIKE 'Waiting for table%'""", conn=m)
    literal(d, "innodb_trx", """SELECT trx_mysql_thread_id, trx_isolation_level, trx_rows_locked, trx_lock_structs,
       TIMESTAMPDIFF(SECOND, trx_started, NOW()) AS age_s, LEFT(trx_query, 120) AS q
FROM information_schema.INNODB_TRX WHERE trx_rows_locked > 1000 ORDER BY trx_rows_locked DESC""", conn=m)
    literal(d, "table_id", "SELECT NAME, TABLE_ID FROM information_schema.INNODB_TABLES WHERE NAME = 'bench/t_part' OR NAME LIKE 'bench/t\\_part#p#%'", conn=m)
    literal(d, "fk", """SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE REFERENCED_TABLE_NAME IS NOT NULL
  AND ((TABLE_SCHEMA='bench' AND TABLE_NAME='orders') OR (REFERENCED_TABLE_SCHEMA='bench' AND REFERENCED_TABLE_NAME='orders'))""", conn=m)
    literal(d, "routines", "SELECT ROUTINE_SCHEMA, ROUTINE_NAME FROM information_schema.ROUTINES WHERE ROUTINE_DEFINITION LIKE '%COUNT(%'", conn=m)
    literal(d, "triggers", "SELECT TRIGGER_SCHEMA, TRIGGER_NAME FROM information_schema.TRIGGERS WHERE ACTION_STATEMENT LIKE '%COUNT(%'", conn=m)
    literal(d, "events", "SELECT EVENT_SCHEMA, EVENT_NAME FROM information_schema.EVENTS WHERE EVENT_DEFINITION LIKE '%COUNT(%'", conn=m)
    literal(d, "status_threads_running", "SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='Threads_running'", conn=m)
    literal(d, "handler_diff", "SHOW SESSION STATUS LIKE 'Handler_read%'")
    rc = connect(REPLICA, read_timeout=20)
    literal(d, "replica_status", "SHOW REPLICA STATUS", conn=rc)
    d["replica_status"]["rows"] = "(present)" if d["replica_status"].get("rows") else d["replica_status"].get("rows")
    literal(d, "replica_gtid", "SELECT @@GLOBAL.gtid_executed", conn=rc)

with r.section("job_code_search") as d:
    # rg is not in the bench image, so the printed pattern is applied with Python's re in the multiline, case-insensitive mode -U -i gives.
    pat = re.compile(r"(insert|replace|update|set|create|do)\b[^;]*count\s*\(|count\s*\([^;]*for\s+(share|update)|lock\s+tables", re.I | re.S)
    samples = {"insert_select": ("INSERT INTO s\n SELECT COUNT(*) FROM t;", True), "set_var": ("SET @c = (SELECT\n COUNT(*) FROM t);", True), "for_share": ("SELECT COUNT(*) FROM t\n FOR SHARE;", True),
               "ctas": ("CREATE TEMPORARY TABLE x AS SELECT COUNT(*) FROM t;", True), "lock": ("LOCK TABLES t READ;", True), "do": ("DO (SELECT COUNT(*) FROM t);", True),
               "plain": ("SELECT COUNT(*) FROM t;", False), "plain_after_set_stmt": ("SET NAMES utf8mb4;\nSELECT COUNT(*) FROM t;", False),
               "orm_like": ("db.execute('select count(*) from t for update')", True), "update_elsewhere": ("update_count(table); stats = count(rows)", True)}
    d["note"] = "rg itself was not executed; this applies the same pattern."
    d["results"] = {k: {"expected": want, "matched": bool(pat.search(text))} for k, (text, want) in samples.items()}
r.finish()
