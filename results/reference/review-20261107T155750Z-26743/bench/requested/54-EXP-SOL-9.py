#!/usr/bin/env python3
"""Answers EXP-SOL-9 (alternatives): parallel settings, several connections, a small index and an invisible one, one and many counter slots, and performance_schema deltas against the truth. Private tables, except reads of t_dense."""
import random, threading, time
import pymysql
from countlib import forced_plan, session_setup, split_count, split_plans
from experiments import DENSE
from lablib import connect, query
from reqlib import Results, builder, guard, measured_run, reset_schema, sql_try
from reqruns import count_once

ID = "54-EXP-SOL-9"
guard(ID, 19)
r = Results(ID, ["EXP-SOL-9", "FIND-BETTER-01"])
b = builder()
S = "req_alt"
reset_schema(b, S)
query(b, f"USE {S}")

with r.section("parallel_threads_primary") as d:
    # FORCE PRIMARY uses the parallel reader. Settings tried on the same statement under the usual load.
    for threads in (1, 4, 8):
        res = measured_run(f"threads={threads}", DENSE, lambda n: count_once(n, "SELECT COUNT(*) FROM t_dense FORCE INDEX (PRIMARY)", [f"SET SESSION innodb_parallel_read_threads={threads}"]), 10, 10)
        res.pop("series_10s", None)
        d[str(threads)] = res
        r.save()

