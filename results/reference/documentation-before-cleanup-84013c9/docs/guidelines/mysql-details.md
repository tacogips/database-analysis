# MySQL：詳細な実行条件・検証結果

**既存環境を上書きしない方針**：業務データ・表定義・DB全体の設定・権限・アクセス制御を変更しません。業務が使用中の接続も流用しません。以下のSET文は新しく作った集計専用接続のtimeoutや読取方法だけを指定する例です。接続単位の設定も変更できない運用では、その設定がすでに満たされる接続を担当者が用意します。読取にもI/O等の負荷はあるため、設定を変更しないことだけで無影響とは判断しません。

[全件数取得のQ&Aと精度の観測方法](#qa)

ここでいう「データ量」は、**業務上のWHEREを付けない表全体のROW COUNT**です。容量bytes・割当量、アプリの有効件数、soft-delete除外件数は別指標です。
正確値も読取snapshotに見える行数であり、未コミット行・削除済み物理版の総数ではありません。サポート中のMySQLを対象とし、採用版のサポートと機能を所有者が確認します。
**対象DBの全テーブルのTABLE_ROWSとENGINEを取得 → 概算値で用途を満たせるか判断 → 必要なテーブルだけ集計用DBでCOUNT**を基本経路にします。
「最適」は必要精度・鮮度・期限・サービス予算を満たす候補で追加取得費用が小さいことです。全条件最速、ゼロ負荷、スキーマロックなし、行数Nだけの安全閾値は約束しません。

## 読み方と性能調査へのリンク

[実行フロー](#実行手順)で方式を選び、このファイル内の対象条件・SQL・停止／終了／回復条件を確認し、[Q&A](#qa)で判断理由を確認します。巨大表の正確COUNTは実行条件を確認した集計用DBが前提です。TABLE_ROWSなどの概算件数と正確値、観測上の精度と保証を分け、条件を確認できなければ、取得方法の切替手順へ進みます。

DBでの性能調査を詳しく確認する場合は、[検証条件・費用比較の説明](../table-count-calibration.md#4-代表fixtureと条件付き標本の位置付け)、[COUNT／統計の費用比較JSON](../../results/reference/review-20261107T155750Z-26743/results/requested_accuracy_cost_frontier.json)、[詳細検証ガイド](../table-count-guideline.md)、[検証の実行記録](../../results/reviews/final-all-proof.json)へ進みます。これらは専用検証環境の根拠です。RDSでの重いSELECTによる悪化率は未計測で、本番の性能予測へ転用しません。

このガイドでいう**集計用DB**は、サービスのリクエストを処理するDBとは負荷を分けてCOUNTを実行するためのDBです。例は、サービスのreadに使っていない分析専用read replicaや、保存済みバックアップから新しく作る、サービスに使わない別DBです。**同じインスタンスに別schemaを作るだけでは、CPU・メモリ・I/Oは分離されません。** 別インスタンスでもstorage/networkや複製元への影響は確認します。

「実行条件を確認する」とは、①必要なデータが揃い、時点も用途に合う、②サービスと資源を共有する箇所と負荷の上限が分かる、③実行中に監視でき、悪化したら中止・終了確認できる、の確認です。replicaを用意しただけでは十分ではありません。以下の各節で確認方法を説明します。

## 実行手順

[InnoDB：6段階の手順とSQL](mysql-innodb.md#実行手順)／[MyISAM：5段階の手順とSQL](mysql-myisam.md#実行手順)

このファイルは詳細確認用です。実行時は上記のengine別マニュアルを使います。

## 取得できる方法へ切り替える

このガイドの目的は件数を取得することです。確認不足や予算超過を最終回答にせず、次の作業で解消してから取り直します。

| 今の方法で進めない理由 | 次に行う作業 |
|---|---|
| 統計が取得できない・誤差が要件を超える | 同じ統計を繰り返すだけでなく、DB担当者が既存権限で取得できる経路を確認する。解決しなければ集計用DBで正確COUNTへ切り替える |
| 専用の分析環境がない | サービスのSELECTも処理するread replicaを次の候補にする。[InnoDBの事前準備](mysql-innodb.md#事前準備作業環境を選ぶ)／[MyISAMの事前準備](mysql-myisam.md#事前準備作業環境を選ぶ)に従い、余力・上限・閑散時間・1本・期限・監視・取消を確認する。writerへ切り替えない |
| 実行中にサービス応答・複製遅延・資源が上限を超える | 実行中queryを取消し、server上の終了と回復を確認する。同じ条件で再試行せず、DBの処理能力・同時実行数・取得時間帯・共有資源を見直して取り直す |
| コピーの時点が古く、要求を満たさない | 必要な時点までのデータを復元・適用できるコピーを準備する。コピー側のCOUNT結果には、その時点を記す |
| MyISAM・その他のengine／不明 | §3の対象表metadataまたは既存の表定義でengineを確認する。MyISAM等は、engine・クラウドの対応範囲を確認し、既存の整合したコピーまたはengine対応の取得方法をDB担当者が準備して数える。RDSのsnapshot復元を当然に使えるとはしない。元のサービスDBへInnoDB用の安全性説明を流用しない。未対応engineの専用手順はDB担当者が用意する |

時点付きのCOUNTと「更新され続ける本番の現在値」は要件が異なります。複数表も含めて指定時点の正確値が必要なら、その時点に揃えたコピーを用意します。常に最新の件数が必要なら、書込み・削除・再試行まで整合する件数管理を別途設計します。異なる時点の値を最新値と表示して完了にはしません。

## 1. 件数一覧を見てから選ぶ

- 接続先・実行期限・監視と取消を準備し、§3のSQLで件数を取得する。
- 一覧を見て、概算のまま使う表と、正確に数える表を選ぶ。件数が多い表は概算で済ませ、少ない表だけCOUNTする選び方でもよい。
- 概算でよい表は、その値と取得方法を記録する。
- 正確に数える表や値を取得できなかった表は、§4で実行先を確認してCOUNTする。概算件数が小さいことだけを理由に、サービス用DBでCOUNTしない。

## 2. 表状態を組み合わせて選ぶ

下表の物理状態に、続く活動・権限・取得先の条件を重ねます。例えば「文字列PK＋100列＋TDE＋更新中」も、SQLで読める通常InnoDBなら同じ基本経路です。
個別状態の性能が未計測でも通常COUNTの意味は変わりません。PKや行幅が不明なだけで実行条件を確認した集計用DBのCOUNTを放棄せず、費用を未校正として予算で制限します。

| 物理・統計状態 | 参考値／正確値への経路と注意 |
|---|---|
| InnoDB BASE TABLE、空・極小・巨大 | export/TABLE_ROWS → 必要なら集計用DBでのCOUNT。参考0や小さい推定からprimary走査を許可しない |
| PKあり／なし、整数／文字列／複合、欠番・疎・偏在 | 同じ経路。AUTO_INCREMENT、MAX−MIN+1、ID範囲は件数の代用にしない |
| 二次索引なし／狭い索引／複数／invisible | 同じ経路。集計用DBで既存索引と通常EXPLAINを確認。PRIMARY強制・索引作成・可視性変更を自動化しない |
| 80/100列、可変長・外部LOB・行外格納、圧縮 | 同じ経路。列数から実読取bytesや安全性を決めず、索引・割当・更新状況を見る |
| partition表全体 | TABLESの表1件を使用。正確値は表全体COUNT。TABLESとPARTITIONSを足したり、重複行を合計しない |
| partition別の件数が必要 | 全表件数とは別の要件。所有者がpartition識別・世代・同一snapshotを設計。更新中の個別COUNT合計を全表正確値にしない |
| cache warm/cold、不明、古い／未初期化統計 | 正確COUNTの経路は同じ、費用は別条件。統計の初期化・鮮度不明は校正を棄却し、cache flushや自動ANALYZEをしない |
| metadata既知の正値／0／NULL／欠落／拒否 | 正値も参考。0は空表の証明なしなら要求件数UNKNOWN。NULL・欠落・拒否も理由付きUNKNOWN、エラーを0へ変換しない |
| TDE、サーバー鍵が利用可能 | 同じ経路。SQLクライアントがseedを知らなくてもサーバーの鍵利用が前提 |
| TDE鍵なし／暗号化backupの復元 | 所有者がkeyring・鍵・復元手順を用意。サーバーが読めなければ取得不可、ファイルだけからCOUNTできるとしない |
| アプリ暗号文、client seed不明 | サーバーがSQLで表を読めれば平文解釈なしのCOUNTへ。接続・権限・実driverの対応は確認し、復号式を追加しない |
| MyISAM | §3のTABLE_ROWS保持件数を使い、cacheと必要時点を確認。現在時点の件数が必要ならengine・クラウドの対応範囲に沿った取得方法を準備し、InnoDB用COUNT手順は流用しない |
| MEMORY等その他のengine | engine別の保存済み件数や取得方法を確認。InnoDBやMyISAMと同じ意味・ロック特性とは扱わない |
| temporary／session-local、view | TABLESではtemporaryが列挙されない。作成sessionの所有者台帳、view定義・可視範囲に対応したexport／サービス用DBと負荷を分ける手順を用意。欠落を0にしない |
| 外部・remote storage、非標準形式、種別不明 | 接続先・エンジン・remote側影響を所有者が特定。承認済み参考値か専用exportを使い、generic COUNTに自動投入しない |

TABLESの表種別・推定値・temporaryの制約は[公式仕様](https://dev.mysql.com/doc/refman/8.4/en/information-schema-tables-table.html)、invisible索引は[公式仕様](https://dev.mysql.com/doc/refman/8.4/en/invisible-indexes.html)を参照します。
TDEのサーバー側鍵要件は[公式暗号化仕様](https://dev.mysql.com/doc/refman/8.4/en/innodb-data-encryption.html)に従います。アプリ暗号文の扱いはCOUNTの意味からの推論で、実アプリ性能の観測ではありません。

| 活動・権限・取得先 | 重ねる条件／戻り先 |
|---|---|
| INSERT/UPDATE/DELETEが多い、長い未終了transaction | 同じ参考／集計用DBでのCOUNT経路。統計誤差・snapshot時点・undo保持と複製適用を監視。未コミット行を含めない |
| soft-delete・tenant・権限制限 | 全表COUNTはsoft-delete行も含む。viewやアプリの可視件数は別要件。主体・範囲を合わせ、全表統計をtenant正確値と表示しない |
| DDL/rebuild/移行/TRUNCATE中、世代不明 | 名前だけで同一表としない。所有者の変更台帳で世代と完了を確認し、競合中は新規投入停止。安定したサービスに使わない別DBのコピーか保留へ |
| primaryだけがある | 既存exportか予算付きTABLE_ROWSなどの概算件数まで。粗い小表推定はprimary COUNT許可ではない。正確値は集計用DBを用意するか保留 |
| 専用replica | §3・§4でコピー対象・更新反映時点・サービスとの共有資源・切替時の停止方法を確認 |
| サービスのSELECTも処理するread replica | 専用環境がない場合の候補。engine別手順の事前準備で余力・許容上限・監視・取消を確認し、閑散時間帯にまず1本だけ実行する。条件を用意できなければ別の実行先を準備する |
| 独立した承認済み復元コピー | 完全なbackup・復元／適用来歴、対象世代・鍵・固定復元点と許容鮮度、HA対象外である承認を確認。作成作業自体にも元DBの予算が必要 |
| 取得先なし／不明、shard欠落 | 取得済み分の小計と欠落一覧を返す。全体はINCOMPLETE、UNKNOWNを0として合計せず、必要設備・担当を明示 |

## 3. 対象DBの全テーブルの概算件数を取る

**コピー前に**所有者の接続・lock待ち・実行・総時間予算とdriver deadline、監視・取消担当を確定します。下の3秒・1000msは設定例であり、本番安全値ではありません。
自動transaction参加を無効にした新規・非pool・専用sessionを使い、既存transactionがないことを接続側でも確認します。既存の仕事にautocommitを設定してcommitしないでください。
表に触る前に初期化します。失敗・想定外の設定は停止。対象DB名を確認して限定します。そのDBの全通常テーブルを一覧にしますが、全サーバー・全schemaを無条件に探索しません。
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
WHERE TABLE_SCHEMA = 'db' AND TABLE_TYPE = 'BASE TABLE'
ORDER BY TABLE_NAME;
```

返却したserver identity・DB/表名・TABLE_TYPE・ENGINEを照合します。InnoDB BASE TABLEだけを通常COUNT経路へ送り、MyISAMは下の保持件数取得経路へ送ります。結果なしは「存在しない」と断定せず、非可視・temporary・変更中も確認します。
既存の`information_schema_stats_expiry`は変更しません。0なら毎回engine取得、非0でもcache miss/期限切れでengine読取が発生し、cache-onlyにはできません。
InnoDBのTABLE_ROWSは推定です。MyISAMは保持件数ですがcacheの制約を別確認します。UPDATE_TIMEは統計更新時刻ではありません。SHOW TABLE STATUS・n_rows・EXPLAINの一致も独立した精度証明ではありません。
割当量は容量の概数でCOUNTの実読取量ではありません。未知・0参考値を別欄に残し、空表認定は同世代・適切な時点の独立した正確値等によります。
DB・共有ストレージ単位で対象を小分けにし総時間も制限します。自動ANALYZE、索引新設、cache flush、実行を伴うEXPLAIN ANALYZEは行いません。

### MyISAMの保持件数を読むSQL

§3の専用接続・負荷上限・監視を確認したうえで、MyISAMの表だけを対象に次を使います。最初の一覧に必要な値が揃っていれば再取得は不要です。

```sql
SELECT TABLE_SCHEMA, TABLE_NAME, ENGINE, TABLE_ROWS AS stored_rows,
       @@session.information_schema_stats_expiry AS stats_cache_expiry_seconds,
       CURRENT_TIMESTAMP(6) AS observed_at
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'db'
  AND TABLE_TYPE = 'BASE TABLE'
  AND ENGINE = 'MyISAM'
ORDER BY TABLE_NAME;
```

MyISAMはengineが正確な行数を保持するため、InnoDBの標本による概算とは仕組みが異なります。ただし**information_schemaの統計cacheは別問題**です。このSQLを読んだ時刻が、件数情報の生成時刻とは限りません。cacheが古い・生成時刻を特定できない場合は、現在時点の正確値とは表示しません。expiry秒の設定だけから、各表の実cache ageを断定もしません。[TABLE_ROWSとcacheの公式仕様](https://dev.mysql.com/doc/refman/8.4/en/information-schema-tables-table.html)

このSQLはユーザーテーブルへのCOUNTやLOCK TABLESを発行しませんが、**metadata参照にもロック待ちやengineアクセスが残り得るので、「完全にロックしないSQL」とは保証しません。** cache回避のためexpiry=0やANALYZEを自動投入せず、取得期限・監視・取消を使います。失敗や時点不一致なら、切替手順でその時点に対応する既存記録やengine対応の整合したコピーを準備します。MyISAMのRDS replica／snapshot復元の制約はQ&Aを参照してください。

## 4. 正確値は実行条件を確認した集計用DBで数える

- **実行先を選ぶ。** サービスのSELECTが来ない専用replicaや分析用DB、独立コピーを優先する。なければengine別手順の事前準備を満たす、サービスのSELECTも処理するread replicaを使う。同じインスタンスの別schemaでは負荷を分けられない。
- **コピー対象を確認する。** DB担当者に「対象表・行の除外がない」「コピー先だけの追加・削除がない」を確認する。SQLだけで過去のコピー漏れは確認できないため、コピーの作成記録と変更記録を使う。確認できなければ、確認できるコピーを用意する。
- **データの時点を確認する。** 復元DBは復元時点を確認する。replicaは既存の複製監視で、更新の適用が動いていること・エラーがないこと・遅延が今回許容する範囲内であることを確認する。
- **負荷と取消方法を確認する。** CPU・I/O・複製遅延・サービス応答の監視画面を開き、停止する上限を決める。別管理接続から実行中queryを確認・取消できるようにする。[取消・終了確認](#6-開始停止終了回復チェック)
- **接続先を確認する。** 選んだDBへの新規専用接続で[§3の初期化SQL](#3-対象dbの全テーブルの概算件数を取る)を実行する。返されたserver・接続IDを記録し、想定した接続先か確認する。業務接続は流用しない。
- **1表ずつ数える。** `db`・`t`を確認済みのDB名・表名へ置き換え、同じ専用接続で次を実行する。既存設定・表定義は変更しない。

```sql
SELECT /* count_job: replace_with_unique_job_id */ COUNT(*) AS exact_rows
FROM `db`.`t`;
```

- **実行中は監視する。** 上限超過・複製エラー・接続先の役割変更・監視不能が発生したら、対象queryを取消す。server上の終了とサービスの回復を確認するまで次の表へ進まない。
- **結果を記録する。** 表名・件数・COUNTの開始終了時刻・復元／複製したデータの時点を記録する。複数表を同時点で数える必要があれば、同じ時点に復元したDBで実行する。

[確認する理由・用語の補足](count-check-rationale.md)。MyISAMにはこのCOUNT手順を使わず、[MyISAM用フロー](mysql-myisam.md#実行手順)へ進む。

## 5. 校正・標本を選べる条件

統計の校正は同じ表世代・統計profile・統計時点・真値時点・版・sample pages・変更条件に対応付けます。行幅、PK、partition、暗号化、cache等の費用条件も記録します。
論理復元や再構築で配置・統計が変わるため、復元先の誤差から古い本番cache統計の誤差を認定しません。未知・不一致・大量変更後は校正を棄却します。
標本はSQLで見える一意整数PK、固定snapshot、セル上限、無作為抽出、停止規則、誤差確率配分等の**必要な全条件を証明した集計用DB**だけのprototypeです。
文字列PK・PKなし・極端な欠番や偏在で条件が成立しない場合はTABLE_ROWSなどの概算件数か通常COUNTへ戻ります。標本0から空／小表と推定せず、反復の安定を精度認定にしません。
信頼区間、今回の実現誤差、将来保証を分けます。詳細は[校正手順](../table-count-calibration.md)と[標本仕様](../probability-count.md)を参照し、初回台帳取得の既定にしません。

## 6. 開始・停止・終了・回復チェック

- **開始**：対象・世代・主体・権限・取得先とデータ同等性・HA role・鮮度を確認。実行担当と別管理接続の監視／取消担当を置き、所有処理の識別・取消・終了確認ができなければ開始しません。
- **予算**：平常p99・失敗率・CPU・I/O・待機・複製遅延・DDL待ち・長いread view/undoを確認。所有者が資源上限、観測間隔、停止値、回復窓を記録します。
- **期限**：検出・取消遅延を見込みSLO違反より手前で止められる余裕を確保。文上限・MDL待ち上限・driver command timeout・呼出し全体のdeadlineを併用します。
- **投入**：共有資源単位でもまず直列。行数Nや列数だけで並列数・安全閾値を決めず、余力を確認してのみ拡大します。
- **停止**：昇格・role/topology/routing/epoch変更、roleの期限切れ・取得不可・不明で実行条件を満たすか再確認が必要です。停止値到達、監視不能、想定外DDL/負荷でも新規投入停止。取消担当は接続ID・一意jobタグ・取得先・account・開始文脈で現在の所有queryを照合してKILL QUERY等の承認済み取消を行います。
- **終了**：古いIDの再利用先を盲目的にKILLせず、server上の文・transaction終了を確認。driver timeout・切断だけで完了とせず、終了不明なら次の表へ進みません。
- **回復**：取消後もcache/I/O・undo等を回復窓まで監視し予算内へ戻ることを確認。自動再試行せず、失敗はUNKNOWNと理由を保存します。
- **判定**：処理完了、要求精度、サービス予算、後始末を別々に記録。未開始・未完了・timeoutを落としてSLO合格にしません。[取消仕様](https://dev.mysql.com/doc/refman/8.4/en/kill.html)

実行前に[運用runbookの記入済み独立復元先／専用replica例と空欄template](operations-runbook.md#1-採用前の記入欄)を埋めます。共有host/storage/networkと実際の上限機構を確認し、CPU/memory制限やResource GroupsだけでI/O上限を認定しません。検出・配送・判断・取消・rollback・jitterの時間上界より長い停止余裕を確認し、p99のms差から余裕秒を推定しません。
[具体的観測SQL・権限](operations-runbook.md#3-mysql接続所有mdlundo複製)ではP_S threadsとconnection所有台帳、MDL、INNODB_TRX/undo、replica適用状態を追跡します。instrumentation不足・部分可視・権限拒否はunavailableで停止し、global instrumentsを自動有効化しません。KILL QUERYだけではtransaction/read view解放を保証しないためclient finallyが自分のtransactionを回収し、所有接続とserver終了を確認します。識別不一致や終了不明では再投入しません。
HA変更で失効した結果は承認までHOLD。自動再接続・再試行は禁止し、driver/proxy retryで新しい物理sessionになった場合もidentity・role・データ同等性・資源・鮮度をすべて再確認します。実backendと専用leaseを固定・観測できなければHOLDです。
昇格候補はHA所有者の手順・イベントと停止gateを連携し、必要な場合は昇格後にサービス負荷を受け入れる前の取消・保持解除ackまで確認します。連携できなければ独立復元先へ戻します。ポーリングは検出遅延を狭めるだけで、競合なくprimary負荷を防ぐ保証ではありません。HAを優先し、COUNT終了を必須failoverの妨げにしません。終了不明はincidentへ引継ぎ、新規COUNTを禁止します。これは[roleの現状態仕様](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-replication-group-members-table.html)と[取消の制約](https://dev.mysql.com/doc/refman/8.4/en/kill.html)を踏まえた運用方針です。[共通の失効・HA引継ぎ手順](operations-runbook.md#1-採用前の記入欄)を満たせない／連携未検証ならHOLDとし、自動failover実験は要求しません。

## 7. 表・シャードごとの記録と観測限界

各行に、job ID、DB/表/シャード・世代、対象範囲・主体、取得先identity/版/role、HA所有者・topology/routing epoch・採取時刻／age・イベント源・承認、同等性監査のscope/世代/時刻、方式、値またはUNKNOWNと理由を保存します。
精度種別（参考／経験的校正／条件付き区間／正確snapshot）、開始終了時刻、snapshotの説明、backup/復元点、遅延・許容鮮度、校正条件を添えます。
snapshotとログ位置を対応付けられない場合はその限界も残し、取得位置をCOUNTの厳密なsnapshot IDとして偽装しません。各予算、到達停止値、取消、server終了、回復、後始末を記録します。
期待shard一覧と取得済み一覧を突合し、欠落・UNKNOWN・異なる範囲／精度／時点を明示。互換な取得済み値だけの小計とINCOMPLETEな全体を分けます。

[統計値の実測JSON](../../results/reference/review-20261107T155750Z-26743/results/requested_metadata_accuracy.json)と[誤差の集計](../../results/reference/owner-acceptance-20261107T161659Z/reports/owner-essential-fresh-metadata-extrema.json)では、非空InnoDB55表の各1観測、既定cache・20 pagesで、ANALYZE前の500,000行が621,877行（+24.3754%）、後の1,000,000行が1,195,881行（+19.5881%）でした。
これは一律10%を超える例で、全条件の誤差上限ではありません。小表一致、ANALYZEやpage増量も精度保証にしません。費用比較は[校正資料の条件付き比較](../table-count-calibration.md#4-代表fixtureと条件付き標本の位置付け)へ進みます。
[検証の実行記録](../../results/reviews/final-all-proof.json)の検証実行は8.4/9.7/26.10系の専用コンテナ観測です。環境・cache・実行順の制約があり、本番SLOや最速順位は未認定です。
[費用比較の元JSON](../../results/reference/review-20261107T155750Z-26743/results/requested_accuracy_cost_frontier.json)は8.4.13・buffer pool 256 MiB・parallel read threads=1の専用labです。host/VM cacheと先行COUNTの持越しは未制御で、本番hardwareの性能予測に使いません。
1億行の細表と75万行の50/100列TDE表は別条件です。**1億行×80/100列×TDE、実アプリ暗号化、物理replica分離、本番のcold/warm性能は未計測**で、組み合わせて外挿しません。
labのreplicaは`t_repl`だけを複製します。その良好なlagから他表の複製範囲・同等性を認定しません。filter／driftやHA連携の新実験は行っていません。

## QA

### Q InnoDBが小さい二次索引でCOUNTするのは、どのバージョンからか

A **5.7系では5.7.18で、小さい二次索引を使う動作に戻されました。** 新規導入ではなく、5.7.2で入ったクラスタ化索引走査への変更を取り消したものです。これは変更履歴の説明で、5.7を新規採用する推奨ではありません。[5.7.18リリースノート](https://dev.mysql.com/doc/relnotes/mysql/5.7/en/news-5-7-18.html)

8.4の公式説明では、通常は最小の利用可能な二次索引、なければクラスタ化索引を走査します。hint・版・実行条件による違いがあるため、必ず同じ索引を使う保証ではありません。[COUNT仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)

### Q クラスタ化索引はPKではないのか。二次索引と何が違うか

A **InnoDBではPKがあれば、それがクラスタ化索引です。ただしリーフページにはPKだけでなく行データ本体も入ります。** 二次索引は索引対象の列とPKを保持します。

```text
クラスタ化索引（PK=id）：id、status、名前、住所、本文…
二次索引（INDEX(status)）：status、id
```

PKがなければ適切なUNIQUE NOT NULL索引、どちらもなければ内部生成の隠し索引をクラスタ化索引に使います。[索引構造](https://dev.mysql.com/doc/refman/8.4/en/innodb-index-types.html)

### Q B-treeの各ノードやページにある件数を足すだけではないのか

A **そのtransactionから見える正確な件数を、各ノードの件数の合計だけで返す仕組みではありません。** 中間ノードは子ページへの探索に使い、COUNTはリーフの索引レコードをたどって見える行を数えます。ページ内でもレコードを確認しますが、名前や本文など全列の値を読み解いて返す必要はありません。[COUNT仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)

### Q statusが全行同じなら、二次索引では何を数えるか

A **statusの種類数ではなく、行ごとの索引エントリを数えます。** 例えば次の5件が1ページにあれば、すべて見える場合のCOUNT(*)は5です。

```text
status  id（PK）
1       101
1       102
1       103
1       104
1       105
```

同じstatusでもPKが違うので、索引エントリが1件にまとめられるわけではありません。種類数を数えるCOUNT(DISTINCT status)ならこの例は1ですが、全行数とは別です。[二次索引の構造](https://dev.mysql.com/doc/refman/8.4/en/innodb-index-types.html)

### Q 索引の全件走査と、テーブル全体を読むことは何が違うか。数億件でも速いのか

A **全件分を確認する点は同じですが、1件あたりのデータ量が違います。** 小さい二次索引なら1ページに多くのエントリが入り、行データ本体を含むクラスタ化索引より読取りページ数を減らせます。「全列を結果として取得しない」ことは「行データのページを一切読まない」ことではありません。

- 索引がキャッシュにあればディスク読取りは減るが、数億件を確認するCPU処理は残る。
- キャッシュにないページはストレージから読む必要がある。
- 更新状況によっては、二次索引だけでは見える行か確認できず、クラスタ化索引や過去の版を参照する。

したがって、二次索引でも一瞬で数えられるわけではありません。行数だけから所要時間は決められず、COUNTの所要時間とサービスの遅延も別に評価します。数億行の本番相当環境でのサービス悪化率は、このrepoでは未計測です。[COUNT](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)・[行の版と二次索引](https://dev.mysql.com/doc/refman/8.4/en/innodb-multi-versioning.html)

### Q MyISAMは件数を保持しているなら、先にTABLE_ROWSを取得しなくてよいか

A **MyISAMと確認済みで正確な件数を取得するなら、TABLE_ROWSは不要です。** 単一表・WHEREなし・他の列を取得しない`SELECT COUNT(*) FROM db.t`は、エンジンが保持する正確な件数を利用できます。InnoDBのような全件の索引走査は必要ありません。[COUNT仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)

TABLE_ROWSは全表のENGINEと件数を一覧にする用途では便利ですが、キャッシュされた古い値の場合があります。COUNT前の必須手順ではありません。COUNTは実行先・監視・取消方法を確認してから実行します。保持件数を使ってもテーブルロックや待ちは残り、コピー先で数えた値はコピー時点の件数です。

### Q 暗号化の有無で、件数取得時に気を付けることはあるか

A **COUNTのSQLは変えません。確認するのは鍵の利用可否と実行負荷です。**

- **InnoDBの保存時暗号化**：DBサーバーが鍵を利用できれば、通常の`COUNT(*)`を使います。実行者が復号鍵をSQLに渡す必要はありません。ディスク読取り時の復号処理も含め、CPU・I/Oを監視します。[MySQL公式](https://dev.mysql.com/doc/refman/8.4/en/innodb-data-encryption.html)
- **RDSのストレージ暗号化**：SQLは同じです。コピー・復元先で必要なKMS鍵を利用できるか確認します。これはInnoDBの表領域暗号化とは別の仕組みです。[AWS公式](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Overview.Encryption.html)
- **アプリが列に暗号文を保存している場合**：WHEREなしの`COUNT(*)`に平文の復号処理は不要です。DBがその表を読めることを確認し、復号関数は追加しません。
- **MyISAM**：InnoDBの表領域暗号化の説明は適用しません。ストレージ暗号化・アプリの暗号化など、実際に使用している方式を確認します。

件数取得のために暗号化を解除したり、鍵・既存設定を変更したりしません。暗号化の有無だけでCOUNTのロック方式は変わりません。実環境での暗号化による性能差は、このrepoでは未計測です。

### Q InnoDBのreplicaでSELECTすると、書込みの反映が遅れるか。行ロックが原因か

A **反映が遅れることはありますが、通常のCOUNTの行ロックが原因ではありません。** READ COMMITTED／REPEATABLE READの通常の`SELECT COUNT(*)`は行ロックを取りません。重いCOUNTがCPU・I/O・メモリを複製処理と取り合うと、replicaへの更新適用が遅れる場合があります。サービスreadを同居させていると、応答の遅延に加え、古いデータを返す時間も長くなります。[AWSの遅延対処](https://repost.aws/knowledge-center/rds-mysql-high-replica-lag)

- サービスのSELECTが来ない専用replicaを優先する。なければ、engine別手順の事前準備に従い、サービスのSELECTも処理するread replicaで監視付きで実行する。
- 開始前と実行中にCPU・I/O・サービス応答と複製の稼働状態を確認する。RDSではCloudWatchの`ReplicaLag`も監視する。
- 許容上限超過・複製エラー・監視不能ならCOUNTを取消し、server上の終了と回復を確認する。次の表を続けて実行しない。

専用replicaでも複製遅延自体は起こり得ます。`ReplicaLag`が取得不能・異常値なら遅延0と扱いません。また、通常COUNTにもMDLは残り、DDLの複製適用と競合する場合があります。[ReplicaLagの確認](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_MySQL.Replication.ReadReplicas.Monitor.html)・[MDL](https://dev.mysql.com/doc/refman/8.4/en/metadata-locking.html)

### Q MyISAMの専用replicaでも、CPU・I/Oと複製遅延の監視は必要か

A **必要です。** 既存の自前MySQL replicaで実行する場合、サービスのSELECTが来ない接続先を優先します。なければ事前準備の条件を満たすread replicaで実行し、サービス応答・CPU・I/O・複製遅延・複製エラーを監視します。MyISAMでは資源競合に加え、テーブルロックが複製更新を待たせる可能性があります。無条件COUNTが高速でも監視を省きません。

許容上限超過・監視不能ならCOUNTを取消し、server上の終了と複製の回復を確認してから取得方法を見直します。RDSの`ReplicaLag`はRDS用の指標名なので、自前MySQLでは既存の複製監視を使います。**RDSのMyISAM replicaを推奨する手順ではありません。** 更新の入らない独立コピーでは複製遅延の監視は不要ですが、CPU・I/Oと共有資源への影響は確認します。[MyISAMのロック](https://dev.mysql.com/doc/refman/8.4/en/internal-locking.html)・[RDSのサポート範囲](https://aws.amazon.com/rds/mysql/faqs/)

### Q InnoDBのCOUNTはロックしないか。FOR UPDATE・FOR SHARE・SERIALIZABLEならどうか

A **READ COMMITTED／REPEATABLE READの通常の`SELECT COUNT(*)`は行ロックを取りません。** MyISAMのような読取り用テーブルロックも使いません。ただし、表定義を保護するメタデータロック（MDL）は取り、`ALTER TABLE`などと競合する場合があります。CPU・I/Oの負荷も残ります。[整合性読取り](https://dev.mysql.com/doc/refman/8.4/en/innodb-consistent-read.html)・[MDL](https://dev.mysql.com/doc/refman/8.4/en/metadata-locking.html)

- **`FOR SHARE`付き**：読み取る索引レコードに共有ロックを取るlocking readです。
- **`FOR UPDATE`付き**：読み取る索引レコードに排他ロックを取るlocking readです。
- **`SERIALIZABLE`**：SQL末尾の句ではなく接続・transactionの分離レベルです。autocommit無効では通常SELECTも`FOR SHARE`相当になります。一方、autocommit有効でSELECT単独のtransactionなら、非ロックの整合性読取りが可能です。「SERIALIZABLEなら常に行ロック」とは限りません。

locking readのロック範囲は索引・検索条件・分離レベルで変わり、範囲のロックを含む場合があります。明示transactionではCOMMIT／ROLLBACKまで保持されます。このガイドでは**新規専用接続をREAD COMMITTED・autocommit=1にし、FOR UPDATE／FOR SHAREを付けない通常COUNTだけを使います**。SERIALIZABLEは採用しません。[locking read](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)・[分離レベル](https://dev.mysql.com/doc/refman/8.4/en/innodb-transaction-isolation-levels.html)

### Q COUNT(1)とCOUNT(*)は同じか。アスタリスクで遅くならないか

A **同じ対象・同じ時点なら件数は同じです。全件数には`COUNT(*)`を使います。** `COUNT(*)`の`*`は行を数える指定で、`SELECT *`のように全列の値を取得する指定ではありません。

- **InnoDB**：`COUNT(*)`と`COUNT(1)`は同じ方法で処理され、性能差はありません。`1`へ変えても高速化しません。
- **MyISAM**：単一表・WHEREなし・他の列を取得しない`COUNT(*)`は、保持件数を使う最適化の対象です。公式仕様では`COUNT(1)`が同じ最適化を受けるのは先頭列が`NOT NULL`の場合に限られます。`COUNT(1)`へ置き換える利点はありません。

`COUNT(列名)`はその列がNULLの行を数えないため、全件数の代用にはしません。[MySQL公式仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)

### Q なぜTABLE_ROWSはInnoDBでは概算なのか。MyISAMでは正確なのか

A **エンジンが件数を管理する方法が違うためです。**

- **InnoDB**：同じ表でもトランザクションごとに見える行が違うため、全接続共通の正確な件数を保持しません。`TABLE_ROWS`は統計からの推定値です。正確な件数が必要なら、対象トランザクションから見える行を`COUNT(*)`で数えます。[MySQLのCOUNT仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)
- **MyISAM**：エンジンが正確な行数を保持しています。単一表・WHEREなしの`COUNT(*)`はその値を利用でき、全行を走査せず取得できます。[MySQLのCOUNT仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)

ただし、**MyISAMでも`information_schema.TABLES.TABLE_ROWS`が現在の正確値とは限りません**。`information_schema_stats_expiry`によるキャッシュがあり、更新前の値を返す場合があります。エンジンが保持する件数の正確さと、返された値の古さは別です。このガイドではキャッシュ設定を変更せず、時点が確認できない値は現在の正確値として使いません。高速に取得できることも、ロックやサービスへの影響がない保証にはなりません。[TABLESと統計キャッシュの仕様](https://dev.mysql.com/doc/refman/8.4/en/information-schema-tables-table.html)


### Q バックアップから別DBを作るとは。バックアップ取得でサービス性能は落ちないか

A **既存のバックアップを読み込んで、新しいDBインスタンスを作り、そのDBへ接続してCOUNTする**という意味です。元のサービスDBを上書きしません。保存済みバックアップを使う作業と、新しく本番DBのバックアップを取る作業は別です。まず必要な時点を満たす既存バックアップがあるか確認します。[RDSのsnapshot復元手順](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_RestoreFromSnapshot.html)

**新規バックアップ取得は性能へ影響し得ます。** RDSのSingle-AZ DB instanceではsnapshot取得に伴う一時的なI/O停止があり得ます。MySQLのMulti-AZ DB instanceではstandbyから取得するためprimaryのそのI/O停止は避けられますが、性能への影響全般がゼロという保証ではありません。AuroraやMulti-AZ DB clusterは別の構成として仕様を確認します。MyISAMはRDSのsnapshot復元がInnoDBと同様にサポートされるとは扱いません。[AWSのsnapshot取得時の影響](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_CreateSnapshot.html)

COUNTのために本番snapshotやreplica作成を自動実行しません。新規取得が必要なら、DB担当者が方式・取得元・時間帯・許容するサービス応答悪化・監視・中止対応を決めます。既存バックアップで足りるなら、それを使うことで新たに本番をbackupする作業を避けられます。復元先も共有storage/networkとの競合、鍵、データの対象範囲と時点を確認します。復元直後は読み込みが遅いことがあるため、復元先でのCOUNTの時間・I/O上限も別途設けます。


### Q InnoDBとは何か。普通のMySQLなら気にしなくてよいか

A InnoDBは、MySQLでテーブルの保存・索引・transaction・ロックを扱う仕組み（storage engine）です。MySQL 8.4では標準設定なので、engineを指定せず作成した一般的なテーブルはInnoDBになります。ただし設定変更や既存テーブル、他engineもあるため、「みんなInnoDB」とは断定しません。普及率はこの調査では確認していません。[MySQL公式の標準設定](https://dev.mysql.com/doc/refman/8.4/en/storage-engine-setting.html)

このガイドで確認する理由は、通常のSELECT COUNT(*)が行ロックを取らないという説明の対象をInnoDBへ限定するためです。InnoDBでもI/O・CPU・MDL等の影響はあります。§3の対象DBのテーブル一覧SQLのENGINE欄がInnoDBか確認します。default_storage_engineだけでは既存の対象表のengineを証明できません。違う場合はengineを変更せず、§2の別経路へ進みます。SQL ServerにはこのInnoDB確認はありません。


### Q MyISAMならCOUNTが速いのでは。InnoDBと分ける必要はあるか

A 分ける必要があります。MyISAMは行数を保持し、条件を満たす無条件COUNTを速く返せる場合があります。一方、InnoDBは読取時点に見える行を数え、両者は同じ実装ではありません。MyISAMにはtable-level lockingがあるため、InnoDBの「通常SELECTは行ロックを取らない」という説明だけを根拠にサービスDBで実行しません。[COUNTの製品仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)・[MyISAMのロック](https://dev.mysql.com/doc/refman/8.4/en/internal-locking.html)

このrepoでは非InnoDBは静止した検証表の参考情報だけを対象とし、サービスへの負荷試験はしていません。MyISAM・その他・engine不明なら、DB担当者がengine・クラウドの制約に対応した取得方法を準備して数えます。COUNTのためにengineを変更する手順ではありません。

### Q MyISAMのCOUNTでテーブルロックを取らない方法はないか

A **MyISAMの`SELECT COUNT(*)`を、SQLの書き換えでテーブルロックなしにする方法はありません。** `LOCK TABLES`を書かなくても、MyISAMは読取りにテーブル単位のロックを使います。保持件数を使って速く返せることと、ロックを取らないことは別です。[MySQLのロック仕様](https://dev.mysql.com/doc/refman/8.4/en/internal-locking.html)

- **件数情報で足りる場合**：このガイドの`TABLE_ROWS`取得SQLを使います。ただし古いキャッシュ値の場合があり、metadata照会自体もロックゼロとは保証できません。
- **サービス側の表にCOUNTのロックを掛けたくない場合**：更新が入らない、サービスと資源を分けた別DBのコピーで`SELECT COUNT(*)`を実行します。コピー側のロックは元DBの表をロックしません。コピー作成時の負荷・ロックは別途確認し、RDSのMyISAM対応範囲は下のQ&Aに従います。
- **専用replicaで実行する場合**：サービスreadを分けても、同じ表への複製更新と競合する可能性は残ります。replicaに変えるだけで解決とはしません。

`concurrent_insert`は条件付きで読取りと追記を並行できる機能で、ロックをなくす設定ではありません。UPDATE・DELETEとの競合まで解消しないため、この件数取得手順では変更しません。[同時INSERTの仕様](https://dev.mysql.com/doc/refman/8.4/en/concurrent-inserts.html)

### Q MyISAMでもread replicaでCOUNTするべきか

A **一律にread replicaを選びません。**

- **RDS for MySQL**：read replicaのサポート対象はInnoDBです。MyISAMのCOUNT先として推奨しません。[AWS公式FAQ](https://aws.amazon.com/rds/mysql/faqs/)
- **自前のMySQL**：既存の専用replicaは候補です。COUNTのロックはprimaryの表には掛かりませんが、replica内の複製更新を待たせる可能性があります。サービスのSELECTが来ないreplicaを優先します。なければ事前準備の条件を満たすread replicaで、サービス応答・複製遅延・負荷を監視します。
- **サービスへの影響を避けることを優先する場合**：更新が入らず、サービスと資源を分けた独立コピーで数えます。コピー作成時の負荷・ロックは別途確認します。RDS snapshot復元をMyISAMでも無条件に使えるとは扱いません。

MyISAMの単一表・WHEREなしのCOUNTは保持件数を利用でき、通常は高速です。InnoDBの大きな表と同じ全件走査を前提にする必要はありません。ただし、高速であることはロックや競合がない保証ではありません。[COUNTの仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)

### Q 専用replicaはInnoDBにもMyISAMにも使えるか

A 一般のMySQLで複製できることと、RDSのサポート範囲、COUNTによる影響は別です。**RDS for MySQLのread replicaは、AWS公式FAQではInnoDBのみがサポート対象です。** MyISAMについても作成時の注意ページはありますが、同等のサポートとは扱いません。MyISAMはtable-level lockingがあり、既存replicaであってもSELECTと複製適用の競合を別確認します。[AWSのengine別サポート](https://aws.amazon.com/rds/mysql/faqs/)・[MyISAM使用時の注意](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_MySQL.Replication.ReadReplicas.html)

またAWS FAQではPITR・snapshot restoreもInnoDBのみをサポートするとしています。MyISAMを「RDS snapshotで別DBへ戻せばよい」と自動的に扱いません。まず既存の整合したコピーや件数記録を探し、なければDB担当者がengineに対応した取得・整合性確認と、必要なメンテナンス時間を計画します。AWSのMyISAM replica作成手順には元DBでの更新停止とロックが含まれるため、このガイドから自動実行しません。COUNTのためのengine変更も行いません。

### Q COUNTは必ずread replicaで実行するのか

A 必ずreplicaという意味ではありません。このガイドでは、巨大表の正確COUNTは**実行条件を確認した集計用DB**で行います。専用の分析replica、独立した復元コピーが候補です。業務primaryしかない場合は既存exportか予算付きTABLE_ROWSなどの概算件数までとし、正確値は設備・鮮度・精度の見直しへ戻します。

replicaでもアプリの読取を受け持つ、storage/networkを共有する、複製適用が詰まる場合はサービスへ影響します。HA昇格候補はrole変更と取消を連携し、連携不能なら独立復元先へ。server UUIDだけでなく実role、同等性、遅延、上限、取消経路を[runbook](operations-runbook.md)で確認します。read_onlyフラグは無負荷の保証ではありません。

### Q RDS read replicaに重いSELECTを流すと、writeやサービスのread性能へ影響するか

A **同じreplicaを使うサービスのreadは遅くなり得ます。primaryのwriteへの直接影響は通常限定されますが、間接影響はあり得ます。** ここではAuroraではない、通常のRDS for MySQL DB instanceの非同期read replicaと、通常InnoDBのRC/RRにおける非locking SELECTを対象にします。

| 影響先 | 起こり得ること |
|---|---|
| 同じreplicaへのサービスread | 重いSELECTとCPU・I/O・buffer pool等を共有するため、応答時間やtimeoutが悪化し得る |
| replicaのデータ鮮度 | SELECTと複製適用が資源を競合し、適用が遅れるとReplicaLagが増える。読取が速く返っても古いデータになり得る |
| primaryのwrite | 非同期複製なので、そのSELECTの終了やreplicaの適用完了を通常のcommitが待つ仕組みではない。replica側SELECTの行ロックがprimaryへ伝播するわけでもない |
| primaryへの間接影響 | 大きな複製遅延に伴うsource側binlog蓄積・空き容量低下、アプリが遅いreplicaからprimaryへreadを迂回する場合の追加負荷などに注意する |

非同期複製とDB instance分離は[AWS RDSの構成仕様](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ReadRepl.html)によります。資源競合と迂回による影響はその構成からの運用上の推論で、悪化率はこのrepoでは未計測です。遅延が大きい場合のbinlog蓄積は[AWSの遅延対処資料](https://repost.aws/knowledge-center/rds-mysql-high-replica-lag)にも記載されています。適用だけが遅いのか、ログ受信も遅いのかを分け、ReplicaLagだけでsourceの容量増加を断定しません。

通常SELECTでもreplica内のMDLや長いsnapshotの保持は残り、DDL適用や後処理へ影響し得ます。行ロックを取らないことは、無負荷を意味しません。Aurora reader、RDS Multi-AZ DB clusterのreader、RDS for SQL Serverは複製・storage・commitの条件が異なるため、このprimary影響の説明を転用しません。Multi-AZ DB instanceの読取不可standbyとも区別します。

### Q サービスのread性能への影響を最小限にするには

A **サービスのreadを受けない、実行条件を確認した分析専用replicaか独立復元先で実行することを優先します。** AWSもレポート・DWH用途のread replicaを案内しています。[公式の用途例](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ReadRepl.html#USER_ReadRepl.Overview)

1. **接続先を分ける。** サービス用と集計用の接続先・pool・routingを分離し、集計専用replicaへサービスreadが流れないことを確認します。HA昇格候補ならrole変更時の取消も必要です。独立復元先は継続複製の競合を避けられますが、元表との同等性・復元点・鍵・鮮度を別確認します。
2. **走査を減らす。** 要件を満たす既存export・時点付き件数cache・TABLE_ROWSなどの概算件数を優先。正確COUNTが必要な場合だけ実行し、共有資源単位でまず1本、低負荷時間帯から始めます。索引の一律新設やhint強制は行いません。
2. **開始前に予算と取消を決める。** 通常時のサービスp95/p99応答時間・error率、replicaのCPU・read latency/queue・空きメモリ、ReplicaLagと複製稼働状態、primaryのwrite応答時間・BinLogDiskUsage・FreeStorageSpaceを確認します。許容悪化幅・鮮度上限・実行期限・観測間隔を所有者が定め、監視不能なら開始しません。汎用の「CPUが何%なら安全」という基準は置きません。
3. **悪化時は集計を止める。** サービス応答・error・鮮度・資源予算のどれかが上限を超えたら、[runbook](operations-runbook.md)で所有確認した集計queryを取消し、server側の終了・transaction終了と回復を確認します。timeout直後の再試行や並列追加はしません。通常の複製適用をCOUNTのために止めません。
4. **迂回と終了後も見る。** サービス側のprimaryへのfallback・retry増幅を確認し、集計からprimaryへ自動再試行しません。query終了だけで回復とはせず、サービスreadと遅延・資源が許容範囲へ戻るまで次を開始しません。

専用環境がない場合は、engine別手順の事前準備を満たす、サービスのSELECTも処理するread replicaを次の候補にします。監視付きでまず1本実行し、上限超過で取消します。writerへ自動的に切り替えません。

### Q 重いクエリは1本だけ。それでも大きな影響が出るのか

A **必ず大きな影響が出るわけではありません。1本という本数だけでは判断できません。** 余力が十分で、scan対象がcacheに収まり、サービスとの競合が小さければ、許容範囲に収まることもあります。一方、1本でも大きなindex scanがI/O帯域を使う、buffer poolの入替えが増える、長いsnapshotを保持する、といった条件でサービスreadや複製適用の余力を削り得ます。CPU全体に余裕があってもI/Oが詰まる場合があります。

無条件COUNTは結果が1行でも多数のページを読む場合があり、返却行数と作業量は別です。「遅いSQLが1本ある」こと自体より、実行前後のサービス応答・error、I/O待ち、ReplicaLagの変化を予算に照らして判断します。一般の重いSELECTにはjoin/sort等もありますが、単純COUNTにそれらの費用が必ず発生するという意味ではありません。影響の大きさは未計測で、一律に危険とも安全とも認定しません。

### Q slow query logに出ることも注意すべきか

A **出力条件に該当すれば記録されるため、既存の監視運用に合わせて実行を識別できるようにします。** RDS for MySQLではslow_query_log、long_query_time、log_output等の実効設定を対象replicaで確認します。CloudWatch Logsへの出力は別途export設定も必要です。短時間でも索引を使わないqueryを記録する設定等があるので、時間だけで出力有無を断定しません。[AWSのログ仕様](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_LogAccess.MySQL.LogFileSize.html)

- 1回の長いCOUNTが、scanした行の数だけslow logへ出るわけではありません。通常は実行したstatementの記録です。1本の短いSQLを1回記録する費用だけで大きな劣化が起こると決めつけず、query本体のscan費用とログ書込費用を分けます。ログ費用はこのrepoでは未計測です。
- 定期実行・timeout後の再試行・多数表の連続実行で記録数が増える場合や、TABLE出力・大きなSQL本文・低すぎる閾値では、ログI/O・容量・CloudWatch取込／保持費用も確認します。AWSは性能面でFILE出力を推奨していますが、COUNT一回のため既存設定を自動変更しません。[AWSの性能上の推奨](https://aws.amazon.com/blogs/database/performance-optimization-strategies-for-mysql-on-amazon-rds/)
- 専用ユーザー・接続先・実行時刻・機密を含まないjob IDを台帳に残します。SQLコメントの保持やアラート側の識別可否は実際の収集経路で確認し、コメントだけへ依存しません。ログにSQL・schema名等が残ることを考慮し、コメントへ個人情報や秘密を入れません。
- slow log連携の通知がある場合は予定集計と識別します。予定queryであることだけを理由にサービス悪化の通知を抑止せず、遅延・error・複製の監視は維持します。ログに出ないようslow logを無効化したり閾値を上げたりして、安全性の代用にはしません。

slow logに載ったことは実行時間等が出力条件を満たした証拠であり、サービス性能が悪化した証拠とは別です。また、通常はstatementの実行後に記録されるため、実行中queryの監視・取消をslow logの到着だけに依存しません。[MySQL slow query log仕様](https://dev.mysql.com/doc/refman/8.4/en/slow-query-log.html)

### Q サービスDBとは別に分析用replicaを置く構成は一般的か

A **ベンダーが公式に案内する標準的な構成パターンです**。AWSはRDS read replicaを読取負荷分散の仕組みとして提供しています。普及率を示す調査ではなく、構成例としての根拠です。[AWS公式](https://aws.amazon.com/rds/features/read-replicas/)

既存の分析replicaがあるなら、まず対象表の複製範囲と鮮度を確認します。新設はストレージ・運用費用、初期コピーと複製による元DBへの負荷を伴うため、COUNT一回のために自動作成しません。頻繁なレポートなら継続的な分離を検討し、まれな件数確認なら既存backupの独立復元が候補です。これは用途に応じた選定方針で、性能の一律優劣ではありません。

### Q 分析基盤や集計テーブルがあればそこで数えてよいか

A 元表との対応が証明できれば使えます。ETLで重複排除、削除行除外、履歴追加、tenant限定、joinによる増幅があると同じ件数ではありません。元表世代、変換規則、削除の反映、最終取込点、欠落・再送の扱いを確認します。集計表ならその行数をCOUNTするのではなく、元表との対応・取得時点・精度を確認できる件数指標を読みます。対応不明なら分析側の件数と表示し、元表の正確件数要求はHOLDです。

### Q 実行するCOUNTのSQLは

A [MySQLガイド§3の専用接続初期化](#3-対象dbの全テーブルの概算件数を取る)と実行先の条件確認を済ませた同sessionで、次の1文です。識別子は承認済み台帳から安全に引用します。

```sql
SELECT /* count_job: unique_job_id */ COUNT(*) AS exact_rows
FROM `db`.`t`;
```

通常InnoDBのRC/RR・autocommit=1で使い、FOR UPDATE/FOR SHARE、SERIALIZABLE、DML/CTASへの埋込みへ変更しません。COUNT(nullable列)はNULLを除くので代用不可。COUNT(1)への変更も高速化策にはしません。[MySQL COUNT仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)

### Q PKや二次索引によってSQLを変えるのか

A 基本は同じCOUNT(*)です。InnoDBは通常、利用可能な小さい二次索引を走査し、なければclustered indexを走査します。索引・行幅・cache・MVCCで費用が変わるので、実行条件を確認した集計用DBで通常EXPLAINを確認します。無条件COUNTは「索引があるので一瞬」とは限りません。[公式仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)

FORCE INDEX/PRIMARYを一律に付けたり、COUNTのため本番へ索引を新設したりしません。文字列PK・複合PK・PKなしでも通常COUNTの意味は同じです。索引選択の変更を試すなら別の隔離検証として費用・計画を比較します。

### Q 暗号化や100列、1億行ならSQLを変えるのか

A サーバーが通常SQLで読めるInnoDBならCOUNT(*)のままです。TDEはサーバーの鍵が利用可能であることが前提で、backup復元には鍵の準備も必要です。アプリ暗号文は行を数えるための平文復号式を追加しません。ただし接続・権限・driverの対応は確認します。[MySQL暗号化仕様](https://dev.mysql.com/doc/refman/8.4/en/innodb-data-encryption.html)

データ量で変えるのは接続先、時間・I/O予算、実行頻度と採用方式です。行数・列数だけから安全時間を算出しません。repoの1億行細表と75万行の幅広表は別条件で、1億行×100列の性能は未計測です。

### 精度と運用の質問

### Q 統計の件数はどれくらい信用できるか

A repoの固定観測を再集計すると次の通りです。各集合の分母を分けています。

| 方法と観測条件 | 観測した精度 | 判断への使い方 |
|---|---|---|
| MySQL非空InnoDB55表、20 pages、ANALYZE前 | 絶対誤差中央値0.2141%、±5%以内50/55、範囲−21.1538〜＋24.3754% | 多くは近いが全表5%以内ではない |
| 同55表、ANALYZE後 | 中央値0.2471%、±5%以内50/55、範囲−18.4832〜＋19.5881% | 更新すれば必ず改善するわけではない |
| MySQL行幅偏りの別fixture、20 pages | ＋4.5604〜＋38.7313% | 上の55表と混ぜず、偏りを別条件として扱う |

元データは[MySQL固定JSON](../../results/reference/review-20261107T155750Z-26743/results/requested_metadata_accuracy.json)。55表集合は各表各phase1観測、非InnoDB3表を除外。−27.785%という過去例は別の初回集合です。自分の表が約91%の確率で5%以内という意味ではありません。[集合別の詳細](../table-count-calibration.md)

5%程度の経験的見込みなら自分の条件で校正し、その観測範囲として使います。5%上限が必須なら統計だけでは採用せず、実行条件を確認した正確COUNT等へ。真値と比較していない場合は誤差率を付けず、TABLE_ROWSの概算件数として表示します。正確さが必要ならCOUNTで確認します。

### Q 自分の表の誤差をどう観測するか

A まず「今の本番cache値」と「復元先で再生成された統計」のどちらを評価するか決めます。以下は本番を止めず、既存の承認済み固定コピーで行う流れです。

1. 統計を更新する前の推定E、取得時刻、統計生成時刻または不明、設定、対象世代・行集合を保存。
2. 対応する固定コピーで正確Nを取得。元の本番Eを評価するなら、元Eの評価時点とコピーのデータ時点の対応が必要。
2. コピーへの書込・apply・DDL等がないことを確認し、統計状態を再読取。変わった場合は別観測として区別。
3. N>0なら `100*(E-N)/N` と行数差を保存。N=0は相対誤差を定義せず、未知・timeoutを0扱いしない。
4. 表・条件ごとに中央値、過小／過大極値、±1/5/10%内の件数/有効数、欠測、表数・反復数を記録。更新・再構築・世代変更で適用性を再確認。

統計が古いこと自体は評価対象です。統計生成時刻と評価時刻を別に保存し、評価時点の行集合をNと対応させます。MySQLでも、更新中に順番に取得するだけでは統計値とCOUNTの時点一致を保証しません。固定コピーと来歴を確認します。

### Q 真値なしで誤差率を計算できるか

A 今回確認した公式のmetadata項目だけでは得られません。sample pagesは誤差率ではありません。繰返し同じ値が返ること、SHOW/EXPLAIN等の一致も真値への近さを証明しません。[MySQL統計](https://dev.mysql.com/doc/refman/8.4/en/innodb-persistent-stats.html)

既存の正確な基点と、欠落・重複・rollback・DDLを扱った完全なcommit済み増減台帳があれば真値を再構成する選択肢はあります。単なるDML回数では不十分です。COUNTのために本番へ新しいtrigger/CDCを無断導入しません。

### Q 大量の表を並列に数えれば速いか

A 個々のCOUNTが予算内でも合計I/O・CPU・undo保持が予算を超えます。共有資源単位でまず直列。取得済みの値を時点付きで再利用し、必要頻度を減らします。並列化は所有者が合計予算に収まることを確認した場合だけです。別snapshotの表・shard合計を全体同時点の正確値としません。

### Q タイムアウトしたら次の表へ進んでよいか

A server上のquery・transaction終了と回復を確認するまで進みません。所有確認したKILL QUERY等とclient側の後始末を使います。古いIDへ盲目的にKILLせず、不明ならincident担当へ引き継ぎます。[取消と回復の実行手順](operations-runbook.md)

### Q COUNTを毎リクエスト発行するべきか

A UIの概数やレポートなら、要件を満たす既存export・時点付きcache・集計値を優先します。正確な現在件数が必須なら、初期化・増減・再試行・削除・世代変更まで整合する常時計数を別機能として設計します。AUTO_INCREMENT/MAX(id)から行数を作りません。これは頻度と要求を見直す設計方針で、今回のrepoで常時計数を検証したものではありません。

公式資料は2026-11-08確認。構成例は普及率や無劣化の実測ではなく、製品・版・tierごとの条件確認が必要です。
