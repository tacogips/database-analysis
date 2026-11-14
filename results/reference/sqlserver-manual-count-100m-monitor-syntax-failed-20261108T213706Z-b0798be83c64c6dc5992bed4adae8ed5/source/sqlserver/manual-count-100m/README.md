# SQL Server manual: fixed 100M × 100, six cells / eighteen counts

Independent disposable lab implementing the frozen latest A/B/C/D manual in `manual/sql-server.md`. `manual/source.json` explains the explicit latest-snapshot binding update; baseline is unchanged. The report is [Japanese, pending measurements](../../docs/sqlserver-manual-count-100m.md). It is mutable and excluded from runtime SOURCE. Old B-only author evidence is history.

## Owner execution boundary

Author runs only stdlib offline verification. **Do not run the following until the owner has verified the separate full-scale cold MySQL success AND cleanup.** The owner's root fixed SQL execute gate remains authoritative; this runner does not launch it, MySQL, nested workflows, reviews or messages. No geometry, DSN, endpoint or timeout overrides exist.

From this directory, with already installed Ruby 4.0.9:

```sh
mise exec -- ruby verify_offline.rb
mise exec -- ruby lab.rb all
```

The first command is offline. The second is OWNER ONLY: it builds and executes the full fixture after prerequisites. The root owner must first obtain the absent shared pinned images. The controller only inspects them, builds with `--pull=false`, and creates containers with `--no-recreate --no-build --pull never`; it never pulls or removes shared images.

- SQL amd64: `mcr.microsoft.com/mssql/server:2025-latest@sha256:245f6b1c8903edae52cb20fa31419cefcde1a49938bb8c3447099011fc871ca3`.
- SDK native arm64: `mcr.microsoft.com/dotnet/sdk:10.0@sha256:212fe4cb74627d5adf9817a4088906ae3edff1bd853bb27565d4b46731efe61e`.

`ManualCount100m.csproj`, `NuGet.Config`, and `packages.lock.json` retain the original bytes: SqlClient 7.1.1 / Pkcs 10.0.14. Owner performs locked restore and actual compilation, keeping restore/build/security warnings, lock and SDK provenance. Offline C# checks are lexical only; APIs/compiler/SQL and real AG support remain unverified until then.

Only the fixed HOME Docker config and current local Unix socket are used, in `colima-brotli-verify`. COLIMA_HOME is derived from that validated socket. Endpoint/TLS/Compose environment overrides are stripped. The client connects only to internal `server` or `secondary`, never to an external DB or a writer fallback. Runtime network is internal with no public ports. Public lab passwords must only be used in this disposable lab.

## Cases and SQL

Exactly 100,000,000 rows, 100 stored columns, 404 logical bytes before engine overhead. BIGINT precedes all offset/multiplication; `c001` varied, `c002` 80% zero, `c003` high-cardinality, `c004..99` constant. One 1000 × 100k committed load, no second INSERT copy or full-target validation COUNT. Every commit receipt must match its exact contiguous ID boundaries and post-COMMIT transaction count zero.

| Cell in execution order | Actual state | Index | Samples |
|---|---|---|---:|
| B-clustered | nonAG, RCSI 1, SNAPSHOT OFF, RC 2 | PK | 3 |
| B-narrow | same | PK + c001 NC | 3 |
| B-composite | same | replace narrow with c001,c002,c003 NC | 3 |
| C-composite | nonAG, RCSI 0, SNAPSHOT ON, SNAPSHOT 5 | same composite | 3 |
| D-composite | nonAG, RCSI 0, SNAPSHOT OFF, RC 2 | same composite | 3 |
| A-composite | real readable AG SECONDARY, RC 2, automatic secondary versioning | restored same composite | 3 |

A/B/C exact SQL: `SELECT COUNT_BIG(*) AS exact_rows FROM [dbo].[Target];`.
D only: `SELECT COUNT_BIG(*) AS observed_rows FROM [dbo].[Target] WITH (NOLOCK);`.
D's latest-request exception is limited to this static fixture. Its quiet 100M match is not snapshot accuracy under writes. No locking-RC/NOLOCK stress grid, business writes, intentional contention, failover, engine internals, hints/MAXDOP, COUNT(1), or 36-count Cartesian grid.

Every cell uses a fresh unpooled no-reconnect connection. C applies the exact SNAPSHOT prefix before saving its final immutable DMV identity (isolation 5); B/D/A require 2. Save server/DB/SPID/login_time/server-returned connection_id GUID/login/isolation. No context writes, ClientConnectionId, SPID-only KILL or explicit COUNT transaction wrappers. Guard, monitor and release check actual per-route state. Disconnected connection rows never conceal surviving sessions/transactions. Settings change only from master after prior DB sessions/requests/transactions/snapshots are gone, without forced rollback.

Before each cell: exact frozen inventory/guard/role SQL, separate SHOWPLAN ON / selected SQL / OFF (independent finally deadline), lock catalog, key map, columns, planned-index pages. D plan includes exact D hint. Common composite structure is hashed across B/C/D/A. No index forcing. Missing pages are unknown, never zero. Metadata values, exact SQL and elapsed time are separate evidence; estimated plan and capacity pages are not scan IO.

## Cold data buffers and timing

Before EACH of 18 samples, B/C/D use separate-admin CHECKPOINT then DBCC DROPCLEANBUFFERS. A first proves static healthy synchronization, redo 0 and dirty target pages 0, then runs DBCC on its exclusively owned secondary, with no writable CHECKPOINT assumption. All require exact target-allocation cached/dirty page counts both zero. Dirty pages, missing proof, DBCC permission/reset failure abort; never silently run warm. This instance-wide DBCC is lab-only, never production guidance.

