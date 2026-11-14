-- Disposable, newly created dedicated lab only. No production migration advice.
EXEC sp_configure 'show advanced options', 1;
RECONFIGURE;
EXEC sp_configure 'max server memory (MB)', 3072;
EXEC sp_configure 'max degree of parallelism', 2;
RECONFIGURE;
-- Bound tempdb to <= 2 GiB in aggregate (data and log).
DECLARE @id int, @name sysname, @stmt nvarchar(max), @files int;
SELECT @files=COUNT(*) FROM tempdb.sys.database_files WHERE type=0;
DECLARE files CURSOR LOCAL FAST_FORWARD FOR
  SELECT file_id,name FROM tempdb.sys.database_files;
OPEN files;
FETCH NEXT FROM files INTO @id,@name;
WHILE @@FETCH_STATUS=0
BEGIN
  SET @stmt=N'ALTER DATABASE tempdb MODIFY FILE (NAME='+QUOTENAME(@name)+
    N', MAXSIZE='+CONVERT(nvarchar(20),CASE WHEN @id=2 THEN 512 ELSE 1536/@files END)+N'MB)';
  EXEC(@stmt);
  FETCH NEXT FROM files INTO @id,@name;
END;
CLOSE files;
DEALLOCATE files;
CREATE DATABASE CountLab
  ON PRIMARY (NAME=CountLab, FILENAME='/var/opt/mssql/data/CountLab.mdf',
              SIZE=64MB, FILEGROWTH=64MB, MAXSIZE=7000MB)
  LOG ON (NAME=CountLab_log, FILENAME='/var/opt/mssql/data/CountLab_log.ldf',
          SIZE=64MB, FILEGROWTH=64MB, MAXSIZE=1024MB);
ALTER DATABASE CountLab SET RECOVERY SIMPLE;
ALTER DATABASE CountLab SET READ_COMMITTED_SNAPSHOT ON;
ALTER DATABASE CountLab SET ALLOW_SNAPSHOT_ISOLATION ON;
-- Deliberately stale optimizer statistics are an isolated fixture condition.
ALTER DATABASE CountLab SET AUTO_UPDATE_STATISTICS OFF;
ALTER DATABASE CountLab SET AUTO_CREATE_STATISTICS OFF;
