#!/usr/bin/env python3
"""Answers EXP-SOL-7 (FIND-SOL-DDL-01) and EXP-R14 (FIND-RESUME-01): a table replaced during a split, and resuming on another server.

Private tables, except the last section, which reads t_repl on the primary and on a paused replica. The applier is always restarted.
"""
import json, os, tempfile, time
from countlib import forced_plan, session_setup, split_count, split_plans
from lablib import REPLICA, connect, query
from reqlib import Results, builder, guard, reset_schema, sql_try

ID = "46-EXP-SOL-7"
guard(ID, 5)
r = Results(ID, ["EXP-SOL-7", "EXP-R14", "FIND-SOL-DDL-01", "FIND-RESUME-01"])
b = builder()
S = "req_gen"
reset_schema(b, S)
TABLE_ID = "SELECT NAME, TABLE_ID FROM information_schema.INNODB_TABLES WHERE NAME = %s OR NAME LIKE %s"


def ids(name):
    return [list(x) for x in query(b, TABLE_ID, (f"{S}/{name}", f"{S}/{name}#p#%"))]


def walk_with_hook(name, hook, width=1):
    c = connect(db=S, read_timeout=60)
    session_setup(c)
    plan = forced_plan(c, S, name, "PRIMARY")
    start = ids(name)
    calls = [0]

    def on_step(_n, _ms, _cursor):
        calls[0] += 1
        hook(calls[0])
    s = split_count(c, name, plan, width=width, limit_ms=500, duty=None, on_step=on_step)
    c.close()
    return {"status": s["status"], "sum": s["rows"], "table_id_same": start == ids(name), "errors": s.get("errors")}


def reset_ab():
    query(b, f"DROP TABLE IF EXISTS {S}.a, {S}.b, {S}.c")
    query(b, f"CREATE TABLE {S}.a (id INT PRIMARY KEY)")
    query(b, f"CREATE TABLE {S}.b (id INT PRIMARY KEY)")
    query(b, f"INSERT INTO {S}.a VALUES (1),(2),(3)")
    query(b, f"INSERT INTO {S}.b VALUES (3),(4)")


with r.section("swap_during_walk") as d:
    reset_ab()

    def swap_once(step):
        if step == 1:
            query(b, f"RENAME TABLE {S}.a TO {S}.c, {S}.b TO {S}.a, {S}.c TO {S}.b")
    d["swap"] = {**walk_with_hook("a", swap_once), "a_at_start": 3, "a_at_end": query(b, f"SELECT COUNT(*) FROM {S}.a")[0][0]}
    reset_ab()

    def swap_and_back(step):
        if step == 1:
            query(b, f"RENAME TABLE {S}.a TO {S}.c, {S}.b TO {S}.a, {S}.c TO {S}.b")
        if step == 2:
            query(b, f"RENAME TABLE {S}.a TO {S}.c, {S}.b TO {S}.a, {S}.c TO {S}.b")
    d["swap_and_back"] = {**walk_with_hook("a", swap_and_back), "a_at_start": 3, "a_at_end": query(b, f"SELECT COUNT(*) FROM {S}.a")[0][0]}
    reset_ab()

    def truncate_at(step):
        if step == 1:
            query(b, f"TRUNCATE {S}.a")
    d["truncate"] = {**walk_with_hook("a", truncate_at), "a_at_start": 3, "a_at_end": query(b, f"SELECT COUNT(*) FROM {S}.a")[0][0]}

with r.section("partition_ddl_during_walk") as d:
    def fresh_parts():
        query(b, f"DROP TABLE IF EXISTS {S}.pt, {S}.ex")
        query(b, f"CREATE TABLE {S}.pt (id INT PRIMARY KEY) PARTITION BY RANGE (id) (PARTITION p1 VALUES LESS THAN (100), PARTITION p2 VALUES LESS THAN (200))")
        query(b, f"INSERT INTO {S}.pt SELECT n FROM bench.seq1m WHERE n<=150")
        query(b, f"CREATE TABLE {S}.ex (id INT PRIMARY KEY)")
        query(b, f"INSERT INTO {S}.ex SELECT 100+n FROM bench.seq1m WHERE n<=20")
    for label, sql in (("drop_partition", "ALTER TABLE {S}.pt DROP PARTITION p2"), ("truncate_partition", "ALTER TABLE {S}.pt TRUNCATE PARTITION p2"), ("exchange_partition", "ALTER TABLE {S}.pt EXCHANGE PARTITION p2 WITH TABLE {S}.ex")):
        fresh_parts()
        def hook(step, sql=sql):
            if step == 3:
                query(b, sql.format(S=S))
        res = walk_with_hook("pt", hook, width=10)
        res["end_count"] = query(b, f"SELECT COUNT(*) FROM {S}.pt")[0][0]
        res["start_count"] = 150
        d[label] = res

