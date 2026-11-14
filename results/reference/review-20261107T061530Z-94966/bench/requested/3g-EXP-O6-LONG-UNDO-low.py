#!/usr/bin/env python3
"""Answers EXP-O6-LONG-UNDO (FIND-O6-LONG-UNDO-01) at 100 writes/s: read views held 60, 180 and 300 s on a private table, plus a control with none."""
from reqlib import guard
from reqruns import long_undo_body

ID = "3g-EXP-O6-LONG-UNDO-low"
guard(ID, 18)
long_undo_body(ID, "low", dict(readers=4, writers=4, rate=25))
