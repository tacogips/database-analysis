"""Whole-table counting: find a usable index, then count it in short statements.

Two implementations live here. The first part (choose_index, walk_count) is the older walker that produced
results/count_shapes.json and results/impact_count_split_*.json; it stays so those results can be regenerated.
The second part (from SQL0 on) is the procedure docs/table-count-guideline.md prescribes, with its statements written
as the guideline writes them. New experiments and count_preflight.py use the second part.
"""
import json, re, threading, time
import pymysql
from lablib import connect, query


def indexes(c, db, table):
    rows = query(c, """SELECT INDEX_NAME,NON_UNIQUE,COLUMN_NAME,NULLABLE,INDEX_TYPE,SUB_PART,IS_VISIBLE
                       FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
                       ORDER BY INDEX_NAME,SEQ_IN_INDEX""", (db, table))
    out = {}
    for name, nonuniq, col, nullable, itype, sub, visible in rows:
        i = out.setdefault(name, {"name": name, "unique": not nonuniq, "cols": [], "nullable": False, "type": itype,
                                  "prefix": False, "visible": visible == "YES"})
        i["cols"].append(col)
        i["nullable"] = i["nullable"] or nullable == "YES"
        i["prefix"] = i["prefix"] or sub is not None
    return out


def index_pages(c, db, table):
    """Index sizes from persistent statistics. Needs SELECT on mysql.innodb_index_stats; returns {} without it."""
    try:
        return {n: int(v) for n, v in query(c, """SELECT index_name,stat_value FROM mysql.innodb_index_stats
                 WHERE database_name=%s AND table_name=%s AND stat_name='size'""", (db, table))}
    except Exception:
        return {}


def walkable(i):
    """Only a BTREE, visible, full-column index has an order to walk."""
    return i["type"] == "BTREE" and i["visible"] and not i["prefix"]


def choose_index(c, db, table):
    """The smallest walkable index, by persistent statistics when they are readable."""
    idx = indexes(c, db, table)
    pages = index_pages(c, db, table)
    usable = [i for i in idx.values() if walkable(i)]
    usable.sort(key=lambda i: (pages.get(i["name"], 1 << 60), i["name"] != "PRIMARY"))
    return usable[0] if usable else None, idx, pages


