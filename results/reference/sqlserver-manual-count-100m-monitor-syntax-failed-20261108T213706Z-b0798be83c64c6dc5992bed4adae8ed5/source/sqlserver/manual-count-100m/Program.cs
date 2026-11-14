using System.Data;
using System.Diagnostics;
using System.Text.Json;
using System.Xml.Linq;
using Microsoft.Data.SqlClient;

internal static partial class Program
{
    const string Db = "ManualCount100m";
    
    static readonly CancellationTokenSource Total = new(TimeSpan.FromHours(6));
    static readonly JsonSerializerOptions Json = new() { WriteIndented = true };
    static readonly string Evidence = "/evidence";
    static int Serial;
    static readonly List<object> Receipts = new(), Conditions = new(), PrimaryStates = new();
    static readonly Dictionary<string, object?> Probe = new() {
        ["completion"]="incomplete", ["precision"]="unverified", ["transaction_release"]="unverified",
        ["service_budget"]="UNAVAILABLE_quiet_static_lab", ["conditions"]=Conditions, ["commits"]=Receipts,
        ["primary_state_observations"]=PrimaryStates,
        ["geometry"]=new { rows=Fixture.Rows, columns=Fixture.Columns, logical_bytes_per_row=404 },
        ["expected_conditions"]=Fixture.Cells, ["expected_count_requests"]=18,
        ["timing_scope"]="client monotonic; includes server + TCP/SqlClient overhead; not server-only",
        ["platform_scope"]="unsupported Linux amd64 on ARM Rosetta; diagnostic only; no native or cross-product speed rank"
    };
    sealed record Identity(string Server, string Database, int Spid, DateTime LoginTime, Guid ConnectionId, string Login, int Isolation);
    // During disconnect, a session can outlive dm_exec_connections. Retain its session/transaction state
    // with connection_id NULL; never treat the missing physical connection row alone as release.
    sealed record Observation(List<List<Dictionary<string,object?>>> Sets, long StartNs, long EndNs, List<List<Dictionary<string,object?>>>? PrimarySets=null);
    static long Ns() => (long)((decimal)Stopwatch.GetTimestamp()*1_000_000_000/Stopwatch.Frequency);
    static void Require(bool yes, string why) { if (!yes) throw new InvalidOperationException(why); }
    static string Sql(string name) => File.ReadAllText($"/app/sql/{name}.sql");
    static void Save(string path, object? value) {
        string full=Path.Combine(Evidence,path), temp=full+".tmp";
        Directory.CreateDirectory(Path.GetDirectoryName(full)!);
        using (var f=new FileStream(temp,FileMode.Create,FileAccess.Write,FileShare.None)) {
            JsonSerializer.Serialize(f,value,Json); f.Flush(true);
        }
        File.Move(temp,full,true);
    }
    static void Persist() => Save("probe.json",Probe);
    static SqlConnection Connection(string db=Db,bool secondary=false) => new(new SqlConnectionStringBuilder {
        DataSource=secondary?"tcp:secondary,1433":"tcp:server,1433", ApplicationIntent=secondary?ApplicationIntent.ReadOnly:ApplicationIntent.ReadWrite, InitialCatalog=db, UserID="sa", Password="ManualCount100m_Lab_2026!",
        Pooling=false, Enlist=false, ConnectRetryCount=0, ConnectTimeout=5, MultipleActiveResultSets=false,
        Encrypt=SqlConnectionEncryptOption.Mandatory, TrustServerCertificate=true
    }.ConnectionString);
    static async Task Open(SqlConnection c, CancellationToken token) {
        using var deadline=CancellationTokenSource.CreateLinkedTokenSource(token);
        deadline.CancelAfter(TimeSpan.FromSeconds(8)); await c.OpenAsync(deadline.Token);
    }
    static async Task<List<List<Dictionary<string,object?>>>> Read(SqlCommand cmd, CancellationToken token) {
        var sets=new List<List<Dictionary<string,object?>>>();
        await using var r=await cmd.ExecuteReaderAsync(token);
        do {
            var rows=new List<Dictionary<string,object?>>();
            while (await r.ReadAsync(token)) {
                var row=new Dictionary<string,object?>();
                for(int i=0;i<r.FieldCount;i++) row[r.GetName(i)]=r.IsDBNull(i)?null:r.GetValue(i);
                rows.Add(row);
            }
            sets.Add(rows);
        } while(await r.NextResultAsync(token));
        return sets;
    }
    static async Task<List<List<Dictionary<string,object?>>>> Query(SqlConnection c,string name,string sql,
        int seconds=15,CancellationToken? token=null,Action<SqlCommand>? parameters=null) {
        long start=Ns(); object? error=null; List<List<Dictionary<string,object?>>>? sets=null;
        using var deadline=CancellationTokenSource.CreateLinkedTokenSource(token??Total.Token);
        deadline.CancelAfter(TimeSpan.FromSeconds(seconds));
        try {
            using var cmd=new SqlCommand(sql,c){CommandTimeout=seconds}; parameters?.Invoke(cmd);
            sets=await Read(cmd,deadline.Token); return sets;
        } catch(Exception e) { error=new { type=e.GetType().Name,e.Message }; throw; }
        finally { long end=Ns(); Save($"raw/{++Serial:00000}-{name}.json",new { sql,start_ns=start,end_ns=end,elapsed_ns=end-start,raw=sets,error }); }
    }
    static async Task Exec(SqlConnection c,string name,string sql,int seconds=15,CancellationToken? token=null) =>
        _=await Query(c,name,sql,seconds,token);
    static Identity Id(Dictionary<string,object?> r) => new((string)r["server_name"]!, (string)r["db_name"]!,
        Convert.ToInt32(r["session_id"]), (DateTime)r["login_time"]!, (Guid)r["connection_id"]!,
        (string)r["login_name"]!, Convert.ToInt32(r["transaction_isolation_level"]));
    static async Task<Identity> Guard(SqlConnection c,string name,Fixture.Cell cell,Identity? expected=null) {
        var initial=await Query(c,name+"-manual-guard-before-prefix",Sql("guard"));
        Require(initial.Count==3 && initial[0].Count==1 && initial[1].Count==1 && initial[2].Count==1,"missing manual guard outputs");
        // A new C connection starts in RC. Apply exact manual prefix BEFORE freezing isolation=5.
        string branch=Sql("branch-"+cell.Route.ToLowerInvariant());
        Require(branch.EndsWith(cell.Sql+"\n"),"manual selected SQL boundary drift");
        await Exec(c,name+"-manual-prefix-"+cell.Route,branch[..^(cell.Sql.Length+1)]);
        var sets=await Query(c,name+"-manual-guard-final",Sql("guard"));
        Require(sets.Count==3 && sets[0].Count==1 && sets[1].Count==1 && sets[2].Count==1,"missing final manual guard");
        var id=Id(sets[0][0]); var table=sets[2][0];
        Require(id.Database==Db && id.Isolation==cell.Isolation && id.Server==(cell.Secondary?"countsecondary":"countprimary"),"actual connection route/isolation mismatch");
        Require(Convert.ToInt32(table["is_memory_optimized"])==0 && Convert.ToInt32(table["is_external"])==0 &&
            (string?)table["temporal_type_desc"]=="NON_TEMPORAL_TABLE","ordinary new table required");
        await State(c,name+"-actual-state",cell);
        if(cell.Secondary) {
            var role=await Query(c,name+"-manual-local-role",Sql("role"));
            Require(role.Count==1 && role[0].Count==1 && (string?)role[0][0]["role_desc"]=="SECONDARY" &&
                new[]{"ALL","READ_ONLY"}.Contains((string?)role[0][0]["secondary_role_allow_connections_desc"]),"actual readable local SECONDARY required");
        }
        if(expected!=null) Require(id==expected,"immutable connection identity changed");
        return id;
    }
    const string Match = """
        s.session_id=@sid AND s.login_time=@login_time AND s.login_name=@login
        AND @@SERVERNAME=@server
        """;
    static readonly string ObserveSql = """
        SELECT s.session_id,s.login_time,s.login_name,DB_NAME(s.database_id) db_name,s.transaction_isolation_level,s.open_transaction_count,
          (SELECT c.connection_id FROM sys.dm_exec_connections c WHERE c.session_id=s.session_id AND c.parent_connection_id IS NULL) connection_id
        FROM sys.dm_exec_sessions s WHERE
        """+Match+";\n"+
        "SELECT r.* FROM sys.dm_exec_requests r JOIN sys.dm_exec_sessions s ON r.session_id=s.session_id WHERE "+Match+";\n"+
        "SELECT l.* FROM sys.dm_tran_locks l JOIN sys.dm_exec_sessions s ON l.request_session_id=s.session_id WHERE "+Match+";\n"+
        "SELECT x.* FROM sys.dm_tran_active_snapshot_database_transactions x JOIN sys.dm_exec_sessions s ON x.session_id=s.session_id WHERE "+Match+";\n"+
        "SELECT x.*, a.transaction_begin_time,a.transaction_state FROM sys.dm_tran_session_transactions x JOIN sys.dm_exec_sessions s ON x.session_id=s.session_id LEFT JOIN sys.dm_tran_active_transactions a ON a.transaction_id=x.transaction_id WHERE "+Match+";\n"+
        StateSql;
    static void Params(SqlCommand q,Identity id) {
        q.Parameters.Add("@sid",SqlDbType.Int).Value=id.Spid;
        q.Parameters.Add("@login_time",SqlDbType.DateTime).Value=id.LoginTime;
        q.Parameters.Add("@cid",SqlDbType.UniqueIdentifier).Value=id.ConnectionId;
        q.Parameters.Add("@login",SqlDbType.NVarChar,128).Value=id.Login;
        q.Parameters.Add("@db",SqlDbType.NVarChar,128).Value=id.Database;
        q.Parameters.Add("@server",SqlDbType.NVarChar,128).Value=id.Server;
        q.Parameters.Add("@isolation",SqlDbType.Int).Value=id.Isolation;
    }
    static async Task<Observation> Observe(SqlConnection admin,Identity id,Fixture.Cell cell,CancellationToken token,SqlConnection? primaryAdmin=null) {
        using var limit=CancellationTokenSource.CreateLinkedTokenSource(token); limit.CancelAfter(TimeSpan.FromSeconds(5));
        using var cmd=new SqlCommand(ObserveSql,admin){CommandTimeout=5}; Params(cmd,id);
        long start=Ns(); var sets=await Read(cmd,limit.Token);
        Require(sets.Count==6 && sets[5].Count==1,"monitor metadata missing");
        ValidateState(sets[5][0],cell);
        var primarySets=cell.Secondary?await PrimaryStatic(token,primaryAdmin):null;
        if(sets[0].Count>0) Require(sets[0].Count==1 && (string?)sets[0][0]["db_name"]==id.Database &&
            Convert.ToInt32(sets[0][0]["transaction_isolation_level"])==id.Isolation &&
            (sets[0][0]["connection_id"]==null || (Guid)sets[0][0]["connection_id"]! == id.ConnectionId),"session database/isolation/physical connection changed");
        return new(sets,start,Ns(),primarySets);
    }
    static bool Idle(Observation o,bool disposed) => o.Sets[1].Count==0 && (!disposed || o.Sets[2].Count==0) &&
        o.Sets[3].Count==0 && o.Sets[4].Count==0 &&
        (disposed ? o.Sets[0].Count==0 : o.Sets[0].Count==1 && Convert.ToInt32(o.Sets[0][0]["open_transaction_count"])==0);
    static async Task Release(Identity id,Fixture.Cell cell,bool disposed,string name) {
        // Independent management deadline; never infer release from the admin session's own @@TRANCOUNT.
        using var budget=new CancellationTokenSource(TimeSpan.FromSeconds(30));
        using var admin=Connection(secondary:cell.Secondary); await Open(admin,budget.Token);
        var history=new List<Observation>();
        try {
            while(true) {
                var state=await Observe(admin,id,cell,budget.Token); history.Add(state);
                if(Idle(state,disposed)) return;
                await Task.Delay(500,budget.Token);
            }
        } finally { Save($"cleanup/{name}.json",new { id,disposed,sql=ObserveSql,primary_sql=cell.Secondary?Sql("ag-primary-state"):null,observations=history }); }
    }
    const string BufferSql = """
        SELECT COUNT_BIG(*) AS cached_target_pages,
               COALESCE(SUM(CONVERT(bigint,b.is_modified)),0) AS dirty_target_pages
        FROM sys.dm_os_buffer_descriptors b
        JOIN sys.allocation_units a ON a.allocation_unit_id=b.allocation_unit_id
        JOIN sys.partitions p
          ON (a.type IN (1,3) AND a.container_id=p.hobt_id)
          OR (a.type=2 AND a.container_id=p.partition_id)
        WHERE b.database_id=DB_ID() AND p.object_id=OBJECT_ID(N'dbo.Target',N'U');
        """;
    sealed record ColdProof(long cached_target_pages,long dirty_target_pages,bool verified,string method);
    static async Task<ColdProof> ColdBufferReset(SqlConnection admin,string name,Fixture.Cell cell) {
        // Instance-wide DBCC is ONLY for these exclusively owned lab instances.
        await State(admin,name+"-before-reset-state",cell);
        if(cell.Secondary) {
            await PrimaryStatic(Total.Token);
            var dirty=await Query(admin,name+"-secondary-dirty-proof",BufferSql,30);
            Require(dirty.Count==1 && dirty[0].Count==1 && Convert.ToInt64(dirty[0][0]["dirty_target_pages"])==0,
                "secondary dirty target pages: cold reset unproven; CHECKPOINT not assumed writable");
            await Exec(admin,name+"-cold-buffer-reset","DBCC DROPCLEANBUFFERS WITH NO_INFOMSGS;",120);
        } else {
            await Exec(admin,name+"-checkpoint","CHECKPOINT;",120);
            await Exec(admin,name+"-cold-buffer-reset","DBCC DROPCLEANBUFFERS WITH NO_INFOMSGS;",120);
        }
        var state=await Query(admin,name+"-cold-buffer-proof",BufferSql,30);
        Require(state.Count==1 && state[0].Count==1 &&
            Convert.ToInt64(state[0][0]["cached_target_pages"])==0 && Convert.ToInt64(state[0][0]["dirty_target_pages"])==0,
            "target DB data buffers not proven empty");
        await State(admin,name+"-after-reset-state",cell);
        return new(Convert.ToInt64(state[0][0]["cached_target_pages"]),Convert.ToInt64(state[0][0]["dirty_target_pages"]),true,cell.Secondary?"secondary_clean_proof_then_DBCC":"CHECKPOINT_then_DBCC");
    }
    static async Task Sample(SqlConnection c,Identity id,Fixture.Cell cell,int sample) {
        string condition=cell.Name, name=$"{condition}-{sample}";
        await Guard(c,name,cell,id);
        using var admin=Connection(secondary:cell.Secondary); await Open(admin,Total.Token);
        using SqlConnection? primaryAdmin=cell.Secondary?Connection("master"):null;
        if(primaryAdmin!=null) await Open(primaryAdmin,Total.Token);
        var cold=await ColdBufferReset(admin,name,cell); // Every sample, including first and post-index, outside COUNT timing.
        var observations=new List<Observation>();
        using var monitoring=new CancellationTokenSource();
        using var countBudget=CancellationTokenSource.CreateLinkedTokenSource(Total.Token);
        countBudget.CancelAfter(TimeSpan.FromMinutes(20));
        using var command=new SqlCommand(cell.Sql,c){CommandTimeout=1200};
        object? error=null; long? value=null,firstNs=null,fullNs=null; bool completed=false,unknown=false;
        // No logging, metadata, process startup, or monitor SQL on the count connection in this timer.
        long start=Ns();
        async Task Count() {
            await using var reader=await command.ExecuteReaderAsync(countBudget.Token);
            Require(await reader.ReadAsync(countBudget.Token),"missing scalar");
            firstNs=Ns()-start; Require(reader.FieldCount==1 && reader.GetName(0)==(cell.Route=="D"?"observed_rows":"exact_rows") && !reader.IsDBNull(0),"invalid scalar shape");
            value=reader.GetInt64(0);
            Require(!await reader.ReadAsync(countBudget.Token),"extra scalar row");
            Require(!await reader.NextResultAsync(countBudget.Token),"unexpected extra result");
            await reader.DisposeAsync(); fullNs=Ns()-start; completed=true;
        }
        Task count=Count();
        async Task Monitor() {
            while(!monitoring.IsCancellationRequested && !count.IsCompleted) {
                var observation=await Observe(admin,id,cell,Total.Token,primaryAdmin);
                observations.Add(observation); Require(observation.Sets[0].Count==1 && observation.Sets[0][0]["connection_id"]!=null,"count physical connection disappeared");
                await Task.Delay(1000,monitoring.Token);
            }
        }
        Task monitor=Monitor();
        try {
            Task watchdog=Task.Delay(TimeSpan.FromMinutes(20));
            Task winner=await Task.WhenAny(count,monitor,watchdog);
            if(winner==watchdog) throw new TimeoutException("count wall deadline exhausted");
            if(winner==monitor && monitor.IsFaulted) await monitor;
            await count;
            Require(value==Fixture.Rows,"count differs from committed truth");
        } catch(Exception e) {
            error=new {type=e.GetType().Name,e.Message};
            try { command.Cancel(); } catch(Exception cancel) { error=new { original=error,cancel=cancel.Message }; }
            try { await count.WaitAsync(TimeSpan.FromSeconds(15)); } catch(TimeoutException) {unknown=true;}
            catch(Exception) { /* primary error and incomplete outcome retained */ }
            throw;
        } finally {
            monitoring.Cancel();
            try { await monitor; } catch(OperationCanceledException) { }
            catch(Exception e) { error ??= new {type=e.GetType().Name,e.Message}; }
            var record=new { condition,sample,id,sql=cell.Sql,route=cell.Route,index=cell.Index,precision=cell.Route=="D"?"observed_rows_quiet_match_only":"exact_rows_static_fixture",start_ns=start,first_result_ns=firstNs,
                full_completion_ns=fullNs,value,completed,completion_unknown=unknown,error,cold_buffer_reset_verified=true,cold_buffer_proof=cold,
                accepted=completed && error==null && value==Fixture.Rows && firstNs>0 && fullNs>=firstNs };
            Save($"samples/{name}.json",record);
            Save($"monitor/{name}.json",new { id,sql=ObserveSql,primary_sql=cell.Secondary?Sql("ag-primary-state"):null,interval_ms=1000,observations });
            Samples[Array.IndexOf(Fixture.Conditions,condition)*3+sample-1]=record; Persist();
            if(unknown) { await c.DisposeAsync().AsTask().WaitAsync(TimeSpan.FromSeconds(15)); throw new InvalidOperationException("completion unknown: stop experiment and server cleanup required"); }
            await Release(id,cell,false,name+"-after-sample");
            Require(error==null,"failed sample; experiment stopped");
        }
    }
    static readonly List<object> Samples=Fixture.Conditions.SelectMany(condition=>Enumerable.Range(1,3).Select(sample=>(object)new { condition,sample,status="pending",completed=false,accepted=false })).ToList();
    static string? CompositeStructure;
    static async Task Metadata(SqlConnection c,Fixture.Cell cell) {
        string condition=cell.Name;
        await Query(c,condition+"-inventory",Sql("inventory"));
        await Guard(c,condition,cell);
        string[] batches=System.Text.RegularExpressions.Regex.Split(Sql("showplan").Trim(),@"(?m)^GO\s*$")
            .Select(s=>s.Trim()).Where(s=>s.Length>0).ToArray();
        Require(batches.Length==3 && batches[1]=="SELECT COUNT_BIG(*) AS exact_rows FROM [dbo].[Target];","exact SHOWPLAN batch boundaries");
        batches[1]=cell.Sql; // D SHOWPLAN uses its exact hinted selected SQL.
        List<List<Dictionary<string,object?>>>? plan=null;
        try { await Exec(c,condition+"-showplan-on",batches[0]); plan=await Query(c,condition+"-estimated-plan",batches[1]); }
        finally {
            using var cleanup=new CancellationTokenSource(TimeSpan.FromSeconds(15));
            await Exec(c,condition+"-showplan-off",batches[2],10,cleanup.Token);
        }
        Require(plan!=null && plan.Count==1 && plan[0].Count==1,"estimated plan unavailable");
        string xml=(string)plan![0][0].Values.Single()!;
        File.WriteAllText(Path.Combine(Evidence,condition+"-estimated.sqlplan"),xml);
        var doc=XDocument.Parse(xml); XNamespace ns="http://schemas.microsoft.com/sqlserver/2004/07/showplan";
        var scans=doc.Descendants(ns+"RelOp").Where(x=>((string?)x.Attribute("PhysicalOp"))?.Contains("Scan")==true)
            .SelectMany(x=>x.Descendants(ns+"Object")).Select(x=>new { table=(string?)x.Attribute("Table"),index=(string?)x.Attribute("Index") }).Distinct().ToArray();
        var indexes=await Query(c,condition+"-index-catalog",Sql("indexes"));
        var keys=await Query(c,condition+"-index-keys", """
            SELECT i.index_id,i.name index_name,ic.index_column_id,ic.key_ordinal,ic.is_included_column,c.name column_name
            FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
            JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
            WHERE i.object_id=OBJECT_ID(N'dbo.Target',N'U') ORDER BY i.index_id,ic.index_column_id;
            """);
        var columns=await Query(c,condition+"-stored-columns", "SELECT column_id,name,system_type_id,max_length,is_nullable,is_computed FROM sys.columns WHERE object_id=OBJECT_ID(N'dbo.Target') ORDER BY column_id;");
        Require(columns.Count==1 && columns[0].Count==100 && columns[0].All(r=>!Convert.ToBoolean(r["is_computed"])),"stored column geometry changed");
        string structure=Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(
            System.Text.Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new { columns,indexes=indexes.SelectMany(s=>s).OrderBy(r=>Convert.ToInt32(r["index_id"])).ToArray(),keys })))).ToLowerInvariant();
        if(cell.Index=="composite") {
            Require(CompositeStructure==null || CompositeStructure==structure,"common composite structure changed across DB-state cases");
            CompositeStructure ??= structure;
        }
        var targets=scans.Where(s=>s.table=="[Target]").Select(s=>s.index?.Trim('[',']')).Distinct().ToArray();
        object? pages=null; string? unavailable=null;
        if(targets.Length==1) {
            var matched=indexes.SelectMany(s=>s).Where(r=>(string?)r["index_name"]==targets[0]).ToArray();
            if(matched.Length==1) {
                int idx=Convert.ToInt32(matched[0]["index_id"]);
                string sql=Sql("pages").Replace("@index_id int = 1;",$"@index_id int = {idx};");
                try {
                    var raw=await Query(c,condition+"-planned-index-pages",sql);
                    if(raw.Count==1 && raw[0].Count==1 && raw[0][0]["used_pages"]!=null && raw[0][0]["reserved_pages"]!=null)
                        pages=new { index_id=idx,raw,used_bytes=Convert.ToInt64(raw[0][0]["used_pages"])*8192L,
                            reserved_bytes=Convert.ToInt64(raw[0][0]["reserved_pages"])*8192L,scope="capacity reference; not scan IO or latency bound" };
                    else unavailable="missing/NULL page metadata; unknown";
                } catch(SqlException e) { unavailable="page metadata unavailable: "+e.Message; }
            } else unavailable="planned index catalog match unavailable";
        } else unavailable="unique planned scan index unavailable";
        var parallel=doc.Descendants(ns+"RelOp").Select(x=>(string?)x.Attribute("Parallel")).Where(x=>x!=null).ToArray();
        var actualState=await State(c,condition+"-metadata-actual-state",cell);
        Conditions.Add(new { condition,actual_state=actualState,route=cell.Route,index=cell.Index,selected_sql=cell.Sql,structure_sha256=structure,planned_scans=scans,planned_parallel=parallel.Length==0?(bool?)null:parallel.Any(x=>x=="1" || x=="true"),parallel_attributes=parallel,pages,unavailable });
        Persist();
    }
    static async Task FixtureSetup() {
        using var master=Connection("master");
        long ready=Ns();
        while(true) {
            try { await Open(master,Total.Token); break; }
            catch(SqlException) when(Ns()-ready<120_000_000_000L) { await Task.Delay(1000,Total.Token); }
        }
        await Exec(master,"server-config", """
            EXEC sys.sp_configure 'show advanced options',1; RECONFIGURE;
            EXEC sys.sp_configure 'max server memory (MB)',2048; RECONFIGURE;
            ALTER DATABASE tempdb MODIFY FILE (NAME=tempdev,MAXSIZE=8192MB);
            ALTER DATABASE tempdb MODIFY FILE (NAME=templog,MAXSIZE=4096MB);
            CREATE DATABASE ManualCount100m ON PRIMARY
              (NAME=ManualCount100m_data,FILENAME='/var/opt/mssql/data/ManualCount100m.mdf',SIZE=64MB,MAXSIZE=81920MB,FILEGROWTH=256MB)
              LOG ON (NAME=ManualCount100m_log,FILENAME='/var/opt/mssql/log/ManualCount100m.ldf',SIZE=64MB,MAXSIZE=4096MB,FILEGROWTH=64MB);
            ALTER DATABASE ManualCount100m SET RECOVERY SIMPLE;
            ALTER DATABASE ManualCount100m SET READ_COMMITTED_SNAPSHOT ON;
            ALTER DATABASE ManualCount100m SET ALLOW_SNAPSHOT_ISOLATION OFF;
            """,120);
        await ServerIdentity(master,false,Total.Token);
        using var c=Connection(); await Open(c,Total.Token);
        await Exec(c,"create-table",Fixture.TableSql);
        var settings=await Query(c,"actual-server-settings", """
            SELECT @@VERSION version,SERVERPROPERTY('ProductVersion') product_version,SERVERPROPERTY('Edition') edition;
            SELECT name,value,value_in_use FROM sys.configurations WHERE name IN ('max server memory (MB)','max degree of parallelism');
            SELECT name,recovery_model_desc,is_read_committed_snapshot_on,snapshot_isolation_state_desc FROM sys.databases;
            SELECT DB_NAME(database_id) db_name,name,physical_name,size,max_size,growth FROM sys.master_files;
            SELECT * FROM sys.security_predicates WHERE target_object_id=OBJECT_ID(N'dbo.Target');
            SELECT c.column_id,c.name,t.name type_name,c.max_length,c.is_nullable FROM sys.columns c
            JOIN sys.types t ON t.user_type_id=c.user_type_id WHERE c.object_id=OBJECT_ID(N'dbo.Target') ORDER BY c.column_id;
            """);
        Require(settings.Count==6 && settings[4].Count==0 && settings[5].Count==100,"new fixture must have 100 stored columns and no RLS predicates");
        for(int column=0;column<100;column++) {
            var r=settings[5][column];
            Require((string?)r["name"]==(column==0?"id":$"c{column:000}") &&
                (string?)r["type_name"]==(column==0?"bigint":"int") &&
                Convert.ToInt32(r["max_length"])==(column==0?8:4) && Convert.ToBoolean(r["is_nullable"])==false,
                "stored column geometry mismatch");
        }
        Require(settings[3].All(r=>((string)r["physical_name"]!).StartsWith("/var/opt/mssql/")),"all system/data/log/tempdb files must be host-bound");
        Require(settings[1].Any(r=>(string?)r["name"]=="max server memory (MB)" && Convert.ToInt32(r["value_in_use"])==2048),"server memory cap mismatch");
        long start=Ns(),committed=0;
        using var loadBudget=CancellationTokenSource.CreateLinkedTokenSource(Total.Token);
        loadBudget.CancelAfter(TimeSpan.FromHours(2));
        for(int batch=0;batch<Fixture.Batches;batch++) {
            Require(Ns()-start<7_200_000_000_000L,"2h load deadline exhausted");
            long before=Ns();
            var raw=await Query(c,$"load-{batch:0000}",Fixture.InsertSql(batch),120,loadBudget.Token);
            Require(raw.Count==1 && raw[0].Count==1,"missing committed receipt"); var receipt=raw[0][0];
            Require(Convert.ToInt64(receipt["committed_rows"])==Fixture.BatchRows && Convert.ToInt32(receipt["transaction_count"])==0 &&
                Convert.ToInt64(receipt["first_id"])==(long)batch*Fixture.BatchRows+1 &&
                Convert.ToInt64(receipt["last_id"])==(long)(batch+1)*Fixture.BatchRows,"invalid commit receipt");
            committed+=Fixture.BatchRows;
            Receipts.Add(new { batch,raw,start_ns=before,end_ns=Ns(),committed_total=committed }); Persist();
            if(committed%5_000_000==0) {
                double elapsed=(Ns()-start)/1e9,rate=committed/elapsed;
                Console.WriteLine(JsonSerializer.Serialize(new { committed,elapsed_seconds=elapsed,rows_per_second=rate,eta_seconds=(Fixture.Rows-committed)/rate }));
                Console.Out.Flush();
            }
        }
        Require(committed==Fixture.Rows && Receipts.Count==1000,"incomplete load");
    }
    public static async Task<int> Main(string[] args) {
        Directory.CreateDirectory(Evidence); Probe["samples"]=Samples; Persist();
        try {
            Require(args.Length==0,"fixed all only; no driver arguments");
            await FixtureSetup();
            foreach(var cell in Fixture.Cells) {
                string condition=cell.Name;
                if(cell.Route=="C" || cell.Route=="D") await Transition(cell);
                if(cell.Route=="A") await SetupAg();
                using var c=Connection(secondary:cell.Secondary); await Open(c,Total.Token); Identity? id=null;
                try {
                    if(cell.Route=="B" && cell.Index!="clustered") {
                        Console.WriteLine("INDEX_CAPACITY_GATE "+cell.Index); Console.Out.Flush();
                        string gate=Path.Combine(Evidence,"index-gate-"+cell.Index);
                        using var deadline=CancellationTokenSource.CreateLinkedTokenSource(Total.Token); deadline.CancelAfter(TimeSpan.FromSeconds(30));
                        while(!File.Exists(gate)) await Task.Delay(200,deadline.Token);
                        await Exec(c,condition+"-fixture-index-ddl",Fixture.IndexSql(cell.Index),7200);
                    }
                    id=await Guard(c,condition+"-count-session",cell);
                    await Metadata(c,cell);
                    for(int sample=1;sample<=3;sample++) {
                        try { await Sample(c,id,cell,sample); }
                        catch(Exception e) {
                            int slot=Array.IndexOf(Fixture.Conditions,condition)*3+sample-1;
                            if(JsonSerializer.SerializeToElement(Samples[slot]).TryGetProperty("status",out _)) {
                                Samples[slot]=new { condition,sample,route=cell.Route,sql=cell.Sql,status="failed_before_count",completed=false,accepted=false,
                                    completion_unknown=false,cold_buffer_reset_verified=false,error=e.Message };
                                Persist();
                            }
                            throw;
                        }
                    }
                } finally {
                    await c.DisposeAsync().AsTask().WaitAsync(TimeSpan.FromSeconds(15));
                    if(id!=null) await Release(id,cell,true,condition+"-after-dispose");
                }
            }
            Require(Samples.Count==18 && Conditions.Count==6 && Samples.All(s=>JsonSerializer.SerializeToElement(s).GetProperty("accepted").GetBoolean()),"missing/failed condition or sample");
            Probe["completion"]="passed_all_18_commands"; Probe["precision"]="18_static_matches_1000_receipts_D_not_snapshot_accuracy_under_writes";
            Probe["transaction_release"]="passed_each_sample_and_disposed_condition"; Persist(); return 0;
        } catch(Exception e) {
            Probe["error"]=new {type=e.GetType().Name,e.Message}; Persist(); Console.Error.WriteLine(e); return 1;
        } finally { Total.Dispose(); }
    }
}
