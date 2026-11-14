#!/usr/bin/env python3
"""Puts the lab back if a script was killed at the runner's limit before its own cleanup. Runs last, before the data check.

92-wide-form-order, 93-metadata-accuracy and 94-metadata-on-replica were added outside the review loop and have no guard. They clean up in finally blocks, which a kill skips.
Only what they change: their private schemas, the sample-pages setting, super_read_only and read_only on the replica, the applier, and the 'bulk' rows in t_repl. Each is checked first, and what it found is printed."""
from lablib import REPLICA, connect, query
from reqlib import sql_try

p = connect(read_timeout=600)
r = connect(REPLICA, read_timeout=120)
query(p, "SET SESSION sql_log_bin=0")
for schema in ("bench_wide", "bench_wide2", "req_uneven"):
    print(schema, "present:", query(p, "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (schema,))[0][0])
    query(p, f"DROP DATABASE IF EXISTS {schema}")
print("sample pages:", query(p, "SELECT @@GLOBAL.innodb_stats_persistent_sample_pages")[0][0])
query(p, "SET GLOBAL innodb_stats_persistent_sample_pages=20")
print("replica read_only, super_read_only:", query(r, "SELECT @@read_only, @@super_read_only")[0])
query(r, "SET GLOBAL super_read_only=OFF")
query(r, "SET GLOBAL read_only=ON")
print("start applier:", sql_try(r, "START REPLICA SQL_THREAD FOR CHANNEL ''")["ok"])
query(r, "SET SESSION sql_log_bin=0")
query(r, "DROP USER IF EXISTS 'lab_reader'@'%'")
# The rows go through the primary in normal replication, so the replica removes them too.
d = connect(read_timeout=600)
removed = 0
while True:
    with d.cursor() as k:
        n = k.execute("DELETE FROM t_repl WHERE note='bulk' LIMIT 100000")
    if not n:
        break
    removed += n
print("bulk rows removed:", removed)
print("probe94 column:", query(d, "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='t_repl' AND COLUMN_NAME='probe94'")[0][0])
if query(d, "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='t_repl' AND COLUMN_NAME='probe94'")[0][0]:
    query(d, "ALTER TABLE t_repl DROP COLUMN probe94")
target = query(d, "SELECT @@GLOBAL.gtid_executed")[0][0]
print("replica catch-up result:", query(r, "SELECT WAIT_FOR_EXECUTED_GTID_SET(%s, 300)", (target,))[0][0])
