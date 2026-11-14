using System.Data;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using System.Xml.Linq;
using Microsoft.Data.SqlClient;

// A disposable functional probe, not a production cancellation service or a COUNT benchmark.
internal static class Program
{
    const string Db = "CountIssue1";
    const string Password = "CountLab_Only_2026!"; // Public disposable lab credential, never production.
    static readonly Stopwatch Clock = Stopwatch.StartNew();
    static readonly CancellationTokenSource Total = new(TimeSpan.FromSeconds(200));
    static readonly AsyncLocal<CancellationToken?> CleanupBudget = new();
    static CancellationToken IoToken => CleanupBudget.Value ?? Total.Token;
    static readonly List<object> Events = new();
    static readonly Dictionary<string, object?> Result = new()
    {
        ["completion"] = "incomplete", ["ownership"] = "unverified",
        ["transaction_release"] = "unverified", ["recovery"] = "unverified",
        ["precision"] = "unverified", ["service_budget"] = "unavailable_functional_only",
        ["communication_scope"] = "SqlConnection.Dispose on idle open snapshot transaction; not a network partition",
        ["carrier"] = "WAITFOR after materialized snapshot: attention injector, not huge COUNT performance"
    };
    record Identity(int Spid, DateTime LoginTime, Guid ConnectionId, byte[] Tag,
        string Login, string OriginalLogin, string App, string Database, string Server);
    record State(List<Dictionary<string, object?>> Sessions, List<Dictionary<string, object?>> Requests,
        List<Dictionary<string, object?>> Transactions, List<Dictionary<string, object?>> Snapshots,
        List<Dictionary<string, object?>> ActiveTransactions);
    static void Event(string name, object? detail) {
        var item = new { name, elapsed_monotonic_ms = Clock.Elapsed.TotalMilliseconds, detail };
        Events.Add(item);
        Console.WriteLine(JsonSerializer.Serialize(item));
    }
    static void Require(bool ok, string why) { if (!ok) throw new InvalidOperationException(why); }
    static SqlConnection Connection(string database = Db, string app = "issue1-owner", string login = "sa") => new(
        new SqlConnectionStringBuilder {
            DataSource = "tcp:server,1433", InitialCatalog = database, UserID = login, Password = Password,
            Pooling = false, Enlist = false, MultipleActiveResultSets = false,
            ConnectTimeout = 3, ConnectRetryCount = 0, ApplicationName = app,
            Encrypt = SqlConnectionEncryptOption.Mandatory, TrustServerCertificate = true
        }.ConnectionString);
    static SqlCommand Command(SqlConnection c, string sql, int timeout = 5) => new(sql, c) { CommandTimeout = timeout };
    static async Task Exec(SqlConnection c, string sql, int timeout = 5) {
        using var q = Command(c, sql, timeout); await q.ExecuteNonQueryAsync(IoToken);
    }
    static async Task<object?> Scalar(SqlConnection c, string sql) {
        using var q = Command(c, sql); var v = await q.ExecuteScalarAsync(IoToken); return v == DBNull.Value ? null : v;
    }
    static async Task<List<Dictionary<string, object?>>> Rows(SqlCommand q) {
        using var reader = await q.ExecuteReaderAsync(IoToken);
        var rows = new List<Dictionary<string, object?>>();
        while (await reader.ReadAsync(IoToken)) {
            var row = new Dictionary<string, object?>();
            for (int i = 0; i < reader.FieldCount; i++) row[reader.GetName(i)] = reader.IsDBNull(i) ? null : reader.GetValue(i);
            rows.Add(row);
        }
        return rows;
    }
    static async Task<List<Dictionary<string, object?>>> Rows(SqlConnection c, string sql) {
        using var q = Command(c, sql); return await Rows(q);
    }
    static object Errors(Exception e) => e is SqlException s
        ? new { type = e.GetType().Name, message = e.Message, errors = s.Errors.Cast<SqlError>().Select(x => new {
            number = x.Number, state = x.State, severity = x.Class, line = x.LineNumber, procedure = x.Procedure, message = x.Message }).ToArray() }
        : new { type = e.GetType().Name, message = e.Message, errors = Array.Empty<object>() };
    const string IdentitySelect = """
        SELECT s.session_id, s.login_time, c.connection_id, s.context_info,
               s.login_name, s.original_login_name, s.program_name, DB_NAME(s.database_id) db_name,
               CONVERT(nvarchar(128), SERVERPROPERTY('ServerName')) server_name
        FROM sys.dm_exec_sessions s JOIN sys.dm_exec_connections c ON c.session_id=s.session_id
        WHERE s.session_id=@sid AND c.parent_connection_id IS NULL;
        """;
    static async Task<Identity?> ReadIdentity(SqlConnection c, int sid) {
        using var q = Command(c, IdentitySelect); q.Parameters.Add("@sid", SqlDbType.Int).Value = sid;
        var r = await Rows(q);
        if (r.Count == 0) return null;
        Require(r.Count == 1, "ambiguous physical connection identity");
        var x = r[0];
        return new(Convert.ToInt32(x["session_id"]), (DateTime)x["login_time"]!, (Guid)x["connection_id"]!,
            (byte[])x["context_info"]!, (string)x["login_name"]!, (string)x["original_login_name"]!,
            (string)x["program_name"]!, (string)x["db_name"]!, (string)x["server_name"]!);
    }
    static bool Same(Identity? a, Identity b) => a != null && a.Spid == b.Spid && a.LoginTime == b.LoginTime &&
        a.ConnectionId == b.ConnectionId && a.Tag.SequenceEqual(b.Tag) && a.Login == b.Login &&
        a.OriginalLogin == b.OriginalLogin && a.App == b.App && a.Database == b.Database && a.Server == b.Server;
    static async Task<Identity> Initialize(SqlConnection c) {
        await c.OpenAsync(IoToken);
        await Exec(c, """
            IF @@TRANCOUNT<>0 OR XACT_STATE()<>0 THROW 51000,'Existing transaction: reject',1;
            SET IMPLICIT_TRANSACTIONS OFF; SET XACT_ABORT OFF; SET LOCK_TIMEOUT 1000;
            IF DB_NAME()<>N'CountIssue1' THROW 51001,'Wrong database',1;
            """);
        using (var q = Command(c, "SET CONTEXT_INFO @tag;")) {
            q.Parameters.Add("@tag", SqlDbType.VarBinary, 128).Value = RandomNumberGenerator.GetBytes(32);
            await q.ExecuteNonQueryAsync(IoToken);
        }
        int sid = Convert.ToInt32(await Scalar(c, "SELECT @@SPID;"));
        return (await ReadIdentity(c, sid)) ?? throw new Exception("own identity not visible");
    }
    static async Task Preflight(SqlConnection control) {
        var p = await Rows(control, """
            SELECT HAS_PERMS_BY_NAME(NULL,NULL,'VIEW SERVER PERFORMANCE STATE') performance_state,
                   HAS_PERMS_BY_NAME(NULL,NULL,'ALTER ANY CONNECTION') alter_connection,
                   CONVERT(nvarchar(128),SERVERPROPERTY('ProductVersion')) product_version,
                   IS_SRVROLEMEMBER('sysadmin') sysadmin;
            """);
        Event("permission_preflight", p);
        Require(Convert.ToInt32(p[0]["performance_state"]) == 1 && Convert.ToInt32(p[0]["alter_connection"]) == 1,
            "monitor unavailable / kill unauthorized; no automatic grants");
        // Validate access to each DMV actually used, not merely an assumed role.
        foreach (var view in new[] { "sys.dm_exec_sessions", "sys.dm_exec_connections", "sys.dm_exec_requests",
            "sys.dm_tran_session_transactions", "sys.dm_tran_active_transactions", "sys.dm_tran_active_snapshot_database_transactions",
            "sys.dm_tran_version_store_space_usage", "sys.dm_tran_persistent_version_store_stats" }) {
            await Rows(control, $"SELECT TOP (0) * FROM {view};");
            Event("dmv_access_preflight", new { view, status = "authorized_lab_sa_not_least_privilege_certification" });
        }
    }
    static async Task<State> Observe(SqlConnection c, Identity id, IEnumerable<long>? captured = null) {
        // IDs are server-returned integers, never interpolated user input. Retain captured TX IDs after session removal.
        string ids = string.Join(',', (captured ?? Array.Empty<long>()).Distinct());
        string txFilter = ids.Length == 0 ? "1=0" : $"transaction_id IN ({ids})";
        var sessions = await Rows(c, $"SELECT session_id,status,open_transaction_count FROM sys.dm_exec_sessions WHERE session_id={id.Spid};");
        var requests = await Rows(c, $"SELECT session_id,request_id,status,command,wait_type,wait_time,blocking_session_id FROM sys.dm_exec_requests WHERE session_id={id.Spid};");
        var tx = await Rows(c, $"SELECT session_id,transaction_id,is_user_transaction,open_transaction_count FROM sys.dm_tran_session_transactions WHERE session_id={id.Spid} OR {txFilter};");
        var snap = await Rows(c, $"SELECT session_id,transaction_id,is_snapshot,transaction_sequence_num,elapsed_time_seconds FROM sys.dm_tran_active_snapshot_database_transactions WHERE session_id={id.Spid} OR {txFilter};");
        var active = await Rows(c, $"SELECT transaction_id,transaction_type,transaction_state FROM sys.dm_tran_active_transactions WHERE transaction_id IN (SELECT transaction_id FROM sys.dm_tran_session_transactions WHERE session_id={id.Spid}) OR {txFilter};");
        return new(sessions, requests, tx, snap, active);
    }
    static long[] TransactionIds(State s) => s.Transactions.Select(t => Convert.ToInt64(t["transaction_id"]))
        .Concat(s.Snapshots.Select(t => Convert.ToInt64(t["transaction_id"])))
        .Concat(s.ActiveTransactions.Select(t => Convert.ToInt64(t["transaction_id"]))).Distinct().ToArray();
    static async Task<object> VersionSpace(SqlConnection c) => new {
        tempdb = await Rows(c, "SELECT database_id,reserved_page_count,reserved_space_kb FROM sys.dm_tran_version_store_space_usage WHERE database_id=DB_ID(N'CountIssue1');"),
        pvs = await Rows(c, "SELECT database_id,persistent_version_store_size_kb,oldest_active_transaction_id FROM sys.dm_tran_persistent_version_store_stats WHERE database_id=DB_ID(N'CountIssue1');"),
        meaning = "allocation diagnostic; empty row is not measured zero; not per-owner retention or production recovery"
    };
    static async Task<string> GuardedKill(SqlConnection admin, Identity id) {
        // KILL is SPID-only. Same-batch recheck reduces, but does not eliminate, TOCTOU/ID reuse.
        using var q = Command(admin, $"""
            IF HAS_PERMS_BY_NAME(NULL,NULL,'VIEW SERVER PERFORMANCE STATE')<>1
               OR HAS_PERMS_BY_NAME(NULL,NULL,'ALTER ANY CONNECTION')<>1
              THROW 51002,'monitor unavailable or kill unauthorized',1;
            IF EXISTS (
              SELECT 1 FROM sys.dm_exec_sessions s JOIN sys.dm_exec_connections c ON c.session_id=s.session_id
              WHERE s.session_id=@sid AND s.login_time=@started AND c.connection_id=@cid
                AND s.context_info=@tag AND s.login_name=@login AND s.original_login_name=@original
                AND s.program_name=@app AND DB_NAME(s.database_id)=@db
                AND CONVERT(nvarchar(128),SERVERPROPERTY('ServerName'))=@server
                AND c.parent_connection_id IS NULL)
            BEGIN
              KILL {id.Spid};
              SELECT N'kill_sent' outcome;
            END
            ELSE SELECT N'refused_identity_mismatch_or_absent' outcome;
            """);
        q.Parameters.Add("@sid", SqlDbType.Int).Value = id.Spid;
        q.Parameters.Add("@started", SqlDbType.DateTime).Value = id.LoginTime;
        q.Parameters.Add("@cid", SqlDbType.UniqueIdentifier).Value = id.ConnectionId;
        q.Parameters.Add("@tag", SqlDbType.VarBinary, 128).Value = id.Tag;
        q.Parameters.Add("@login", SqlDbType.NVarChar, 128).Value = id.Login;
        q.Parameters.Add("@original", SqlDbType.NVarChar, 128).Value = id.OriginalLogin;
        q.Parameters.Add("@app", SqlDbType.NVarChar, 128).Value = id.App;
        q.Parameters.Add("@db", SqlDbType.NVarChar, 128).Value = id.Database;
        q.Parameters.Add("@server", SqlDbType.NVarChar, 128).Value = id.Server;
        return Convert.ToString(await q.ExecuteScalarAsync(IoToken))!;
    }
    static async Task PollRelease(SqlConnection admin, Identity id, long[] ids, bool requireSessionGone) {
        double until = Clock.Elapsed.TotalSeconds + 12;
        while (true) {
            var s = await Observe(admin, id, ids);
            Event("release_poll", s);
            // A reused SPID is not ours, but do not infer safe completion from that alone: retain TX IDs.
            var current = await ReadIdentity(admin, id.Spid);
            bool absent = current == null;
            if (current != null && !Same(current, id)) throw new Exception("ownership changed: refuse/escalate; no stale-SPID retry");
            if (s.Requests.Count == 0 && s.Transactions.Count == 0 && s.Snapshots.Count == 0 && s.ActiveTransactions.Count == 0 && (!requireSessionGone || (absent && s.Sessions.Count == 0))) return;
            Require(Clock.Elapsed.TotalSeconds < until, "release unknown: stop new work and escalate to owner");
            await Task.Delay(150, IoToken);
        }
    }
    static async Task Cleanup(SqlConnection owner, SqlConnection admin, Identity id, long[] ids, bool ownTx, bool injectRollbackFailure) {
        // Cleanup has a fresh bounded token; a cancelled work/global token cannot suppress rollback.
        var previousBudget = CleanupBudget.Value;
        using var cleanupBudget = new CancellationTokenSource(TimeSpan.FromSeconds(30));
        CleanupBudget.Value = cleanupBudget.Token;
        try {
            Event("client_finally_enter", new { id.Spid, connection_state = owner.State.ToString(), ownTx, injectRollbackFailure });
            bool rolledBack = false;
            if (owner.State == ConnectionState.Open && ownTx) {
                try {
                    Require(Same(await ReadIdentity(owner, id.Spid), id), "original ownership mismatch; refuse rollback");
                    if (injectRollbackFailure) throw new InvalidOperationException("lab injected client rollback failure before SQL dispatch; connection stays leased");
                    await Exec(owner, """
                        IF @@TRANCOUNT<>1 OR XACT_STATE()=0 THROW 51003,'Unexpected owned transaction state',1;
                        ROLLBACK TRANSACTION;
                        IF @@TRANCOUNT<>0 OR XACT_STATE()<>0 THROW 51004,'Rollback incomplete',1;
                        """, 3);
                    rolledBack = true;
                    Event("original_rollback", "completed");
                } catch (Exception e) { Event("original_rollback_unavailable_or_failed", Errors(e)); }
            }
            if (!rolledBack) {
                var current = await ReadIdentity(admin, id.Spid);
                if (current != null) {
                    Require(Same(current, id), "management ownership uncertain: refuse/escalate");
                    string action = await GuardedKill(admin, id);
                    Event("management_cleanup", action);
                    Require(action == "kill_sent", "management recheck refused: escalate, never retry stale SPID");
                } else Event("management_cleanup", "physical session already absent; no KILL sent");
            }
            await PollRelease(admin, id, ids, !rolledBack);
            owner.Dispose(); // Pooling=false. Never return a broken/open-TX connection to a pool.
            await PollRelease(admin, id, ids, true);
            Event("retention_release", new { status = "owner_session_transaction_and_active_snapshot_absent", space = await VersionSpace(admin),
                production_recovery = "unverified; physical reclamation can lag retention release" });
        } finally { CleanupBudget.Value = previousBudget; }
    }
    static async Task Fixture(SqlConnection master) {
        await Exec(master, "EXEC sp_configure 'show advanced options',1; RECONFIGURE; EXEC sp_configure 'max server memory (MB)',3072; RECONFIGURE;");
        Require(await Scalar(master, "SELECT DB_ID(N'CountIssue1');") == null, "fixture already exists; refuse");
        await Exec(master, """
            CREATE DATABASE CountIssue1
              ON PRIMARY (NAME=CountIssue1,FILENAME='/var/opt/mssql/data/CountIssue1.mdf',SIZE=16MB,MAXSIZE=256MB,FILEGROWTH=16MB)
              LOG ON (NAME=CountIssue1_log,FILENAME='/var/opt/mssql/data/CountIssue1_log.ldf',SIZE=8MB,MAXSIZE=128MB,FILEGROWTH=8MB);
            ALTER DATABASE CountIssue1 SET RECOVERY SIMPLE;
            ALTER DATABASE CountIssue1 SET READ_COMMITTED_SNAPSHOT ON;
            ALTER DATABASE CountIssue1 SET ALLOW_SNAPSHOT_ISOLATION ON;
            """, 15);
        using var c = Connection(app: "issue1-fixture"); await c.OpenAsync(IoToken);
        await Exec(c, """
            CREATE TABLE dbo.Target(id int NOT NULL CONSTRAINT PK_Target PRIMARY KEY, value int NOT NULL);
            ;WITH n AS (SELECT 1 n UNION ALL SELECT n+1 FROM n WHERE n<4096)
              INSERT dbo.Target SELECT n,0 FROM n OPTION(MAXRECURSION 4096);
            """);
        Event("fixture", new { known_rows = 4096, engine = "disk rowstore SQL Server; InnoDB not applicable", privileged = "disposable lab sa" });
    }
    static async Task RestrictedObserver(SqlConnection master, Identity id) {
        await Exec(master, "CREATE LOGIN issue1_observer WITH PASSWORD='CountLab_Only_2026!',CHECK_POLICY=OFF; DENY VIEW SERVER PERFORMANCE STATE TO issue1_observer;");
        using var r = Connection("master", "issue1-restricted-observer", "issue1_observer"); await r.OpenAsync(IoToken);
        var permission = await Scalar(r, "SELECT HAS_PERMS_BY_NAME(NULL,NULL,'VIEW SERVER PERFORMANCE STATE');");
        Require(Convert.ToInt32(permission) == 0, "restricted observer unexpectedly privileged");
        try {
            var observed = await ReadIdentity(r, id.Spid);
            Event("restricted_observer", new { status = "monitor_unavailable_insufficient_visibility", target_visible = observed != null,
                meaning = "partial/empty visibility never zero sessions" });
        } catch (SqlException e) { Event("restricted_observer", new { status = "monitor_unavailable_denied", error = Errors(e) }); }
    }
    static async Task Case(SqlConnection admin, SqlConnection master, string name) {
        using var owner = Connection(app: "issue1-" + name);
        var id = await Initialize(owner);
        bool ownTx = false; long[] ids = Array.Empty<long>();
        try {
            Require(Same(await ReadIdentity(admin, id.Spid), id), "two-connection identity mismatch");
            Event("two_connection_identity_equal", new { case_name = name, server_identity = id,
                client_connection_id = owner.ClientConnectionId, client_id_not_assumed_equal_to_server_id = true });
            if (name == "server_error") {
                await RestrictedObserver(master, id);
                foreach (var wrong in new[] { id with { Tag = RandomNumberGenerator.GetBytes(32) },
                    id with { LoginTime = id.LoginTime.AddSeconds(-1) }, id with { ConnectionId = Guid.NewGuid() } }) {
                    string refused = await GuardedKill(admin, wrong);
                    Require(refused == "refused_identity_mismatch_or_absent", "mismatch KILL not refused");
                    Require(Convert.ToInt32(await Scalar(owner, "SELECT 1;")) == 1, "owner unusable after mismatch");
                    Event("wrong_identity_refused_owner_usable", new { refused, wrong_tag = !wrong.Tag.SequenceEqual(id.Tag),
                        wrong_time = wrong.LoginTime != id.LoginTime, wrong_connection_id = wrong.ConnectionId != id.ConnectionId, kill_sent = false });
                }
            }
            await Exec(owner, "IF @@TRANCOUNT<>0 OR XACT_STATE()<>0 THROW 51000,'Existing tx',1; SET TRANSACTION ISOLATION LEVEL SNAPSHOT; BEGIN TRANSACTION;");
            ownTx = true;
            Require(Convert.ToInt64(await Scalar(owner, "SELECT COUNT_BIG(*) FROM dbo.Target;")) == 4096, "snapshot count wrong");
            // Ordinary small update generates versions without conflict with a snapshot reader.
            await Exec(admin, "UPDATE dbo.Target SET value=value+1 WHERE id=1;");
            var before = await Observe(admin, id); ids = TransactionIds(before);
            Event("materialized_snapshot_sleeping_open_transaction", new { case_name = name, state = before, space = await VersionSpace(admin) });
            Require(before.Requests.Count == 0 && before.Transactions.Count > 0 && before.Snapshots.Count > 0 &&
                (string?)before.Sessions.Single()["status"] == "sleeping", "sleeping open-TX not demonstrated");
            if (name == "dispose_connection") {
                owner.Dispose();
                Event("communication_failure_simulation", "SqlConnection.Dispose on idle physical connection; no actual network partition exercised");
            } else if (name == "management_fallback") {
                Event("cleanup_fault_injection", "client rollback path forced to fail before dispatch; leased idle connection retained for guarded KILL test");
            } else {
                using var q = Command(owner, name == "server_error" ? "THROW 51010,'Issue1 deliberate server error',1;" : "WAITFOR DELAY '00:00:12';", name == "command_timeout" ? 1 : 16);
                Exception? observedError = null;
                Require(Same(await ReadIdentity(admin, id.Spid), id), "identity mismatch before fault command/timeout");
                double begin = Clock.Elapsed.TotalMilliseconds;
                Task pending = q.ExecuteNonQueryAsync(IoToken);
                if (name == "client_cancel") {
                    double waitUntil = Clock.Elapsed.TotalSeconds + 4;
                    while ((await Observe(admin, id, ids)).Requests.Count == 0) {
                        Require(!pending.IsCompleted && Clock.Elapsed.TotalSeconds < waitUntil, "attention carrier never became active");
                        await Task.Delay(50, IoToken);
                    }
                    Require(Same(await ReadIdentity(admin, id.Spid), id), "identity mismatch before Cancel");
                    q.Cancel(); Event("client_cancel_called", new { result = "Cancel returned; server completion still requires observation" });
                }
                try { await pending; } catch (Exception e) { observedError = e; Event("command_error", new { case_name = name, error = Errors(e) }); }
                Require(observedError != null, "expected fault did not occur");
                if (name == "server_error") Require(observedError is SqlException se && se.Number == 51010, "wrong server error");
                if (name == "command_timeout") Require(observedError is SqlException te && te.Number == -2, "wrong timeout result");
                if (name == "client_cancel") Require(observedError is SqlException or OperationCanceledException, "unexpected Cancel result");
                Event("fault_elapsed", new { case_name = name, elapsed_monotonic_ms = Clock.Elapsed.TotalMilliseconds - begin });
                var after = await Observe(admin, id, ids);
                Event("post_fault_before_finally", new { case_name = name, state = after });
                Require(after.Requests.Count == 0 && after.Transactions.Count > 0 && after.Snapshots.Count > 0 &&
                    (string?)after.Sessions.Single()["status"] == "sleeping", "post-fault sleeping snapshot TX not observed");
            }
        } finally {
            // Cleanup ownership is in the driver, not in a TRY/CATCH that cannot catch attention.
            if (ownTx) await Cleanup(owner, admin, id, ids, ownTx, name == "management_fallback");
        }
        Event("case_passed", name);
    }
    static async Task<List<Dictionary<string, object?>>> Settings(SqlConnection c) => await Rows(c, """
        SELECT name,compatibility_level,is_auto_create_stats_on,is_auto_update_stats_on,is_auto_update_stats_async_on,
               is_read_committed_snapshot_on,snapshot_isolation_state,is_accelerated_database_recovery_on,
               CONVERT(nvarchar(128),SERVERPROPERTY('ProductVersion')) product_version
        FROM sys.databases WHERE database_id=DB_ID();
        """);
    static async Task<List<Dictionary<string, object?>>> Stats(SqlConnection c, string table) => await Rows(c, $"""
        SELECT s.name,s.stats_id,s.auto_created,s.user_created,s.has_filter,p.last_updated,p.rows,p.rows_sampled,p.modification_counter
        FROM sys.stats s OUTER APPLY sys.dm_db_stats_properties(s.object_id,s.stats_id) p
        WHERE s.object_id=OBJECT_ID(N'dbo.{table}');
        """);
    static async Task StatisticsProbe(SqlConnection c) {
        Event("stats_original_settings", await Settings(c));
        Event("stats_low_priority_existing", await Rows(c, "SELECT name,value,value_for_secondary FROM sys.database_scoped_configurations WHERE name=N'ASYNC_STATS_UPDATE_WAIT_AT_LOW_PRIORITY';"));
        foreach (string profile in new[] { "off", "sync_on", "async_on" }) {
            // Only this newly-created lab database is reconfigured. No production recommendation.
            await Exec(c, $"ALTER DATABASE CountIssue1 SET AUTO_CREATE_STATISTICS {(profile == "off" ? "OFF" : "ON")}; ALTER DATABASE CountIssue1 SET AUTO_UPDATE_STATISTICS {(profile == "off" ? "OFF" : "ON")}; ALTER DATABASE CountIssue1 SET AUTO_UPDATE_STATISTICS_ASYNC {(profile == "async_on" ? "ON" : "OFF")};");
            string table = "Stats_" + profile;
            await Exec(c, $"""
                CREATE TABLE dbo.{table}(id int NOT NULL CONSTRAINT PK_{table} PRIMARY KEY, value int NOT NULL);
                ;WITH n AS (SELECT 1 n UNION ALL SELECT n+1 FROM n WHERE n<1024)
                  INSERT dbo.{table} SELECT n,n FROM n OPTION(MAXRECURSION 1024);
                CREATE STATISTICS fixture_value ON dbo.{table}(value) WITH FULLSCAN;
                ;WITH n AS (SELECT 1025 n UNION ALL SELECT n+1 FROM n WHERE n<4096)
                  INSERT dbo.{table} SELECT n,n FROM n OPTION(MAXRECURSION 4096);
                UPDATE dbo.{table} SET value=value+1;
                """);
            // Fixture statistics creation precedes measurement; only plain unfiltered COUNT is the candidate.
            var beforeSettings = await Settings(c); var before = await Stats(c, table);
            Event("stats_before_count", new { profile, settings = beforeSettings, stats = before });
            await Exec(c, "SET TRANSACTION ISOLATION LEVEL READ COMMITTED; SET STATISTICS XML ON;");
            var plans = new List<string>(); long? count = null;
            double start = Clock.Elapsed.TotalMilliseconds;
            try {
                using var q = Command(c, $"SELECT COUNT_BIG(*) AS exact_rows FROM dbo.{table};");
                using var reader = await q.ExecuteReaderAsync(IoToken);
                do {
                    while (await reader.ReadAsync(IoToken)) {
                        if (reader.FieldCount == 1 && reader.GetName(0) == "exact_rows") count = reader.GetInt64(0);
                        else if (reader.FieldCount == 1 && reader.GetValue(0) is string xml && xml.Contains("ShowPlanXML")) plans.Add(xml);
                    }
                } while (await reader.NextResultAsync(IoToken));
            } finally { await Exec(c, "SET STATISTICS XML OFF;"); }
            double elapsed = Clock.Elapsed.TotalMilliseconds - start;
            Require(count == 4096, "stats probe exact count wrong");
            foreach (string xml in plans) File.WriteAllText($"/evidence/plan-{profile}.xml", xml);
            var usage = plans.SelectMany(x => XDocument.Parse(x).Descendants().Where(e => e.Name.LocalName == "OptimizerStatsUsage")
                .Select(e => e.ToString())).ToArray();
            Event("stats_count_plan", new { profile, count, elapsed_monotonic_ms = elapsed,
                plan_status = plans.Count > 0 ? "actual_plan_captured" : "unavailable",
                optimizer_stats_usage = usage, usage_status = usage.Length > 0 ? "present" : "not_present_or_plan_unavailable",
                auto_stats_xe = "not_collected; no event absence claim" });
            var windows = new List<object>();
            foreach (int delay in new[] { 0, 1000, 3000 }) {
                if (delay > 0) await Task.Delay(delay, IoToken);
                var stats = await Stats(c, table);
                var work = await Rows(c, "SELECT session_id,command,status,wait_type,wait_time FROM sys.dm_exec_requests WHERE database_id=DB_ID() AND session_id<>@@SPID;");
                windows.Add(new { elapsed_monotonic_ms = Clock.Elapsed.TotalMilliseconds - start, stats, observed_db_requests = work });
            }
            var after = await Stats(c, table);
            bool changed = JsonSerializer.Serialize(before) != JsonSerializer.Serialize(after);
            Event("stats_after_count", new { profile, settings = await Settings(c), before, after, recovery_windows = windows,
                conclusion = changed ? "statistics state changed in this bounded observation; correlate plan/time, not universal causality" : "update not observed under this schema/plan/state/window; not never",
                uncertainty = "async background may outlive request and observation window; XE not collected, no production cost certification" });
        }
        Event("stats_final_settings", await Settings(c));
        Event("stats_low_priority_final", await Rows(c, "SELECT name,value,value_for_secondary FROM sys.database_scoped_configurations WHERE name=N'ASYNC_STATS_UPDATE_WAIT_AT_LOW_PRIORITY';"));
    }
    public static async Task<int> Main(string[] args) {
        int exit = 1;
        try {
            Require(args.Length == 0, "no host/DSN/connection overrides accepted");
            using var master = Connection("master", "issue1-controller");
            double readyUntil = Clock.Elapsed.TotalSeconds + 90;
            while (true) {
                try { await master.OpenAsync(IoToken); break; }
                catch (SqlException e) {
                    Event("readiness_attempt", new { e.Number });
                    Require(Clock.Elapsed.TotalSeconds < readyUntil, "server readiness deadline");
                    await Task.Delay(1000, IoToken);
                }
            }
            await Preflight(master); await Fixture(master);
            using var admin = Connection(app: "issue1-control"); await admin.OpenAsync(IoToken);
            Require(Convert.ToInt32(await Scalar(admin, "SELECT is_read_committed_snapshot_on FROM sys.databases WHERE database_id=DB_ID();")) == 1, "RCSI disabled");
            foreach (string name in new[] { "server_error", "client_cancel", "command_timeout", "dispose_connection", "management_fallback" }) await Case(admin, master, name);
            await StatisticsProbe(admin);
            Result["completion"] = "passed"; Result["ownership"] = "passed_two_connection_and_three_mismatch_refusals";
            Result["transaction_release"] = "passed_owner_sessions_tx_ids_and_active_snapshots_absent";
            Result["recovery"] = "retention_release_passed_allocation_return_and_production_recovery_unverified";
            Result["precision"] = "passed_fixture_counts_only";
            exit = 0;
        } catch (Exception e) { Event("fatal", Errors(e)); Result["error"] = Errors(e); }
        finally {
            Result["events"] = Events; Result["exit_status"] = exit;
            Result["elapsed_monotonic_ms"] = Clock.Elapsed.TotalMilliseconds;
            Directory.CreateDirectory("/evidence");
            File.WriteAllText("/evidence/probe.json", JsonSerializer.Serialize(Result, new JsonSerializerOptions { WriteIndented = true }));
            Console.WriteLine($"CLIENT_FINAL_EXIT_STATUS={exit}");
        }
        return exit;
    }
}
