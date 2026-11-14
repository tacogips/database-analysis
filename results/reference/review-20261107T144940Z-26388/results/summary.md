# Impact of whole-table counts

Latency counts from the scheduled start under an open-loop workload (100 reads/s, 100 writes/s) in its own process and CPU; a sample is filed under its scheduled time. bl = baseline before the method, dur = during. Source: results/impact_<name>.json.

| experiment | method rows/s | done | write p50 bl→dur ms | write p99 bl→dur ms | write max ms | write err+stuck | read p99 bl→dur ms | row lock waits | peak row locks | table-level / MDL excl / global lock | HLL start→peak | peak trx age s | MDL waiters | disk reads/s | replica lag s | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 0 | time-box | 1.86→1.69 | 21.84→9.84 | 41.29 | 0 | 12.37→8.27 | 0 | - | - / 0 / 0 | 256→280 | 0 | 0 | 106 | - |  |
| baseline_end | 0 | time-box | 1.62→1.6 | 8.25→17.84 | 70.01 | 0 | 8.19→8.11 | 0 | - | - / 0 / 0 | 77→280 | 0 | 0 | 0 | - |  |
| count_default | 0 | yes | 1.78→1.15 | 15.34→5.65 | 6.46 | 0 | 8.28→6.62 | 0 | - | - / 0 / 0 | 100→701 | 7 | 0 | 16677 | - | result=100000000; seconds=7.53 |
| count_estimate | 0 | yes | 1.72→- | 16.76→- | - | 0 | 7.94→1.1 | - | - | - / - / - | -→- | - | - | - | - | table_rows=99619308; table_rows_ms=1.89; stats_n_rows=99619308; stats_n_rows_ms=3.63; pk_span=100000000; pk_span_ms=0.5 |
| count_force_primary | 0 | yes | 1.88→4.15 | 21.75→38.84 | 77.46 | 0 | 7.99→5.87 | 0 | - | - / 0 / 0 | 233→4008 | 40 | 0 | 1086 | - | result=100000000; seconds=40.27 |
| count_force_secondary | 0 | yes | 1.63→1.11 | 8.01→5.83 | 6.23 | 0 | 8.3→6.4 | 0 | - | - / 0 / 0 | 200→601 | 6 | 0 | 17020 | - | result=100000000; seconds=6.57 |
| count_on_replica_t_repl | 0 | yes | 1.69→4.33 | 8.05→10.84 | 14.47 | 0 | 8.29→7.27 | 0 | - | - / 0 / 0 | 287→287 | 0 | 0 | 120 | 1 | result=20000000; seconds=5.5 |
| count_timeout_hint | 0 | yes | 1.77→1.11 | 15.66→5.51 | 6.19 | 0 | 8.13→6.14 | 0 | - | - / 0 / 0 | 99→501 | 5 | 0 | 17271 | - | error=1317: Query execution was interrupted; seconds_until_return=5.0 |
| replica_control_no_count | 0 | time-box | 1.67→1.71 | 7.97→13.98 | 35.92 | 0 | 8.17→8.13 | 0 | - | - / 0 / 0 | 240→284 | 0 | 0 | 60 | 0 |  |
