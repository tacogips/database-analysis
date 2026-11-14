#!/usr/bin/env python3
"""Answers EXP-O8-PKONLY-THREADS (FIND-O8-PKONLY-PARALLEL): a real primary-key-only table larger than the 1.5 GB pool, COUNT(*) with no hint at 1, 2 and 4 read threads, with a control of equal length. See bench/reqmix.py."""
from reqlib import guard
from reqmix import pkonly_body

ID = "0a-EXP-O8-PKONLY-THREADS-large"
guard(ID, 19)
pkonly_body(ID, 30_000_000)
