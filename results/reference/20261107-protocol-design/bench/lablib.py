"""Shared helpers: connections, an open-loop service workload, and a 1 Hz server sampler."""
import json, math, multiprocessing as mp, os, random, threading, time
import pymysql
import pymysql.cursors

PRIMARY = os.environ.get("LAB_HOST", "primary")
REPLICA = os.environ.get("LAB_REPLICA", "replica")
# compose.yaml gives bench CPUs 2 and 3, and the primary CPUs 0 and 1.
WORKLOAD_CPU, METHOD_CPU = 2, 3


def validate_lab_host(host):
    """Fail before opening a socket outside this compose lab's named servers."""
    allowed = {"primary", "replica", "lab-primary", "lab-replica"} | {
        "lab-matrix-" + version.replace(".", "-")
        for version in ("8.4.0", "8.4.13", "9.7.0", "9.7.4", "26.10.0")}
    if host not in allowed:
        raise ValueError("refusing non-lab database host")
    return host


def pin(cpu):
    """Best effort: outside the lab's cpuset this is a no-op."""
    try:
        os.sched_setaffinity(0, {cpu})
    except OSError:
        pass


def connect(host=PRIMARY, read_timeout=600, cursor=None, autocommit=True, db="bench"):
    validate_lab_host(host)
    return pymysql.connect(host=host, user="root", password="lab", database=db, autocommit=autocommit,
                           read_timeout=read_timeout, write_timeout=600, connect_timeout=10,
                           cursorclass=cursor or pymysql.cursors.Cursor)


def query(conn, sql, args=None):
    with conn.cursor() as c:
        c.execute(sql, args)
        return c.fetchall() if c.description else []


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(len(s) * p))], 2)


def summarize(lat):
    return {"n": len(lat), "p50_ms": pct(lat, .50), "p95_ms": pct(lat, .95), "p99_ms": pct(lat, .99),
            "max_ms": round(max(lat), 2) if lat else None}


class Workload:
    """Open-loop service simulator in its own process and CPU, so extraction cannot steal its GIL or core.

    A sample is filed under its SCHEDULED time and its latency counts from then: a stall shows up in the window
    in which it began, and requests that never finished are reported as stuck rather than disappearing.
    """

    def __init__(self, table, key="id", max_id=5_000_000, write_col="v", head=200_000,
                 readers=4, writers=4, rate=25, host=PRIMARY, write_sql=None, read_sql=None):
        self.cfg = dict(host=host, max_id=max_id, head=head, readers=readers, writers=writers, rate=rate,
                        write_sql=write_sql or f"UPDATE {table} SET {write_col}={write_col}+1 WHERE {key}=%s",
                        read_sql=read_sql or f"SELECT * FROM {table} WHERE {key}=%s")
        self.parent, child = mp.Pipe()
        self.stop_flag = mp.Event()
        self.arrival_cutoff = mp.Value("d", 0.0)
        self.proc = mp.Process(target=_workload_main, args=(self.cfg, self.stop_flag, child, self.arrival_cutoff), daemon=True)
        self.samples = []
        self.accounting = {}

    def start(self):
        self.proc.start()

    def finish(self):
        self.arrival_cutoff.value = time.monotonic()
        self.stop_flag.set()
        received = self.parent.poll(30)
        if received:
            payload = self.parent.recv()
            self.samples = payload["samples"]
            self.accounting = payload["accounting"]
        self.proc.join(5)
        if self.proc.is_alive():
            self.proc.terminate()
            self.proc.join(5)
        if not received or not self.accounting.get("consistent"):
            raise RuntimeError(f"workload arrival accounting failed: {self.accounting}")

    def window(self, t0, t1):
        rows = [s for s in self.samples if t0 <= s[0] < t1]
        out = {}
        for kind in ("read", "write"):
            part = [r for r in rows if r[1] == kind]
            out[kind] = summarize([r[2] for r in part])
            out[kind]["errors"] = sum(1 for r in part if r[3] == "error")
            queued = sum(1 for r in part if r[3] == "queued")
            inflight = sum(1 for r in part if r[3] == "stuck")
            out[kind].update(scheduled=len(part), started=len(part)-queued,
                             completed=sum(1 for r in part if r[3] == "ok"), failed=out[kind]["errors"],
                             inflight_at_end=inflight, queued_at_end=queued, stuck_at_end=inflight+queued,
                             slo_assessable=(inflight+queued == 0),
                             latency_status="censored_lower_bounds" if inflight+queued else "completed_responses")
            out[kind]["dispatch_delay"] = summarize([r[4] for r in part if len(r)>4 and r[4] is not None])
            out[kind]["client_execution"] = summarize([r[5] for r in part if len(r)>5 and r[5] is not None])
            assert len(part) == out[kind]["completed"]+out[kind]["failed"]+inflight+queued
        return out


