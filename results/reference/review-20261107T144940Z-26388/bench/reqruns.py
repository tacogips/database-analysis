"""Run bodies shared by more than one requested script: EXP-R8, EXP-R9, EXP-O6-WRITE-MIX and EXP-O6-LONG-UNDO. Lab only."""
import clocklib
import threading, time
import pymysql
from lablib import Sampler, Workload, connect, pin, query, METHOD_CPU
from countlib import session_setup, split_count, split_plans
from experiments import DENSE
from reqlib import Results, builder, measured_run, global_status, reset_schema

STATS = ("Innodb_buffer_pool_reads", "Innodb_buffer_pool_read_requests", "Innodb_data_read", "Innodb_rows_read")
DENSE_WRITE = "UPDATE t_dense SET status=MOD(status+1,5) WHERE id=%s"


def split_on_dense(notes, width=100_000, duty=None, limit_ms=5000, index=None):
    """SQL 4 on t_dense through its chosen index, with one fresh connection."""
    c = connect(read_timeout=900)
    session_setup(c, 0)
    plans = split_plans(c, "bench", "t_dense")
    plan = next(p for p in plans["plans"] if p["index"] == index) if index else plans["plans"][0]
    r = split_count(c, "t_dense", plan, width=width, limit_ms=limit_ms, duty=duty)
    c.close()
    notes.update({k: r.get(k) for k in ("status", "rows", "index", "form", "steps", "stmt_ms_p50", "stmt_ms_p99", "stmt_ms_max", "seconds_total", "plan_check", "width_end", "errors")})
    return r


def count_once(notes, sql, setup=()):
    c = connect(read_timeout=900)
    session_setup(c, 0)
    for s in setup:
        query(c, s)
    t = time.monotonic()
    notes["rows"] = query(c, sql)[0][0]
    notes["seconds_total"] = round(time.monotonic() - t, 3)
    c.close()


# ---- EXP-R8: repeats with the cache state controlled ----
def evict(admin):
    """Reads about the size of the buffer pool from an unrelated table, so t_dense's pages leave the pool."""
    t = time.monotonic()
    before = global_status(admin, *STATS)
    query(admin, "SET SESSION innodb_parallel_read_threads=1")
    query(admin, "SELECT COUNT(*) FROM t_repl FORCE INDEX (PRIMARY)")
    query(admin, "SELECT COUNT(*) FROM t_composite FORCE INDEX (PRIMARY)")
    after = global_status(admin, *STATS)
    return {"seconds": round(time.monotonic() - t, 1), "disk_reads": after["Innodb_buffer_pool_reads"] - before["Innodb_buffer_pool_reads"]}


R8_METHODS = {
    "default": lambda notes: count_once(notes, "SELECT COUNT(*) FROM t_dense"),
    "force_k_created": lambda notes: count_once(notes, "SELECT COUNT(*) FROM t_dense FORCE INDEX (k_created)"),
    "split_100k": lambda notes: split_on_dense(notes),
}


