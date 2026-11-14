#!/usr/bin/env python3
"""Partitioned-v3. See write-mix-plan.json; all declared opportunities mandatory."""
from reqlib import guard
from reqmix import write_mix2_body

ID = '09a-EXP-O6-WRITE-MIX-monotonic-800'
guard(ID, 19)
write_mix2_body(ID, 'monotonic_insert_delete', 800, group='splits', block=0)
