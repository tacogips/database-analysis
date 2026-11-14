IF @@TRANCOUNT <> 0 OR XACT_STATE() <> 0
  THROW 51000, 'Unexpected transaction', 1;
IF DB_NAME() <> N'ManualCount100m' THROW 51000, 'Unexpected database', 1;
IF NOT EXISTS (SELECT 1 FROM sys.databases
               WHERE database_id = DB_ID() AND snapshot_isolation_state = 1)
  THROW 51000, 'SNAPSHOT required', 1;
SET IMPLICIT_TRANSACTIONS OFF;
SET TRANSACTION ISOLATION LEVEL SNAPSHOT;
SELECT COUNT_BIG(*) AS exact_rows FROM [dbo].[Target];
