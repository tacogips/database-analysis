#!/usr/bin/env python3
"""Answers EXP-O6-WRITE-MIX and FIND-O8-CLIENT-TIMEOUT: uniform-key INSERT and DELETE at 800 writes/s. See bench/reqmix.py."""
from reqlib import guard
from reqmix import write_mix2_body

ID = "08-EXP-O6-WRITE-MIX-uniform-800"
guard(ID, 19)
write_mix2_body(ID, "uniform_insert_delete", 800)