def _workload_main(cfg, stop, pipe, arrival_cutoff=None):
    pin(WORKLOAD_CPU)
    random.seed(int(os.environ.get("LAB_SEED", "42")))
    samples, lock = [], threading.Lock()
    inflight = {}
    jobs = [("read", cfg["read_sql"])] * cfg["readers"] + [("write", cfg["write_sql"])] * cfg["writers"]
    interval = 1.0 / cfg["rate"]
    origin = time.monotonic()
    schedules = {i: (kind, origin + random.random()*interval, 0) for i,(kind,_) in enumerate(jobs)}
    first_arrivals = dict(schedules)

    def pick():
        return random.randint(1, cfg["head"]) if random.random() < .5 else random.randint(1, cfg["max_id"])

    def worker(idx, kind, sql):
        conn = None
        first_at, sequence = schedules[idx][1:]
        next_at = first_at
        while not stop.is_set():
            now = time.monotonic()
            cutoff = arrival_cutoff.value if arrival_cutoff is not None else 0
            if cutoff and now >= cutoff:
                break
            if now < next_at:
                time.sleep(min(next_at-now, .05))
                continue
            sched = next_at
            sched_wall = time.time() - (now-sched)
            with lock:
                if stop.is_set():
                    break
                sequence += 1
                next_at = first_at + sequence*interval
                schedules[idx] = (kind, first_at, sequence)
                inflight[idx] = (sched_wall, kind, sched, now)
            state = "ok"
            try:
                if conn is None:
                    conn = connect(cfg["host"], read_timeout=120)
                    query(conn, "SET SESSION innodb_lock_wait_timeout=50")
                query(conn, sql, (pick(),))
            except Exception:
                state = "error"
                try:
                    conn and conn.close()
                except Exception:
                    pass
                conn = None
            finished = time.monotonic()
            with lock:
                inflight.pop(idx, None)
                samples.append((sched_wall, kind, (finished-sched)*1000, state,
                                (now-sched)*1000, (finished-now)*1000))
        conn and conn.close()

    threads = []
    for i,(kind,sql) in enumerate(jobs):
        t = threading.Thread(target=worker,args=(i,kind,sql),daemon=True)
        t.start()
        threads.append(t)
    stop.wait()
    cutoff = arrival_cutoff.value if arrival_cutoff is not None else time.monotonic()
    cutoff_wall = time.time() - (time.monotonic()-cutoff)
    for t in threads:
        t.join(1)
    with lock:
        now = time.monotonic()
        for sched_wall,kind,sched,started in list(inflight.values()):
            samples.append((sched_wall,kind,(now-sched)*1000,"stuck",(started-sched)*1000,None))
        # Save every scheduled arrival, including requests never dispatched before the cutoff.
        for kind,first_at,sequence in schedules.values():
            next_at = first_at + sequence*interval
            while next_at < cutoff:
                samples.append((cutoff_wall+next_at-cutoff,kind,(cutoff-next_at)*1000,"queued",None,None))
                sequence += 1
                next_at = first_at + sequence*interval
        scheduled = 0
        for _,first_at,_ in first_arrivals.values():
            expected = max(0, math.ceil((cutoff-first_at)/interval))
            while expected and first_at+(expected-1)*interval >= cutoff:
                expected -= 1
            while first_at+expected*interval < cutoff:
                expected += 1
            scheduled += expected
        counts = {state: sum(r[3] == state for r in samples) for state in ("ok","error","stuck","queued")}
        accounting = dict(scheduled=scheduled, started=counts["ok"]+counts["error"]+counts["stuck"],
                          completed=counts["ok"], failed=counts["error"], inflight=counts["stuck"],
                          unstarted=counts["queued"], consistent=(scheduled == len(samples)),
                          arrival_window_seconds=cutoff-origin)
        pipe.send({"samples": samples, "accounting": accounting})


STATUS = ("Threads_running", "Innodb_buffer_pool_reads", "Innodb_buffer_pool_read_requests",
          "Innodb_row_lock_time", "Innodb_row_lock_waits", "Innodb_row_lock_current_waits",
          "Innodb_rows_read", "Table_locks_waited", "Innodb_data_read")


