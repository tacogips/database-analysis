#!/usr/bin/env python3
"""Answers EXP-O2-1 (the guideline's procedure on every lab table), EXP-SOL-3 (timing that includes the NULL count),
FIND-PREFLIGHT-01 (the corrected preflight on every table), and the secondary+PK plan rule on t_dense.

Run again after the loop and monitor changes (FIND-PREFLIGHT-01): the earlier result predates them.
For each table: truth from a plain COUNT(*) on an admin connection, then the flow as a developer would run it with a new
connection: SQL 0, SQL M through a monitor, SQL 2, then SQL 3 and SQL 4 from countlib, width 10000, 500 ms per statement, no sleep.
Each table is read only.
"""
import threading, time
import pymysql
from count_preflight import analyze
from count_shapes import TABLES
from countlib import CountMonitor, choose_index, session_setup, split_count, split_plans, split_statements, plan_check, key_args, walk_count
from lablib import PRIMARY, connect, query
from reqlib import Results, guard, session_status

ID = "10-EXP-O2-1-all-tables"
guard(ID, 15)
r = Results(ID, ["EXP-O2-1", "EXP-SOL-3", "FIND-PREFLIGHT-01", "FIND-R3-LOOPEDGE-01"], width=10000, statement_limit_ms=500, sql2_limit_ms=1000)
admin = connect(read_timeout=900)


def sql2(c, table):
    before = session_status(c, "Max_execution_time_exceeded").get("Max_execution_time_exceeded", 0)
    t = time.monotonic()
    try:
        value = query(c, f"SELECT /*+ MAX_EXECUTION_TIME(1000) */ COUNT(*) FROM `{table}`")[0][0]
        res = {"value": value}
    except pymysql.MySQLError as e:
        res = {"errno": e.args[0]}
    res["ms"] = round((time.monotonic() - t) * 1000, 1)
    res["timer_counter_delta"] = session_status(c, "Max_execution_time_exceeded").get("Max_execution_time_exceeded", 0) - before
    return res


def one_table(table, d):
    d["preflight"] = analyze(connect(read_timeout=60), "bench", table)
    d["recommendation"] = d["preflight"]["recommendation"]
    d["truth"] = query(admin, f"SELECT COUNT(*) FROM `{table}`")[0][0]
    c = connect(read_timeout=120)
    d["session"] = session_setup(c)
    d["sql2"] = sql2(c, table)
    if d["preflight"]["type"] == "VIEW" or d["preflight"]["engine"] != "InnoDB":
        d["split"] = "not applicable: flow does not split this table"
        return
    plans = split_plans(c, "bench", table)
    d["plans"], d["rejected"] = plans["plans"], plans["rejected"]
    if not plans["plans"]:
        d["split"] = "no eligible index: flow goes to the 'cannot split' choices"
        return
    plan = plans["plans"][0]
    stop = threading.Event()
    mon = CountMonitor(PRIMARY, "bench", table, c.thread_id(), stop, interval=1)
    mon.start()
    res = split_count(c, table, plan, width=10_000, limit_ms=500, duty=None, stop=stop, keep_sql=True)
    mon.finish()
    res["monitor_reason"] = mon.reason
    res["correct"] = res["rows"] == d["truth"]
    if res["status"] == "rejected:plan":  # keep the value too, so the rule's cost is visible
        again = split_count(c, table, plan, width=10_000, limit_ms=500, duty=None, enforce_plan=False)
        res["without_plan_rule"] = {"status": again["status"], "rows": again["rows"], "correct": again["rows"] == d["truth"], "seconds_total": again["seconds_total"], "stmt_ms_max": again["stmt_ms_max"]}
    d["split"] = res
    c.close()


with r.section("tables") as out:
    for table in TABLES:
        if r.time_left() < 120:
            out["stopped_early_before"] = table
            break
        d = out.setdefault(table, {})
        try:
            one_table(table, d)
        except Exception as e:
            d["_error"] = repr(e)[:400]
            r.errors.append(table)
        split = d.get("split")
        print(table, d.get("truth"), split if isinstance(split, str) else (split or {}).get("status"), (split or {}).get("correct") if isinstance(split, dict) else "", flush=True)
        r.save()

with r.section("secondary_plus_pk_plan_rule") as d:
    # The rule's addedScope: does used_key_parts list the appended primary key on a secondary index?
    c = connect(read_timeout=120)
    session_setup(c)
    for table in ("t_dense", "t_events", "orders", "customer_tags", "order_items"):
        plans = split_plans(c, "bench", table)
        plan = next((p for p in plans["plans"] if p["index"] != "PRIMARY"), None)
        if not plan:
            d[table] = {"no_secondary_plan": plans["rejected"]}
            continue
        s = split_statements(table, plan, 10_000, 500)
        key = tuple(query(c, s["first"])[0])
        before = session_status(c, "Handler_read%")
        query(c, s["mid"], key_args(key))
        after = session_status(c, "Handler_read%")
        d[table] = {"plan": plan, "check": plan_check(c, plan, s["explain_mid"], key_args(key)), "handler_delta": {k: after[k] - before[k] for k in after if after[k] != before.get(k)}}

with r.section("old_timer_vs_new_timer") as d:
    # EXP-SOL-3: the old walker starts its timer after the NULL count. Time the whole call and the NULL count alone.
    for table in ("t_uniq_nullable", "d_soft_delete", "categories"):
        c = connect(read_timeout=300)
        best, idx, _ = choose_index(c, "bench", table)
        t = time.monotonic()
        old = walk_count(c, "bench", table, best, idx)
        whole = time.monotonic() - t
        t = time.monotonic()
        nulls = query(c, f"SELECT COUNT(*) FROM `{table}` FORCE INDEX (`{best['name']}`) WHERE `{best['cols'][0]}` IS NULL")[0][0] if best["nullable"] else None
        d[table] = {"index": best["name"], "old_reported_seconds": old["seconds"], "whole_call_seconds": round(whole, 3), "null_count_seconds": round(time.monotonic() - t, 3), "null_rows": nulls, "rows": old["rows"]}

r.finish()
