# SQL Server：大規模テーブルの全件数取得ガイドライン

「データ量」は**業務上のWHEREなしの表全体ROW COUNT**です。容量bytes、アプリ有効件数、soft-delete除外件数は別指標です。
正確値も指定した実行主体のsnapshotに見える行数で、全物理版や未コミット行の総数ではありません。採用版・構成のサポートは所有者が確認します。
**承認済みexport → 対象限定のpartition参考行数 → 必要なら事前適格化した資源隔離先のversioned COUNT_BIG**を基本経路にします。
最適性は必要精度・鮮度・期限・サービス予算を満たす候補の追加費用で判断します。普遍的最速、影響ゼロ、スキーマロックなし、一律10%精度、行数Nだけの安全閾値は主張しません。

## 1. 要求と取得先を先に決める

1. 所有者、DB/schema/表/シャード・世代、実行主体・RLS/tenant範囲、必要精度・許容時点・期限を確定します。
2. 承認済み台帳・監視exportが要求を満たせば再利用。追加SQLには接続・総時間・CPU/I/O・待機・取消の予算を付けます。
3. 規模の参考なら§3、必須の誤差上限・正確値なら§4へ。metadata一致例だけで精度を保証しません。
4. データ同等性・HA role・隔離・versioning・鮮度・監視・取消・回復余力が不足すれば、独立復元先を用意するか、精度／鮮度の緩和を所有者へ返して保留します。

| 要求 | 最初に選ぶ方法 | 表示・不足時の分岐 |
|---|---|---|
| 既存値の時点でよい | 承認済みexport | 元の方式・範囲・時点を継承 |
| 粗い規模 | partition DMV、権限に応じsys.partitions | 参考行数、誤差上限なし。権限なしは管理者の既存exportへ |
| 経験的な精度でよい | 同世代・同時点の真値と照合した参考値 | 条件・観測極値付き。条件不一致なら校正棄却 |
| 必須の誤差上限／正確な可視行数 | 適格な隔離先のCOUNT_BIG(*) | statement/transaction snapshotの正確値、鮮度を別判定 |
| primary現在値・全シャード同時点 | 常時計数・差分整合や固定復元点等を別設計 | 別snapshotの合計で代用しない。経路なしは保留 |

## 2. 状態を組み合わせて基本経路へ振り分ける

物理形状、活動、権限、取得先の各条件を重ねます。「PKなし＋100列＋圧縮＋TDE」等も通常の読取可能disk-based表なら同じ基本経路です。
状態別の性能が未計測でも通常COUNTの意味は変わりません。PK・行幅不明だけで隔離COUNTを放棄せず、費用未校正として上限を設けます。

