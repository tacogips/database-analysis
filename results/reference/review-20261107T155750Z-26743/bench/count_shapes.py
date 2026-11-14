#!/usr/bin/env python3
"""Every table design: accuracy, plan, speed and read volume of each whole-table count, on a quiet server."""
import sys, time
from lablib import connect, query, save
from count_preflight import analyze
from countlib import choose_index, explain, walk_count

TABLES = ["t_sparse", "t_uuid", "t_composite", "t_nopk", "t_uniq_notnull", "t_uniq_nullable", "customers", "orders",
          "order_items", "order_items_nofk", "tags", "customer_tags", "categories", "circ_a", "circ_b", "t_lob", "t_part",
          "t_strpk", "t_events", "t_myisam", "t_memory", "t_archive", "t_hot", "t_repl",
          "d_negative_pk", "d_float_pk", "d_decimal_pk", "d_datetime_pk", "d_timestamp_pk", "d_enum_pk", "d_desc_pk",
          "d_snowflake", "d_unsigned_max", "d_str_bin", "d_prefix_unique", "d_gipk", "d_noindex", "d_compressed",
          "d_json_virtual", "d_fulltext", "d_spatial", "d_soft_delete", "d_history", "d_eav", "d_polymorphic", "d_triggered",
          "fk_diamond_a", "fk_diamond_b", "fk_diamond_c", "fk_diamond_d", "fk_comp_parent", "fk_comp_child",
          "fk_uq_parent", "fk_uq_child", "fk_null_parent", "fk_null_child", "fk_cross_schema", "v_orders_with_customer"]
READS = ("Handler_read_first", "Handler_read_key", "Handler_read_next", "Handler_read_rnd_next")


def bp_reads(c):
    return int(query(c, "SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_reads'")[0][1])


def timed_count(c, sql, setup=()):
    for s in setup:
        query(c, s)
    query(c, "FLUSH STATUS")
    before = bp_reads(c)
    t = time.monotonic()
    value = query(c, sql)[0][0]
    secs = time.monotonic() - t
    stats = dict(query(c, "SHOW SESSION STATUS WHERE Variable_name LIKE 'Handler_read%'"))
    return {"count": value, "seconds": round(secs, 3), "disk_page_reads": bp_reads(c) - before,
            "handler_reads": {k: int(stats[k]) for k in READS}}


def run(c, table):
    rep = analyze(c, "bench", table)
    out = {"preflight": rep, "methods": {},
           "scope": "quiet design catalog; non-InnoDB scans are not compliant nonlocking service candidates"}
    if rep["type"] == "VIEW":
        out["methods"]["view_count"] = timed_count(c, f"SELECT COUNT(*) FROM `{table}`")
        return out
    t = f"`{table}`"
    m = out["methods"]
    plain = f"SELECT COUNT(*) FROM {t}"
    query(c, "SET SESSION innodb_parallel_read_threads=DEFAULT")
    m["default_run1"] = timed_count(c, plain)
    m["default_run2"] = timed_count(c, plain)
    truth = m["default_run1"]["count"]
    m["single_thread"] = timed_count(c, plain, ["SET SESSION innodb_parallel_read_threads=1"])
    query(c, "SET SESSION innodb_parallel_read_threads=DEFAULT")
    idx = {i["name"]: i for i in rep.get("indexes", [])}
    if "PRIMARY" in idx and rep["engine"] == "InnoDB":
        m["force_primary"] = timed_count(c, f"SELECT COUNT(*) FROM {t} FORCE INDEX (PRIMARY)")
        m["force_primary"]["plan"] = explain(c, f"SELECT COUNT(*) FROM {t} FORCE INDEX (PRIMARY)")
    # The older walker's own choice, not the guideline's: this file measures every index shape, including the excluded ones.
    best = choose_index(c, "bench", table)[0]
    if best and best["name"] != "PRIMARY" and rep["engine"] == "InnoDB":
        m["force_smallest"] = timed_count(c, f"SELECT COUNT(*) FROM {t} FORCE INDEX (`{best['name']}`)")
        m["force_smallest"]["index"] = best["name"]
    if best:
        try:
            query(c, "SET SESSION innodb_parallel_read_threads=1")
            w = walk_count(c, "bench", table, best, idx)
            w["correct"] = w["rows"] == truth
            w["index"] = best["name"]
            m["split_walk_100k"] = w
        except Exception as e:
            m["split_walk_100k"] = {"failed": repr(e)[:300], "index": best["name"]}
        query(c, "SET SESSION innodb_parallel_read_threads=DEFAULT")
    if rep.get("engine") == "InnoDB" and table == "t_part":
        t0 = time.monotonic()
        total = sum(query(c, f"SELECT COUNT(*) FROM {t} PARTITION ({p})")[0][0] for p in ("p1", "p2", "p3", "p4", "p5", "p6"))
        m["sum_of_partitions"] = {"count": total, "seconds": round(time.monotonic() - t0, 3)}
    t0 = time.monotonic()
    est = query(c, "SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA='bench' AND TABLE_NAME=%s", (table,))[0][0] or 0
    m["estimate_table_rows"] = {"value": est, "ms": round((time.monotonic() - t0) * 1000, 2), "error_pct": round(abs(est - truth) * 100 / max(truth, 1), 2)}
    if "PRIMARY" in idx and len(idx["PRIMARY"]["cols"]) == 1:
        col = idx["PRIMARY"]["cols"][0]
        try:
            lo, hi = query(c, f"SELECT MIN(`{col}`),MAX(`{col}`) FROM {t}")[0]
            span = int(hi) - int(lo) + 1
            m["pk_span"] = {"value": span, "error_pct": round(abs(span - truth) * 100 / max(truth, 1), 2)}
        except Exception as e:
            m["pk_span"] = {"not_applicable": str(e)[:100]}
    return out


def composite_forms(c):
    """The same walk step deep inside the largest tenant (5M rows), written three ways."""
    forms = {"tuple": ("(tenant_id,seq) > (%s,%s)", (1, 9_000_000)),
             "and_or": ("tenant_id >= %s AND (tenant_id > %s OR seq > %s)", (1, 1, 9_000_000)),
             "or_expanded": ("tenant_id > %s OR (tenant_id = %s AND seq > %s)", (1, 1, 9_000_000))}
    out = {}
    for name, (where, args) in forms.items():
        sql = f"SELECT tenant_id,seq FROM t_composite WHERE {where} ORDER BY tenant_id,seq LIMIT 1 OFFSET 4999"
        query(c, "FLUSH STATUS")
        t = time.monotonic()
        query(c, sql, args)
        ms = round((time.monotonic() - t) * 1000, 1)
        out[name] = {"ms": ms, "handler_read_next": int(dict(query(c, "SHOW SESSION STATUS LIKE 'Handler_read_next'"))["Handler_read_next"]), "plan": explain(c, sql, args)}
    return out


def main():
    c = connect(read_timeout=3600)
    forms = composite_forms(c)
    save("count_shapes_forms", forms)
    print(forms, flush=True)
    res = {}
    for t in TABLES:
        s = time.time()
        res[t] = run(c, t)
        m = res[t]["methods"]
        print(f"{t:26s} {res[t]['preflight']['recommendation']['method']:24s} "
              + " ".join(f"{k}={v.get('seconds', v.get('error_pct', '-'))}" for k, v in m.items())
              + f" | split_ok={m.get('split_walk_100k', {}).get('correct')}", flush=True)
        save("count_shapes", res)


if __name__ == "__main__":
    sys.exit(main())
