using System.Globalization;

internal static class Fixture
{
    public const long Rows = 100_000_000;
    public const int Columns = 100, BatchRows = 100_000, Batches = 1000;
    public static readonly string[] Conditions = ["clustered", "narrow", "composite"];
    public static string TableSql => "CREATE TABLE dbo.Target (id BIGINT NOT NULL CONSTRAINT PK_Target PRIMARY KEY CLUSTERED, " +
        string.Join(", ", Enumerable.Range(1, 99).Select(i => $"c{i:000} INT NOT NULL")) + ");";
    // The sequence and offset are BIGINT before any multiply; SQL int overflow cannot occur.
    public static string InsertSql(int batch)
    {
        if (batch < 0 || batch >= Batches) throw new ArgumentOutOfRangeException(nameof(batch));
        long offset = (long)batch * BatchRows;
        return """
            SET XACT_ABORT ON;
            BEGIN TRANSACTION;
            WITH digits(n) AS (SELECT n FROM (VALUES(0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) d(n)),
            seq(id) AS (SELECT CAST(a.n AS BIGINT) + CAST(b.n AS BIGINT)*10 + CAST(c.n AS BIGINT)*100
              + CAST(d.n AS BIGINT)*1000 + CAST(e.n AS BIGINT)*10000 + CAST(
            """ + offset.ToString(CultureInfo.InvariantCulture) + " AS BIGINT) + 1 FROM digits a CROSS JOIN digits b CROSS JOIN digits c CROSS JOIN digits d CROSS JOIN digits e)\n" +
            "INSERT INTO dbo.Target SELECT id, (id*104729)%100000, CASE WHEN ((id/100000)%10)<8 THEN 0 ELSE ((id/100000)%1000)+10 END, (id*99991)%1000003, " +
            string.Join(", ", Enumerable.Range(4, 96)) + " FROM seq;\n" +
            "DECLARE @inserted BIGINT = @@ROWCOUNT;\nCOMMIT TRANSACTION;\nSELECT @inserted AS committed_rows, " +
            (offset + 1).ToString(CultureInfo.InvariantCulture) + " AS first_id, " +
            (offset + BatchRows).ToString(CultureInfo.InvariantCulture) + " AS last_id, @@TRANCOUNT AS transaction_count;";
    }
    public static string IndexSql(string condition) => condition switch {
        "narrow" => "CREATE NONCLUSTERED INDEX k_count ON dbo.Target(c001);",
        "composite" => "DROP INDEX k_count ON dbo.Target; CREATE NONCLUSTERED INDEX k_count_v2 ON dbo.Target(c001,c002,c003);",
        _ => throw new ArgumentException("No index DDL for clustered condition")
    };
}
