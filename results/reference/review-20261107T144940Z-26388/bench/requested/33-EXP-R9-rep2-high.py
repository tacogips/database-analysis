#!/usr/bin/env python3
"""Answers EXP-R9 (FIND-IMPACT-01). Repeat 2, high load, in a different order. Skipped by budget last round; the first script to run this round."""
from reqlib import guard
from reqruns import r9_body

ID = "33-EXP-R9-rep2-high"
guard(ID, 20)
r9_body(ID, ["EXP-R9", "FIND-IMPACT-01", "FIND-O6-NOISE-FLOOR-01"], [("high", "default"), ("high", "split_nosleep"), ("high", "split_duty50")])
