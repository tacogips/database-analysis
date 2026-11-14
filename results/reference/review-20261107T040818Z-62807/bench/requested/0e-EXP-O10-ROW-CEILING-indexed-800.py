#!/usr/bin/env python3
"""Answers EXP-O10-ROW-CEILING (FIND-O10-STEP-CEILING): the splits of script 06 at 800 indexed writes/s, under the row ceiling the guideline prescribes. Statement arms are not repeated. See bench/reqmix.py."""
from reqlib import guard
from reqmix import write_mix2_body

ID = "0e-EXP-O10-ROW-CEILING-indexed-800"
guard(ID, 19)
write_mix2_body(ID, "indexed", 800, split_only=True, answers=["EXP-O10-ROW-CEILING", "FIND-O10-STEP-CEILING"])