with r.section("several_connections") as d:
    # Non-overlapping key ranges on t_dense, read by N connections at once. The sum is not one instant.
    def ranged(n_conns):
        c = connect(read_timeout=120)
        ids = [(i * 100_000_000 // n_conns, (i + 1) * 100_000_000 // n_conns) for i in range(n_conns)]
        c.close()

        def worker(lo, hi, out):
            x = connect(read_timeout=600)
            session_setup(x, 0)
            walked, cursor = 0, lo
            while cursor < hi:
                upper = min(cursor + 100_000, hi)
                walked += query(x, "SELECT /*+ MAX_EXECUTION_TIME(5000) */ COUNT(*) FROM t_dense WHERE id > %s AND id <= %s", (cursor, upper))[0][0]
                cursor = upper
            out.append(walked)
            x.close()

        def body(notes):
            out, ts = [], []
            for lo, hi in ids:
                t = threading.Thread(target=worker, args=(lo, hi, out), daemon=True)
                t.start()
                ts.append(t)
            for t in ts:
                t.join()
            notes.update(rows=sum(out), connections=n_conns)
        return body
    for n in (1, 4):
        if r.time_left() < 500:
            d["stopped_before"] = n
            break
        res = measured_run(f"conns={n}", DENSE, ranged(n), 10, 10)
        res.pop("series_10s", None)
        d[str(n)] = res
        r.save()

with r.section("small_or_invisible_index") as d:
    # A wide table with no small index, then one added. The walk's cost against the DDL's and the write amplification.
    query(b, "CREATE TABLE w (id BIGINT PRIMARY KEY, v INT NOT NULL, pad VARCHAR(1500) NOT NULL)")
    for off in range(0, 500_000, 100_000):
        query(b, f"INSERT INTO w SELECT n+{off}, 0, REPEAT(MD5(n),40) FROM bench.seq1m WHERE n<=100000")
    query(b, "ANALYZE TABLE w")
    c = connect(db=S, read_timeout=300)
    session_setup(c, 0)

    def full(plan):
        s = split_count(c, "w", plan, width=10_000, limit_ms=5000, duty=None, enforce_plan=False)
        return {k: s.get(k) for k in ("status", "rows", "seconds_total", "stmt_ms_max", "stmt_ms_p50")}

    def write_rate(seconds=5):
        w = connect(db=S, read_timeout=60)
        t0, n = time.monotonic(), 0
        while time.monotonic() - t0 < seconds:
            query(w, "UPDATE w SET v=v+1 WHERE id=%s", (1 + n % 400_000,))
            n += 1
        w.close()
        return round(n / seconds)
    d["primary_walk"] = full(forced_plan(c, S, "w", "PRIMARY"))
    d["update_rate_before_index"] = write_rate()
    t = time.monotonic()
    query(b, "ALTER TABLE w ADD INDEX k_v (v), ALGORITHM=INPLACE, LOCK=NONE")
    d["add_index_seconds"] = round(time.monotonic() - t, 1)
    query(b, "ANALYZE TABLE w")
    d["pages"] = {x[0]: int(x[1]) for x in query(b, "SELECT index_name,stat_value FROM mysql.innodb_index_stats WHERE database_name=%s AND table_name='w' AND stat_name='size'", (S,))}
    d["index_walk"] = full(forced_plan(c, S, "w", "k_v"))
    d["update_rate_after_index"] = write_rate()
    # Invisible to the optimizer for the service, visible only to a session that asks for it.
    query(b, "ALTER TABLE w ALTER INDEX k_v INVISIBLE")
    d["invisible_force_default_session"] = sql_try(c, "SELECT COUNT(*) FROM w FORCE INDEX (k_v)")
    query(c, "SET SESSION optimizer_switch='use_invisible_indexes=on'")
    d["invisible_force_with_switch"] = sql_try(c, "SELECT /*+ MAX_EXECUTION_TIME(5000) */ COUNT(*) FROM w FORCE INDEX (k_v)")
    d["invisible_with_set_var_hint"] = sql_try(c, "SELECT /*+ SET_VAR(optimizer_switch='use_invisible_indexes=on') */ COUNT(*) FROM w FORCE INDEX (k_v)")
    d["invisible_explain_with_switch"] = [str(x) for x in query(c, "EXPLAIN SELECT COUNT(*) FROM w FORCE INDEX (k_v)")[0][4:8]]
    d["sql3_rejects_invisible"] = split_plans(c, S, "w")["rejected"]
    c.close()

with r.section("counter_slots") as d:
    # One row against sixteen rows, at the same write rate, and with one long write transaction holding a slot.
    query(b, "CREATE TABLE c_t (id BIGINT PRIMARY KEY AUTO_INCREMENT, v INT NOT NULL)")
    query(b, "CREATE TABLE c_one (k INT PRIMARY KEY, c BIGINT NOT NULL)")
    query(b, "INSERT INTO c_one VALUES (0,0)")
    query(b, "CREATE TABLE c_many (k INT PRIMARY KEY, c BIGINT NOT NULL)")
    query(b, "INSERT INTO c_many SELECT n-1, 0 FROM bench.seq1m WHERE n<=16")

    def trial(body_sql, threads, seconds=8, hold=None):
        lat, stop = [], threading.Event()

        def worker(seed):
            c = connect(db=S, read_timeout=60, autocommit=False)
            query(c, "SET SESSION innodb_lock_wait_timeout=20")
            i = seed
            while not stop.is_set():
                i += 1
                t = time.monotonic()
                try:
                    query(c, "INSERT INTO c_t (v) VALUES (1)")
                    query(c, body_sql, () if "%s" not in body_sql else (i % 16,))
                    query(c, "COMMIT")
                    lat.append((time.monotonic() - t) * 1000)
                except pymysql.MySQLError:
                    query(c, "ROLLBACK")
                    lat.append(-1)
            c.close()
        before = query(b, "SELECT @@GLOBAL.innodb_lock_wait_timeout")[0][0]
        ws = [threading.Thread(target=worker, args=(s * 1000,), daemon=True) for s in range(threads)]
        h = None
        if hold:
            h = connect(db=S, autocommit=False, read_timeout=60)
            query(h, hold)
        for w in ws:
            w.start()
        time.sleep(seconds)
        stop.set()
        if h:
            query(h, "ROLLBACK")
            h.close()
        for w in ws:
            w.join(25)
        ok = sorted(x for x in lat if x >= 0)
        return {"commits": len(ok), "errors": sum(1 for x in lat if x < 0), "p50_ms": round(ok[len(ok) // 2], 2) if ok else None, "p99_ms": round(ok[int(len(ok) * .99)], 2) if ok else None, "server_lock_wait_setting": before}
    ONE = "UPDATE c_one SET c=c+1 WHERE k=0"
    MANY = "UPDATE c_many SET c=c+1 WHERE k=%s"
    for threads in (4, 16):
        d[f"one_slot_{threads}_writers"] = trial(ONE, threads)
        d[f"sixteen_slots_{threads}_writers"] = trial(MANY, threads)
    d["one_slot_long_trx_holding_it"] = trial(ONE, 4, hold="UPDATE c_one SET c=c+1 WHERE k=0")
    d["sixteen_slots_long_trx_holding_one"] = trial(MANY, 4, hold="UPDATE c_many SET c=c+1 WHERE k=0")
    d["sum_of_slots"] = int(query(b, "SELECT SUM(c) FROM c_many")[0][0])

with r.section("key_span_sampling") as d:
    # A rough count only: 1000 random spans of the key range, scaled up. Fixed seeds, three repeats, against the truth. Its bias on uneven keys is the point.
    c = connect(read_timeout=120)
    spec = {"t_sparse": ("id", 10_000_000), "d_snowflake": ("id", 1_000_000), "t_events": ("id", 5_000_000), "t_dense": ("id", 100_000_000)}
    for table, (key, truth) in spec.items():
        lo, hi = [int(x) for x in query(c, f"SELECT MIN({key}), MAX({key}) FROM {table}")[0]]
        span = max((hi - lo) // 100_000, 1)
        runs = []
        for seed in (1, 2, 3):
            rng = random.Random(seed)
            t = time.monotonic()
            total = 0
            for _ in range(1000):
                start = rng.randint(lo, hi)
                total += query(c, f"SELECT COUNT(*) FROM {table} WHERE {key} >= %s AND {key} < %s", (start, start + span))[0][0]
            est = total / 1000 * ((hi - lo + 1) / span)
            runs.append({"seed": seed, "estimate": round(est), "error_pct": round(100 * (est - truth) / truth, 2), "seconds": round(time.monotonic() - t, 2)})
        d[table] = {"truth": truth, "span": span, "runs": runs}
        r.save()
    c.close()

with r.section("performance_schema_deltas") as d:
    # Do P_S row operations reflect the count? Known operations, each compared with what COUNT(*) moved by.
    query(b, "CREATE TABLE ps (id BIGINT PRIMARY KEY, v INT NOT NULL)")
    query(b, "INSERT INTO ps SELECT n, n FROM bench.seq1m WHERE n<=10000")
    ops = {"commit_insert": ["START TRANSACTION", "INSERT INTO ps SELECT n+100000, 0 FROM bench.seq1m WHERE n<=100", "COMMIT"],
           "rollback_insert": ["START TRANSACTION", "INSERT INTO ps SELECT n+200000, 0 FROM bench.seq1m WHERE n<=100", "ROLLBACK"],
           "replace_existing": ["REPLACE INTO ps SELECT n, 1 FROM bench.seq1m WHERE n<=100"],
           "commit_delete": ["START TRANSACTION", "DELETE FROM ps WHERE id<=100", "COMMIT"],
           "truncate": ["TRUNCATE ps"]}
    q = "SELECT COUNT_INSERT, COUNT_DELETE FROM performance_schema.table_io_waits_summary_by_table WHERE OBJECT_SCHEMA=%s AND OBJECT_NAME='ps'"
    for name, statements in ops.items():
        a0 = (query(b, q, (S,)) or [(0, 0)])[0]
        n0 = query(b, "SELECT COUNT(*) FROM ps")[0][0]
        for s in statements:
            query(b, s)
        a1 = (query(b, q, (S,)) or [(0, 0)])[0]
        n1 = query(b, "SELECT COUNT(*) FROM ps")[0][0]
        d[name] = {"true_change": n1 - n0, "ps_insert_minus_delete": int(a1[0] - a0[0]) - int(a1[1] - a0[1]), "ps_after": [int(x) for x in a1]}

query(b, f"DROP DATABASE {S}")
r.finish()