class Sampler:
    """1 Hz server-state sampler. Locks are aggregated per table; samples are cheap but not free."""

    def __init__(self, table, host=PRIMARY, replica_host=None, data_locks=True):
        self.table, self.host, self.replica_host, self.data_locks = table, host, replica_host, data_locks
        self.stop = threading.Event()
        self.rows = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        c = connect(self.host, read_timeout=20)
        rc = connect(self.replica_host, read_timeout=20) if self.replica_host else None
        while not self.stop.is_set():
            t0 = time.time()
            row = {"t": t0}
            try:
                names = ",".join(f"'{n}'" for n in STATUS)
                for k, v in query(c, f"SELECT VARIABLE_NAME,VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME IN ({names})"):
                    row[k] = float(v)
                row["hll"] = query(c, "SELECT `count` FROM information_schema.INNODB_METRICS WHERE name='trx_rseg_history_len'")[0][0]
                r = query(c, "SELECT COALESCE(MAX(TIMESTAMPDIFF(SECOND,trx_started,NOW())),0),COUNT(*) FROM information_schema.INNODB_TRX")[0]
                row["max_trx_age_s"], row["active_trx"] = int(r[0]), int(r[1])
                row["mdl_waiters"] = query(c, "SELECT COUNT(*) FROM performance_schema.metadata_locks WHERE LOCK_STATUS='PENDING'")[0][0]
                row["table_mdl_exclusive"] = query(c, "SELECT COUNT(*) FROM performance_schema.metadata_locks WHERE OBJECT_NAME=%s AND LOCK_TYPE IN ('SHARED_READ_ONLY','SHARED_NO_READ_WRITE','SHARED_NO_WRITE','EXCLUSIVE')", (self.table,))[0][0]
                row["global_read_lock"] = query(c, "SELECT COUNT(*) FROM performance_schema.metadata_locks WHERE OBJECT_TYPE='GLOBAL' AND LOCK_TYPE='SHARED' AND LOCK_STATUS='GRANTED'")[0][0]
                if self.data_locks:
                    # Bounded: counting all locks is itself expensive during a locking scan.
                    row["row_locks_seen"] = query(c, "SELECT COUNT(*) FROM (SELECT 1 FROM performance_schema.data_locks WHERE OBJECT_NAME=%s LIMIT 100000) x", (self.table,))[0][0]
                    row["table_is_locks"] = query(c, "SELECT COUNT(*) FROM performance_schema.data_locks WHERE OBJECT_NAME=%s AND LOCK_TYPE='TABLE' AND LOCK_MODE IN ('S','X')", (self.table,))[0][0]
                if rc:
                    st = query(rc, "SHOW REPLICA STATUS")
                    if st:
                        # Seconds_Behind_Source position varies by version, so resolve it by name.
                        with rc.cursor(pymysql.cursors.DictCursor) as dc:
                            dc.execute("SHOW REPLICA STATUS")
                            row["replica_lag_s"] = (dc.fetchone() or {}).get("Seconds_Behind_Source")
            except Exception as e:  # keep sampling through kills and stalls
                row["sampler_error"] = str(e)[:120]
                try:
                    c = connect(self.host, read_timeout=20)
                except Exception:
                    pass
            self.rows.append(row)
            self.stop.wait(max(0, 1 - (time.time() - t0)))

    def start(self):
        self.thread.start()

    def finish(self):
        self.stop.set()
        self.thread.join(timeout=25)

    def window(self, t0, t1):
        rows = [r for r in self.rows if t0 <= r["t"] < t1 and "hll" in r]
        capabilities = {"table_data_locks": "enabled" if self.data_locks else "not_measured",
                        "replica_lag": "enabled" if self.replica_host else "not_measured"}
        if not rows:
            return {"observation_capabilities": capabilities, "samples": 0,
                    "peak_row_locks_seen": None, "peak_table_level_locks": None}
        first, last = rows[0], rows[-1]
        span = max(last["t"] - first["t"], 1)
        delta = lambda k: last.get(k, 0) - first.get(k, 0)
        peak = lambda k: max((r[k] for r in rows if r.get(k) is not None), default=None)
        reqs = delta("Innodb_buffer_pool_read_requests")
        return {
            "observation_capabilities": capabilities, "samples": len(rows),
            "table_data_lock_samples": sum(r.get("row_locks_seen") is not None for r in rows),
            "seconds": round(span, 1),
            "rows_read_per_s": round(delta("Innodb_rows_read") / span),
            "bp_disk_reads_per_s": round(delta("Innodb_buffer_pool_reads") / span),
            "bp_miss_pct": round(100 * delta("Innodb_buffer_pool_reads") / reqs, 3) if reqs else None,
            "row_lock_time_ms": round(delta("Innodb_row_lock_time")),
            "row_lock_waits": round(delta("Innodb_row_lock_waits")),
            "table_locks_waited": round(delta("Table_locks_waited")),
            "peak_threads_running": peak("Threads_running"),
            "peak_row_lock_current_waits": peak("Innodb_row_lock_current_waits"),
            "hll_start": first["hll"], "hll_end": last["hll"], "hll_peak": peak("hll"),
            "peak_trx_age_s": peak("max_trx_age_s"),
            "peak_mdl_waiters": peak("mdl_waiters"),
            "peak_table_mdl_exclusive": peak("table_mdl_exclusive"),
            "peak_global_read_lock": peak("global_read_lock"),
            "peak_row_locks_seen": peak("row_locks_seen"),
            "peak_table_level_locks": peak("table_is_locks"),
            "peak_replica_lag_s": peak("replica_lag_s"),
        }


def save(name, obj):
    os.makedirs("/work/results", exist_ok=True)
    path = f"/work/results/{name}.json"
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
    return path
