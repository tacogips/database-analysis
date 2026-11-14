#!/usr/bin/env python3
"""Answers EXP-SOL-5 (FIND-SOL-COUNTER-01): starting a counter on a table in use, by three methods, against a quiet true count.

Old: COUNT then trigger, with writes in between. Stop writes. N-D: trigger first with the counter at 0, then a snapshot of both N and D, then c = c + (N - D) once.
Writers run with INSERT, DELETE and ROLLBACK, and the correction is retried after a lost reply. A private table with a private counter stands in for the replica's stopped state: N and D are read in one consistent snapshot.
"""
import random, threading, time
import pymysql
from lablib import connect, query
from reqlib import Results, builder, guard, reset_schema, sql_try

ID = "49-EXP-SOL-5"
guard(ID, 3)
r = Results(ID, ["EXP-SOL-5", "FIND-SOL-COUNTER-01"], method_note="The replica's stopped state is stood in for by one REPEATABLE READ snapshot; the replica itself is covered by EXP-R15.")
S = "req_cnt"
b = builder()
reset_schema(b, S)
query(b, f"USE {S}")


def fresh():
    for s in ("DROP TABLE IF EXISTS t", "DROP TABLE IF EXISTS cnt", "DROP TABLE IF EXISTS applied",
              "CREATE TABLE t (id BIGINT PRIMARY KEY AUTO_INCREMENT, v INT NOT NULL)", "CREATE TABLE cnt (k VARCHAR(10) PRIMARY KEY, c BIGINT NOT NULL)",
              "CREATE TABLE applied (correction_id VARCHAR(40) PRIMARY KEY)", "INSERT INTO cnt VALUES ('t',0)", "INSERT INTO t (v) SELECT n FROM bench.seq1m WHERE n<=50000"):
        query(b, s)


TRIGGERS = ("CREATE TRIGGER t_ai AFTER INSERT ON t FOR EACH ROW UPDATE cnt SET c=c+1 WHERE k='t'", "CREATE TRIGGER t_ad AFTER DELETE ON t FOR EACH ROW UPDATE cnt SET c=c-1 WHERE k='t'")


class Writers:
    def __init__(self, seed=3):
        self.halt = threading.Event()
        self.th = [threading.Thread(target=self.run, args=(seed + i,), daemon=True) for i in range(3)]
        self.ops = 0

    def run(self, seed):
        c = connect(db=S, autocommit=False, read_timeout=30)
        rng = random.Random(seed)
        while not self.halt.is_set():
            kind = rng.random()
            try:
                if kind < .5:
                    query(c, "INSERT INTO t (v) VALUES (1)")
                else:
                    query(c, "DELETE FROM t WHERE id=%s", (rng.randint(1, 50000),))
                query(c, "ROLLBACK" if rng.random() < .2 else "COMMIT")
                self.ops += 1
            except pymysql.MySQLError:
                query(c, "ROLLBACK")
            time.sleep(.002)
        c.close()

    def __enter__(self):
        for t in self.th:
            t.start()
        return self

    def __exit__(self, *exc):
        self.halt.set()
        for t in self.th:
            t.join(5)


def check():
    time.sleep(.2)
    n = query(b, "SELECT COUNT(*) FROM t")[0][0]
    c = query(b, "SELECT c FROM cnt WHERE k='t'")[0][0]
    return {"true_count": n, "counter": c, "drift": c - n}


with r.section("methods") as d:
    # Old: count, then trigger, while writes continue.
    fresh()
    with Writers() as w:
        time.sleep(.5)
        n = query(b, "SELECT COUNT(*) FROM t")[0][0]
        time.sleep(.5)  # the window between the count and the trigger
        query(b, "UPDATE cnt SET c=%s WHERE k='t'", (n,))
        for s in TRIGGERS:
            query(b, s)
        time.sleep(2)
    d["old_count_then_trigger"] = {**check(), "writer_ops": w.ops}

    # Writes stopped while the initial value and the trigger go in.
    fresh()
    with Writers() as w:
        time.sleep(.5)
    n = query(b, "SELECT COUNT(*) FROM t")[0][0]
    query(b, "UPDATE cnt SET c=%s WHERE k='t'", (n,))
    for s in TRIGGERS:
        query(b, s)
    with Writers(seed=11) as w2:
        time.sleep(2)
    d["stop_writes"] = {**check(), "writer_ops": w.ops + w2.ops}

    # N-D: trigger first, then N and D from one snapshot, then one correction, with a retry after an unclear reply.
    fresh()
    for s in TRIGGERS:
        query(b, s)
    with Writers(seed=23) as w:
        time.sleep(1)
        snap = connect(db=S, autocommit=False, read_timeout=30)
        query(snap, "START TRANSACTION WITH CONSISTENT SNAPSHOT")
        n = query(snap, "SELECT COUNT(*) FROM t")[0][0]
        dd = query(snap, "SELECT c FROM cnt WHERE k='t'")[0][0]
        query(snap, "ROLLBACK")
        snap.close()
        cor = n - dd
        time.sleep(.3)

        def correct(cid):
            c = connect(db=S, autocommit=False, read_timeout=30)
            try:
                query(c, "INSERT INTO applied VALUES (%s)", (cid,))
                query(c, "UPDATE cnt SET c=c+%s WHERE k='t'", (cor,))
                query(c, "COMMIT")
                return "applied"
            except pymysql.err.IntegrityError:
                query(c, "ROLLBACK")
                return "already applied"
            finally:
                c.close()
        first = correct("boot-1")
        retry = correct("boot-1")  # as after a lost reply
        time.sleep(1.5)
    d["n_minus_d_with_id"] = {**check(), "n": n, "d": dd, "first": first, "retry": retry, "writer_ops": w.ops}

    # The same without the id: the retry adds the correction again.
    fresh()
    for s in TRIGGERS:
        query(b, s)
    with Writers(seed=31) as w:
        time.sleep(1)
        snap = connect(db=S, autocommit=False, read_timeout=30)
        query(snap, "START TRANSACTION WITH CONSISTENT SNAPSHOT")
        n = query(snap, "SELECT COUNT(*) FROM t")[0][0]
        dd = query(snap, "SELECT c FROM cnt WHERE k='t'")[0][0]
        query(snap, "ROLLBACK")
        snap.close()
        query(b, "UPDATE cnt SET c=c+%s WHERE k='t'", (n - dd,))
        query(b, "UPDATE cnt SET c=c+%s WHERE k='t'", (n - dd,))
        time.sleep(1.5)
    d["n_minus_d_retried_without_id"] = {**check(), "correction": n - dd}

    # Paths the trigger does not see.
    fresh()
    n = query(b, "SELECT COUNT(*) FROM t")[0][0]
    query(b, "UPDATE cnt SET c=%s WHERE k='t'", (n,))
    for s in TRIGGERS:
        query(b, s)
    paths = {}
    query(b, "REPLACE INTO t (id,v) VALUES (1,99)")
    paths["replace_existing"] = check()
    query(b, "INSERT INTO t (id,v) VALUES (2,99) ON DUPLICATE KEY UPDATE v=98")
    paths["upsert_existing"] = check()
    query(b, "TRUNCATE t")
    paths["truncate"] = check()
    d["paths"] = paths

query(b, f"DROP DATABASE {S}")
r.finish()
