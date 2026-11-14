#!/usr/bin/env python3
"""Answers EXP-R8 (FIND-FAST-01), repeats 0 to 2 of 6: default COUNT(*), FORCE k_created and the split, each cold and warm."""
from reqlib import guard
from reqruns import r8_body

ID = "21-EXP-R8-a"
guard(ID, 20)
r8_body(ID, ["EXP-R8", "FIND-FAST-01"], [0, 1, 2])
