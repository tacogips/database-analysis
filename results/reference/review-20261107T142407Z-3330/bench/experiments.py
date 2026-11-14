#!/usr/bin/env python3
"""Impact of whole-table counts. Each runs: baseline -> method (time-boxed, then killed) -> recovery,
under a constant open-loop service workload, and records latency, locks, undo growth and method speed.

Usage: experiments.py list | run <name>... [--window 60] | all
Results go to results/impact_<name>.json.
"""
import argparse, sys, threading, time
import pymysql
from countlib import choose_index, walk_count
from lablib import METHOD_CPU, PRIMARY, REPLICA, Sampler, Workload, connect, pin, query, save

READ_COMMITTED = ["SET SESSION transaction_isolation='READ-COMMITTED'"]
DENSE = dict(table="t_dense", max_id=100_000_000, write_col="status",
             write_sql="UPDATE t_dense SET status=MOD(status+1,5) WHERE id=%s")
HOT = dict(table="t_hot", max_id=5_000_000)
REPL = dict(table="t_repl", max_id=20_000_000, write_sql="UPDATE t_repl SET v=v+1 WHERE id=%s")


class Ctx:
    """Passed to each method: a stop flag, a kill helper and progress counters."""

    def __init__(self):
        self.stop = threading.Event()
        self.rows = 0
        self.chunks = 0
        self.chunk_ms = []
        self.notes = {}
        self.conns = []

    def conn(self, **kw):
        c = connect(**kw)
        self.conns.append(c)
        return c

    def kill_all(self):
        killer = connect(read_timeout=20)
        for c in self.conns:
            try:
                query(killer, f"KILL {c.thread_id()}")
            except Exception:
                pass
        killer.close()


EXPERIMENTS = {}


def exp(name, wl=DENSE, doc="", replica=False, window=None, data_locks=False, cleanup=None, prepare=None, wl_host=PRIMARY):
    def deco(fn):
        EXPERIMENTS[name] = dict(fn=fn, wl=wl, doc=doc, replica=replica, window=window, data_locks=data_locks,
                                 cleanup=cleanup, prepare=prepare, wl_host=wl_host)
        return fn
    return deco


SINGLE = "SET SESSION innodb_parallel_read_threads=1"


def count_once(ctx, sql, setup=(), host=PRIMARY):
    c = ctx.conn(host=host, read_timeout=3000)
    for s in setup:
        query(c, s)
    t = time.time()
    ctx.notes["result"] = query(c, sql)[0][0]
    ctx.notes["seconds"] = round(time.time() - t, 2)


def walk(ctx, table, duty=None, width=100_000, host=PRIMARY):
    c = ctx.conn(host=host, read_timeout=600)
    query(c, SINGLE)
    best, idx, _pages = choose_index(c, "bench", table)
    ctx.notes["index"] = best["name"]

    def on_chunk(n, ms):
        ctx.chunk_ms.append(ms)
        ctx.chunks += 1
        ctx.rows += n

    r = walk_count(c, "bench", table, best, idx, width=width, duty=duty, stop=ctx.stop, on_chunk=on_chunk)
    ctx.notes.update(result=r["rows"], complete=r["complete"], seconds=r["seconds"], max_chunk_ms=r["max_chunk_ms"])


# ---- Noise floor, repeated through the run ----
@exp("baseline", doc="Nothing runs: noise floor at the start")
def _(ctx):
    ctx.stop.wait()


# ---- Ways to count the 100M table ----
@exp("count_estimate", doc="TABLE_ROWS and MAX-MIN on the PK")
def _(ctx):
    c = ctx.conn()
    for label, sql in (("table_rows", "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='t_dense'"),
                       ("stats_n_rows", "SELECT n_rows FROM mysql.innodb_table_stats WHERE database_name='bench' AND table_name='t_dense'"),
                       ("pk_span", "SELECT MAX(id)-MIN(id)+1 FROM t_dense")):
        t = time.time()
        ctx.notes[label] = query(c, sql)[0][0]
        ctx.notes[label + "_ms"] = round((time.time() - t) * 1000, 2)


@exp("count_default", window=900, doc="SELECT COUNT(*) with default settings")
def _(ctx):
    count_once(ctx, "SELECT COUNT(*) FROM t_dense")