def r8_body(script_id, answers, reps):
    r = Results(script_id, answers, tolerance_fixed_in_advance="A method is called faster only if its median is at least 20% lower and the fastest and slowest repeats do not overlap. Anything else is reported as no established difference.",
                design="One workload runs throughout. Each cycle evicts, then runs a method cold, then the same method warm. The order of methods rotates by repeat.")
    admin = connect(read_timeout=900)
    wl = Workload(**DENSE)
    sampler = Sampler("t_dense", data_locks=False)
    wl.start()
    pin(METHOD_CPU)
    sampler.start()
    time.sleep(10)
    runs, stamps = [], []
    with r.section("plans") as d:
        d["default_explain"] = query(admin, "EXPLAIN SELECT COUNT(*) FROM t_dense")[0][:10]
        d["default_tree"] = query(admin, "EXPLAIN FORMAT=TREE SELECT COUNT(*) FROM t_dense")[0][0]
        d["forced_explain"] = query(admin, "EXPLAIN SELECT COUNT(*) FROM t_dense FORCE INDEX (k_created)")[0][:10]
    names = list(R8_METHODS)
    with r.section("runs") as d:
        d["runs"] = runs
        for rep in reps:
            order = names[rep % 3:] + names[:rep % 3]
            for name in order:
                if r.time_left() < 130:
                    d["stopped_early"] = f"rep {rep} {name}: too close to the script limit"
                    break
                ev = evict(admin)
                for state in ("cold", "warm"):
                    before = global_status(admin, *STATS)
                    notes = {}
                    t1 = clocklib.now()
                    R8_METHODS[name](notes)
                    t2 = clocklib.now()
                    after = global_status(admin, *STATS)
                    runs.append({"rep": rep, "method": name, "cache": state, "evict": ev if state == "cold" else None, "notes": notes, "wall_s": round(t2 - t1, 3),
                                 "disk_reads": after["Innodb_buffer_pool_reads"] - before["Innodb_buffer_pool_reads"],
                                 "bytes_read": after["Innodb_data_read"] - before["Innodb_data_read"]})
                    stamps.append((len(runs) - 1, t1, t2))
                    r.save()
                    print(rep, name, state, notes.get("seconds_total"), flush=True)
    wl.finish()
    sampler.finish()
    with r.section("windows") as d:
        for i, t1, t2 in stamps:
            runs[i]["latency"] = wl.window(t1, t2)
            s = sampler.window(t1 - 1, t2 + 1)
            runs[i]["server"] = {k: s.get(k) for k in ("hll_peak", "peak_trx_age_s", "bp_disk_reads_per_s", "peak_threads_running")}
    r.finish()


# ---- EXP-R9: a working set of 80% of the buffer pool ----
RATES = {"low": dict(readers=4, writers=4, rate=25), "high": dict(readers=6, writers=2, rate=100)}
BASE, RECOVER = 120, 120


def working_set(admin):
    data, rows = query(admin, "SELECT DATA_LENGTH,TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='t_dense'")[0]
    pool = query(admin, "SELECT @@innodb_buffer_pool_size")[0][0]
    return min(int(.8 * pool / data * rows), 99_000_000), {"data_length": data, "table_rows_estimate": rows, "buffer_pool": pool}


def warm(admin, head):
    """A scan's pages stay in the old part of the LRU list unless old_blocks_time is 0, so it is 0 only during warming."""
    old = query(admin, "SELECT @@GLOBAL.innodb_old_blocks_time")[0][0]
    t = time.monotonic()
    query(admin, "SET GLOBAL innodb_old_blocks_time=0")
    try:
        query(admin, f"SELECT COUNT(note) FROM t_dense FORCE INDEX (PRIMARY) WHERE id BETWEEN 1 AND {head}")
        query(admin, f"SELECT COUNT(note) FROM t_dense FORCE INDEX (PRIMARY) WHERE id BETWEEN 1 AND {head}")
    finally:
        query(admin, f"SET GLOBAL innodb_old_blocks_time={int(old)}")
    return {"seconds": round(time.monotonic() - t, 1), "old_blocks_time_restored": int(old)}


def pool_stats(admin):
    row = query(admin, """SELECT POOL_SIZE,DATABASE_PAGES,OLD_DATABASE_PAGES,PAGES_MADE_YOUNG,PAGES_NOT_MADE_YOUNG,HIT_RATE
                          FROM information_schema.INNODB_BUFFER_POOL_STATS""")
    return [list(x) for x in row]


def cpu_only(notes, seconds=20):
    """EXP-O6-CPU-CONTROL: one connection burns server CPU for as long as a count takes, touching no table and no buffer pool page."""
    c = connect(read_timeout=120)
    killer = connect(read_timeout=20)
    before = global_status(killer, *STATS)
    timer = threading.Timer(seconds, lambda: query(killer, f"KILL QUERY {c.thread_id()}"))
    timer.start()
    t = time.monotonic()
    try:
        query(c, "SELECT BENCHMARK(100000000000, SHA2('x',256))")
        notes["ended"] = "finished before the timer"
    except pymysql.MySQLError as e:
        notes["ended"] = f"killed: {e.args[0]}"
    timer.cancel()
    timer.join(5)
    notes["seconds_total"] = round(time.monotonic() - t, 2)
    after = global_status(killer, *STATS)
    notes["buffer_pool_read_requests_delta"] = after["Innodb_buffer_pool_read_requests"] - before["Innodb_buffer_pool_read_requests"]
    notes["disk_reads_delta"] = after["Innodb_buffer_pool_reads"] - before["Innodb_buffer_pool_reads"]
    c.close()
    killer.close()


