#!/usr/bin/env python3
"""Marks when this lab run started, so later scripts can skip themselves rather than overrun the runner's 4 hour limit.
Numeric prefixes set the run order, because lab.rb runs scripts in sorted order. Edit this file to reset the clock in a later round."""
import os, time
from reqlib import MARKER

os.makedirs(os.path.dirname(MARKER), exist_ok=True)
with open(MARKER, "w") as f:
    f.write(time.strftime("%Y-%m-%dT%H:%M:%S\n"))
print("budget clock started: round 4 (revision 11)")