@exp("count_single_thread", window=900, doc="COUNT(*) with innodb_parallel_read_threads=1")
def _(ctx):
    count_once(ctx, "SELECT COUNT(*) FROM t_dense", [SINGLE])


@exp("count_force_primary", window=900, doc="COUNT(*) FORCE INDEX (PRIMARY)")
def _(ctx):
    count_once(ctx, "SELECT COUNT(*) FROM t_dense FORCE INDEX (PRIMARY)")


@exp("count_force_secondary", window=900, doc="COUNT(*) FORCE INDEX on the small secondary index")
def _(ctx):
    count_once(ctx, "SELECT COUNT(*) FROM t_dense FORCE INDEX (k_created)")


@exp("count_split_100k_nosleep", window=300, doc="Split count, 100k entries a step, no sleep")
def _(ctx):
    walk(ctx, "t_dense", width=100_000)


@exp("count_split_100k_duty50", window=300, doc="Split count, 100k entries a step, sleep as long as each step")
def _(ctx):
    walk(ctx, "t_dense", duty=.5, width=100_000)


@exp("count_split_1m_duty50", window=900, doc="Split count, 1M entries a step, sleep as long as each step: runs to the end")
def _(ctx):
    walk(ctx, "t_dense", duty=.5, width=1_000_000)


@exp("count_split_10k_duty50", window=120, doc="Split count, 10k entries a step, sleep as long as each step")
def _(ctx):
    walk(ctx, "t_dense", duty=.5, width=10_000)


@exp("baseline_mid", doc="Nothing runs: noise floor mid-run")
def _(ctx):
    ctx.stop.wait()


# ---- Stopping a count ----
@exp("count_timeout_hint", doc="MAX_EXECUTION_TIME(5000) on COUNT(*): when does it really stop?")
def _(ctx):
    c = ctx.conn(read_timeout=900)
    t = time.time()
    try:
        ctx.notes["result"] = query(c, "SELECT /*+ MAX_EXECUTION_TIME(5000) */ COUNT(*) FROM t_dense")[0][0]
    except pymysql.MySQLError as e:
        ctx.notes["error"] = f"{e.args[0]}: {e.args[1]}"
    ctx.notes["seconds_until_return"] = round(time.time() - t, 2)


@exp("count_kill_latency", doc="KILL QUERY on a running COUNT(*): when does it really stop?")
def _(ctx):
    c = ctx.conn(read_timeout=900)
    killer = connect(read_timeout=30)
    th = threading.Timer(5, lambda: query(killer, f"KILL QUERY {c.thread_id()}"))
    th.start()
    t = time.time()
    try:
        ctx.notes["result"] = query(c, "SELECT COUNT(*) FROM t_dense")[0][0]
    except pymysql.MySQLError as e:
        ctx.notes["error"] = f"{e.args[0]}: {e.args[1]}"
    ctx.notes["seconds_until_return"] = round(time.time() - t, 2)
    ctx.notes["kill_sent_at_s"] = 5


# ---- Locks under writes to the same rows (5M table) ----
def repeat(ctx, statements, setup=(), autocommit=True):
    c = ctx.conn(read_timeout=600, autocommit=autocommit)
    for s in setup:
        query(c, s)
    runs, secs = 0, []
    while not ctx.stop.is_set():
        t = time.time()
        for st in statements:
            query(c, st)
        if not autocommit:
            query(c, "COMMIT")
        secs.append(time.time() - t)
        runs += 1
    ctx.notes["runs"] = runs
    ctx.notes["avg_seconds"] = round(sum(secs) / len(secs), 2) if secs else None


RR = ["SET SESSION transaction_isolation='REPEATABLE-READ'"]
RC = ["SET SESSION transaction_isolation='READ-COMMITTED'"]
LOCK_TESTS = {
    "lock_plain_count": ("Plain COUNT(*) in autocommit", ["SELECT COUNT(*) FROM t_hot"], RR, True),
}


def add_lock_tests():
    for name, (doc, statements, setup, autocommit) in LOCK_TESTS.items():
        def fn(ctx, st=statements, su=setup, ac=autocommit):
            repeat(ctx, st, su, ac)
        exp(name, wl=HOT, data_locks=True, doc=doc)(fn)


exp("lock_baseline", wl=HOT, data_locks=True, doc="Nothing runs on t_hot, with lock sampling")(lambda ctx: ctx.stop.wait())
add_lock_tests()


