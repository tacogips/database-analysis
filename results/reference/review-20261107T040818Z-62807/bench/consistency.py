#!/usr/bin/env python3
"""Measure what extraction loses under concurrent writes, and FK integrity of the result.

Part 1 (t_hot): chunked copy vs single snapshot under updates, deletes, inserts and PK moves.
Part 2 (customers/orders/order_items): orphan rows by read order under concurrent parent+child writes.
Part 3: load order with self-referencing and circular FKs.
"""
import random, sys, threading, time
from array import array
from lablib import connect, query, save

LO = 8_000_000  # ids used for moved and inserted rows, above the initial 5M


def writer(stop, stats):
    c = connect(read_timeout=60)
    next_new, moved_from = LO, []
    while not stop.is_set():
        r = random.random()
        i = random.randint(1, 5_000_000)
        try:
            if r < .6:
                with c.cursor() as k:
                    stats["updates"] += k.execute("UPDATE t_hot SET version=version+1 WHERE id=%s", (i,))
            elif r < .75:
                with c.cursor() as k:
                    stats["deletes"] += k.execute("DELETE FROM t_hot WHERE id=%s", (i,))
            elif r < .9:
                next_new += 1
                query(c, "INSERT INTO t_hot (id,version,v) VALUES (%s,0,0)", (next_new,))
                stats["inserts"] += 1
            else:
                # A PK update moves a row forward, past the reader, or backward, behind it.
                next_new += 1
                with c.cursor() as k:
                    stats["moves_forward"] += k.execute("UPDATE t_hot SET id=%s WHERE id=%s", (next_new, i))
        except Exception:
            pass
        stop.wait(.002)


def write_tag_setup(c):
    query(c, "DELETE FROM t_hot WHERE id > 5000000")
    query(c, "UPDATE t_hot SET version=0 WHERE version<>0")


ID_SPACE = 9_000_000  # versions are held in flat arrays: -1 means "row absent"


def blank():
    return array("q", [-1]) * ID_SPACE


def load_all(c, target):
    last = 0
    while True:
        rows = query(c, "SELECT id,version FROM t_hot WHERE id > %s ORDER BY id LIMIT 50000", (last,))
        if not rows:
            return
        for i, v in rows:
            target[i] = v
        last = rows[-1][0]


def copy_keyset(snapshot=False):
    """Returns (id->version array, duplicate rows). snapshot=True reads one REPEATABLE READ view."""
    c = connect(read_timeout=600, autocommit=not snapshot)
    if snapshot:
        query(c, "SET SESSION transaction_isolation='REPEATABLE-READ'")
        query(c, "START TRANSACTION WITH CONSISTENT SNAPSHOT")
    seen, dups, last = blank(), 0, 0
    while True:
        rows = query(c, "SELECT id,version FROM t_hot WHERE id > %s ORDER BY id LIMIT 20000", (last,))
        if not rows:
            break
        for i, v in rows:
            if seen[i] >= 0:
                dups += 1
            seen[i] = v
        last = rows[-1][0]
    if snapshot:
        query(c, "COMMIT")
    c.close()
    return seen, dups


def part1(snapshot):
    admin = connect(read_timeout=600)
    write_tag_setup(admin)
    stop, stats = threading.Event(), dict(updates=0, deletes=0, inserts=0, moves_forward=0)
    th = threading.Thread(target=writer, args=(stop, stats), daemon=True)
    th.start()
    time.sleep(2)
    # The reference view opens just before the copy, so "start state" means the state at copy start.
    ref = connect(read_timeout=600, autocommit=False)
    query(ref, "SET SESSION transaction_isolation='REPEATABLE-READ'")
    query(ref, "START TRANSACTION WITH CONSISTENT SNAPSHOT")
    t0 = time.time()
    seen, dups = copy_keyset(snapshot)
    secs = time.time() - t0
    stop.set()
    th.join()
    start = blank()
    load_all(ref, start)
    query(ref, "COMMIT")
    end = blank()
    load_all(admin, end)
    m = dict(missing_vs_start=0, extra_vs_start=0, version_differs_vs_start=0, alive_at_end_but_missing=0,
             stale_vs_end=0, deleted_by_end_but_copied=0, rows_copied=0)
    for i in range(ID_SPACE):
        a, b, e = seen[i], start[i], end[i]
        if a >= 0:
            m["rows_copied"] += 1
        if b >= 0 and a < 0:
            m["missing_vs_start"] += 1
        if a >= 0 and b < 0:
            m["extra_vs_start"] += 1
        if a >= 0 and b >= 0 and a != b:
            m["version_differs_vs_start"] += 1
        if e >= 0 and a < 0:
            m["alive_at_end_but_missing"] += 1
        if a >= 0 and e >= 0 and a != e:
            m["stale_vs_end"] += 1
        if a >= 0 and e < 0:
            m["deleted_by_end_but_copied"] += 1
    admin.close()
    return {"mode": "one_snapshot" if snapshot else "autocommit_chunks", "seconds": round(secs, 1), "writes": stats,
            "duplicate_rows_returned": dups, **m}


