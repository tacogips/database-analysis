#!/usr/bin/env python3
"""Answers EXP-O6-LONG-UNDO (FIND-O6-LONG-UNDO-01) at 400 writes/s."""
from reqlib import guard
from reqruns import long_undo_body

ID = "3h-EXP-O6-LONG-UNDO-high"
guard(ID, 18)
long_undo_body(ID, "high", dict(readers=4, writers=8, rate=50))
