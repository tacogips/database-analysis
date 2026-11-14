"""Run bodies for EXP-O6-WRITE-MIX (second design) and EXP-O8-PKONLY-THREADS. Lab only, private tables.

The first write-mix design ran an unbounded statement first on one shared connection, and one timeout lost every other arm.
Here every arm has its own connection, the splits run first in each repeat, every arm has a server-side ceiling, and a failed arm is saved and passed over.
"""
import threading, time
import pymysql
from countlib import forced_plan, session_setup, split_count
from lablib import METHOD_CPU, Sampler, Workload, connect, pin, query
from reqlib import Results, builder, global_status, measured_run, reset_schema

ROWS = 20_000_000
SPLIT_SECONDS = 150  # the request suggested about 300 s; four arms at 300 s would not fit the runner's 20 minute limit
STATEMENT_LIMIT_MS = 120_000
GS = ("Innodb_buffer_pool_read_requests", "Innodb_buffer_pool_reads")
MIXES = {
    "indexed": {"k": "CRC32(n+{off}) MOD 1000000",
                "loads": [("UPDATE req_mix.t SET k=k+1 WHERE id=%s", 4, 1.0)],
                "note": "k moves, so split_k is a labelled negative control: the guideline forbids walking it."},
    "uniform_insert_delete": {"k": "CRC32(n+{off}) MOD 1000000",
                              "loads": [("INSERT IGNORE INTO req_mix.t (id,k,v) SELECT x, MOD(x,1000000), 0 FROM (SELECT %s+30000000 AS x) d", 2, .5),
                                        ("DELETE FROM req_mix.t WHERE id=%s", 2, .5)],
                              "note": "Both touch every index at random places. k does not move, so split_k is eligible."},
    "monotonic_insert_delete": {"k": "n+{off}",
                                "loads": [("INSERT INTO req_mix.t (k,v) SELECT UNIX_TIMESTAMP(NOW(6))*1000000 + %s*0, 0", 2, .5),
                                          ("DELETE FROM req_mix.t WHERE id >= %s*0 ORDER BY id LIMIT 1", 1, .5)],
                                "note": "New keys at the end of both indexes and the oldest row deleted, as in a rolling log table."},
}


def _handler(c):
    return {k: int(v) for k, v in query(c, "SHOW SESSION STATUS LIKE 'Handler_read%'")}


