#!/usr/bin/env python3
"""Answers EXP-O6-CPU-CONTROL (FIND-O6-INSTRUMENT-01), low load. The R9 windows, with 20 s of SELECT BENCHMARK() on the lab primary in the middle and no table access.
Compare with 36-EXP-R9-control-low, which has nothing in the middle, and with the count arms. Busy CPU per VM CPU is in vm_cpu_busy_pct.
Similar falls in latency would show a CPU confound; they would not by themselves prove the VM wake-up mechanism."""
from reqlib import guard
from reqruns import r9_body

ID = "38-EXP-O6-CPU-CONTROL-low"
guard(ID, 19)
r9_body(ID, ["EXP-O6-CPU-CONTROL", "FIND-O6-INSTRUMENT-01"], [("low", "cpu_only")] * 3)
