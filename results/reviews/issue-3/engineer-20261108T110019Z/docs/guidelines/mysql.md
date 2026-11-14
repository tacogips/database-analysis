# MySQL：大規模テーブルの全件数取得ガイドライン

ここでいう「データ量」は、**業務上のWHEREを付けない表全体のROW COUNT**です。容量bytes・割当量、アプリの有効件数、soft-delete除外件数は別指標です。
正確値も読取snapshotに見える行数であり、未コミット行・削除済み物理版の総数ではありません。サポート中のMySQLを対象とし、採用版のサポートと機能を所有者が確認します。
**承認済みexport → 対象限定の参考統計 → 必要な場合だけ事前適格化した資源隔離先の通常COUNT**を基本経路にします。
「最適」は必要精度・鮮度・期限・サービス予算を満たす候補で追加取得費用が小さいことです。全条件最速、ゼロ負荷、スキーマロックなし、行数Nだけの安全閾値は約束しません。

## 1. 最初の選択

1. 所有者、DB/表/シャードと世代、全行か業務可視範囲か、必要精度・許容時点・期限を確定します。
2. 既存の承認済み台帳・監視exportに要求を満たす値があれば、取得元・時点・精度種別を付けて使います。
3. 粗い規模でよければ、追加取得予算のある接続先だけで§3のTABLE_ROWSを取得します。統計照会も無料ではありません。
4. 誤差の経験的見込みでよければ、同条件の校正を確認します。必須の誤差上限・正確値なら§4へ進みます。
5. 実行先のデータ同等性・HA role・資源分離・鮮度・監視・取消・終了確認が成立しなければ、参考値への精度緩和、古い隔離コピーへの鮮度緩和、設備追加のいずれかを所有者へ返して保留します。

| 必要精度・鮮度 | 最初に選ぶ方法 | 結果の表示と不足時の分岐 |
|---|---|---|
| 既存値の時点でよい | 承認済みexport | 元の方式・時点を継承。値だけを「現在値」にしない |
| おおよその規模 | I_S.TABLES.TABLE_ROWS | 参考統計、誤差上限なし。鮮度不明は不明と記録 |
| 条件付きの経験的精度 | 同世代・同時点の真値と照合した統計 | 条件・観測誤差付き。条件不一致なら参考値か隔離COUNT |
| 条件付きの確率区間が使える | 適格な隔離先の標本prototype | §5の全条件を証明。未達は要求精度についてUNKNOWN |
| 必須の誤差上限／正確値 | 適格な隔離先のCOUNT(*) | そのsnapshotの正確値。鮮度不足なら要求未達 |
| primary現在値・全シャード同時点が必須 | 整合性を設計した常時計数や固定復元点等を別途設計 | 別snapshotのCOUNT合計で代用しない。経路がなければ保留 |

## 2. 表状態を組み合わせて選ぶ

下表の物理状態に、続く活動・権限・取得先の条件を重ねます。例えば「文字列PK＋100列＋TDE＋更新中」も、SQLで読める通常InnoDBなら同じ基本経路です。
個別状態の性能が未計測でも通常COUNTの意味は変わりません。PKや行幅が不明なだけで適格な隔離先のCOUNTを放棄せず、費用を未校正として予算で制限します。

