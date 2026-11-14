# Impact of whole-table counts

Latency counts from the scheduled start under an open-loop workload (100 reads/s, 100 writes/s) in its own process and CPU; a sample is filed under its scheduled time. bl = baseline before the method, dur = during. Source: results/impact_<name>.json.

| experiment | method rows/s | done | write p50 bl→dur ms | write p99 bl→dur ms | write max ms | write err+stuck | read p99 bl→dur ms | row lock waits | peak row locks | table-level / MDL excl / global lock | HLL start→peak | peak trx age s | MDL waiters | disk reads/s | replica lag s | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 0 | time-box | 0.94→1.4 | 27.64→100.7 | 204.74 | 0 | 7.26→10.12 | 0 | 0 | 0 / 0 / 0 | 40→313 | 1 | 0 | 691 | 0 |  |
| baseline_end | 0 | time-box | 1.42→1.94 | 5.9→256.91 | 362.7 | 0 | 7.02→23.3 | 0 | 0 | 0 / 0 / 0 | 57→297 | 0 | 0 | 63 | 0 |  |
| baseline_mid | 0 | time-box | 1.18→1.83 | 5.58→118.94 | 211.58 | 0 | 6.96→16.0 | 0 | 0 | 0 / 0 / 0 | 264→273 | 0 | 0 | 57 | 0 |  |
| count_default | 0 | yes | 0.9→1.65 | 6.36→264.19 | 307.85 | 0 | 6.15→19.07 | 0 | 0 | 0 / 0 / 0 | 204→1804 | 18 | 0 | 6160 | 0 | result=100000000; seconds=18.63 |
| count_estimate | 0 | yes | 0.8→- | 5.44→- | - | 0 | 6.38→1.06 | - | - | - / - / - | -→- | - | - | - | - | table_rows=99619308; table_rows_ms=0.45; stats_n_rows=99619308; stats_n_rows_ms=1.05; pk_span=100000000; pk_span_ms=0.27 |
| count_force_primary | 0 | yes | 1.95→4.59 | 170.74→209.79 | 394.75 | 0 | 17.76→12.68 | 0 | 0 | 0 / 0 / 0 | 155→5310 | 53 | 0 | 2340 | 0 | result=100000000; seconds=53.04 |
| count_force_secondary | 0 | yes | 1.58→1.44 | 215.47→8.88 | 28.38 | 0 | 13.21→6.54 | 0 | 0 | 0 / 0 / 0 | 100→1102 | 11 | 0 | 9955 | 0 | result=100000000; seconds=11.73 |
| count_kill_latency | 0 | yes | 1.64→2.3 | 6.33→34.73 | 43.8 | 0 | 7.28→7.43 | 0 | 0 | 0 / 0 / 0 | 100→500 | 5 | 0 | 4912 | 0 | error=1317: Query execution was interrupted; seconds_until_return=5.0; kill_sent_at_s=5 |
| count_on_primary_t_repl | 0 | yes | 1.23→6.0 | 5.64→484.39 | 556.45 | 0 | 7.1→19.53 | 0 | 0 | 0 / 0 / 0 | 236→1202 | 12 | 0 | 958 | 2 | result=20000000; seconds=12.53 |
| count_on_replica_t_repl | 0 | yes | 2.06→4.22 | 300.71→23.93 | 42.43 | 0 | 18.04→6.97 | 0 | 0 | 0 / 0 / 0 | 30→233 | 0 | 0 | 0 | 0 | result=20000000; seconds=6.01 |
| count_single_thread | 0 | yes | 0.63→1.52 | 3.87→152.06 | 202.59 | 0 | 3.26→16.96 | 0 | 0 | 0 / 0 / 0 | 249→1703 | 17 | 0 | 6369 | 0 | result=100000000; seconds=17.87 |
| count_split_100k_duty50 | 2427426 | yes | 1.45→1.65 | 6.19→82.31 | 175.49 | 0 | 7.34→10.78 | 0 | 0 | 0 / 0 / 0 | 231→309 | 1 | 0 | 2795 | 0 | index=k_created; result=100000000; complete=True; seconds=41.19; max_chunk_ms=376.1 |
| count_split_100k_nosleep | 5395666 | yes | 2.04→1.51 | 75.99→9.05 | 47.92 | 0 | 11.45→6.96 | 0 | 0 | 0 / 0 / 0 | 275→275 | 0 | 0 | 6152 | 0 | index=k_created; result=100000000; complete=True; seconds=18.53; max_chunk_ms=115.1 |
| count_split_10k_duty50 | 2168307 | yes | 1.87→1.63 | 136.77→52.5 | 129.0 | 0 | 13.84→7.97 | 0 | 0 | 0 / 0 / 0 | 45→289 | 0 | 0 | 2502 | 0 | index=k_created; result=100000000; complete=True; seconds=46.12; max_chunk_ms=107.8 |
| count_split_1m_duty50 | 2798182 | yes | 1.54→1.51 | 97.72→87.62 | 183.08 | 0 | 8.62→9.85 | 0 | 0 | 0 / 0 / 0 | 98→305 | 1 | 0 | 3263 | 0 | index=k_created; result=100000000; complete=True; seconds=35.7; max_chunk_ms=1029.6 |
| count_timeout_hint | 0 | yes | 1.29→3.81 | 5.67→209.31 | 232.93 | 0 | 6.96→35.19 | 0 | 0 | 0 / 0 / 0 | 104→382 | 4 | 0 | 3633 | 0 | error=1317: Query execution was interrupted; seconds_until_return=5.0 |
| count_under_history | 0 | yes | 2.17→5.68 | 6.93→1678.74 | 1809.84 | 0 | 7.66→8.59 | 75 | 0 | 0 / 0 / 0 | 231→2298 | 20 | 0 | 0 | 0 | quiet_rr=0.26; history_rr_old_snapshot_held_by_other=0.3; history_read_uncommitted=0.28; after_snapshot_released=0.5; hll_after_release=50 |
| counter_no_trigger | 0 | time-box | 1.81→2.99 | 144.33→277.86 | 389.74 | 0 | 14.55→21.54 | 0 | 0 | 0 / 0 / 0 | 135→313 | 0 | 0 | 0 | 0 |  |
| counter_trigger_one_row | 0 | time-box | 3.13→1.79 | 792.43→8.13 | 28.5 | 0 | 31.72→7.54 | 538 | 0 | 0 / 0 / 0 | 29→282 | 0 | 0 | 0 | 0 |  |
| engine_archive_count | 0 | time-box | 2.2→1.65 | 39.25→322.14 | 490.57 | 0 | 5928.9→29222.57 | 0 | 0 | 0 / 0 / 0 | 0→0 | 0 | 0 | 0 | 0 | runs=167313; avg_seconds=0.0 |
| engine_memory_count | 0 | time-box | 1.87→1.18 | 167.12→94.52 | 173.71 | 0 | 443.88→246.84 | 0 | 0 | 0 / 0 / 0 | 0→0 | 0 | 0 | 0 | 0 | runs=570342; avg_seconds=0.0 |
| engine_myisam_count | 0 | time-box | 1.88→1.85 | 6.93→307.83 | 430.08 | 0 | 7.77→538.03 | 0 | 0 | 0 / 0 / 0 | 251→251 | 0 | 0 | 0 | 0 | runs=265402; avg_seconds=0.0 |
| fk_lock_tables_child_blocks_parent | 0 | time-box | 2.05→2.61 | 24.84→1093.7 | 1424.96 | 0 | 8.73→77.2 | 0 | 0 | 0 / 0 / 0 | 180→867 | 11 | 0 | 633 | 0 | runs=26; avg_seconds=1.55 |
| lock_baseline | 0 | time-box | 1.63→2.08 | 7.87→240.31 | 314.22 | 0 | 7.54→17.46 | 0 | 8 | 0 / 0 / 0 | 228→300 | 0 | 0 | 78 | 0 |  |
| lock_count_for_share | 0 | time-box | 3.28→13150.17 | 128.54→25584.05 | 26537.49 | 0 | 15.74→11.63 | 198 | 100000 | 0 / 0 / 0 | 27→383 | 2 | 0 | 0 | 0 | runs=43; avg_seconds=0.94 |
| lock_insert_summary_rc | 0 | time-box | 1.5→1.58 | 6.01→103.4 | 240.4 | 0 | 7.32→13.58 | 0 | 8 | 0 / 0 / 0 | 198→313 | 1 | 0 | 0 | 0 | runs=174; avg_seconds=0.23 |
| lock_insert_summary_rr | 0 | time-box | 1.69→13344.87 | 227.67→26374.97 | 27374.94 | 0 | 12.16→8.03 | 224 | 100000 | 0 / 0 / 0 | 40→366 | 1 | 0 | 0 | 0 | runs=44; avg_seconds=0.91 |
| lock_plain_count | 0 | time-box | 1.35→2.51 | 7.22→206.22 | 271.31 | 0 | 7.14→17.24 | 0 | 8 | 0 / 0 / 0 | 99→401 | 3 | 0 | 142 | 0 | runs=303; avg_seconds=0.13 |
| lock_serializable_autocommit | 0 | time-box | 1.85→1.63 | 16.22→74.3 | 174.89 | 0 | 7.69→9.54 | 0 | 6 | 0 / 0 / 0 | 208→308 | 0 | 0 | 0 | 0 | runs=506; avg_seconds=0.08 |
| lock_serializable_in_trx | 0 | time-box | 1.67→12262.98 | 7.62→23619.09 | 24632.87 | 0 | 7.86→21.34 | 262 | 100000 | 0 / 0 / 0 | 85→359 | 1 | 0 | 0 | 0 | runs=54; avg_seconds=0.74 |
| lock_set_variable_rr | 0 | time-box | 2.74→13776.27 | 22.22→23974.91 | 25017.12 | 0 | 8.34→60.28 | 254 | 100000 | 0 / 0 / 0 | 181→376 | 2 | 0 | 0 | 0 | runs=52; avg_seconds=0.78 |
| lock_tables_then_count | 0 | time-box | 1.31→337.8 | 5.81→1423.56 | 1577.18 | 0 | 6.77→17.98 | 0 | 8 | 0 / 1 / 0 | 102→428 | 0 | 4 | 0 | 0 | runs=113; avg_seconds=0.36 |
| mdl_long_count_then_ddl | 0 | yes | 1.89→5819.68 | 184.23→13580.27 | 13732.47 | 0 | 17.13→13551.87 | 0 | 0 | 0 / 1 / 0 | 77→520 | 18 | 9 | 4051 | 0 | result=100000000; seconds=18.68; ddl=completed; ddl_seconds=13.74 |
| mdl_split_count_then_ddl | 2128127 | yes | 2.49→2.17 | 18.55→192.01 | 278.33 | 0 | 7.66→16.61 | 0 | 0 | 0 / 0 / 0 | 24→296 | 1 | 0 | 1577 | 0 | index=k_created; ddl=completed; ddl_seconds=0.02; result=100000000; complete=True; seconds=46.98; max_chunk_ms=544.3 |
| replica_control_no_count | 0 | time-box | 1.69→2.0 | 6.94→262.96 | 344.03 | 0 | 7.43→26.23 | 0 | 0 | 0 / 0 / 0 | 109→307 | 1 | 0 | 102 | 1 |  |
| replica_count_vs_ddl | 0 | yes | 1.7→1.81 | 16.08→63.43 | 347.75 | 0 | 7.58→8.01 | 0 | 0 | 0 / 0 / 0 | 278→296 | 0 | 0 | 28 | 39 |  |
