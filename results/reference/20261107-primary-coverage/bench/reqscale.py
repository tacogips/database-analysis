"""Run body for EXP-O10-PRIMARY-SCALE. A fresh small server (256 MB buffer pool), a private table 4 to 8 times the pool, and write rates well below saturation.

Which index should a statement read when writes are scattered: does the 20M row, 800 writes/s result on a 1.3 times pool table hold when the table is much larger than the pool and the server is not saturated?
Lab only: the host is the matrix server lab.rb starts. Every arm has its own connection and a server-side limit, and is saved as it ends.
"""
import threading, time
import pymysql
from countlib import forced_plan, session_setup, split_count
from lablib import METHOD_CPU, Sampler, Workload, connect, pin, query
from reqlib import Results, global_status

STATEMENT_LIMIT_MS = 300_000
SPLIT_SECONDS = 150
GS = ("Innodb_buffer_pool_read_requests", "Innodb_buffer_pool_reads")
MIXES = {"indexed": [("UPDATE bench.t SET k=k+1 WHERE id=%s", 4, 1.0)],
         "uniform_insert_delete": [("INSERT IGNORE INTO bench.t (id,k,v) SELECT x, MOD(x,1000000), 0 FROM (SELECT %s+100000000 AS x) d", 2, .5), ("DELETE FROM bench.t WHERE id=%s", 2, .5)]}


def build(host, rows):
    query(connect(host, db="mysql"), "CREATE DATABASE IF NOT EXISTS bench")
    a = connect(host, read_timeout=900)
    query(a, "SET SESSION sql_log_bin=0")
    query(a, "SET SESSION cte_max_recursion_depth=300000")
    query(a, "CREATE TABLE seq (n INT PRIMARY KEY)")
    query(a, "INSERT INTO seq WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n<200000) SELECT n FROM s")
    query(a, "CREATE TABLE t (id BIGINT PRIMARY KEY, k BIGINT NOT NULL, v INT NOT NULL, pad CHAR(60) NOT NULL DEFAULT 'x')")
    for off in range(0, rows, 200_000):
        query(a, f"INSERT INTO t (id,k,v) SELECT n+{off}, CRC32(n+{off}) MOD 1000000, 0 FROM seq")
    # The index goes on last, because random inserts into it would be slow with so small a pool.
    query(a, "ALTER TABLE t ADD KEY k_k (k)")
    query(a, "ANALYZE TABLE t")
    return a


def capacity(host, statements, rows, seconds=15, threads=8):
    done, halt = [0] * threads, threading.Event()

    def worker(i):
        c = connect(host, read_timeout=60)
        n = i * 7919
        while not halt.is_set():
            n += 104729
            try:
                query(c, statements[n % len(statements)], (1 + n % rows,))
                done[i] += 1
            except pymysql.MySQLError:
                pass
        c.close()
    ts = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(threads)]
    for t in ts:
        t.start()
    time.sleep(seconds)
    halt.set()
    for t in ts:
        t.join(10)
    return sum(done) / seconds


def run_arm(host, a, name, plans, control_seconds, rows):
    rec = {"arm": name}
    g0, t0 = global_status(a, *GS), time.time()
    if name == "control":
        time.sleep(control_seconds)
        rec.update(status="nothing ran", rows=None)
    else:
        c = connect(host, read_timeout=(SPLIT_SECONDS if name.startswith("split") else STATEMENT_LIMIT_MS / 1000) + 30)
        session_setup(c, 0)
        h0 = {k: int(v) for k, v in query(c, "SHOW SESSION STATUS LIKE 'Handler_read%'")}
        try:
            if name.startswith("split"):
                s = split_count(c, "t", plans[name], width=10_000, limit_ms=500, duty=None, max_rows=rows * 3, max_seconds=SPLIT_SECONDS, db="bench")
                rec.update({k: s.get(k) for k in ("status", "rows", "partial_rows", "steps", "stmt_ms_p50", "stmt_ms_p99", "stmt_ms_max", "width_end", "width_reductions", "errors")})
            else:
                index = "k_k" if name == "statement_k" else "PRIMARY"
                rec["rows"] = query(c, f"SELECT /*+ MAX_EXECUTION_TIME({STATEMENT_LIMIT_MS}) */ COUNT(*) FROM t FORCE INDEX (`{index}`)")[0][0]
                rec["status"] = "complete"
        except (pymysql.MySQLError, OSError) as e:
            rec.update(status=f"error_{e.args[0] if e.args else 0}")
        try:
            h1 = {k: int(v) for k, v in query(c, "SHOW SESSION STATUS LIKE 'Handler_read%'")}
            rec["index_entries_read"] = {k: h1[k] - h0.get(k, 0) for k in h1 if h1[k] != h0.get(k, 0)}
            c.close()
        except (pymysql.MySQLError, OSError):
            rec["index_entries_read"] = None
    t1 = time.time()
    g1 = global_status(a, *GS)
    rec.update(t0=t0, t1=t1, seconds=round(t1 - t0, 3), pool_read_requests=g1[GS[0]] - g0[GS[0]], disk_reads=g1[GS[1]] - g0[GS[1]])
    return rec


