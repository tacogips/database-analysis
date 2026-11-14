# Owned static 100M-row / 100-column engine comparison

Owner command from repository root: `mise -C mysql/engine-count-100m run all`.
Ruby4.0.9 stdlib and the pinned existing MySQL8.4.13 image are required; no client library or image build. Author/reviewer phases do not start Docker/DB. This is an isolated lab, never a production counting tool.

## Current comparison

Four conditions each execute three ordinary `SELECT COUNT(*)` requests: InnoDB PRIMARY only; same InnoDB data with single-INT k_count(c001); same data after that index is replaced by k_count_v2(c001,c002,c003); MyISAM PRIMARY only. Twelve measured scalars are planned. No COUNT(1), business WHERE count, forced index, explicit locking read, service load or cache flush is used.

Each engine loads exactly100000000 rows into100 stored columns: BIGINT UNSIGNED NOT NULL PK id plus99 INT NOT NULL. Both use the same deterministic generator. c001 permutes100k values, c002 has80-percent-zero block skew, c003 is a high-cardinality modular sequence; c004..c099 retain their column numbers. Nominal scalar payload404bytes excludes storage overhead. This replaces the unexecuted constant-only prototype; no constant-only timing is claimed. No VARCHAR/LOB, compression, encryption or update/delete workload is certified.

The actual single-column index catalogue and chosen plan are saved before reference refinement. It is a valid narrow baseline, but insufficient as the only wider-index reference. The wider three-column case is added on unchanged rows, and both index build durations are separate from COUNT time. Default optimizer choice is observed, never forced. Fixed order and post-load/post-index cache warming confound any universal causal speed claim.

## Existing manual adaptation

The existing working-tree InnoDB manual is frozen under results/reviews/mysql-engine-count-100m/manual-sources/. A fresh dedicated CLI/socket connection is opened for each COUNT condition, after loader/fixture-DDL clients finish. It sets autocommit1, READ COMMITTED, lock_wait_timeout3s and max_execution_time1200000ms; the20min lab limit replaces the manual's illustrative10s. Recorded references include TABLE_ROWS inventory, exact EXPLAIN FORMAT=TRADITIONAL, selected index, cardinality/catalogue, selected-index size/n_leaf_pages/last_update, page size, each SQL/raw output/elapsedns and connection identity. Missing or uninitialized page statistics stay unavailable; MyISAM InnoDB statistics are not applicable. Metadata pages are not actual physical IO bytes, a safety threshold or a latency bound.

Only the index-inspection/COUNT portion is reproduced; production monitoring, replica/failover and query-cancellation operation are not certified by this quiet lab. Index creation is deliberate fixture setup, never a recommendation to modify service tables for COUNT.

## Execution and capacity ownership

One engine at a time, same image/config: fresh InnoDB server and load, three conditions, cleanup, then fresh MyISAM and load, one condition, cleanup. Host bind data resides exclusively in ignored .data/engine-count-100m/<nonce>/<engine>/stage, with a private owner marker outside datadirs. MySQL runs as host uid/gid; arbitrary uid inability fails closed. A local Unix Docker context only, network_mode:none, skip-networking, fixed Unix socket, no published port, no external DSN or volume, no inherited Docker/Compose overrides. Pinned digest is in compose.yaml. Lifecycle flock/name inventory/no-recreate/nonce labels prevent accidental reuse; foreground server and persistent CLI processes are reaped. Cleanup verifies ownership, runtime absence and realpaths before removing only owned host data. No global prune, other app deletion, permission repair, or root cleanup helper.

Host free space must be70GiB before each engine,24GiB before index work, with12GiB periodic floor while waiting on work. The host bind avoids the small guest filesystem; both capacities are recorded. Server4GiB/3CPU, InnoDB buffer pool1GiB, MyISAM key buffer128MiB, redo1GiB and disabled lab binlog; other actual settings are recorded. Overall6h plus120s cleanup; each load/index build at most2h, each count20min. Bounds are not promises of completion or production safety.

The100000-row SQL sequence and1000 server-side INSERT SELECT batches generate rows without client-side row transfer. Every batch records actual ROW_COUNT immediately after INSERT and validates the boundary after COMMIT. MyISAM remains nontransactional: this does not grant rollback/atomicity/crash durability. Counts themselves must each return100000000. No additional full-table validation count runs. Every5M rows logs throughput/roughETA. Partial attempts, errors, invalid samples and deadlines remain in evidence and cannot pass.

## Timing, evidence and acceptance

Primary timing is monotonic caller nanoseconds from before write to arrival of the sole scalar, before raw logging. Persistent Docker-exec/mysql socket transport remains included. No per-query process startup is timed. Boundary completion is separately measured and required for success even if a scalar already arrived. Server-only timing is unavailable. First/repeats are not certified cold/fullywarm. Three samples give descriptive min/median/max, not confidence or production SLO bounds.

Results, all command/client/server logs, actual exits, per-batch receipts, all planned conditions/samples, capacities, image/environment, sourcebefore/after, precision/completion/service-budget/cleanup are saved under results/mysql-engine-count-100m/runs/. SQL errors, mismatches, nonzero exit, source drift, missing condition or uncertain cleanup cannot pass. No row files or keys are committed. Executable/config/README and frozen manual inputs are runtime-hashed; the mutable standalone Japanese report is separately bound after actual results integration. Final acceptance requires owner semantic/source/cleanup audit and two separate GPT-6.1 Sol reviews of final bytes. Historical benchmarks and unrelated worktree changes remain untouched.

Offline only: `mise exec -- ruby mysql/engine-count-100m/verify_offline.rb`. Simulated protocol/deadline/lifecycle and condition-model checks are not measured results. SQL Server is queued after the MySQL phase.
