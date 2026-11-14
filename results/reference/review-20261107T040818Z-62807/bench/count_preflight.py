#!/usr/bin/env python3
"""Read-only: which whole-table count fits this table? Metadata, EXPLAIN and one key lookup for a real cursor; nothing is counted.

Follows the flow in docs/table-count-guideline.md: no size threshold, the eligibility table under SQL 3, and rule 2 of
SQL 4 checked with a real cursor value. EXPLAIN takes a short shared metadata lock, so SQL 0's lock_wait_timeout comes first.
Usage: count_preflight.py <table> [--host primary] [--db bench]
"""
import argparse, json, sys
from lablib import connect, query
from countlib import explain, hint, indexes, key_args, plan_check, split_plans, split_statements


def first_key(c, table, plan):
    """The first key entry, so the plan check sees a real value of the right type. Reads one index entry."""
    s = split_statements(table, plan, 1, 500)
    row = query(c, s["first"])
    return tuple(row[0]) if row else None


def analyze(c, db, table):
    query(c, "SET SESSION lock_wait_timeout = 3")
    meta = query(c, """SELECT TABLE_TYPE,ENGINE,TABLE_ROWS,DATA_LENGTH,INDEX_LENGTH,CREATE_OPTIONS FROM information_schema.TABLES
                       WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s""", (db, table))
    if not meta:
        raise SystemExit(f"no such table: {db}.{table}")
    ttype, engine, est, data, index, opts = meta[0]
    report = {"table": table, "type": ttype, "engine": engine, "estimated_rows": est, "data_bytes": data, "index_bytes": index,
              "create_options": opts}
    if ttype == "VIEW":
        report["recommendation"] = {"method": "DECIDE_TARGET", "why": "A view's count is a query over its base tables, and the base counts do not substitute for it. Decide what to count."}
        return report
    idx = indexes(c, db, table)
    report["indexes"] = list(idx.values())
    report["default_plan"] = explain(c, f"SELECT COUNT(*) FROM `{table}`")
    if engine != "InnoDB":
        stored = report["default_plan"]["select_tables_optimized_away"] or "optimized away" in str(report["default_plan"]["message"]).lower()
        report["recommendation"] = ({"method": "COUNT_STORED", "why": f"{engine} returned a stored count: the plan is 'Select tables optimized away'."} if stored else
                                    {"method": "COUNT_WITH_LIMIT_ELSE_UNSPLITTABLE", "why": f"{engine} scans under a table lock. Try SQL 2, then the 'cannot split' choices."})
        return report
    plans = split_plans(c, db, table)
    report["rejected_indexes"], report["index_pages"] = plans["rejected"], plans["pages"]
    best = plans["plans"][0] if plans["plans"] else None
    report["split_plan_choice"] = best
    report["best_index"] = idx.get(best["index"]) if best else None
    if best:
        key = first_key(c, table, best)
        if key is not None:
            args = key_args(key) if best["form"] == "A" else (key[0],)
            report["split_plan"] = plan_check(c, best, split_statements(table, best, 10_000, 500)["explain_mid"], args)
    partitioned = "partitioned" in (opts or "")
    if partitioned:
        method = "PARTITION_SUM_THEN_SPLIT" if best else "PARTITION_SUM_ELSE_UNSPLITTABLE"
    elif not best or ("split_plan" in report and not report["split_plan"]["ok"]):
        method = "COUNT_WITH_LIMIT_ELSE_UNSPLITTABLE"
    else:
        method = "COUNT_WITH_LIMIT_THEN_SPLIT"
    report["recommendation"] = {"method": method, "index": best["index"] if best else None, "form": best["form"] if best else None,
                                "why": "SQL 2 first; on a timeout follow the guideline's flow. No size threshold: reported sizes can be wrong.",
                                "first_statement": f"SELECT {hint(1000)}COUNT(*) FROM `{table}`"}
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("table")
    ap.add_argument("--host", default="primary")
    ap.add_argument("--db", default="bench")
    a = ap.parse_args()
    print(json.dumps(analyze(connect(a.host, db=a.db), a.db, a.table), ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    sys.exit(main())
