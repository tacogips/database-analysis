# SQL Server：件数取得の手順とQ&A

`db`・`dbo.Target`は対象のDB名・表名に置き換えます。業務接続を流用せず、新しい専用接続を使います。業務データ・表定義・DB全体の設定・権限は変更しません。

## 事前準備：作業環境を選ぶ

- **サービスからのクエリが飛ばないread replicaや分析用DBがあれば、そこを作業環境にする。** 対象表・行が揃っていることと、データの反映時点を確認する。
- 専用環境がなければ、サービスのSELECTも処理するread replicaを候補にする。通常負荷に対する余力・許容上限を確認し、閑散時間帯にまず1本だけ実行する。writerへ自動的に切り替えない。
- COUNTにはRCSIが有効なDB、SNAPSHOTがすでに有効なDB、またはAGの読取り可能な複製先を優先する。いずれも使えない場合は、未COMMIT・重複・数え漏れを許容する件数としてNOLOCK付きCOUNTを使う。replicaという名称だけでは判断せず、手順3で確認する。
- 実行期限、サービス応答・CPU・I/O・複製遅延の監視、中止・終了確認を用意する。同じインスタンスの別DBではCPU・メモリ・I/Oを分離できない。
- 接続の自動再接続を無効にし、終了確認まで専用接続を保持する。CONTEXT_INFO／SESSION_CONTEXTを書き換えない。RLSがある場合、全表の概算と実行ユーザーに見える行数を混同しない。

## 実行手順

### 1. 概算の件数一覧を取得する

対象DBへ接続し、クライアントの実行期限・取消方法を用意して次を実行します。

```sql
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
```

- heap／clusteredの全partitionを合計する。非クラスタ化索引の行数を足さない。
- `sys.partitions.rows`は概算。NULL・権限による欠落・取得失敗を0件にしない。表示0件も正確な空表の証明にはしない。
- 通常のディスク上の表が対象。memory-optimized・external・viewはこのSQLで代用せず、その形式の取得方法をDB担当者に確認する。temporalのcurrent表とhistory表は別々の件数。

[sys.partitionsの仕様](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-partitions-transact-sql)

### 2. 概算でよければ終了する

- 一覧を見て、概算のまま使う表と正確に数える表を選ぶ。
- 概算でよい表は、表名・件数・取得先・取得時刻を記録して終了する。
- 概算一覧では足りず実際に走査して数える表、値を取得できなかった表は次へ進む。正確値が必要ならA／B／Cを使い、NOLOCKのDは選ばない。概算が少ないことだけで、サービス用DBのCOUNTを安全とは判断しない。

### 3. COUNTの実行先と読取り方法を確認する

**まず接続先の用途を確認し、その後に読取り設定を確認します。** RCSI／SNAPSHOTが有効でも、サービスの書込みを受けるDBではCOUNTしません。

| 接続先の用途（DB担当者に確認） | 次の作業 |
|---|---|
| サービスのINSERT・UPDATE・DELETEを受けるprimary | COUNTを実行せず、分析用DBまたは読取り可能な複製先を選び直す |
| サービスのクエリを受けない分析用DB・独立コピー | 下の確認SQLへ進む。AGに属していなくてもよい |
| SELECTできる複製先 | 下の確認SQLへ進む。サービスと共用なら余力・監視・中止条件も確認する |

`is_ag_primary`で分かるのはAG内の役割です。サービスが使っているか、AG以外の構成で書込み元なのかは、この値だけでは判定できません。

選んだDBへ新規専用接続を作り、以下を実行します。`LOCK_TIMEOUT`はロック待ちだけの上限です。例の値は運用上の上限に置き換え、クライアントにも実行時間の上限を設定します。

```sql
IF @@TRANCOUNT <> 0 OR XACT_STATE() <> 0
  THROW 51000, 'Use a new connection without a transaction', 1;
SET IMPLICIT_TRANSACTIONS OFF;
SET LOCK_TIMEOUT 1500; -- 例：ロック待ち上限（ミリ秒）
IF DB_NAME() <> N'db' THROW 51000, 'Unexpected database', 1;
SELECT @@SERVERNAME AS server_name, DB_NAME() AS db_name,
       s.session_id, s.login_time, c.connection_id, s.login_name,
       s.transaction_isolation_level
FROM sys.dm_exec_sessions AS s
JOIN sys.dm_exec_connections AS c ON c.session_id = s.session_id
WHERE s.session_id = @@SPID AND c.parent_connection_id IS NULL;
SELECT is_read_committed_snapshot_on, snapshot_isolation_state_desc,
       sys.fn_hadr_is_primary_replica(DB_NAME()) AS is_ag_primary
FROM sys.databases WHERE database_id = DB_ID();
SELECT name, is_memory_optimized, is_external, temporal_type_desc
FROM sys.tables WHERE object_id = OBJECT_ID(N'dbo.Target', N'U');
```

- server・DB・接続情報を記録する。対象表が取得できない場合は名前と既存権限を確認する。通常のディスク上の表以外へ後続SQLを流用しない。
**上の確認SQLが返した次の3列を使い、下表を上から順に判定します。最初に該当したA／B／C／Dを記録し、手順6でも同じSQLを選びます。**

