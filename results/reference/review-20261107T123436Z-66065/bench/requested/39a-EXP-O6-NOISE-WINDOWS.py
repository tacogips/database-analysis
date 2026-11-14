#!/usr/bin/env python3
"""Answers EXP-O6-NOISE-WINDOWS (FIND-O6-NOISE-FLOOR-01). Nothing but the workload runs. Why do the idle 'during' windows of the first results show write p99 of 100-260 ms when their before and after windows show 6 ms?

Part 1 repeats the first-group shape exactly: 10 s, 40 s, 10 s, three times. Part 2 runs each harness for 240 s and cuts it into windows of 12, 20, 60 and 120 s.
A 1 Hz log of redo, flushing and fsync counters is kept, and each write over 50 ms is listed with its time, to see whether stalls line up with them.
"""
import threading, time
from experiments import DENSE
from lablib import METHOD_CPU, Workload, connect, pin, query
from reqlib import Results, global_status, guard, measured_run
from reqruns import RATES, warm, working_set

ID = "39a-EXP-O6-NOISE-WINDOWS"
guard(ID, 19)
r = Results(ID, ["EXP-O6-NOISE-WINDOWS", "FIND-O6-NOISE-FLOOR-01"])
COUNTERS = ("Innodb_os_log_written", "Innodb_buffer_pool_pages_flushed", "Innodb_buffer_pool_pages_dirty", "Innodb_data_fsyncs", "Innodb_os_log_fsyncs", "Innodb_redo_log_checkpoint_lsn", "Innodb_redo_log_current_lsn", "Innodb_buffer_pool_wait_free")


def status_log(halt, rows):
    c = connect(read_timeout=20)
    while not halt.is_set():
        t = time.time()
        rows.append({"t": round(t, 1), **global_status(c, *COUNTERS)})
        halt.wait(max(0, 1 - (time.time() - t)))
    c.close()


def windows(wl, t0, t1, lengths=(12, 20, 60, 120)):
    out = {}
    for L in lengths:
        cuts = []
        t = t0
        while t + L <= t1:
            w = wl.window(t, t + L)["write"]
            cuts.append({"from_s": round(t - t0), "n": w["n"], "p50": w["p50_ms"], "p95": w["p95_ms"], "p99": w["p99_ms"], "max": w["max_ms"]})
            t += L
        p99 = sorted(x["p99"] for x in cuts if x["p99"] is not None)
        out[str(L)] = {"cuts": cuts, "p99_min": p99[0] if p99 else None, "p99_median": p99[len(p99) // 2] if p99 else None, "p99_max": p99[-1] if p99 else None}
    return out


def stall_context(wl, t0, t1, rows, threshold=50):
    """Writes over the threshold, by scheduled second, beside what the counters did in that second."""
    slow = sorted((s[0], s[2]) for s in wl.samples if s[1] == "write" and t0 <= s[0] < t1 and s[2] > threshold)
    by_second = {}
    for t, ms in slow:
        by_second.setdefault(int(t - t0), []).append(ms)
    index = {int(x["t"] - t0): i for i, x in enumerate(rows)}
    out = []
    for sec, values in sorted(by_second.items()):
        i = index.get(sec)
        delta = {k: rows[i][k] - rows[i - 1][k] for k in rows[i] if k != "t" and i and k in rows[i - 1]} if i else None
        out.append({"second": sec, "writes_over": len(values), "max_ms": round(max(values), 1), "counter_delta": delta})
    return {"threshold_ms": threshold, "slow_writes": len(slow), "seconds": out[:80], "seconds_total": len(out)}


def long_run(label, cfg, seconds=240):
    wl = Workload(**cfg)
    halt, rows = threading.Event(), []
    th = threading.Thread(target=status_log, args=(halt, rows), daemon=True)
    wl.start()
    pin(METHOD_CPU)
    th.start()
    time.sleep(5)
    t0 = time.time()
    time.sleep(seconds)
    t1 = time.time()
    wl.finish()
    halt.set()
    th.join(5)
    return {"label": label, "whole": wl.window(t0, t1), "windows": windows(wl, t0, t1), "stalls": stall_context(wl, t0, t1, rows)}


with r.section("first_group_shape") as d:
    d["runs"] = []
    for i in range(3):
        res = measured_run(f"idle_{i}", DENSE, lambda n: None, 10, 10, during=40)
        res.pop("series_10s", None)
        d["runs"].append(res)
        r.save()
        print(i, res["latency"]["baseline"]["write"]["p99_ms"], res["latency"]["during"]["write"]["p99_ms"], res["latency"]["recovery"]["write"]["p99_ms"], flush=True)

with r.section("first_group_harness_long") as d:
    d.update(long_run("first_group", DENSE))
    print(d["windows"]["60"]["p99_max"], flush=True)

admin = connect(read_timeout=600)
head, facts = working_set(admin)
for rate in ("low", "high"):
    with r.section(f"r9_harness_{rate}") as d:
        if r.time_left() < 290:
            d["skipped"] = "too close to the script limit"
        else:
            d["warm"] = warm(admin, head)
            d.update(long_run(rate, dict(DENSE, max_id=head, head=head, **RATES[rate])))
r.finish()