# ---- Metadata lock chain: a long count, then a DDL, then everything queues ----








# ---- Storage engines ----
MYISAM = dict(table="t_myisam", max_id=3_000_000)
MEMORY = dict(table="t_memory", max_id=500_000)
ARCHIVE = dict(table="t_archive", max_id=1_000_000, write_sql="INSERT INTO t_archive (v) VALUES (%s)")


def repeat_count(ctx, table):
    repeat(ctx, [f"SELECT COUNT(*) FROM {table}"])








# ---- Undo history: open snapshots and churn make a count slower ----
@exp("count_under_history", wl=HOT, window=120, doc="COUNT(*) quiet, then with an old snapshot held and heavy churn, at RR and RU")
def _(ctx):
    c = ctx.conn(read_timeout=600)
    query(c, SINGLE)

    def timed(label, setup=()):
        for s in setup:
            query(c, s)
        t = time.time()
        query(c, "SELECT COUNT(*) FROM t_hot")
        ctx.notes[label] = round(time.time() - t, 2)

    timed("quiet_rr")
    snap = ctx.conn(read_timeout=600, autocommit=False)
    query(snap, "START TRANSACTION WITH CONSISTENT SNAPSHOT")
    w = ctx.conn(read_timeout=600)
    t0 = time.time()
    while time.time() - t0 < 20 and not ctx.stop.is_set():
        query(w, "UPDATE t_hot SET v=v+1 WHERE id BETWEEN 1 AND 20000")
    timed("history_rr_old_snapshot_held_by_other")
    timed("history_read_uncommitted", ["SET SESSION transaction_isolation='READ-UNCOMMITTED'"])
    query(c, "SET SESSION transaction_isolation='REPEATABLE-READ'")
    query(snap, "COMMIT")
    time.sleep(15)
    timed("after_snapshot_released")
    ctx.notes["hll_after_release"] = query(c, "SELECT `count` FROM information_schema.INNODB_METRICS WHERE name='trx_rseg_history_len'")[0][0]


# ---- Replica offload ----
@exp("replica_control_no_count", wl=REPL, replica=True, doc="Primary traffic with an idle replica")
def _(ctx):
    ctx.stop.wait()


@exp("count_on_primary_t_repl", wl=REPL, replica=True, window=900, doc="COUNT(*) on the primary, 20M rows")
def _(ctx):
    count_once(ctx, "SELECT COUNT(*) FROM t_repl")


@exp("count_on_replica_t_repl", wl=REPL, replica=True, window=900, doc="COUNT(*) on the replica, same table")
def _(ctx):
    count_once(ctx, "SELECT COUNT(*) FROM t_repl", host=REPLICA)




# ---- Foreign keys ----


# ---- A maintained counter instead of counting ----
def make_counted(trigger):
    def fn(c):
        query(c, "DROP TABLE IF EXISTS d_counted")
        query(c, "CREATE TABLE d_counted (id BIGINT AUTO_INCREMENT PRIMARY KEY, v INT NOT NULL)")
        query(c, "REPLACE INTO count_summary VALUES ('d_counted',0)")
        if trigger:
            query(c, "CREATE TRIGGER d_counted_ai AFTER INSERT ON d_counted FOR EACH ROW UPDATE count_summary SET c=c+1 WHERE t='d_counted'")
    return fn


def drop_counted(c):
    query(c, "DROP TABLE IF EXISTS d_counted")
    query(c, "DELETE FROM count_summary WHERE t='d_counted'")


COUNTED = dict(table="d_counted", max_id=1000, head=1000, write_sql="INSERT INTO d_counted (v) VALUES (%s)",
               read_sql="SELECT * FROM d_counted WHERE id=%s")


@exp("counter_no_trigger", wl=COUNTED, prepare=make_counted(False), cleanup=drop_counted, doc="Inserts with no counter (control)")
def _(ctx):
    ctx.stop.wait()


@exp("counter_trigger_one_row", wl=COUNTED, prepare=make_counted(True), cleanup=drop_counted, doc="Inserts, each updating one counter row by trigger")
def _(ctx):
    ctx.stop.wait()


@exp("baseline_end", doc="Nothing runs: noise floor at the end")
def _(ctx):
    ctx.stop.wait()