| 順序 | 確認SQLの結果・条件 | 選ぶSQLとこの接続の設定 |
|---|---|---|
| A | `is_ag_primary = 0`で、下の追加確認も満たす：AGの読取り可能な複製先 | **SQL A**。`SET TRANSACTION ISOLATION LEVEL READ COMMITTED;` |
| B（A以外） | `is_read_committed_snapshot_on = 1`：RCSIが有効 | **SQL B**。`SET TRANSACTION ISOLATION LEVEL READ COMMITTED;` |
| C（A・B以外） | `snapshot_isolation_state_desc = 'ON'`：SNAPSHOTがすでに有効 | **SQL C**。`SET TRANSACTION ISOLATION LEVEL SNAPSHOT;` |
| D（A・B・C以外） | 未COMMIT・重複・数え漏れを許容する件数でよい | **SQL D**。`WITH (NOLOCK)`付きCOUNT。開始時点の正確値とは扱わない |
| 該当なし | A・B・Cを使えず、Dの誤差も許容できない | A／B／Cを使える分析用DB・複製先を選び、手順3からやり直す |

- AGは接続先の種類、RCSI／SNAPSHOTは読取り設定なので、条件が重なる場合はAを優先する。
- `is_ag_primary`の1はAGのprimary、0はprimary以外、NULLはAGに属さないDBなど。NULLだけを理由に分析用DBを除外せず、用途を確認したうえでB／C／Dを判定する。
- エラー・権限不足・結果の欠落を「無効」とみなして次の分岐へ進まない。DB担当者が確認する。SNAPSHOTの`ON`以外の遷移状態もCには進まない。

**Aの追加確認**：`is_ag_primary = 0`の場合は、同じ接続で対象DBの現在の役割と読取り許可を確認します。

```sql
SELECT rs.role_desc, ar.secondary_role_allow_connections_desc
FROM sys.dm_hadr_database_replica_states AS ds
JOIN sys.dm_hadr_availability_replica_states AS rs
  ON rs.replica_id = ds.replica_id AND rs.group_id = ds.group_id
JOIN sys.availability_replicas AS ar ON ar.replica_id = ds.replica_id
WHERE ds.is_local = 1 AND rs.is_local = 1 AND ds.database_id = DB_ID();
```

`role_desc = 'SECONDARY'`で、読取り許可が`ALL`または`READ_ONLY`ならAの候補です。`READ_ONLY`では接続文字列に`ApplicationIntent=ReadOnly`が必要です。対象DBへ実際に読取り接続できることを確認します。権限不足なら担当者の既存監視で同じ項目を確認し、設定変更や権限追加はしません。`is_ag_primary = 0`なのに追加確認を満たさない場合は、B／C／Dへ回さず接続先を確認し直します。接続文字列の指定だけをsecondaryの証明にはしません。[現在の役割](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-hadr-availability-replica-states-transact-sql)・[読取り許可](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-availability-replicas-transact-sql)

**Dでも接続先の用途・権限・監視条件は省略しません。** 確認失敗や役割変更をNOLOCKで回避せず、サービスの書込み用primaryへ切り替えません。COUNTのために本番のRCSI／SNAPSHOTを有効化しません。

既定のlocking READ COMMITTEDでは、読取りで共有ロックを取ります。A／B／Cは行versioning、DはNOLOCKを使い、データ読取りの共有ロックを避けます。ただし**Sch-Sは残り、DDLやredoと競合し得ます**。接続先が昇格・切替したらCOUNTを中止し、接続先と設定を確認し直します。

[分離レベル](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-transaction-isolation-level-transact-sql)・[AG secondaryの読取り](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/active-secondaries-readable-secondary-replicas-always-on-availability-groups)

### 4. COUNTを実行せず、実行計画とロック設定を確認する

同じ専用接続で以下を実行します。**Dを選んだ場合は、下のSELECTを`SELECT COUNT_BIG(*) AS observed_rows FROM [dbo].[Target] WITH (NOLOCK);`に置き換えます。** 実行予定と同じSQLの推定計画を確認します。SSMS／sqlcmdでは`GO`でbatchを区切ります。ドライバから実行する場合はON・SELECT・OFFをそれぞれ別batchで送り、エラー時もOFFへ戻します。

```sql
SET SHOWPLAN_XML ON;
GO
SELECT COUNT_BIG(*) AS exact_rows FROM [dbo].[Target];
GO
SET SHOWPLAN_XML OFF;
GO
```

- SSMSの「実際の実行プランを含める」はOFFにする。これは**推定実行計画**の取得であり、COUNT本体は実行しない。
- Scanの対象表・索引名、並列実行の有無を確認する。Estimated RowsはCOUNTの結果ではない。権限がなければ既存権限を持つ担当者に同じ実行先で取得してもらう。
- 計画作成にもコンパイル・Sch-S・自動統計処理の負荷があり得る。期限と監視を付け、手動の統計更新や設定変更はしない。

[SHOWPLAN_XMLの仕様](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-showplan-xml-transact-sql)

続けて、対象表の既存設定を読み取ります。

```sql
SELECT t.lock_escalation_desc, i.index_id, i.name AS index_name,
       i.type_desc, i.allow_row_locks, i.allow_page_locks
FROM sys.tables AS t
JOIN sys.indexes AS i ON i.object_id = t.object_id
WHERE t.object_id = OBJECT_ID(N'dbo.Target', N'U');
```

- 行ストアでは`allow_row_locks`／`allow_page_locks`の1は「許可する」の意味。**その単位だけでロックする保証ではない。** 両方0ならデータロックが必要なアクセスは表単位になる。列ストアにはこの判定を流用しない。
- `lock_escalation_desc`はロックを広い範囲へまとめる設定。`TABLE`でも毎回表ロックする意味ではなく、`DISABLE`でもあらゆる表ロックを禁止する意味ではない。
- **推定計画と設定だけで、実際のロック範囲を確定することはできない。** 実行中のロックは手順6で観測する。ロック設定やROWLOCK等を追加変更しない。NOLOCKはDで指定した場合だけ使う。

