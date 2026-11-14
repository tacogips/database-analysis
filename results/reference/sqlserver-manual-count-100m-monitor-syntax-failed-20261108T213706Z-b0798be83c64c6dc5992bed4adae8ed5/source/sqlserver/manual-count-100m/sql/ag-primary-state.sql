SELECT @@SERVERNAME server_name,sys.fn_hadr_is_primary_replica(N'ManualCount100m') is_ag_primary,
 (SELECT COUNT(*) FROM sys.dm_exec_requests r JOIN sys.dm_exec_sessions s ON s.session_id=r.session_id
  WHERE s.is_user_process=1 AND r.database_id=DB_ID(N'ManualCount100m')
    AND r.command IN ('INSERT','UPDATE','DELETE','MERGE','BULK INSERT','SELECT INTO')) active_user_writes,
 (SELECT COUNT(*) FROM sys.dm_tran_database_transactions dt JOIN sys.dm_tran_session_transactions st ON st.transaction_id=dt.transaction_id
  WHERE dt.database_id=DB_ID(N'ManualCount100m') AND st.is_user_transaction=1) active_user_transactions;
SELECT ds.replica_id,ds.synchronization_state_desc,ds.synchronization_health_desc,ds.log_send_queue_size,
 ds.redo_queue_size,ds.is_suspended,ds.last_sent_lsn,ds.last_hardened_lsn,ds.last_redone_lsn,ds.last_commit_time
FROM sys.dm_hadr_database_replica_states ds
JOIN sys.availability_replicas ar ON ar.replica_id=ds.replica_id
WHERE ds.is_local=0 AND ds.database_id=DB_ID(N'ManualCount100m') AND ar.replica_server_name=N'countsecondary';
