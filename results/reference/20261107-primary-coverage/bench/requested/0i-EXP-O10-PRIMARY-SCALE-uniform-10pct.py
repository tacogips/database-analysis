#!/usr/bin/env python3
"""Answers EXP-O10-PRIMARY-SCALE (FIND-O10-PRIMARY-SCALE): 10M rows, scattered INSERT and DELETE at 10% of closed-loop capacity. Argv is <host> <version>. See bench/reqscale.py."""
import sys
from reqlib import guard
from reqscale import scale_body

ID = "0i-EXP-O10-PRIMARY-SCALE-uniform-10pct"
guard(ID, 19)
scale_body(ID, sys.argv[1], sys.argv[2], "uniform_insert_delete", .10, 10_000_000)