with r.section("resume_R14") as d:
    # A checkpoint with and without server identity and atomic save. Interrupted, then resumed on a replica that lags.
    primary = connect(read_timeout=120)
    replica = connect(REPLICA, read_timeout=120)
    session_setup(primary)
    plan = split_plans(primary, "bench", "t_repl")["plans"][0]
    d["plan"] = plan["index"]
    w = connect(read_timeout=60)
    uuid_primary = query(primary, "SELECT @@server_uuid")[0][0]
    table_id = query(primary, "SELECT TABLE_ID FROM information_schema.INNODB_TABLES WHERE NAME='bench/t_repl'")[0][0]
    # Interrupted after a known number of steps, saved after the step.
    state = {}

    def save_after(n, _ms, cursor):
        state.update(rows=state.get("rows", 0) + n, cursor=[int(v) if isinstance(v, int) else str(v) for v in cursor], steps=state.get("steps", 0) + 1, server_uuid=uuid_primary, table_id=table_id, plan_index=plan["index"])
    s = split_count(primary, "t_repl", plan, width=100_000, limit_ms=5000, duty=None, on_step=save_after, max_steps=3)
    d["interrupted"] = {"status": s["status"], "state": dict(state)}
    # Atomic save: the record is written to a temporary file and renamed, so a crash leaves the old record or the new one.
    path = os.path.join(tempfile.gettempdir(), "r14_state.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    with open(path) as f:
        saved = json.load(f)
    # Resuming on the replica: the identity check must refuse. The old method has no check and mixes the two.
    replica_uuid = query(replica, "SELECT @@server_uuid")[0][0]
    d["identity_check_refuses_other_server"] = saved["server_uuid"] != replica_uuid
    d["old_method_on_paused_replica"] = {}
    try:
        query(replica, "STOP REPLICA SQL_THREAD")
        extra = 0
        for i in range(100):
            query(w, "INSERT INTO t_repl (id,v,note) VALUES (%s,0,'r14')", (40_000_000 + i,))
            extra += 1
        session_setup(replica)
        cursor = tuple(saved["cursor"])
        rows = saved["rows"]
        rep = query(replica, "SELECT /*+ MAX_EXECUTION_TIME(60000) */ COUNT(*) FROM t_repl WHERE id > %s", cursor)[0][0]
        pri = query(connect(read_timeout=120), "SELECT COUNT(*) FROM t_repl")[0][0]
        d["old_method_on_paused_replica"] = {"mixed_sum": rows + rep, "primary_true_count": pri, "difference": rows + rep - pri, "rows_committed_while_paused": extra}
    finally:
        sql_try(replica, "START REPLICA SQL_THREAD")
        query(w, "DELETE FROM t_repl WHERE id>=40000000")
        query(w, "ALTER TABLE t_repl AUTO_INCREMENT=20000001")
        target = query(w, "SELECT @@GLOBAL.gtid_executed")[0][0]
        d["replica_caught_up_wait"] = query(replica, "SELECT WAIT_FOR_EXECUTED_GTID_SET(%s, 120)", (target,))[0][0]
    # Connection loss, then a clean resume on the same server, compared with the truth of the same id range.
    c = connect(read_timeout=120)
    session_setup(c)
    t1 = split_count(c, "t_repl", plan, width=1000, limit_ms=500, duty=None, max_steps=10)
    query(connect(read_timeout=10), f"KILL {c.thread_id()}")
    time.sleep(.1)
    alive = sql_try(c, "SELECT 1")
    d["connection_killed"] = {"statement_after_kill": {"ok": alive["ok"], "errno": alive.get("errno")}}
    c2 = connect(read_timeout=120)
    session_setup(c2)
    d["resumed_with_new_session"] = query(c2, "SELECT @@autocommit, @@time_zone, @@max_execution_time")[0]
    tail_cursor = int(t1["last_cursor"][0])
    resumed = query(c2, "SELECT /*+ MAX_EXECUTION_TIME(60000) */ COUNT(*) FROM t_repl WHERE id > %s", (tail_cursor,))[0][0]
    d["resume_same_server"] = {"counted_before_loss": t1["partial_rows"], "counted_after_loss": resumed, "sum": t1["partial_rows"] + resumed, "truth": query(connect(read_timeout=120), "SELECT COUNT(*) FROM t_repl")[0][0]}

query(b, f"DROP DATABASE {S}")
r.finish()
