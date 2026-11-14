#!/usr/bin/env python3
"""Answers EXP-O8-PKONLY-THREADS (FIND-O8-PKONLY-PARALLEL): the same on a table that fits in the pool. Each count is short, so each arm repeats it for 10 s. See bench/reqmix.py."""
from reqlib import guard
from reqmix import pkonly_body

ID = "0b-EXP-O8-PKONLY-THREADS-cached"
guard(ID, 12)
pkonly_body(ID, 3_000_000, repeat_seconds=10)
