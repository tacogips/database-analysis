SELECT ag.name,ag.cluster_type_desc,ar.replica_server_name,ar.endpoint_url,ar.availability_mode_desc,
 ar.failover_mode_desc,ar.seeding_mode_desc,ar.secondary_role_allow_connections_desc,
 rs.is_local,rs.role_desc,rs.connected_state_desc,rs.synchronization_health_desc
FROM sys.availability_groups ag JOIN sys.availability_replicas ar ON ar.group_id=ag.group_id
LEFT JOIN sys.dm_hadr_availability_replica_states rs ON rs.replica_id=ar.replica_id;
SELECT * FROM sys.dm_hadr_database_replica_states;
SELECT name,state_desc,port FROM sys.tcp_endpoints WHERE name=N'Hadr_endpoint';
SELECT name,physical_name,size,max_size,growth FROM sys.master_files;
