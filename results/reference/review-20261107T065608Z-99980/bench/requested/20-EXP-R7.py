#!/usr/bin/env python3
"""Answers EXP-R7 (FIND-LAB-CONTAM-01): where did the unexplained background reading in the first impact results come from?

Part 1 repeats the order of the original run (lock_factors, then baseline, default, single thread, force primary) with no gate
and a 1 Hz log of rows read and active statements. Part 2 repeats the methods behind a quiet gate. Results do not overwrite
the original impact files. lock_factors runs in its private schema; its result file is left alone.
"""
import time
import lablib
_real_save = lablib.save
lablib.save = lambda name, obj: f"(not saved: {name})"  # lock_factors must not overwrite results/lock_factors.json
import lock_factors
lablib.save = _real_save
from experiments import DENSE
from reqlib import Results, compact_watch, guard, measured_run, wait_quiet
from reqruns import count_once
from lablib import connect, query

ID = "20-EXP-R7"
guard(ID, 18)
r = Results(ID, ["EXP-R7", "FIND-LAB-CONTAM-01", "FIND-FAST-01"], windows={"base": 10, "recover": 10, "baseline_method": 40},
            expected_rows_read_per_s_from_workload=200)
METHODS = {"baseline": None,
           "count_default": lambda n: count_once(n, "SELECT COUNT(*) FROM t_dense"),
           "count_single_thread": lambda n: count_once(n, "SELECT COUNT(*) FROM t_dense", ["SET SESSION innodb_parallel_read_threads=1"]),
           "count_force_primary": lambda n: count_once(n, "SELECT COUNT(*) FROM t_dense FORCE INDEX (PRIMARY)")}


def one(label, name):
    fn = METHODS[name]
    res = measured_run(label, DENSE, (lambda n: None) if fn is None else fn, 10, 10, during=40 if fn is None else None, watcher=True)
    res["watcher_interesting"] = compact_watch(res.pop("watcher"))
    res.pop("series_10s", None)
    server = res["server"]
    print(label, res["method_seconds"], {k: (server[k] or {}).get("rows_read_per_s") for k in server}, flush=True)
    return res


with r.section("normal_order_no_gate") as d:
    t = time.monotonic()
    lock_factors.main()
    d["lock_factors_seconds"] = round(time.monotonic() - t)
    for name in METHODS:
        d[name] = one("no_gate/" + name, name)
        r.save()

with r.section("behind_quiet_gate") as d:
    for name in METHODS:
        if r.time_left() < 180:
            d["stopped_early_before"] = name
            break
        gate = wait_quiet(rows_per_s=500, seconds=5, timeout=120)
        res = one("gate/" + name, name)
        res["gate"] = gate
        d[name] = res
        r.save()

with r.section("plan") as d:
    c = connect(read_timeout=60)
    d["default_explain"] = [str(x) for x in query(c, "EXPLAIN SELECT COUNT(*) FROM t_dense")[0]]
    d["default_tree"] = query(c, "EXPLAIN FORMAT=TREE SELECT COUNT(*) FROM t_dense")[0][0]
    d["parallel_read_threads"] = query(c, "SELECT @@innodb_parallel_read_threads")[0][0]
r.finish()