def scale_body(script_id, host, version, mix, share, rows):
    r = Results(script_id, ["EXP-O10-PRIMARY-SCALE", "FIND-O10-PRIMARY-SCALE"], version=version, mix=mix, share_of_capacity=share, rows=rows, buffer_pool_mb=256,
                statement_limit_ms=STATEMENT_LIMIT_MS, split_seconds_ceiling=SPLIT_SECONDS, tolerance_fixed_in_advance="As EXP-R8: a median at least 20% lower and no overlap. An arm that reached its limit is reported as such.",
                limits_of_this_test="One size and rate per script, small server. A result here does not make a general rule beyond the sizes and rates measured.")
    with r.section("build") as d:
        a = build(host, rows)
        d["pages"] = {x[0]: int(x[1]) for x in query(a, "SELECT index_name, stat_value FROM mysql.innodb_index_stats WHERE database_name='bench' AND table_name='t' AND stat_name='size'")}
        d["pool_pages"] = query(a, "SELECT @@innodb_buffer_pool_size / @@innodb_page_size")[0][0]
    cfg = MIXES[mix]
    with r.section("rate") as d:
        d["closed_loop_per_s"] = round(capacity(host, [s for s, _w, _f in cfg], rows))
        total = max(10, d["closed_loop_per_s"] * share)
        d["target_writes_per_s"] = round(total)
    loads = [Workload("bench.t", host=host, max_id=rows, head=rows, readers=1 if i == 0 else 0, writers=w, rate=max(1, total * f / w), write_sql=s, read_sql="SELECT * FROM bench.t WHERE id=%s") for i, (s, w, f) in enumerate(cfg)]
    sampler = Sampler("t", host=host, data_locks=False)
    for w in loads:
        w.start()
    pin(METHOD_CPU)
    sampler.start()
    time.sleep(10)
    c = connect(host, read_timeout=60)
    plans = {"split_pk": forced_plan(c, "bench", "t", "PRIMARY"), "split_k": forced_plan(c, "bench", "t", "k_k")}
    c.close()
    names = ["statement_k", "statement_pk", "split_pk", "control"] + ([] if mix == "indexed" else ["split_k"])  # k moves under "indexed": not walked
    runs, hits, control_seconds = [], {}, 30
    with r.section("arms") as d:
        d["runs"] = runs
        for rep in range(5):
            for name in names[rep % len(names):] + names[:rep % len(names)]:
                # Two hits are enough: the arm's answer is 'over the limit', and repeating it would crowd out the others.
                if hits.get(name, 0) >= 2:
                    continue
                need = control_seconds + 30 if name == "control" else (SPLIT_SECONDS if name.startswith("split") else STATEMENT_LIMIT_MS / 1000) + 30
                if r.time_left() < need:
                    d.setdefault("not_run_near_limit", []).append(f"{rep}/{name}")
                    continue
                rec = run_arm(host, a, name, plans, control_seconds, rows)
                rec["rep"] = rep
                runs.append(rec)
                if name == "statement_pk" and rec["status"] == "complete":
                    control_seconds = max(5, min(rec["seconds"], 120))
                if name != "control" and rec["status"] != "complete":
                    hits[name] = hits.get(name, 0) + 1
                r.save()
                print(rep, name, rec["seconds"], rec["status"], flush=True)
                time.sleep(5)
    for w in loads:
        w.finish()
    sampler.finish()
    with r.section("windows") as d:
        for rec in runs:
            rec["write"] = [w.window(rec["t0"], rec["t1"])["write"] for w in loads]
            # The 10 s before the arm: a backlog carried over from the previous arm shows up here, not in that arm.
            rec["write_10s_before"] = [w.window(rec["t0"] - 10, rec["t0"])["write"] for w in loads]
            rec["read"] = loads[0].window(rec["t0"], rec["t1"])["read"]
            s = sampler.window(rec["t0"] - 1, rec["t1"] + 1)
            rec["server"] = {k: s.get(k) for k in ("hll_peak", "peak_trx_age_s", "bp_disk_reads_per_s", "bp_miss_pct", "peak_threads_running")}
            after = sampler.window(rec["t1"], rec["t1"] + 6)
            rec["miss_pct_just_after"] = after.get("bp_miss_pct")
    r.finish()
