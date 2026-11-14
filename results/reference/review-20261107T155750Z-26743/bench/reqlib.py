"""Helpers for bench/requested/*.py: partial saving, a shared time budget, and runs with aligned windows.

Lab only. Every connection goes through lablib.connect to the compose hosts or to a matrix server passed by lab.rb.
"""
import clocklib
import json, os, signal, sys, threading, time, traceback
from contextlib import contextmanager
import pymysql
from lablib import METHOD_CPU, PRIMARY, Sampler, Workload, connect, pin, query, save

MARKER = "/work/results/requested/.budget_start"
BUDGET_MINUTES = int(os.environ.get("LAB_REQUESTED_BUDGET_MINUTES", "0"))  # 0: no aggregate cap for mise run all
SCRIPT_SECONDS = 1140  # the runner kills a script at 1200 seconds


def guard(script_id, est_minutes):
    """Skip a script that could overrun the runner's limit. Exit 3 is recorded as a failed run, so it is never taken for evidence."""
    if BUDGET_MINUTES <= 0 or not os.path.exists(MARKER):
        return
    with open(MARKER) as source:
        marker = json.load(source)  # Legacy wall-only markers require explicit regeneration.
    if marker.get('clock', {}).get('system') != clocklib.system_identity() or marker['clock'].get('format') != 'lab-monotonic-v2':
        raise ValueError('unsupported/foreign budget marker clock')
    elapsed = (clocklib.now() - marker['begin']) / 60
    if elapsed < 0:
        raise ValueError('inverted budget marker interval')
    if elapsed > 360:  # a marker from an earlier round
        return
    if elapsed + est_minutes > BUDGET_MINUTES:
        print(f"SKIPPED {script_id}: {elapsed:.0f} min used, needs {est_minutes}, budget {BUDGET_MINUTES}", flush=True)
        save(f"requested_{script_id}", {"id": script_id, "skipped": True, "elapsed_minutes": round(elapsed), "needs_minutes": est_minutes})
        sys.exit(3)


