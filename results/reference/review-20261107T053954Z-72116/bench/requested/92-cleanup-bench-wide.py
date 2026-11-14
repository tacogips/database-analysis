#!/usr/bin/env python3
"""Removes bench_wide if 90-wide-count-forms was killed at the runner's limit before its own cleanup. Only that private schema. The replica has no copy: 90 builds it with sql_log_bin=0."""
from lablib import connect, query

c = connect(read_timeout=120)
query(c, "SET SESSION sql_log_bin=0")
print("bench_wide present:", query(c, "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='bench_wide'")[0][0])
query(c, "DROP DATABASE IF EXISTS bench_wide")
