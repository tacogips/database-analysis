#!/usr/bin/env python3
"""3/5/10% targets, exact COUNT, seven TDE key/row-width distributions."""
import sys
from accuracy_frontier import static_frontier
from reqlib import guard

guard('01c-ACCURACY-COST', 18)
static_frontier('requested_accuracy_cost_frontier', *sys.argv[1:3])
