"""Partitioned write-mix diagnostics. Private compose-lab fixtures only.

Each required opportunity has a record before setup. Two rounds per method group
run in separate scripts. A failure is saved, fails scope, and stops later arms.
Quiet-count differences are diagnostics, never a write-count drift guarantee.
"""
import threading, time
import pymysql
from countlib import TIMER_COUNTER, forced_plan, session_setup, split_count
from lablib import METHOD_CPU, Sampler, Workload, connect, pin, query
from reqlib import Results, builder, global_status, measured_run, reset_schema
from raw_evidence import write_samples
from protocol_outcomes import classify_outcome, close_owned, opportunity_schedule, scope_complete

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
    rec = {"kind": kind, "index": index or plan["index"], "status": "not_started", "t0": time.time()}
    c, pid = None, None
    try:
        g0 = global_status(b, *GS)
        c = connect(db="req_mix", read_timeout=(SPLIT_SECONDS if kind == "split" else STATEMENT_LIMIT_MS / 1000) + 30)
        pid = c.thread_id()
        rec["connection_id"] = pid
        session_setup(c, 0)
        h0 = _handler(c)
        if kind == "split":
            limits = {"max_rows": ROWS * 3} if ceiling == "rows" else {"max_steps": ROWS // 10_000 * 3}
            step_log = []
            result = split_count(c, "t", plan, width=10_000, limit_ms=500, duty=None,
                max_seconds=SPLIT_SECONDS, db="req_mix", on_step=lambda _n, ms, _c:
                    step_log.append((round(time.time()-rec["t0"], 2), round(ms, 1))), **limits)
            rec.update(result)  # preserve the complete raw stop/error evidence
            rec["slowest_steps_at_s_and_ms"] = sorted(step_log, key=lambda x: -x[1])[:10]
            rec["longest_step_ms_by_10s"] = {}
            for at, ms in step_log:
                key = str(int(at // 10) * 10)
                rec["longest_step_ms_by_10s"][key] = max(rec["longest_step_ms_by_10s"].get(key, 0), ms)
        else:
            rec["timer_counter_before"] = int(query(c, TIMER_COUNTER)[0][1])
            rec["rows"] = query(c, f"SELECT /*+ MAX_EXECUTION_TIME({STATEMENT_LIMIT_MS}) */ COUNT(*) FROM t FORCE INDEX (`{index}`)")[0][0]
            rec["status"] = "complete"
    except Exception as error:
        rec.update(status=f"error_{error.args[0] if error.args else 0}", error=repr(error))
    finally:
        rec["t1"] = time.time()  # actual method, excluding cleanup below
        rec["seconds"] = round(rec["t1"]-rec["t0"], 3)
        if c:
            for field, fn in (("handler", lambda: {k: v-h0.get(k, 0) for k, v in _handler(c).items()}),
                              ("timer_counter_after", lambda: int(query(c, TIMER_COUNTER)[0][1]))):
                try:
                    rec[field] = fn()
                except Exception as error:
                    rec[field] = None
                    rec[field+"_error"] = repr(error)
            admin = None
            try:
                admin = connect(read_timeout=5)
                rec["cleanup"] = close_owned(admin, c, pid, query)
            except Exception as error:
                rec["cleanup"] = {"confirmed": False, "error": repr(error)}
                try:
                    c.close()
                except Exception:
                    pass
            finally:
                if admin:
                    admin.close()
        else:
            rec["cleanup"] = {"confirmed": True, "status": "connection_not_created"}
        try:
            g1 = global_status(b, *GS)
            rec.update(pool_read_requests=g1[GS[0]]-g0[GS[0]], disk_reads=g1[GS[1]]-g0[GS[1]])
        except Exception as error:
            rec["measurement_error"] = repr(error)
        rec["cleanup_finished_at"] = time.time()
        rec["outcome"] = classify_outcome(rec)
        if rec.get("measurement_error"):
            rec["outcome"] = "unexpected_failure"
    return rec


def _hit_ceiling(rec):
    return classify_outcome(rec) == "confirmed_ceiling"


def _client_timeout_probe(b):
    c, pid = None, None
    out = {"client_read_timeout_s": 5, "t0": time.time(), "status": "not_started"}
    try:
        c = connect(db="req_mix", read_timeout=5)
        pid = c.thread_id()
        out["connection_id"] = pid
        query(c, "SET SESSION max_execution_time=0")
        try:
            query(c, "SELECT /* client_timeout_probe */ COUNT(*) FROM t FORCE INDEX (k_k)")
            out.update(status="complete", client_saw="finished within client timeout")
        except (pymysql.MySQLError, OSError) as error:
            # A disconnect is the hypothesis here, never a COUNT ceiling.
            out.update(status="observed_client_error" if error.args and error.args[0] in (2006,2013) else "probe_failed",
                       client_saw=repr(error), errno=error.args[0] if error.args else None,
                       interpretation="Observed disconnect in explicit5s driver-timeout probe; cause not proven; never a COUNT ceiling")
        out["client_returned_at"] = time.time()
        out["seen"] = []
        deadline = time.monotonic()+20
        while True:
            rows = query(b, "SELECT PROCESSLIST_STATE FROM performance_schema.threads WHERE PROCESSLIST_ID=%s AND PROCESSLIST_INFO IS NOT NULL", (pid,))
            out["seen"].append({"t": time.time(), "server_statement_running": bool(rows)})
            if not rows or time.monotonic() >= deadline:
                break
            time.sleep(1)
    except Exception as error:
        out.update(status="probe_failed", error=repr(error))
    finally:
        admin = None
        try:
            if c:
                admin = connect(read_timeout=5)
                out["cleanup"] = close_owned(admin, c, pid, query)
        except Exception as error:
            out["cleanup"] = {"confirmed": False, "error": repr(error)}
            c.close()
        finally:
            if admin:
                admin.close()
    out["t1"] = time.time()
    out["outcome"] = "completed" if out.get("cleanup", {}).get("confirmed") and out["status"] in ("complete", "observed_client_error") else "unexpected_failure"
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
    rec["server"] = sampler.window(rec["t0"], rec["t1"])


def write_mix2_body(script_id, mix, total_rate, split_only=False, tail_splits=0, answers=None,
                    group="splits", block=0, tail_repeat=0):
    cfg = MIXES[mix]
    if group in ("splits", "statements"):
        opportunities = opportunity_schedule(group, block)
    elif group == "tail":
        opportunities = [{"rep": tail_repeat, "arm": "control", "outcome": "not_started"},
                         {"rep": tail_repeat, "arm": "split_pk", "outcome": "not_started"}]
        if tail_repeat % 2:
            opportunities.reverse()
    elif group == "auxiliary":
        opportunities = [{"rep": 0, "arm": "drift_split", "outcome": "not_started"}]
        if total_rate >= 800 and not split_only:
            opportunities.insert(0, {"rep": 0, "arm": "client_timeout", "outcome": "not_started"})
    else:
        raise ValueError(group)
    r = Results(script_id, answers or ["EXP-O6-WRITE-MIX", "FIND-O6-WRITE-SHAPE-01", "FIND-O8-CLIENT-TIMEOUT"],
        protocol="partitioned-v3", mix=mix, target_writes_per_s=total_rate, group=group, block=block,
        rows=ROWS, note=cfg["note"], split_seconds_ceiling=SPLIT_SECONDS, statement_limit_ms=STATEMENT_LIMIT_MS,
        split_settings={"width": 10_000, "limit_ms": 500, "duty": None},
        required_repeats_across_scripts=[0,1,2,3], scopeComplete=False, opportunities=opportunities,
        tolerance_fixed_in_advance="Completed medians require all4repeats,20% lower and no overlap. Censored outcomes cannot rank completed speed.",
        limitations="Uncontrolled host/cache carryover,10s warmup and5s recovery do not certify restoration or SLO. Main arms have no matched no-count control. Auxiliary quiet truth is not a bounded drift estimate.")
    b, loads, sampler, runs = None, [], None, []
    log_rows, log_halt, log_thread = [], threading.Event(), None
    cleanup = r.data["cleanup"] = {"confirmed": False, "workloads": []}
    try:
        b = builder()
        reset_schema(b, "req_mix")
        query(b, "USE req_mix")
        with r.section("build") as d:
            query(b, "CREATE TABLE t (id BIGINT PRIMARY KEY AUTO_INCREMENT, k BIGINT NOT NULL, v INT NOT NULL, pad CHAR(60) NOT NULL DEFAULT 'x', KEY k_k(k))")
            for off in range(0, ROWS, 1_000_000):
                query(b, f"INSERT INTO t (id,k,v) SELECT n+{off}, {cfg['k'].format(off=off)}, 0 FROM bench.seq1m")
            query(b, "ANALYZE TABLE t")
            d["pages"] = {x[0]: int(x[1]) for x in query(b, "SELECT index_name,stat_value FROM mysql.innodb_index_stats WHERE database_name='req_mix' AND table_name='t' AND stat_name='size'")}
        if r.errors:
            raise RuntimeError("fixture incomplete")
        # Reserve the entire declared group, not just the next arm. No shortened
        # ceilings and no two-hit suppression. Setup exhaustion is incomplete.
        reserve = sum(150 if x['arm'] in ('control', 'drift_split') or x['arm'].startswith('split') else
                      60 if x['arm'] == 'client_timeout' else 120 for x in opportunities) + 90
        r.data["required_seconds_after_setup"] = reserve
        if r.time_left() < reserve:
            for item in opportunities:
                item.update(outcome="budget_insufficient", reason="setup consumed group reserve")
            raise RuntimeError("setup consumed required comparison reserve")
        for i, (sql, writers, share) in enumerate(cfg["loads"]):
            w = Workload("req_mix.t", max_id=ROWS, head=ROWS, readers=1 if i == 0 else 0,
                writers=writers, rate=max(1, total_rate*share/writers), write_sql=sql,
                read_sql="SELECT * FROM req_mix.t WHERE id=%s")
            w.start()
            loads.append(w)
        sampler = Sampler("t", data_locks=False)
        pin(METHOD_CPU)
        sampler.start()
        if group == "tail":
            log_thread = threading.Thread(target=_server_log, args=(log_halt, log_rows), daemon=True)
            log_thread.start()
        time.sleep(10)
        c = connect(db="req_mix", read_timeout=60)
        try:
            session_setup(c, 0)
            plans = {"split_k": forced_plan(c, "req_mix", "t", "k_k"), "split_pk": forced_plan(c, "req_mix", "t", "PRIMARY")}
        finally:
            c.close()
        with r.section("arms") as d:
            d["runs"] = runs
            for item in opportunities:
                name = item["arm"]
                item["outcome"] = "started"
                r.save()
                if name == "control":
                    rec = {"status": "control", "outcome": "completed", "t0": time.time()}
                    time.sleep(SPLIT_SECONDS)
                    rec.update(t1=time.time(), seconds=SPLIT_SECONDS)
                elif name == "client_timeout":
                    rec = _client_timeout_probe(b)
                else:
                    actual = "split_pk" if name == "drift_split" else name
                    rec = _arm(b, "split", plan=plans[actual]) if actual.startswith("split") else _arm(b, "statement", index="k_k" if name == "statement_k" else "PRIMARY")
                rec.update(rep=item["rep"], arm=name)
                runs.append(rec)
                item.update(outcome=rec["outcome"], run_index=len(runs)-1)
                r.save()  # preserve errors and cleanup before semantic failure
                if rec["outcome"] not in ("completed", "confirmed_ceiling"):
                    raise RuntimeError("unexpected or unconfirmed outcome; later opportunities blocked")
                print(item["rep"], name, rec.get("seconds"), rec["status"], flush=True)
                time.sleep(5)
        if r.errors:
            raise RuntimeError("arms incomplete")
    except Exception as error:
        r.errors.append("protocol:"+repr(error))
    finally:
        unrecorded_started = any(item["outcome"] == "started" for item in opportunities)
        for item in opportunities:
            if item["outcome"] == "started":
                item.update(outcome="unexpected_failure", reason="section exception before arm result; cleanup unknown")
            if item["outcome"] == "not_started":
                item["reason"] = "prior_failure_or_section_exception; no later comparison started"
        log_halt.set()
        if log_thread:
            log_thread.join(25)
            if log_thread.is_alive():
                r.errors.append("server_log_worker_unterminated")
        for w in loads:
            try:
                w.finish(confirm_server=True)
            except Exception as error:
                r.errors.append("workload_stop:"+repr(error))
            cleanup["workloads"].append(getattr(w, "cleanup", {"confirmed":False}))
        if sampler:
            try:
                sampler.finish()
            except Exception as error:
                r.errors.append("sampler_stop:"+repr(error))
        cleanup["confirmed"] = not unrecorded_started and all(x.get("confirmed") for x in cleanup["workloads"]) and all(
            x.get("cleanup", {"confirmed": True}).get("confirmed") for x in runs)
        with r.section("windows") as d:
            if loads and sampler:
                for rec in runs:
                    _window_of(rec, loads, sampler)
                d["arrival_accounting"] = [w.accounting for w in loads]
                d["raw_service_samples"] = [write_samples("/work/results", f"requested_{script_id}", f"service-{i}", w.samples) for i, w in enumerate(loads)]
                d["raw_server_samples"] = write_samples("/work/results", f"requested_{script_id}", "server", sampler.rows)
            if log_rows:
                d["raw_stall_server_samples"] = write_samples("/work/results", f"requested_{script_id}", "stall-server", log_rows)
            d["quiet_truth_status"] = "not_requested; no write-count drift bound claimed"
        # No quiet truth or DROP is attempted while an owned statement may live.
        if b:
            if cleanup["confirmed"] and not r.errors:
                with r.section("fixture_cleanup") as d:
                    query(b, "DROP DATABASE req_mix")
                    d["status"] = "dropped"
            else:
                r.data["fixture_cleanup"] = {"status": "retained_after_failure; no further SQL on builder"}
            admin = None
            try:
                admin = connect(read_timeout=5)
                cleanup["builder"] = close_owned(admin, b, b.thread_id(), query)
            except Exception as error:
                cleanup["builder"] = {"confirmed":False,"error":repr(error)}
                b.close()
            finally:
                if admin:
                    admin.close()
            cleanup["confirmed"] = cleanup["confirmed"] and cleanup["builder"]["confirmed"]
            if not cleanup["confirmed"]:
                r.errors.append("cleanup_unconfirmed; retained fixture; stop lab before later measurement")
        r.data["scopeComplete"] = scope_complete(opportunities) and cleanup["confirmed"] and not r.errors
        if not r.data["scopeComplete"]:
            r.errors.append("incomplete_scope")
        r.save()
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