| 物理・統計状態 | 参考値／正確値への経路と注意 |
|---|---|
| InnoDB BASE TABLE、空・極小・巨大 | export/TABLE_ROWS → 必要なら隔離COUNT。参考0や小さい推定からprimary走査を許可しない |
| PKあり／なし、整数／文字列／複合、欠番・疎・偏在 | 同じ経路。AUTO_INCREMENT、MAX−MIN+1、ID範囲は件数の代用にしない |
| 二次索引なし／狭い索引／複数／invisible | 同じ経路。隔離先で既存索引と通常EXPLAINを確認。PRIMARY強制・索引作成・可視性変更を自動化しない |
| 80/100列、可変長・外部LOB・行外格納、圧縮 | 同じ経路。列数から実読取bytesや安全性を決めず、索引・割当・更新状況を見る |
| partition表全体 | TABLESの表1件を使用。正確値は表全体COUNT。TABLESとPARTITIONSを足したり、重複行を合計しない |
| partition別の件数が必要 | 全表件数とは別の要件。所有者がpartition識別・世代・同一snapshotを設計。更新中の個別COUNT合計を全表正確値にしない |
| cache warm/cold、不明、古い／未初期化統計 | 正確COUNTの経路は同じ、費用は別条件。統計の初期化・鮮度不明は校正を棄却し、cache flushや自動ANALYZEをしない |
| metadata既知の正値／0／NULL／欠落／拒否 | 正値も参考。0は空表の証明なしなら要求件数UNKNOWN。NULL・欠落・拒否も理由付きUNKNOWN、エラーを0へ変換しない |
| TDE、サーバー鍵が利用可能 | 同じ経路。SQLクライアントがseedを知らなくてもサーバーの鍵利用が前提 |
| TDE鍵なし／暗号化backupの復元 | 所有者がkeyring・鍵・復元手順を用意。サーバーが読めなければ取得不可、ファイルだけからCOUNTできるとしない |
| アプリ暗号文、client seed不明 | サーバーがSQLで表を読めれば平文解釈なしのCOUNTへ。接続・権限・実driverの対応は確認し、復号式を追加しない |
| 非InnoDB、MyISAM、MEMORY等 | 承認済みexport・エンジン別の参考台帳を使用。所有者が専用隔離先／export手順を指定し、InnoDBの非ロック性や本番COUNTを転用しない |
| temporary／session-local、view | TABLESではtemporaryが列挙されない。作成sessionの所有者台帳、view定義・可視範囲に対応したexport／隔離手順を用意。欠落を0にしない |
| 外部・remote storage、非標準形式、種別不明 | 接続先・エンジン・remote側影響を所有者が特定。承認済み参考値か専用exportを使い、generic COUNTに自動投入しない |

