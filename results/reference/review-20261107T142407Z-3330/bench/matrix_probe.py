#!/usr/bin/env python3
"""Compare how each MySQL version runs and locks a whole-table count, on a small dataset.

Facts are probed rather than assumed: a statement that fails is reported with its error.
Usage: matrix_probe.py <host> <label>
"""
import json, sys, threading, time
import pymysql
from lablib import connect, query, save
from count_preflight import analyze
from countlib import choose_index, explain as count_explain, walk_count


def attempt(conn, sql, args=None):
    try:
        query(conn, sql, args)
        return "ok"
    except pymysql.MySQLError as e:
        return f"error {e.args[0]}: {str(e.args[1])[:90]}"


def variables(c):
    names = ["innodb_parallel_read_threads", "innodb_redo_log_capacity", "sql_generate_invisible_primary_key",
             "restrict_fk_on_non_standard_key", "explain_json_format_version", "information_schema_stats_expiry",
             "binlog_format", "binlog_row_image", "transaction_isolation", "innodb_adaptive_hash_index",
             "innodb_change_buffering", "innodb_old_blocks_time", "replica_parallel_workers", "replica_preserve_commit_order",
             "slave_parallel_workers", "lock_wait_timeout", "innodb_lock_wait_timeout", "max_execution_time",
             "log_bin", "gtid_mode", "authentication_policy", "default_authentication_plugin", "innodb_stats_on_metadata"]
    out = {}
    for n in names:
        r = query(c, "SHOW VARIABLES LIKE %s", (n,))
        out[n] = r[0][1] if r else None
    return out


def build(c):
    query(c, "SET SESSION foreign_key_checks=0")
    query(c, "SET SESSION cte_max_recursion_depth=300000")
    for ddl in [
        "CREATE TABLE seq (n INT PRIMARY KEY)",
        "CREATE TABLE hot (id BIGINT PRIMARY KEY, v INT NOT NULL, KEY k_v (v))",
        "CREATE TABLE parent (id BIGINT PRIMARY KEY, name VARCHAR(20) NOT NULL)",
        "CREATE TABLE child (id BIGINT PRIMARY KEY AUTO_INCREMENT, parent_id BIGINT NOT NULL, v INT NOT NULL, KEY k_p (parent_id), CONSTRAINT fk_c FOREIGN KEY (parent_id) REFERENCES parent(id))",
        "CREATE TABLE enum_pk (s ENUM('z','a','m') NOT NULL, id BIGINT NOT NULL, PRIMARY KEY (s,id))",
        "CREATE TABLE comp (a INT NOT NULL, b INT NOT NULL, v INT NOT NULL, PRIMARY KEY (a,b))",
        "CREATE TABLE sink_count (c BIGINT)",
    ]:
        query(c, ddl)
    query(c, "INSERT INTO seq WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n<200000) SELECT n FROM s")
    query(c, "INSERT INTO hot SELECT n, n FROM seq")
    query(c, "INSERT INTO parent SELECT n, CONCAT('p',n) FROM seq WHERE n<=20000")
    query(c, "INSERT INTO child (parent_id,v) SELECT 1+MOD(n,20000), n FROM seq")
    query(c, "INSERT INTO enum_pk SELECT ELT(1+MOD(n,3),'z','a','m'), n FROM seq WHERE n<=30000")
    query(c, "INSERT INTO comp SELECT MOD(n,100), n, n FROM seq WHERE n<=100000")
    query(c, "SET SESSION foreign_key_checks=1")
    query(c, "ANALYZE TABLE hot, parent, child, enum_pk, comp")




def blocks_writer(host, setup, write):
    """Run setup in one session, then try a write in another with a 1 s lock timeout."""
    h = connect(host, autocommit=False)
    try:
        for s in setup:
            query(h, s)
        w = connect(host)
        query(w, "SET SESSION innodb_lock_wait_timeout=1")
        query(w, "SET SESSION lock_wait_timeout=1")
        t = time.time()
        result = attempt(w, write)
        w.close()
        return {"write": result, "seconds": round(time.time() - t, 2)}
    finally:
        try:
            query(h, "ROLLBACK")
        except Exception:
            pass
        h.close()




