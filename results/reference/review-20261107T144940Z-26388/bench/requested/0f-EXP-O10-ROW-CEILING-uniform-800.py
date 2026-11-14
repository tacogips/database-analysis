#!/usr/bin/env python3
"""Partitioned-v3. See write-mix-plan.json; all declared opportunities mandatory."""
from reqlib import guard
from reqmix import write_mix2_body

ID = '0f-EXP-O10-ROW-CEILING-uniform-800'
guard(ID, 19)
write_mix2_body(ID, 'uniform_insert_delete', 800, split_only=True, answers=['EXP-O10-ROW-CEILING', 'FIND-O10-STEP-CEILING'], group='splits', block=0)
