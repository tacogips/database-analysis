#!/usr/bin/env python3
"""Answers EXP-O6-WRITE-MIX and FIND-O8-CLIENT-TIMEOUT: uniform updates of the indexed column at 800 writes/s, second design. See bench/reqmix.py."""
from reqlib import guard
from reqmix import write_mix2_body

ID = "06-EXP-O6-WRITE-MIX-indexed-800"
guard(ID, 19)
write_mix2_body(ID, "indexed", 800)
