#!/usr/bin/env python3
"""Answers EXP-R9 (FIND-IMPACT-01). Repeat 3, high load. Replaces 61-EXP-R9-rep3-high."""
from reqlib import guard
from reqruns import r9_body

ID = "35-EXP-R9-rep3-high"
guard(ID, 20)
r9_body(ID, ["EXP-R9", "FIND-IMPACT-01", "FIND-O6-NOISE-FLOOR-01"], [("high", "split_duty50"), ("high", "default"), ("high", "split_nosleep")])
