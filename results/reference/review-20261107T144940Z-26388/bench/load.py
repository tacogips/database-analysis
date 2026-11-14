#!/usr/bin/env python3
"""Bulk-load the lab tables. Idempotent: a table with enough rows is skipped."""
import os, sys, time
import pymysql
from lablib import validate_lab_host

HOST = os.environ.get("LAB_HOST", "primary")


def conn():
    validate_lab_host(HOST)
    return pymysql.connect(host=HOST, user="root", password="lab", database="bench", autocommit=True,
                           read_timeout=7200, write_timeout=7200)


c = conn()
cur = c.cursor()


def q(sql):
    cur.execute(sql)
    return cur.fetchall()


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def done(table):
    return bool(q("SELECT 1 FROM load_state WHERE t=%s" % repr(table)))


def begin(table):
    """True if the table still needs loading. Clears anything left by an interrupted run."""
    # A finished table can still be empty: MEMORY tables lose their rows on every restart.
    if done(table) and q(f"SELECT 1 FROM {table} LIMIT 1"):
        log(f"skip {table}")
        return False
    q("DELETE FROM load_state WHERE t=%s" % repr(table))
    if q(f"SELECT 1 FROM {table} LIMIT 1"):
        # ARCHIVE has no TRUNCATE, so a partial table is rebuilt from its own definition.
        definition = q(f"SHOW CREATE TABLE {table}")[0][1]
        q(f"DROP TABLE {table}")
        q(definition)
    return True


def finish(table):
    q("INSERT INTO load_state VALUES (%s)" % repr(table))
    log(f"done {table}")


