#!/usr/bin/env python3
"""Answers EXP-O6-LOOP-RULES and EXP-O2-2's integration (FIND-STEP-01, FIND-R6-MDL-AS-TIMEOUT, FIND-O6-WALK-CEILING-01): every stop path, through split_count itself.

Timer: a width so large each step reaches its limit, which must halve. Third-party KILL: must stop with the width unchanged. Pending DDL: the same counterexample that reported 'unsplittable' before.
Hint lost: must stop on long success. Other errors, ceilings and a cursor that does not advance. Reads t_dense; the rest uses a private table.
"""
import threading, time
from countlib import forced_plan, session_setup, split_count, split_plans
from lablib import connect, query
from reqlib import Results, bounded, builder, guard, reset_schema, sql_try

ID = "3a-EXP-O6-LOOP-RULES"
guard(ID, 8)
r = Results(ID, ["EXP-O6-LOOP-RULES", "FIND-STEP-01", "FIND-R6-MDL-AS-TIMEOUT", "FIND-O6-WALK-CEILING-01", "FIND-R3-HINT-01"])
KEEP = ("status", "rows", "partial_rows", "steps", "width_start", "width_end", "width_reductions", "errors", "seconds_total", "long_success_ms", "statements")
HUGE = 40_000_000  # t_dense reads about 10M entries a second, so a step this wide overruns any limit below


def keep(s):
    return {k: s.get(k) for k in KEEP}


def dense():
    c = connect(read_timeout=120)
    session_setup(c)
    return c, next(p for p in split_plans(c, "bench", "t_dense")["plans"] if p["index"] == "k_created")


def timer_arm():
    c, plan = dense()
    s = split_count(c, "t_dense", plan, width=HUGE, limit_ms=300, duty=None, max_steps=2, db="bench", enforce_plan=False)
    c.close()
    return keep(s)


def kill_arm(at_ms=150):
    c, plan = dense()
    k = connect(read_timeout=10)
    timer = threading.Timer(at_ms / 1000, lambda: query(k, f"KILL QUERY {c.thread_id()}"))
    timer.start()
    s = split_count(c, "t_dense", plan, width=HUGE, limit_ms=5000, duty=None, max_steps=2, db="bench", enforce_plan=False)
    timer.cancel()
    timer.join(2)
    k.close()
    c.close()
    return keep(s)


def lost_hint_arm():
    """No hint and no session limit, as if a proxy stripped the comment and SQL 0's limit were not in force."""
    c, plan = dense()
    query(c, "SET SESSION max_execution_time = 0")
    s = split_count(c, "t_dense", plan, width=20_000_000, limit_ms=0, expected_limit_ms=500, duty=None, max_steps=2, db="bench", enforce_plan=False)
    c.close()
    return keep(s)


def session_limit_only_arm():
    """The hint is lost and SQL 0's session limit catches the statement."""
    c, plan = dense()
    query(c, "SET SESSION max_execution_time = 500")
    s = split_count(c, "t_dense", plan, width=HUGE, limit_ms=0, expected_limit_ms=500, duty=None, max_steps=2, db="bench", enforce_plan=False)
    c.close()
    return keep(s)


with r.section("through_split_count") as d:
    d["timer"] = [bounded(60, timer_arm) for _ in range(5)]
    d["third_party_kill"] = [bounded(30, kill_arm) for _ in range(5)]
    d["lost_hint"] = [bounded(60, lost_hint_arm) for _ in range(3)]
    d["session_limit_only"] = [bounded(60, session_limit_only_arm) for _ in range(3)]

