IF @@TRANCOUNT <> 0 OR XACT_STATE() <> 0
  THROW 51000, 'Use a new connection without a transaction', 1;
SET IMPLICIT_TRANSACTIONS OFF;
SET LOCK_TIMEOUT 1500; -- 例：ロック待ち上限（ミリ秒）
IF DB_NAME() <> N'ManualCount100m' THROW 51000, 'Unexpected database', 1;
SELECT @@SERVERNAME AS server_name, DB_NAME() AS db_name,
       s.session_id, s.login_time, c.connection_id, s.login_name,
       s.transaction_isolation_level
FROM sys.dm_exec_sessions AS s
JOIN sys.dm_exec_connections AS c ON c.session_id = s.session_id
WHERE s.session_id = @@SPID AND c.parent_connection_id IS NULL;
SELECT is_read_committed_snapshot_on, snapshot_isolation_state_desc,
       sys.fn_hadr_is_primary_replica(DB_NAME()) AS is_ag_primary
FROM sys.databases WHERE database_id = DB_ID();
SELECT name, is_memory_optimized, is_external, temporal_type_desc
FROM sys.tables WHERE object_id = OBJECT_ID(N'dbo.Target', N'U');
