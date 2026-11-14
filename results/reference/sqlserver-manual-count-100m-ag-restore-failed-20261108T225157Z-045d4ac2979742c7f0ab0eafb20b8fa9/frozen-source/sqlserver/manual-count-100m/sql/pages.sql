DECLARE @index_id int = 1; -- 実際に使用予定のIDへ置換
SELECT index_id, SUM(used_page_count) AS used_pages,
       SUM(reserved_page_count) AS reserved_pages
FROM sys.dm_db_partition_stats
WHERE object_id = OBJECT_ID(N'dbo.Target', N'U')
  AND index_id = @index_id
GROUP BY index_id;
