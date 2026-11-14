-- Owned static fixture only; no production setting-change advice. No automatic second copy.
ALTER DATABASE ManualCount100m SET RECOVERY FULL;
CREATE AVAILABILITY GROUP ManualCountAg WITH(CLUSTER_TYPE=NONE)
 FOR REPLICA ON
 N'countprimary' WITH(ENDPOINT_URL=N'tcp://countprimary:5022',AVAILABILITY_MODE=SYNCHRONOUS_COMMIT,
 FAILOVER_MODE=MANUAL,SEEDING_MODE=MANUAL,SECONDARY_ROLE(ALLOW_CONNECTIONS=READ_ONLY)),
 N'countsecondary' WITH(ENDPOINT_URL=N'tcp://countsecondary:5022',AVAILABILITY_MODE=SYNCHRONOUS_COMMIT,
 FAILOVER_MODE=MANUAL,SEEDING_MODE=MANUAL,SECONDARY_ROLE(ALLOW_CONNECTIONS=READ_ONLY));
BACKUP DATABASE ManualCount100m TO DISK=N'/backup/fixture-full.bak' WITH COMPRESSION,CHECKSUM;
BACKUP LOG ManualCount100m TO DISK=N'/backup/fixture-log.trn' WITH COMPRESSION,CHECKSUM;
ALTER AVAILABILITY GROUP ManualCountAg ADD DATABASE ManualCount100m;
SELECT name,recovery_model_desc FROM sys.databases WHERE name=N'ManualCount100m';
SELECT database_guid,family_guid,recovery_fork_guid FROM sys.database_recovery_status WHERE database_id=DB_ID(N'ManualCount100m');
SELECT DB_NAME(database_id) db_name,name,physical_name,size,max_size,growth FROM sys.master_files;
