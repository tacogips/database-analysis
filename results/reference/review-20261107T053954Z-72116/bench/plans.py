#!/usr/bin/env python3
"""How indexes, foreign keys, predicates and statistics change the plan of an extraction query.

Each case shows the EXPLAIN plan and, where it is cheap, the actual chunk runtime. Indexes created here
are dropped again. Results go to results/plans.json.
"""
import json, sys, time
from lablib import connect, query, save


def plan_of(c, sql, args=None):
    p = json.loads(query(c, "EXPLAIN FORMAT=JSON " + sql, args)[0][0])["query_block"]
    op = p.get("ordering_operation", {})
    groups = p.get("grouping_operation", {})
    t = op.get("table") or groups.get("table") or p.get("table")
    if t is None and "nested_loop" in (op or p):
        loop = (op or p)["nested_loop"]
        return {"join_order": [(n["table"]["table_name"], n["table"].get("access_type"), n["table"].get("key"),
                                n["table"].get("using_join_buffer")) for n in loop],
                "filesort": op.get("using_filesort", False), "cost": p.get("cost_info", {}).get("query_cost")}
    if t is None:
        return {"raw": p}
    return {"access": t.get("access_type"), "key": t.get("key"), "rows_est": t.get("rows_examined_per_scan"),
            "covering": t.get("using_index", False), "filesort": op.get("using_filesort", False),
            "icp": t.get("index_condition") is not None, "cost": p.get("cost_info", {}).get("query_cost")}


def timed(c, sql, args=None):
    t = time.monotonic()
    r = query(c, sql, args)
    return {"rows": len(r), "ms": round((time.monotonic() - t) * 1000, 1)}


def case(out, name, c, sql, args=None, run=True, note=""):
    entry = {"sql": sql, "args": args, "note": note, "plan": plan_of(c, sql, args)}
    if run:
        entry["actual"] = timed(c, sql, args)
    out[name] = entry
    print(name, json.dumps({k: entry[k] for k in ("plan", "actual") if k in entry}, default=str), flush=True)


