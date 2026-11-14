#!/usr/bin/env python3
"""Answers EXP-S8-REPLICA-CHANNEL-GATE (FIND-S8-REPLICA-CHANNEL-01). The local compose replica only.

A second, never-connected channel is added, so one channel runs and one is stopped. The procedure must refuse before changing anything. An unqualified START shows why. The channel is removed in finally."""
from countlib import replica_channels, replica_point_count
from lablib import REPLICA, connect, query
from reqlib import Results, guard, sql_try

ID = "03-EXP-S8-REPLICA-CHANNEL-GATE"
guard(ID, 2)
r = Results(ID, ["EXP-S8-REPLICA-CHANNEL-GATE", "FIND-S8-REPLICA-CHANNEL-01"])
rc = connect(REPLICA, read_timeout=60)
p = connect(read_timeout=60)
CH = "req_test"


def states():
    return {row.get("Channel_Name"): {"sql": row.get("Replica_SQL_Running"), "io": row.get("Replica_IO_Running")} for row in replica_channels(rc)}


try:
    with r.section("single_channel_control") as d:
        d["before"] = states()
        c = connect(REPLICA, read_timeout=60)
        res = replica_point_count(c, "t_repl")
        res.pop("gtid_at_stop", None)
        d["run"] = res
        d["after"] = states()
        c.close()

    with r.section("two_channels") as d:
        query(rc, f"CHANGE REPLICATION SOURCE TO SOURCE_HOST='primary', SOURCE_USER='root', SOURCE_PASSWORD='lab', SOURCE_AUTO_POSITION=1, GET_SOURCE_PUBLIC_KEY=1 FOR CHANNEL '{CH}'")
        d["before"] = states()
        d["first_row_of_show_status_only"] = replica_channels(rc)[0].get("Channel_Name")
        c = connect(REPLICA, read_timeout=60)
        res = replica_point_count(c, "t_repl")
        d["run"] = res
        d["after_refusal"] = states()
        d["changed_nothing"] = d["before"] == d["after_refusal"]
        c.close()
        # Why it refuses: START with no channel reaches every channel, including the one that was stopped on purpose.
        d["unqualified_start"] = sql_try(rc, "START REPLICA SQL_THREAD")["ok"]
        d["after_unqualified_start"] = states()
        sql_try(rc, f"STOP REPLICA SQL_THREAD FOR CHANNEL '{CH}'")
        d["after_stopping_the_test_channel"] = states()
finally:
    sql_try(rc, f"STOP REPLICA FOR CHANNEL '{CH}'")
    r.data["remove_channel"] = sql_try(rc, f"RESET REPLICA ALL FOR CHANNEL '{CH}'")["ok"]
    sql_try(rc, "START REPLICA SQL_THREAD FOR CHANNEL ''")
    r.data["final"] = states()
    target = query(p, "SELECT @@GLOBAL.gtid_executed")[0][0]
    r.data["final_catch_up"] = query(rc, "SELECT WAIT_FOR_EXECUTED_GTID_SET(%s, 60)", (target,))[0][0]
r.finish()