with r.section("private_table") as d:
    b = builder()
    reset_schema(b, "req_rules")
    query(b, "CREATE TABLE req_rules.t (id BIGINT PRIMARY KEY, code VARCHAR(20) NOT NULL, v INT NOT NULL, KEY k_code (code))")
    query(b, "INSERT INTO req_rules.t SELECT n, CONCAT('k',LPAD(n,8,'0')), n FROM bench.seq1m WHERE n<=100000")
    query(b, "ANALYZE TABLE req_rules.t")
    c = connect(db="req_rules", read_timeout=60)
    session_setup(c)
    pk = forced_plan(c, "req_rules", "t", "PRIMARY")

    def pending_arm():
        # The earlier counterexample: a reader holds the table, an ALTER waits, the walk queues behind it with a 500 ms limit.
        hold = connect(db="req_rules", autocommit=False, read_timeout=60)
        query(hold, "START TRANSACTION")
        query(hold, "SELECT * FROM t WHERE id=1")
        ddl = connect(db="req_rules", read_timeout=60)
        query(ddl, "SET SESSION lock_wait_timeout=6")
        th = threading.Thread(target=lambda: sql_try(ddl, "ALTER TABLE t ADD COLUMN p INT NULL, ALGORITHM=INSTANT"), daemon=True)
        th.start()
        time.sleep(.5)
        job = connect(db="req_rules", read_timeout=60)
        session_setup(job)
        s = split_count(job, "t", pk, width=1000, limit_ms=500, duty=None, db="req_rules")
        query(hold, "ROLLBACK")
        th.join(10)
        for x in (hold, ddl, job):
            x.close()
        if query(b, "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='req_rules' AND TABLE_NAME='t' AND COLUMN_NAME='p'")[0][0]:
            query(b, "ALTER TABLE req_rules.t DROP COLUMN p")
        return keep(s)
    d["pending_ddl_5_times"] = [bounded(30, pending_arm) for _ in range(5)]

    def ceiling(**kw):
        job = connect(db="req_rules", read_timeout=60)
        session_setup(job)
        s = split_count(job, "t", pk, width=100, limit_ms=500, duty=None, db="req_rules", **kw)
        job.close()
        return keep(s)
    d["max_steps"] = ceiling(max_steps=5)
    d["max_rows"] = ceiling(max_rows=500)  # the ceiling the guideline prescribes, re-run for EXP-O10-ROW-CEILING
    d["max_seconds"] = ceiling(max_seconds=.05, pause_ms=20)

    def back_int(v):
        return v - 150 if v > 1000 else v

    def back_key(v):
        # After the first thousand rows the value sent is 150 behind, so each step returns a key 50 behind the cursor.
        if isinstance(v, str):
            n = int(v[1:])
            return f"k{n - 150:08d}" if n > 1000 else v
        return back_int(v)

    def backward_int():
        # For integers the comparison in the job is what stops it.
        job = connect(db="req_rules", read_timeout=60)
        session_setup(job)
        s = split_count(job, "t", pk, width=100, limit_ms=500, duty=None, db="req_rules", cursor_codec=back_int, max_steps=50)
        job.close()
        return keep(s)
    d["integer_cursor_sent_backward"] = bounded(20, backward_int)

    def backward_str(check):
        # For a string key only the server's comparison is right. Unchecked, the walk repeats until its ceiling.
        job = connect(db="req_rules", read_timeout=60)
        session_setup(job)
        p = forced_plan(job, "req_rules", "t", "k_code")
        s = split_count(job, "t", p, width=100, limit_ms=500, duty=None, db="req_rules", cursor_codec=back_key, max_rows=20_000, check_advance=check)
        job.close()
        return keep(s)
    d["string_cursor_sent_backward_checked"] = bounded(20, backward_str, True)
    d["string_cursor_sent_backward_unchecked_control"] = bounded(20, backward_str, False)

    def plain():
        job = connect(db="req_rules", read_timeout=60)
        session_setup(job)
        p = forced_plan(job, "req_rules", "t", "k_code")
        s = split_count(job, "t", p, width=1000, limit_ms=500, duty=None, db="req_rules")
        job.close()
        return {**keep(s), "correct": s["rows"] == 100000}
    d["string_cursor_normal_walk_with_advance_check"] = plain()

    def other_error(sql, kind_at):
        job = connect(db="req_rules", read_timeout=60)
        session_setup(job)
        seen = [0]

        def hook(_k, _s, _a):
            seen[0] += 1
            if seen[0] == kind_at:
                query(b, sql)
        s = split_count(job, "t", forced_plan(job, "req_rules", "t", "k_code"), width=100, limit_ms=500, duty=None, db="req_rules", before_statement=hook, max_steps=50)
        job.close()
        return keep(s)
    d["index_dropped_midway"] = other_error("ALTER TABLE req_rules.t DROP INDEX k_code", 6)
    query(b, "DROP DATABASE req_rules")
r.finish()
