#!/usr/bin/env python3
"""Five mandatory PRIMARY/control pairs across scripts; script05 covers shared main arms."""
from reqlib import guard
from reqmix import write_mix2_body
ID = '0d-EXP-O10-INDEXED-100-STALL-tail-r3'
guard(ID, 19)
write_mix2_body(ID, "indexed", 100, group="tail", tail_repeat=2, answers=["EXP-O10-INDEXED-100-STALL", "FIND-O10-INDEXED-100-STALL", "EXP-O10-ROW-CEILING"])
