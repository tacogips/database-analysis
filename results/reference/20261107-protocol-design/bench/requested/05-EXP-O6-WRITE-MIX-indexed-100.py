#!/usr/bin/env python3
"""Answers EXP-O6-WRITE-MIX (FIND-O6-WRITE-SHAPE-01): uniform updates of the indexed column at 100 writes/s, second design. See bench/reqmix.py."""
from reqlib import guard
from reqmix import write_mix2_body

ID = "05-EXP-O6-WRITE-MIX-indexed-100"
guard(ID, 19)
write_mix2_body(ID, "indexed", 100)
