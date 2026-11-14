-- Regenerated inside CountLab, after versioning preflight; never export table data.
CREATE TABLE dbo.Numbers(n int NOT NULL PRIMARY KEY);
WITH d(n) AS (SELECT n FROM (VALUES(0),(1),(2),(3),(4),(5),(6),(7),(8),(9))v(n))
INSERT dbo.Numbers(n)
SELECT a.n+10*b.n+100*c.n+1000*d.n+10000*e.n
FROM d a CROSS JOIN d b CROSS JOIN d c CROSS JOIN d d CROSS JOIN d e
WHERE a.n+10*b.n+100*c.n+1000*d.n+10000*e.n<50000;
CREATE TABLE dbo.empty_table(id bigint NOT NULL);
CREATE TABLE dbo.heap_table(id bigint NOT NULL, payload varchar(32) NOT NULL);
CREATE TABLE dbo.clustered_table(id bigint NOT NULL PRIMARY KEY, v int NOT NULL, payload varchar(64) NOT NULL);
CREATE INDEX ix_clustered_v ON dbo.clustered_table(v);
CREATE PARTITION FUNCTION pf_count(bigint) AS RANGE RIGHT FOR VALUES(50000,150000);
CREATE PARTITION SCHEME ps_count AS PARTITION pf_count ALL TO ([PRIMARY]);
CREATE TABLE dbo.partitioned_table(id bigint NOT NULL PRIMARY KEY, v int NOT NULL) ON ps_count(id);
CREATE TABLE dbo.columnstore_table(id bigint NOT NULL, v int NOT NULL);
CREATE CLUSTERED COLUMNSTORE INDEX cci_count ON dbo.columnstore_table;
CREATE TABLE dbo.skew_gaps(id bigint NOT NULL PRIMARY KEY, payload varchar(2000) NOT NULL);
CREATE TABLE dbo.thin_100m(id bigint NOT NULL);
-- The service diagnostic modifies existing rows only: committed row delta is zero.
CREATE TABLE dbo.service_table(id bigint NOT NULL PRIMARY KEY, v bigint NOT NULL, payload char(64) NOT NULL);