def main():
    c = connect(read_timeout=1800)
    query(c, "SET SESSION lock_wait_timeout=30")
    out = {}

    # 1. COUNT(*) uses the smallest usable index, so adding an FK (with its index) changes the plan.
    case(out, "count_child_with_fk_index", c, "SELECT COUNT(*) FROM order_items", note="FK created k_order, smallest index")
    case(out, "count_child_forced_pk", c, "SELECT COUNT(*) FROM order_items FORCE INDEX (PRIMARY)")
    case(out, "count_same_shape_no_fk", c, "SELECT COUNT(*) FROM order_items_nofk", note="No FK and no secondary index")

    # 2. Join between parent and child, with FK index vs without.
    join = "SELECT o.id,i.id FROM orders o JOIN {t} i ON i.order_id=o.id WHERE o.id BETWEEN 1000 AND 1100"
    case(out, "join_child_with_fk_index", c, join.format(t="order_items"))
    case(out, "join_child_no_fk_index", c, join.format(t="order_items_nofk"), run=False, note="Same SQL; no index because no FK")

    # 3. Keyset: predicate selectivity flips the chosen index when a second index exists.
    query(c, "ALTER TABLE t_hot ADD INDEX k_tmp_v (v)")
    q = "SELECT * FROM t_hot WHERE id > %s AND v < %s ORDER BY id LIMIT 5000"
    case(out, "keyset_filter_low_selectivity", c, q, (0, 900000), note="Non-selective: walks PK in order")
    case(out, "keyset_filter_high_selectivity", c, q, (0, 50), note="Selective: may switch to k_tmp_v plus filesort")
    case(out, "keyset_filter_forced_pk", c, "SELECT * FROM t_hot FORCE INDEX (PRIMARY) WHERE id > %s AND v < %s ORDER BY id LIMIT 5000", (0, 50),
         note="FORCE INDEX keeps the order but may walk the whole table to fill LIMIT")

    # 4. A filter that matches nothing in the remaining range makes a forced-PK chunk read everything left.
    case(out, "keyset_filter_rare_forced_pk", c, "SELECT * FROM t_hot FORCE INDEX (PRIMARY) WHERE id > %s AND v < 0 ORDER BY id LIMIT 5000", (0,),
         note="LIMIT never fills: one chunk becomes a full scan. EXPLAIN rows does not reveal it")
    query(c, "ALTER TABLE t_hot DROP INDEX k_tmp_v")

    # 5. Keyset query after the index it needs disappears.
    query(c, "ALTER TABLE t_events ADD INDEX k_tmp_cid (created_at, id)")
    q = "SELECT id FROM t_events WHERE (created_at,id) > (%s,%s) ORDER BY created_at,id LIMIT 5000"
    case(out, "tuple_keyset_with_index", c, q, ("2026-01-01 00:10:00", 0), note="Row constructor: not a range on the index")
    q_or = "SELECT id FROM t_events WHERE created_at > %s OR (created_at = %s AND id > %s) ORDER BY created_at,id LIMIT 5000"
    case(out, "or_expanded_keyset_with_index", c, q_or, ("2026-01-01 00:10:00", "2026-01-01 00:10:00", 0))
    query(c, "ALTER TABLE t_events DROP INDEX k_tmp_cid")
    case(out, "or_expanded_keyset_after_drop", c, q_or, ("2026-01-01 00:10:00", "2026-01-01 00:10:00", 0), note="InnoDB secondary keys include the PK, so k_created still works")
    query(c, "ALTER TABLE t_events DROP INDEX k_created")
    case(out, "or_expanded_keyset_no_time_index", c, q_or, ("2026-01-01 00:10:00", "2026-01-01 00:10:00", 0), note="No index: every chunk is a full scan and sort")
    try:
        query(c, "SELECT id FROM t_events FORCE INDEX (k_created) WHERE created_at > '2026-01-01' LIMIT 1")
        out["force_index_on_missing_index"] = {"error": None}
    except Exception as e:
        out["force_index_on_missing_index"] = {"error": str(e), "note": "Fails fast instead of silently scanning"}
    print("force_index_on_missing_index", out["force_index_on_missing_index"], flush=True)
    query(c, "ALTER TABLE t_events ADD INDEX k_created (created_at)")

    # 6. Type or collation mismatch on the parameter.
    case(out, "string_pk_string_param", c, "SELECT * FROM t_strpk WHERE code > %s ORDER BY code LIMIT 5000", ("Ab5",))
    case(out, "string_pk_numeric_param", c, "SELECT * FROM t_strpk WHERE code > %s ORDER BY code LIMIT 5000", (5,),
         note="Numeric comparison cannot use the string index, and gives a different result set")
    case(out, "string_pk_other_collation", c, "SELECT * FROM t_strpk WHERE code > CONVERT(%s USING utf8mb4) COLLATE utf8mb4_bin ORDER BY code LIMIT 5000", ("Ab5",),
         note="Explicit collation differs from the index collation")

    # 7. MIN / MAX cost, LIMIT with no matching key, partition pruning.
    case(out, "min_max_on_pk", c, "SELECT MIN(id),MAX(id) FROM t_dense")
    case(out, "partition_pruned", c, "SELECT * FROM t_part WHERE created_at >= '2026-03-01' AND created_at < '2026-04-01' AND id > 0 ORDER BY id LIMIT 5000")
    case(out, "partition_unpruned", c, "SELECT * FROM t_part WHERE id > 0 ORDER BY id LIMIT 5000")

    # 8. Deep OFFSET vs keyset at the same position: EXPLAIN looks identical.
    case(out, "offset_deep_50m", c, "SELECT * FROM t_dense ORDER BY id LIMIT 5000 OFFSET 50000000", note="EXPLAIN rows hides the 50M skipped rows")
    case(out, "keyset_at_50m", c, "SELECT * FROM t_dense WHERE id > 50000000 ORDER BY id LIMIT 5000")

    # 9. Stale statistics: estimate is the only thing EXPLAIN shows.
    est = query(c, "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='t_dense'")[0][0]
    t = time.monotonic()
    query(c, "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='t_dense'")
    out["table_rows_estimate"] = {"estimate": est, "truth": 100_000_000, "error_pct": round(abs(est - 1e8) / 1e8 * 100, 2),
                                  "ms": round((time.monotonic() - t) * 1000, 2)}
    print("table_rows_estimate", out["table_rows_estimate"], flush=True)
    save("plans", out)


if __name__ == "__main__":
    sys.exit(main())