[索引のロック設定](https://learn.microsoft.com/en-us/sql/t-sql/statements/create-index-transact-sql)・[ロック範囲と拡大](https://learn.microsoft.com/en-us/sql/relational-databases/sql-server-transaction-locking-and-row-versioning-guide)

### 5. 使用予定の索引のページ数を確認する

`@index_id`は手順4の計画に表示された索引に対応するIDへ置き換えます。Table Scanでheapを読む場合は0です。

```sql
DECLARE @index_id int = 1; -- 実際に使用予定のIDへ置換
SELECT index_id, SUM(used_page_count) AS used_pages,
       SUM(reserved_page_count) AS reserved_pages
FROM sys.dm_db_partition_stats
WHERE object_id = OBJECT_ID(N'dbo.Target', N'U')
  AND index_id = @index_id
GROUP BY index_id;
```

- 全partitionのページ数を合計する。使用ページ数は容量の目安で、COUNTの実読取りページ数や所要時間ではない。
- 権限がない・対象が取得できない場合は「ページ数未取得」と記録する。0ページとは扱わず、手順6で負荷を監視する。索引新設や権限変更はしない。

[ページ数の仕様と必要権限](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-db-partition-stats-transact-sql)

### 6. 監視・中止条件を準備してCOUNTする

- サービス応答・CPU・I/O・ブロッキング・version storeと空き容量を監視する。replicaでは複製遅延も監視する。AGはprimary側のログ・空き容量・回収状態も確認する。
- 許容上限と実行期限を決め、別管理接続から監視・取消・終了確認できる状態で1表ずつ実行する。手順3と同じ専用接続を使う。
- **手順3で選んだSQL A／B／C／Dのうち、1つだけ実行する。** A→B→C→Dを続けて実行する手順ではない。
- 直前に手順3の接続情報（server・DB・session_id・login_time・connection_id）と用途・役割・選択条件が変わっていないことを確認する。変化・確認失敗時はCOUNTせず手順3へ戻る。実行中の役割変更時も取消し、別のSQLやwriterへ自動で切り替えない。
- 各SQLは既存transactionがないことを確認し、専用接続をautocommitにしてCOUNTを1文実行する。BEGIN／COMMITは書かない。クライアント側でもtransactionを自動開始する機能を使わない。

#### SQL A：AGの読取り可能な複製先（手順3のA）

手順3で`is_ag_primary = 0`、現在の役割が`SECONDARY`、読取り許可と接続条件を確認した場合だけ使います。役割変更の監視は継続します。

```sql
IF @@TRANCOUNT <> 0 OR XACT_STATE() <> 0
  THROW 51000, 'Unexpected transaction', 1;
IF DB_NAME() <> N'db' THROW 51000, 'Unexpected database', 1;
IF ISNULL(sys.fn_hadr_is_primary_replica(DB_NAME()), 1) <> 0
  THROW 51000, 'Readable AG secondary required', 1;
SET IMPLICIT_TRANSACTIONS OFF;
SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
SELECT COUNT_BIG(*) AS exact_rows FROM [dbo].[Target];
```

#### SQL B：RCSIが有効なDB（手順3のB）

Aに該当せず、手順3の`is_read_committed_snapshot_on = 1`だった場合に使います。RCSIは、READ COMMITTEDで行の過去バージョンを使って読むDB設定です。

```sql
IF @@TRANCOUNT <> 0 OR XACT_STATE() <> 0
  THROW 51000, 'Unexpected transaction', 1;
IF DB_NAME() <> N'db' THROW 51000, 'Unexpected database', 1;
IF NOT EXISTS (SELECT 1 FROM sys.databases
               WHERE database_id = DB_ID() AND is_read_committed_snapshot_on = 1)
  THROW 51000, 'RCSI required', 1;
SET IMPLICIT_TRANSACTIONS OFF;
SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
SELECT COUNT_BIG(*) AS exact_rows FROM [dbo].[Target];
```

#### SQL C：SNAPSHOTがすでに有効なDB（手順3のC）

A・Bに該当せず、手順3の`snapshot_isolation_state_desc = 'ON'`だった場合に使います。下の`snapshot_isolation_state = 1`は同じ有効状態を数値で確認しています。SNAPSHOTは読取り方法の名前で、バックアップを使う意味ではありません。

```sql
IF @@TRANCOUNT <> 0 OR XACT_STATE() <> 0
  THROW 51000, 'Unexpected transaction', 1;
IF DB_NAME() <> N'db' THROW 51000, 'Unexpected database', 1;
IF NOT EXISTS (SELECT 1 FROM sys.databases
               WHERE database_id = DB_ID() AND snapshot_isolation_state = 1)
  THROW 51000, 'SNAPSHOT required', 1;
SET IMPLICIT_TRANSACTIONS OFF;
SET TRANSACTION ISOLATION LEVEL SNAPSHOT;
SELECT COUNT_BIG(*) AS exact_rows FROM [dbo].[Target];
```

#### SQL D：NOLOCKで数える（手順3のD）

A／B／Cを使えず、未COMMITの行・重複カウント・数え漏れを許容する場合に使います。**結果はCOUNT開始時点の正確な行数ではなく、誤差の上限も保証しません。** 概算一覧で足りるなら、全件走査せず手順2で終了します。

```sql
IF @@TRANCOUNT <> 0 OR XACT_STATE() <> 0
  THROW 51000, 'Unexpected transaction', 1;
IF DB_NAME() <> N'db' THROW 51000, 'Unexpected database', 1;
SET IMPLICIT_TRANSACTIONS OFF;
SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
SELECT COUNT_BIG(*) AS observed_rows FROM [dbo].[Target] WITH (NOLOCK);
```

NOLOCKはこの表の読取りに指定します。既存のDB全体の設定は変更しません。データの共有ロックによる更新待ちは避けますが、全件走査のCPU・I/O負荷は残ります。Sch-Sも残るため、実行中にALTER TABLE等があれば競合し得ます。エラー・取消・timeoutは取得失敗として扱い、0件や取得完了にせず、原因を確認して再実行条件を見直します。[NOLOCKの仕様](https://learn.microsoft.com/en-us/sql/t-sql/queries/hints-transact-sql-table#readuncommitted)

**Dの誤差も許容できない場合は、A／B／Cを使える実行先を選び直します。** 以下の監視・終了確認はA／B／C／D共通です。

実行中のロックは、**同じサーバーの別管理接続**から確認します。`57`は記録したCOUNT接続のsession_idへ置き換えます。

```sql
SELECT resource_database_id, resource_type, request_mode, request_status,
       resource_associated_entity_id
FROM sys.dm_tran_locks
WHERE request_session_id = 57;
```

- `KEY`／`RID`は索引キー／行、`PAGE`はページ、`OBJECT`は表などのオブジェクト。`request_mode`の`S`・`Sch-S`・`IS`は別の種類なので、`OBJECT`行があるだけで「表全体への共有データロック」と判断しない。
- `GRANT`は取得済み、`WAIT`は取得待ち。これは観測時点の状態で、短時間のロックをすべて捕捉するものではない。既存権限で見られなければ担当者の監視を使う。
- 上限超過・監視不能・接続先の役割変更時はクライアントからCancelし、次の表の投入を止める。
- 管理接続で終了処理する場合、手順3で記録したserver・session_id・login_time・connection_id・login・DBと現在の処理を照合する。古いIDだけでKILLしない。
- **サーバー上のquery・transaction・snapshotの終了とサービス／複製の回復を確認してから次へ進む。** requestが消えてもtransactionが残る場合がある。終了未確認の接続をpoolへ返さない。
- 表名・COUNT_BIG結果・取得先・データの反映時点・開始終了時刻を記録する。Dは「NOLOCKで取得した件数」と明記し、正確値や開始時点の件数と表示しない。replicaの件数をprimaryの現在件数として表示しない。

[ロック観測の仕様](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-tran-locks-transact-sql)・[取消・ROLLBACK・終了確認のSQL](operations-runbook.md#4-sql-server別接続で可視なidentityと終了確認)

## 性能調査・判断理由

- [COUNT／概算件数の性能調査](../../results/reference/final-sqlserver-52556dd/docs/sqlserver-table-count-guideline.md)・[実測JSON](../../results/reference/final-sqlserver-52556dd/results/sqlserver/runs/20261107T230105Z-8533a31e/result.json)
- [確認する理由・用語の補足](count-check-rationale.md)・[自動統計と計画作成の負荷](operations-runbook.md#5-sql-server既存自動統計とcompilebackgroundの費用)

実測は専用コンテナの非サポートエミュレーション構成です。本番性能やRDSでの悪化率へ外挿しません。この手順自体のDB実行検証は未実施です。

## QA

### Q RCSI、SNAPSHOT、AGの読取り可能な複製先は何が違うか

A **RCSI／SNAPSHOTは読取り方法、AGの読取り可能な複製先は接続先です。**

| 用語 | 意味 | 手順3で確認する値 |
|---|---|---|
| RCSI | READ COMMITTEDで、SELECTごとの時点の行バージョンを読むDB設定 | `is_read_committed_snapshot_on = 1` |
| SNAPSHOTがすでに有効 | SNAPSHOT分離レベルを利用可能にしたDB。同じtransaction内で読取り時点を維持する。接続側でもSNAPSHOTを指定して使う | `snapshot_isolation_state_desc = 'ON'` |
| AGの読取り可能な複製先 | Always On可用性グループで、書込み元の更新を受け取り、SELECTも許可しているDB。通常のディスク上の表はsnapshotで読む | `is_ag_primary = 0`に加え、現在の役割・読取り許可・接続条件 |

SNAPSHOTは保存済みバックアップではありません。いずれもCOUNTのためにDB全体の設定を変更せず、すでに利用できる実行先を使います。[読取り設定](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-transaction-isolation-level-transact-sql)・[AG複製先](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/active-secondaries-readable-secondary-replicas-always-on-availability-groups)

### Q primaryでは中止するのか。secondaryでなければCOUNTできないのか

A **サービスの書込みを受けるprimaryでは、この手順のCOUNTを実行しません。** RCSIが有効でもCPU・I/Oへの影響は残るためです。分析用DBか読取り可能な複製先を選び直します。

一方、サービスからのクエリを受けない独立した分析用DBなら、secondaryでなくても、手順3のBまたはCを満たせば候補になります。サービスと共用のread replicaは余力・監視・中止条件を確認します。AG内の役割だけでDBの用途を判断しません。これは本ガイドの実行先選定方針であり、SQL ServerがprimaryでCOUNTを禁止するという意味ではありません。

### Q SELECTだけでもtransactionは発生するか。BEGINやCOMMITは必要か

A **SELECTだけでも内部ではtransactionとして実行されます。** autocommitなら1文ごとに自動終了するため、今回のCOUNT 1文に明示的なBEGIN／COMMITは不要です。通常のCOUNTは業務データを書き換えません。[autocommitの仕様](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-implicit-transactions-transact-sql)

BEGINを省いても、索引走査やCOUNT実行中の古い行バージョン保持の負荷は残ります。共有ロックを避けるには読取り設定の確認も必要です。取消・timeoutでもサーバー上のquery・transaction・snapshotの終了を確認し、接続不能なら管理接続から確認します。予期せずtransactionが残った場合は、自分の専用接続と確認したうえでROLLBACK等の終了処理を行います。

### Q どの範囲がロックされるか、COUNT前に調べられるか

A **分離レベル・索引の設定・推定実行計画で可能性は調べられますが、実際の範囲は確定できません。** 実行中の状況によってロックの単位や拡大が変わるためです。

[手順3](#3-countの実行先と読取り方法を確認する)で行versioningを確認し、[手順4](#4-countを実行せず実行計画とロック設定を確認する)でCOUNTを実行せず計画と設定を調べます。実際に取得したロックは[手順6](#6-監視中止条件を準備してcountする)の`sys.dm_tran_locks`で観測します。RCSI／SNAPSHOTでもSch-Sは残ります。[Microsoftのロック仕様](https://learn.microsoft.com/en-us/sql/relational-databases/sql-server-transaction-locking-and-row-versioning-guide)

### Q COUNT(*)はどうやって数えるか

A 通常の行ストアでは、索引またはテーブルを走査し、読み取った行を集計します。典型的にはIndex Scan／Clustered Index Scan／Table ScanからStream Aggregateへ渡します。並列実行では部分的な件数を最後に合計することもあります。MyISAMのように保持件数を読むだけではありません。非クラスタ化索引、クラスタ化索引、ヒープのどれを読むかは実行計画によります。[Microsoftの実装解説](https://techcommunity.microsoft.com/blog/sqlserver/the-building-blocks-of-query-execution/383091)・[並列集計](https://techcommunity.microsoft.com/blog/sqlserver/partial-aggregation/383338)

列ストアにはバッチ処理や走査内で集計する最適化があるため、行ストアと同じ説明にはしません。[列ストアの最適化](https://learn.microsoft.com/en-us/sql/relational-databases/indexes/columnstore-indexes-query-performance)

### Q locking READ COMMITTEDではCOUNT終了まで表全体がロックされるのか

A **必ず表全体がロックされるわけではありません。** RCSIがOFFで、ロックの挙動を変えるhint等を付けない通常SELECTでは、取得した共有ロックの範囲によって解放時点が違います。

| 取得した共有ロック | 通常の解放タイミング |
|---|---|
| 行・索引キー | 次の行を処理する前 |
| ページ | 次のページを読むとき |
| 表全体 | 文が終了したとき |

計画やロック数・メモリ状況によって表ロックになることもあります。ただし、数億行を累計で読んだから必ず表ロックになる、という意味ではありません。読み終わった行のロックを順次解放する動作と、同時に保持するロック数は区別します。[分離レベルの公式仕様](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-transaction-isolation-level-transact-sql)・[ロック拡大の解説](https://learn.microsoft.com/en-us/troubleshoot/sql/database-engine/performance/resolve-blocking-problems-caused-lock-escalation)

このガイドはRCSI／SNAPSHOT等を優先し、使えない場合は重複・数え漏れを許容するNOLOCK付きCOUNT（D）も採用します。DB全体の設定は変更しません。行versioningでもスキーマ安定性ロック（Sch-S）とCPU・I/O負荷は残ります。上表をREPEATABLE READ／SERIALIZABLEに転用しません。

### Q 暗号化の有無で、件数取得時に気を付けることはあるか

A **TDEで暗号化されていても、SQLは`COUNT_BIG(*)`のままです。**

- DBサーバーが必要な鍵を利用できることを確認する。別インスタンスへの復元では、元のTDE証明書・秘密鍵など、構成に対応した鍵の準備が必要。[TDE公式](https://learn.microsoft.com/en-us/sql/relational-databases/security/encryption/transparent-data-encryption)
- 実行時は復号処理を含むCPU・I/O負荷を監視する。暗号化なしの環境で測った時間をそのまま使わない。
- RDSのストレージ暗号化を使う場合は、復元先でKMS鍵を利用できることも確認する。[AWS公式](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Overview.Encryption.html)
- アプリが保存した暗号文を含む表でも、全行COUNTに復号式を追加しない。Always EncryptedはTDEと別方式なので、実際のdriver・接続・権限条件を確認する。

件数取得のために暗号化を解除したり、鍵・既存設定を変更したりしません。TDEの有無で、ガイドのversioning・ロック・取消条件は変わりません。本番環境での性能差は未計測です。

### Q 接続に設定する値で、COUNTの対象が変わることはあるか

A あります。SQL ServerのRLSは、実行ユーザーや接続に設定されたtenant ID等に基づいて見える行を絞れます。同じ表でも、ユーザー・接続の設定が違うとCOUNTの意味が変わります。

接続のCONTEXT_INFO／SESSION_CONTEXTを変更せず、既存の実行ユーザーと閲覧範囲を維持します。ジョブの識別は接続が元々持つ情報とclient側台帳で行います。必要な範囲を読めない場合は、正しい権限を持つ既存の取得経路を担当者が用意します。


### Q SQL Serverの場合、read replicaへのSELECT COUNTがprimaryに与える影響は？

A **COUNTの走査はreplica側で行いますが、primaryへの影響がなくなるわけではありません。** AGの読取り可能な複製先では、長いCOUNTが必要とする古いデータを残すため、primary側の削除済みレコードの回収を待たせる場合があります。

- **primaryのデータ領域**：削除済みレコードを回収できず、その領域の再利用が遅れることがある。
- **primaryのログ領域**：複製先の切断・データ転送停止などで回収できない状態が続く場合、ログの切り詰めにも影響し、ログ領域を圧迫することがある。
- **replicaの更新反映**：COUNTがCPU・I/Oを複製処理と取り合うほか、表定義を保護するSch-SがDDLの適用と競合する場合もある。これはprimaryの行にCOUNTの共有ロックを掛ける、という意味ではない。[AGの制限と性能への影響](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/active-secondaries-readable-secondary-replicas-always-on-availability-groups)

replica側のCPU・I/O・複製遅延に加え、primary側の書込み応答・データ／ログの空き容量と回収状態も監視します。許容上限を超えたらCOUNTを取消し、終了と回復を確認します。影響の大きさは負荷・構成によるため、このrepoでは本番での悪化率を保証していません。

### Q 分析用SQL Serverを別に置く構成はあるか

A MicrosoftはAlways Onのread-scaleでreporting・analyticsをsecondaryへ分離する構成を案内しています。HA以外の読取拡張を目的とする構成もあります。[Microsoft公式構成例](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/read-scale-availability-groups?view=sql-server-ver17)

分析用DWH・ETL済み表も選択肢ですが、元表の全行数とは別指標になり得ます。元表世代・対象範囲・変換・重複排除・削除反映・最終取込点を照合します。Azure SQLのread scale-outは別サービスの機能で、利用tier・routing・監視条件をそのサービス仕様で確認し、self-hosted SQL Serverへ設定をそのまま転用しません。[Azure SQL例](https://learn.microsoft.com/en-us/azure/azure-sql/database/read-scale-out?view=azuresql)

### Q 実行すべきSQLはCOUNTかCOUNT_BIGか

A 大規模表はCOUNT_BIG(*)を使います。COUNTはint、COUNT_BIGはbigintなので、後者が大きな件数を扱えます。[公式仕様](https://learn.microsoft.com/en-us/sql/t-sql/functions/count-big-transact-sql?view=sql-server-ver17)

```sql
SELECT COUNT_BIG(*) AS exact_rows FROM [dbo].[Target];
```

NOLOCKを使う場合は手順6のSQL Dを使います。実行には[手順3の接続先・読取り方法の確認](#3-countの実行先と読取り方法を確認する)が必要です。この1文だけを既定のlocking READ COMMITTEDの業務接続へ貼り付けないでください。取消後も[手順6の終了確認](#6-監視中止条件を準備してcountする)まで行います。

### Q indexや暗号化、データ量によってSQLを変えるか

A 対応する通常disk表ならCOUNT_BIG(*)を維持します。heap、clustered、二次索引、columnstoreでは実計画・scan量が変わりますが、索引hintやMAXDOPを一律に強制しません。filtered indexの行数は全表の代用ではありません。MAXDOPはCPU quotaではありません。

TDEでサーバー鍵が利用可能なら同じSQLです。Always EncryptedはTDEと異なり、実driver・接続・鍵要件を別確認します。「client鍵なしでも必ず実行できる」とはしません。memory-optimized、view、external、temporal historyは対象と意味を確認し、[対象表の確認](#1-概算の件数一覧を取得する)へ戻します。通常表のSQLを自動適用しません。

### Q 未COMMITや数え漏れを許容するならNOLOCKを使ってよいか

A **このガイドでは、その前提でNOLOCK付きCOUNTを採用します（手順3・6のD）。** データの共有ロックを取らず、更新の排他ロックも読取りを待たせないため、locking READ COMMITTEDで起きるデータロックの待ち合いを減らせます。

ただし、未COMMITだけでなく同じ行の重複・数え漏れやエラーも起こり得ます。結果はSELECT開始時点の行数ではなく、誤差が小さいことや一定割合以内であることも保証しません。RCSI／SNAPSHOTやAGの読取り可能な複製先は通常COUNTでもデータの共有ロックを避けるため、使える場合はそちらを優先します。

NOLOCKでも全件走査のCPU・I/O負荷は残ります。Sch-Sは表定義を保護するロックで、通常のINSERT・UPDATE・DELETEをそれ自体で止めるものではありませんが、ALTER TABLE等のSch-Mとは競合します。サービス応答・複製遅延の監視と取消は継続します。READPASTは別の機能であり、この手順では使いません。[Microsoftの仕様](https://learn.microsoft.com/en-us/sql/t-sql/queries/hints-transact-sql-table#readuncommitted)

### Q 分析基盤や集計テーブルがあればそこで数えてよいか

A 元表との同等性と時点が証明できる場合に使います。ETLのjoin、履歴、重複排除、tenant限定、削除反映で件数が変わり得ます。集計表の行数ではなく元表との対応・取得時点・精度を確認できる件数指標を読み、対応を確認できなければ、元表の正確値として使わず、元表と同じ範囲を読める実行先で数え直します。

### Q RDS read replicaに重いSELECTを流すと、writeやサービスのread性能へ影響するか

A 同じreplicaのサービスreadはCPU・I/O・cache競合で遅くなり得て、ログ受信・redoも資源不足やSch-S/Sch-M競合で遅れる場合があります。RDS SQL Serverのread replicaは非同期ですが、長いsecondary読取がprimaryの回収・容量へ影響する経路もあるため、write無影響とはしません。primary側のwrite応答・ログ容量／回収状態とreplicaのredo・鮮度を両側で監視します。MySQLのbinlogやAuroraの説明を転用せず、HA用の同期replicaとも区別します。[AWS構成・資源条件](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/SQLServer.ReadReplicas.html)・[AWS遅延対処](https://repost.aws/knowledge-center/rds-sql-server-read-replica-lag)・[AG読取の制限](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/active-secondaries-readable-secondary-replicas-always-on-availability-groups?view=sql-server-ver17)

### Q サービスのread性能への影響を最小限にするには

A サービスreadを受けない分析専用replicaか独立復元先を優先し、接続先・pool・routingを分離します。HA role変更と取消を連携し、資源隔離・同等性・鮮度・手順3の読取り方法の確認を行います。サービスと共用のreplicaしかなければ、手順3の読取り方法・余力・許容上限・監視と取消を確認し、閑散時間帯に1本ずつ実行します。writerへ自動的に切り替えません。

既存export・時点付きcache・参考件数で足りるなら走査を省きます。NOLOCKを含むCOUNT_BIGは共有資源単位でまず1本とし、サービスp95/p99・error率、CPU・I/O待ち、redoやログ送信queue・ReplicaLag、primaryのwrite応答・ログ／空き容量、version保持を監視します。上限・期限・観測間隔を所有者が事前に決め、監視不能なら開始しません。MAXDOPはCPU quotaではなく、一律のhintで安全とはしません。

予算超過時は[runbook](operations-runbook.md)に従い所有確認したqueryを取消し、transaction終了・rollbackとサービス／複製の回復まで確認します。集計をprimaryへ自動再試行せず、サービス側fallback・retry増幅も確認します。COUNTのために通常の複製を止めません。このrepoでRDSの悪化率は未計測です。

### Q 重いクエリは1本だけ。それでも大きな影響が出るのか

A 必ず大きな影響が出るわけではありません。余力があれば許容内のこともありますが、1本でもscan I/O、計画による並列CPU使用、cache入替え、長いversion保持等が競合し得ます。COUNT_BIGの返却結果が1行でも作業量が小さいとは限りません。本数や実行時間だけでなく、サービス応答・error、I/O待ち、redo・鮮度の実変化を予算に照らして判断します。通常SELECTでもSch-Sは残ります。

### Q slow queryの収集やログへの出力も注意すべきか

A SQL ServerにはMySQLのslow_query_logと同じ設定はありません。既存のExtended Events、Query Store、Database Insights等について、実行先replicaでの対応・有効状態・収集対象・保存先を確認します。「長いので必ずログに載る」「primaryで有効ならsecondaryも同じ」とはしません。[RDS Extended Events仕様](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/SQLServer.ExtendedEvents.html)

1本の長いqueryの収集費用だけで大きな劣化が起こると決めつけませんが、イベント頻度、SQL本文・実行plan等の収集内容、target・保存容量で費用が変わります。1実行でも複数イベントを生成し得て、常に1ログとは限りません。SQL Trace/Profilerはdeprecatedで、採用候補はExtended Eventsです。収集負荷は未計測です。[Microsoftの収集負荷に関する診断](https://learn.microsoft.com/en-us/troubleshoot/sql/database-engine/performance/troubleshoot-entire-sqlserver-slow)

専用ユーザー・接続先・時刻・機密を含まないjob IDで予定集計を識別し、SQL本文等の閲覧権限・保持・転送費用と通知先を確認します。CONTEXT_INFO等へ識別子を勝手に設定せず、既存RLS/context契約に従います。予定queryだからという理由でサービス悪化の通知や既存収集を無効化しません。完了イベントや集計済みの診断情報だけへ実行中の監視・取消を依存させません。

### 精度と運用の質問

### Q 件数一覧のSUM(p.rows)は何を集計しているか。COUNT_BIGと差が出る原因は何か

A [手順1のSQL](#1-概算の件数一覧を取得する)は、**sys.partitionsが持つpartitionごとの概算行数を、表ごとに合計しています。** 表の全行を走査して数えるSQLではありません。InnoDBのTABLE_ROWSのような標本からの推定や、SQL Serverの実行計画用の列統計とは別の値です。

- `index_id IN (0, 1)`：heap（0）またはクラスタ化索引（1）だけを対象にする。非クラスタ化索引まで足すと、同じ表の行を重複して数えてしまう。
- `SUM(p.rows)`：その表の全partitionを合計する。明示的にpartitionを分けていない表にも1つのpartitionがある。
- `observed_at`：一覧を取得した時刻。件数情報が更新された時刻や、COUNT_BIGのsnapshot時点ではない。
- WHERE条件：システム表・memory-optimized表・external表を除外する。権限で見えない表も一覧から欠けるため、これだけで全表を網羅したとは判断しない。NULLや欠落を0件にしない。[sys.partitionsの仕様](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-partitions-transact-sql)

**COUNT_BIGと差が出る原因は、概算行数とCOUNTが同じ状態・範囲を数えているとは限らないことです。**

- `p.rows`は公式仕様で概算とされ、ある時点のCOMMIT済み全行数との一致を保証しない。更新中の値を、そのままCOUNTの正確値として扱わない。
- 一覧取得とCOUNTの間にINSERT・DELETEがあれば、比較する時点が異なる。metadataは行versioningの対象ではないため、同じSNAPSHOT transaction内で取得してもCOUNTとの時点一致を保証しない。[metadataと分離レベル](https://learn.microsoft.com/en-us/sql/t-sql/language-elements/transaction-isolation-levels)
- RLSなどでCOUNTの見える行が制限される場合、表全体のmetadata件数とは対象範囲が違う。replica側の一覧とprimary側のCOUNTを比較する場合も、更新の反映時点を揃える必要がある。

InnoDBのsample pagesやMySQLの24時間キャッシュの説明を、この値へ転用しません。誤差率はこのSQLの結果だけでは分からず、固定の誤差上限も設定できません。正確値が必要なら手順3以降のA／B／Cの条件を満たしてCOUNT_BIGを実行します。NOLOCKのDは正確値の確認には使いません。件数確認のためにUPDATE STATISTICSやDBCC UPDATEUSAGEを実行する手順にはしません。

### Q 統計の件数はどれくらい信用できるか

A repoの固定観測を再集計すると次の通りです。各集合の分母を分けています。

| 方法と観測条件 | 観測した精度 | 判断への使い方 |
|---|---|---|
| SQL Server partition DMV、非空9表×3段階 | 27観測すべて真値と一致 | 静止状態と小さな確定INSERT後の観測。更新中の保証ではない |
| SQL Server optimizer lab_id.rows、同集合 | −0.0419824〜0% | 統計更新時の件数でありpartition counterとは別物 |

元データは[SQL Server固定JSON](../../results/reference/final-sqlserver-52556dd/results/sqlserver/runs/20261107T230105Z-8533a31e/result.json)。非空9表×3段階、auto stats OFF・エミュレーション・限定fixtureです。更新中の誤差上限や、他製品との精度順位を示しません。

5%程度の経験的見込みなら自分の条件で校正し、その観測範囲として使います。5%上限が必須なら統計だけでは採用せず、実行条件を確認した正確COUNT等へ。真値と比較していない場合は誤差率を付けず、DBの概算件数として表示します。正確さが必要ならA／B／CのCOUNT_BIGで確認し、NOLOCKの結果を真値にはしません。

### Q 自分の表の誤差をどう観測するか

A まず「今の本番cache値」と「復元先で再生成された統計」のどちらを評価するか決めます。以下は本番を止めず、既存の承認済み固定コピーで行う流れです。

1. 統計を更新する前の推定E、取得時刻、統計生成時刻または不明、設定、対象世代・行集合を保存。
2. 対応する固定コピーで正確Nを取得。元の本番Eを評価するなら、元Eの評価時点とコピーのデータ時点の対応が必要。
2. コピーへの書込・apply・DDL等がないことを確認し、統計状態を再読取。変わった場合は別観測として区別。
3. N>0なら `100*(E-N)/N` と行数差を保存。N=0は相対誤差を定義せず、未知・timeoutを0扱いしない。
4. 表・条件ごとに中央値、過小／過大極値、±1/5/10%内の件数/有効数、欠測、表数・反復数を記録。更新・再構築・世代変更で適用性を再確認。

統計が古いこと自体は評価対象です。統計生成時刻と評価時刻を別に保存し、評価時点の行集合をNと対応させます。SQL Serverではmetadataはversioning対象ではなく、SNAPSHOTでCOUNTとDMVを包むだけでは時点一致を保証しません。[公式仕様](https://learn.microsoft.com/en-us/sql/t-sql/language-elements/transaction-isolation-levels?view=sql-server-ver17)

### Q 真値なしで誤差率を計算できるか

A 今回確認した公式のmetadata項目だけでは得られません。rows_sampled/modification_counterは誤差率ではありません。繰返し同じ値が返ること、DMVやoptimizer推定等の一致も真値への近さを証明しません。[SQL Server統計properties](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-db-stats-properties-transact-sql?view=sql-server-ver17)

既存の正確な基点と、欠落・重複・rollback・DDLを扱った完全なcommit済み増減台帳があれば真値を再構成する選択肢はあります。単なるDML回数では不十分です。COUNTのために本番へ新しいtrigger/CDCを無断導入しません。

### Q 大量の表を並列に数えれば速いか

A 個々のCOUNTが予算内でも合計I/O・CPU・version保持が予算を超えます。共有資源単位でまず直列。取得済みの値を時点付きで再利用し、必要頻度を減らします。並列化は所有者が合計予算に収まることを確認した場合だけです。別snapshotの表・shard合計を全体同時点の正確値としません。

### Q タイムアウトしたら次の表へ進んでよいか

A server上のquery・transaction終了と回復を確認するまで進みません。Cancelとclient finally/rollback、必要なら所有確認した管理接続の終了処理を使います。古いIDへ盲目的にKILLせず、不明ならincident担当へ引き継ぎます。[取消と回復の実行手順](operations-runbook.md)

### Q COUNTを毎リクエスト発行するべきか

A UIの概数やレポートなら、要件を満たす既存export・時点付きcache・集計値を優先します。正確な現在件数が必須なら、初期化・増減・再試行・削除・世代変更まで整合する常時計数を別機能として設計します。IDENTITY/MAX(id)から行数を作りません。これは頻度と要求を見直す設計方針で、今回のrepoで常時計数を検証したものではありません。

公式資料は2026-11-08確認。構成例は普及率や無劣化の実測ではなく、製品・版・tierごとの条件確認が必要です。

[確認する理由・用語の補足](count-check-rationale.md)
