#!/usr/bin/env python3
"""Read-only preflight: inspect a table, EXPLAIN (never execute) the chunk query, recommend a method.

Everything here reads metadata only. EXPLAIN still takes a short shared metadata lock, so
lock_wait_timeout is lowered first to avoid queueing behind a pending DDL.

Usage: preflight.py <table> [--host primary] [--db bench] [--json]
"""
import argparse, json, sys
import pymysql
from lablib import connect, query

ROW_LOCK_ENGINES = {"InnoDB"}
LOB_TYPES = {"tinyblob", "blob", "mediumblob", "longblob", "tinytext", "text", "mediumtext", "longtext", "json"}


def indexes(conn, db, table):
    rows = query(conn, """SELECT INDEX_NAME,NON_UNIQUE,COLUMN_NAME,SEQ_IN_INDEX,NULLABLE,INDEX_TYPE,SUB_PART
                          FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
                          ORDER BY INDEX_NAME,SEQ_IN_INDEX""", (db, table))
    out = {}
    for name, nonuniq, col, _seq, nullable, itype, subpart in rows:
        i = out.setdefault(name, {"name": name, "unique": not nonuniq, "cols": [], "nullable": False,
                                  "type": itype, "prefix": False})
        i["cols"].append(col)
        i["nullable"] = i["nullable"] or nullable == "YES"
        i["prefix"] = i["prefix"] or subpart is not None
    return out


def pick_key(idx):
    """A keyset key must be unique, NOT NULL, full-column and ordered (BTREE)."""
    usable = [i for i in idx.values() if i["unique"] and not i["nullable"] and not i["prefix"] and i["type"] == "BTREE"]
    usable.sort(key=lambda i: (i["name"] != "PRIMARY", len(i["cols"])))
    return usable[0] if usable else None


def key_predicate(cols):
    """`a > ? OR (a = ? AND (b > ? OR (b = ? AND c > ?)))`. It uses every key part, so a chunk starts at the cursor.

    `(a,b) > (?,?)` and `a >= ? AND (a > ? OR b > ?)` do not: see results/shapes.json, composite_keyset_forms.
    """
    head = f"`{cols[0]}`"
    if len(cols) == 1:
        return f"{head} > %s"
    return f"{head} > %s OR ({head} = %s AND ({key_predicate(cols[1:])}))"


def key_args(values):
    """Parameters for key_predicate, in order."""
    values = list(values)
    if len(values) == 1:
        return values
    return [values[0], values[0]] + key_args(values[1:])


def chunk_sql(table, key, columns=None, n=5000):
    # Keys come first and t.* follows, so the key is returned even when it is an invisible column.
    cols = key["cols"]
    columns = columns or ",".join(f"`{c}`" for c in cols) + f",`{table}`.*"
    order = ",".join(f"`{c}`" for c in cols)
    return f"SELECT {columns} FROM `{table}` FORCE INDEX (`{key['name']}`) WHERE {key_predicate(cols)} ORDER BY {order} LIMIT {n}"


def explain(conn, sql, args, key_cols):
    """The plan passes only if it is a range on every key part with no sort. `type=range` alone is not enough:
    a range on just the first key part re-reads that whole value's rows on every chunk."""
    plan = json.loads(query(conn, "EXPLAIN FORMAT=JSON " + sql, args)[0][0])
    tab = plan["query_block"]
    order = tab.get("ordering_operation", {})
    filesort = order.get("using_filesort", False)
    temp = order.get("using_temporary_table", False)
    inner = order.get("table", tab.get("table", {}))
    used = inner.get("used_key_parts") or []
    return {"access_type": inner.get("access_type"), "key": inner.get("key"), "used_key_parts": used,
            "rows_examined_estimate": inner.get("rows_examined_per_scan"), "partitions": inner.get("partitions"),
            "filesort": filesort, "temporary": temp, "attached_condition": inner.get("attached_condition"),
            "ok": inner.get("access_type") == "range" and not filesort and not temp and len(used) >= len(key_cols)}


def probe_chunk(conn, sql, args, limit=5000):
    """Run one real chunk and count what it read. This is the check EXPLAIN cannot do. Cost is one chunk."""
    query(conn, "FLUSH STATUS")
    rows = query(conn, sql, args)
    stats = dict(query(conn, "SHOW SESSION STATUS WHERE Variable_name LIKE 'Handler_read%'"))
    read = sum(int(stats.get(k, 0)) for k in ("Handler_read_next", "Handler_read_prev", "Handler_read_rnd_next"))
    return {"rows_returned": len(rows), "index_rows_read": read,
            "ok": read <= max(len(rows), 1) * 2 + 10}


def fk_graph(conn, db, table):
    parents = query(conn, """SELECT DISTINCT REFERENCED_TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE
                             WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND REFERENCED_TABLE_NAME IS NOT NULL""", (db, table))
    children = query(conn, """SELECT DISTINCT TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE
                              WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME=%s""", (db, table))
    return [p[0] for p in parents], [c[0] for c in children]