def walk_columns(c, db, table, index, idx):
    """Columns that make the walk unique. An InnoDB secondary index also stores the primary key in its order, so
    adding it gives a unique cursor even on a non-unique index. Without a primary key this returns unique=False."""
    cols = list(index["cols"])
    unique = index["unique"] and not index["nullable"]
    if not unique and "PRIMARY" in idx:
        cols += [k for k in idx["PRIMARY"]["cols"] if k not in cols]
        unique = True
    types = dict(query(c, "SELECT COLUMN_NAME,DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (db, table)))
    # ENUM and SET sort by stored number but compare as text with a string, so the cursor must be the number.
    exprs = [f"`{k}`+0" if types[k] in ("enum", "set") else f"`{k}`" for k in cols]
    return cols, exprs, unique


def key_predicate(cols):
    """`a > ? OR (a = ? AND b > ?)` seeks to the cursor on every key part. `(a,b) > (?,?)` scans the index from the
    start, and `a >= ? AND (a > ? OR b > ?)` scans all rows of a. See results/count_shapes_forms.json."""
    if len(cols) == 1:
        return f"`{cols[0]}` > %s"
    return f"`{cols[0]}` > %s OR (`{cols[0]}` = %s AND ({key_predicate(cols[1:])}))"


def key_args(values):
    values = list(values)
    return values if len(values) == 1 else [values[0], values[0]] + key_args(values[1:])


def walk_count(c, db, table, index, idx, width=100_000, duty=None, stop=None, on_chunk=None):
    """Each step asks the server for the entry `width` ahead, so no rows reach the client, and adds `width`.
    The last step counts what is left. Rows with NULL in a nullable index are not in the range, so they are counted first.
    Without a unique cursor (no primary key), ranges of values are counted instead."""
    cols, exprs, unique = walk_columns(c, db, table, index, idx)
    t = f"`{table}`"
    hint = f"FORCE INDEX (`{index['name']}`)"
    order = ",".join(f"`{k}`" for k in cols)
    select = ",".join(exprs)
    lead = f"`{cols[0]}`"
    nulls = query(c, f"SELECT COUNT(*) FROM {t} {hint} WHERE {lead} IS NULL")[0][0] if index["nullable"] else 0
    total, chunks, worst = 0, 0, 0.0
    t0 = time.monotonic()
    cursor = None

    def result(complete):
        return {"rows": total + nulls, "null_rows": nulls, "unique_cursor": unique, "cursor_columns": cols, "chunks": chunks,
                "max_chunk_ms": round(worst, 1), "seconds": round(time.monotonic() - t0, 2), "complete": complete}

    not_null = f"{lead} IS NOT NULL"
    while not (stop and stop.is_set()):
        q0 = time.monotonic()
        if unique:
            where = not_null if cursor is None else f"({key_predicate(cols)})"
            args = () if cursor is None else key_args(cursor)
            row = query(c, f"SELECT {select} FROM {t} {hint} WHERE {where} ORDER BY {order} LIMIT 1 OFFSET {width - 1}", args)
            n = width if row else query(c, f"SELECT COUNT(*) FROM {t} {hint} WHERE {where}", args)[0][0]
            cursor = row[0] if row else cursor
        else:
            where = not_null if cursor is None else f"{lead} > %s"
            args = () if cursor is None else (cursor[0],)
            row = query(c, f"SELECT {lead} FROM {t} {hint} WHERE {where} ORDER BY {lead} LIMIT 1 OFFSET {width - 1}", args)
            upper = f" AND {lead} <= %s" if row else ""
            n = query(c, f"SELECT COUNT(*) FROM {t} {hint} WHERE {where}{upper}", args + ((row[0][0],) if row else ()))[0][0]
            cursor = row[0] if row else cursor
        ms = (time.monotonic() - q0) * 1000
        worst = max(worst, ms)
        total += n
        chunks += 1
        if on_chunk:
            on_chunk(n, ms)
        if not row:
            return result(True)
        if duty:
            time.sleep(ms / 1000 * (1 / duty - 1))
    return result(False)


def explain(c, sql, args=None):
    raw = json.loads(query(c, "EXPLAIN FORMAT=JSON " + sql, args)[0][0])
    if "query_block" not in raw:
        # 9.7 and later default to JSON format version 2, which has a different shape. Read the version 1 shape.
        query(c, "SET SESSION explain_json_format_version=1")
        raw = json.loads(query(c, "EXPLAIN FORMAT=JSON " + sql, args)[0][0])
        query(c, "SET SESSION explain_json_format_version=DEFAULT")
    plan = raw["query_block"]
    tab = plan.get("ordering_operation", {}).get("table") or plan.get("table") or {}
    return {"access": tab.get("access_type"), "key": tab.get("key"), "used_key_parts": tab.get("used_key_parts"),
            "covering": tab.get("using_index", False), "rows_estimate": tab.get("rows_examined_per_scan"),
            "select_tables_optimized_away": plan.get("select_tables_optimized_away", False),
            "message": plan.get("message") or tab.get("message"), "raw_keys": sorted(plan.keys())}


# ---- The procedure in docs/table-count-guideline.md. Keep the statement text identical to the guideline. ----
SQL0 = ("SET SESSION autocommit = 1",
        "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ",
        "SET SESSION lock_wait_timeout = 3",
        "SET SESSION time_zone = '+00:00'",
        "SET NAMES utf8mb4",
        "SET SESSION max_execution_time = 1000")
SQL0_CHECK = ("SELECT VERSION(), @@server_uuid, @@read_only, @@autocommit, @@transaction_isolation, @@time_zone, "
              "@@character_set_results")
SQLM = ("SELECT ENABLED FROM performance_schema.setup_instruments WHERE NAME = 'wait/lock/metadata/sql/mdl'",
        "SELECT `count` FROM information_schema.INNODB_METRICS WHERE name = 'trx_rseg_history_len'",
        "SELECT COUNT(*) FROM information_schema.INNODB_TRX")
SQL3 = """SELECT s.INDEX_NAME, s.SEQ_IN_INDEX, s.COLUMN_NAME, s.COLLATION, s.NON_UNIQUE, s.NULLABLE,
       s.INDEX_TYPE, s.SUB_PART, s.IS_VISIBLE, s.EXPRESSION, c.DATA_TYPE, c.EXTRA
FROM information_schema.STATISTICS s
LEFT JOIN information_schema.COLUMNS c
  ON c.TABLE_SCHEMA = s.TABLE_SCHEMA AND c.TABLE_NAME = s.TABLE_NAME AND c.COLUMN_NAME = s.COLUMN_NAME
WHERE s.TABLE_SCHEMA = %s AND s.TABLE_NAME = %s ORDER BY s.INDEX_NAME, s.SEQ_IN_INDEX"""
SQL3_PAGES = """SELECT index_name, stat_value AS pages FROM mysql.innodb_index_stats
WHERE database_name = %s AND table_name = %s AND stat_name = 'size'"""
INTEGER_TYPES = {"tinyint", "smallint", "mediumint", "int", "bigint"}
CONNECTION_ERRORS = {0, 2006, 2013, 2014, 2055}


def session_setup(c, max_execution_ms=1000):
    """SQL 0. Returns what the server says the session now is."""
    for s in SQL0[:-1]:
        query(c, s)
    query(c, f"SET SESSION max_execution_time = {int(max_execution_ms)}")
    keys = ("version", "server_uuid", "read_only", "autocommit", "isolation", "time_zone", "character_set_results")
    return dict(zip(keys, query(c, SQL0_CHECK)[0]))


def monitor_ready(c):
    """SQL M. Raises when the monitor cannot be trusted; an empty result is not 'no waits'."""
    enabled = query(c, SQLM[0])
    if not enabled or enabled[0][0] != "YES":
        raise RuntimeError(f"MDL instrument not enabled: {enabled!r}")
    if not query(c, SQLM[1]):
        raise RuntimeError("trx_rseg_history_len unavailable")
    query(c, SQLM[2])
    return True


def _rejections(name, parts, cursor_parts, mutable):
    """Why an index cannot be walked, per the table under SQL 3. Applies to appended primary key columns too."""
    why = []
    if parts[0]["type"] != "BTREE":
        why.append("not_btree")
    if any(p["visible"] != "YES" for p in parts):
        why.append("invisible")
    if any(p["sub_part"] is not None for p in parts):
        why.append("prefix")
    if any(p["expression"] is not None or p["column"] is None for p in parts):
        why.append("expression_or_multivalued")
    if why:
        return why
    dirs = [p["collation"] for p in cursor_parts]
    if any(d != "A" for d in dirs) and not (name == "PRIMARY" and dirs == ["D"]):
        why.append("not_all_ascending")
    if any(p["nullable"] == "YES" for p in cursor_parts[1:]):
        why.append("nullable_non_leading_part")
    if any(p["data_type"] in ("enum", "set", "float", "bit") for p in cursor_parts):
        why.append("cursor_type_excluded")
    for p in cursor_parts:
        extra = (p["extra"] or "").lower()
        # DEFAULT_GENERATED only marks an expression default, so it does not make a column movable.
        if "on update" in extra or "virtual generated" in extra or "stored generated" in extra:
            why.append("column_moves:" + p["column"])
        if p["column"] in mutable:
            why.append("declared_mutable:" + p["column"])
    return why


def split_plans(c, db, table, mutable=(), stats_table=None):
    """SQL 3 as written. Returns {"plans": [best first], "rejected": {index: reasons}, "pages": {...}}."""
    groups = {}
    for name, _seq, col, coll, nonuniq, nullable, itype, sub, vis, expr, dtype, extra in query(c, SQL3, (db, table)):
        groups.setdefault(name, []).append({"column": col, "collation": coll, "non_unique": nonuniq, "nullable": nullable,
                                            "type": itype, "sub_part": sub, "visible": vis, "expression": expr,
                                            "data_type": (dtype or "").lower(), "extra": extra})
    try:
        pages = {n: int(v) for n, v in query(c, SQL3_PAGES, (db, stats_table or table))}
    except pymysql.MySQLError:
        pages = {}
    pk = groups.get("PRIMARY")
    plans, rejected = [], {}
    for name, parts in groups.items():
        cursor_parts = list(parts)
        have = {p["column"] for p in parts}
        unique = (parts[0]["non_unique"] == 0 and all(p["nullable"] != "YES" for p in parts)
                  and all(p["sub_part"] is None for p in parts))
        if not unique and pk:
            cursor_parts += [p for p in pk if p["column"] not in have]
        form = "A" if (unique or pk) else "B"
        why = _rejections(name, parts, cursor_parts, set(mutable))
        if form == "B":  # The cursor is the leading column alone; the table under SQL 3 still judges the whole index.
            cursor_parts = parts[:1]
        if why:
            rejected[name] = why
            continue
        plans.append({"index": name, "form": form, "cols": [p["column"] for p in cursor_parts],
                      "types": [p["data_type"] for p in cursor_parts], "lead_nullable": parts[0]["nullable"] == "YES",
                      "pages": pages.get(name)})
    plans.sort(key=lambda p: ("double" in p["types"], p["lead_nullable"],
                              p["pages"] if p["pages"] is not None else 1 << 60, p["index"] != "PRIMARY"))
    return {"plans": plans, "rejected": rejected, "pages": pages}


def forced_plan(c, db, table, index, cols=None):
    """A plan for one index regardless of its eligibility. Only experiments use this, to show what a rule prevents."""
    rows = query(c, SQL3, (db, table))
    parts = [r for r in rows if r[0] == index]
    pk = [r for r in rows if r[0] == "PRIMARY"]
    have = {r[2] for r in parts}
    unique = parts[0][4] == 0 and all(r[5] != "YES" for r in parts)
    use = parts if unique else parts + [r for r in pk if r[2] not in have]
    form = "A" if (unique or pk) else "B"
    if form == "B":
        use = parts[:1]
    return {"index": index, "form": form, "cols": cols or [r[2] for r in use], "types": [(r[10] or "").lower() for r in use],
            "lead_nullable": parts[0][5] == "YES", "pages": None}


def bq(name):
    return "`" + name.replace("`", "``") + "`"


def hint(ms):
    return f"/*+ MAX_EXECUTION_TIME({int(ms)}) */ " if ms else ""


def split_statements(table, plan, width, limit_ms=500, partition=None):
    """Every statement SQL 4 uses, as text with %s placeholders."""
    h = hint(limit_ms)
    src = bq(table) + (f" PARTITION ({bq(partition)})" if partition else "") + f" FORCE INDEX ({bq(plan['index'])})"
    cols = plan["cols"]
    lead = bq(cols[0])
    order = ", ".join(bq(k) for k in cols)
    off = int(width) - 1
    s = {"null_count": f"SELECT {h}COUNT(*) FROM {src} WHERE {lead} IS NULL",
         "tail_first": f"SELECT {h}COUNT(*) FROM {src} WHERE {lead} IS NOT NULL"}
    if plan["form"] == "A":
        pred = key_predicate(cols)
        s["first"] = f"SELECT {h}{order} FROM {src} WHERE {lead} IS NOT NULL ORDER BY {order} LIMIT 1 OFFSET {off}"
        s["mid"] = f"SELECT {h}{order} FROM {src} WHERE {pred} ORDER BY {order} LIMIT 1 OFFSET {off}"
        s["tail"] = f"SELECT {h}COUNT(*) FROM {src} WHERE {pred}"
        s["explain_mid"] = f"SELECT {order} FROM {src} WHERE {pred} ORDER BY {order} LIMIT 1 OFFSET {off}"
        eq = " AND ".join(f"{bq(k)} = %s" for k in cols)
    else:
        s["first"] = f"SELECT {h}{lead} FROM {src} WHERE {lead} IS NOT NULL ORDER BY {lead} LIMIT 1 OFFSET {off}"
        s["mid"] = f"SELECT {h}{lead} FROM {src} WHERE {lead} > %s ORDER BY {lead} LIMIT 1 OFFSET {off}"
        s["range_first"] = f"SELECT {h}COUNT(*) FROM {src} WHERE {lead} IS NOT NULL AND {lead} <= %s"
        s["range"] = f"SELECT {h}COUNT(*) FROM {src} WHERE {lead} > %s AND {lead} <= %s"
        s["tail"] = f"SELECT {h}COUNT(*) FROM {src} WHERE {lead} > %s"
        s["explain_mid"] = f"SELECT {lead} FROM {src} WHERE {lead} > %s ORDER BY {lead} LIMIT 1 OFFSET {off}"
        eq = f"{lead} = %s"
    s["roundtrip"] = f"SELECT {h}1 FROM {src} WHERE {eq} LIMIT 1"
    # The same check from the second step on: the value exists and sorts after the previous cursor by the column's own rules.
    s["roundtrip_advance"] = f"SELECT {h}1 FROM {src} WHERE {eq} AND ({key_predicate(cols if plan['form'] == 'A' else cols[:1])}) LIMIT 1"
    return s


def plan_check(c, plan, sql, args):
    """Rule 2 of SQL 4: EXPLAIN the walk with a real cursor. Returns the verdict and what it saw."""
    query(c, "SET SESSION explain_json_format_version = 1")
    raw = json.loads(query(c, "EXPLAIN FORMAT=JSON " + sql, args)[0][0])
    block = raw["query_block"]
    order = block.get("ordering_operation") or {}
    tab = order.get("table") or block.get("table") or {}
    used = tab.get("used_key_parts") or []
    verdict = {"access_type": tab.get("access_type"), "key": tab.get("key"), "used_key_parts": used,
               "using_filesort": bool(order.get("using_filesort")), "partitions": tab.get("partitions"),
               "rows_examined_per_scan": tab.get("rows_examined_per_scan")}
    verdict["ok"] = (verdict["access_type"] == "range" and not verdict["using_filesort"] and list(used) == list(plan["cols"]))
    return verdict


class _Stop(Exception):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


TIMER_COUNTER = "SHOW SESSION STATUS LIKE 'Max_execution_time_exceeded'"
PENDING_SQL = """SELECT COUNT(*) FROM performance_schema.metadata_locks
WHERE OBJECT_TYPE = 'TABLE' AND OBJECT_SCHEMA = %s AND OBJECT_NAME = %s AND LOCK_STATUS = 'PENDING'"""
# Waits that metadata_locks may not show. A metadata lock wait on another table is not in this list: only the counted table's PENDING row stops a job.
SERVER_WAIT_STATES = ("Waiting for table flush", "Waiting for tables", "Waiting for global read lock")
SERVER_WAITS_SQL = ("SELECT COUNT(*) FROM performance_schema.threads WHERE PROCESSLIST_STATE IN ("
                    + ", ".join(f"'{s}'" for s in SERVER_WAIT_STATES) + ")")


def split_count(c, table, plan, width=10_000, limit_ms=500, duty=0.5, pause_ms=0, stop=None, partition=None, check_roundtrip=True,
                enforce_plan=True, min_width=100, max_steps=None, on_step=None, before_statement=None, cursor_codec=None,
                keep_sql=False, db=None, expected_limit_ms=None, max_seconds=None, check_advance=True, pending_check=None,
                long_success_factor=2, max_rows=None):
    """SQL 4 with its rules. `rows` is meaningful only when status == 'complete'.

    The connection must already have had session_setup. limit_ms=0 issues the statements without a hint, which is what a
    proxy that strips comments would produce; pass expected_limit_ms so the long-success rule still applies. cursor_codec
    simulates a client that alters values. pending_check is a callable returning the PENDING count, for a job whose own
    account cannot read performance_schema. max_rows and max_seconds are the job's ceiling, as the guideline prescribes.
    max_steps is for experiments: halving the width raises the number of steps, so a step ceiling stops a healthy walk.
    """
    t_start = time.monotonic()
    checked_types = [t for t in plan["types"] if t not in INTEGER_TYPES and t != "decimal"]
    stmt_ms, by_kind, sql_seen = [], {}, {}
    out = {"table": table, "index": plan["index"], "form": plan["form"], "cursor_columns": plan["cols"], "width_start": width,
           "steps": 0, "null_rows": 0, "plan_check": None, "roundtrips": 0, "width_reductions": 0}
    total, cursor, w = 0, None, int(width)
    stm = split_statements(table, plan, w, limit_ms, partition)
    long_limit = expected_limit_ms or limit_ms

    def timer_counter():
        return int(query(c, TIMER_COUNTER)[0][1])

    try:
        timer = {"seen": timer_counter()}
    except (pymysql.MySQLError, OSError) as e:
        out.update(errors=[{"kind": "timer_counter", "errno": e.args[0] if e.args else 0, "msg": str(e)[:160]}])
        timer = {"seen": None}

    def finish(status):
        ordered = sorted(stmt_ms)
        pick = lambda p: round(ordered[min(len(ordered) - 1, int(len(ordered) * p))], 2) if ordered else None
        out.update(status=status, complete=status == "complete", rows=(total + out["null_rows"]) if status == "complete" else None,
                   partial_rows=total + out["null_rows"], statements=by_kind, stmt_ms_p50=pick(.5), stmt_ms_p99=pick(.99),
                   stmt_ms_max=round(ordered[-1], 2) if ordered else None, seconds_total=round(time.monotonic() - t_start, 3),
                   width_end=w, last_cursor=[str(v) for v in cursor] if cursor else None)
        if keep_sql:
            out["sql"] = sql_seen
        return out

    def classify(e, kind, ms):
        code = e.args[0] if e.args and isinstance(e.args[0], int) else 0
        out.setdefault("errors", []).append({"kind": kind, "errno": code, "ms": round(ms, 1), "msg": str(e)[:160]})
        if stop is not None and stop.is_set():
            return "stopped:stop_flag"
        if code in (1317, 3024):
            # Neither the number nor the elapsed time says why it ended. The session's timer counter does.
            try:
                now = timer_counter()
            except (pymysql.MySQLError, OSError):
                return "stopped:timer_counter_unreadable"
            fired = timer["seen"] is not None and now > timer["seen"]
            timer["seen"] = now
            if not fired:
                return "stopped:killed_not_timer"
            # A timeout can be an MDL wait in disguise. Halving would not help, and the job should get out of the way.
            try:
                waiting = pending_check() if pending_check else query(c, PENDING_SQL, (db or query(c, "SELECT DATABASE()")[0][0], table))[0][0]
            except Exception:
                return "stopped:pending_check_failed"
            return "stopped:mdl_pending" if waiting else "timer"
        if code == 1205:
            return "stopped:1205"
        if code in CONNECTION_ERRORS or isinstance(e, (pymysql.err.InterfaceError, OSError)):
            return "stopped:connection_lost"
        return f"stopped:error_{code}"

    def run_stmt(kind, sql, args=None):
        if stop is not None and stop.is_set():
            raise _Stop("stopped:stop_flag")
        if before_statement:
            before_statement(kind, sql, args)
        if keep_sql:
            sql_seen.setdefault(kind, sql)
        t = time.monotonic()
        try:
            rows = query(c, sql, args)
        except (pymysql.MySQLError, OSError) as e:
            raise _Stop(classify(e, kind, (time.monotonic() - t) * 1000))
        ms = (time.monotonic() - t) * 1000
        stmt_ms.append(ms)
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if long_limit and ms > long_success_factor * long_limit:
            # It succeeded far past its limit, so neither the hint nor the session limit is in force.
            out["long_success_ms"] = round(ms, 1)
            raise _Stop("stopped:long_success")
        return rows

    def check_plan(args):
        t = time.monotonic()
        try:
            out["plan_check"] = plan_check(c, plan, stm["explain_mid"], args)
        except (pymysql.MySQLError, OSError) as e:
            raise _Stop(classify(e, "explain", (time.monotonic() - t) * 1000))
        return out["plan_check"]["ok"] or not enforce_plan

    try:
        if plan["lead_nullable"]:
            try:
                out["null_rows"] = run_stmt("null_count", stm["null_count"])[0][0]
            except _Stop as e:
                return finish("unsplittable:timer_null_count" if e.status == "timer" else e.status)
        while True:
            if max_seconds and time.monotonic() - t_start > max_seconds:
                return finish("stopped:max_seconds")
            q0 = time.monotonic()
            sent = cursor if cursor_codec is None or cursor is None else tuple(cursor_codec(v) for v in cursor)
            args = None if cursor is None else (key_args(sent) if plan["form"] == "A" else (sent[0],))
            kind = "first" if cursor is None else "mid"
            try:
                if cursor is not None and out["plan_check"] is None and not check_plan(args):
                    return finish("rejected:plan")
                row = run_stmt(kind, stm[kind], args)
                if not row:
                    n = run_stmt("tail", stm["tail_first" if cursor is None else "tail"], args)[0][0]
                elif plan["form"] == "A":
                    n = w
                elif row[0][0] is None:
                    return finish("stopped:null_cursor")
                elif cursor is None:
                    n = run_stmt("range", stm["range_first"], (row[0][0],))[0][0]
                else:
                    n = run_stmt("range", stm["range"], (sent[0], row[0][0]))[0][0]
            except _Stop as e:
                if e.status != "timer":
                    return finish(e.status)
                w //= 2
                out["width_reductions"] += 1
                if w < min_width:
                    return finish("unsplittable:timer_min_width")
                stm = split_statements(table, plan, w, limit_ms, partition)
                continue
            if not row:
                total += n
                out["steps"] += 1
                if on_step:
                    on_step(n, (time.monotonic() - q0) * 1000, cursor)
                return finish("complete")
            new = tuple(row[0])
            if any(v is None for v in new):
                return finish("stopped:null_cursor")
            if new == cursor:
                return finish("stopped:no_progress")
            # Numbers compare the same here as on the server. Other keys are compared by the server, in the round trip below.
            if check_advance and cursor is not None and not checked_types and new <= cursor:
                return finish("stopped:cursor_not_advancing")
            if check_roundtrip and checked_types:
                out["roundtrips"] += 1
                try:
                    if cursor is None or not check_advance:
                        found = run_stmt("roundtrip", stm["roundtrip"], new)
                    else:
                        found = run_stmt("roundtrip", stm["roundtrip_advance"], tuple(new) + tuple(key_args(cursor) if plan["form"] == "A" else (cursor[0],)))
                    if not found:
                        return finish("stopped:roundtrip_failed")
                except _Stop as e:
                    return finish("stopped:timer_roundtrip" if e.status == "timer" else e.status)
            cursor = new
            total += n
            out["steps"] += 1
            ms = (time.monotonic() - q0) * 1000
            if on_step:
                on_step(n, ms, cursor)
            if max_rows and total >= max_rows:
                return finish("stopped:max_rows")
            if max_steps and out["steps"] >= max_steps:
                return finish("stopped:max_steps")
            wait = (ms / 1000 * (1 / duty - 1) if duty else 0) + pause_ms / 1000
            if wait:
                time.sleep(wait)
    except _Stop as e:
        return finish(e.status)


class CountMonitor(threading.Thread):
    """The separate monitor of §8: raises the shared stop flag first, then sends KILL QUERY. Losing the monitor stops the job too.

    `ready` is set once SQL M has passed. Each cycle re-reads the instrument, because a metadata_locks table that has gone
    quiet looks exactly like one with no waits. recheck_instrument=False reproduces the earlier monitor, for comparison only.
    max_hll stops the job when the history list passes that length, which also covers the job's own read view.
    """

    def __init__(self, host, db, table, job_thread_id, stop, interval=.2, connect_fn=None, recheck_instrument=True, max_hll=None):
        super().__init__(daemon=True)
        self.host, self.db, self.table, self.job, self.flag, self.interval = host, db, table, job_thread_id, stop, interval
        self.connect_fn = connect_fn or (lambda: connect(host, read_timeout=10))
        self.recheck_instrument, self.max_hll = recheck_instrument, max_hll
        self.reason, self.reason_at, self.kill_result, self.conn = None, None, None, None
        self.done, self.ready = threading.Event(), threading.Event()
        self.cycles = 0

    def _stop_job(self, reason):
        self.reason, self.reason_at = reason, time.time()
        self.flag.set()
        try:
            k = self.connect_fn()
            query(k, f"KILL QUERY {int(self.job)}")
            k.close()
            self.kill_result = "sent"
        except Exception as e:
            self.kill_result = repr(e)[:160]

    def run(self):
        try:
            self.conn = self.connect_fn()
            monitor_ready(self.conn)
        except Exception as e:
            self._stop_job("monitor_not_ready:" + repr(e)[:120])
            self.ready.set()
            return
        self.ready.set()
        while not self.done.is_set() and not self.flag.is_set():
            self.cycles += 1
            try:
                if self.recheck_instrument:
                    enabled = query(self.conn, SQLM[0])
                    if not enabled or enabled[0][0] != "YES":
                        self._stop_job("monitor_lost:instrument_disabled")
                        return
                hll = query(self.conn, SQLM[1])
                if query(self.conn, PENDING_SQL, (self.db, self.table))[0][0]:
                    self._stop_job("mdl_pending")
                elif query(self.conn, SERVER_WAITS_SQL)[0][0]:
                    self._stop_job("server_wide_wait")
                elif query(self.conn, "SELECT COUNT(*) FROM information_schema.INNODB_TRX WHERE trx_mysql_thread_id=%s AND trx_rows_locked>1000", (self.job,))[0][0]:
                    self._stop_job("job_holds_row_locks")
                elif self.max_hll and hll and hll[0][0] > self.max_hll:
                    self._stop_job("hll_over_limit")
            except Exception as e:
                self._stop_job("monitor_lost:" + repr(e)[:120])
                return
            self.done.wait(self.interval)

    def finish(self):
        self.done.set()
        self.join(5)
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass


def replica_channels(c):
    """Every row of SHOW REPLICA STATUS, one per channel. The first row says nothing about the others."""
    with c.cursor(pymysql.cursors.DictCursor) as dc:
        dc.execute("SHOW REPLICA STATUS")
        return dc.fetchall() or []


def restore_applier(host, stop_failed=False, deadline_seconds=30.0, poll=.25, settle=.5):
    """Brings back an applier this job asked to stop, on a connection of its own, and reports what it found.

    A STOP that failed stays in force and takes effect later, so 'running' proves nothing until it has been seen stopped.
    After a failed STOP this returns restored=True only once it has seen the stop land and started the applier again.
    At the deadline it returns restored=False with unresolved_stop=True: a late stop can still land, so whoever holds
    that result must keep watching (call this again) or alert someone. It never reports success on 'still running'."""
    t0 = time.monotonic()
    out = {"stop_failed": stop_failed, "starts_issued": 0, "saw_stopped": False, "restored": False, "unresolved_stop": False, "last_state": None}
    running_since = None
    try:
        own = connect(host, read_timeout=30)
        while True:
            rows = replica_channels(own)
            out["last_state"] = rows[0].get("Replica_SQL_Running") if rows else None
            if out["last_state"] != "Yes":
                out["saw_stopped"] = True
                running_since = None
                query(own, "START REPLICA SQL_THREAD FOR CHANNEL ''")
                out["starts_issued"] += 1
            elif not stop_failed or out["saw_stopped"]:
                running_since = running_since or time.monotonic()
                if time.monotonic() - running_since >= settle:
                    out["restored"] = True
                    break
            if time.monotonic() - t0 > deadline_seconds:
                out["unresolved_stop"] = stop_failed and not out["saw_stopped"]
                break
            time.sleep(poll)
        own.close()
    except (pymysql.MySQLError, OSError) as e:
        out["error"] = {"errno": e.args[0] if e.args else 0, "msg": str(e)[:160]}
        out["unresolved_stop"] = stop_failed and not out["saw_stopped"]
    out["seconds"] = round(time.monotonic() - t0, 2)
    return out


def replica_point_count(replica, table, max_execution_ms=60_000, kill_hook=None, stop_failure_grace=60):
    """The replica procedure in §7, for one ordinary channel only. It refuses anything else before changing anything.
    stop_failure_grace is how long it watches for a failed STOP to land before reporting the applier as not confirmed.

    The intent to stop is recorded before STOP is sent, so a STOP that fails, times out or loses its connection still reaches
    the restore. An applier that was already stopped is left stopped. `rows` is None when the count did not complete."""
    channels = replica_channels(replica)
    names = [r.get("Channel_Name") for r in channels]
    out = {"channels": names, "stop_requested": False, "stopped_by_us": False, "rows": None}
    if len(channels) != 1 or names[0] != "":
        out["status"] = "refused:not_one_default_channel"
        return out
    before = channels[0].get("Replica_SQL_Running")
    out["sql_thread_before"] = before
    stop_failed = False
    try:
        if before == "Yes":
            out["stop_requested"] = True
            try:
                query(replica, "STOP REPLICA SQL_THREAD FOR CHANNEL ''")
            except (pymysql.MySQLError, OSError):
                stop_failed = True
                raise
            out["stopped_by_us"] = True
        out["gtid_at_stop"] = query(replica, "SELECT @@GLOBAL.gtid_executed")[0][0]
        if kill_hook:
            kill_hook()
        out["rows"] = query(replica, f"SELECT /*+ MAX_EXECUTION_TIME({int(max_execution_ms)}) */ COUNT(*) FROM {bq(table)}")[0][0]
    except (pymysql.MySQLError, OSError) as e:
        out["error"] = {"errno": e.args[0] if e.args else 0, "msg": str(e)[:160], "during_stop": stop_failed}
    finally:
        if out["stop_requested"]:
            out["restore"] = restore_applier(replica.host, stop_failed=stop_failed, deadline_seconds=stop_failure_grace if stop_failed else 30.0)
    out["status"] = "complete" if out["rows"] is not None else "failed"
    if out.get("restore") and not out["restore"]["restored"]:
        out["status"] += ":applier_not_confirmed"  # alert: the job must not report this replica as back in service
    return out


def enum_labels(c, db, table, column):
    """The labels in declared order, from COLUMN_TYPE."""
    ctype = query(c, "SELECT COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s", (db, table, column))[0][0]
    if isinstance(ctype, bytes):
        ctype = ctype.decode()
    return [m.replace("''", "'") for m in re.findall(r"'((?:[^']|'')*)'", ctype)]


def enum_split_count(c, db, table, lead, rest, width=10_000, limit_ms=500, max_steps=None, check_zero=True):
    """The value-by-value walk for a primary key that starts with an ENUM. check_zero=False reproduces the walk without its guard.

    An invalid value stored by a non-strict session has internal number 0, belongs to no label, and sorts first in the index.
    So the first index entry shows whether any exist. A table with any is refused rather than counted short."""
    h, order, src = hint(limit_ms), ", ".join(bq(k) for k in rest), f"{bq(table)} FORCE INDEX (`PRIMARY`)"
    out = {"status": None, "rows": None, "steps": 0, "labels": enum_labels(c, db, table, lead), "per_label": {}}
    first = query(c, f"SELECT {h}{bq(lead)}+0 FROM {src} ORDER BY {bq(lead)}, {order} LIMIT 1")
    out["first_entry_number"] = int(first[0][0]) if first else None
    if not first:
        out.update(status="complete", rows=0)
        return out
    if check_zero and out["first_entry_number"] == 0:
        out["status"] = "refused:enum_index_zero"
        return out
    total = 0
    for label in out["labels"]:
        cursor, here = None, 0
        while True:
            where, args = f"{bq(lead)} = %s", [label]
            if cursor is not None:
                where += f" AND ({key_predicate(rest)})"
                args += key_args(cursor)
            row = query(c, f"SELECT {h}{order} FROM {src} WHERE {where} ORDER BY {order} LIMIT 1 OFFSET {int(width) - 1}", args)
            out["steps"] += 1
            if max_steps and out["steps"] > max_steps:
                out["status"] = "stopped:max_steps"
                return out
            if not row:
                here += query(c, f"SELECT {h}COUNT(*) FROM {src} WHERE {where}", args)[0][0]
                break
            cursor = tuple(row[0])
            here += int(width)
        out["per_label"][label] = here
        total += here
    out.update(status="complete", rows=total)
    return out
