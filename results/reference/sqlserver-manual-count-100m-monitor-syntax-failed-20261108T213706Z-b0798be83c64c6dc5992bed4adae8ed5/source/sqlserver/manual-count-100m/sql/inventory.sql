SELECT s.name AS schema_name, t.name AS table_name,
       SUM(p.rows) AS approximate_rows,
       SYSDATETIMEOFFSET() AS observed_at
FROM sys.tables AS t
JOIN sys.schemas AS s ON s.schema_id = t.schema_id
LEFT JOIN sys.partitions AS p
  ON p.object_id = t.object_id AND p.index_id IN (0, 1)
WHERE t.is_ms_shipped = 0
  AND t.is_memory_optimized = 0 AND t.is_external = 0
GROUP BY s.name, t.name
ORDER BY s.name, t.name;
