#!/usr/bin/env python3
"""Answers EXP-R4 (FIND-PK-01), second part: inline wide rows, where each PRIMARY entry is heavy. A private table about the size of the buffer pool.

Compares PRIMARY at a fixed width, PRIMARY with a time target, the small secondary index at a fixed width, and the inline-TEXT/JSON shape's own
statement time. Service traffic works on the same table. Records statement time, disk reads, latency and recovery.
"""
import time
from countlib import forced_plan, key_args, session_setup, split_count, split_statements
from lablib import connect, query
from reqlib import Results, builder, guard, measured_run, reset_schema

ID = "27-EXP-R4-wide"
guard(ID, 19)
ROWS = 500_000
r = Results(ID, ["EXP-R4", "FIND-PK-01"], rows=ROWS, row_bytes_about=2500, base_s=20, recover_s=20)
b = builder()
reset_schema(b, "req_wide")
SCHEMA = "req_wide"

with r.section("build") as d:
    query(b, f"""CREATE TABLE {SCHEMA}.w (id BIGINT PRIMARY KEY, k INT NOT NULL, v INT NOT NULL, body VARCHAR(4000) NOT NULL, doc JSON NOT NULL, KEY k_k (k))""")
    for off in range(0, ROWS, 50_000):
        query(b, f"INSERT INTO {SCHEMA}.w SELECT n+{off}, n+{off}, 0, REPEAT(MD5(n),75), JSON_OBJECT('m',MD5(n)) FROM bench.seq1m WHERE n<=50000")
    query(b, f"ANALYZE TABLE {SCHEMA}.w")
    d["sizes"] = [list(x) for x in query(b, "SELECT DATA_LENGTH,INDEX_LENGTH,AVG_ROW_LENGTH FROM information_schema.TABLES WHERE TABLE_SCHEMA='req_wide' AND TABLE_NAME='w'")]
    d["pages"] = {x[0]: int(x[1]) for x in query(b, "SELECT index_name,stat_value FROM mysql.innodb_index_stats WHERE database_name='req_wide' AND table_name='w' AND stat_name='size'")}

WL = dict(table=f"{SCHEMA}.w", max_id=ROWS, head=50_000, write_sql=f"UPDATE {SCHEMA}.w SET v=v+1 WHERE id=%s", read_sql=f"SELECT id,v FROM {SCHEMA}.w WHERE id=%s")


def walk(index, width, target_ms=None):
    def body(notes):
        c = connect(db=SCHEMA, read_timeout=300)
        session_setup(c, 0)
        plan = forced_plan(c, SCHEMA, "w", index)
        if target_ms is None:
            s = split_count(c, "w", plan, width=width, limit_ms=5000, duty=.5)
        else:
            # The time target in §8: start small, double while steps stay within target, halve when one goes over, one width per stretch of steps.
            # split_count takes a fixed width, so the adapting walk keeps its own cursor.
            w, cursor_rows, steps, worst, t0, trail, cursor = width, 0, 0, 0.0, time.monotonic(), [], None
            while True:
                st = split_statements("w", plan, w, 5000)
                q = time.monotonic()
                row = query(c, st["first" if cursor is None else "mid"], None if cursor is None else key_args(cursor))
                if not row:
                    n = query(c, st["tail_first" if cursor is None else "tail"], None if cursor is None else key_args(cursor))[0][0]
                    cursor_rows += n
                    break
                ms = (time.monotonic() - q) * 1000
                cursor = tuple(row[0])
                cursor_rows += w
                steps += 1
                worst = max(worst, ms)
                trail.append((w, round(ms, 1)))
                time.sleep(ms / 1000)
                if ms > target_ms:
                    w = max(w // 2, 100)
                elif ms < target_ms / 2:
                    w = min(w * 2, 1_000_000)
            s = {"status": "complete", "rows": cursor_rows, "steps": steps, "stmt_ms_max": round(worst, 1), "seconds_total": round(time.monotonic() - t0, 2), "width_end": w, "index": index, "trail_head": trail[:20], "trail_tail": trail[-5:]}
        notes.update({k: s.get(k) for k in ("status", "rows", "index", "steps", "stmt_ms_p50", "stmt_ms_p99", "stmt_ms_max", "seconds_total", "width_end", "errors", "trail_head", "trail_tail", "plan_check")})
        notes["correct"] = s["rows"] == ROWS
        c.close()
    return body


ARMS = {"primary_fixed_10k": walk("PRIMARY", 10_000), "primary_target_100ms": walk("PRIMARY", 1000, target_ms=100), "secondary_fixed_10k": walk("k_k", 10_000), "control": lambda n: time.sleep(30)}

with r.section("runs") as d:
    d["runs"] = []
    for rep in range(2):
        for name, body in ARMS.items():
            if r.time_left() < 150:
                d["stopped_early"] = f"{rep}/{name}"
                break
            res = measured_run(name, WL, body, 20, 20, table="w")
            res.update(arm=name, rep=rep)
            d["runs"].append(res)
            r.save()
            print(rep, name, res["method_seconds"], res["notes"].get("stmt_ms_max"), flush=True)

query(b, f"DROP DATABASE {SCHEMA}")
r.finish()