R9_ARMS = {
    "control": None,
    "cpu_only": cpu_only,
    "default": lambda notes: count_once(notes, "SELECT COUNT(*) FROM t_dense"),
    "split_nosleep": lambda notes: split_on_dense(notes),
    "split_duty50": lambda notes: split_on_dense(notes, duty=.5),
}


def r9_body(script_id, answers, runs):
    """`runs` is a list of (rate, arm). Windows: baseline 120 s, method, recovery 120 s. Control's method window is 60 s."""
    r = Results(script_id, answers, windows={"baseline_s": BASE, "recovery_s": RECOVER}, rates=RATES)
    admin = connect(read_timeout=900)
    head, facts = working_set(admin)
    r.data["working_set"] = {"head_rows": head, **facts}
    with r.section("runs") as d:
        d["runs"] = []
        for rate, arm in runs:
            if r.time_left() < BASE + RECOVER + 100:
                d["stopped_early"] = f"{rate}/{arm}: too close to the script limit"
                break
            w = warm(admin, head)
            before = pool_stats(admin)
            wl = dict(DENSE, max_id=head, head=head, **RATES[rate])
            fn = R9_ARMS[arm]
            res = measured_run(f"{rate}/{arm}", wl, (lambda n: None) if fn is None else fn, BASE, RECOVER, during=60 if fn is None else None)
            res.update(rate=rate, arm=arm, warm=w, pool_before=before, pool_after=pool_stats(admin))
            d["runs"].append(res)
            r.save()
            lat = res["latency"]
            print(rate, arm, res["method_seconds"], "read p50", lat["baseline"]["read"]["p50_ms"], lat["during"]["read"]["p50_ms"], lat["recovery"]["read"]["p50_ms"], flush=True)
    r.finish()


# ---- EXP-O6-WRITE-MIX: the same methods under writes that touch the counted index ----
MIXES = {"nonindexed": ["UPDATE req_mix.t SET v=v+1 WHERE id=%s"],
         "indexed": ["UPDATE req_mix.t SET k=k+1 WHERE id=%s"],
         "insert_delete": ["INSERT IGNORE INTO req_mix.t (id,k,v) SELECT x, MOD(x,1000000), 0 FROM (SELECT %s+30000000 AS x) d", "DELETE FROM req_mix.t WHERE id=%s"]}
MIX_ROWS = 20_000_000


def _closed_loop_rate(statements, seconds=10, threads=8):
    """The rate the server takes flat out, with no count running. The fixed rate is a share of this."""
    done, halt = [0] * threads, threading.Event()

    def w(i):
        c = connect(read_timeout=30)
        n = i * 7919
        while not halt.is_set():
            n += 104729
            try:
                query(c, statements[n % len(statements)], (1 + n % MIX_ROWS,))
                done[i] += 1
            except pymysql.MySQLError:
                pass
        c.close()
    ts = [threading.Thread(target=w, args=(i,), daemon=True) for i in range(threads)]
    for t in ts:
        t.start()
    time.sleep(seconds)
    halt.set()
    for t in ts:
        t.join(5)
    return sum(done) / seconds


