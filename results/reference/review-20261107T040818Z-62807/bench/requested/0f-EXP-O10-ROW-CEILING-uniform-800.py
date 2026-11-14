#!/usr/bin/env python3
"""Answers EXP-O10-ROW-CEILING (FIND-O10-STEP-CEILING): the splits of script 08 at 800 scattered INSERT and DELETE per second, under the row ceiling. See bench/reqmix.py."""
from reqlib import guard
from reqmix import write_mix2_body

ID = "0f-EXP-O10-ROW-CEILING-uniform-800"
guard(ID, 19)
write_mix2_body(ID, "uniform_insert_delete", 800, split_only=True, answers=["EXP-O10-ROW-CEILING", "FIND-O10-STEP-CEILING"])
