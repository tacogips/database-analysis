#!/usr/bin/env python3
"""Answers EXP-O10-PRIMARY-SCALE (FIND-O10-PRIMARY-SCALE): 20M rows, about 8 times a 256 MB pool, scattered INSERT and DELETE at 10% of closed-loop capacity. The size step against 0i. Argv is <host> <version>. Partitioned protocol, repetition3/3, groupcore. See bench/reqscale.py."""
import sys
from reqlib import guard
from reqscale import scale_body

ID = "0k-EXP-O10-PRIMARY-SCALE-uniform-10pct-20m-core-r3"
guard(ID, 19)
scale_body(ID, sys.argv[1], sys.argv[2], "uniform_insert_delete", .10, 20_000_000, repeat=2, group="core")
