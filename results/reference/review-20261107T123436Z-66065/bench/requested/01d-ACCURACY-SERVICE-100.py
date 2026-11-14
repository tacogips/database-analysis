#!/usr/bin/env python3
"""Paired100-column TDE control/exact/3%/10% under the same service budget."""
import sys
from accuracy_frontier import wide_service_frontier
from reqlib import guard

guard('01d-ACCURACY-SERVICE-100', 18)
wide_service_frontier('requested_accuracy_service_100', *sys.argv[1:3])
