IF @@TRANCOUNT <> 0 OR XACT_STATE() <> 0
  THROW 51000, 'Unexpected transaction', 1;
IF DB_NAME() <> N'ManualCount100m' THROW 51000, 'Unexpected database', 1;
IF ISNULL(sys.fn_hadr_is_primary_replica(DB_NAME()), 1) <> 0
  THROW 51000, 'Readable AG secondary required', 1;
SET IMPLICIT_TRANSACTIONS OFF;
SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
SELECT COUNT_BIG(*) AS exact_rows FROM [dbo].[Target];
