#!/usr/bin/env python3
"""Answers EXP-O6-CPU-CONTROL (FIND-O6-INSTRUMENT-01), high load. Compare with 37-EXP-R9-control-high and the count arms."""
from reqlib import guard
from reqruns import r9_body

ID = "39-EXP-O6-CPU-CONTROL-high"
guard(ID, 19)
r9_body(ID, ["EXP-O6-CPU-CONTROL", "FIND-O6-INSTRUMENT-01"], [("high", "cpu_only")] * 3)
