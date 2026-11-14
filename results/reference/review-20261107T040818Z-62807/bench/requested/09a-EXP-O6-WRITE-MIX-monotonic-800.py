#!/usr/bin/env python3
"""Answers EXP-O6-WRITE-MIX and FIND-O8-CLIENT-TIMEOUT: monotonic-key INSERT with oldest-row DELETE at 800 writes/s. See bench/reqmix.py."""
from reqlib import guard
from reqmix import write_mix2_body

ID = "09a-EXP-O6-WRITE-MIX-monotonic-800"
guard(ID, 19)
write_mix2_body(ID, "monotonic_insert_delete", 800)
