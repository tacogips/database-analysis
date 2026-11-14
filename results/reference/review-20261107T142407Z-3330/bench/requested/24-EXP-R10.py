#!/usr/bin/env python3
"""Answers EXP-R10 (FIND-EST-01): the statistics cache and the statistics themselves, separately, against the true count.

Why does TABLE_ROWS read 0, and how far do the estimates move after bulk changes? Private tables only, except read-only metadata
on the shared tables and timing EXPLAIN on t_dense. Also answers the EXPLAIN-time gap in the fastCount question and the INNODB_TABLESTATS/PARTITIONS candidates.
"""
import time
from lablib import connect, query
from reqlib import Results, builder, guard, reset_schema, sql_try

ID = "24-EXP-R10"
guard(ID, 11)
r = Results(ID, ["EXP-R10", "FIND-EST-01"])
b = builder(db=None)
reset_schema(b, "req_est")
SCHEMA = "req_est"


def estimates(t, schema=SCHEMA):
    """Every estimate, each with a named cache setting, against the true count."""
    c = connect(db=schema, read_timeout=120)
    out = {"truth": query(c, f"SELECT COUNT(*) FROM `{t}`")[0][0]}
    meta = "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s"
    query(c, "SET SESSION information_schema_stats_expiry=DEFAULT")
    out["table_rows_cached"] = query(c, meta, (schema, t))[0][0]
    query(c, "SET SESSION information_schema_stats_expiry=0")
    out["table_rows_expiry0"] = query(c, meta, (schema, t))[0][0]
    query(c, "SET SESSION information_schema_stats_expiry=DEFAULT")
    out["table_rows_cached_after"] = query(c, meta, (schema, t))[0][0]
    out["explain_rows"] = query(c, f"EXPLAIN SELECT COUNT(*) FROM `{t}`")[0][9]
    stats = query(c, "SELECT n_rows,last_update FROM mysql.innodb_table_stats WHERE database_name=%s AND table_name=%s", (schema, t))
    out["innodb_table_stats_n_rows"] = stats[0][0] if stats else None
    out["stats_last_update"] = str(stats[0][1]) if stats else None
    tab = query(c, "SELECT NUM_ROWS,STATS_INITIALIZED,MODIFIED_COUNTER FROM information_schema.INNODB_TABLESTATS WHERE NAME=%s", (f"{schema}/{t}",))
    out["innodb_tablestats"] = {"num_rows": tab[0][0], "initialized": tab[0][1], "modified_counter": tab[0][2]} if tab else None
    c.close()
    truth = out["truth"] or 1
    for k in ("table_rows_cached", "table_rows_expiry0", "explain_rows", "innodb_table_stats_n_rows"):
        if out.get(k) is not None:
            out[k + "_error_pct"] = round(100 * (int(out[k]) - out["truth"]) / truth, 2)
    if tab:
        out["num_rows_error_pct"] = round(100 * (tab[0][0] - out["truth"]) / truth, 2)
    return out


with r.section("cache_on_a_new_table") as d:
    query(b, f"CREATE TABLE {SCHEMA}.fresh (id BIGINT PRIMARY KEY, v INT NOT NULL)")
    c = connect(db=SCHEMA, read_timeout=60)
    meta = "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='req_est' AND TABLE_NAME='fresh'"
    d["read_while_empty_default_expiry"] = query(c, meta)[0][0]  # this read fills the cache with the empty table's value
    query(b, f"INSERT INTO {SCHEMA}.fresh SELECT n, n FROM bench.seq1m")
    d["after_1m_inserts_default_expiry"] = query(c, meta)[0][0]
    time.sleep(30)
    d["after_30s_default_expiry"] = query(c, meta)[0][0]
    query(c, "SET SESSION information_schema_stats_expiry=0")
    d["after_30s_expiry0"] = query(c, meta)[0][0]
    query(c, "SET SESSION information_schema_stats_expiry=DEFAULT")
    d["explain_rows"] = query(c, "EXPLAIN SELECT COUNT(*) FROM fresh")[0][9]
    query(b, f"CREATE TABLE {SCHEMA}.fresh2 (id BIGINT PRIMARY KEY, v INT NOT NULL)")
    query(b, f"INSERT INTO {SCHEMA}.fresh2 SELECT n, n FROM bench.seq1m")
    time.sleep(15)
    d["never_read_before_insert_default_expiry"] = query(c, "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='req_est' AND TABLE_NAME='fresh2'")[0][0]
    c.close()

with r.section("shared_tables_that_read_zero") as d:
    # count_shapes.json had 19 tables at 0. Read only.
    c = connect(read_timeout=120)
    rows = query(c, "SELECT TABLE_NAME,TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='bench' AND TABLE_NAME IN ('d_soft_delete','d_json_virtual','d_history','fk_diamond_d','fk_uq_child','d_eav')")
    d["default_expiry"] = {k: v for k, v in rows}
    query(c, "SET SESSION information_schema_stats_expiry=0")
    rows = query(c, "SELECT TABLE_NAME,TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='bench' AND TABLE_NAME IN ('d_soft_delete','d_json_virtual','d_history','fk_diamond_d','fk_uq_child','d_eav')")
    d["expiry_0"] = {k: v for k, v in rows}
    query(c, "SET SESSION information_schema_stats_expiry=DEFAULT")
    rows = query(c, "SELECT TABLE_NAME,TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='bench' AND TABLE_NAME IN ('d_soft_delete','d_json_virtual','d_history','fk_diamond_d','fk_uq_child','d_eav')")
    d["default_expiry_after"] = {k: v for k, v in rows}
    c.close()