| 物理・統計状態 | 選ぶ経路と注意 |
|---|---|
| disk-based heap／clustered、空・極小・巨大 | export/partition参考値 → 必要なら隔離versioned COUNT_BIG。参考0・小さい推定はprimary scan許可ではない |
| PKあり／なし、整数／文字列／複合、疎・欠番・偏在 | 同じ経路。IDENTITY、MAX−MIN+1、ID範囲を件数にしない |
| 二次索引なし／狭い索引／複数／filtered | 同じ経路。filtered索引・統計の行数を全表件数にしない。既存索引を確認し、COUNT用索引新設やhint強制を自動化しない |
| 80/100列、LOB・row-overflow、行／page圧縮 | 同じ経路。列数からscan量を決めず、索引・割当・cache・更新状態と予算で判断 |
| partition表全体 | heap/clusteredだけの全partitionを合計。正確値は表全体COUNT。NCIやallocation unitとのjoin重複を加算しない |
| partition単位 | 全表件数とは別。所有者がpartition境界・世代・時点を固定。動くpartitionの別snapshot合計を全表正確値にしない |
| 対応disk-based columnstore | 通常metadataと隔離versioned COUNT_BIGを維持。delta/deletes/遷移を含む参考値に精度保証や最速順位を付けない |
| cache warm/cold、不明、古い／未初期化統計 | 正確値経路は同じ、費用は別条件。optimizer統計とpartition counterを混同せず、取得側からUPDATE STATISTICSやcache flushを自動実行しない。既存auto stats/asyncとcompile費用も下記で確認 |
| metadata正値／0／NULL／欠落／拒否 | 正値も参考。0は独立した空表確認なしなら要求件数UNKNOWN。NULL・空集合・拒否・timeoutも理由を保存し0にしない |
| TDEでserver鍵あり／なし | 鍵がありSQLで読めれば通常経路。鍵なしは所有者の復旧へ。backup復元にはcertificate/private key等のprovisionが必要 |
| アプリ暗号文・client seed不明 | 平文解釈はCOUNTに不要という意味上の推論。接続・権限・driverの可否は確認。実Always Encryptedでclient鍵回避できるとは約束しない |
| memory-optimized | 通常disk表SQLへ自動投入しない。所有者が当該形式の分離レベル・可視性・耐久性と承認済みexport／隔離取得手順を指定 |
| temporal current／history | 別objectとして意味を決定。通常current表は現在行、historyは履歴行の件数。各物理対象を確認して適格なdisk表経路へ。自動UNIONやFOR SYSTEM_TIMEで二重計上しない |
| view／indexed view | 基表物理行数とview結果を区別。定義・主体・重複・filterに対応する所有者export／隔離照会を使い、基表metadataで代用しない |
| temp／session-local | 作成session・tempdb・寿命・世代を所有者が特定。元sessionの承認済み結果export等を使用し、通常DBの対象SQLへ名前だけ渡さない |
| external／remote／synonym／種別不明 | 実体・remote先・権限と件数の意味を特定。所有者の承認済み参考値／export／隔離経路へ。remote負荷不明のgeneric COUNTは禁止 |

