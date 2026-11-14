# SQL Server manual: fixed 100M × 100 diagnostic lab

This independent owner runner implements the frozen manual in `manual/sql-server.md`, receipt in `manual/source.json`. Input bindings are in `input-bindings.json`. No root `all`, MySQL controller, or issue3 runtime is invoked. `docs/sqlserver-manual-count-100m.md` is a mutable reporting document and deliberately excluded from runtime SOURCE.

## Execution boundary

The author performs offline stdlib checks only. The owner executes this runner **after the concurrent MySQL full-scale measurement and its cleanup have completed**, then audits source, evidence, final server state and recovered capacity. Image downloads, locked restore, compilation and SQL execution belong to that owner phase. Do not run concurrently with MySQL, restart/reconfigure the VM, change host permissions, resize storage, prune resources, or substitute fewer rows/columns or named-volume storage.

From repository root:

```sh
mise -C sqlserver/manual-count-100m run all
```

Equivalent from this directory: `mise exec -- ruby lab.rb all`. Ruby is fixed at 4.0.9; system Ruby 2.6 is unsuitable. There are no DSN, geometry, sample-count, endpoint or timeout overrides. Only the current context in fixed `$HOME/.docker/config.json` is read. It must be `colima-brotli-verify` with a local Unix socket; endpoint/TLS/Compose variables are not inherited.

The owner must repull these exact shared base images after MySQL cleanup if absent. The runner never pulls them or deletes them:

- SQL Server: `mcr.microsoft.com/mssql/server:2025-latest@sha256:245f6b1c8903edae52cb20fa31419cefcde1a49938bb8c3447099011fc871ca3` (`linux/amd64`).
- SDK: `mcr.microsoft.com/dotnet/sdk:10.0@sha256:212fe4cb74627d5adf9817a4088906ae3edff1bd853bb27565d4b46731efe61e` (`linux/arm64`).

Only the owner build needs package access. `dotnet restore --locked-mode` uses the copied issue3 dependency graph (SqlClient 7.1.1 and Pkcs 10.0.14, net10). Restore/build logs, actual lock file and `dotnet --info` are copied from the built image. The runtime network is internal and exposes no host ports. The client uses fixed `tcp:server,1433`; no other DB is supported. The disposable lab credential is public fixture configuration and must never be used outside this lab.

## Platform and capacity gates

macOS ARM64 runs unsupported Linux SQL Server amd64 through **Rosetta**, never QEMU. The runner captures VM configuration and enabled binfmt registration `/mnt/lima-rosetta/rosetta`, then obtains actual SQL server host PIDs with `docker top` on the pinned server ID and reads `/proc/PID/exe` inside the prescribed VM. Each selected executable must contain `rosetta`. Config alone does not pass. Results are diagnostic: no native timing estimate and no direct cross-product speed ranking.

At least 70 GiB host free space is required before server setup, at least 24 GiB before each index change, and a 12 GiB floor is checked every three seconds while the client runs. Host data is exclusively `repo/.data/sqlserver-manual-count-100m/<32hex-owner>/stage`, mapped to `/var/opt/mssql`. The VM's small root filesystem is not used for database files. The private ownership marker is outside `stage`; exact realpaths, UID and nonce must agree before deletion. Server and client run as the actual `Process.uid:0`, following the SQL image arbitrary-user contract. Permission/virtiofs failure aborts and records failure; it never silently switches storage or scale.

All master/model/msdb/tempdb and lab physical file paths are recorded and checked under the host bind. Lab recovery is SIMPLE, lab data is capped at 80 GiB and lab log at 4 GiB; the host floor remains an independent stop condition. SQL memory is capped at 3072 MB, container 4 GiB/3 CPUs, client at 1 GiB/1 CPU. Global deadline is six hours, load two hours, each index DDL two hours, each COUNT twenty minutes, controller cleanup 120 seconds. A failed stop or forced kill is recorded as failure even when subsequent removal succeeds.

## Fixed experimental model

`dbo.Target`: `id BIGINT NOT NULL PRIMARY KEY CLUSTERED` plus `c001..c099 INT NOT NULL`: exactly 100 stored columns, logical 404 bytes before engine overhead. IDs 1..100,000,000 use BIGINT arithmetic before multiplication/offsets. A five-digit cross join generates 100,000 rows per batch, exactly 1000 committed INSERT batches, one load. Each receipt includes post-COMMIT row count, ID boundaries, transaction count, exact SQL, raw output and monotonic elapsed time. There is no extra table-wide validation COUNT. No RLS predicates are created; SA is not treated as proof that RLS would be bypassed in another database.

Three immutable-data conditions, fixed order:

1. Clustered primary key only.
2. Same rows plus nonclustered `k_count(c001)`.
3. Drop the narrow index and create `k_count_v2(c001,c002,c003)`.

