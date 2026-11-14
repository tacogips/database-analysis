#!/usr/bin/env python3
"""Run preflight and a complete extraction for every table shape, on a quiet server.

Correctness is measured against an independent COUNT and a full PK sum where possible.
Also runs the naive variants that are expected to lose rows, to measure how silently they fail.
"""
import json, sys, time
from lablib import connect, query, save
from preflight import analyze, chunk_sql, key_args, probe_chunk

# (table, row cap for a full extraction). The 100M table is covered by the impact experiments.
TABLES = ["t_sparse", "t_uuid", "t_composite", "t_nopk", "t_uniq_notnull", "t_uniq_nullable", "customers", "orders",
          "order_items", "tags", "customer_tags", "categories", "circ_a", "circ_b", "t_lob", "t_part", "t_strpk",
          "t_events", "t_myisam", "t_memory", "t_archive", "order_items_nofk", "t_hot", "t_repl", "t_dense",
          "d_negative_pk", "d_float_pk", "d_decimal_pk", "d_datetime_pk", "d_timestamp_pk", "d_enum_pk", "d_desc_pk",
          "d_snowflake", "d_unsigned_max", "d_str_bin", "d_prefix_unique", "d_gipk", "d_compressed", "d_json_virtual",
          "d_fulltext", "d_spatial", "d_soft_delete", "d_history", "d_eav", "d_polymorphic", "d_triggered",
          "fk_diamond_a", "fk_diamond_b", "fk_diamond_c", "fk_diamond_d", "fk_comp_parent", "fk_comp_child",
          "fk_uq_parent", "fk_uq_child", "fk_null_parent", "fk_null_child", "fk_cross_schema", "v_orders_with_customer"]
FULL_LIMIT = 40_000_000


SLOW_CHUNK_MS = 15_000


def extract(c, sql, key_idx, first, n=5000):
    """Generic keyset loop with a guard: a chunk this slow means the plan is wrong, so stop rather than continue."""
    last, rows, chunks, worst = first, 0, 0, 0
    t0 = time.monotonic()
    while True:
        t = time.monotonic()
        res = query(c, sql, key_args(last))
        ms = (time.monotonic() - t) * 1000
        worst = max(worst, ms)
        if not res:
            break
        rows += len(res)
        chunks += 1
        last = tuple(res[-1][i] for i in key_idx)
        if len(res) < n:
            break
        if ms > SLOW_CHUNK_MS:
            raise TimeoutError(f"chunk took {ms:.0f} ms at {last} after {rows} rows")
    return rows, chunks, round(worst, 1), round(time.monotonic() - t0, 1)


def run_table(c, table):
    rep = analyze(c, "bench", table)
    result = {"preflight": rep}
    key = rep["key"]
    est = rep["estimated_rows"] or 0
    if not key or est > FULL_LIMIT:
        result["extraction"] = {"skipped": "no safe key" if not key else "too large for this script"}
        return result
    truth = query(c, f"SELECT COUNT(*) FROM `{table}`")[0][0]
    key_idx = list(range(len(key["cols"])))  # chunk_sql returns the key columns first
    # Start below every value: read the first row explicitly so any key type works.
    order = ",".join(f"`{k}`" for k in key["cols"])
    head = query(c, f"SELECT {order} FROM `{table}` FORCE INDEX (`{key['name']}`) ORDER BY {order} LIMIT 1")
    if not head:
        result["extraction"] = {"empty": True}
        return result
    first = tuple(head[0][i] for i in key_idx)
    sql = chunk_sql(table, key)
    result["first_chunk_probe"] = probe_chunk(c, sql, key_args(first))
    try:
        rows, chunks, worst, secs = extract(c, sql, key_idx, first)
    except TimeoutError as e:
        result["extraction"] = {"aborted": str(e)}
        return result
    rows += 1  # the row used as the starting point
    result["extraction"] = {"truth": truth, "rows": rows, "complete": rows == truth, "chunks": chunks,
                            "max_chunk_ms": worst, "seconds": secs, "rows_per_s": round(rows / max(secs, .01))}
    return result


