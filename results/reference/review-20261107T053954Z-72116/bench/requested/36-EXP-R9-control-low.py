#!/usr/bin/env python3
"""Answers EXP-R9 (FIND-IMPACT-01, FIND-O6-NOISE-FLOOR-01). The control arm at low load: the same windows, three times, nothing running.
The 60 s middle window is also cut into 12, 20 and 30 s (equal_windows), to match the length of each count arm. Replaces 62-EXP-R9-control."""
from reqlib import guard
from reqruns import r9_body

ID = "36-EXP-R9-control-low"
guard(ID, 19)
r9_body(ID, ["EXP-R9", "FIND-IMPACT-01", "FIND-O6-NOISE-FLOOR-01", "FIND-O6-INSTRUMENT-01"], [("low", "control")] * 3)
