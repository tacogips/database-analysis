#!/usr/bin/env python3
"""Is COUNT(*) really slower than COUNT(1) on a wide table, or is something else going on?

90-wide-count-forms always ran COUNT(*) first, right after loading the table: 12.9 s against 1.4 s for COUNT(1) on 60 columns
with no secondary index. Three explanations are separated here:
- position: every form takes every position in turn
- buffer pool state: each round starts by evicting the table, and pages in the pool are recorded
- flushing after the load: the table is measured once with its dirty pages still flushing, then only after they reach zero

90 recorded the same 12,800 Innodb_buffer_pool_reads for fast and slow runs, about 200 MB of a 2.9 GB table. That counter excludes
read-ahead, so this also records read-ahead and bytes actually read. The host file cache under the bind mount cannot be cleared
from here, so a difference that remains could still come from it.
"""
import time
from lablib import connect, query, save

ROWS = 3_000_000
NCOLS = 60
FORMS = {"count_star": "COUNT(*)", "count_1": "COUNT(1)", "count_id": "COUNT(id)", "count_notnull": "COUNT(notnull_col)"}


def build(c):
    query(c, "SET SESSION sql_log_bin=0")
    query(c, "DROP DATABASE IF EXISTS bench_wide2")
    query(c, "CREATE DATABASE bench_wide2")
    cols = ["id BIGINT NOT NULL PRIMARY KEY", "notnull_col INT NOT NULL"]
    vals = []
    for i in range(NCOLS - 2):
        cols.append(f"c{i} VARCHAR(24) NOT NULL" if i % 2 == 0 else f"c{i} INT NOT NULL")
        vals.append("LEFT(SHA2(n,256),24)" if i % 2 == 0 else "n")
    query(c, f"CREATE TABLE bench_wide2.w ({','.join(cols)}) ENGINE=InnoDB")
    for off in range(0, ROWS, 100_000):
        query(c, f"INSERT INTO bench_wide2.w SELECT n+{off}, n, {','.join(vals)} FROM bench.seq1m WHERE n<=100000")
    query(c, "ANALYZE TABLE bench_wide2.w")


STATUS = ("Innodb_buffer_pool_reads", "Innodb_buffer_pool_read_ahead", "Innodb_data_read", "Innodb_buffer_pool_read_requests")


def status(c):
    names = ",".join(f"'{n}'" for n in STATUS)
    return {k: int(v) for k, v in query(c, f"SELECT VARIABLE_NAME,VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME IN ({names})")}


def dirty(c):
    return int(query(c, "SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_pages_dirty'")[0][1])


def pages_in_pool(c):
    return query(c, """SELECT COUNT(*) FROM information_schema.INNODB_BUFFER_PAGE WHERE TABLE_NAME='`bench_wide2`.`w`'""")[0][0]


def timed(c, sql):
    before, d0 = status(c), dirty(c)
    t = time.monotonic()
    query(c, sql)
    secs = round(time.monotonic() - t, 3)
    after = status(c)
    return {"seconds": secs, "dirty_pages_at_start": d0, **{k: after[k] - before[k] for k in STATUS}}


def wait_clean(c, limit=600):
    t = time.monotonic()
    while dirty(c) > 200 and time.monotonic() - t < limit:
        time.sleep(2)
    return {"waited_seconds": round(time.monotonic() - t, 1), "dirty_pages": dirty(c)}


def main():
    c = connect(read_timeout=1800)
    build(c)
    # The same situation as 90: straight after the load, COUNT(*) then COUNT(1), repeated while pages are still being flushed.
    after_load = []
    for i in range(3):
        for name in ("count_star", "count_1"):
            r = {"repeat": i, "form": name, **timed(c, f"SELECT {FORMS[name]} FROM bench_wide2.w")}
            after_load.append(r)
            print("after_load", r, flush=True)
    save("requested_wide_form_order_after_load", after_load)
    print("wait_clean", wait_clean(c), flush=True)
    names = list(FORMS)
    rows = []
    try:
        for threads in ("DEFAULT", "1"):
            query(c, f"SET SESSION innodb_parallel_read_threads={threads}")
            for round_no in range(len(names) * 2):
                order = names[round_no % len(names):] + names[:round_no % len(names)]
                # Evict: about 2.1 GB of t_dense's clustered index against a 1.5 GB buffer pool.
                query(c, "SELECT COUNT(*) FROM bench.t_dense FORCE INDEX (PRIMARY) WHERE id <= 25000000")
                in_pool = pages_in_pool(c)
                for position, name in enumerate(order):
                    r = timed(c, f"SELECT {FORMS[name]} FROM bench_wide2.w")
                    rows.append({"threads": threads, "round": round_no, "position": position, "form": name, "pages_in_pool_after_evict": in_pool, **r})
                    print(threads, round_no, position, name, r["seconds"], "data_read_mb", round(r["Innodb_data_read"] / 1e6), "read_ahead", r["Innodb_buffer_pool_read_ahead"], "in_pool", in_pool, flush=True)
                save("requested_wide_form_order", rows)
    finally:
        query(c, "DROP DATABASE IF EXISTS bench_wide2")
    summary = {}
    for r in rows:
        summary.setdefault((r["threads"], r["form"], "first" if r["position"] == 0 else "later"), []).append(r["seconds"])
    for key in sorted(summary):
        v = sorted(summary[key])
        print(key, "median", v[len(v) // 2], "min", v[0], "max", v[-1], "n", len(v), flush=True)


if __name__ == "__main__":
    main()
