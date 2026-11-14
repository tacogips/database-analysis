#!/usr/bin/env python3
"""Render results/impact_*.json into results/summary.md. Every number stays traceable to its file."""
import glob, json, os


def fmt(v):
    return "-" if v is None else str(v)


def main():
    rows = []
    for path in sorted(glob.glob("results/impact_*.json")):
        with open(path) as f:
            rows.append(json.load(f))
    lines = ["# Impact of whole-table counts", "",
             "Latency counts from the scheduled start under an open-loop workload (100 reads/s, 100 writes/s) in its own process and CPU; a sample is filed under its scheduled time. "
             "bl = baseline before the method, dur = during. Source: results/impact_<name>.json.", "",
             "| experiment | method rows/s | done | write p50 bl→dur ms | write p99 bl→dur ms | write max ms | write err+stuck | read p99 bl→dur ms | row lock waits | peak row locks | table-level / MDL excl / global lock | HLL start→peak | peak trx age s | MDL waiters | disk reads/s | replica lag s | notes |",
             "|" + "---|" * 17]
    for r in rows:
        lb, ld = r["latency"]["baseline"], r["latency"]["during"]
        sv = r["server"]["during"] or {}
        notes = dict(r.get("notes") or {})
        notes.pop("stderr_tail", None) if not notes.get("rc") else None
        note = "; ".join(f"{k}={str(v)[:70]}" for k, v in notes.items())
        if r.get("errors"):
            note += " ERR:" + str(r["errors"])[:80]
        lines.append("| " + " | ".join(map(str, [
            r["name"], r["method_rows_per_s"], "yes" if r["finished_within_window"] else "time-box",
            f"{fmt(lb['write']['p50_ms'])}→{fmt(ld['write']['p50_ms'])}",
            f"{fmt(lb['write']['p99_ms'])}→{fmt(ld['write']['p99_ms'])}", fmt(ld["write"]["max_ms"]), ld["write"]["errors"] + ld["write"].get("stuck_at_end", 0),
            f"{fmt(lb['read']['p99_ms'])}→{fmt(ld['read']['p99_ms'])}",
            fmt(sv.get("row_lock_waits")), fmt(sv.get("peak_row_locks_seen")),
            f"{fmt(sv.get('peak_table_level_locks'))} / {fmt(sv.get('peak_table_mdl_exclusive'))} / {fmt(sv.get('peak_global_read_lock'))}",
            f"{fmt(sv.get('hll_start'))}→{fmt(sv.get('hll_peak'))}", fmt(sv.get("peak_trx_age_s")), fmt(sv.get("peak_mdl_waiters")),
            fmt(sv.get("bp_disk_reads_per_s")), fmt(sv.get("peak_replica_lag_s")), note.replace("|", "/")])) + " |")
    os.makedirs("results", exist_ok=True)
    with open("results/summary.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"{len(rows)} experiments -> results/summary.md")


if __name__ == "__main__":
    main()
