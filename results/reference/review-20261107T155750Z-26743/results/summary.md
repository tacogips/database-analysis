# Impact of whole-table counts

Latency counts from the scheduled start under an open-loop workload (100 reads/s, 100 writes/s) in its own process and CPU; a sample is filed under its scheduled time. bl = baseline before the method, dur = during. Source: results/impact_<name>.json.

| experiment | method rows/s | done | write p50 bl→dur ms | write p99 bl→dur ms | write max ms | write err+stuck | read p99 bl→dur ms | row lock waits | peak row locks | table-level / MDL excl / global lock | HLL start→peak | peak trx age s | MDL waiters | disk reads/s | replica lag s | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 0 | time-box | 1.89→1.74 | 7.92→8.31 | 22.45 | 0 | 8.22→8.35 | 0 | - | - / 0 / 0 | 266→305 | 0 | 0 | 106 | - |  |
| baseline_end | 0 | time-box | 1.73→1.8 | 7.89→8.06 | 32.57 | 0 | 7.67→7.92 | 0 | - | - / 0 / 0 | 59→296 | 0 | 0 | 0 | - |  |
| count_default | 0 | yes | 1.77→1.29 | 8.18→18.45 | 49.71 | 0 | 8.44→6.8 | 0 | - | - / 0 / 0 | 196→801 | 8 | 0 | 13733 | - | result=100000000; seconds=8.58 |
| count_estimate | 0 | yes | 1.79→- | 8.14→- | - | 0 | 7.79→0.69 | - | - | - / - / - | -→- | - | - | - | - | table_rows=99619308; table_rows_ms=1.05; stats_n_rows=99619308; stats_n_rows_ms=2.04; pk_span=100000000; pk_span_ms=0.44 |
| count_force_primary | 0 | yes | 1.8→4.32 | 7.9→18.28 | 68.8 | 0 | 7.82→6.17 | 0 | - | - / 0 / 0 | 287→4208 | 41 | 0 | 1038 | - | result=100000000; seconds=42.06 |
| count_force_secondary | 0 | yes | 1.87→1.13 | 8.09→5.01 | 6.62 | 0 | 7.96→6.3 | 0 | - | - / 0 / 0 | 100→602 | 6 | 0 | 17160 | - | result=100000000; seconds=6.55 |
| count_on_replica_t_repl | 0 | yes | 1.86→4.18 | 7.4→11.1 | 19.01 | 0 | 7.71→7.42 | 0 | - | - / 0 / 0 | 292→292 | 0 | 0 | 120 | 1 | result=20000000; seconds=5.65 |
| count_timeout_hint | 0 | yes | 1.87→1.26 | 7.9→5.67 | 6.67 | 0 | 7.95→6.4 | 0 | - | - / 0 / 0 | 309→501 | 5 | 0 | 13940 | - | error=3024: Query execution was interrupted, maximum statement execution tim; seconds_until_return=5.0 |
| replica_control_no_count | 0 | time-box | 1.8→1.9 | 7.95→7.98 | 21.29 | 0 | 7.89→7.9 | 0 | - | - / 0 / 0 | 185→294 | 0 | 0 | 59 | 0 |  |