def batches(table, total, select_tmpl, step=100_000, columns=""):
    """Autocommit batches. select_tmpl uses n (1..1M from seq1m) and {off}; n+{off} is the global sequence."""
    if not begin(table):
        return
    t0 = time.time()
    for off in range(0, total, step):
        size = min(step, total - off)
        q(f"INSERT INTO {table} {columns} " + select_tmpl.format(off=off) + f" FROM seq1m WHERE n <= {size}")
        if (off + size) % 1_000_000 == 0 and (off + size) % (max(total // 10, 1_000_000)) == 0:
            log(f"{table} {off + size:,}/{total:,} {time.time() - t0:.0f}s")
    log(f"{table} {total:,} rows in {time.time() - t0:.0f}s")
    finish(table)


def load_designs():
    """Small tables for each remaining design. Every key value is deterministic."""
    m = 1_000_000
    batches("d_negative_pk", m, "SELECT CAST(n AS SIGNED)+{off}-500000, n")
    batches("d_float_pk", m, "SELECT (n+{off})/3.0, n")
    batches("d_decimal_pk", m, "SELECT (n+{off})/7, n")
    batches("d_datetime_pk", m, "SELECT DATE_ADD('2026-01-01', INTERVAL (n+{off})*1234 MICROSECOND), n")
    batches("d_timestamp_pk", m, "SELECT TIMESTAMPADD(MICROSECOND,(n+{off})*1234,'2026-01-01 00:00:00'), n")
    batches("d_enum_pk", m, "SELECT ELT(1+MOD(n+{off},3),'z','a','m'), n+{off}, n")
    batches("d_desc_pk", m, "SELECT n+{off}, n")
    batches("d_snowflake", m, "SELECT ((1700000000000+(n+{off})*37)<<22)+MOD(n+{off},4096), n")
    batches("d_unsigned_max", 100_000, "SELECT 18446744073709551615-(n+{off})+1, n")
    if begin("d_str_bin"):
        q("INSERT INTO d_str_bin SELECT CONCAT(IF(MOD(n,2)=0,'A','a'),n), n FROM seq1m WHERE n<=500000")
        finish("d_str_bin")
    batches("d_prefix_unique", 500_000, "SELECT CONCAT(LPAD(n+{off},30,'0'),'-tail'), n")
    batches("d_noindex", m, "SELECT MOD(n+{off},1000), n")
    batches("d_gipk", m, "SELECT MOD(n+{off},1000), n")
    batches("d_compressed", 500_000, "SELECT n+{off}, REPEAT(MD5(n),5)")
    batches("d_json_virtual", 500_000, "SELECT n+{off}, JSON_OBJECT('k',MOD(n,100),'v',MD5(n))", columns="(id,doc)")
    batches("d_fulltext", 200_000, "SELECT n+{off}, CONCAT('word',MOD(n,1000),' ',MD5(n))")
    batches("d_spatial", 200_000, "SELECT n+{off}, ST_SRID(POINT(MOD(n,180),MOD(n,90)),4326)")
    # Deleted rows form one long run, so a filtered chunk can walk a lot of rows to fill LIMIT.
    batches("d_soft_delete", 2 * m, "SELECT n+{off}, IF(n+{off}<=1500000,'2026-01-01',NULL), n")
    batches("d_history", m, "SELECT 1+MOD(n+{off},100000), DATE_ADD('2020-01-01', INTERVAL n+{off} SECOND), n")
    batches("d_eav", m, "SELECT 1+FLOOR((n+{off}-1)/5), ELT(1+MOD(n+{off}-1,5),'a','b','c','d','e'), MD5(n)")
    batches("d_polymorphic", m, "SELECT n+{off}, ELT(1+MOD(n,2),'order','customer'), 1+MOD(n,1000000)")
    batches("d_triggered", 500_000, "SELECT n+{off}, n")
    batches("fk_diamond_a", 100_000, "SELECT n+{off}")
    batches("fk_diamond_b", 500_000, "SELECT n+{off}, 1+MOD(n,100000)")
    batches("fk_diamond_c", 500_000, "SELECT n+{off}, 1+MOD(n,100000)")
    batches("fk_diamond_d", m, "SELECT n+{off}, 1+MOD(n,500000), 1+MOD(n+7,500000)")
    batches("fk_comp_parent", 500_000, "SELECT 1+FLOOR((n+{off})/1000), MOD(n+{off},1000)")
    batches("fk_comp_child", m, "SELECT n+{off}, 1+FLOOR(MOD(n,500000)/1000), MOD(MOD(n,500000),1000)")
    batches("fk_uq_parent", 500_000, "SELECT n+{off}, (n+{off})*3")
    batches("fk_uq_child", m, "SELECT n+{off}, (1+MOD(n,500000))*3")
    batches("fk_null_parent", 100_000, "SELECT n+{off}")
    batches("fk_null_child", 500_000, "SELECT n+{off}, IF(MOD(n,4)=0,NULL,1+MOD(n,100000))")
    batches("bench_other.parent", 100_000, "SELECT n+{off}")
    batches("fk_cross_schema", 500_000, "SELECT n+{off}, 1+MOD(n,100000)")
    # Schema-per-tenant: many identical small tables in several databases.
    if not done("tenants"):
        for t in range(1, 21):
            q(f"CREATE DATABASE IF NOT EXISTS bench_tenant_{t}")
            q(f"DROP TABLE IF EXISTS bench_tenant_{t}.orders")
            q(f"CREATE TABLE bench_tenant_{t}.orders (id BIGINT NOT NULL PRIMARY KEY, v INT NOT NULL) ENGINE=InnoDB")
            q(f"INSERT INTO bench_tenant_{t}.orders SELECT n, n FROM seq1m WHERE n <= {t * 1000}")
        finish("tenants")


def main():
    q("SET SESSION foreign_key_checks=0")  # also allows TRUNCATE of FK parents on a resume
    q("SET SESSION unique_checks=0")
    q("SET SESSION sql_log_bin=0")
    q("SET GLOBAL innodb_flush_log_at_trx_commit=0")
    q("SET GLOBAL sync_binlog=0")
    q("SET SESSION cte_max_recursion_depth=2000000")
    # Lab only: small autocommit batches with no redo. A crash here means reloading.
    q("ALTER INSTANCE DISABLE INNODB REDO_LOG")
    q("SET GLOBAL key_buffer_size=536870912")

    q("CREATE TABLE IF NOT EXISTS load_state (t VARCHAR(64) PRIMARY KEY)")
    if begin("seq1m"):
        q("INSERT INTO seq1m WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n < 1000000) SELECT n FROM s")
        finish("seq1m")

    # Small and FK-related tables first; the 100M table last so everything else is usable early.
    batches("t_sparse", 10_000_000,
            "SELECT (n+{off})*97 + IF((n+{off})>5000000, 1000000000000, 0), n")
    if begin("t_uuid"):
        # Sorted insert keeps a random-PK build fast with a small buffer pool.
        for bucket in range(16):
            q(f"INSERT INTO t_uuid SELECT UNHEX(MD5(n*16 + {bucket})), n FROM seq1m WHERE n <= 312500 ORDER BY 1")
        finish("t_uuid")
    batches("t_composite", 10_000_000,
            "SELECT IF(MOD(n+{off},2)=0, 1, 2 + MOD(n+{off},999)), n+{off}, n")
    batches("t_nopk", 5_000_000, "SELECT MOD(n+{off},1000), n")
    batches("t_uniq_notnull", 3_000_000, "SELECT n+{off}, n")
    batches("t_uniq_nullable", 3_000_000, "SELECT IF(MOD(n+{off},3)=0, NULL, n+{off}), n")

    batches("customers", 1_000_000, "SELECT n+{off}, CONCAT('c', n)")
    batches("orders", 5_000_000, "SELECT n+{off}, 1+MOD(CRC32(n+{off}),1000000), MOD(n,10000)/100")
    batches("order_items", 15_000_000, "SELECT n+{off}, 1+MOD(n+{off}-1,5000000), MOD(n,5000), 1+MOD(n,5)")
    batches("tags", 1000, "SELECT n+{off}, CONCAT('t', n)")
    batches("customer_tags", 5_000_000, "SELECT 1+MOD(n+{off},1000000), 1+FLOOR((n+{off}-1)/1000000)*200+MOD(n+{off},200)")

    if begin("categories"):
        # Parents have larger ids than children, so id-ordered copy violates the FK.
        q("INSERT INTO categories SELECT n, IF(n=1000000 OR MOD(n,10)=0, NULL, LEAST(n+10-MOD(n,10),1000000)) FROM seq1m")
        finish("categories")
    if not done("circ"):
        q("ALTER TABLE circ_a DROP FOREIGN KEY fk_a_b") if q("SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA='bench' AND CONSTRAINT_NAME='fk_a_b'") else None
        q("TRUNCATE circ_b")
        q("TRUNCATE circ_a")
        q("INSERT INTO circ_a SELECT n, n FROM seq1m WHERE n<=100000")
        q("INSERT INTO circ_b SELECT n, n FROM seq1m WHERE n<=100000")
        q("ALTER TABLE circ_a ADD CONSTRAINT fk_a_b FOREIGN KEY (b_id) REFERENCES circ_b (id)")
        finish("circ")

    batches("t_lob", 300_000, "SELECT n+{off}, n, REPEAT(MD5(n), 256)", step=50_000)
    batches("t_part", 10_000_000,
            "SELECT n+{off}, DATE_ADD('2026-01-01', INTERVAL MOD(CRC32(n+{off}),180) DAY), n")
    if begin("t_strpk"):
        q("INSERT INTO t_strpk SELECT CASE MOD(n,4) WHEN 0 THEN CONCAT(n) WHEN 1 THEN CONCAT('Ab',n) "
          "WHEN 2 THEN CONCAT('Äb-',n) ELSE CONCAT('z',n,' x') END, n FROM seq1m")
        finish("t_strpk")
    # 1000 rows share every second: chunk edges always land inside a duplicate run.
    batches("t_events", 5_000_000,
            "SELECT CRC32(n+{off})*100000 + MOD(n+{off},100000), "
            "DATE_ADD('2026-01-01', INTERVAL FLOOR((n+{off})/1000) SECOND), n")
    batches("t_myisam", 3_000_000, "SELECT n+{off}, n")
    # MEMORY fixes its size limit at table creation, so rebuild it under the larger session limit.
    q("SET SESSION max_heap_table_size=1073741824")
    q("ALTER TABLE t_memory ENGINE=MEMORY")
    batches("t_memory", 500_000, "SELECT n+{off}, n")
    batches("t_archive", 1_000_000, "SELECT n+{off}, n")
    batches("order_items_nofk", 5_000_000, "SELECT n+{off}, 1+MOD(n+{off}-1,5000000), MOD(n,5000), 1+MOD(n,5)")
    batches("t_hot", 5_000_000, "SELECT n+{off}, 0, n")

    # Replicated: binlog must be on, unlike every other table.
    q("SET SESSION sql_log_bin=1")
    batches("t_repl", 20_000_000, "SELECT n+{off}, n, MD5(n)")
    q("SET SESSION sql_log_bin=0")

    batches("t_dense", 100_000_000,
            "SELECT n+{off}, CRC32(n+{off}) MOD 10000000, MOD(n,5), MOD(n,100000)/100, "
            "DATE_ADD('2020-01-01', INTERVAL FLOOR((n+{off})/10) SECOND), LEFT(MD5(n),16)")
    if not q("SHOW INDEX FROM t_dense WHERE Key_name='k_created'"):
        log("t_dense building k_created")
        q("ALTER TABLE t_dense ADD KEY k_created (created_at)")
        log("t_dense k_created done")

    load_designs()
    q("ALTER INSTANCE ENABLE INNODB REDO_LOG")
    q("SET GLOBAL key_buffer_size=8388608")
    q("SET GLOBAL innodb_flush_log_at_trx_commit=1")
    q("SET GLOBAL sync_binlog=1")
    q("ANALYZE TABLE t_dense, t_sparse, t_uuid, t_composite, t_nopk, t_uniq_notnull, t_uniq_nullable, "
      "customers, orders, order_items, customer_tags, categories, t_lob, t_part, t_strpk, t_events, t_myisam, t_archive, order_items_nofk, t_hot, t_repl")
    log("all done")


if __name__ == "__main__":
    sys.exit(main())
