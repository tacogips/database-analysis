#!/usr/bin/env python3
"""Answers EXP-R4 (FIND-PK-01), first part: the split through PRIMARY against the split through k_created on t_dense, under the same load.
Alternates the two indexes, three times without sleep and once at 50% duty. Also reruns FORCE PRIMARY as one statement for comparison."""
from experiments import DENSE
from reqlib import Results, guard, measured_run, wait_quiet
from reqruns import count_once, split_on_dense

ID = "26-EXP-R4-dense"
guard(ID, 19)
r = Results(ID, ["EXP-R4", "FIND-PK-01"], width=100_000, statement_limit_ms=5000, base_s=15, recover_s=15)
SEQ = [("PRIMARY", None), ("k_created", None), ("PRIMARY", None), ("k_created", None), ("PRIMARY", None), ("k_created", None), ("k_created", .5), ("PRIMARY", .5)]

with r.section("runs") as d:
    d["runs"] = []
    for index, duty in SEQ:
        if r.time_left() < 240:
            d["stopped_early"] = f"{index}/{duty}"
            break
        gate = wait_quiet(rows_per_s=500, seconds=3, timeout=60)
        res = measured_run(f"{index}/{duty}", DENSE, lambda n: split_on_dense(n, duty=duty, index=index), 15, 15)
        res.update(index=index, duty=duty, gate=gate)
        res.pop("series_10s", None)
        d["runs"].append(res)
        r.save()
        print(index, duty, res["method_seconds"], res["notes"].get("stmt_ms_max"), res["latency"]["during"]["write"], flush=True)
r.finish()
