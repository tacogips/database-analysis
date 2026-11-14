#!/usr/bin/env python3
"""Answers EXP-R9 (FIND-IMPACT-01). Repeat 1, low load: working set 80% of the buffer pool. Baseline and recovery are 120 s each."""
from reqlib import guard
from reqruns import r9_body

ID = "30-EXP-R9-rep1-low"
guard(ID, 20)
r9_body(ID, ["EXP-R9", "FIND-IMPACT-01"], [("low", "default"), ("low", "split_nosleep"), ("low", "split_duty50")])
