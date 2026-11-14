#!/usr/bin/env python3
"""Answers EXP-R9 (FIND-IMPACT-01). Repeat 1, high load."""
from reqlib import guard
from reqruns import r9_body

ID = "31-EXP-R9-rep1-high"
guard(ID, 20)
r9_body(ID, ["EXP-R9", "FIND-IMPACT-01"], [("high", "split_nosleep"), ("high", "split_duty50"), ("high", "default")])