形式識別は[sys.tables](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-tables-transact-sql?view=sql-server-ver17)、時制の意味は[temporal仕様](https://learn.microsoft.com/en-us/sql/relational-databases/tables/temporal/query-data?view=sql-server-ver17)に従います。特殊形式は必要情報と取得責任者を明示し、単なる「対象外」で終えません。
TDEと実Always Encryptedは別です。[TDE復元の鍵要件](https://learn.microsoft.com/en-us/sql/relational-databases/security/encryption/move-a-tde-protected-database-to-another-sql-server?view=sql-server-ver17)・[Always Encryptedのdriver/鍵](https://learn.microsoft.com/en-us/sql/relational-databases/security/encryption/always-encrypted-database-engine?view=sql-server-ver17)

| 活動・security・取得先 | 重ねる条件／不足時の戻り先 |
|---|---|
| 大量UPDATE/INSERT/DELETE・長いtransaction | 参考値とsnapshot時点を分け、version保持・長いtransaction・空き容量を監視。未コミット行を数えない |
| soft-delete／RLS／tenant／EXECUTE AS | 全表COUNTはsoft-delete行を含む。RLSの可視COUNTは主体依存。全体metadataをtenant正確値として返さず、承認されたpolicy/session contextを維持する。CONTEXT_INFOを業務利用するRLSは本識別方式では未対応、候補COUNT前にHOLD／承認済みexportへ |
| DDL/rebuild/移行/TRUNCATE・世代不明 | object_idだけに依存せず作成情報・変更台帳と照合。競合中は新規投入停止、安定した隔離コピーか完了確認後へ |
| primaryのみ | exportまたは予算付き参考値。既定locking RCのCOUNTは採用しない。正確値は隔離先か保留 |
| 専用／共用readable replica | §3のHA role/routingと§4のversioning・対象データ同等性・資源・primary側影響を事前適格化。共用host/storage/network/reporting/redoを制限できなければ独立復元先へ |
| 独立した承認済み復元コピー | backup・復元済みLSN等・鍵・世代・鮮度とversioningを確認。backup取得／replica準備自体の元DB予算も必要 |
| 取得先なし・不明／shard欠落 | export、精度／鮮度緩和、設備追加を指定して保留。取得済み小計と欠落を返し、全体はINCOMPLETE |

RLSで見える0件は物理表が空という意味ではありません。dbo/管理者もpolicy適用対象で、権限の強さはcontext衝突を解消しません。RLSの範囲は[公式仕様](https://learn.microsoft.com/en-us/sql/relational-databases/security/row-level-security?view=sql-server-ver17)で確認します。権限追加を自動実施せず、管理者の既存収集経路を優先します。

## 3. identityを確認し対象限定の参考値を取る

**コピー前に**所有者予算、driver command timeout・呼出全体deadline、別管理接続の監視／取消担当を確定します。1500msはlock待ちの設定例で、本番安全値でも文実行期限でもありません。
新規・非pool・ambient transactionへの自動参加なしの専用sessionを使います。既存transactionを勝手にCOMMIT/ROLLBACKせず拒否し、metadata前に暗黙transactionをOFFにします。
HA所有者の権威あるtopology／routing exportでrole・昇格候補・epoch・採取時刻／許容age・イベント源・必要承認を記録します。意図したlistenerと実server/backend/DBの対応を、既存AG DMV／HA collectorのlocal roleと対象DBのis_primary_replicaへ照合します。`ApplicationIntent=ReadOnly`だけをsecondary保証にしません。[routing公式](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/configure-read-only-routing-for-an-availability-group-sql-server?view=sql-server-ver17)・[具体的role観測SQL](operations-runbook.md#4-sql-server別接続で可視なidentityと終了確認)
**DRIVER承認gate（既定拒否、下のSQLを送信する前）**：対象主体に依存する候補metadata／COUNTも含め、最初のSET CONTEXT_INFOより前に所有者の明示承認を確認します。承認欄は[runbookのcontext互換性](operations-runbook.md#1-採用前の記入欄)です。対象RLS policyと関数・参照表等の依存先、tenant/業務初期化（後続SETを含む）、監査、trigger、接続の全寿命について非競合を監査し、CONTEXT_INFOスロットが**JOB_EXCLUSIVE**であること、主体・対象世代・承認元・有効寿命を記録します。業務初期化は完了・監査済みで、job中のcontextは不変とします。

```csharp
// DRIVER側の制御フロー。SQL単独の保証ではなく、送信側に必須のgate。
if (approval is null || approval.ContextUsage != "JOB_EXCLUSIVE" ||
    !approval.CoversSubjectPolicyDependenciesBusinessInitLaterSetAuditTriggersAndLifetime ||
    !approval.BusinessInitCompletedAndAudited || !approval.IsCurrentForThisLease)
    return HoldAndUseApprovedExport(); // SQL・タグ書込み・候補metadata/COUNTを送信しない
// 承認後だけ下の初期化SQLを一度送信。各候補送信前にも契約・現在identityを照合。
```

NULL／空context、新規非pool接続、管理者権限、可視なsys.security_policiesがないことだけでは承認できません。UNKNOWN／承認欠落／BUSINESS_USEなら業務contextを保持し、HOLD／承認済みexportへ戻します。CONTEXT_INFO依存RLSのCOUNT＋取消は本方式では未対応です。SESSION_CONTEXTは別observerから遠隔参照できず代替しません。独断の連結・prefix・保存復元、policy変更／無効化もしません。Microsoftの[CONTEXT_INFOを使うRLS例](https://techcommunity.microsoft.com/blog/sqlserver/row-level-security-performance-and-common-patterns/384367/)はこの衝突が実在する理由です。

承認済みの通常非RLS表では、他の適格性gateも満たせば従来の参考値／versioned COUNT経路を使えます。[新labのInitialize／PrepareAndCount／GuardedCandidateCount](../../sqlserver/issue3/Program.cs)は同じ既定拒否を実装しますが、既知の新規非RLS fixture限定で、本番汎用実装ではありません。SQLのcommentだけではserver側に承認が強制されないため、SQL単独コピーでは開始しません。

下は承認済みの`db`/`dbo.Target`に限定した例です。初回にserver/DB/login/contextを台帳と照合し、種別を§2へ振り分けてから同sessionの後続SQLを実行します。

```sql
IF @@TRANCOUNT <> 0 OR XACT_STATE() <> 0
  THROW 51000, 'Existing transaction: reject connection', 1;
SET IMPLICIT_TRANSACTIONS OFF;
SET LOCK_TIMEOUT 1500; -- 例：driver期限は別途必須
IF DB_NAME() <> N'db' THROW 51000, 'Unexpected database', 1;
-- DRIVER承認gate通過後だけ。生成した一意randombinaryをparameterで渡す（一度限り）
SET CONTEXT_INFO @job_tag;
SELECT @@SERVERNAME AS server_name, DB_NAME() AS db_name, @@SPID AS spid,
       ORIGINAL_LOGIN() AS original_login, SUSER_SNAME() AS execution_login;
SELECT s.session_id,s.login_time,c.connection_id,s.context_info,s.login_name,
       s.original_login_name,s.program_name,DB_NAME(s.database_id) AS db_name
FROM sys.dm_exec_sessions s JOIN sys.dm_exec_connections c ON c.session_id=s.session_id
WHERE s.session_id=@@SPID AND c.parent_connection_id IS NULL;
DECLARE @obj int = OBJECT_ID(N'dbo.Target', N'U');
IF @obj IS NULL THROW 51000, 'Missing or invisible table', 1;
SELECT object_id, name, create_date, modify_date, is_memory_optimized,
       is_external, temporal_type, history_table_id FROM sys.tables WHERE object_id=@obj;
```

承認された同じidentity契約を読取・別observer・取消直前KILL照合に使い、job中のtagを変更しません。後続の業務初期化やcontext/契約のdrift・不一致は拒否して担当へ引継ぎ、取消を通すため再tagしません。

別管理接続からも保存したSPIDの同DMVを読み、server・login_time・サーバーconnection_id・CONTEXT_INFO・login/account・app・DBを照合します。ClientConnectionIdとの一致を仮定しません。SESSION_CONTEXT()は呼出元session専用なので別接続識別には使いません。2022以降の監視にはVIEW SERVER PERFORMANCE STATE、KILLにはALTER ANY CONNECTIONを事前確認し、自動GRANTしません。拒否・一部可視・空結果で識別不能なら監視不可として保留します。[所有確認SQL・権限・不一致分岐](operations-runbook.md#4-sql-server別接続で可視なidentityと終了確認)

対象世代・形式・実行主体を照合した通常disk表で、同sessionの次batchとして実行します。変数はbatchごとに宣言します。DMV拒否時は権限を上げず、適切に可視な同scopeのsys.partitionsか承認済みexportへ戻ります。

```sql
DECLARE @count_obj int = OBJECT_ID(N'dbo.Target', N'U');
IF @count_obj IS NULL THROW 51000, 'Missing or invisible table', 1;
SELECT SUM(row_count) AS reference_rows, COUNT_BIG(*) AS partitions
FROM sys.dm_db_partition_stats WHERE object_id=@count_obj AND index_id IN (0,1);
```

DMVを使えない場合は上のbatchの最後のSELECTだけを`SELECT SUM(rows) AS reference_rows, COUNT_BIG(*) AS partitions FROM sys.partitions WHERE object_id=@count_obj AND index_id IN (0,1);`へ置換します。両結果を足しません。
heap=0／clustered=1の全partitionだけを合計し、NCIを除きます。集計結果NULL・partition数0はUNKNOWNです。両方式とも概数で、一致しても正確値にはなりません。
SQL Server 2022+のDMV権限は`VIEW DATABASE PERFORMANCE STATE`と`VIEW SECURITY DEFINITION`。自動GRANTしません。[DMV仕様](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-db-partition-stats-transact-sql?view=sql-server-ver17)・[sys.partitionsと可視性](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-partitions-transact-sql?view=sql-server-ver17)
optimizerの`sys.dm_db_stats_properties.rows`は**統計更新時の行数**で、現在のpartition counterではありません。filtered統計は範囲も異なり、全統計の合計を件数にしません。
last_updated/rows_sampled/modification_counterは診断材料です。NULLや未初期化は0でなく不明、取得側からUPDATE STATISTICSを自動実行しません。engineの自動統計作業は§5で別に確認します。[統計properties](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-db-stats-properties-transact-sql?view=sql-server-ver17)

## 4. 正確値：既存versioningと隔離の両方が必要

既定のREAD COMMITTEDはlockingです。既存RCSIのREAD COMMITTED、既存の許可済みSNAPSHOTを区別し、本番RCSI/SNAPSHOTを自動有効化しません。
DRIVERは§3のJOB_EXCLUSIVE承認・主体・lease寿命と現在identityをCOUNT送信前にも照合し、不明／driftなら送信せずHOLDにします。以下のSQLだけにそのgateはありません。
下は**既存RCSIが有効な適格隔離先**で§3の専用接続・identity/世代/形式確認を済ませた続きです。COUNT前にも既存transactionなしを確認します。

```sql
IF @@TRANCOUNT <> 0 OR XACT_STATE() <> 0
  THROW 51000, 'Existing transaction: reject connection', 1;
IF NOT EXISTS (SELECT 1 FROM sys.databases
               WHERE database_id=DB_ID() AND is_read_committed_snapshot_on=1)
  THROW 51000, 'RCSI precondition missing', 1;
SET TRANSACTION ISOLATION LEVEL READ COMMITTED;
SELECT COUNT_BIG(*) AS exact_rows FROM [dbo].[Target];
```

既存SNAPSHOTを使う場合も§3を先に適用し、対象形式の対応を確認します。RCSI例の代わりに短い専用transactionを使います。

```sql
IF @@TRANCOUNT <> 0 OR XACT_STATE() <> 0
  THROW 51000, 'Existing transaction: reject connection', 1;
IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE database_id=DB_ID() AND snapshot_isolation_state=1)
  THROW 51000, 'SNAPSHOT precondition missing', 1;
SET TRANSACTION ISOLATION LEVEL SNAPSHOT;
BEGIN TRANSACTION;
SELECT COUNT_BIG(*) AS exact_rows FROM [dbo].[Target];
-- 正常時COMMIT。例外・Cancel・timeout時はdriver finallyが自分のTXを回収する。
COMMIT;
```

このSQLだけでは取消時の終了保証になりません。**attentionはT-SQL TRY/CATCHで捕捉されず**、末尾ROLLBACKにも到達しないことがあります。必ずclient finallyを実装します。使える元接続ではidentityと自分が開始したtransactionを確認してbounded rollback、接続不通／rollback失敗では別管理接続で同identityを再照合してKILLし、対象session・保存した実TX ID・active snapshotの終了をpollします。requestが0でもsleeping/open TXは残り得ます。管理接続自身の@@TRANCOUNT=0は対象の終了確認ではありません。
識別不一致は取消拒否、終了不明はUNKNOWNで新規投入停止・DBAへ引継ぎ。KILLはSPID指定で照合とのTOCTOUが残るため専用leaseを保ち、古いSPIDへの再送をしません。broken/open TX接続をpoolへ返しません。[具体的finally例・通信失敗・保持解除と容量回復](operations-runbook.md#client-finallyがcleanupを所有する)・[C#実装例](../../sqlserver/issue3/Program.cs)・[TRY/CATCH仕様](https://learn.microsoft.com/en-us/sql/t-sql/language-elements/try-catch-transact-sql?view=sql-server-ver17)
分離レベルの意味は[公式仕様](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-transaction-isolation-level-transact-sql?view=sql-server-ver17)に従います。COUNT_BIGはbigintでint overflowを避け、`*`はNULLを含む行も数えます。COUNT_BIG(nullable列)は代用しません。[COUNT_BIG仕様](https://learn.microsoft.com/en-us/sql/t-sql/functions/count-big-transact-sql?view=sql-server-ver17)
RCSIは文、SNAPSHOTはtransactionのsnapshotです。全表・全partitionを1文で数え、別snapshotの表／shard／partition合計を全体の同時点正確値にしません。
通常COUNTにもSch-SがありSch-Mと競合します。version store（tempdb／構成によりADRのPVS）、長いtransaction、CPU/I/O・空き容量の予算が必要です。[locking/versioning](https://learn.microsoft.com/en-us/sql/relational-databases/sql-server-transaction-locking-and-row-versioning-guide?view=sql-server-ver17)
NOLOCK/READPAST/TABLOCK/SERIALIZABLE、書込文へ埋め込むCOUNT、意図的競合注入は採用しません。監視開始前に資源上限・共有競合の隔離を確立します。

AG readable secondaryのdisk-based読取はsnapshotへmapされます。対象DB/表/世代の同等性は承認済み復元・seed・変更／整合性監査で確認し、snapshot点／鮮度・資源と別判定にします。不明なら元サービス表の正確要求はUNKNOWN/HOLDです。実際のrole/routing・対応表を所有者の承認済み手順で確認し、専用sessionとidentity guardを適用して通常COUNT_BIGを使います。RCSI例のguardを根拠なく削除して流用しません。
別hardwareでもsecondaryの長い読取はprimaryのghost回収・領域再利用に影響し得ます。既存の承認済み監視で**primary/secondary双方**のCPU/I/O、redo、data/log空き、log再利用待ち、data movement/切断を確認します。
primary側の回収・容量も停止／回復予算に含め、監視・余裕不足なら独立した承認済み復元コピーか保留へ。切断・data movement停止時はAG所有者の復旧手順で回収・log再利用の回復まで確認し、独断のreplica削除等はしません。[AG読取と制限](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/active-secondaries-readable-secondary-replicas-always-on-availability-groups?view=sql-server-ver17)
同期commit・遅延の小ささだけでprimary現在値としません。復元／適用済みLSN等・取得時刻・許容鮮度を記録し、実snapshotとの対応不明も残します。

## 5. 開始・停止・終了・回復チェック

- **開始**：所有者が対象世代・主体/RLS・取得先・データ同等性・versioning・鮮度・資源分離を承認。HA所有者のrole/topology/routing epoch・採取時刻／age・イベント源・必要承認も確認。取消担当と別管理接続を用意し、所有処理を識別・終了確認できなければ保留します。
- **予算**：平常p99・失敗率・CPU/I/O・待機・replica/redo遅延・DDL待ち・version保持・容量を確認。AGはprimary側も含め、上限・観測間隔・停止値・回復窓を事前記録します。
- **期限**：LOCK_TIMEOUTはlock待ちだけ。driver command timeoutと全体deadlineを別に設定し、検出・取消遅延を見込んでSLO違反前に止める余裕を確保します。[LOCK_TIMEOUT](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-lock-timeout-transact-sql?view=sql-server-ver17)
- **投入／停止**：共有資源単位でまず直列。昇格・role/topology/routing/epoch変更、roleの期限切れ・取得不可・不明で適格性は失効。停止値到達・監視不能・想定外DDL/負荷でも新規投入停止、driver Cancel/attention等の所有者経路で取消。行数Nだけで安全を認定しません。
- **所有確認**：SPID・CONTEXT_INFO・login_time・サーバーconnection_id・login/app/server/DBを別管理接続で照合し、管理KILL直前にも同batch再照合。不一致・不可なら拒否／引継ぎ。KILLは原子的な所有者比較ではありません。[Cancel](https://learn.microsoft.com/en-us/dotnet/api/microsoft.data.sqlclient.sqlcommand.cancel)
- **終了**：timeout・切断だけで完了にせず、server request/transaction終了とrollback完了を確認。必要時のKILL/STATUSONLYも現在の所有処理に限り、未確認なら次表へ進みません。[KILL](https://learn.microsoft.com/en-us/sql/t-sql/language-elements/kill-transact-sql?view=sql-server-ver17)
- **回復**：文終了後も同指標を追い、version・cache/I/O・AG回収とdata/log余裕が予算内へ戻るまで確認。取消直後の自動再試行をしません。
- **判定**：処理完了、要求精度、サービス予算、server終了／後始末を別判定。未開始・未完了・timeoutを落としてSLO合格にしません。

失効時は現在の所有queryを照合してCancel／finally／rollback・release・回復を確認し、結果は承認までHOLDにします。自動再接続・再試行は禁止し、driver/proxy retryで新しい物理sessionになったらidentity・role・データ同等性・資源・鮮度を再適格化します。実backendと専用leaseを固定・観測できなければHOLDです。
昇格候補はHA所有者の手順・イベントと新規投入停止／取消・保持解除ackを連携し、必要な場合は昇格先のサービス負荷受付前までの確認を組み込みます。同期不能・連携未検証なら独立復元先へ。ポーリングは検出遅延を狭めるだけでraceを防ぎません。HAを優先し、COUNT終了を必須failoverの妨げにしません。終了不明はincidentへ引継ぎ新規COUNT禁止とし、primaryへの負荷防止を保証しません。[failoverのrole交代](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/failover-and-failover-modes-always-on-availability-groups?view=sql-server-ver17)と[Cancelの制約](https://learn.microsoft.com/en-us/dotnet/api/microsoft.data.sqlclient.sqlcommand.cancel)を踏まえた運用方針で、自動failover実験は要求しません。[共通の失効・HA引継ぎ手順](operations-runbook.md#1-採用前の記入欄)を使用します。

事前適格化は[記入済み独立復元先／専用replica例と空欄template](operations-runbook.md#1-採用前の記入欄)を使用します。CPU/memory制限だけで共有I/O/networkの上限が成立するとはせず、停止からrollbackまでの上界より長い余裕を別途確認します。p99のms差を時間余裕の秒数へ変換しません。

旧labは`sqlserver/setup.sql`でAUTO_CREATE_STATISTICSとAUTO_UPDATE_STATISTICSをOFFにしています。利用先の既存auto create/update/async、対応版の低priority設定、compatibility・統計状態を開始前後に記録します。手動UPDATE STATISTICSを実施しない方針でもengineの自動統計・compile・async backgroundは別で、予算／回復監視へ含めます。通常の無条件COUNTでの該当性はplanごとに確認し、不明なら未検証として適格独立コピー／保留へ。本番設定の一律OFF/ONは推奨しません。[設定・named stats・planの観測手順](operations-runbook.md#5-sql-server既存自動統計とcompilebackgroundの費用)

## 6. 記録・観測限界・監査付記

表／shardごとにjob ID、DB/schema/object_idと世代、主体・RLS/session context・対象範囲、取得先identity/版/role、HA所有者・topology/routing epoch・採取時刻／age・イベント源・承認と同等性根拠、方式、値またはUNKNOWNと理由を記録します。
精度種別（参考／経験的校正／正確snapshot）、開始終了時刻・snapshot説明、backup/復元／適用LSN等と取得時刻・対応限界、遅延・鮮度要件、各予算・停止理由・取消・server終了・rollback・回復状態を残します。
期待shard一覧を突合し、欠落・異なる時点／範囲／精度を明示。互換な取得済み値の小計とINCOMPLETEな全体を分け、UNKNOWNを0にしません。
校正は同世代・同時点の真値と比較し、論理復元・再構築後の誤差を古い本番統計へ移しません。TABLESAMPLEや少数一致例から普遍的10%保証を作りません。

[実測JSON](../../results/reference/final-sqlserver-52556dd/results/sqlserver/runs/20261107T230105Z-8533a31e/result.json)と[owner audit](../../results/reference/final-sqlserver-52556dd/results/sqlserver/owner-audit.json)では、正の真値9表×3段階の27観測でpartition DMVが真値に一致しました。これは観測一致で、誤差上限の認定ではありません。
同じ集合のheapでoptimizer統計は更新時50,000行のまま、真値50,021行に対し約−0.041982%でした。partition counterとは別機構です。空表相対誤差はNULL、拒否・非可視も0として扱いません。
SQL Server 2025 `17.0.5035.5` Developer、container上限4 GiB/CPU 2の専用labで、ARM上amd64エミュレーションは**Microsoft非サポート構成**です。native性能や本番絶対時間は未計測です。[公式サポート境界](https://learn.microsoft.com/en-us/sql/linux/containers/deploy?view=sql-server-ver17)
1億行細表と75万行80/100列表はいずれもTDE有効ですが別条件で、**1億行×80/100列×TDEは未計測**です。columnstoreはOPEN deltaのみのfixtureで、圧縮済みscan/deletesの性能順位へ外挿しません。
実Always Encrypted・client鍵なしdriver動作、物理replica分離、本番cold/warm性能は未計測。cache/実行順も未制御で、サービスSLOは未認定です。[所有者proofと完全logへの参照](../../results/reference/final-sqlserver-52556dd/results/sqlserver/owner-proof.json)・[費用比較と詳細](../../results/reference/final-sqlserver-52556dd/docs/sqlserver-table-count-guideline.md)
利用者がlab/Rielaを実行する必要はありません。現行ガイドの受入可否と対象hashは[Issue 3受入記録](../../results/reviews/issue-3/acceptance.json)を照合します（所有者確認・別セッションGPT-6.1 Sol 2役の結果と対象hashを確認し、記録なし・不一致は受入保留）。[Issue 2修正前snapshot](../../results/reference/issue-2-pre-fix-de1c79c/manifest.json)と保存された[Issue 1受入](../../results/reference/issue-2-pre-fix-de1c79c/results/reviews/issue-1/acceptance.json)は旧hashだけに適用します。今回の文書修正で既存実行を再認定しません。Issue 1の[専用機能検証](../../sqlserver/issue1/README.md)は所有確認・取消・統計条件を対象とし、既存観測の再認定や本番SLO証明ではありません。最終Sol 2役レビュー後に変更したhashは再査読が必要です。

[Issue 3修正前snapshot](../../results/reference/issue-3-pre-fix-16cc9b3/manifest.json)と[歴史的Issue 2受入](../../results/reference/issue-3-pre-fix-16cc9b3/results/reviews/issue-2/acceptance.json)は保存された旧hashだけに適用します。現行Issue 3集約受入は所有者の専用実行・意味/source/後始末監査と別セッションSol 2役の最終査読待ちです。記録なし・hash不一致はHOLDであり、受入済みとは扱いません。

[旧Issue 1 Program](../../sqlserver/issue1/Program.cs)はRLSなし4,096行fixture限定の凍結実装です。そのInitializeの無条件上書きは本番安全な初期化例ではなく、旧成功をRLS context安全性の検証へ転用しません。[Issue 3 HOLD検証仕様](../../sqlserver/issue3/README.md)では物理seed 5行と業務context 1/2/99の可視2/3/0をfixture controlとしてのみ扱い、候補経路の拒否・context維持・書込み/COUNT送信0回と解放を独立確認します。実行結果がなければ未検証で、CONTEXT_INFO-RLSの運用COUNT＋取消を認定しません。