def write_mix_body(script_id, mix):
    from countlib import forced_plan
    r = Results(script_id, ["EXP-O6-WRITE-MIX", "FIND-O6-WRITE-SHAPE-01"], mix=mix, rows=MIX_ROWS, tolerance_fixed_in_advance="As EXP-R8: a median at least 20% lower and no overlap between the fastest and slowest repeats.",
                note="split_k under the 'indexed' mix is a negative control: the guideline forbids walking a column that moves.")
    b = builder()
    reset_schema(b, "req_mix")
    query(b, "USE req_mix")
    with r.section("build") as d:
        query(b, "CREATE TABLE t (id BIGINT PRIMARY KEY, k INT NOT NULL, v INT NOT NULL, pad CHAR(60) NOT NULL DEFAULT 'x', KEY k_k (k))")
        for off in range(0, MIX_ROWS, 1_000_000):
            query(b, f"INSERT INTO t (id,k,v) SELECT n+{off}, CRC32(n+{off}) MOD 1000000, 0 FROM bench.seq1m")
        query(b, "ANALYZE TABLE t")
        d["sizes"] = [list(x) for x in query(b, "SELECT index_name, stat_value FROM mysql.innodb_index_stats WHERE database_name='req_mix' AND table_name='t' AND stat_name='size'")]
    statements = MIXES[mix]
    with r.section("rate") as d:
        d["closed_loop_per_s"] = round(_closed_loop_rate(statements))
        target = min(d["closed_loop_per_s"] * .7, 800)  # the client's one CPU is the other limit
        d["fixed_rate_per_s"] = round(target)
    each = max(1, round(target / len(statements) / 4))
    loads = [Workload("req_mix.t", max_id=MIX_ROWS, head=MIX_ROWS, readers=2, writers=4, rate=each, write_sql=s, read_sql="SELECT * FROM req_mix.t WHERE id=%s") for s in statements]
    sampler = Sampler("t", data_locks=False)
    for w in loads:
        w.start()
    pin(METHOD_CPU)
    sampler.start()
    time.sleep(5)
    stamps = []
    c = connect(db="req_mix", read_timeout=300)
    session_setup(c, 0)
    plan_k, plan_pk = forced_plan(c, "req_mix", "t", "k_k"), forced_plan(c, "req_mix", "t", "PRIMARY")

    def statement(sql):
        def fn(notes):
            notes["rows"] = query(c, sql)[0][0]
        return fn

    def split(plan, **kw):
        def fn(notes):
            s = split_count(c, "t", plan, enforce_plan=False, duty=None, **kw)
            notes.update({k: s.get(k) for k in ("status", "rows", "steps", "stmt_ms_p50", "stmt_ms_p99", "stmt_ms_max", "width_end", "width_reductions", "errors")})
        return fn
    methods = {"statement_k": statement("SELECT COUNT(*) FROM t FORCE INDEX (k_k)"), "statement_pk": statement("SELECT COUNT(*) FROM t FORCE INDEX (PRIMARY)"),
               "split_k": split(plan_k, width=100_000, limit_ms=5000), "split_pk": split(plan_pk, width=100_000, limit_ms=5000)}
    names = list(methods)
    with r.section("runs") as d:
        d["runs"] = runs = []
        for rep in range(6):
            for name in names[rep % 4:] + names[:rep % 4]:
                if r.time_left() < 90:
                    d["stopped_early"] = f"{rep}/{name}"
                    break
                before, gb = {}, global_status(b, *STATS)
                for k, v in query(c, "SHOW SESSION STATUS LIKE 'Handler_read%'"):
                    before[k] = int(v)
                notes = {}
                t1 = clocklib.now()
                methods[name](notes)
                t2 = clocklib.now()
                after = {k: int(v) for k, v in query(c, "SHOW SESSION STATUS LIKE 'Handler_read%'")}
                ga = global_status(b, *STATS)
                runs.append({"rep": rep, "method": name, "seconds": round(t2 - t1, 3), "notes": notes, "handler": {k: after[k] - before.get(k, 0) for k in after if after[k] != before.get(k, 0)},
                             "pool_read_requests": ga["Innodb_buffer_pool_read_requests"] - gb["Innodb_buffer_pool_read_requests"], "disk_reads": ga["Innodb_buffer_pool_reads"] - gb["Innodb_buffer_pool_reads"]})
                stamps.append((len(runs) - 1, t1, t2))
                r.save()
                print(rep, name, runs[-1]["seconds"], notes.get("status"), flush=True)
    with r.section("short_limit") as d:
        # The guideline's own start: width 10000, 500 ms per statement. Does a step hit its limit under this mix?
        notes = {}
        split(plan_k, width=10_000, limit_ms=500, max_seconds=60)(notes)
        d["k"] = dict(notes)
        notes = {}
        split(plan_pk, width=10_000, limit_ms=500, max_seconds=60)(notes)
        d["pk"] = dict(notes)
    for w in loads:
        w.finish()
    sampler.finish()
    with r.section("windows") as d:
        for i, t1, t2 in stamps:
            runs[i]["write"] = [w.window(t1, t2)["write"] for w in loads]
            s = sampler.window(t1 - 1, t2 + 1)
            runs[i]["server"] = {k: s.get(k) for k in ("hll_peak", "peak_trx_age_s", "bp_disk_reads_per_s", "peak_threads_running")}
        d["quiet_truth_after"] = query(b, "SELECT COUNT(*) FROM t FORCE INDEX (PRIMARY)")[0][0]
    query(b, "DROP DATABASE req_mix")
    r.finish()


