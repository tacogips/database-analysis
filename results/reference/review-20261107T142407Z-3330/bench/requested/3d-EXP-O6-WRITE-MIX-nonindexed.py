#!/usr/bin/env python3
"""Answers EXP-O6-WRITE-MIX (FIND-O6-WRITE-SHAPE-01): writes that do not touch the counted index, on a private 20M row table. The control for the next two."""
from reqlib import guard
from reqruns import write_mix_body

ID = "3d-EXP-O6-WRITE-MIX-nonindexed"
guard(ID, 15)
write_mix_body(ID, "nonindexed")
