#!/usr/bin/env python3
"""Answers EXP-R1 (FIND-KEY-ENUM-01): does the ENUM numeric cursor read more as the table grows, and does walking value by value avoid it? Private table grown to 1M, 5M and 20M rows. Skipped by budget last round."""
import time
from lablib import connect, query
from reqlib import Results, builder, guard, reset_schema, session_status

ID = "53-EXP-R1"
guard(ID, 12)
r = Results(ID, ["EXP-R1", "FIND-KEY-ENUM-01"])
S = "req_enum"
b = builder()
reset_schema(b, S)
query(b, f"USE {S}")
query(b, "CREATE TABLE t (status ENUM('z','a','m') NOT NULL, id BIGINT NOT NULL, v INT NOT NULL, PRIMARY KEY (status, id))")


def grow(start, end):
    for off in range(start, end, 500_000):
        query(b, f"INSERT INTO t SELECT ELT(1+MOD(n+{off},3),'z','a','m'), n+{off}, n FROM bench.seq1m WHERE n<=500000")
    query(b, "ANALYZE TABLE t")


def read_step(c, sql, args):
    before = session_status(c, "Handler_%")
    t = time.monotonic()
    row = query(c, sql, args)
    ms = (time.monotonic() - t) * 1000
    after = session_status(c, "Handler_%")
    delta = {k: after[k] - before.get(k, 0) for k in ("Handler_read_next", "Handler_read_prev", "Handler_read_key", "Handler_read_first", "Handler_read_rnd_next") if k in after and after[k] != before.get(k, 0)}
    return {"ms": round(ms, 2), "handler": delta, "row": [str(x) for x in row[0]] if row else None}


def plan(c, sql, args):
    return [str(x) for x in query(c, "EXPLAIN " + sql, args)[0][4:10]]


with r.section("scale") as d:
    size, c = 0, connect(db=S, read_timeout=300)
    for target in (1_000_000, 5_000_000, 20_000_000):
        if r.time_left() < 150:
            d["stopped_before"] = target
            break
        grow(size, target)
        size = target
        out = {}
        # Three positions in the table: early, middle and late in the enum's stored order, each 10000 entries.
        numeric = "SELECT status+0, id FROM t WHERE status+0 > %s OR (status+0 = %s AND id > %s) ORDER BY status, id LIMIT 1 OFFSET 9999"
        value_eq = "SELECT status, id FROM t WHERE status = %s AND id > %s ORDER BY id LIMIT 1 OFFSET 9999"
        value_tuple = "SELECT status+0, id FROM t WHERE status > %s OR (status = %s AND id > %s) ORDER BY status, id LIMIT 1 OFFSET 9999"
        for label, (num, label2, cursor) in {"first_value_start": (1, "z", 0), "second_value_mid": (2, "a", target // 2), "third_value_late": (3, "m", target - 60_000)}.items():
            out[label] = {"numeric_cursor": {**read_step(c, numeric, (num, num, cursor)), "plan": plan(c, numeric, (num, num, cursor))},
                          "value_equality": {**read_step(c, value_eq, (label2, cursor)), "plan": plan(c, value_eq, (label2, cursor))},
                          "numeric_literal_on_column": {**read_step(c, value_tuple, (num, num, cursor)), "plan": plan(c, value_tuple, (num, num, cursor))}}
        d[str(target)] = out
        r.save()
        print(target, out["third_value_late"]["numeric_cursor"]["ms"], out["third_value_late"]["value_equality"]["ms"], flush=True)

with r.section("value_by_value_walk_is_correct") as d:
    c = connect(db=S, read_timeout=300)
    truth = query(c, "SELECT COUNT(*) FROM t")[0][0]
    total, steps, worst = 0, 0, 0.0
    for value in ("z", "a", "m"):  # the ENUM's declared order; the labels come from COLUMN_TYPE
        cursor = None
        while True:
            where = "status = %s" + ("" if cursor is None else " AND id > %s")
            args = (value,) if cursor is None else (value, cursor)
            t = time.monotonic()
            row = query(c, f"SELECT /*+ MAX_EXECUTION_TIME(500) */ id FROM t WHERE {where} ORDER BY id LIMIT 1 OFFSET 99999", args)
            if not row:
                total += query(c, f"SELECT /*+ MAX_EXECUTION_TIME(500) */ COUNT(*) FROM t WHERE {where}", args)[0][0]
                worst = max(worst, (time.monotonic() - t) * 1000)
                break
            cursor = row[0][0]
            total += 100_000
            steps += 1
            worst = max(worst, (time.monotonic() - t) * 1000)
            if r.time_left() < 30:
                d["stopped_early"] = True
                break
    d.update(truth=truth, walked=total, correct=total == truth, steps=steps, worst_step_ms=round(worst, 1))

query(b, f"DROP DATABASE {S}")
r.finish()