# ---- EXP-O6-LONG-UNDO: a read view held as long as a long count would hold it ----
def long_undo_body(script_id, rate_name, wl_rates):
    """A snapshot connection stands in for a count of that duration: a single count on this private table cannot last minutes.
    That substitution is why results say 'read view age', not 'count duration'."""
    r = Results(script_id, ["EXP-O6-LONG-UNDO", "FIND-O6-LONG-UNDO-01"], rate=rate_name, workload=wl_rates, ages_s=[60, 180, 300], substitution="one consistent snapshot holds the read view; no count statement runs for that long")
    b = builder()
    reset_schema(b, "req_undo")
    query(b, "USE req_undo")
    query(b, "CREATE TABLE t (id BIGINT PRIMARY KEY, k INT NOT NULL, v INT NOT NULL, KEY k_k (k))")
    for off in range(0, 3_000_000, 1_000_000):
        query(b, f"INSERT INTO t SELECT n+{off}, MOD(n,1000), 0 FROM bench.seq1m")
    query(b, "ANALYZE TABLE t")

    def hll():
        return query(b, "SELECT `count` FROM information_schema.INNODB_METRICS WHERE name='trx_rseg_history_len'")[0][0]

    def undo_files():
        return [list(x) for x in query(b, "SELECT NAME, FILE_SIZE, ALLOCATED_SIZE FROM information_schema.INNODB_TABLESPACES WHERE NAME LIKE 'innodb\\_undo%'")]

    wl = Workload("req_undo.t", max_id=3_000_000, head=3_000_000, write_sql="UPDATE req_undo.t SET v=v+1 WHERE id=%s", read_sql="SELECT * FROM req_undo.t WHERE id=%s", **wl_rates)
    sampler = Sampler("t", data_locks=False)
    wl.start()
    pin(METHOD_CPU)
    sampler.start()
    time.sleep(10)
    stamps = []
    with r.section("ages") as d:
        d["runs"] = runs = []
        for age in (0, 60, 180, 300):
            if r.time_left() < age + 150:
                d["stopped_early"] = age
                break
            hold_for = age or 60
            start_hll, start_files = hll(), undo_files()
            t0 = clocklib.now()
            snap = None
            if age:
                snap = connect(db="req_undo", autocommit=False, read_timeout=600)
                query(snap, "START TRANSACTION WITH CONSISTENT SNAPSHOT")
                query(snap, "SELECT COUNT(*) FROM t WHERE id=1")
            time.sleep(hold_for)
            end_hll, end_files = hll(), undo_files()
            c = connect(db="req_undo", read_timeout=120)
            t = time.monotonic()
            query(c, "SELECT COUNT(*) FROM t FORCE INDEX (k_k)")
            count_new_view = round(time.monotonic() - t, 3)
            c.close()
            if snap:
                query(snap, "ROLLBACK")
                snap.close()
            t1 = clocklib.now()
            limit = start_hll + 300
            while clocklib.now() - t1 < 120 and hll() > limit:
                time.sleep(1)
            runs.append({"read_view_age_s": age, "held_s": hold_for, "hll_start": start_hll, "hll_end": end_hll, "hll_growth_per_s": round((end_hll - start_hll) / hold_for, 1), "undo_files_start": start_files, "undo_files_end": end_files,
                         "seconds_until_hll_near_start": round(clocklib.now() - t1, 1), "hll_after_wait": hll(), "count_with_new_view_at_end_s": count_new_view})
            stamps.append((len(runs) - 1, t0, t1, clocklib.now()))
            r.save()
            print(age, runs[-1]["hll_end"], runs[-1]["seconds_until_hll_near_start"], flush=True)
    wl.finish()
    sampler.finish()
    with r.section("windows") as d:
        for i, t0, t1, t2 in stamps:
            runs[i]["latency_hold"] = wl.window(t0, t1)
            runs[i]["latency_after"] = wl.window(t1, t2)
            runs[i]["latency_last_30s_of_hold"] = wl.window(t1 - 30, t1)
    query(b, "DROP DATABASE req_undo")
    r.finish()