def has_cycle(conn, db, start):
    seen, stack = set(), [start]
    while stack:
        cur = stack.pop()
        for p in fk_graph(conn, db, cur)[0]:
            if p == start:
                return True
            if p not in seen:
                seen.add(p)
                stack.append(p)
    return False


def analyze(conn, db, table):
    query(conn, "SET SESSION lock_wait_timeout=3")
    meta = query(conn, """SELECT ENGINE,TABLE_ROWS,AVG_ROW_LENGTH,DATA_LENGTH,INDEX_LENGTH,CREATE_OPTIONS
                          FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s""", (db, table))
    if not meta:
        raise SystemExit(f"no such table: {table}")
    engine, est_rows, avg_len, data_len, index_len, opts = meta[0]
    if engine is None:
        return {"table": table, "engine": None, "warnings": ["view: read its base tables instead"], "key": None, "plan": None,
                "estimated_rows": None, "recommendation": {"method": "NOT_A_TABLE", "reason": "A view has no key of its own and may join several tables"}}
    cols = query(conn, """SELECT COLUMN_NAME,DATA_TYPE,COLLATION_NAME FROM information_schema.COLUMNS
                          WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION""", (db, table))
    lob_cols = [c[0] for c in cols if c[1] in LOB_TYPES]
    idx = indexes(conn, db, table)
    key = pick_key(idx)
    parents, children = fk_graph(conn, db, table)
    parts = query(conn, """SELECT COUNT(*) FROM information_schema.PARTITIONS
                           WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND PARTITION_NAME IS NOT NULL""", (db, table))[0][0]
    report = {"table": table, "engine": engine, "estimated_rows": est_rows, "avg_row_bytes": avg_len,
              "data_bytes": data_len, "index_bytes": index_len, "lob_columns": lob_cols,
              "partitions": parts, "fk_parents": parents, "fk_children": children,
              "self_reference": table in parents, "fk_cycle": has_cycle(conn, db, table),
              "key": key, "indexes": list(idx.values()), "warnings": [], "plan": None}
    w = report["warnings"]

    if engine not in ROW_LOCK_ENGINES:
        w.append(f"{engine} has no MVCC: reads can block writes or see no consistent view")
    if parts:
        w.append("partitioned: check that the key is unique across partitions and consider per-partition chunks")
    if lob_cols:
        w.append(f"LOB columns {lob_cols}: size chunks by bytes and exclude columns you do not need")
    if report["self_reference"] or report["fk_cycle"]:
        w.append("self-reference or FK cycle: no parent-first load order; load with foreign_key_checks=0 and verify afterwards")
    if parents or children:
        w.append("FK family: rows copied at different times can be orphaned; use one snapshot or reconcile afterwards")

    if key:
        order = ",".join(f"`{c}`" for c in key["cols"])
        keycols = order
        first = query(conn, f"SELECT {keycols} FROM `{table}` FORCE INDEX (`{key['name']}`) ORDER BY {order} LIMIT 1")
        args = list(first[0]) if first else [0] * len(key["cols"])
        sql = chunk_sql(table, key)
        try:
            report["plan"] = {"sql": sql, **explain(conn, sql, key_args(args), key["cols"])}
        except pymysql.MySQLError as e:
            report["plan"] = {"sql": sql, "ok": False, "error": str(e)}

    report["recommendation"] = recommend(report)
    return report


def recommend(r):
    if r["engine"] != "InnoDB":
        return {"method": "OFFLOAD_ONLY", "reason": f"{r['engine']} reads are not MVCC. Read from a backup or replica, "
                "or take an agreed window. Do not chunk on the primary without measuring write stalls."}
    if not r["key"]:
        return {"method": "OFFLOAD_ONLY", "reason": "No unique NOT NULL full-column BTREE key: keyset cannot prove "
                "completeness. Read one scan on a backup or replica."}
    if not r["plan"] or not r["plan"].get("ok"):
        return {"method": "STOP_AND_REVIEW", "reason": f"Chunk query plan is not a clean range scan: {r['plan']}"}
    method = "KEYSET_CHUNKS"
    notes = []
    if r["key"]["name"] != "PRIMARY":
        notes.append("key is a secondary index; on InnoDB it is only clustered if there is no PK")
    if r["lob_columns"]:
        notes.append("use a byte budget per chunk")
    if r["partitions"]:
        notes.append("run per partition with PARTITION (pN) when the key has no global ordering need")
    if r["fk_parents"] or r["fk_children"]:
        notes.append("FK family: for a consistent set, read all tables from one snapshot on a backup or replica")
    return {"method": method, "reason": "unique ordered key and a clean range plan", "notes": notes}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("table")
    ap.add_argument("--host", default="primary")
    ap.add_argument("--db", default="bench")
    a = ap.parse_args()
    print(json.dumps(analyze(connect(a.host, db=a.db), a.db, a.table), ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    sys.exit(main())
