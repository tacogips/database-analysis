# 巨大 SQL Server テーブルの全件数取得ガイドライン

対象は SQL Server の業務 WHERE なし全件数です。**既存の承認済み export / 台帳を先に確認し、追加 SQL には予算を付けます。**
所有者が専用コンテナで完了した[実測結果](../results/sqlserver/runs/20261107T230105Z-8533a31e/result.json)を採用しています。
[owner proof](../results/sqlserver/owner-proof.json)はベンチマークの検証であり、文書の最終受入ではありません。MySQL の計測値・非ロック性を転用しません。
[計画](../results/sqlserver/plan.json)は `planned_not_db_executed` の履歴です。予定と実績が異なる場合は実測結果を確認します。

## 1. 先に取得先と要求を決める

1. 表・世代・所有者・実行主体・必要精度・許容時点・期限を決めます。RLS 適用後の可視件数か、管理者の全体件数かも区別します。
2. 追加負荷を避けたいときは、既存の承認済み export / 監視結果を取得元・取得時刻付きで利用します。
3. 追加 metadata SQL もロック・CPU・I/O・権限確認を伴い得ます。接続/総時間/取消/監視予算がない取得先へ実行しません。
4. 参考値で足りれば、その方式と鮮度・誤差未保証を付けて終了します。誤差の上限が必須なら metadata の一致例だけで認定しません。
5. 正確値は資源を隔離した復元コピー、または読取を承認された replica で検討します。隔離・鮮度・終了確認が成立しなければ保留します。

SQL Server の既定 READ COMMITTED は通常 locking です。RCSI 有効 DB の READ COMMITTED と、事前に許可済みの SNAPSHOT を別に扱います。
本手順の COUNT 候補は row versioning が成立する取得先だけです。設定確認に失敗した場合も停止します。
RCSI の本番有効化はここで自動実施せず、所有者の別の移行判断にします。[分離レベル](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-transaction-isolation-level-transact-sql?view=sql-server-ver17)

| 必要なもの | 候補 | 返す意味 |
|---|---|---|
| 規模の参考値 | 承認済み export、partition metadata | 取得元の参考行数。誤差上限なし |
| 条件付きの経験的精度 | 隔離した同世代コピーで真値と校正 | 観測条件・極値付き。将来保証ではない |
| 正確な可視件数 | 隔離先の versioned `COUNT_BIG(*)` | その statement / transaction snapshot の正確値 |
| primary の現在値 | 整合性を設計した常時計数・差分管理等 | replica の過去値で代用しない |

## 2. metadata と optimizer 統計を混ぜない

専用接続で DB・対象名・実行主体を確認し、承認済み予算内だけで取得します。次は固定した対象名の例で、動的な名前は安全に引用/パラメータ化します。
`OBJECT_ID` が NULL、対象不明・metadata 非可視なら停止します。空集合・partition 数 0・NULL・欠落・権限不足・timeout は **UNKNOWN** とし、0件に変換しません。
参考行数が0でも、それだけでは空表と認定しません。独立に空表を確認できなければ件数はUNKNOWNとし、参考統計0を別欄に残します。
以下のSQLも、§4のcommand timeout・呼出期限・監視・取消経路を準備してから実行します。

```sql
SET LOCK_TIMEOUT 1500; -- 所有者予算の例。クエリの実行期限ではない
DECLARE @object_id int = OBJECT_ID(N'dbo.Target', N'U');
IF @object_id IS NULL THROW 51000, 'Unknown or invisible table', 1;
SELECT SUM(row_count) AS reference_rows, COUNT_BIG(*) AS partitions
FROM sys.dm_db_partition_stats
WHERE object_id = @object_id AND index_id IN (0, 1);
SELECT SUM(rows) AS catalog_rows, COUNT_BIG(*) AS partitions
FROM sys.partitions
WHERE object_id = @object_id AND index_id IN (0, 1);
```