class Results:
    """Saves after every section, so a script killed at its limit keeps what it measured."""

    def __init__(self, script_id, answers, **meta):
        self.id = script_id
        self.errors = []
        self.t0 = time.monotonic()
        self.data = {"id": script_id, "answers": answers, "clock":clocklib.metadata(), "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "sections": {}, **meta}
        self.save()

    def time_left(self):
        return SCRIPT_SECONDS - (time.monotonic() - self.t0)

    def save(self):
        self.data["elapsed_seconds"] = round(time.monotonic() - self.t0)
        self.data["section_errors"] = self.errors
        save(f"requested_{self.id}", self.data)

    @contextmanager
    def section(self, name):
        d = self.data["sections"].setdefault(name, {})
        t = time.monotonic()
        try:
            yield d
        except Exception:
            d["_error"] = traceback.format_exc()[-1800:]
            self.errors.append(name)
            print(f"SECTION FAILED {name}\n{d['_error']}", flush=True)
        finally:
            d["_seconds"] = round(time.monotonic() - t, 2)
            print(f"section {name}: {d['_seconds']} s", flush=True)
            self.save()

    def finish(self):
        self.save()
        print(json.dumps({"id": self.id, "section_errors": self.errors, "elapsed_seconds": self.data["elapsed_seconds"]}), flush=True)
        sys.exit(1 if self.errors else 0)


def sql_try(c, sql, args=None):
    """Runs one statement and reports it instead of raising."""
    t = time.monotonic()
    try:
        rows = query(c, sql, args)
        return {"ok": True, "ms": round((time.monotonic() - t) * 1000, 2), "rows": [list(r) for r in rows[:5]]}
    except (pymysql.MySQLError, OSError) as e:
        return {"ok": False, "ms": round((time.monotonic() - t) * 1000, 2), "errno": e.args[0] if e.args else None, "msg": str(e)[:200]}


def builder(host=PRIMARY, db=None):
    """A connection for building private tables. Nothing it writes reaches the binlog or the replica."""
    c = connect(host, read_timeout=1100, db=db)
    query(c, "SET SESSION sql_log_bin=0")
    return c


def reset_schema(c, schema):
    query(c, f"DROP DATABASE IF EXISTS `{schema}`")
    query(c, f"CREATE DATABASE `{schema}`")


def global_status(c, *names):
    marks = ",".join(["%s"] * len(names))
    return {k: float(v) for k, v in query(c, f"SELECT VARIABLE_NAME,VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME IN ({marks})", names)}


def session_status(c, pattern):
    return {k: int(v) for k, v in query(c, "SHOW SESSION STATUS LIKE %s", (pattern,)) if str(v).lstrip("-").isdigit()}


class Watcher(threading.Thread):
    """1 Hz log of who is reading: rows_read rate and every statement active for more than half a second."""

    def __init__(self, host=PRIMARY):
        super().__init__(daemon=True)
        self.c = connect(host, read_timeout=20)
        self.halt = threading.Event()
        self.rows = []

    def run(self):
        last = None
        while not self.halt.is_set():
            t = clocklib.now()
            try:
                now = global_status(self.c, "Innodb_rows_read", "Handler_read_rnd_next", "Handler_read_next", "Innodb_rows_updated")
                active = query(self.c, """SELECT t.PROCESSLIST_ID,t.PROCESSLIST_STATE,t.PROCESSLIST_TIME,LEFT(t.PROCESSLIST_INFO,100),e.ROWS_EXAMINED
                                 FROM performance_schema.threads t LEFT JOIN performance_schema.events_statements_current e ON e.THREAD_ID=t.THREAD_ID
                                 WHERE t.PROCESSLIST_COMMAND<>'Sleep' AND t.TYPE='FOREGROUND' AND t.PROCESSLIST_ID<>CONNECTION_ID()
                                   AND t.PROCESSLIST_TIME>=1""")
                bg = query(self.c, "SELECT NAME,PROCESSLIST_STATE FROM performance_schema.threads WHERE TYPE='BACKGROUND' AND PROCESSLIST_STATE IS NOT NULL LIMIT 20")
                row = {"t": t, "clock_domain":clocklib.metadata()["domain_id"], "rows_read_per_s": None if last is None else round(now["Innodb_rows_read"] - last["Innodb_rows_read"]),
                       "active": [list(a) for a in active], "bg": [list(b) for b in bg]}
                last = now
                self.rows.append(row)
            except Exception as e:
                self.rows.append({"t": t, "clock_domain":clocklib.metadata()["domain_id"], "error": repr(e)[:160]})
            self.halt.wait(max(0, 1 - (clocklib.now() - t)))

    def finish(self):
        self.halt.set()
        self.join(5)
        return self.rows


def wait_quiet(host=PRIMARY, rows_per_s=500, seconds=5, timeout=180):
    """The gate: no foreground statement running and Innodb_rows_read below the limit for `seconds`, or report that it never got quiet."""
    c = connect(host, read_timeout=20)
    seen, t0 = [], time.monotonic()
    prev = global_status(c, "Innodb_rows_read")["Innodb_rows_read"]
    calm = 0
    while time.monotonic() - t0 < timeout:
        time.sleep(1)
        now = global_status(c, "Innodb_rows_read")["Innodb_rows_read"]
        rate = now - prev
        prev = now
        busy = query(c, """SELECT COUNT(*) FROM performance_schema.threads WHERE TYPE='FOREGROUND' AND PROCESSLIST_COMMAND NOT IN ('Sleep','Daemon','Binlog Dump GTID','Binlog Dump') AND PROCESSLIST_ID<>CONNECTION_ID()""")[0][0]
        seen.append([round(rate), busy])
        calm = calm + 1 if rate < rows_per_s and busy == 0 else 0
        if calm >= seconds:
            c.close()
            return {"quiet": True, "seconds_waited": round(time.monotonic() - t0, 1), "last": seen[-seconds:]}
    c.close()
    return {"quiet": False, "seconds_waited": round(time.monotonic() - t0, 1), "last": seen[-10:]}


class ArmTimeout(Exception):
    pass


def bounded(seconds, fn, *args, **kw):
    """Runs one arm with an alarm, so one looping arm cannot use up a script. Main thread only. Reports instead of raising."""
    def on_alarm(_sig, _frame):
        raise ArmTimeout(f"over {seconds} s")
    previous = signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(int(seconds))
    try:
        return fn(*args, **kw)
    except ArmTimeout as e:
        return {"arm_timeout": str(e)}
    except Exception as e:
        return {"arm_error": repr(e)[:400]}
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def cpu_ticks():
    """The VM's per-CPU busy and total ticks. Containers share the VM's kernel, so this is the whole VM, not one container."""
    out = {}
    try:
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("cpu") and line[3].isdigit():
                    p = line.split()
                    vals = [int(x) for x in p[1:9]]
                    out[p[0]] = (sum(vals) - vals[3] - vals[4], sum(vals))
    except OSError:
        pass
    return out


def cpu_busy(a, b):
    return {k: round(100 * (b[k][0] - a[k][0]) / max(b[k][1] - a[k][1], 1), 1) for k in a if k in b}


def equal_windows(workload, t1, t2, t3, lengths=(12, 20, 30, 60)):
    """The same length before, during and after, because a tail percentile depends on how many samples it has.
    `during` is reported only for lengths the method actually lasted."""
    out = {}
    for L in lengths:
        row = {"before": workload.window(t1 - L, t1), "after": workload.window(t2, min(t2 + L, t3))}
        if t2 - t1 >= L:
            row["during"] = workload.window(t1, t1 + L)
        out[str(L)] = row
    return out


def _series(workload, sampler, t0, t3, step=10):
    out = []
    t = t0
    while t < t3:
        w = workload.window(t, t + step)
        s = sampler.window(t, t + step + 1)
        out.append({"from_s": round(t - t0), "read_p50": w["read"]["p50_ms"], "read_p99": w["read"]["p99_ms"], "write_p50": w["write"]["p50_ms"],
                    "write_p99": w["write"]["p99_ms"], "disk_reads_per_s": s.get("bp_disk_reads_per_s"), "miss_pct": s.get("bp_miss_pct"), "hll": s.get("hll_peak")})
        t += step
    return out


def measured_run(label, wl, method, base, recover, during=None, table=None, host=PRIMARY, sampler_host=None, replica_host=None, data_locks=False, watcher=False):
    """Baseline, method, recovery under one open-loop workload. `during` pads a method that ends early, so windows can be aligned."""
    workload = Workload(host=host, **wl)
    sampler = Sampler(table or wl["table"], host=sampler_host or host, replica_host=replica_host, data_locks=data_locks)
    watch = Watcher(host) if watcher else None
    workload.start()
    pin(METHOD_CPU)
    sampler.start()
    if watch:
        watch.start()
    clock_start = clocklib.metadata()
    t0 = clocklib.now()
    cpu0 = cpu_ticks()
    time.sleep(base)
    t1 = clocklib.now()
    cpu1 = cpu_ticks()
    notes, err = {}, None
    method_begin = clocklib.now()
    try:
        method(notes)
    except Exception:
        err = traceback.format_exc()[-1200:]
    method_end = clocklib.now()
    if during:
        left = t1 + during - clocklib.now()
        if left > 0:
            time.sleep(left)
    t2 = clocklib.now()
    cpu2 = cpu_ticks()
    time.sleep(recover)
    t3 = clocklib.now()
    cpu3 = cpu_ticks()
    workload.finish()
    sampler.finish()
    res = {"label": label, "method_seconds": round(t2 - t1, 2), "notes": notes, "error": err,
           "equal_windows": equal_windows(workload, t1, t2, t3),
           "vm_cpu_busy_pct": {"baseline": cpu_busy(cpu0, cpu1), "during": cpu_busy(cpu1, cpu2), "recovery": cpu_busy(cpu2, cpu3)},
           "latency": {"baseline": workload.window(t0, t1), "during": workload.window(t1, t2), "recovery": workload.window(t2, t3)},
           "server": {"baseline": sampler.window(t0, t1), "during": sampler.window(t1, t2), "recovery": sampler.window(t2, t3)},
           "series_10s": _series(workload, sampler, t0, t3)}
    res.update(clock=clock_start, clock_end=clocklib.metadata(), raw_service_sample_format="arrival-tuple-v2", raw_service_samples=workload.samples,
               arrival_accounting=workload.accounting, server_samples=sampler.rows,
               windows={label:clocklib.interval(a,b) for label,a,b in (
                   ('baseline',t0,t1),('during',t1,t2),('recovery',t2,t3))},
               actual_method_window=clocklib.interval(method_begin,method_end),
               actual_method_service=workload.window(method_begin,method_end),
               actual_method_server=sampler.window(method_begin,method_end))
    if watch:
        res["watcher"] = watch.finish()
    return res


def compact_watch(rows, threshold=5000):
    """Only the seconds with unexplained reading."""
    return [r for r in rows if (r.get("rows_read_per_s") or 0) > threshold or r.get("active")][:200]
