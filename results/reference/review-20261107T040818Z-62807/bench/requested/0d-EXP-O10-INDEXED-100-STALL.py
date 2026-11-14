#!/usr/bin/env python3
"""Answers EXP-O10-INDEXED-100-STALL (FIND-O10-INDEXED-100-STALL): uniform updates of the indexed column at 100 writes/s, as in script 05, with five PRIMARY splits at the end, each after a no-count control of the same length.
It logs the server at 1 Hz, saves a window for every arm including the drift arm, and lists writes over a second by time. If no stall recurs, the earlier one stays reported as one unexplained event. See bench/reqmix.py."""
from reqlib import guard
from reqmix import write_mix2_body

ID = "0d-EXP-O10-INDEXED-100-STALL"
guard(ID, 19)
write_mix2_body(ID, "indexed", 100, tail_splits=5, answers=["EXP-O10-INDEXED-100-STALL", "FIND-O10-INDEXED-100-STALL", "EXP-O10-ROW-CEILING"])