# ---- Runner ----
def run(name, window, base=10, recover=10):
    e = EXPERIMENTS[name]
    window = e["window"] or window
    admin = connect(read_timeout=120)
    if e["prepare"]:
        e["prepare"](admin)
    table = e["wl"]["table"]
    wl = Workload(host=e["wl_host"], **e["wl"])
    sampler = Sampler(table, replica_host=REPLICA if e["replica"] else None, data_locks=e["data_locks"])
    wl.start()
    pin(METHOD_CPU)  # after the fork: the extraction and its client tools stay off the workload's CPU
    sampler.start()
    time.sleep(base)
    ctx = Ctx()
    t_base_end = time.time()
    err = []

    def body():
        try:
            e["fn"](ctx)
        except Exception as ex:
            if not ctx.stop.is_set():
                err.append(repr(ex)[:300])

    th = threading.Thread(target=body, daemon=True)
    th.start()
    th.join(window)
    finished = not th.is_alive()
    t_method_end = time.time()
    ctx.stop.set()
    th.join(1)
    if th.is_alive():
        ctx.kill_all()
        th.join(10)
    worker_terminated = not th.is_alive()
    if not worker_terminated:
        err.append("method worker still alive after cancellation; cleanup unconfirmed")
    time.sleep(recover)
    t_end = time.time()
    wl.finish()
    sampler.finish()
    if e["cleanup"]:
        try:
            e["cleanup"](connect(read_timeout=300))
        except Exception as ex:
            err.append("cleanup: " + repr(ex)[:200])
    t0 = t_base_end - base
    dur = t_method_end - t_base_end
    result = {
        "name": name, "doc": e["doc"], "target": e["wl"]["table"], "window_s": window, "method_seconds": round(dur, 1),
        "finished_within_window": finished, "method_rows": ctx.rows, "method_rows_per_s": round(ctx.rows / dur) if dur else 0,
        "chunks": ctx.chunks,
        "chunk_ms": {"p50": round(sorted(ctx.chunk_ms)[len(ctx.chunk_ms) // 2], 1), "p99": round(sorted(ctx.chunk_ms)[int(len(ctx.chunk_ms) * .99)], 1),
                     "max": round(max(ctx.chunk_ms), 1)} if ctx.chunk_ms else None,
        "notes": ctx.notes, "errors": err, "worker_terminated": worker_terminated,
        "cancellation": "not_needed" if finished else "requested_and_joined" if worker_terminated else "unconfirmed",
        "latency": {"baseline": wl.window(t0, t_base_end), "during": wl.window(t_base_end, t_method_end),
                    "recovery": wl.window(t_method_end, t_end)},
        "server": {"baseline": sampler.window(t0, t_base_end), "during": sampler.window(t_base_end, t_method_end),
                   "recovery": sampler.window(t_method_end, t_end)},
    }
    save(f"impact_{name}", result)
    d, b = result["latency"]["during"], result["latency"]["baseline"]
    print(f"{name:42s} rows/s={result['method_rows_per_s']:>8} write p99 {b['write']['p99_ms']}->{d['write']['p99_ms']} ms "
          f"max={d['write']['max_ms']} err={d['write']['errors']} read p99 {b['read']['p99_ms']}->{d['read']['p99_ms']} ms", flush=True)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "run", "all"])
    ap.add_argument("names", nargs="*")
    ap.add_argument("--window", type=int, default=40)
    ap.add_argument("--base", type=int, default=10)
    ap.add_argument("--recover", type=int, default=10)
    ap.add_argument("--match", help="run every experiment whose name starts with this prefix")
    a = ap.parse_args()
    if a.cmd == "list":
        for k, v in EXPERIMENTS.items():
            print(f"{k:42s} {v['doc']}")
        return
    names = a.names if a.cmd == "run" else list(EXPERIMENTS)
    if a.match:
        names = [n for n in EXPERIMENTS if n.startswith(a.match)]
    failed = []
    for n in names:
        result = run(n, a.window, a.base, a.recover)
        if result.get("errors") or result.get("worker_terminated") is False:
            failed.append(n)
        if result.get("worker_terminated") is False:
            break  # do not overlap another experiment with a worker still alive
    if failed:
        print("semantic experiment failures: " + ", ".join(failed), file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
