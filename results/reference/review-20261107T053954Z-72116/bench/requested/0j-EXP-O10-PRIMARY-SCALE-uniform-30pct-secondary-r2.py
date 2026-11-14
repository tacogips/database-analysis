#!/usr/bin/env python3
"""Answers EXP-O10-PRIMARY-SCALE (FIND-O10-PRIMARY-SCALE): 10M rows, scattered INSERT and DELETE at 30% of closed-loop capacity. Argv is <host> <version>. Partitioned protocol, repetition2/3, groupsecondary. See bench/reqscale.py."""
import sys
from reqlib import guard
from reqscale import scale_body

ID = "0j-EXP-O10-PRIMARY-SCALE-uniform-30pct-secondary-r2"
guard(ID, 19)
scale_body(ID, sys.argv[1], sys.argv[2], "uniform_insert_delete", .30, 10_000_000, repeat=1, group="secondary")
