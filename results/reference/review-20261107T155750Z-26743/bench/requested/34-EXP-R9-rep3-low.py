#!/usr/bin/env python3
"""Answers EXP-R9 (FIND-IMPACT-01). Repeat 3, low load. Replaces 60-EXP-R9-rep3-low, so that it runs before the optional scripts."""
from reqlib import guard
from reqruns import r9_body

ID = "34-EXP-R9-rep3-low"
guard(ID, 20)
r9_body(ID, ["EXP-R9", "FIND-IMPACT-01", "FIND-O6-NOISE-FLOOR-01"], [("low", "split_nosleep"), ("low", "split_duty50"), ("low", "default")])