heap は index_id=0、clustered は 1 を合計し、**全 partition を含めます**。二次索引まで合計すると同じ行を重複計上します。
DMV の row_count は概数で、cache/system table 等に保持される partition 情報です。SQL Server 2022+ は DB の `VIEW DATABASE PERFORMANCE STATE` と `VIEW SECURITY DEFINITION` が必要です。
本番で権限を自動追加せず、管理者の既存収集経路を優先します。[partition DMV](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-db-partition-stats-transact-sql?view=sql-server-ver17)
`sys.partitions.rows` も概数です。public からの参照にも metadata visibility が適用されます。両方式の一致は独立した真値証明ではありません。[カタログ仕様](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-partitions-transact-sql?view=sql-server-ver17)

optimizer 統計はさらに別の機構です。以下は診断用で、値を全統計・全索引にわたり合計しません。

```sql
SELECT s.name, s.has_filter, s.filter_definition,
       p.last_updated, p.rows, p.rows_sampled, p.unfiltered_rows, p.modification_counter
FROM sys.stats AS s
OUTER APPLY sys.dm_db_stats_properties(s.object_id, s.stats_id) AS p
WHERE s.object_id = OBJECT_ID(N'dbo.Target', N'U');
```

`rows` は統計更新時の対象行数、`rows_sampled` は統計作成時の標本行数です。filtered 統計は対象範囲も違います。
統計 object がない/非可視の場合は空集合、空表等で blob が作られなければ last_updated が NULL になり得ます。
権限は統計列の SELECT、所有権、所定 role 等の仕様を確認します。OUTER APPLY により取得不可を消さず残します。
**optimizer 統計の古さや誤差から partition DMV の誤差を導きません。** UPDATE STATISTICS はサービス上の件数取得手順に追加しません。[統計 properties と権限](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-db-stats-properties-transact-sql?view=sql-server-ver17)

