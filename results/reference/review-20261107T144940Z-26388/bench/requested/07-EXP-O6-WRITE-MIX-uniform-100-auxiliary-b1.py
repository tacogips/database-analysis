#!/usr/bin/env python3
"""Partitioned-v3. See write-mix-plan.json; all declared opportunities mandatory."""
from reqlib import guard
from reqmix import write_mix2_body

ID = '07-EXP-O6-WRITE-MIX-uniform-100-auxiliary-b1'
guard(ID, 19)
write_mix2_body(ID, 'uniform_insert_delete', 100, group='auxiliary', block=0)
