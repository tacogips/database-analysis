SELECT t.lock_escalation_desc, i.index_id, i.name AS index_name,
       i.type_desc, i.allow_row_locks, i.allow_page_locks
FROM sys.tables AS t
JOIN sys.indexes AS i ON i.object_id = t.object_id
WHERE t.object_id = OBJECT_ID(N'dbo.Target', N'U');
