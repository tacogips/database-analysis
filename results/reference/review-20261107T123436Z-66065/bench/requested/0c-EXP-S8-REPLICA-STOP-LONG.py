#!/usr/bin/env python3
"""Nonlocking remainder of EXP-S8-REPLICA-STOP-FAILURE: already-stopped control only.
Failed-STOP blocking injection is superseded_by_explicit_user_scope, not measured here.
"""
from countlib import replica_channels, replica_point_count
from lablib import REPLICA, connect, query
from reqlib import Results, guard
ID = "0c-EXP-S8-REPLICA-STOP-LONG"
guard(ID, 8)
r = Results(ID, ["EXP-S8-REPLICA-STOP-FAILURE", "FIND-S8-REPLICA-STOP-FAILURE-01"])
rc = connect(REPLICA, read_timeout=120)
def running():
    return replica_channels(rc)[0].get("Replica_SQL_Running")
try:
    with r.section("already_stopped_control") as d:
        query(rc, "STOP REPLICA SQL_THREAD FOR CHANNEL ''")
        c = connect(REPLICA, read_timeout=60)
        res = replica_point_count(c, "t_repl", max_execution_ms=200)
        res.pop("gtid_at_stop", None)
        d["call"] = res
        d["left_stopped"] = running() == "No"
        c.close()
        query(rc, "START REPLICA SQL_THREAD FOR CHANNEL ''")
finally:
    query(rc, "START REPLICA SQL_THREAD FOR CHANNEL ''")
    rc.close()
r.finish()
