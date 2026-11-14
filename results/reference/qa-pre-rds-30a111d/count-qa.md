# 全件数取得の Q&A

受入状況と対象hashは[確認記録](../../results/reviews/count-qa/acceptance.json)を参照します。

全件数を取得する前に、**どの行集合を、どの時点で、何%の誤差まで許容するか**を決めます。既存の承認済み件数で足りれば再走査しません。足りない場合は[MySQLフロー](mysql.md#実行フロー)または[SQL Serverフロー](sql-server.md#実行フロー)へ進みます。対象は業務上のWHEREなしの全表件数です。

## MySQL の質問

### Q COUNTは必ずread replicaで実行するのか

A 必ずreplicaという意味ではありません。このガイドでは、巨大表の正確COUNTは**事前適格化した資源隔離先**で行います。専用の分析replica、独立した復元コピーが候補です。業務primaryしかない場合は既存exportか予算付き参考統計までとし、正確値は設備・鮮度・精度の見直しへ戻します。

replicaでもアプリの読取を受け持つ、storage/networkを共有する、複製適用が詰まる場合はサービスへ影響します。HA昇格候補はrole変更と取消を連携し、連携不能なら独立復元先へ。server UUIDだけでなく実role、同等性、遅延、上限、取消経路を[runbook](operations-runbook.md)で確認します。read_onlyフラグは無負荷の保証ではありません。

### Q サービスDBとは別に分析用replicaを置く構成は一般的か

A **ベンダーが公式に案内する標準的な構成パターンです**。AWSはRDS read replicaを読取負荷分散の仕組みとして提供しています。普及率を示す調査ではなく、構成例としての根拠です。[AWS公式](https://aws.amazon.com/rds/features/read-replicas/)

既存の分析replicaがあるなら、まず対象表の複製範囲と鮮度を確認します。新設はストレージ・運用費用、初期コピーと複製による元DBへの負荷を伴うため、COUNT一回のために自動作成しません。頻繁なレポートなら継続的な分離を検討し、まれな件数確認なら既存backupの独立復元が候補です。これは用途に応じた選定方針で、性能の一律優劣ではありません。

### Q 分析基盤や集計テーブルがあればそこで数えてよいか

A 元表との対応が証明できれば使えます。ETLで重複排除、削除行除外、履歴追加、tenant限定、joinによる増幅があると同じ件数ではありません。元表世代、変換規則、削除の反映、最終取込点、欠落・再送の扱いを確認します。集計表ならその行数をCOUNTするのではなく、承認済みの「元表件数」指標を読みます。対応不明なら分析側の件数と表示し、元表の正確件数要求はHOLDです。

### Q 実行するCOUNTのSQLは

A [MySQLガイド§3の専用接続初期化](mysql.md#3-対象限定で参考統計を取る)と実行先の適格化を済ませた同sessionで、次の1文です。識別子は承認済み台帳から安全に引用します。

```sql
SELECT /* count_job: unique_job_id */ COUNT(*) AS exact_rows
FROM `db`.`t`;
```

通常InnoDBのRC/RR・autocommit=1で使い、FOR UPDATE/FOR SHARE、SERIALIZABLE、DML/CTASへの埋込みへ変更しません。COUNT(nullable列)はNULLを除くので代用不可。COUNT(1)への変更も高速化策にはしません。[MySQL COUNT仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)

### Q PKや二次索引によってSQLを変えるのか

A 基本は同じCOUNT(*)です。InnoDBは通常、利用可能な小さい二次索引を走査し、なければclustered indexを走査します。索引・行幅・cache・MVCCで費用が変わるので、事前適格化した隔離先で通常EXPLAINを確認します。無条件COUNTは「索引があるので一瞬」とは限りません。[公式仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)

FORCE INDEX/PRIMARYを一律に付けたり、COUNTのため本番へ索引を新設したりしません。文字列PK・複合PK・PKなしでも通常COUNTの意味は同じです。索引選択の変更を試すなら別の隔離検証として費用・計画を比較します。

### Q 暗号化や100列、1億行ならSQLを変えるのか

A サーバーが通常SQLで読めるInnoDBならCOUNT(*)のままです。TDEはサーバーの鍵が利用可能であることが前提で、backup復元には鍵の準備も必要です。アプリ暗号文は行を数えるための平文復号式を追加しません。ただし接続・権限・driverの対応は確認します。[MySQL暗号化仕様](https://dev.mysql.com/doc/refman/8.4/en/innodb-data-encryption.html)

データ量で変えるのは接続先、時間・I/O予算、実行頻度と採用方式です。行数・列数だけから安全時間を算出しません。repoの1億行細表と75万行の幅広表は別条件で、1億行×100列の性能は未計測です。

## SQL Server の質問

### Q SQL Serverもread replicaを選べば安全か

A replicaという名称だけでは不十分です。AG readable secondary、独立復元先などで、資源隔離・対象データ・鮮度・可視性・監視・取消を確認します。AGの長い読取はsecondaryのredoだけでなくprimaryの回収・容量へ影響し得るため両側を監視します。[AG読取の制限](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/active-secondaries-readable-secondary-replicas-always-on-availability-groups?view=sql-server-ver17)

通常サーバーの既定READ COMMITTEDはlockingです。既存RCSI、許可済みSNAPSHOT、AG secondaryのsnapshot mappingを区別します。本番の設定をCOUNTのため自動変更しません。ApplicationIntent=ReadOnlyだけで実secondaryを保証せずrole/routingを照合します。

### Q 分析用SQL Serverを別に置く構成はあるか

A MicrosoftはAlways Onのread-scaleでreporting・analyticsをsecondaryへ分離する構成を案内しています。HA以外の読取拡張を目的とする構成もあります。[Microsoft公式構成例](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/read-scale-availability-groups?view=sql-server-ver17)

分析用DWH・ETL済み表も選択肢ですが、元表の全行数とは別指標になり得ます。上の分析基盤の対応確認をSQL Serverにも適用します。Azure SQLのread scale-outは別サービスの機能で、利用tier・routing・監視条件をそのサービス仕様で確認し、self-hosted SQL Serverへ設定をそのまま転用しません。[Azure SQL例](https://learn.microsoft.com/en-us/azure/azure-sql/database/read-scale-out?view=azuresql)

### Q 実行すべきSQLはCOUNTかCOUNT_BIGか

A 大規模表はCOUNT_BIG(*)を使います。COUNTはint、COUNT_BIGはbigintなので、後者が大きな件数を扱えます。[公式仕様](https://learn.microsoft.com/en-us/sql/t-sql/functions/count-big-transact-sql?view=sql-server-ver17)

```sql
SELECT COUNT_BIG(*) AS exact_rows FROM [dbo].[Target];
```

この1文だけを業務接続に貼り付けません。[SQL Serverガイド§3](sql-server.md#3-identityを確認し対象限定の参考値を取る)のcontext互換性・専用session初期化・所有確認を行い、[§4](sql-server.md#4-正確値既存versioningと隔離の両方が必要)のRCSIまたはSNAPSHOT手順のCOUNT文として使います。AG経路は同節の別条件です。CONTEXT_INFOがRLS等の業務用途なら本方式はHOLD。管理者でもRLSを無視できるとはしません。

### Q indexや暗号化、データ量によってSQLを変えるか

A 対応する通常disk表ならCOUNT_BIG(*)を維持します。heap、clustered、二次索引、columnstoreでは実計画・scan量が変わりますが、索引hintやMAXDOPを一律に強制しません。filtered indexの行数は全表の代用ではありません。MAXDOPはCPU quotaではありません。

TDEでサーバー鍵が利用可能なら同じSQLです。Always EncryptedはTDEと異なり、実driver・接続・鍵要件を別確認します。「client鍵なしでも必ず実行できる」とはしません。memory-optimized、view、external、temporal historyは対象と意味を確認し、[状態別の分岐](sql-server.md#2-状態を組み合わせて基本経路へ振り分ける)へ戻します。通常表のSQLを自動適用しません。

### Q NOLOCKならサービスを止めず正確に数えられるか

A 採用しません。dirty readでは未コミット値等を読み得て、正確snapshot件数の代用にはなりません。READPASTも飛ばした行を数えないため不適切です。versioningでもSch-SとSch-Mの競合やI/O競合は残ります。[locking/versioning公式](https://learn.microsoft.com/en-us/sql/relational-databases/sql-server-transaction-locking-and-row-versioning-guide?view=sql-server-ver17)

## 精度と運用に共通する質問

### Q 統計の件数はどれくらい信用できるか

A repoの固定観測を再集計すると次の通りです。各集合の分母を分けています。

| 方法と観測条件 | 観測した精度 | 判断への使い方 |
|---|---|---|
| MySQL非空InnoDB55表、20 pages、ANALYZE前 | 絶対誤差中央値0.2141%、±5%以内50/55、範囲−21.1538〜＋24.3754% | 多くは近いが全表5%以内ではない |
| 同55表、ANALYZE後 | 中央値0.2471%、±5%以内50/55、範囲−18.4832〜＋19.5881% | 更新すれば必ず改善するわけではない |
| MySQL行幅偏りの別fixture、20 pages | ＋4.5604〜＋38.7313% | 上の55表と混ぜず、偏りを別条件として扱う |
| SQL Server partition DMV、非空9表×3段階 | 27観測すべて真値と一致 | 静止状態と小さな確定INSERT後の観測。更新中の保証ではない |
| SQL Server optimizer lab_id.rows、同集合 | −0.0419824〜0% | 統計更新時の件数でありpartition counterとは別物 |

元データは[MySQL固定JSON](../../results/reference/review-20261107T155750Z-26743/results/requested_metadata_accuracy.json)と[SQL Server固定JSON](../../results/reference/final-sqlserver-52556dd/results/sqlserver/runs/20261107T230105Z-8533a31e/result.json)。MySQLは各表各phase1観測、非InnoDB3表を除外。SQL Serverはauto stats OFF・エミュレーション・限定fixtureです。製品間の精度順位や「自分の表も約91%の確率で5%以内」を示しません。−27.785%という過去例は別の初回集合です。[集合別の詳細](../table-count-calibration.md)

5%程度の経験的見込みなら自分の条件で校正し、その観測範囲として使います。5%上限が必須なら統計だけでは採用せず、適格な正確COUNT等へ。真値がない場合はUNKNOWNとし、参考統計としてのみ表示します。

### Q 自分の表の誤差をどう観測するか

A まず「今の本番cache値」と「復元先で再生成された統計」のどちらを評価するか決めます。以下は本番を止めず、既存の承認済み固定コピーで行う流れです。

1. 統計を更新する前の推定E、取得時刻、統計生成時刻または不明、設定、対象世代・行集合を保存。
2. 対応する固定コピーで正確Nを取得。元の本番Eを評価するなら、元Eの評価時点とコピーのデータ時点の対応が必要。
3. コピーへの書込・apply・DDL等がないことを確認し、統計状態を再読取。変わった場合は別観測として区別。
4. N>0なら `100*(E-N)/N` と行数差を保存。N=0は相対誤差を定義せず、未知・timeoutを0扱いしない。
5. 表・条件ごとに中央値、過小／過大極値、±1/5/10%内の件数/有効数、欠測、表数・反復数を記録。更新・再構築・世代変更で適用性を再確認。

統計が古いこと自体は評価対象です。統計生成時刻と評価時刻を別に保存し、評価時点の行集合をNと対応させます。SQL Serverではmetadataはversioning対象ではなく、SNAPSHOTでCOUNTとDMVを包むだけでは時点一致を保証しません。[公式仕様](https://learn.microsoft.com/en-us/sql/t-sql/language-elements/transaction-isolation-levels?view=sql-server-ver17)

### Q 真値なしで誤差率を計算できるか

A 今回確認した公式のmetadata項目だけでは得られません。MySQLのsample pages、SQL Serverのrows_sampled/modification_counterは誤差率ではありません。繰返し同じ値が返ること、SHOW/EXPLAIN等の一致も真値への近さを証明しません。[MySQL統計](https://dev.mysql.com/doc/refman/8.4/en/innodb-persistent-stats.html)・[SQL Server統計properties](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-views/sys-dm-db-stats-properties-transact-sql?view=sql-server-ver17)

既存の正確な基点と、欠落・重複・rollback・DDLを扱った完全なcommit済み増減台帳があれば真値を再構成する選択肢はあります。単なるDML回数では不十分です。COUNTのために本番へ新しいtrigger/CDCを無断導入しません。

### Q 大量の表を並列に数えれば速いか

A 個々のCOUNTが予算内でも合計I/O・CPU・undo/version保持が予算を超えます。共有資源単位でまず直列。取得済みの値を時点付きで再利用し、必要頻度を減らします。並列化は所有者が合計予算を適格化した場合だけです。別snapshotの表・shard合計を全体同時点の正確値としません。

### Q タイムアウトしたら次の表へ進んでよいか

A server上のquery・transaction終了と回復を確認するまで進みません。MySQLは所有確認したKILL QUERY等、SQL ServerはCancelとclient finally/rollback、必要なら所有確認した管理接続の終了処理を使います。古いIDへ盲目的にKILLせず、不明ならincident担当へ引き継ぎます。[取消と回復の実行手順](operations-runbook.md)

### Q COUNTを毎リクエスト発行するべきか

A UIの概数やレポートなら、要件を満たす既存export・時点付きcache・集計値を優先します。正確な現在件数が必須なら、初期化・増減・再試行・削除・世代変更まで整合する常時計数を別機能として設計します。AUTO_INCREMENT/IDENTITY/MAX(id)から行数を作りません。これは頻度と要求を見直す設計方針で、今回のrepoで常時計数を検証したものではありません。

## Web事例の読み方

2026-11-08確認。AWSのread replica、Microsoftのread-scale AG、Azure SQLのread scale-outは、分析・レポートを読取先へ分離する公式の構成例です。特定企業の無劣化実測や普及率調査ではありません。接続先分離は有用でも、遅延・共有資源・HA・元表との同等性を省略できません。クラウドの管理権限や機能対応は製品・tier・版別に確認します。