def main():
    host, label = sys.argv[1], sys.argv[2]
    query(connect(host, db="mysql"), "CREATE DATABASE IF NOT EXISTS bench")
    c = connect(host, read_timeout=300)
    build(c)
    out = {"label": label, "version": query(c, "SELECT VERSION()")[0][0], "variables": variables(c)}

    out["statements"] = {s: attempt(c, s) for s in [
        "SHOW REPLICA STATUS", "SHOW SLAVE STATUS", "SHOW MASTER STATUS", "SHOW BINARY LOG STATUS", "SHOW REPLICAS", "SHOW SLAVE HOSTS",
        "SELECT * FROM performance_schema.data_locks LIMIT 1", "SELECT * FROM performance_schema.metadata_locks LIMIT 1",
        "SELECT `count` FROM information_schema.INNODB_METRICS WHERE name='trx_rseg_history_len'",
        "EXPLAIN FORMAT=TREE SELECT * FROM hot WHERE id>1 LIMIT 1", "EXPLAIN ANALYZE SELECT * FROM hot WHERE id>1 LIMIT 1"]}
    out["max_execution_time_hint"] = attempt(c, "SELECT /*+ MAX_EXECUTION_TIME(100) */ COUNT(*) FROM seq a CROSS JOIN seq b WHERE a.n+b.n=0")

    # How each version runs a whole-table count, as stated by EXPLAIN and as actually read.
    def counted(sql, setup=()):
        for st in setup:
            query(c, st)
        query(c, "FLUSH STATUS")
        t = time.monotonic()
        value = query(c, sql)[0][0]
        secs = round(time.monotonic() - t, 3)
        stats = dict(query(c, "SHOW SESSION STATUS WHERE Variable_name LIKE 'Handler_read%'"))
        return {"count": value, "seconds": secs, "handler": {k: int(v) for k, v in stats.items() if int(v)}}

    out["count"] = {
        "explain_default_child": count_explain(c, "SELECT COUNT(*) FROM child"),
        "explain_json_top_keys": sorted(json.loads(query(c, "EXPLAIN FORMAT=JSON SELECT COUNT(*) FROM child")[0][0]).keys()),
        "default": counted("SELECT COUNT(*) FROM child"),
        "single_thread": counted("SELECT COUNT(*) FROM child", ["SET SESSION innodb_parallel_read_threads=1"]),
        "force_primary": counted("SELECT COUNT(*) FROM child FORCE INDEX (PRIMARY)"),
        "force_secondary": counted("SELECT COUNT(*) FROM child FORCE INDEX (k_p)"),
    }
    query(c, "SET SESSION innodb_parallel_read_threads=DEFAULT")
    out["count_tree"] = query(c, "EXPLAIN FORMAT=TREE SELECT COUNT(*) FROM child")[0][0][:200]

    # Rows actually read by one walk step in each way of writing a composite cursor.
    def read_next(sql, args):
        query(c, "FLUSH STATUS")
        query(c, sql, args)
        return int(dict(query(c, "SHOW SESSION STATUS LIKE 'Handler_read_next'"))["Handler_read_next"])
    out["composite_forms_rows_read"] = {
        "tuple": read_next("SELECT a,b FROM comp WHERE (a,b)>(%s,%s) ORDER BY a,b LIMIT 1 OFFSET 99", (50, 90000)),
        "and_or": read_next("SELECT a,b FROM comp WHERE a>=%s AND (a>%s OR b>%s) ORDER BY a,b LIMIT 1 OFFSET 99", (50, 50, 90000)),
        "or_expanded": read_next("SELECT a,b FROM comp WHERE a>%s OR (a=%s AND b>%s) ORDER BY a,b LIMIT 1 OFFSET 99", (50, 50, 90000)),
    }

    # The split count must agree with COUNT(*) on this version, including the ENUM cursor.
    out["split_correct"] = {}
    for t in ("hot", "child", "comp", "enum_pk"):
        best, idx, _p = choose_index(c, "bench", t)
        truth = query(c, f"SELECT COUNT(*) FROM {t}")[0][0]
        r = walk_count(c, "bench", t, best, idx, width=5000)
        out["split_correct"][t] = {"index": best["name"], "rows": r["rows"], "truth": truth, "correct": r["rows"] == truth}

    out["locks"] = {
        "plain_count_blocks_update": blocks_writer(host, ["START TRANSACTION", "SELECT COUNT(*) FROM hot"], "UPDATE hot SET v=v+1 WHERE id=10"),
    }
    out["scope"] = "nonlocking default; intentional row/table/global-lock and pending-DDL injection excluded; see bench/nonlocking-scope.json"
    out["preflight"] = {}
    for t in ("hot", "child", "comp", "enum_pk"):
        try:
            r = analyze(connect(host), "bench", t)
            out["preflight"][t] = {"method": r["recommendation"]["method"], "index": (r.get("best_index") or {}).get("name"), "default_plan": r["default_plan"]}
        except Exception as e:
            out["preflight"][t] = {"crash": repr(e)[:200]}
    path = save(f"matrix_{label}", out)
    print(label, out["version"], "->", path, flush=True)


if __name__ == "__main__":
    sys.exit(main())
