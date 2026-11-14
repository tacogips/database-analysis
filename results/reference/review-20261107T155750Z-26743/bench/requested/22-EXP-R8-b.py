#!/usr/bin/env python3
"""Answers EXP-R8 (FIND-FAST-01), repeats 3 to 5 of 6. Each script runs its own workload, so the two parts are comparable but not one window."""
from reqlib import guard
from reqruns import r8_body

ID = "22-EXP-R8-b"
guard(ID, 20)
r8_body(ID, ["EXP-R8", "FIND-FAST-01"], [3, 4, 5])
