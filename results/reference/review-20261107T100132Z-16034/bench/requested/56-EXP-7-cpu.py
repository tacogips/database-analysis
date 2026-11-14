#!/usr/bin/env python3
"""Answers EXP-7's CPU-saturation condition. Skipped by budget last round. The primary's two CPUs are kept busy by BENCHMARK() connections while the usual workload runs, then one COUNT(*) and the split run. Arm order alternates. All hogs are killed at the end of each arm."""
import threading, time
from experiments import DENSE
from lablib import connect, query
from reqlib import Results, guard, measured_run
from reqruns import count_once, split_on_dense

ID = "56-EXP-7-cpu"
guard(ID, 11)
r = Results(ID, ["EXP-7"], hogs=4, base_s=15, recover_s=15)


class Hogs:
    def __init__(self, n=4):
        self.conns = [connect(read_timeout=600) for _ in range(n)]
        self.threads = [threading.Thread(target=self._run, args=(c,), daemon=True) for c in self.conns]
        self.halt = threading.Event()

    def _run(self, c):
        while not self.halt.is_set():
            try:
                query(c, "SELECT BENCHMARK(100000000, SHA2('x',256))")
            except Exception:
                return

    def __enter__(self):
        for t in self.threads:
            t.start()
        time.sleep(1)
        return self

    def __exit__(self, *exc):
        self.halt.set()
        k = connect(read_timeout=10)
        for c in self.conns:
            try:
                query(k, f"KILL QUERY {c.thread_id()}")
            except Exception:
                pass
        for t in self.threads:
            t.join(10)
        k.close()


ARMS = {"control": lambda n: time.sleep(30), "count": lambda n: count_once(n, "SELECT COUNT(*) FROM t_dense"), "split_duty50": lambda n: split_on_dense(n, duty=.5)}
with r.section("no_hogs_reference") as d:
    res = measured_run("idle_cpu_control", DENSE, ARMS["control"], 10, 10)
    res.pop("series_10s", None)
    d["control"] = res

with r.section("saturated") as d:
    d["runs"] = []
    for name in ("control", "count", "split_duty50", "split_duty50", "count", "control"):
        if r.time_left() < 180:
            d["stopped_early"] = name
            break
        with Hogs():
            res = measured_run(name, DENSE, ARMS[name], 15, 15)
        res.update(arm=name)
        res.pop("series_10s", None)
        d["runs"].append(res)
        r.save()
        print(name, res["method_seconds"], res["latency"]["during"]["write"], flush=True)
r.finish()
