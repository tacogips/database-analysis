SELECT b.backup_set_id,b.backup_set_uuid,b.database_name,b.database_guid,b.family_guid,b.type,
 b.first_lsn,b.last_lsn,b.checkpoint_lsn,b.database_backup_lsn,b.backup_start_date,b.backup_finish_date,
 b.backup_size,b.compressed_backup_size,b.has_backup_checksums,m.physical_device_name
FROM msdb.dbo.backupset b JOIN msdb.dbo.backupmediafamily m ON m.media_set_id=b.media_set_id
WHERE b.database_name=N'ManualCount100m' AND m.physical_device_name IN (N'/backup/fixture-full.bak',N'/backup/fixture-log.trn')
ORDER BY b.backup_set_id;
