#!/usr/bin/env python3
"""Answers EXP-R5 (FIND-DRIFT-01): how far is a split sum from the true count under known committed changes?

A private 1M row table. A writer applies one kind of change at about 200/s and logs each committed change. The walk records its
cursor after every step. From the two logs a model predicts the sum: a change is reflected only if it is on the side of the cursor not yet
read. Quiet true counts are taken before and after, so the sum is compared to both and to the prediction.
"""
import random, threading, time
from countlib import forced_plan, session_setup, split_count
from lablib import connect, query
from reqlib import Results, builder, guard, reset_schema

ID = "23-EXP-R5"
guard(ID, 9)
r = Results(ID, ["EXP-R5", "FIND-DRIFT-01"], rows=1_000_000, width=1000, pause_ms=10, ops_per_s_target=200)
b = builder()
reset_schema(b, "req_drift")
N = 1_000_000
BASE = 10_000_000_000


def rebuild():
    query(b, "DROP TABLE IF EXISTS req_drift.t")
    query(b, "CREATE TABLE req_drift.t (id BIGINT PRIMARY KEY, k BIGINT NOT NULL, s INT NOT NULL, KEY k_k (k))")
    query(b, "INSERT INTO req_drift.t SELECT n*10, n*10, 0 FROM bench.seq1m")
    query(b, "ANALYZE TABLE req_drift.t")


def pos(index, id_, k):
    return (id_,) if index == "PRIMARY" else (k, id_)


def run_kind(kind, index, seed=7):
    rebuild()
    c = connect(db="req_drift", read_timeout=300)
    session_setup(c)
    plan = forced_plan(c, "req_drift", "t", index)
    n0 = query(b, "SELECT COUNT(*) FROM req_drift.t")[0][0]
    cursor_log, ops, cur = [], [], {"v": None}
    done = threading.Event()

    def on_step(_n, _ms, cursor):
        cursor_log.append((time.time(), cursor))
        cur["v"] = cursor

    def writer():
        w = connect(db="req_drift", read_timeout=60)
        rng = random.Random(seed)
        seq = 0
        while not done.is_set():
            seq += 1
            i = rng.randint(1, N) * 10  # an id that was in the table at the start (may be gone already)
            try:
                if kind == "insert_ahead":  # new keys beyond everything, as an auto-increment append
                    nid = BASE + seq * 10
                    query(w, "INSERT INTO t VALUES (%s,%s,0)", (nid, nid))
                    ops.append((time.time(), "ins", None, (nid, nid)))
                elif kind == "insert_behind":  # new keys just behind the cursor's current position
                    at = cur["v"][0] if cur["v"] else 10
                    nid = int(max(at - 5, 5) // 10 * 10 + rng.randint(1, 9)) + 0
                    if query(w, "SELECT 1 FROM t WHERE id=%s", (nid,)):
                        continue
                    query(w, "INSERT INTO t VALUES (%s,%s,0)", (nid, nid))
                    ops.append((time.time(), "ins", None, (nid, nid)))
                elif kind == "delete_uniform":
                    row = query(w, "SELECT id,k FROM t WHERE id=%s", (i,))
                    if row and query(w, "DELETE FROM t WHERE id=%s", (i,)) == []:
                        ops.append((time.time(), "del", tuple(row[0]), None))
                elif kind == "update_status":
                    query(w, "UPDATE t SET s=s+1 WHERE id=%s", (i,))
                    ops.append((time.time(), "same", (i, i), (i, i)))
                elif kind == "update_k_forward":  # updated_at = now(): the row moves to the end of the index
                    row = query(w, "SELECT id,k FROM t WHERE id=%s", (i,))
                    if row:
                        nk = BASE * 2 + seq
                        query(w, "UPDATE t SET k=%s WHERE id=%s", (nk, i))
                        ops.append((time.time(), "move", tuple(row[0]), (i, nk)))
                elif kind == "update_k_backward":  # the row moves before the start of the index
                    row = query(w, "SELECT id,k FROM t WHERE id=%s", (i,))
                    if row:
                        nk = -seq
                        query(w, "UPDATE t SET k=%s WHERE id=%s", (nk, i))
                        ops.append((time.time(), "move", tuple(row[0]), (i, nk)))
                elif kind == "update_pk":  # the primary key itself changes, and moves forward
                    row = query(w, "SELECT id,k FROM t WHERE id=%s", (i,))
                    if row:
                        nid = BASE * 3 + seq
                        query(w, "UPDATE t SET id=%s WHERE id=%s", (nid, i))
                        ops.append((time.time(), "move", tuple(row[0]), (nid, row[0][1])))
            except Exception as e:
                ops.append((time.time(), "error", repr(e)[:100], None))
            time.sleep(.005)
        w.close()

    th = threading.Thread(target=writer, daemon=True)
    th.start()
    time.sleep(.2)
    s = split_count(c, "t", plan, width=1000, limit_ms=2000, duty=None, pause_ms=10, on_step=on_step, enforce_plan=False)
    done.set()
    th.join()
    time.sleep(.2)
    n1 = query(b, "SELECT COUNT(*) FROM req_drift.t")[0][0]
    # Replay: a change counts only against the cursor position at the moment it committed.
    predicted, idx, last = n0, 0, None
    for t, op, old, new in ops:
        if op in ("error", "same"):
            continue
        while idx < len(cursor_log) and cursor_log[idx][0] <= t:
            last = cursor_log[idx][1]
            idx += 1
        ahead = lambda p: last is None or p > tuple(last)
        if op == "ins" and ahead(pos(index, *new)):
            predicted += 1
        elif op == "del" and ahead(pos(index, *old)):
            predicted -= 1
        elif op == "move":
            was, now = ahead(pos(index, *old)), ahead(pos(index, *new))
            predicted += (1 if now and not was else 0) - (1 if was and not now else 0)
    errs = sum(1 for o in ops if o[1] == "error")
    res = {"kind": kind, "index": index, "status": s["status"], "sum": s["rows"], "n_start": n0, "n_end": n1, "predicted": predicted,
           "committed_changes": len(ops) - errs, "writer_errors": errs, "first_writer_error": next((o[2] for o in ops if o[1] == "error"), None),
           "seconds": s["seconds_total"], "steps": s["steps"], "stmt_ms_max": s["stmt_ms_max"], "errors": s.get("errors")}
    if s["rows"] is not None:
        res.update(error_vs_start=s["rows"] - n0, error_vs_end=s["rows"] - n1, residual_vs_prediction=s["rows"] - predicted,
                   error_pct_vs_end=round(100 * (s["rows"] - n1) / max(n1, 1), 4),
                   error_per_change=round((s["rows"] - n1) / max(len(ops) - errs, 1), 3))
    c.close()
    return res


with r.section("runs") as d:
    d["runs"] = []
    for kind in ("update_status", "insert_ahead", "insert_behind", "delete_uniform", "update_k_forward", "update_k_backward", "update_pk"):
        for index in ("PRIMARY", "k_k"):
            if r.time_left() < 60:
                d["stopped_early"] = f"{kind}/{index}"
                break
            res = run_kind(kind, index)
            d["runs"].append(res)
            print(res, flush=True)
            r.save()
query(b, "DROP DATABASE req_drift")
r.finish()