def bitmap(n):
    return bytearray(n // 8 + 1)


def bit_set(b, i):
    b[i >> 3] |= 1 << (i & 7)


def bit_get(b, i):
    return (b[i >> 3] >> (i & 7)) & 1


FK_MAX_ORDER = 8_000_000
FK_ORDERS = 2_000_000  # deletes target old orders so a reader can have passed them already


def fk_writer(stop, stats):
    c = connect(read_timeout=60)
    while not stop.is_set():
        try:
            # A new order and its items. Deleting an old order cascades to its items.
            with c.cursor() as k:
                k.execute("INSERT INTO orders (customer_id,total) VALUES (%s,1)", (random.randint(1, 1_000_000),))
                oid = k.lastrowid
                k.execute("INSERT INTO order_items (order_id,sku,qty) VALUES (%s,1,1)", (oid,))
                stats["new_orders"] += 1
                if random.random() < .2:
                    victim = random.randint(1, FK_ORDERS)
                    stats["deleted_orders"] += k.execute("DELETE FROM orders WHERE id=%s", (victim,))
        except Exception:
            pass
        stop.wait(.001)


def read_ids(c, table, col="id", limit=None):
    """Keyset over the PK, collecting one column."""
    last, rows = 0, array("Q")
    while True:
        res = query(c, f"SELECT id,{col} FROM {table} WHERE id > %s ORDER BY id LIMIT 20000", (last,))
        if not res:
            break
        last = res[-1][0]
        for r in res:
            rows.append(r[1])
        if limit and last > limit:
            break
    return rows


def part2(order, snapshot):
    admin = connect(read_timeout=600)
    stop, stats = threading.Event(), dict(new_orders=0, deleted_orders=0)
    th = threading.Thread(target=fk_writer, args=(stop, stats), daemon=True)
    th.start()
    time.sleep(2)
    c = connect(read_timeout=900, autocommit=not snapshot)
    if snapshot:
        query(c, "SET SESSION transaction_isolation='REPEATABLE-READ'")
        query(c, "START TRANSACTION WITH CONSISTENT SNAPSHOT")
    t0 = time.time()
    parents = bitmap(FK_MAX_ORDER)
    max_item_parent = array("Q")

    def read_parents():
        for i in read_ids(c, "orders", "id"):
            bit_set(parents, i)

    def read_children():
        max_item_parent.extend(read_ids(c, "order_items", "order_id"))

    for step in order:
        (read_parents if step == "orders" else read_children)()
        time.sleep(1)  # the gap between tables that any non-snapshot extraction has
    secs = time.time() - t0
    orphans = sum(1 for p in max_item_parent if not bit_get(parents, p))
    if snapshot:
        query(c, "COMMIT")
    stop.set()
    th.join()
    # Clean up what the writer added so later runs keep a stable table.
    query(admin, "DELETE FROM orders WHERE id > 5000000")
    admin.close()
    return {"read_order": order, "snapshot": snapshot, "seconds": round(secs, 1), "writes": stats, "items_read": len(max_item_parent), "orphan_child_rows": orphans}


def part3():
    """Can extraction order alone satisfy FKs during load? Runs on small samples in a scratch schema."""
    c = connect(read_timeout=300)
    query(c, "DROP DATABASE IF EXISTS fk_load")
    query(c, "CREATE DATABASE fk_load")
    query(c, "CREATE TABLE fk_load.categories LIKE bench.categories")
    query(c, "ALTER TABLE fk_load.categories ADD CONSTRAINT fk_l_p FOREIGN KEY (parent_id) REFERENCES fk_load.categories(id)")
    out = {}
    rows = query(c, "SELECT id,parent_id FROM bench.categories ORDER BY id LIMIT 100")
    try:
        for r in rows:
            query(c, "INSERT INTO fk_load.categories VALUES (%s,%s)", r)
        out["self_ref_pk_order_fk_on"] = "ok"
    except Exception as e:
        out["self_ref_pk_order_fk_on"] = f"failed: {e}"
    query(c, "TRUNCATE fk_load.categories")
    query(c, "SET SESSION foreign_key_checks=0")
    for r in rows:
        query(c, "INSERT INTO fk_load.categories VALUES (%s,%s)", r)
    query(c, "SET SESSION foreign_key_checks=1")
    out["self_ref_pk_order_fk_off"] = "ok"
    # FK checks off lets dangling rows in without an error; verify explicitly.
    out["dangling_after_fk_off"] = query(c, "SELECT COUNT(*) FROM fk_load.categories c LEFT JOIN fk_load.categories p ON c.parent_id=p.id WHERE c.parent_id IS NOT NULL AND p.id IS NULL")[0][0]
    out["circular_note"] = "circ_a/circ_b need FK checks off or NULL-then-UPDATE; no parent-first order exists"
    query(c, "DROP DATABASE fk_load")
    return out


def main():
    res = {"part1": [], "part2": []}
    for snap in (False, True):
        r = part1(snap)
        res["part1"].append(r)
        print(r, flush=True)
    for order, snap in ((["orders", "order_items"], False), (["order_items", "orders"], False), (["orders", "order_items"], True)):
        r = part2(order, snap)
        res["part2"].append(r)
        print(r, flush=True)
    res["part3"] = part3()
    print(res["part3"], flush=True)
    write_tag_setup(connect(read_timeout=600))
    save("consistency", res)


if __name__ == "__main__":
    sys.exit(main())