DDL = "(id BIGINT PRIMARY KEY, k INT NOT NULL, pad CHAR(100) NOT NULL DEFAULT 'x', KEY k_k (k))"


def fresh(name, opts=""):
    query(b, f"DROP TABLE IF EXISTS {SCHEMA}.{name}")
    query(b, f"CREATE TABLE {SCHEMA}.{name} {DDL} {opts}")
    query(b, f"INSERT INTO {SCHEMA}.{name} (id,k) SELECT n, MOD(n,1000) FROM bench.seq1m")
    query(b, f"ANALYZE TABLE {SCHEMA}.{name}")


def changed(d, name, change, opts=""):
    fresh(name, opts)
    res = {"before": estimates(name)}
    t = time.monotonic()
    query(b, change.format(t=f"{SCHEMA}.{name}"))
    res["change_seconds"] = round(time.monotonic() - t, 1)
    res["immediately"] = estimates(name)
    time.sleep(20)
    res["after_20s"] = estimates(name)
    query(b, f"ANALYZE TABLE {SCHEMA}.{name}")
    res["after_analyze"] = estimates(name)
    d[name] = res
    r.save()


with r.section("after_bulk_changes") as d:
    changed(d, "del_uniform", "DELETE FROM {t} WHERE MOD(id,2)=0")
    changed(d, "del_head", "DELETE FROM {t} WHERE id<=500000")
    changed(d, "del_90pct", "DELETE FROM {t} WHERE MOD(id,10)<>0")
    changed(d, "update_half", "UPDATE {t} SET k=k+1 WHERE MOD(id,2)=0")
    changed(d, "insert_double", "INSERT INTO {t} (id,k) SELECT n+2000000, MOD(n,1000) FROM bench.seq1m")
    changed(d, "del_uniform_recalc_off", "DELETE FROM {t} WHERE MOD(id,2)=0", "STATS_AUTO_RECALC=0")
    changed(d, "insert_double_recalc_off", "INSERT INTO {t} (id,k) SELECT n+2000000, MOD(n,1000) FROM bench.seq1m", "STATS_AUTO_RECALC=0")
    changed(d, "del_uniform_transient", "DELETE FROM {t} WHERE MOD(id,2)=0", "STATS_PERSISTENT=0")

with r.section("partitions_table_rows") as d:
    query(b, f"""CREATE TABLE {SCHEMA}.p (id BIGINT NOT NULL, d INT NOT NULL, PRIMARY KEY (id,d))
                 PARTITION BY RANGE (d) (PARTITION p1 VALUES LESS THAN (100), PARTITION p2 VALUES LESS THAN (200), PARTITION p3 VALUES LESS THAN MAXVALUE)""")
    query(b, f"INSERT INTO {SCHEMA}.p SELECT n, MOD(n,300) FROM bench.seq1m")
    query(b, f"ANALYZE TABLE {SCHEMA}.p")
    query(b, f"DELETE FROM {SCHEMA}.p WHERE d>=200")
    c = connect(db=SCHEMA, read_timeout=120)
    query(c, "SET SESSION information_schema_stats_expiry=0")
    d["partitions"] = [list(x) for x in query(c, "SELECT PARTITION_NAME,TABLE_ROWS FROM information_schema.PARTITIONS WHERE TABLE_SCHEMA='req_est' AND TABLE_NAME='p'")]
    d["table_rows"] = query(c, "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='req_est' AND TABLE_NAME='p'")[0][0]
    d["truth"] = query(c, "SELECT COUNT(*) FROM p")[0][0]
    d["stats_names"] = [x[0] for x in query(c, "SELECT DISTINCT table_name FROM mysql.innodb_index_stats WHERE database_name='req_est' AND table_name LIKE 'p#%'")]
    d["tablestats_names"] = [[x[0], x[1]] for x in query(c, "SELECT NAME,NUM_ROWS FROM information_schema.INNODB_TABLESTATS WHERE NAME LIKE 'req\\_est/p#%'")]
    c.close()

with r.section("cost_on_t_dense") as d:
    c = connect(read_timeout=120)
    runs = {"explain": "EXPLAIN SELECT COUNT(*) FROM t_dense",
            "table_rows_default": "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='t_dense'",
            "tablestats": "SELECT NUM_ROWS FROM information_schema.INNODB_TABLESTATS WHERE NAME='bench/t_dense'",
            "innodb_table_stats": "SELECT n_rows FROM mysql.innodb_table_stats WHERE database_name='bench' AND table_name='t_dense'"}
    for name, sql in runs.items():
        times = []
        for _ in range(20):
            t = time.monotonic()
            res = query(c, sql)
            times.append((time.monotonic() - t) * 1000)
        d[name] = {"p50_ms": round(sorted(times)[10], 3), "max_ms": round(max(times), 3), "value": str(res[0][0] if name != "explain" else res[0][9])}
    query(c, "SET SESSION information_schema_stats_expiry=0")
    times = []
    for _ in range(20):
        t = time.monotonic()
        res = query(c, runs["table_rows_default"])
        times.append((time.monotonic() - t) * 1000)
    d["table_rows_expiry0"] = {"p50_ms": round(sorted(times)[10], 3), "max_ms": round(max(times), 3), "value": str(res[0][0])}
    d["mysql_n_rows_true_count_unavailable_note"] = "t_dense has 100,000,000 rows by construction (results/data_check.json)."
    c.close()

query(b, "DROP DATABASE req_est")
r.finish()