def rows_read(c, sql, args=None):
    query(c, "FLUSH STATUS")
    t = time.monotonic()
    got = query(c, sql, args)
    ms = round((time.monotonic() - t) * 1000, 1)
    stats = dict(query(c, "SHOW SESSION STATUS WHERE Variable_name LIKE 'Handler_read%'"))
    plan = json.loads(query(c, "EXPLAIN FORMAT=JSON " + sql, args)[0][0])["query_block"]
    table = plan.get("ordering_operation", {}).get("table") or plan.get("table", {})
    return {"rows": len(got), "ms": ms, "handler_read_next": int(stats["Handler_read_next"]),
            "explain_access": table.get("access_type"), "explain_key_parts": table.get("used_key_parts"),
            "explain_rows_estimate": table.get("rows_examined_per_scan")}


def composite_forms(c):
    """Same logical chunk, deep inside the largest tenant (5M rows), written three ways."""
    forms = {"tuple": ("(tenant_id,seq) > (%s,%s)", (1, 9_000_000)),
             "and_or": ("tenant_id >= %s AND (tenant_id > %s OR seq > %s)", (1, 1, 9_000_000)),
             "or_expanded": ("tenant_id > %s OR (tenant_id = %s AND seq > %s)", (1, 1, 9_000_000))}
    return {k: rows_read(c, f"SELECT tenant_id,seq FROM t_composite WHERE {w} ORDER BY tenant_id,seq LIMIT 5000", a) for k, (w, a) in forms.items()}


