#!/usr/bin/env python3
"""Answers EXP-O10-PRIMARY-SCALE (FIND-O10-PRIMARY-SCALE): 10M rows, indexed-column updates at 30% of closed-loop capacity. Argv is <host> <version>. Partitioned protocol, repetition1/3, groupcore. See bench/reqscale.py."""
import sys
from reqlib import guard
from reqscale import scale_body

ID = "0h-EXP-O10-PRIMARY-SCALE-indexed-30pct"
guard(ID, 19)
scale_body(ID, sys.argv[1], sys.argv[2], "indexed", .30, 10_000_000, repeat=0, group="core")
