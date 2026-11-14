#!/usr/bin/env python3
"""Partitioned-v3. See write-mix-plan.json; all declared opportunities mandatory."""
from reqlib import guard
from reqmix import write_mix2_body

ID = '06-EXP-O6-WRITE-MIX-indexed-800-auxiliary-b1'
guard(ID, 19)
write_mix2_body(ID, 'indexed', 800, group='auxiliary', block=0)