SQL plan cache stays. OS/VM/macOS residual file caches are explicitly allowed and uncontrolled. No global purge/drop_caches/VM restart. This is DB-DATA-buffer cold, not all-layer or compilation cold.

Scalar-arrival and full reader/command completion are positive monotonic ns. Only selected SQL is timed; setup/load/index/reset/metadata/backup/restore are outside. These are client end-to-end observations with TCP/SqlClient and monitor resource overhead, not server-only duration. Sparse admin DMVs (~1 second) track route, identity, request, locks, snapshot and transactions. A additionally checks primary static state and send/redo queues. Observations are retained; absence on short requests does not prove no locks.

All 18 slots exist pending before load. Scalar then EOF/error/timeout fails. Cancel targets original command; bounded await/dispose; unknown completion stops everything. Each sample verifies request/transaction/snapshot release; Dispose then verifies session absence independently. All 18 completions/100M values and 1000 receipts are needed. D precision remains `observed_rows_quiet_match_only`. Service budget always `UNAVAILABLE_quiet_static_lab`; Sch-S and CPU/IO/version-store costs remain.

## Actual A topology

Precreate two nonce-owned containers plus client; start only primary initially. Stable hostnames `countprimary` / `countsecondary` (under 15 chars), HADR enabled initially and verified by SERVERPROPERTY and actual @@SERVERNAME. Secondary starts only at foreground `AG_SECONDARY_GATE` after B/C/D release. Controller pins/checks its ID/mounts/capacity, starts it, proves actual Rosetta PID executable, and atomically writes the ready receipt. Incomplete gate fails.

Use Linux read-scale AG `CLUSTER_TYPE=NONE`, synchronous commit, MANUAL failover, MANUAL seeding; no cluster manager or HA/failover certification. Per-node master keys/private certs stay inside owned master DB; exchange public certs only in private shared bind, certificate endpoint auth on internal 5022. After B/C/D, change owned fixture recovery to FULL; compressed checksum full+log backups, FILELIST/HEADER/backup identity/LSN/size evidence, NORECOVERY restore to secondary, actual AG join. No uncontrolled automatic 40GB copy. Static actual SECONDARY/read allow/healthy SYNCHRONIZED/redo 0 are mandatory. No role stubs, fake flags, NULL-role acceptance, or primary-labelled-secondary query. Impossible A remains a concrete failed blocker, not omitted/pass.

Sources: [Microsoft Linux read-scale](https://learn.microsoft.com/en-us/sql/linux/business-continuity/availability-groups/configure-read-scale?view=sql-server-ver17), [manual seeding/restore alternative](https://learn.microsoft.com/en-us/sql/linux/sql-server-linux-availability-group-cross-platform?view=sql-server-ver17).

## Capacity and owned lifecycle

MacBook Air M4 10 CPU (4P/6E), 32GB, macOS 26.5.2; VZ Colima VM 4 vCPU / 8,307,171,328 bytes as prior owner observation, capture again at runtime. Two SQL containers each 3 CPU / 4GiB limit, actual SQL 2048MB; client 1 CPU / 1GiB. SQL Linux amd64 on ARM through **Rosetta only**, unsupported diagnostic. Capture source images/hardware/binfmt plus actual docker-top PID -> VM `sudo -n readlink /proc/PID/exe` for BOTH pinned servers. Reject QEMU; do not infer native/product speed rankings or production SLO.

Host-owned `repo/.data/sqlserver-manual-count-100m/<nonce>/stage/primary` and `/secondary` bind each whole `/var/opt/mssql`, including system DBs/data/log/tempdb/secrets. `/shared` binds both `/backup`. Same actual host UID:0; private owner marker OUTSIDE stage. Exact nonce/UID/realpaths/per-node mounts checked; no named/anonymous volumes. All actual SQL file paths/sizes are recorded. Guest root (~16GB) is not data storage.

Host free >=70GiB initially and before secondary, >=24GiB before index, >=12GiB checked every 3 seconds through load/count/backup/restore. Actual stage sizes recorded. Owner observed ~125GiB host free; actual floor remains decisive. No uncompressed third full copy, VM resize, unrelated deletion or scale-down. Six-hour root deadline plus independent cleanup120 seconds; load/index/AG-copy <=2h each, count20min, readiness120, synchronization poll120. Failed steps abort without dropping cells.

Controller keeps original foreground process group lifecycle: wait/reap every subprocess; flock, nonce label and ID pin before start/inspect/remove; stop client then secondary then primary. Each started SQL server must exit0, not OOM/137; stop errors preserved. Successful independent inventories establish container/network/build-image absence before full guarded-stage deletion. Never delete unowned/replaced resources or shared SQL/SDK images. Capacity restoration within1GiB of preflight is checked separately. Shared-host changes can fail that observation.

Evidence under `results/sqlserver/manual-count-100m/runs/<UTC>-<nonce>/`: atomic probe/result, all pending/failed samples, raw SQL/values/time, plans/keymaps/pages, commit receipts, AG backup/restore/role/lag states, hardware/Rosetta, CLI complete logs/exit/hash, cleanup and before/after SOURCE hash. Runtime SOURCE covers every helper/SQL/manual/config/locked graph/verifier; mutable final report excluded. Owner audits actual meanings/evidence/final source/cleanup, then TWO separate fresh GPT-6.1 Sol review roles. Author offline pass is not that acceptance.

Owned stage deletion is performed through the prescribed guest filesystem after all owned runtime resources are absent and host marker/UID/realpath guards pass. This sends unlink/forget to virtiofs, avoiding host-retained deleted-file descriptors observed when deleting behind the guest. The exact nonce directory alone is removed; no global cache purge, VM restart or unrelated cleanup is used. The host then independently verifies path absence and capacity recovery.