def _arm(b, kind, plan=None, index=None, ceiling="rows"):
    """One arm on its own connection. Whatever happens, it returns a record.
    ceiling="rows" is what the guideline prescribes. The step ceiling of the earlier runs stopped walks whose width had shrunk."""
    c = connect(db="req_mix", read_timeout=(SPLIT_SECONDS if kind == "split" else STATEMENT_LIMIT_MS / 1000) + 30)
    session_setup(c, 0)
    g0, h0, t0 = global_status(b, *GS), _handler(c), time.time()
    rec = {"kind": kind, "index": index or plan["index"]}
    try:
        if kind == "split":
            limits = {"max_rows": ROWS * 3} if ceiling == "rows" else {"max_steps": ROWS // 10_000 * 3}
            step_log = []
            s = split_count(c, "t", plan, width=10_000, limit_ms=500, duty=None, max_seconds=SPLIT_SECONDS, db="req_mix", on_step=lambda _n, ms, _c: step_log.append((round(time.time() - t0, 2), round(ms, 1))), **limits)
            rec["slowest_steps_at_s_and_ms"] = sorted(step_log, key=lambda x: -x[1])[:10]
            by_10s = {}
            for at, ms in step_log:
                key = str(int(at // 10) * 10)
                by_10s[key] = max(by_10s.get(key, 0), ms)
            rec["longest_step_ms_by_10s"] = by_10s
            rec.update({k: s.get(k) for k in ("status", "rows", "partial_rows", "steps", "stmt_ms_p50", "stmt_ms_p99", "stmt_ms_max", "width_end", "width_reductions", "plan_check", "errors", "statements")})
        else:
            rec["rows"] = query(c, f"SELECT /*+ MAX_EXECUTION_TIME({STATEMENT_LIMIT_MS}) */ COUNT(*) FROM t FORCE INDEX (`{index}`)")[0][0]
            rec["status"] = "complete"
    except (pymysql.MySQLError, OSError) as e:
        rec.update(status=f"error_{e.args[0] if e.args else 0}", error=str(e)[:160])
    t1 = time.time()
    g1 = global_status(b, *GS)
    rec.update(t0=t0, t1=t1, seconds=round(t1 - t0, 3), pool_read_requests=g1[GS[0]] - g0[GS[0]], disk_reads=g1[GS[1]] - g0[GS[1]])
    rec["pool_read_requests_per_s"] = round(rec["pool_read_requests"] / max(t1 - t0, .001))
    try:
        h1 = _handler(c)
        rec["handler"] = {k: h1[k] - h0.get(k, 0) for k in h1 if h1[k] != h0.get(k, 0)}
    except (pymysql.MySQLError, OSError):
        rec["handler"] = None
    try:
        c.close()
    except Exception:
        pass
    return rec


def _hit_ceiling(rec):
    return rec["status"] != "complete"


def _client_timeout_probe(b):
    """EXP: a driver read timeout with no server limit. What happens to the server's statement, its lock and its read view?"""
    tag = "client_timeout_probe"
    c = connect(db="req_mix", read_timeout=5)
    query(c, "SET SESSION max_execution_time = 0")
    pid = c.thread_id()
    t0 = time.monotonic()
    out = {"client_read_timeout_s": 5}
    try:
        query(c, f"SELECT /* {tag} */ COUNT(*) FROM t FORCE INDEX (k_k)")
        out["client_saw"] = "finished within the client timeout"
    except (pymysql.MySQLError, OSError) as e:
        out["client_saw"] = f"error {e.args[0] if e.args else 0}"
    out["client_returned_s"] = round(time.monotonic() - t0, 2)
    seen = []
    while time.monotonic() - t0 < 25:
        row = query(b, "SELECT PROCESSLIST_TIME, PROCESSLIST_STATE FROM performance_schema.threads WHERE PROCESSLIST_ID=%s AND PROCESSLIST_INFO IS NOT NULL", (pid,))
        mdl = query(b, """SELECT COUNT(*) FROM performance_schema.metadata_locks m JOIN performance_schema.threads t ON t.THREAD_ID=m.OWNER_THREAD_ID
                          WHERE t.PROCESSLIST_ID=%s AND m.OBJECT_NAME='t'""", (pid,))[0][0]
        seen.append({"at_s": round(time.monotonic() - t0, 1), "server_statement_running": bool(row), "mdl_rows": mdl})
        if not row:
            break
        time.sleep(1)
    out["seen"] = seen[:30]
    out["still_running_when_probe_ended"] = bool(seen and seen[-1]["server_statement_running"])
    if out["still_running_when_probe_ended"]:
        query(b, f"KILL QUERY {int(pid)}")
        t1 = time.monotonic()
        while time.monotonic() - t1 < 20 and query(b, "SELECT 1 FROM performance_schema.threads WHERE PROCESSLIST_ID=%s AND PROCESSLIST_INFO IS NOT NULL", (pid,)):
            time.sleep(.2)
        out["seconds_after_kill_until_gone"] = round(time.monotonic() - t1, 2)
    try:
        c.close()
    except Exception:
        pass
    return out


SERVER_LOG = ("Innodb_buffer_pool_pages_dirty", "Innodb_buffer_pool_pages_flushed", "Innodb_buffer_pool_wait_free", "Innodb_os_log_written", "Innodb_data_fsyncs", "Innodb_buffer_pool_reads", "Innodb_row_lock_current_waits", "Threads_running")


def _server_log(halt, rows):
    """1 Hz: flushing, redo, reads and the history list, to see what a stall lines up with. Keeps going through the whole run."""
    c = connect(read_timeout=20)
    while not halt.is_set():
        t = time.time()
        try:
            rows.append({"t": round(t, 1), "hll": query(c, "SELECT `count` FROM information_schema.INNODB_METRICS WHERE name='trx_rseg_history_len'")[0][0], **global_status(c, *SERVER_LOG)})
        except (pymysql.MySQLError, OSError) as e:
            rows.append({"t": round(t, 1), "error": str(e)[:80]})
            c = connect(read_timeout=20)
        halt.wait(max(0, 1 - (time.time() - t)))
    c.close()


def _window_of(rec, loads, sampler):
    rec["write"] = [w.window(rec["t0"], rec["t1"])["write"] for w in loads]
    rec["read"] = loads[0].window(rec["t0"], rec["t1"])["read"]
    s = sampler.window(rec["t0"] - 1, rec["t1"] + 1)
    rec["server"] = {k: s.get(k) for k in ("hll_peak", "peak_trx_age_s", "bp_disk_reads_per_s", "peak_threads_running")}
    rec["committed_writes_in_window"] = sum(x["n"] - x["errors"] - x["stuck_at_end"] for x in rec["write"])


def write_mix2_body(script_id, mix, total_rate, split_only=False, tail_splits=0, answers=None):
    """split_only leaves out the statement arms, for re-running the splits under the row ceiling. tail_splits adds that many
    PRIMARY splits at the end of the run, each after a no-count control of the same length, with a 1 Hz server log."""
    cfg = MIXES[mix]
    r = Results(script_id, answers or ["EXP-O6-WRITE-MIX", "FIND-O6-WRITE-SHAPE-01", "FIND-O8-CLIENT-TIMEOUT"], mix=mix, target_writes_per_s=total_rate, split_only=split_only, tail_splits=tail_splits, ceiling="max_rows and max_seconds", rows=ROWS, note=cfg["note"], split_seconds_ceiling=SPLIT_SECONDS, statement_limit_ms=STATEMENT_LIMIT_MS,
                split_settings={"width": 10_000, "limit_ms": 500, "duty": None}, tolerance_fixed_in_advance="As EXP-R8: a median at least 20% lower and no overlap between the fastest and slowest repeats. An arm that reached its ceiling is reported as such, not as a time.")
    b = builder()
    reset_schema(b, "req_mix")
    query(b, "USE req_mix")
    with r.section("build") as d:
        query(b, "CREATE TABLE t (id BIGINT PRIMARY KEY AUTO_INCREMENT, k BIGINT NOT NULL, v INT NOT NULL, pad CHAR(60) NOT NULL DEFAULT 'x', KEY k_k (k))")
        for off in range(0, ROWS, 1_000_000):
            query(b, f"INSERT INTO t (id,k,v) SELECT n+{off}, {cfg['k'].format(off=off)}, 0 FROM bench.seq1m")
        query(b, "ANALYZE TABLE t")
        d["pages"] = {x[0]: int(x[1]) for x in query(b, "SELECT index_name, stat_value FROM mysql.innodb_index_stats WHERE database_name='req_mix' AND table_name='t' AND stat_name='size'")}
    loads = []
    for i, (sql, writers, share) in enumerate(cfg["loads"]):
        rate = max(1, total_rate * share / writers)
        loads.append(Workload("req_mix.t", max_id=ROWS, head=ROWS, readers=1 if i == 0 else 0, writers=writers, rate=rate, write_sql=sql, read_sql="SELECT * FROM req_mix.t WHERE id=%s"))
    sampler = Sampler("t", data_locks=False)
    for w in loads:
        w.start()
    pin(METHOD_CPU)
    sampler.start()
    time.sleep(10)
    c = connect(db="req_mix", read_timeout=60)
    session_setup(c, 0)
    plan_k, plan_pk = forced_plan(c, "req_mix", "t", "k_k"), forced_plan(c, "req_mix", "t", "PRIMARY")
    c.close()
    runs = []
    hits = {}
    arms = {"split_k": lambda: _arm(b, "split", plan=plan_k), "split_pk": lambda: _arm(b, "split", plan=plan_pk),
            "statement_k": lambda: _arm(b, "statement", index="k_k"), "statement_pk": lambda: _arm(b, "statement", index="PRIMARY")}
    with r.section("arms") as d:
        d["runs"] = runs
        for rep in range(4):
            order = (["split_k", "split_pk"] if rep % 2 == 0 else ["split_pk", "split_k"]) + ([] if split_only else (["statement_k", "statement_pk"] if rep % 2 == 0 else ["statement_pk", "statement_k"]))
            for name in order:
                if hits.get(name, 0) >= 2:
                    continue  # two hits on a ceiling answer the question, and more would crowd out the other arms
                need = (SPLIT_SECONDS if name.startswith("split") else STATEMENT_LIMIT_MS / 1000) + 60
                if r.time_left() < need:
                    d.setdefault("not_run_near_limit", []).append(f"{rep}/{name}")
                    continue
                rec = arms[name]()
                rec.update(rep=rep, arm=name)
                runs.append(rec)
                if _hit_ceiling(rec):
                    hits[name] = hits.get(name, 0) + 1
                r.save()
                print(rep, name, rec["seconds"], rec["status"], rec.get("stmt_ms_max"), flush=True)
    tail, log_rows, log_halt = [], [], threading.Event()
    if tail_splits:
        threading.Thread(target=_server_log, args=(log_halt, log_rows), daemon=True).start()
        with r.section("tail") as d:
            d["runs"] = tail
            length = 30
            for i in range(tail_splits):
                if r.time_left() < 2 * SPLIT_SECONDS + 60:
                    d.setdefault("not_run_near_limit", []).append(i)
                    continue
                control = {"arm": "control", "rep": i, "t0": time.time(), "seconds": length, "status": "nothing ran"}
                time.sleep(length)
                control["t1"] = time.time()
                tail.append(control)
                rec = _arm(b, "split", plan=plan_pk)
                rec.update(rep=i, arm="split_pk")
                tail.append(rec)
                length = max(5, min(rec["seconds"], SPLIT_SECONDS))
                r.save()
                print("tail", i, rec["seconds"], rec["status"], rec.get("stmt_ms_max"), flush=True)
    if total_rate >= 800 and not split_only:
        with r.section("client_timeout") as d:
            d.update(_client_timeout_probe(b))
    drift = {}
    with r.section("drift_arm") as d:
        # The last arm: a split, then the writers stop at once, so its sum can be set against a true count taken with nothing running.
        if r.time_left() > SPLIT_SECONDS + 60:
            drift = _arm(b, "split", plan=plan_pk)
            d.update({k: drift.get(k) for k in ("status", "rows", "partial_rows", "seconds", "t0", "t1", "steps", "stmt_ms_max", "slowest_steps_at_s_and_ms", "longest_step_ms_by_10s")})
    log_halt.set()
    for w in loads:
        w.finish()
    sampler.finish()
    with r.section("windows") as d:
        for rec in runs + tail + ([drift] if drift else []):
            _window_of(rec, loads, sampler)
        if drift:
            d["drift_arm_window"] = {k: drift.get(k) for k in ("write", "read", "server", "committed_writes_in_window")}
        if log_rows:
            d["server_log_1hz"] = log_rows
        # Writes that took over a second, by time, to tell whether the tail sits inside an arm or between arms.
        stalls = sorted((round(s[0] - runs[0]["t0"], 1), round(s[2])) for w in loads for s in w.samples if s[1] == "write" and s[2] > 1000) if runs else []
        d["writes_over_1s_at_s_and_ms"] = stalls[:300]
        d["writes_over_1s_total"] = len(stalls)
        truth = query(b, "SELECT COUNT(*) FROM t FORCE INDEX (PRIMARY)")[0][0]
        d["quiet_true_count_after"] = truth
        if drift.get("rows") is not None:
            changes = sum(x["n"] - x["errors"] - x["stuck_at_end"] for x in (w.window(drift["t0"], drift["t1"])["write"] for w in loads))
            d["drift"] = {"sum": drift["rows"], "true_count_after": truth, "difference": drift["rows"] - truth, "write_statements_during_split": changes, "within_write_statements": abs(drift["rows"] - truth) <= changes}
        whole = [w.window(sampler.rows[0]["t"], sampler.rows[-1]["t"])["write"] for w in loads]
        d["whole_run_write"] = whole
    query(b, "DROP DATABASE req_mix")
    r.finish()


def pkonly_body(script_id, rows, repeat_seconds=None):
    """COUNT(*) with no hint on a table with no secondary index, at several read-thread settings, with a control of equal length.
    Updates are to a non-key column, which still changes the clustered index this count reads."""
    r = Results(script_id, ["EXP-O8-PKONLY-THREADS", "FIND-O8-PKONLY-PARALLEL"], rows=rows, base_s=10, recover_s=10, tolerance_fixed_in_advance="As EXP-R8: a median at least 20% lower and no overlap between repeats.")
    b = builder()
    reset_schema(b, "req_pk")
    query(b, "USE req_pk")
    with r.section("build") as d:
        query(b, "CREATE TABLE t (id BIGINT PRIMARY KEY, v INT NOT NULL, pad CHAR(60) NOT NULL DEFAULT 'x')")
        for off in range(0, rows, 1_000_000):
            query(b, f"INSERT INTO t (id,v) SELECT n+{off}, 0 FROM bench.seq1m WHERE n<={min(1_000_000, rows - off)}")
        query(b, "ANALYZE TABLE t")
        d["size"] = [list(x) for x in query(b, "SELECT DATA_LENGTH, INDEX_LENGTH FROM information_schema.TABLES WHERE TABLE_SCHEMA='req_pk' AND TABLE_NAME='t'")]
        d["buffer_pool"] = query(b, "SELECT @@innodb_buffer_pool_size")[0][0]
        d["default_threads"] = query(b, "SELECT @@GLOBAL.innodb_parallel_read_threads")[0][0]
        d["explain"] = [str(x) for x in query(b, "EXPLAIN SELECT COUNT(*) FROM t")[0]]
        if repeat_seconds:
            query(b, "SELECT COUNT(*) FROM t")
            query(b, "SELECT COUNT(*) FROM t")
    WL = dict(table="req_pk.t", max_id=rows, head=rows, readers=4, writers=4, rate=25, write_sql="UPDATE req_pk.t SET v=v+1 WHERE id=%s", read_sql="SELECT * FROM req_pk.t WHERE id=%s")
    durations = []

    def count_at(threads):
        def body(notes):
            c = connect(db="req_pk", read_timeout=150)
            query(c, f"SET SESSION innodb_parallel_read_threads={threads}")
            notes["threads_read_back"] = query(c, "SELECT @@SESSION.innodb_parallel_read_threads")[0][0]
            g0 = global_status(c, *GS)
            times = []
            t_all = time.monotonic()
            while True:
                t = time.monotonic()
                try:
                    notes["rows"] = query(c, "SELECT /*+ MAX_EXECUTION_TIME(120000) */ COUNT(*) FROM t")[0][0]
                except pymysql.MySQLError as e:
                    notes["error"] = e.args[0]
                    break
                times.append(round(time.monotonic() - t, 3))
                if not repeat_seconds or time.monotonic() - t_all >= repeat_seconds:
                    break
            g1 = global_status(c, *GS)
            notes.update(statement_seconds=times, disk_reads=g1[GS[1]] - g0[GS[1]], pool_read_requests=g1[GS[0]] - g0[GS[0]])
            durations.extend(times)
            c.close()
        return body

    def control(notes):
        notes["control_seconds"] = repeat_seconds or round(sum(durations) / len(durations), 1) if durations or repeat_seconds else 20
        time.sleep(notes["control_seconds"])

    arms = {"threads_1": count_at(1), "threads_2": count_at(2), "threads_4": count_at(4), "control": control}
    names = list(arms)
    with r.section("runs") as d:
        d["runs"] = []
        for rep in range(3):
            for name in names[rep % 4:] + names[:rep % 4]:
                if r.time_left() < 150:
                    d.setdefault("not_run_near_limit", []).append(f"{rep}/{name}")
                    continue
                res = measured_run(name, WL, arms[name], 10, 10, table="t")
                res.pop("series_10s", None)
                res.update(arm=name, rep=rep)
                d["runs"].append(res)
                r.save()
                print(rep, name, res["method_seconds"], res["latency"]["during"]["write"]["p50_ms"], res["notes"].get("statement_seconds"), flush=True)
    query(b, "DROP DATABASE req_pk")
    r.finish()
