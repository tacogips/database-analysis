SELECT rs.role_desc, ar.secondary_role_allow_connections_desc
FROM sys.dm_hadr_database_replica_states AS ds
JOIN sys.dm_hadr_availability_replica_states AS rs
  ON rs.replica_id = ds.replica_id AND rs.group_id = ds.group_id
JOIN sys.availability_replicas AS ar ON ar.replica_id = ds.replica_id
WHERE ds.is_local = 1 AND rs.is_local = 1 AND ds.database_id = DB_ID();
