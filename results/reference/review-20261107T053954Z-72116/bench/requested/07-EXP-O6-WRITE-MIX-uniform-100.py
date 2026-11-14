#!/usr/bin/env python3
"""Answers EXP-O6-WRITE-MIX (FIND-O6-WRITE-SHAPE-01): uniform-key INSERT and DELETE at 100 writes/s. See bench/reqmix.py."""
from reqlib import guard
from reqmix import write_mix2_body

ID = "07-EXP-O6-WRITE-MIX-uniform-100"
guard(ID, 19)
write_mix2_body(ID, "uniform_insert_delete", 100)
