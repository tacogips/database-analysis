#!/usr/bin/env python3
"""Answers EXP-R9 (FIND-IMPACT-01). Repeat 2, low load, in a different order."""
from reqlib import guard
from reqruns import r9_body

ID = "32-EXP-R9-rep2-low"
guard(ID, 20)
r9_body(ID, ["EXP-R9", "FIND-IMPACT-01"], [("low", "split_duty50"), ("low", "default"), ("low", "split_nosleep")])