TABLESの表種別・推定値・temporaryの制約は[公式仕様](https://dev.mysql.com/doc/refman/8.4/en/information-schema-tables-table.html)、invisible索引は[公式仕様](https://dev.mysql.com/doc/refman/8.4/en/invisible-indexes.html)を参照します。
TDEのサーバー側鍵要件は[公式暗号化仕様](https://dev.mysql.com/doc/refman/8.4/en/innodb-data-encryption.html)に従います。アプリ暗号文の扱いはCOUNTの意味からの推論で、実アプリ性能の観測ではありません。

| 活動・権限・取得先 | 重ねる条件／戻り先 |
|---|---|
| INSERT/UPDATE/DELETEが多い、長い未終了transaction | 同じ参考／隔離COUNT経路。統計誤差・snapshot時点・undo保持と複製適用を監視。未コミット行を含めない |
| soft-delete・tenant・権限制限 | 全表COUNTはsoft-delete行も含む。viewやアプリの可視件数は別要件。主体・範囲を合わせ、全表統計をtenant正確値と表示しない |
| DDL/rebuild/移行/TRUNCATE中、世代不明 | 名前だけで同一表としない。所有者の変更台帳で世代と完了を確認し、競合中は新規投入停止。安定した隔離コピーか保留へ |
| primaryだけがある | 既存exportか予算付き参考統計まで。粗い小表推定はprimary COUNT許可ではない。正確値は隔離先を用意するか保留 |
| 専用replica | データ同等性、HA role、資源、鮮度を別々に適格化（§3・§4）。GTID追随やlag 0だけでは採用しない |
| 共用replica／共有資源 | replicaという名前だけで許可しない。競合を隔離・制限できなければ独立した復元コピーへ |
| 独立した承認済み復元コピー | 完全なbackup・復元／適用来歴、対象世代・鍵・固定復元点と許容鮮度、HA対象外である承認を確認。作成作業自体にも元DBの予算が必要 |
| 取得先なし／不明、shard欠落 | 取得済み分の小計と欠落一覧を返す。全体はINCOMPLETE、UNKNOWNを0として合計せず、必要設備・担当を明示 |

## 3. 対象限定で参考統計を取る

**コピー前に**所有者の接続・lock待ち・実行・総時間予算とdriver deadline、監視・取消担当を確定します。下の3秒・1000msは設定例であり、本番安全値ではありません。
自動transaction参加を無効にした新規・非pool・専用sessionを使い、既存transactionがないことを接続側でも確認します。既存の仕事にautocommitを設定してcommitしないでください。
表に触る前に初期化します。失敗・想定外の設定は停止。対象DB/表は承認済み台帳から限定し、高価な全サーバー探索をしません。
HA所有者の既存トポロジexportでrole・昇格候補・topology/routing epoch・採取時刻／許容age・イベント源・承認を確認し、下の実server UUID・read_only/super_read_only、承認channelのSHOW REPLICA STATUSのChannel_Name/Source_UUIDと照合します。実際のGR構成だけは既存P_Sのmember role/stateも使用します。[読取観測・権限と失効手順](operations-runbook.md#3-mysql接続所有mdlundo複製)
台帳と現物の不一致・role不明はHOLDとし、観測のためにGRANT／global instruments／構成を自動変更しません。read_onlyフラグやchannelの存在だけを権威あるrole判定にしません。フラグは変更可能な現状態で、過去のlocal writeなしも証明しません。[read_only公式](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html#sysvar_read_only)・[replica status](https://dev.mysql.com/doc/refman/8.4/en/show-replica-status.html)

```sql
SET SESSION autocommit = 1;
SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED;
SET SESSION lock_wait_timeout = 3;       -- 例：所有者予算へ置換
SET SESSION max_execution_time = 1000; -- 例：driver期限も別途設定
SELECT CONNECTION_ID(), @@server_uuid, @@hostname, @@version, @@session.autocommit,
       @@session.transaction_isolation, @@session.information_schema_stats_expiry,
       @@global.read_only, @@global.super_read_only;
SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, ENGINE, TABLE_ROWS,
       AVG_ROW_LENGTH, DATA_LENGTH, INDEX_LENGTH, CREATE_OPTIONS, UPDATE_TIME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'db' AND TABLE_NAME = 't';
```

返却したserver identity・DB/表名・TABLE_TYPE・ENGINEを照合し、InnoDB BASE TABLEだけを通常COUNT経路へ送ります。結果なしは「存在しない」と断定せず、非可視・temporary・変更中も確認します。
既存の`information_schema_stats_expiry`は変更しません。0なら毎回engine取得、非0でもcache miss/期限切れでengine読取が発生し、cache-onlyにはできません。
TABLE_ROWSは推定、UPDATE_TIMEは統計更新時刻ではありません。SHOW TABLE STATUS・n_rows・EXPLAINの一致も独立した精度証明ではありません。
割当量は容量の概数でCOUNTの実読取量ではありません。未知・0参考値を別欄に残し、空表認定は同世代・適切な時点の独立した正確値等によります。
DB・共有ストレージ単位で対象を小分けにし総時間も制限します。自動ANALYZE、索引新設、cache flush、実行を伴うEXPLAIN ANALYZEは行いません。

## 4. 正確値は適格な隔離先で数える

次のgateを独立に記録し、すべてを満たした取得先だけで実行します。[runbookの記入欄](operations-runbook.md#1-採用前の記入欄)に根拠・適用範囲／表世代・確認時刻・承認者を残します。

- **データ同等性（DATA EQUIVALENCE）**：source UUID／channel→元schema/table/世代→コピー側schema/table/世代の対応を所有者台帳で確認。名前書換え・multi-sourceの全関係、現在と過去のsource/binlog filter・replicaのchannel/global filterを含めます。初期コピーの完全性と、その後すべての再seed・rebuild・DDL、skip・空GTID・手動repair・local writeの履歴を、既存の承認済み整合性監査／変更記録で確認します。「履歴なし」は根拠なしに記入しません。[初期データ](https://dev.mysql.com/doc/refman/8.4/en/replication-setup-replicas.html)・[filter仕様](https://dev.mysql.com/doc/refman/8.4/en/replication-rules.html)・[skip仕様](https://dev.mysql.com/doc/refman/8.4/en/replication-administration-skip.html)
- **鮮度（FRESHNESS）**：同等性が適格でも、許容snapshot点、復元／適用位置と採取時刻、実COUNT snapshotとの対応限界、lag・許容ageを別判定。GTIDやlag 0からprimary現在正確値とはしません。
- **資源**：CPU/I/O/memory・共有host/storage/network・通常読取／apply余力と上限、監視・取消・回復余力を確認。監視だけでは資源分離を代替できません。HA roleの継続適格性も§6で確認します。

replicaでfilterされたtransactionもGTIDは保存されるため、現在のGTID・lag・フラグでは来歴を証明できません。[GTID Life Cycle](https://dev.mysql.com/doc/refman/8.4/en/replication-gtids-lifecycle.html)に基づく採用方針です。同等性が不明なら、COUNTが正常終了しても正確なのは**このコピー自体のsnapshot件数だけ**です。元サービス表の正確件数要求は**UNKNOWN/HOLD**に戻し、鮮度良好で覆しません。確認のために本番COUNT／全表checksum／ANALYZE／repair／filter変更を自動投入せず、既存承認記録で不足なら保留します。
§3と同じ新規専用sessionを表に触る前に初期化し、予算を設定し直します。下は同sessionで確認済みの識別子を安全に引用した例です。

```sql
SELECT /* count_job: replace_with_unique_job_id */ COUNT(*) AS exact_rows
FROM `db`.`t`;
```

通常のRCまたはRR、autocommit=1の1文を使います。`COUNT(*)`はNULLのある行も数えます。COUNT(nullable列)は代用不可、COUNT(1)も高速化策にはしません。[COUNT公式](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)
通常SELECTは行ロックを取りませんが、MDL・DDL待ち・undo保持・CPU/I/O競合は残ります。[整合性読取](https://dev.mysql.com/doc/refman/8.4/en/innodb-consistent-read.html)・[MDL](https://dev.mysql.com/doc/refman/8.4/en/metadata-locking.html)
FOR SHARE/FOR UPDATE、SERIALIZABLE、LOCK TABLES/FTWRLは除外します。DML/CTAS/SETへCOUNTを埋め込む形は通常SELECTと異なり読み取り元をロックし得るため使いません。
partitionも表全体を1文で数えます。別文・別表・別シャードのsnapshotは別時点で、更新中の合計を「全体の現在正確値」と呼びません。
GTIDや遅延0もCOUNTのsnapshotとprimary現在値の一致を証明しません。同時点必須なら固定した隔離復元点等を用意し、鮮度要件を満たせなければ要求未達です。

## 5. 校正・標本を選べる条件

統計の校正は同じ表世代・統計profile・統計時点・真値時点・版・sample pages・変更条件に対応付けます。行幅、PK、partition、暗号化、cache等の費用条件も記録します。
論理復元や再構築で配置・統計が変わるため、復元先の誤差から古い本番cache統計の誤差を認定しません。未知・不一致・大量変更後は校正を棄却します。
標本はSQLで見える一意整数PK、固定snapshot、セル上限、無作為抽出、停止規則、誤差確率配分等の**全適格条件を証明した隔離先**だけのprototypeです。
文字列PK・PKなし・極端な欠番や偏在で条件が成立しない場合は参考統計か通常COUNTへ戻ります。標本0から空／小表と推定せず、反復の安定を精度認定にしません。
信頼区間、今回の実現誤差、将来保証を分けます。詳細は[校正手順](../table-count-calibration.md)と[標本仕様](../probability-count.md)を参照し、初回台帳取得の既定にしません。

## 6. 開始・停止・終了・回復チェック

- **開始**：対象・世代・主体・権限・取得先とデータ同等性・HA role・鮮度を確認。実行担当と別管理接続の監視／取消担当を置き、所有処理の識別・取消・終了確認ができなければ開始しません。
- **予算**：平常p99・失敗率・CPU・I/O・待機・複製遅延・DDL待ち・長いread view/undoを確認。所有者が資源上限、観測間隔、停止値、回復窓を記録します。
- **期限**：検出・取消遅延を見込みSLO違反より手前で止められる余裕を確保。文上限・MDL待ち上限・driver command timeout・呼出し全体のdeadlineを併用します。
- **投入**：共有資源単位でもまず直列。行数Nや列数だけで並列数・安全閾値を決めず、余力を確認してのみ拡大します。
- **停止**：昇格・role/topology/routing/epoch変更、roleの期限切れ・取得不可・不明で適格性は失効します。停止値到達、監視不能、想定外DDL/負荷でも新規投入停止。取消担当は接続ID・一意jobタグ・取得先・account・開始文脈で現在の所有queryを照合してKILL QUERY等の承認済み取消を行います。
- **終了**：古いIDの再利用先を盲目的にKILLせず、server上の文・transaction終了を確認。driver timeout・切断だけで完了とせず、終了不明なら次の表へ進みません。
- **回復**：取消後もcache/I/O・undo等を回復窓まで監視し予算内へ戻ることを確認。自動再試行せず、失敗はUNKNOWNと理由を保存します。
- **判定**：処理完了、要求精度、サービス予算、後始末を別々に記録。未開始・未完了・timeoutを落としてSLO合格にしません。[取消仕様](https://dev.mysql.com/doc/refman/8.4/en/kill.html)

実行前に[運用runbookの記入済み独立復元先／専用replica例と空欄template](operations-runbook.md#1-採用前の記入欄)を埋めます。共有host/storage/networkと実際の上限機構を確認し、CPU/memory制限やResource GroupsだけでI/O上限を認定しません。検出・配送・判断・取消・rollback・jitterの時間上界より長い停止余裕を確認し、p99のms差から余裕秒を推定しません。
[具体的観測SQL・権限](operations-runbook.md#3-mysql接続所有mdlundo複製)ではP_S threadsとconnection所有台帳、MDL、INNODB_TRX/undo、replica適用状態を追跡します。instrumentation不足・部分可視・権限拒否はunavailableで停止し、global instrumentsを自動有効化しません。KILL QUERYだけではtransaction/read view解放を保証しないためclient finallyが自分のtransactionを回収し、所有接続とserver終了を確認します。識別不一致や終了不明では再投入しません。
HA変更で失効した結果は承認までHOLD。自動再接続・再試行は禁止し、driver/proxy retryで新しい物理sessionになった場合もidentity・role・データ同等性・資源・鮮度をすべて再適格化します。実backendと専用leaseを固定・観測できなければHOLDです。
昇格候補はHA所有者の手順・イベントと停止gateを連携し、必要な場合は昇格後にサービス負荷を受け入れる前の取消・保持解除ackまで確認します。連携できなければ独立復元先へ戻します。ポーリングは検出遅延を狭めるだけで、競合なくprimary負荷を防ぐ保証ではありません。HAを優先し、COUNT終了を必須failoverの妨げにしません。終了不明はincidentへ引継ぎ、新規COUNTを禁止します。これは[roleの現状態仕様](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-replication-group-members-table.html)と[取消の制約](https://dev.mysql.com/doc/refman/8.4/en/kill.html)を踏まえた運用方針です。[共通の失効・HA引継ぎ手順](operations-runbook.md#1-採用前の記入欄)を満たせない／連携未検証ならHOLDとし、自動failover実験は要求しません。

## 7. 表・シャードごとの記録と観測限界

各行に、job ID、DB/表/シャード・世代、対象範囲・主体、取得先identity/版/role、HA所有者・topology/routing epoch・採取時刻／age・イベント源・承認、同等性監査のscope/世代/時刻、方式、値またはUNKNOWNと理由を保存します。
精度種別（参考／経験的校正／条件付き区間／正確snapshot）、開始終了時刻、snapshotの説明、GTID等の取得位置とその取得時刻、backup/復元点、遅延・許容鮮度、校正条件を添えます。
snapshotとログ位置を対応付けられない場合はその限界も残し、取得位置をCOUNTの厳密なsnapshot IDとして偽装しません。各予算、到達停止値、取消、server終了、回復、後始末を記録します。
期待shard一覧と取得済み一覧を突合し、欠落・UNKNOWN・異なる範囲／精度／時点を明示。互換な取得済み値だけの小計とINCOMPLETEな全体を分けます。

過去に採用された[凍結実行の統計JSON](../../results/reference/review-20261107T155750Z-26743/results/requested_metadata_accuracy.json)と[極値監査](../../results/reference/owner-acceptance-20261107T161659Z/reports/owner-essential-fresh-metadata-extrema.json)では、非空InnoDB55表の各1観測、既定cache・20 pagesで、ANALYZE前の500,000行が621,877行（+24.3754%）、後の1,000,000行が1,195,881行（+19.5881%）でした。
これは一律10%を超える例で、全条件の誤差上限ではありません。小表一致、ANALYZEやpage増量も精度保証にしません。費用比較は[校正資料の条件付き比較](../table-count-calibration.md#4-代表fixtureと条件付き標本の位置付け)へ進みます。
[所有者proof](../../results/reviews/final-all-proof.json)の縮小白紙実行は8.4/9.7/26.10系の専用コンテナ観測です。環境・cache・実行順の制約があり、本番SLOや最速順位は未認定です。
[費用比較の元JSON](../../results/reference/review-20261107T155750Z-26743/results/requested_accuracy_cost_frontier.json)は8.4.13・buffer pool 256 MiB・parallel read threads=1の専用labです。host/VM cacheと先行COUNTの持越しは未制御で、本番hardwareの性能予測に使いません。
1億行の細表と75万行の50/100列TDE表は別条件です。**1億行×80/100列×TDE、実アプリ暗号化、物理replica分離、本番のcold/warm性能は未計測**で、組み合わせて外挿しません。
既存の旧観測・当時の採用実行・却下された時計窓を混ぜない根拠と完全logは[凍結詳細ガイド](../table-count-guideline.md)・[校正資料](../table-count-calibration.md)に保存されています。利用者がlabやRielaを実行する必要はありません。
labのreplicaは`t_repl`だけを複製します。その良好なlagから他表の複製範囲・同等性を認定しません。filter／driftやHA連携の新実験は行っていません。
監査付記：現行版の受入可否と対象hashは[Issue 3受入記録](../../results/reviews/issue-3/acceptance.json)を照合します（所有者確認・別セッションSol 2役の結果と対象hashを確認し、記録なし・不一致は受入保留）。[Issue 2修正前snapshot](../../results/reference/issue-2-pre-fix-de1c79c/manifest.json)と保存された[Issue 1受入](../../results/reference/issue-2-pre-fix-de1c79c/results/reviews/issue-1/acceptance.json)は旧hashだけの証跡です。最終査読後に変更したhashは再査読が必要です。今回の文書修正は既存実行や本番SLOの再認定ではありません。

[Issue 3修正前snapshot](../../results/reference/issue-3-pre-fix-16cc9b3/manifest.json)と[歴史的Issue 2受入](../../results/reference/issue-3-pre-fix-16cc9b3/results/reviews/issue-2/acceptance.json)は保存された旧hashだけに適用します。現行Issue 3集約受入は所有者の専用実行・意味/source/後始末監査と別セッションSol 2役の最終査読待ちです。記録なし・hash不一致はHOLDであり、受入済みとは扱いません。
