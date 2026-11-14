#!/usr/bin/env python3
"""Partitioned-v3. See write-mix-plan.json; all declared opportunities mandatory."""
from reqlib import guard
from reqmix import write_mix2_body

ID = '05-EXP-O6-WRITE-MIX-indexed-100-statements-b2'
guard(ID, 19)
write_mix2_body(ID, 'indexed', 100, group='statements', block=1)