Exactly three plain COUNT_BIG(*) commands per condition, nine accepted samples required. Each condition owns one fresh unpooled SqlConnection with no reconnect or ambient transaction; server name, DB, SPID, login time, actual server connection_id, login name and isolation are saved and rechecked. No ClientConnectionId substitution, context/tag writes, hints, alternate isolation matrix or ownership-tag logic is used. Index presence is never substituted for optimizer selection. Offline index DDL and INSERT/DDL natural locks are fixture work; no lock-free index-build claim is made.

## Manual and timing evidence

Before each condition the driver executes the frozen manual inventory, transaction/database/identity/RCSI/table-kind guard, separate SHOWPLAN ON / SELECT / OFF batches, index lock catalog, additional index-key mapping and planned-index partition page aggregation. Only DB name and the plan-derived index ID are substituted. SHOWPLAN OFF has an independent cleanup deadline, even on error. Plans are estimated XML, never actual execution plans. Scan indexes and parallel attributes are parsed from XML. Missing/NULL inventory or pages stay unknown; page counts ×8192 are capacity references, never actual scan IO or latency bounds.

Only branch B is dispatched: READ COMMITTED under RCSI=1 in this new independent quiet lab, with AG role NULL explicitly required. RCSI is enabled before count sessions as fixture setup; this is not advice to enable it in production. The frozen manual's fallback D is excluded by AGENTS/user scope. Sch-S and resource costs remain. There is no forced error, competing DDL, intentional contention, service workload, or query hint.

Primary timing starts immediately before SqlClient execution. High-resolution monotonic nanoseconds to first scalar arrival are separate from full command/reader completion. The primary command contains only the plain exact SELECT; identity/guard/metadata and recording occur outside it. Timer includes server plus TCP/SqlClient/client overhead. A separate already-open admin connection sparsely samples actual request/locks/snapshot/transaction DMVs and DB role/settings every one second; observations are buffered and saved after count completion, without evidence flushes in the primary timer. Short requests may have no in-flight DMV sample; that is not evidence of zero locks.

After every sample an independent management deadline verifies the exact session has no request, transaction or snapshot; raw locks remain observational. After condition Dispose it verifies that physical tuple is gone. Matching uses SPID plus login time, login and server, and validates the actual connection_id whenever present. A disconnected session with connection_id NULL stays visible until its session/transactions disappear; database and isolation are independently checked, never used to hide a changed live connection. On failure Command.Cancel targets the same instance first, then bounded await/dispose. Unknown completion stops the experiment and transfers server cleanup to the controller. No blind KILL of a recycled SPID occurs; admin @@TRANCOUNT is not release evidence.

Nine slots begin as pending. Every attempted failure is retained, including a scalar followed by timeout/EOF. All nine completions and 100M values plus all 1000 commit receipts are needed for precision/completion acceptance. Quiet-lab service budget is always UNAVAILABLE, even on success.

## Lifecycle and acceptance

The foreground controller holds a flock, rejects pre-existing reserved names, uses nonce labels, Compose create `--no-recreate --no-build --pull never`, verifies ownership and pins resource IDs before start/inspect/remove. It owns subprocess groups until waited/reaped. Only its nonce build image can be removed; shared SQL/SDK images are preserved. Successful inventories, never failed inspect calls, establish absence. Server bind inspection refuses anonymous/named volumes; container removal uses `--volumes` for owned containers. Host-stage deletion requires successful absence verification and marker validation. Data absence and host free-space recovery within 1 GiB of preflight are recorded separately (shared-host capacity can cause failure).

Evidence: `results/sqlserver/manual-count-100m/runs/<UTC>-<nonce>/result.json`, driver `probe.json`, exact SQL/raw per-step JSON, per-condition `.sqlplan`, scalar and full-completion timings, monitor/cleanup observations, index DDL timings, driver/server/CLI logs and actual exit states. Probe JSON is atomically persisted with fsync after each 100k committed receipt and sample; controller result imports it during capacity polling and after terminal exit. Runtime SOURCE covers every implementation/config/helper/manual/receipt/README/verifier dependency and is hashed before/after. Source drift fails acceptance. Individual CLI logs have terminal exit status and hashes. Incomplete logs, unknown completion, missing samples, stop failures or cleanup ambiguity never pass.

Owner acceptance requires successful locked compilation and full-scale run, semantic/SQL/source/cleanup audit, then two adversarial GPT-6.1 Sol roles in fresh separate sessions. This runner does not launch reviews or nested workflows. Fixed index order confounds cache state, load and index-build warming; first post-load is not certified cold, and repeats are not certified warm. The wider index is a reference comparison, not a recommendation to add it in production. COUNT(1) is unmeasured and unnecessary for this question.

Offline stdlib verification: `mise exec -- ruby verify_offline.rb`. It parses Ruby syntax and C# lexical structure, tests acceptance/failure and lifecycle models, SQL/manual boundaries, geometry and ownership refusal. It does not compile C# or validate runtime behavior. Compilation belongs to the owner.
