#!/usr/bin/env python3
"""Answers EXP-O10-PRIMARY-SCALE (FIND-O10-PRIMARY-SCALE): 10M rows, about 4 times a 256 MB pool, indexed-column updates at 10% of closed-loop capacity. Argv is <host> <version>. Partitioned protocol, repetition2/3, groupcore. See bench/reqscale.py."""
import sys
from reqlib import guard
from reqscale import scale_body

ID = "0g-EXP-O10-PRIMARY-SCALE-indexed-10pct-core-r2"
guard(ID, 19)
scale_body(ID, sys.argv[1], sys.argv[2], "indexed", .10, 10_000_000, repeat=1, group="core")
