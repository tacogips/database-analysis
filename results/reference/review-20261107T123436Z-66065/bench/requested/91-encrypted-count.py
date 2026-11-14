#!/usr/bin/env python3
"""InnoDB TDE: the same table with and without ENCRYPTION='Y', on one server with a small buffer pool.

Pages are encrypted on disk only, so a cost is expected on page reads, not on reads served from the buffer pool.
Usage: 91-encrypted-count.py <host> <version>
"""
import sys, threading, time
import pymysql
from count_preflight import analyze
from countlib import choose_index, walk_count
from lablib import connect, query, save

ROWS = 4_000_000
DDL = "CREATE TABLE {t} (id BIGINT NOT NULL PRIMARY KEY, v INT NOT NULL, pad VARCHAR(64) NOT NULL, KEY k_v (v)) ENGINE=InnoDB {opt}"


STATUS = ("Innodb_buffer_pool_reads", "Innodb_buffer_pool_read_ahead", "Innodb_data_read")


def status(c):
    names = ",".join(f"'{n}'" for n in STATUS)
    return {k: int(v) for k, v in query(c, f"SELECT VARIABLE_NAME,VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME IN ({names})")}


def timed(c, sql):
    before = status(c)
    t = time.monotonic()
    v = query(c, sql)[0][0]
    secs = round(time.monotonic() - t, 3)
    after = status(c)
    return {"seconds": secs, "count": v, **{k: after[k] - before[k] for k in STATUS}}


def evict(c):
    """A third-table scan before each pair member. This is not proof of equal residency or physical cold I/O.
    An earlier version evicted each table with the other one. That left the table built last in the pool and made
    encrypted reads look faster."""
    query(c, "SELECT COUNT(*) FROM t_evict FORCE INDEX (PRIMARY)")


def compare(c, sql_tmpl, rounds=6):
    """Alternate table order. Labels describe operations, not guaranteed cache states.
    The eviction scan can leave target pages resident; immediate repeat can still reread data.
    Host/VM caches cannot be cleared and encrypted/plain residency is not assumed equal."""
    out = {"plain_after_evict": [], "plain_immediate_repeat": [], "enc_after_evict": [], "enc_immediate_repeat": []}
    for i in range(rounds):
        pair = [("plain", "t_plain"), ("enc", "t_enc")]
        for tag, t in (pair if i % 2 == 0 else pair[::-1]):
            evict(c)
            out[f"{tag}_after_evict"].append(timed(c, sql_tmpl.format(t=t)))
            out[f"{tag}_immediate_repeat"].append(timed(c, sql_tmpl.format(t=t)))
    return out




def _md5_available(c):
    try:
        query(c, "SELECT MD5('a')")
        return True
    except pymysql.MySQLError as e:
        return f"error {e.args[0]}: {e.args[1]}"


def main():
    host, version = sys.argv[1], sys.argv[2]
    query(connect(host, db="mysql"), "CREATE DATABASE IF NOT EXISTS bench")
    c = connect(host, read_timeout=1800)
    out = {"version": query(c, "SELECT VERSION()")[0][0],
           "cache_protocol": "after_evict = third-table scan, immediate_repeat = immediate reexecution; neither guarantees equal residency or physical cold/warm storage",
           "flush_method": query(c,"SELECT @@innodb_flush_method")[0][0]}
    out["keyring_status"] = [list(r) for r in query(c, "SELECT * FROM performance_schema.keyring_component_status")]
    # MD5() is absent on 9.7 and 26.10 in this lab, so SHA2 is used to fill the same width on every version.
    out["md5_available"] = _md5_available(c)
    query(c, "SET SESSION cte_max_recursion_depth=200000")
    query(c, "CREATE TABLE seq (n INT PRIMARY KEY)")
    query(c, "INSERT INTO seq WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n<100000) SELECT n FROM s")
    for t, opt in (("t_plain", ""), ("t_enc", "ENCRYPTION='Y'")):
        query(c, DDL.format(t=t, opt=opt))
        for off in range(0, ROWS, 100_000):
            query(c, f"INSERT INTO {t} SELECT n+{off}, MOD(n,100000), LEFT(SHA2(n,256),60) FROM seq")
    query(c, "CREATE TABLE t_evict (id BIGINT NOT NULL PRIMARY KEY, pad VARCHAR(255) NOT NULL) ENGINE=InnoDB")
    for off in range(0, 2_000_000, 100_000):
        query(c, f"INSERT INTO t_evict SELECT n+{off}, REPEAT(LEFT(SHA2(n,256),60),4) FROM seq")
    query(c, "ANALYZE TABLE t_plain, t_enc, t_evict")
    out["sizes"] = [list(r) for r in query(c, "SELECT table_name,data_length,index_length,create_options FROM information_schema.tables WHERE table_schema='bench' AND table_name IN ('t_plain','t_enc')")]
    out["detect_with_tablespaces"] = [list(r) for r in query(c, "SELECT name,encryption FROM information_schema.innodb_tablespaces WHERE name LIKE 'bench/t_%'")]
    out["explain"] = {t: [str(x) for x in query(c, f"EXPLAIN SELECT COUNT(*) FROM {t}")[0]] for t in ("t_plain", "t_enc")}
    out["preflight"] = {t: {k: str(v)[:300] for k, v in analyze(connect(host), "bench", t).items() if k in ("recommendation", "create_options", "default_plan")} for t in ("t_plain", "t_enc")}
    out["count_default"] = compare(c, "SELECT COUNT(*) FROM {t}")
    query(c, "SET SESSION innodb_parallel_read_threads=1")
    out["count_single_thread"] = compare(c, "SELECT COUNT(*) FROM {t}")
    out["count_force_primary"] = compare(c, "SELECT COUNT(*) FROM {t} FORCE INDEX (PRIMARY)")
    out["count_force_secondary"] = compare(c, "SELECT COUNT(*) FROM {t} FORCE INDEX (k_v)")
    best, idx, _p = choose_index(c, "bench", "t_enc")
    out["split"] = {"index": best["name"], **walk_count(c, "bench", "t_enc", best, idx, width=100_000)}
    query(c, "SET SESSION innodb_parallel_read_threads=DEFAULT")
    # Locks do not depend on encryption: same plain count and same locking form on the encrypted table.
    a = connect(host, autocommit=False)
    query(a, "START TRANSACTION")
    query(a, "SELECT COUNT(*) FROM t_enc")
    out["plain_count_record_locks"] = query(c, "SELECT COUNT(*) FROM performance_schema.data_locks WHERE OBJECT_NAME='t_enc' AND LOCK_TYPE='RECORD'")[0][0]
    query(a, "ROLLBACK")
    a.close()
    out["excluded_probes"] = {"insert_select_count_record_locks": "superseded_by_explicit_user_scope",
                              "rotate_during_count": "superseded_by_explicit_user_scope: runtime administrative contention injection"}
    print(version, {k: out[k] for k in ("keyring_status", "sizes", "plain_count_record_locks")}, flush=True)
    for name in ("count_default", "count_single_thread", "count_force_primary", "count_force_secondary"):
        print(name, {k: [r["seconds"] for r in v] for k, v in out[name].items()}, flush=True)
    save(f"requested_encrypted_count_{version}", out)


if __name__ == "__main__":
    main()
