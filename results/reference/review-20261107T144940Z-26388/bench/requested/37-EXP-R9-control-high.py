#!/usr/bin/env python3
"""Answers EXP-R9 (FIND-IMPACT-01, FIND-O6-NOISE-FLOOR-01). The control arm at high load. Replaces 63-EXP-R9-control-high."""
from reqlib import guard
from reqruns import r9_body

ID = "37-EXP-R9-control-high"
guard(ID, 19)
r9_body(ID, ["EXP-R9", "FIND-IMPACT-01", "FIND-O6-NOISE-FLOOR-01", "FIND-O6-INSTRUMENT-01"], [("high", "control")] * 3)