RLS や EXECUTE AS / session context により可視 COUNT は主体ごとに変わります。表全体 metadata を tenant の正確値として返しません。
権限・security policy・実行文脈を合わせた別検証が必要です。[RLS](https://learn.microsoft.com/en-us/sql/relational-databases/security/row-level-security?view=sql-server-ver17)

## 3. 正確値は隔離した取得先で

**監視開始より先に CPU・memory・I/O・同時実行の資源上限を決めます。** 共有 host/storage/network の競合を制限できなければ隔離復元先を使います。
backup 取得や replica 新設の作業自体にも primary の予算が必要です。既存の承認済み backup と復元手順を優先します。
AG readable secondary の disk-based 読取は snapshot へ map されます。単なる接続名や ApplicationIntent だけで実際の取得先を保証しません。
適用済みログ位置・redo の進み・取得開始終了時刻・routing の実体を記録します。同期 commit でも「読取時点の primary 現在値」とは呼びません。
secondary の読取は CPU/I/O/version 保持を使い、Sch-S が redo の DDL と競合し得ます。共有 reporting traffic と redo の余力も管理します。[readable secondary](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/active-secondaries-readable-secondary-replicas-always-on-availability-groups?view=sql-server-ver17)
復元コピーでは backup 時点・復元済み log 位置を示します。TDE の certificate/private key 等を所有者が先に provision します。[TDE DB の移動](https://learn.microsoft.com/en-us/sql/relational-databases/security/encryption/move-a-tde-protected-database-to-another-sql-server?view=sql-server-ver17)

以下は RCSI が**既に有効な隔離先**で、§4のcommand timeout・監視・取消・終了確認を準備した後に使う例です。1500ms は設定例で、安全閾値ではありません。
新規接続でもクライアントの ambient transaction に自動参加し得ます。自動参加を防ぎ、共有接続・pool 由来の長期 transaction を使わない専用接続を確保します。[SqlClient の自動参加](https://learn.microsoft.com/en-us/sql/connect/ado-net/system-transactions-integration-with-sql-server?view=sql-server-ver17)
開始前にクライアント側と `@@TRANCOUNT` / `XACT_STATE()` を確認します。既存の仕事を COMMIT/ROLLBACK して空にせず、所有する接続と transaction だけを終了対象にします。

```sql
IF @@TRANCOUNT <> 0 OR XACT_STATE() <> 0
  THROW 51000, 'Existing transaction: use a dedicated connection', 1;
SET IMPLICIT_TRANSACTIONS OFF;
SET LOCK_TIMEOUT 1500;
EXEC sys.sp_set_session_context @key=N'count_job', @value=N'unique-owned-job-id';
SELECT @@SPID AS session_id, DB_NAME() AS database_name, ORIGINAL_LOGIN() AS login_name;
IF NOT EXISTS (SELECT 1 FROM sys.databases
               WHERE database_id=DB_ID() AND is_read_committed_snapshot_on=1)
  THROW 51000, 'RCSI precondition missing', 1;
SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
SELECT COUNT_BIG(*) AS exact_rows FROM dbo.Target;
```

COUNT_BIG は bigint を返し、COUNT の int overflow を避けます。`*` は NULL を含む行を数え、COUNT_BIG(nullable_column) とは意味が違います。[COUNT_BIG](https://learn.microsoft.com/en-us/sql/t-sql/functions/count-big-transact-sql?view=sql-server-ver17)
RCSI の結果は文の snapshot、SNAPSHOT は transaction の snapshot です。既存の許可済み SNAPSHOT を使うなら、短い専用 transaction で実行し正常時 COMMIT、異常時 rollback/終了確認を行います。
各表を別文で数えた合計や分割 COUNT の合計を、更新中の全体同時点と呼びません。[分離レベルの時点](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-transaction-isolation-level-transact-sql?view=sql-server-ver17)
通常 COUNT でも Sch-S 等は残り、Sch-M と競合します。「テーブルを一切ロックしない」とは約束できません。
row versioning はデータ読取の共有ロック競合を減らしますが、長い snapshot は version store の保持・掃除と資源に影響します。
tempdb / ADR の persistent version store 等、版と構成に合う保持領域・長い transaction を監視します。[locking / versioning](https://learn.microsoft.com/en-us/sql/relational-databases/sql-server-transaction-locking-and-row-versioning-guide?view=sql-server-ver17)
NOLOCK、READPAST、TABLOCK、SERIALIZABLE、COUNT を書込文に埋める方式、DDL 待ちの意図的注入は採用しません。
既存の狭い索引を使う exact scan と metadata は別候補です。列数・推定行数だけで I/O や安全性を判定せず、件数取得だけのために primary へ索引を新設しません。

## 4. 期限と終了確認を分ける

- **事前**：所有者が baseline の p99・失敗率・CPU・I/O・待機・redo/replica遅延・DDL待ち・version store と空き容量を確認します。
- SLO の停止値は検出・取消・回復の遅延を見込み、違反より手前に余裕を置きます。余裕と資源上限を決められなければ保留します。
- まず共有資源単位で直列化します。行数 N だけで安全な閾値は設定できません。取得中と終了後の回復窓も同じ指標で追います。
- `SET LOCK_TIMEOUT` は lock 待ちの上限です。文全体の deadline ではなく、CREATE/ALTER/DROP DATABASE 等が従わない点にも注意します。[LOCK_TIMEOUT](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-lock-timeout-transact-sql?view=sql-server-ver17)
- driver の command timeout と呼出し全体の monotonic deadline を別途設定します。timeout=0 の無期限設定を避け、connect timeout とも区別します。[SqlCommand.CommandTimeout](https://learn.microsoft.com/en-us/dotnet/api/microsoft.data.sqlclient.sqlcommand.commandtimeout)
- 監視不能・停止値到達で新規投入を止めます。driver の Cancel / attention が成功したか、サーバーの request / transaction が終了したかを別管理接続で確認します。[SqlCommand.Cancel](https://learn.microsoft.com/en-us/dotnet/api/microsoft.data.sqlclient.sqlcommand.cancel)
- session ID に加えて job context、login、接続先、開始文脈を照合します。古い SPID を盲目的に KILL せず、現在の所有 session だけを所有者の取消経路で扱います。[session context](https://learn.microsoft.com/en-us/sql/relational-databases/system-stored-procedures/sp-set-session-context-transact-sql?view=sql-server-ver17)
- KILL は rollback が長引く場合があります。切断を後始末完了とせず、必要時は所定の `KILL ... WITH STATUSONLY` と DMV で終了を確認します。終了未確認では次へ進みません。[KILL](https://learn.microsoft.com/en-us/sql/t-sql/language-elements/kill-transact-sql?view=sql-server-ver17)
- 実験完了、要求精度、method 期限、service 予算、後始末を別判定します。未完了・未到着・timeout を捨てて SLO 合格にしません。

## 5. 暗号化・表形式の分岐

| 状態 | 扱い |
|---|---|
| TDE・サーバーが必要な鍵を利用可能 | クライアントに TDE 鍵を渡さず COUNT。通常の取得先・予算判断へ |
| TDE backup の復元 | サーバー側の certificate/private key 等を所有者が provision |
| TDE に必要なサーバー鍵がない | サーバーが DB を開いて読めない。ファイルだけから COUNT 可能としない |
| アプリ暗号化の ciphertext / クライアント列鍵がない | `COUNT_BIG(*)` 自体に平文解釈は不要という意味上の推論。driver・権限・接続可否を所有者が確認して取得先へ振り分ける |
| 80/100 列・LOB | 宣言列数で費用を予測せず、論理行幅・索引・割当量・memory状態を確認 |

TDE は at-rest の page I/O 暗号化で、接続暗号化やアプリ列暗号化とは別です。鍵利用・復元の条件は [TDE 仕様](https://learn.microsoft.com/en-us/sql/relational-databases/security/encryption/transparent-data-encryption?view=sql-server-ver17)に従います。
今回の全 fixture は TDE 有効 DB 上で、開始・最終の encryption_state=3 を確認しました。TDE on/off 比較や暗号化による増減の因果推定はしていません。[encryption DMV](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-database-encryption-keys-transact-sql?view=sql-server-ver17)
wide fixture は runner が一時 passphrase を生成し、サーバーの `EncryptByPassPhrase` で作った単一暗号文を複製しています。再生成 seed / passphrase は本番のクライアント列鍵でも TDE 鍵でもありません。
実アプリ暗号化・Always Encrypted・鍵なし driver 動作・行ごとの暗号化費用は未計測です。COUNT に復号式を足さず、上表の意味上の推論と実測を区別します。[Always Encrypted の鍵と driver](https://learn.microsoft.com/en-us/sql/relational-databases/security/encryption/always-encrypted-database-engine?view=sql-server-ver17)
columnstore は今回 OPEN delta rowgroup 200,000 行、deleted_rows=0 でした。圧縮済み scan や削除/遷移の制御実験は未実施です。[rowgroup DMV](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-db-column-store-row-group-physical-stats-transact-sql?view=sql-server-ver17)
memory-optimized、external、temporal/current/history、view/indexed view、partition 単位・複数 shard の合計は自動投入せず、所有者へ意味・時点・権限・対応方式を戻します。
この rowstore 中心 lab から各形式の対応や正確性を認定しません。[sys.tables の種別情報](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-tables-transact-sql?view=sql-server-ver17)

## 6. 標本と校正の位置付け

TABLESAMPLE SYSTEM は page ベースの標本で、一様な行標本ではありません。小表・偏在・空隙では 0 行や特定群の欠落が起こり得ます。
指定 ROWS は概数で、REPEATABLE にも条件があります。本 lab は精度認定しません。[TABLESAMPLE](https://learn.microsoft.com/en-us/sql/t-sql/queries/from-transact-sql?view=sql-server-ver17#tablesample-clause)
標本や metadata に普遍的な「10%以内」を付けず、必要なら隔離した同世代・同時点の少数代表表で真値と比較します。
復元・索引再構築・統計更新は配置や統計を変えます。復元先の誤差を元の本番の古い統計へそのまま移しません。
真値 N>0 の符号付き誤差は `100*(estimate-N)/N`、絶対誤差はその絶対値です。N=0 の相対誤差は NULL とし空表一致を別欄にします。
反復の min/max は表・索引・partition・暗号化・統計時点・変更量・cache・実行順付きで返します。未初期化/権限不足は精度観測に混ぜません。
少数 fixture の極値・一致・安定性は将来誤差の保証ではありません。要求上限が必須なら隔離先 exact または整合性を設計した常時計数へ進みます。
常時計数には初期化、INSERT/DELETE、retry、移行、TRUNCATE 等の整合性を別機能として設計します。

## 7. 今回の実測：一致・費用・限界を分けて読む

採用 run は `20261107T230105Z-8533a31e`、SQL Server 2025 / `17.0.5035.5` Developer、固定 digest の linux/amd64 image です。
ARM 上の amd64 emulation は Microsoft 非サポートです。native 性能・本番の絶対時間は未計測です。[Linux/container サポート](https://learn.microsoft.com/en-us/sql/linux/containers/deploy?view=sql-server-ver17)
Docker memory は 8,307,171,328 bytes、container 上限 4 GiB / CPU 2、SQL memory 3072 MiB / MAXDOP 2 の専用 lab です。
1億行の1列 heap、各750,000行の80/100列表、空表、50,000行 heap、200,000行 clustered / 3-partition / columnstore、100,000行の偏在・空隙表と service 表の計10表を検証しました。
全 fixture が TDE 有効です。**1億行かつ80/100列かつTDEの組合せは未計測**で、細表と広い表の別々の成功を合成しません。今回 `notrun` の fixture はありません。

### 誤差：今回の条件だけの観測極値

[owner audit](../results/sqlserver/owner-audit.json)の `extrema` と実測 `errors` を、生ログの値・初期/最終 oracle・既知の committed delta と照合し再計算しています。
各方式の正の真値は9表×3段階の27観測です。自動統計更新を無効にし `lab_id` 統計を SAMPLE 20 PERCENT 指定で作った後、静かな heap / clustered だけに7行、さらに14行を commit（累計7/21行）しました。
他表の行数は固定です。これは意図的に古くした optimizer 統計の小さな変更実験で、経過日数・大量変更・統計の古さ一般の保証ではありません。

| 方式 | 符号付き誤差 min / max (%) | 絶対誤差 min / max (%) | 同率の扱い |
|---|---:|---:|---|
| partition DMV | 0 / 0 | 0 / 0 | 全27観測が最小・最大の両方 |
| sys.partitions | 0 / 0 | 0 / 0 | 全27観測が最小・最大の両方 |
| RCSI COUNT_BIG | 0 / 0 | 0 / 0 | 全27観測が最小・最大の両方 |
| SNAPSHOT COUNT_BIG | 0 / 0 | 0 / 0 | 全27観測が最小・最大の両方 |
| optimizer lab_id.rows | −0.0419823674 / 0 | 0 / 0.0419823674 | 絶対最小23観測、最大1観測。符号付き最大も23観測 |

optimizer の絶対最大（符号付き最小）は heap の `quiet_delta_2`：真値50,021、統計更新時50,000、rows_sampled=50,000、modification_counter=21 です。
[統計の生ログ](../results/sqlserver/runs/20261107T230105Z-8533a31e/0859-quiet_delta_2-r2-heap_table-optimizer-stats.stdout.log)と[exact 生ログ](../results/sqlserver/runs/20261107T230105Z-8533a31e/0841-quiet_delta_2-r2-heap_table-rcsi.stdout.log)で確認できます。partition metadata の一致とこの統計のずれは別機構の観測です。
空表は各3段階で exact / 両 partition metadata が0に一致しましたが、真値0の相対誤差は未定義（NULL）です。optimizer は空表の rows / last_updated 等が NULL で、誤差観測に含めていません。
権限実験でも非可視時は object_id / catalog / optimizer が NULL、DMV は error 262、表 SELECT のみでも DMV は取得不可でした。これらを0件にしません。詳細は実測 `permission_probes` に残しています。

### 費用：同じ lab の反復中央値の例

次は `prepared` / `quiet_delta_1` / `quiet_delta_2` の**各3反復**の中央値です。初期・最終 oracle と service 診断は除外しました。
各セルは **client秒 / SQL Server elapsed ms**。client は Docker CLI / sqlcmd / 通信・起動等を含み、SQL Server 側も JSON 整形を含む計装 SELECT の値です。純粋な COUNT 単体の時間ではありません。

| 表（初期行数） | RCSI exact | SNAPSHOT exact | partition DMV | sys.partitions |
|---|---:|---:|---:|---:|
| 空表 | 0.293439 / 0 | 0.295111 / 0 | 0.301516 / 0 | 0.301377 / 0 |
| heap（50,000） | 0.299269 / 7 | 0.299717 / 7 | 0.300363 / 0 | 0.303409 / 0 |
| clustered（200,000） | 0.309516 / 17 | 0.313889 / 17 | 0.304905 / 0 | 0.305010 / 0 |
| 80列（750,000） | 0.358223 / 63 | 0.356482 / 62 | 0.299078 / 0 | 0.299970 / 0 |
| 100列（750,000） | 0.359622 / 63 | 0.355931 / 62 | 0.297896 / 0 | 0.303063 / 0 |
| 1列 heap（100,000,000） | 6.654055 / 6360 | 6.627222 / 6328 | 0.300679 / 0 | 0.304075 / 0 |

中央値の数値源は owner audit の `metrics` と実測 `observations` です。CPU / I/O は SQL Server の STATISTICS TIME / IO を stderr 生ログから別に照合しました。[IO](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-statistics-io-transact-sql?view=sql-server-ver17)・[TIME](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-statistics-time-transact-sql?view=sql-server-ver17)
1億行 RCSI の中央値は CPU 11,501 ms・logical reads 383,143、DMV は CPU 1 ms・logical reads 9 でした。CPU は累積時間で elapsed と異なります。
80/100列 RCSI は logical reads がともに4,469、CPU は64/63 msでした。両表には既存の狭い二次索引があり、表全体の列幅だけで scan 費用を説明できません。実行計画による索引選択の検証はしていません。
[1億行 exact の stderr 例](../results/sqlserver/runs/20261107T230105Z-8533a31e/0973-prepared-r0-thin_100m-rcsi.stderr.log)と[DMV の stderr 例](../results/sqlserver/runs/20261107T230105Z-8533a31e/0979-prepared-r0-thin_100m-partition-dmv.stderr.log)は単一反復です。表の中央値そのものとは区別します。
`server_metrics_status=captured` は計装取得済みの意味です。elapsed=0 は記録分解能上の0で、CPU/I/O/ロックがないという意味ではありません。
load・先行 COUNT・物理形状診断の cache 持越し、実行順・時間変動は未制御です。global cache flush はせず、この比較から最速方式・native 性能・本番負荷の因果関係を認定しません。

### 完了、精度、負荷予算、終了確認

所有者の `mise -C sqlserver run all` は終了コード0、実験完了・exact oracle 一致・lab の45秒 query期限内完了・後始末 verified を別々に記録しています。
[完全な runner log](../results/sqlserver/runs/20261107T230105Z-8533a31e/runner.log)と[外側の実行 log](../results/sqlserver/reviews/canonical-invocation.log)、[実行終了記録](../results/sqlserver/reviews/canonical-invocation.json)を保持し、終了コード不明の記録を成功扱いしません。
所有者は source 前後 hash、2,662 raw artifact の完全性、残存 session 0、専用 container / volume / network の不在と既存 MySQL 入力bytesの保持を監査しました。現時点の再起動確認を本稿の author が行ったわけではありません。
service は baseline → exact → metadata の固定順、単一clientの UPDATE のみで行数差0、各12件・計36 arrivalsがcommitし、未到着・未完了・失敗は各0でした。
これは小さな CLI 診断で、本番アプリの負荷・p99・回復余力を認定するものではありません。service SLO は **unavailable**、metadata の誤差上限も未認定です。完了を無影響や数%以内の保証に読み替えません。

## 8. 再現と文書の最終受入

[runner 手順](../sqlserver/README.md)の canonical command は `mise -C sqlserver run all` です。author は DB/Docker/計測を起動しません。
接続はローカル context の Unix socket に固定し、この専用ラボだけを対象にします。実DB・rootのMySQL runnerに接続先や処理を追加しません。
MySQL all/reset は `results/sqlserver` も削除し得ます。所有者は他の reset 前に今回の証跡をコミットまたは別途保全し、保持を確認します。
文書の最終受入は[final-acceptance.json](../results/sqlserver/reviews/final-acceptance.json)の受入済み記録を条件とします。所有者は、別々の最終セッションの GPT-6.1 Sol 2役が完了し、対象ガイド・README・owner proof の正確な hash が一致した後にのみ、その受入記録を作成します。
ファイルの存在・保留状態・owner proof だけで最終受入済みとはしません。最終査読後に文書を変更した場合は、その hash での2役査読と受入をやり直します。
