#!/usr/bin/env python3
"""Nonlocking plain COUNT only: indexes, RC/RR and explicit transaction/autocommit.

Each statement runs in an open transaction (or in autocommit where it says so). We read data_locks, then try writes
from another session with a 1 s timeout. Same 200k rows in three index designs.
"""
import clocklib
import sys, time
import pymysql
from lablib import connect, query, save

ROWS = 200_000
DESIGNS = {
    "pk_only": "CREATE TABLE lk (id BIGINT PRIMARY KEY, v INT NOT NULL, payload INT NOT NULL)",
    "pk_and_small_secondary": "CREATE TABLE lk (id BIGINT PRIMARY KEY, v INT NOT NULL, payload INT NOT NULL, KEY k_v (v))",
    "no_pk_no_index": "CREATE TABLE lk (id BIGINT NOT NULL, v INT NOT NULL, payload INT NOT NULL)",
}
STATEMENTS = {"plain_count": ("SELECT COUNT(*) FROM lk", True),
              "plain_count_autocommit": ("SELECT COUNT(*) FROM lk", False)}
ISOLATIONS = ["READ-COMMITTED", "REPEATABLE-READ"]


def build(design_sql):
    c = connect(read_timeout=600)
    query(c, "DROP DATABASE IF EXISTS bench_lock")
    query(c, "CREATE DATABASE bench_lock")
    query(c, "USE bench_lock")
    query(c, design_sql)
    query(c, "CREATE TABLE lk_summary (c BIGINT)")
    query(c, "CREATE TABLE lk_summary2 (k INT PRIMARY KEY, c BIGINT)")
    query(c, "INSERT INTO lk_summary2 VALUES (1,0)")
    query(c, f"INSERT INTO lk SELECT n*2, MOD(n,1000), n FROM bench.seq1m WHERE n<={ROWS}")
    query(c, "ANALYZE TABLE lk")
    c.close()


def try_write(conn, sql):
    t = clocklib.now()
    try:
        query(conn, sql)
        query(conn, "ROLLBACK")
        return {"blocked": False, "ms": round((clocklib.now() - t) * 1000)}
    except pymysql.MySQLError as e:
        try:
            query(conn, "ROLLBACK")
        except Exception:
            pass
        return {"blocked": e.args[0] == 1205, "error": e.args[0], "ms": round((clocklib.now() - t) * 1000)}


def measure(design, isolation, name, sql, in_trx):
    a = connect(autocommit=not in_trx, db="bench_lock", read_timeout=120)
    obs = connect(db="bench_lock", read_timeout=120)
    w = connect(autocommit=False, db="bench_lock", read_timeout=30)
    query(w, "SET SESSION innodb_lock_wait_timeout=1")
    query(a, f"SET SESSION transaction_isolation='{isolation}'")
    if in_trx:
        query(a, "START TRANSACTION")
    t = clocklib.now()
    outcome = "ok"
    try:
        query(a, sql)
    except pymysql.MySQLError as e:
        outcome = f"error {e.args[0]}: {str(e.args[1])[:80]}"
    ms = round((clocklib.now() - t) * 1000)
    modes = {m: n for m, n in query(obs, """SELECT LOCK_MODE,COUNT(*) FROM performance_schema.data_locks
               WHERE OBJECT_SCHEMA='bench_lock' AND OBJECT_NAME='lk' AND LOCK_TYPE='RECORD' GROUP BY LOCK_MODE""")}
    # id 360000 exists and is far from the head of the table. Odd ids fall in gaps.
    res = {"design": design, "isolation": isolation, "statement": name, "in_transaction": in_trx, "sql": sql, "outcome": outcome, "ms": ms,
           "record_locks": sum(modes.values()), "lock_modes": modes,
           "update_row": try_write(w, "UPDATE lk SET payload=payload+1 WHERE id=360000"),
           "insert_row": try_write(w, "INSERT INTO lk (id,v,payload) VALUES (360001,1,0)")}
    try:
        query(a, "ROLLBACK")
    except Exception:
        pass
    for x in (a, obs, w):
        x.close()
    return res


def main():
    out = []
    for design, ddl in DESIGNS.items():
        build(ddl)
        for iso in ISOLATIONS:
            for name, (sql, in_trx) in STATEMENTS.items():
                r = measure(design, iso, name, sql, in_trx)
                out.append(r)
                print(f"{design:24s} {iso:17s} {name:26s} locks={r['record_locks']:>7} update_blocked={r['update_row']['blocked']!s:5} insert_blocked={r['insert_row']['blocked']!s:5} {r['outcome'][:40]}", flush=True)
    query(connect(), "DROP DATABASE bench_lock")
    save("lock_factors", out)


if __name__ == "__main__":
    sys.exit(main())
