using Microsoft.Data.SqlClient;

internal static partial class Program
{
    const string StateSql = """
        SELECT d.name,d.is_read_committed_snapshot_on,d.snapshot_isolation_state,d.snapshot_isolation_state_desc,
          sys.fn_hadr_is_primary_replica(d.name) is_ag_primary,
          SERVERPROPERTY('IsHadrEnabled') is_hadr_enabled,@@SERVERNAME server_name,
          rs.role_desc,ar.secondary_role_allow_connections_desc,rs.connected_state_desc,
          ds.synchronization_state_desc,ds.synchronization_health_desc,ds.is_suspended,
          ds.redo_queue_size,ds.last_redone_lsn,ds.last_hardened_lsn,ds.last_commit_time,
          (SELECT COUNT(*) FROM sys.dm_exec_requests r JOIN sys.dm_exec_sessions s ON s.session_id=r.session_id
           WHERE s.is_user_process=1 AND r.database_id=d.database_id AND r.command IN
             ('INSERT','UPDATE','DELETE','MERGE','BULK INSERT','SELECT INTO')) active_user_writes
        FROM sys.databases d
        LEFT JOIN sys.dm_hadr_database_replica_states ds ON ds.database_id=d.database_id AND ds.is_local=1
        LEFT JOIN sys.dm_hadr_availability_replica_states rs ON rs.replica_id=ds.replica_id AND rs.group_id=ds.group_id AND rs.is_local=1
        LEFT JOIN sys.availability_replicas ar ON ar.replica_id=ds.replica_id
        WHERE d.database_id=DB_ID();
        """;
    static void ValidateState(Dictionary<string,object?> r,Fixture.Cell cell) {
        Require(Convert.ToInt32(r["is_read_committed_snapshot_on"])==(cell.Route=="B"?1:0),"wrong actual RCSI for selected route");
        Require(Convert.ToInt32(r["snapshot_isolation_state"])==(cell.Route=="C"?1:0),"wrong actual SNAPSHOT state for selected route");
        Require(Convert.ToInt32(r["active_user_writes"])==0,"unexpected writes in static fixture");
        if(!cell.Secondary) {
            Require(r["is_ag_primary"]==null && r["role_desc"]==null,"non-AG route required; no route fallback");
        } else {
            Require(r["is_ag_primary"]!=null && Convert.ToInt32(r["is_ag_primary"])==0 &&
                (string?)r["role_desc"]=="SECONDARY" && (string?)r["server_name"]=="countsecondary" &&
                new[]{"ALL","READ_ONLY"}.Contains((string?)r["secondary_role_allow_connections_desc"]) &&
                (string?)r["connected_state_desc"]=="CONNECTED" &&
                (string?)r["synchronization_state_desc"]=="SYNCHRONIZED" &&
                (string?)r["synchronization_health_desc"]=="HEALTHY" &&
                r["redo_queue_size"]!=null && Convert.ToInt64(r["redo_queue_size"])==0 &&
                r["is_suspended"]!=null && !Convert.ToBoolean(r["is_suspended"]),
                "A blocker: actual healthy readable SECONDARY with zero redo required");
        }
    }
    static async Task<Dictionary<string,object?>> State(SqlConnection c,string name,Fixture.Cell cell) {
        var sets=await Query(c,name,StateSql);
        Require(sets.Count==1 && sets[0].Count==1,"missing route state metadata");
        ValidateState(sets[0][0],cell);
        return sets[0][0];
    }
    static async Task PrimaryStatic(CancellationToken token) {
        using var c=Connection("master"); await Open(c,token);
        var sets=await Query(c,"ag-primary-static",Sql("ag-primary-state"),5,token);
        Require(sets.Count==2 && sets[0].Count==1 && sets[1].Count==1,"AG primary/remote state unavailable");
        var p=sets[0][0]; var s=sets[1][0];
        Require((string?)p["server_name"]=="countprimary" && p["is_ag_primary"]!=null &&
            Convert.ToInt32(p["is_ag_primary"])==1 && Convert.ToInt32(p["active_user_writes"])==0 &&
            Convert.ToInt32(p["active_user_transactions"])==0 &&
            (string?)s["synchronization_state_desc"]=="SYNCHRONIZED" &&
            (string?)s["synchronization_health_desc"]=="HEALTHY" &&
            s["log_send_queue_size"]!=null && Convert.ToInt64(s["log_send_queue_size"])==0 &&
            s["redo_queue_size"]!=null && Convert.ToInt64(s["redo_queue_size"])==0 &&
            !Convert.ToBoolean(s["is_suspended"]),"A blocker: primary/replication changed or static state unproven");
    }
    static async Task NoDatabaseSessions(SqlConnection master,string name) {
        using var wait=CancellationTokenSource.CreateLinkedTokenSource(Total.Token); wait.CancelAfter(TimeSpan.FromSeconds(120));
        while(true) {
            var sets=await Query(master,name, """
                SELECT s.session_id,s.login_time,s.login_name,s.database_id
                FROM sys.dm_exec_sessions s WHERE s.is_user_process=1 AND s.database_id=DB_ID(N'ManualCount100m');
                SELECT st.session_id,dt.transaction_id FROM sys.dm_tran_database_transactions dt
                JOIN sys.dm_tran_session_transactions st ON st.transaction_id=dt.transaction_id
                WHERE dt.database_id=DB_ID(N'ManualCount100m') AND st.is_user_transaction=1;
                SELECT x.* FROM sys.dm_tran_active_snapshot_database_transactions x
                WHERE EXISTS(SELECT 1 FROM sys.dm_tran_database_transactions dt
                  WHERE dt.transaction_id=x.transaction_id AND dt.database_id=DB_ID(N'ManualCount100m'));
                """,5,wait.Token);
            Require(sets.Count==3,"missing previous session/transaction/snapshot absence proof");
            if(sets.All(s=>s.Count==0)) return;
            await Task.Delay(1000,wait.Token);
        }
    }
    static async Task Transition(Fixture.Cell cell) {
        using var c=Connection("master"); await Open(c,Total.Token);
        await NoDatabaseSessions(c,"transition-"+cell.Route+"-absence");
        const string actual="SELECT name,is_read_committed_snapshot_on,snapshot_isolation_state,snapshot_isolation_state_desc FROM sys.databases WHERE name=N'ManualCount100m';";
        await Query(c,"transition-"+cell.Route+"-before",actual);
        string sql=cell.Route=="C" ?
            "ALTER DATABASE ManualCount100m SET READ_COMMITTED_SNAPSHOT OFF; ALTER DATABASE ManualCount100m SET ALLOW_SNAPSHOT_ISOLATION ON;" :
            "ALTER DATABASE ManualCount100m SET ALLOW_SNAPSHOT_ISOLATION OFF;";
        await Exec(c,"transition-"+cell.Route+"-ddl",sql,120);
        using var wait=CancellationTokenSource.CreateLinkedTokenSource(Total.Token); wait.CancelAfter(TimeSpan.FromSeconds(120));
        while(true) {
            var sets=await Query(c,"transition-"+cell.Route+"-after",actual,5,wait.Token);
            Require(sets.Count==1 && sets[0].Count==1,"missing transition state");
            if(Convert.ToInt32(sets[0][0]["is_read_committed_snapshot_on"])==0 &&
                Convert.ToInt32(sets[0][0]["snapshot_isolation_state"])==(cell.Route=="C"?1:0)) break;
            await Task.Delay(1000,wait.Token);
        }
    }
    static async Task ServerIdentity(SqlConnection c,bool secondary,CancellationToken token) {
        var sets=await Query(c,"ag-server-identity", """
            SELECT @@SERVERNAME server_name,SERVERPROPERTY('IsHadrEnabled') is_hadr_enabled,
              @@VERSION version,SERVERPROPERTY('ProductVersion') product_version,SERVERPROPERTY('Edition') edition;
            SELECT DB_NAME(database_id) db_name,name,physical_name,size,max_size,growth FROM sys.master_files;
            SELECT name,value,value_in_use FROM sys.configurations WHERE name IN ('max server memory (MB)','max degree of parallelism');
            """,15,token);
        Require(sets.Count==3 && sets[0].Count==1 &&
            (string?)sets[0][0]["server_name"]==(secondary?"countsecondary":"countprimary") &&
            sets[0][0]["is_hadr_enabled"]!=null && Convert.ToInt32(sets[0][0]["is_hadr_enabled"])==1,
            "A blocker: HADR not enabled or actual server name differs from AG replica name");
        Require(sets[1].Count>0 && sets[1].All(r=>((string)r["physical_name"]!).StartsWith("/var/opt/mssql/")),"unbound SQL file path");
        Require(sets[2].Any(r=>(string?)r["name"]=="max server memory (MB)" && Convert.ToInt32(r["value_in_use"])==3072),"actual memory cap mismatch");
    }
    static async Task SetupAg() {
        // Controller owns actual secondary start and Rosetta proof; no fake role or writer fallback.
        Console.WriteLine("AG_SECONDARY_GATE"); Console.Out.Flush();
        using var copy=CancellationTokenSource.CreateLinkedTokenSource(Total.Token); copy.CancelAfter(TimeSpan.FromHours(2));
        using(var gate=CancellationTokenSource.CreateLinkedTokenSource(copy.Token)) {
            gate.CancelAfter(TimeSpan.FromSeconds(120));
            while(!File.Exists(Path.Combine(Evidence,"ag-secondary-ready"))) await Task.Delay(200,gate.Token);
        }
        var gateReceipt=System.Text.Json.JsonSerializer.Deserialize<Dictionary<string,System.Text.Json.JsonElement>>(
            File.ReadAllText(Path.Combine(Evidence,"ag-secondary-ready")))!;
        Require(gateReceipt["secondary_rosetta_verified"].GetBoolean() && gateReceipt["mounts_verified"].GetBoolean() &&
            !string.IsNullOrWhiteSpace(gateReceipt["secondary_container_id"].GetString()) &&
            gateReceipt["owner"].GetString()?.Length==32 && gateReceipt["free_bytes"].GetInt64()>=70L*1024*1024*1024,
            "A blocker: incomplete owned secondary start/Rosetta/capacity gate");
        Probe["secondary_gate"]=gateReceipt; Persist();
        using var primary=Connection("master"); await Open(primary,copy.Token);
        await NoDatabaseSessions(primary,"ag-before-copy-absence");
        await ServerIdentity(primary,false,copy.Token);
        using var secondary=Connection("master",true);
        using(var ready=CancellationTokenSource.CreateLinkedTokenSource(copy.Token)) {
            ready.CancelAfter(TimeSpan.FromSeconds(120));
            while(true) {
                try { await Open(secondary,ready.Token); break; }
                catch(SqlException) { await Task.Delay(1000,ready.Token); }
            }
        }
        await Exec(secondary,"secondary-config", """
            EXEC sys.sp_configure 'show advanced options',1; RECONFIGURE;
            EXEC sys.sp_configure 'max server memory (MB)',3072; RECONFIGURE;
            ALTER DATABASE tempdb MODIFY FILE (NAME=tempdev,MAXSIZE=8192MB);
            ALTER DATABASE tempdb MODIFY FILE (NAME=templog,MAXSIZE=4096MB);
            """,120,copy.Token);
        await ServerIdentity(secondary,true,copy.Token);
        foreach(var pair in new[]{(primary,"primary"),(secondary,"secondary")}) {
            await Exec(pair.Item1,"endpoint-own-"+pair.Item2,Sql("ag-endpoint").Replace("__NODE__",pair.Item2),60,copy.Token);
        }
        foreach(var pair in new[]{(primary,"secondary"),(secondary,"primary")}) {
            await Exec(pair.Item1,"endpoint-peer-"+pair.Item2,Sql("ag-peer").Replace("__PEER__",pair.Item2),60,copy.Token);
        }
        await Query(primary,"ag-before-recovery-setting","SELECT name,recovery_model_desc FROM sys.databases WHERE name=N'ManualCount100m';",15,copy.Token);
        await Exec(primary,"ag-create-and-backup",Sql("ag-primary-setup"),7200,copy.Token);
        var backups=await Query(primary,"ag-backup-identity",Sql("ag-backup-proof"),30,copy.Token);
        Require(backups.Count==1 && backups[0].Count==2 && backups[0].All(r=>
            Convert.ToBoolean(r["has_backup_checksums"]) && Convert.ToInt64(r["compressed_backup_size"])>0 &&
            Convert.ToInt64(r["compressed_backup_size"])<Convert.ToInt64(r["backup_size"])),"compressed checksum full+log backup proof missing");
        Require((string?)backups[0][0]["type"]=="D" && (string?)backups[0][1]["type"]=="L" &&
            backups[0][0]["family_guid"]!=null && Equals(backups[0][0]["family_guid"],backups[0][1]["family_guid"]) &&
            Equals(backups[0][0]["database_guid"],backups[0][1]["database_guid"]) &&
            Convert.ToDecimal(backups[0][1]["database_backup_lsn"])==Convert.ToDecimal(backups[0][0]["checkpoint_lsn"]),
            "full/log identity or backup chain mismatch");
        var files=await Query(secondary,"ag-restore-filelist","RESTORE FILELISTONLY FROM DISK=N'/backup/fixture-full.bak';",30,copy.Token);
        Require(files.Count==1 && files[0].Count==2 && files[0].Select(r=>(string?)r["LogicalName"]).OrderBy(n=>n)
            .SequenceEqual(new[]{"ManualCount100m_data","ManualCount100m_log"}),"unexpected backup physical/logical file geometry");
        await Query(secondary,"ag-restore-header","RESTORE HEADERONLY FROM DISK=N'/backup/fixture-full.bak'; RESTORE HEADERONLY FROM DISK=N'/backup/fixture-log.trn';",30,copy.Token);
        await Exec(secondary,"ag-manual-restore-join",Sql("ag-secondary-setup"),7200,copy.Token);
        // Both the stage and each polling request have independent finite bounds.
        using var sync=CancellationTokenSource.CreateLinkedTokenSource(copy.Token); sync.CancelAfter(TimeSpan.FromSeconds(120));
        while(true) {
            sync.Token.ThrowIfCancellationRequested();
            var state=await Query(secondary,"ag-sync-wait", "SELECT synchronization_state_desc,synchronization_health_desc,redo_queue_size,is_suspended FROM sys.dm_hadr_database_replica_states WHERE is_local=1 AND database_id=DB_ID(N'ManualCount100m');",5,sync.Token);
            if(state.Count==1 && state[0].Count==1 && (string?)state[0][0]["synchronization_state_desc"]=="SYNCHRONIZED" &&
                (string?)state[0][0]["synchronization_health_desc"]=="HEALTHY" && state[0][0]["redo_queue_size"]!=null &&
                Convert.ToInt64(state[0][0]["redo_queue_size"])==0 && !Convert.ToBoolean(state[0][0]["is_suspended"])) break;
            await Task.Delay(1000,sync.Token);
        }
        await Query(primary,"ag-final-topology",Sql("ag-topology"),30,copy.Token);
        await Query(secondary,"ag-secondary-topology",Sql("ag-topology"),30,copy.Token);
        await PrimaryStatic(copy.Token);
        using var readable=Connection(secondary:true); await Open(readable,copy.Token);
        await State(readable,"ag-readable-start",Fixture.Cells[^1]);
        var restored=await Query(readable,"ag-restored-files","SELECT name,physical_name,size,max_size,growth FROM sys.database_files; SELECT database_guid,family_guid,recovery_fork_guid FROM sys.database_recovery_status WHERE database_id=DB_ID();",15,copy.Token);
        Require(restored.Count==2 && restored[0].Count==2 && restored[1].Count==1 &&
            restored[0].All(r=>((string)r["physical_name"]!).StartsWith("/var/opt/mssql/")) &&
            Equals(restored[1][0]["family_guid"],backups[0][0]["family_guid"]),"restored database family/owned file paths mismatch");
        Probe["ag_setup"]="actual_secondary_readable_manual_full_log_restore_same_committed_fixture"; Persist();
    }
}
