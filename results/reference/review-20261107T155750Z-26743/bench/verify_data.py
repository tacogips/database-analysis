#!/usr/bin/env python3
"""Check that the load produced exactly what every experiment assumes, and record it."""
import sys
from lablib import REPLICA, connect, query, save

EXPECTED = {"t_sparse": 10_000_000, "t_uuid": 5_000_000, "t_composite": 10_000_000, "t_nopk": 5_000_000,
            "t_uniq_notnull": 3_000_000, "t_uniq_nullable": 3_000_000, "customers": 1_000_000, "orders": 5_000_000,
            "order_items": 15_000_000, "tags": 1000, "customer_tags": 5_000_000, "categories": 1_000_000,
            "circ_a": 100_000, "circ_b": 100_000, "t_lob": 300_000, "t_part": 10_000_000, "t_strpk": 1_000_000,
            "t_events": 5_000_000, "t_myisam": 3_000_000, "t_memory": 500_000, "t_archive": 1_000_000,
            "order_items_nofk": 5_000_000, "t_hot": 5_000_000, "t_repl": 20_000_000, "t_dense": 100_000_000,
            "d_negative_pk": 1_000_000, "d_float_pk": 1_000_000, "d_decimal_pk": 1_000_000, "d_datetime_pk": 1_000_000,
            "d_timestamp_pk": 1_000_000, "d_enum_pk": 1_000_000, "d_desc_pk": 1_000_000, "d_snowflake": 1_000_000,
            "d_unsigned_max": 100_000, "d_str_bin": 500_000, "d_prefix_unique": 500_000, "d_gipk": 1_000_000, "d_noindex": 1_000_000,
            "d_compressed": 500_000, "d_json_virtual": 500_000, "d_fulltext": 200_000, "d_spatial": 200_000,
            "d_soft_delete": 2_000_000, "d_history": 1_000_000, "d_eav": 1_000_000, "d_polymorphic": 1_000_000,
            "d_triggered": 500_000, "fk_diamond_a": 100_000, "fk_diamond_b": 500_000, "fk_diamond_c": 500_000,
            "fk_diamond_d": 1_000_000, "fk_comp_parent": 500_000, "fk_comp_child": 1_000_000, "fk_uq_parent": 500_000,
            "fk_uq_child": 1_000_000, "fk_null_parent": 100_000, "fk_null_child": 500_000, "fk_cross_schema": 500_000}


def main():
    c = connect(read_timeout=3600)
    bad, out = [], {}
    for t, want in EXPECTED.items():
        got = query(c, f"SELECT COUNT(*) FROM `{t}`")[0][0]
        out[t] = {"expected": want, "actual": got, "method": "COUNT(*)"}
        # ARCHIVE cannot delete, and engine_archive_count appends to it during its workload.
        if got != want and not (t == "t_archive" and got > want):
            bad.append(t)
    sizes = query(c, "SELECT table_name,engine,table_rows,data_length,index_length FROM information_schema.tables WHERE table_schema='bench'")
    out["_sizes"] = [list(s) for s in sizes]
    out["_version"] = query(c, "SELECT VERSION()")[0][0]
    gtid = query(c, "SELECT @@GLOBAL.gtid_executed")[0][0]
    r = connect(REPLICA, read_timeout=3600)
    timed_out = query(r, "SELECT WAIT_FOR_EXECUTED_GTID_SET(%s, 3000)", (gtid,))[0][0]
    out["_replica_rows"] = query(r, "SELECT COUNT(*) FROM t_repl")[0][0]
    out["_replica_caught_up"] = timed_out == 0
    save("data_check", out)
    print("mismatched:", bad or "none", "| replica caught up:", out["_replica_caught_up"], "| replica t_repl:", out["_replica_rows"])
    return 1 if bad or not out["_replica_caught_up"] or out["_replica_rows"] != EXPECTED["t_repl"] else 0


if __name__ == "__main__":
    sys.exit(main())