def naive_cases(c):
    out = {"composite_keyset_forms": composite_forms(c)}
    print("composite_keyset_forms", out["composite_keyset_forms"], flush=True)
    # A: keyset on a nullable unique key silently drops NULL rows.
    truth = query(c, "SELECT COUNT(*) FROM t_uniq_nullable")[0][0]
    got = query(c, "SELECT COUNT(*) FROM t_uniq_nullable WHERE code > -1")[0][0]
    out["nullable_unique_keyset"] = {"truth": truth, "rows_seen": got, "lost": truth - got}

    # B: keyset on a non-unique column loses the rest of a duplicate run at each chunk edge.
    truth = query(c, "SELECT COUNT(*) FROM t_events")[0][0]
    last, seen = "0000-01-01 00:00:00", 0
    while True:
        r = query(c, "SELECT created_at FROM t_events WHERE created_at > %s ORDER BY created_at LIMIT 5000", (last,))
        if not r:
            break
        seen += len(r)
        last = r[-1][0]
    out["non_unique_time_keyset"] = {"truth": truth, "rows_seen": seen, "lost": truth - seen}

    # B2: the fix: (created_at, id), written so that every chunk seeks to the cursor.
    last, seen = ("0000-01-01 00:00:00", 0), 0
    while True:
        r = query(c, "SELECT created_at,id FROM t_events WHERE created_at > %s OR (created_at = %s AND id > %s) ORDER BY created_at,id LIMIT 5000", key_args(last))
        if not r:
            break
        seen += len(r)
        last = r[-1]
    out["time_plus_pk_keyset"] = {"truth": truth, "rows_seen": seen, "lost": truth - seen}

    # C: no ORDER BY: optimizer may pick a smaller covering index, so "last id" is meaningless.
    plan = json.loads(query(c, "EXPLAIN FORMAT=JSON SELECT id FROM t_events WHERE id > 0 LIMIT 5000")[0][0])
    last, seen, loops = 0, 0, 0
    while loops < 2000:
        r = query(c, "SELECT id FROM t_events WHERE id > %s LIMIT 5000", (last,))
        if not r:
            break
        seen += len(r)
        last = r[-1][0]
        loops += 1
    out["no_order_by_keyset"] = {"truth": truth, "rows_seen": seen, "lost": truth - seen, "key": plan["query_block"].get("table", {}).get("key")}

    # D: no-PK table keyed by a non-unique column.
    truth = query(c, "SELECT COUNT(*) FROM t_nopk")[0][0]
    last, seen = -1, 0
    while True:
        r = query(c, "SELECT a FROM t_nopk WHERE a > %s ORDER BY a LIMIT 5000", (last,))
        if not r:
            break
        seen += len(r)
        last = r[-1][0]
    out["no_pk_non_unique_keyset"] = {"truth": truth, "rows_seen": seen, "lost": truth - seen}

    # E: partition table: id alone is not declared unique. Global tuple vs per-partition reads.
    t = time.monotonic()
    rows, _c, w, _s = extract(c, "SELECT id,created_at FROM t_part WHERE id > %s OR (id = %s AND created_at > %s) ORDER BY id,created_at LIMIT 5000", [0, 1], (0, "1000-01-01"))
    out["partition_global_tuple"] = {"rows": rows, "max_chunk_ms": w, "seconds": round(time.monotonic() - t, 1)}
    t = time.monotonic()
    total = worst = 0
    for p in ("p1", "p2", "p3", "p4", "p5", "p6"):
        rows, _c, w, _s = extract(c, f"SELECT id,created_at FROM t_part PARTITION ({p}) WHERE id > %s OR (id = %s AND created_at > %s) ORDER BY id,created_at LIMIT 5000", [0, 1], (0, "1000-01-01"))
        total, worst = total + rows, max(worst, w)
    out["partition_per_partition"] = {"rows": total, "max_chunk_ms": worst, "seconds": round(time.monotonic() - t, 1)}

    # F: gaps: fixed numeric ranges issue many empty queries.
    lo, hi = query(c, "SELECT MIN(id),MAX(id) FROM t_sparse")[0]
    width, ranges = 5000 * 97, 0
    probe = lo
    empties = 0
    t = time.monotonic()
    while probe <= hi and ranges < 20000:
        if not query(c, "SELECT 1 FROM t_sparse WHERE id > %s AND id <= %s LIMIT 1", (probe, probe + width)):
            empties += 1
        probe += width
        ranges += 1
    out["range_chunks_on_sparse"] = {"id_span": hi - lo, "ranges_needed_estimate": (hi - lo) // width, "ranges_tried": ranges, "empty_in_tried": empties, "seconds": round(time.monotonic() - t, 1)}

    # H: keyset that starts at 0 on a signed PK drops every negative id.
    truth = query(c, "SELECT COUNT(*) FROM d_negative_pk")[0][0]
    got = query(c, "SELECT COUNT(*) FROM d_negative_pk WHERE id > 0")[0][0]
    out["negative_pk_start_at_zero"] = {"truth": truth, "rows_seen": got, "lost": truth - got}

    # I: ENUM keyset. The index sorts z,a,m but a string parameter compares as text, a,m,z.
    def enum_walk(sql, first, to_args):
        last, seen, loops = first, 0, 0
        while loops < 1000:
            r = query(c, sql, to_args(last))
            if not r:
                break
            seen += len(r)
            last = r[-1]
            loops += 1
        return seen
    truth = query(c, "SELECT COUNT(*) FROM d_enum_pk")[0][0]
    # A text cursor compares by text (a<m<z) although the index stores declaration order (z,a,m).
    text = "SELECT status,id FROM d_enum_pk WHERE status > %s OR (status = %s AND id > %s) ORDER BY status,id LIMIT 5000"
    first = query(c, "SELECT status,id FROM d_enum_pk ORDER BY status,id LIMIT 1")[0]
    naive = enum_walk(text, first, key_args) + 1
    # A numeric cursor compares by index position, which matches the stored order.
    num = "SELECT status+0,id FROM d_enum_pk WHERE status > %s OR (status = %s AND id > %s) ORDER BY status,id LIMIT 5000"
    first_num = query(c, "SELECT status+0,id FROM d_enum_pk ORDER BY status,id LIMIT 1")[0]
    numeric = enum_walk(num, first_num, key_args) + 1
    out["enum_keyset_text_cursor"] = {"truth": truth, "rows_seen": naive, "lost": truth - naive}
    out["enum_keyset_numeric_cursor"] = {"truth": truth, "rows_seen": numeric, "lost": truth - numeric,
                                         **rows_read(c, num, key_args((1, 100)))}

    # J: DOUBLE PK: the client text form of a float may not round-trip to the same stored value.
    truth = query(c, "SELECT COUNT(*) FROM d_float_pk")[0][0]
    last, seen, dup_guard = -1.0, 0, 0
    while dup_guard < 400:
        r = query(c, "SELECT id FROM d_float_pk WHERE id > %s ORDER BY id LIMIT 5000", (last,))
        if not r:
            break
        seen += len(r)
        last = r[-1][0]
        dup_guard += 1
    out["float_pk_keyset"] = {"truth": truth, "rows_seen": seen, "difference": truth - seen}

    # K: soft delete filter in the chunk query: one chunk walks the whole deleted run to fill LIMIT.
    t = time.monotonic()
    r = query(c, "SELECT id FROM d_soft_delete FORCE INDEX (PRIMARY) WHERE id > 0 AND deleted_at IS NULL ORDER BY id LIMIT 5000")
    filtered_ms = round((time.monotonic() - t) * 1000, 1)
    t = time.monotonic()
    query(c, "SELECT id FROM d_soft_delete FORCE INDEX (PRIMARY) WHERE id > 0 ORDER BY id LIMIT 5000")
    plain_ms = round((time.monotonic() - t) * 1000, 1)
    out["soft_delete_filter_in_chunk"] = {"filtered_ms": filtered_ms, "unfiltered_ms": plain_ms, "rows": len(r)}

    # L: GIPK: SELECT * hides the key, so a keyset needs it named explicitly.
    star_cols = query(c, "SELECT * FROM d_gipk LIMIT 1")[0]
    named = query(c, "SELECT my_row_id, d_gipk.* FROM d_gipk ORDER BY my_row_id LIMIT 1")[0]
    out["gipk_select_star"] = {"columns_with_star": len(star_cols), "columns_named": len(named)}

    # M: TIMESTAMP PK: the same keyset value means a different instant if the session zone changes.
    query(c, "SET SESSION time_zone='+00:00'")
    ts = query(c, "SELECT id FROM d_timestamp_pk ORDER BY id LIMIT 1 OFFSET 500000")[0][0]
    a = query(c, "SELECT COUNT(*) FROM d_timestamp_pk WHERE id > %s", (ts,))[0][0]
    query(c, "SET SESSION time_zone='+09:00'")
    b = query(c, "SELECT COUNT(*) FROM d_timestamp_pk WHERE id > %s", (ts,))[0][0]
    query(c, "SET SESSION time_zone=SYSTEM")
    out["timestamp_pk_time_zone"] = {"rows_after_cursor_utc": a, "rows_after_same_text_jst": b, "difference": abs(a - b)}

    # G: LOB: same rows with and without the LOB column.
    for label, cols in (("with_lob", "*"), ("without_lob", "id,small_col")):
        t = time.monotonic()
        last, rows, nbytes = 0, 0, 0
        while True:
            r = query(c, f"SELECT {cols} FROM t_lob WHERE id > %s ORDER BY id LIMIT 500", (last,))
            if not r:
                break
            rows += len(r)
            last = r[-1][0]
            nbytes += sum(sum(len(str(v)) for v in row) for row in r[:5]) * (len(r) / 5)
            if rows >= 100_000:
                break
        secs = time.monotonic() - t
        out[f"lob_{label}"] = {"rows": rows, "seconds": round(secs, 2), "rows_per_s": round(rows / secs), "approx_mb_per_s": round(nbytes / secs / 1e6, 1)}
    return out


def main():
    c = connect(read_timeout=3600)
    out = {"tables": {}, "naive": None}
    for t in TABLES:
        s = time.time()
        out["tables"][t] = run_table(c, t)
        e = out["tables"][t].get("extraction", {})
        print(t, out["tables"][t]["preflight"]["recommendation"]["method"], e, f"{time.time() - s:.0f}s", flush=True)
    out["naive"] = naive_cases(c)
    print(json.dumps(out["naive"], indent=1, default=str), flush=True)
    save("shapes", out)


if __name__ == "__main__":
    sys.exit(main())
