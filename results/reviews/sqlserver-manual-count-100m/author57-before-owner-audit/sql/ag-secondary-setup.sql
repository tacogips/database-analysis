ALTER AVAILABILITY GROUP ManualCountAg JOIN WITH(CLUSTER_TYPE=NONE);
RESTORE DATABASE ManualCount100m FROM DISK=N'/backup/fixture-full.bak'
 WITH NORECOVERY,CHECKSUM,
 MOVE N'ManualCount100m_data' TO N'/var/opt/mssql/data/ManualCount100m.mdf',
 MOVE N'ManualCount100m_log' TO N'/var/opt/mssql/log/ManualCount100m.ldf';
RESTORE LOG ManualCount100m FROM DISK=N'/backup/fixture-log.trn' WITH NORECOVERY,CHECKSUM;
ALTER DATABASE ManualCount100m SET HADR AVAILABILITY GROUP=ManualCountAg;
