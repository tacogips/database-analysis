# 全件COUNTの事前適格化・停止・回復runbook

本書は[MySQL](mysql.md)・[SQL Server](sql-server.md)の通常COUNTを、所有者が承認した隔離先で実施するための記録と観測手順です。**本番無劣化を認定した実測はありません**。監視は資源隔離を代替しません。共有資源・権限・検出時間・取消・回復のどれかが不明ならHOLDとし、参考値／許容時点の緩和か適格な独立復元先へ戻します。

現行版の受入可否と対象hashは[Issue 3受入記録](../../results/reviews/issue-3/acceptance.json)で確認します（所有者確認・別セッションGPT-6.1 Sol 2役の結果と対象hashを確認し、記録なし・不一致は受入保留）。[Issue 2修正前snapshot](../../results/reference/issue-2-pre-fix-de1c79c/manifest.json)の旧5文書と[Issue 1受入](../../results/reference/issue-2-pre-fix-de1c79c/results/reviews/issue-1/acceptance.json)は保存された旧hashだけの証跡です。今回の文書修正は既存実行を再認定しません。

## 1. 採用前の記入欄

以下をjob台帳へコピーし、空欄・「不明」を所有者の判断として残します。測定元export、scope、時刻、権限検査結果、source hash、完全log、実終了codeへのリンクを付けます。DB名／表名は承認台帳から引用し、値・session ID等はdriver parameterで渡します。観測SQLに業務行データの取得は不要です。

| 欄 | 所有者記入欄 |
|---|---|
| job／対象 | job ID、DB/table/shard/世代、主体、全行の意味、要求精度／鮮度、復元・適用位置とCOUNT時点との対応：___ |
| SQL Server context互換性（独立承認） | JOB_EXCLUSIVE／BUSINESS_USE／UNKNOWN：___。対象主体・表世代、RLS policyと全依存先、tenant/業務初期化と後続SET、監査・trigger、業務初期化完了・監査、接続全寿命での非競合、承認者・根拠・時刻・有効期限、読取/observer/取消の同一不変identity契約：___。欠落／UNKNOWN／BUSINESS_USEはタグ書込み・候補metadata/COUNT前にHOLD/export。NULL・非pool・管理者は証明でない |
| 実行先 | identity、engine/version/role、復元済みbackup承認・作成時の元DB予算、既存RCSI/SNAPSHOTまたはInnoDB条件：___ |
| HA適格性 | HA所有者の権威あるtopology export／承認記録、現在role・昇格候補、topology/routing epoch、採取時刻／age上限・有効期限、イベント源・停止配送先、必要承認：___ |
| 物理経路 | 意図したendpoint/listener→実server/backend/DB、専用lease固定と照合方法、driver/proxy再接続検出、物理sessionごとの再gate：___ |
| 昇格連携 | HA手順／イベント→新規COUNT停止→所有query取消／finally→TX/read view解放ack→必要なサービス受付gate、責任者・ack記録／期限、未検証ならHOLD：___ |
| MySQL同等性・対応 | source UUID/channel→元schema/table/世代→コピー側schema/table/世代、名前書換え・multi-sourceの全対応、適用範囲・履歴期間／ログ位置境界、監査時刻・所有者承認：___ |
| MySQL同等性・来歴 | 現在と全履歴のsource/binlog・replica channel/global filter、完全初期コピーの出所／範囲／位置、以後すべての再seed/rebuild/DDL、skip/空GTID/手動repair/local write履歴と既存承認済み整合性監査・変更記録：___ |
| 同等性判定 | 対象世代と適用期間・位置境界を覆う根拠／欠落、DATA EQUIVALENCE適格またはUNKNOWN、判定者・時刻：___ |
| 鮮度判定 | 許容snapshot点・許容age、復元／適用位置・採取時刻とCOUNT snapshot対応限界、lag／状態、FRESHNESS適格または不明：___ |
| 共有資源inventory | host/VM親、CPU/memory、storage controller/volume/物理device、cache、network uplink、backup、replication/redo、primary GC依存、他読取：___ |
| 制御と検証 | 資源ごとの上限機構、設定値、実際の階層・device対応・readback/counter・適格化試験の証拠、未制御資源：___ |
| 観測・権限 | app arrivals/error/latency、host/device/network、MDL/undo/applyまたはrequest/TX/version/stats/AG、採取元とscope、必要権限／既存collector担当、不可時停止経路：___ |
| 平常変動 | 代表業務mix・時間帯・到着数、baseline窓、p99/error/queue/容量/適用の通常範囲、cache条件：___ |
| 検出から停止 | sample間隔、集計窓、配送遅延、判断、Cancel、rollback、jitterの上界と確認元、総時間：___ |
| 停止余裕 | 指標ごとの停止値／危険域、最悪変化速度・余裕時間の測定元、総停止時間より長い根拠、証明できなければHOLD：___ |
| 回復 | 観測窓と連続正常期間、session/request/TX/read view/保持解除、容量・cache/I/O/統計background・replica/primary回収、窓内不明時のエスカレーション：___ |
| 比較の母集団 | planned/started/completed/failed/pending/cancelled、timeoutを含む応答、窓をまたぐ到着の追跡、未完了処理の扱い：___ |
| 責任者 | executor、別接続observer/cancel、infra制御、replication/AG、service承認者、連絡・終了不明時の引継先：___ |
| 別々の判定 | 実験完了 ___／要求精度 ___／サービス予算 ___／後始末 ___／本番採用PASSまたはHOLDと理由 ___ |

CPU/memoryのcgroup制限だけではI/O/networkは制限できません。VM・host・親cgroup・実際のbacking deviceまで対応を検証し、必要なら`io.max`、hypervisor/device QoS、vNIC shaping等の既存承認済み制御を使います。readbackだけでなく該当階層のcounterと適格化試験で実効性を確認し、経路が不明な「hard cap」を宣言しません。[kernel cgroup仕様](https://docs.kernel.org/admin-guide/cgroup-v2.html)
MySQL Resource GroupsのCPU割当／priorityはI/O上限ではありません。[公式仕様](https://dev.mysql.com/doc/refman/8.4/en/resource-groups.html)
SQL MAXDOPは並列実行の制御でありCPU quotaではありません。既存設定も所有者承認なく変更しません。[公式仕様](https://learn.microsoft.com/en-us/sql/database-engine/configure-windows/configure-the-max-degree-of-parallelism-server-configuration-option?view=sql-server-ver17)

停止時間はsample＋集計＋配送＋判断＋取消＋rollback＋jitter等の保守的上界で扱います。p99がSLOより何ms低いかだけから何秒余裕があるかは計算できません。独立した適格化・変化速度・容量の下限で時間余裕を証明できない共有先では投入しません。観測欠落／期限超過は新規投入停止、所有確認した取消、終了不明なら次表を開始せず担当へ引き継ぎます。

両製品でHA所有者の承認と現物観測を照合し、**昇格・role/topology/routing/epoch変更、role情報の期限切れ・取得不可・不明で適格性を失効**させます。新規投入を止め、現在の所有queryを§3／§4のidentity確認→取消→client finally／rollback→session/TX/read view解放→回復経路で処理します。既存結果も承認までHOLD。再開にはidentity・role・対象データ同等性・資源・鮮度を再適格化します。自動再接続／再実行や盲目的なretryは禁止し、driver/proxyが新しい物理sessionを作った場合も全gateをやり直します。実backendとleaseを固定・観測できなければHOLDです。

昇格候補を採用するには、既存HA手順・イベントと上の停止gateを連携し、必要な場合は昇格先がサービス負荷を受け入れる前の取消・保持解除ackまでHA所有者が確認します。同期できない／連携未検証なら独立復元先を選びます。ポーリングは検出遅延を狭めるだけでraceを防ぎません。**HAを優先し、COUNTを理由に必須failoverを妨げません**。緊急時に終了を確認できなければHA／incident責任者へ引継ぎ、新規COUNT禁止を維持します。primaryへの負荷を絶対防ぐ保証はありません。自動failover実験は要求しません。
これは[MySQLの変更可能なread_only](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html#sysvar_read_only)・[取消制約](https://dev.mysql.com/doc/refman/8.4/en/kill.html)、[SQL Serverのrole交代](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/failover-and-failover-modes-always-on-availability-groups?view=sql-server-ver17)・[routing条件](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/configure-read-only-routing-for-an-availability-group-sql-server?view=sql-server-ver17)を踏まえた本ガイドの採用方針であり、製品による原子的gateの保証ではありません。

## 2. 記入済みの架空例

この節の数値予算は[例入力JSON](../../sqlserver/issue1/runbook-example-inputs.json)由来の **illustrative_not_measured（未計測の架空値、推奨値ではない）** です。追加した来歴・HA欄を含め、説明上の適格化証拠・人物名・記録IDも架空です。本番の設定値や実測として引用しません。

| 欄 | 独立した復元先（架空例A） | 専用replica（架空例B） |
|---|---|---|
| 対象／取得先 | 承認済み既存backupからrestore-host-Aへ復元、固定復元点の通常disk表。SQLは既存versioning、MySQLはInnoDBを確認。サービス共用host/storage/network/backup作業なし | replica-host-Bの専用CPU/memory、SAN/controller/uplinkはprimaryと共有。SQL AG primaryのghost/PVS/log再利用・redo、MySQLの場合source/apply/undoも依存 |
| データ同等性 | 架空のlineage-Aで、元source-Aのdb.t世代G-A→restore-host-Aのdb.t/G-A、完全backup-Aと固定復元点R-Aまでの全適用をDBA Aが復元時に確認した設定。初期コピーからR-Aまでのsource/binlog filterは対象を除外せず、再seed/rebuild/DDLとskip/空GTID/repair/local writeの全履歴を既存監査audit-A・変更台帳で照合し、未解決差分なしという設定（復元先channelなし、replica channel/global filter・書換え・multi-source非該当）。架空の確認時刻2026-11-08T09:00+09:00。適格範囲はG-A・完全backup-Aの基点からR-Aまでのみ | 架空のsource-B/channel-B→db.t/G-B。現在filterとlagが良好でも、初期コピー・過去filter／再seed／skip／local writeの記録が欠落。**同等性UNKNOWN、元表要求HOLD**。架空の確認時刻2026-11-08T09:00+09:00、対象G-B・初期コピーから採取時点までの履歴が不明。既存COUNTがあってもこのコピーのsnapshot件数だけ |
| SQL Server context互換性 | **架空の明示予約**：context-AでCONTEXT_INFOをjob専用予約。DBA Aが対象policy/関数/参照表・業務init完了と後続SETなし・監査/triggerとの非競合を監査した設定。主体・G-A・専用lease開始〜Dispose/releaseの寿命に限りJOB_EXCLUSIVE、job中不変。同一identityで読取/observer/KILL照合する設定 | **UNKNOWN**：今NULLでも後続tenant初期化と監査の使用が不明。予約・承認なしなのでタグ書込みも候補metadata/COUNTもせずHOLD/export。他gateの良好さでは覆さない |
| 鮮度 | 固定復元点R-Aと承認時刻を鮮度台帳fresh-Aに記録し、要求がその時点を許容する設定。現在primary値とはしない | 仮にFRESHNESS適格でも同等性UNKNOWNを覆さない。GTID／lagを来歴の代用にしない |
| HA／経路 | HA所有者Iのtopology-AでHA対象外の独立復元先・endpoint→実backend-A/DBを承認、epoch-A、架空の採取時刻2026-11-08T09:00+09:00、age上限／有効期限は所有者のha-A規定内と確認し、イベント源events-AをSRE Bが監視する設定。専用leaseを保持 | 昇格候補。HA所有者Jのepoch-Bと実backend-Bの照合はできる設定だが、昇格イベント→停止／取消・解放ack→必要なサービス受付gateの連携は未検証。**HA適格性もHOLD** |
| 上限／確認 | CPU 2 core、8GiB、read 40MiB/s・400 IOPS、network 50Mbit/s。Infra CがVM親cpu.max/memory.max、device-Aのio.maxとvNIC shaper、実device/route/counterと試験をinfra-A/caps-Aで確認したという設定 | CPU 2 core、8GiBは親階層まで確認。共有SAN I/O cap、network shaper、物理対応の証拠なし。**共有資源未制御なのでHOLD** |
| 採取元／権限 | app arrivals export、infra-readerのhypervisor/device/vNIC counter、下記DB observer。SRE Bが可視性・取消権限を事前試験した設定 | app export・DB observerは可視。SAN counter取得不可。primary GC回復の上界未確認。不可を0としない |
| 平常変動 | 30分のbaseline、p99 72–84ms、error 0–0.1%、service CPU 22–35%、device queue 1–3ms | 同baseline p99/error、redo queue 0–4MiB。共用SANの変動は不明 |
| 検出・取消 | sample 1秒＋集計2秒＋配送1秒＋判断1秒＋Cancel 2秒＋rollback 3秒＋jitter 2秒＝上界12秒 | 同12秒を仮入力。ただし共用SAN遅延下の妥当性とprimary回復時間は未検証 |
| 停止余裕 | p99停止90ms/SLO120ms、queue停止5ms。架空の反復適格化でlatency危険域まで最小20秒と確認した設定。容量8GiB停止・2GiB下限、最悪増加0.25GiB/sなら24秒。両方が12秒を上回る | p99停止90ms/SLO120ms、redo停止8MiB。ただし停止余裕秒は不明。30ms差から秒へ換算しない |
| 回復 | 120秒観測内で60秒連続baseline、所有session/TX/read view/snapshotなし、undo/version保持解除と容量・cache/I/O正常、統計backgroundなしを確認する設定 | 同窓を仮入力。secondary終了だけでは不十分。primary ghost/PVS/log/data movementの復帰が不明でHOLD |
| 母集団 | planned100/completed100/pending0/failed0、p99 82ms | planned100/completed98/pending2/failed0。完了分だけのp99 82msをSLO合格にしない |
| 担当 | DBA A実行、SRE B監視取消、Infra C上限、Service D採用 | DBA E実行、SRE F監視取消、Infra G共有制御、Service H採用。AG所有者へprimary確認を依頼 |
| 判定 | 架空の範囲で実験完了／精度／予算／cleanup PASS、**illustrative qualification PASSだけ**。実本番採用は所有者の実測・承認待ち | 実験INCOMPLETE、精度UNKNOWN、予算UNKNOWN、cleanup UNKNOWN、適格化／採用HOLD。良好な完了分p99で覆さない |

例Aは来歴・時点・HA承認まで架空の記入例で、実際の同等性やHA連携を検証したものではありません。例Bはcontext互換性・同等性・資源・HAの各不足だけでもHOLDです。
例Bの未完了到着はdeadlineまで追跡し、期限超過なら失敗として分母に残します。測定窓末に残った処理・取消・回復が未確認なら、実験完了とSLOは未確定です。COUNT値の精度、実験完了、予算、後始末の4判定を独立して残します。

## 3. MySQL：接続所有・MDL・undo・複製

承認済みserver UUID、専用接続の`CONNECTION_ID()`、account、接続開始台帳、driverから渡した一意job属性を保持し、監視接続で現在のthreadへ対応付けます。commentは実行文にしか残らない場合があり、sleeping時の唯一の識別子にしません。`session_connect_attrs`を使う場合はdriverが本当に送信し、欠落／切詰めなしに読めることを先に確認します。接続は終了確認まで専用leaseを維持します。
HA所有者の既存トポロジexportとイベント／承認台帳を先に読み、role・昇格候補・topology/routing epoch・採取時刻／ageを§1へ記入します。下のUUID・read_only/super_read_onlyと、SHOW REPLICA STATUSのChannel_Name／Source_UUIDをその対応へ照合します。read_onlyやchannel存在だけでは権威あるroleにならず、過去のlocal writeも否定できません。[read_only公式](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html#sysvar_read_only)
実際のGR構成だけは既存observerで`SELECT MEMBER_ID,MEMBER_ROLE,MEMBER_STATE FROM performance_schema.replication_group_members WHERE MEMBER_ID=@@server_uuid;`も照合します。非GRへ転用せず、承認済みSECONDARY/ONLINEとの一致を確認します。台帳不一致・空／不可・不明はHOLDとし、GRANT／global instruments／構成を自動変更しません。[GR member仕様](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-replication-group-members-table.html)

以下の`?`はobserverがparameterで渡す所有connection ID／schema／tableです。SESSION変数を使って業務接続の状態を変える必要はありません。P_S SELECT権限を持つ既存observerを利用します。threadsは通常のPROCESSLISTと可視性が異なり、P_S SELECTで他threadも見えるため権限を広く配りません。[threads公式](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-threads-table.html)

```sql
SELECT @@server_uuid, @@hostname, @@version, @@global.read_only, @@global.super_read_only;
SELECT NAME, ENABLED, TIMED FROM performance_schema.setup_instruments
WHERE NAME='wait/lock/metadata/sql/mdl';
SELECT THREAD_ID, PROCESSLIST_ID, PROCESSLIST_USER, PROCESSLIST_HOST,
       PROCESSLIST_DB, PROCESSLIST_COMMAND, PROCESSLIST_STATE, INSTRUMENTED
FROM performance_schema.threads WHERE PROCESSLIST_ID=?;
SELECT PROCESSLIST_ID, ATTR_NAME, ATTR_VALUE
FROM performance_schema.session_connect_attrs
WHERE PROCESSLIST_ID=? AND ATTR_NAME IN ('count_job','program_name');
SELECT m.OBJECT_TYPE,m.OBJECT_SCHEMA,m.OBJECT_NAME,m.LOCK_TYPE,m.LOCK_DURATION,
       m.LOCK_STATUS,m.OWNER_THREAD_ID,t.PROCESSLIST_ID
FROM performance_schema.metadata_locks m
LEFT JOIN performance_schema.threads t ON t.THREAD_ID=m.OWNER_THREAD_ID
WHERE (m.OBJECT_SCHEMA=? AND m.OBJECT_NAME=?) OR t.PROCESSLIST_ID=?;
SELECT trx_id,trx_state,trx_started,trx_mysql_thread_id,trx_rows_locked,trx_rows_modified
FROM information_schema.INNODB_TRX WHERE trx_mysql_thread_id=?;
SELECT NAME,COUNT,STATUS FROM information_schema.INNODB_METRICS
WHERE NAME='trx_rseg_history_len';
SELECT CHANNEL_NAME,WORKER_ID,THREAD_ID,SERVICE_STATE,LAST_ERROR_NUMBER,
       LAST_ERROR_TIMESTAMP,APPLYING_TRANSACTION
FROM performance_schema.replication_applier_status_by_worker WHERE CHANNEL_NAME=?;
SHOW REPLICA STATUS FOR CHANNEL 'approved_channel';
```

MDL instrumentation OFF、P_S無効／容量不足（`Performance_schema_metadata_lock_lost`等の既存collector確認）、権限不足、thread/属性欠落は**unavailable**。全表の0件・待ちなしとしません。自動でglobal instruments/consumersを有効化しません。[MDL table公式](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-metadata-locks-table.html)
`INNODB_TRX`と`INNODB_METRICS`はPROCESSが必要です。read-only非locking transactionのtrx ID等に制約があり、不在1回だけでread view解放とせず、専用接続終了・複数採取と既存undo監視を合わせます。[INNODB_TRX](https://dev.mysql.com/doc/refman/8.4/en/information-schema-innodb-trx-table.html)・[INNODB_METRICS](https://dev.mysql.com/doc/refman/8.4/en/information-schema-innodb-metrics-table.html)
History listはserver全体のundo傾向で所有jobだけの保持量ではありません。metric無効なら既存のPROCESS権限collectorによる`SHOW ENGINE INNODB STATUS`のHistory list length等を使用し、SQL本文を含む全出力を配布しません。global設定変更で観測を作りません。[InnoDB monitor](https://dev.mysql.com/doc/refman/8.4/en/innodb-standard-monitor.html)
SHOW REPLICA STATUSはREPLICATION CLIENT（または旧SUPER）、P_S applier表はSELECTを確認。channelの実在／worker構成、受信・適用状態、errors、GTIDとlagを併記し、NULL/停止/権限不足/該当rowなしを遅延0としません。Seconds_Behind_Source単独で鮮度・余力を認定しません。[replica status](https://dev.mysql.com/doc/refman/8.4/en/show-replica-status.html)・[applier表](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-replication-applier-status-by-worker-table.html)

**同等性は鮮度・資源とは別gate**です。§1のsource/channel→対象表・世代、現在と過去のsource/binlog・channel/global filter、初期コピーと以後の全変更来歴を、対象範囲・世代・期間／位置境界・監査時刻付きの既存承認監査／変更記録で確認します。名前書換え・multi-sourceも含め、現在の状態表示だけでは履歴を埋めません。[filter仕様](https://dev.mysql.com/doc/refman/8.4/en/replication-rules.html)
replicaでfilterされたtransactionのGTIDも保存されます。[GTID Life Cycle](https://dev.mysql.com/doc/refman/8.4/en/replication-gtids-lifecycle.html)。同等性不明なら正常COUNTも**このコピー自体のsnapshot件数だけ**で、元サービス表の正確要求は**UNKNOWN/HOLD**です。同等性適格でも許容snapshot点・鮮度は別判定し、primary現在値としません。証明用の本番COUNT／全表checksum／ANALYZE／repair／filter変更は自動投入しません。labは`t_repl`のみ複製し、良好なlagで他表の同等性を認定しません。filter／drift試験を新たに実施した証拠もありません。

停止時はdriver取消、または現在の所有queryを再照合した承認済みKILL QUERY。KILL QUERYはtransactionまで終了させるとは限らず、client finallyで自分のtransactionだけrollbackし、必要なら所有確認したKILL CONNECTIONを担当者が実施します。他accountの取消にはCONNECTION_ADMIN（旧SUPER）、SYSTEM_USER対象には追加条件があります。server identity/thread ID/connection ID/account/job属性とleaseを照合し、再利用・属性欠落なら盲目的取消を拒否します。KILLも原子的所有者比較ではありません。[KILL公式](https://dev.mysql.com/doc/refman/8.4/en/kill.html)

## 4. SQL Server：別接続で可視なidentityと終了確認

**最初にDRIVERの既定拒否gateを実行します。** §1のcontext互換性欄で、所有者が対象RLS policy＋全依存先、tenant/業務初期化（後続SET含む）、監査・triggerと接続全寿命を監査し、CONTEXT_INFOをJOB_EXCLUSIVEと明示承認し、業務init完了・監査済みである場合だけ進めます。承認の主体・対象世代・根拠・寿命が欠ける／UNKNOWN／BUSINESS_USEなら、SET CONTEXT_INFOも主体依存の候補metadata/COUNTも送信せず、業務contextを保持してHOLD／承認済みexportへ戻します。[SQLより前のdriver gateと新C# guard](sql-server.md#3-identityを確認し対象限定の参考値を取る)を必須にし、SQLのcommentだけをserver側保証にしません。

NULL、新規非pool、管理者、可視policyの不在だけでは承認できません。CONTEXT_INFOに依存するRLSの運用COUNT＋取消は未対応です。[公式RLS仕様](https://learn.microsoft.com/en-us/sql/relational-databases/security/row-level-security?view=sql-server-ver17)ではdbo等もpolicy適用対象で、[MicrosoftのCONTEXT_INFOパターン](https://techcommunity.microsoft.com/blog/sqlserver/row-level-security-performance-and-common-patterns/384367/)もあります。RLS可視0と物理空を区別し、全体metadataをtenant countにしません。独断の連結/prefix/保存復元、policy変更/無効化で回避しません。通常の非RLS表で明示予約・他gateを満たす経路は引き続き使えます。

承認後は新規非pool・Enlist=false・MARS=falseの専用接続を使い、既存`@@TRANCOUNT`/`XACT_STATE`を拒否してからIMPLICIT_TRANSACTIONS OFF。乱数job tagをparameterで`SET CONTEXT_INFO @tag`へ**一度だけ**渡し、読取・observer・KILL直前照合は同一の不変identity契約を使います。各候補送信前にも承認とidentityを再確認し、後続SETやdrift／不一致は拒否・エスカレーションします。取消を通す再tagは禁止です。SESSION_CONTEXT()は呼出元sessionの値なので別observerの識別に使いません。[CONTEXT_INFO](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-context-info-transact-sql?view=sql-server-ver17)・[SESSION_CONTEXT](https://learn.microsoft.com/en-us/sql/t-sql/functions/session-context-transact-sql?view=sql-server-ver17)

ownerとobserver双方で次をparameter `@sid`（ownerで取得した@@SPID）へ限定して取得し、server、SPID、login_time、**サーバーDMVのconnection_id**、tag、login/original login、app、DBをすべて照合します。ClientConnectionIdとの同一性を仮定しません。タグは認可の代わりではなく、account/appも単独では強い識別になりません。

```sql
SELECT HAS_PERMS_BY_NAME(NULL,NULL,'VIEW SERVER PERFORMANCE STATE') can_monitor,
       HAS_PERMS_BY_NAME(NULL,NULL,'ALTER ANY CONNECTION') can_kill;
SELECT CONVERT(nvarchar(128),SERVERPROPERTY('ServerName')) server_name,
       s.session_id,s.login_time,c.connection_id,s.context_info,
       s.login_name,s.original_login_name,s.program_name,DB_NAME(s.database_id) db_name,
       s.status,s.open_transaction_count
FROM sys.dm_exec_sessions s JOIN sys.dm_exec_connections c ON c.session_id=s.session_id
WHERE s.session_id=@sid AND c.parent_connection_id IS NULL;
SELECT session_id,request_id,status,command,wait_type,wait_time,blocking_session_id
FROM sys.dm_exec_requests WHERE session_id=@sid;
SELECT session_id,transaction_id,is_user_transaction,open_transaction_count
FROM sys.dm_tran_session_transactions WHERE session_id=@sid;
SELECT session_id,transaction_id,is_snapshot,transaction_sequence_num,elapsed_time_seconds
FROM sys.dm_tran_active_snapshot_database_transactions
WHERE session_id=@sid OR transaction_id=@captured_txid;
SELECT transaction_id,transaction_type,transaction_state
FROM sys.dm_tran_active_transactions WHERE transaction_id=@captured_txid;
```

SQL Server 2022以降では、上記[sessions](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-exec-sessions-transact-sql?view=sql-server-ver17)、[connections](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-exec-connections-transact-sql?view=sql-server-ver17)、[requests](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-exec-requests-transact-sql?view=sql-server-ver17)、[session transactions](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-tran-session-transactions-transact-sql?view=sql-server-ver17)、[active snapshots](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-tran-active-snapshot-database-transactions-transact-sql?view=sql-server-ver17)の全体可視性には各仕様のVIEW SERVER PERFORMANCE STATEが必要です。旧版は各DMVのVIEW SERVER STATE等を確認します。自sessionだけ見えるrestricted observer、空結果、エラーは監視不能で、sessionなし／transactionなしと認定しません。permission preflightと所有fixtureの実可視性を両方確認し、本番へ自動GRANTしません。

**requestsが0でも、sleeping sessionにtransactionとsnapshotが残り得ます**。別管理接続自身の@@TRANCOUNT=0では対象の終了を証明できません。保持した全対象TX IDをsession消失後もsys.dm_tran_active_transactionsで追跡し、所有sessionとtransaction/snapshot entryの終了を確認します。完了entryが短時間残ることもあるため、bounded pollし期限超過はUNKNOWNで担当へ返します。

### client finallyがcleanupを所有する

[TRY/CATCHはattentionを捕捉しません](https://learn.microsoft.com/en-us/sql/t-sql/language-elements/try-catch-transact-sql?view=sql-server-ver17)。SQL内CATCHや末尾ROLLBACKへ到達すると期待せず、command timeout/Cancel/例外に対しdriver finallyで次を実装します。実装例は[専用C# runner](../../sqlserver/issue3/Program.cs)の`Initialize`/`PrepareAndCount`/`GuardedCandidateCount`/`PositiveCase`/`Cleanup`/`GuardedKill`/`PollRelease`です。これは既知のlab fixture限定で、本番用実装ではありません。

1. 新規専用sessionのidentityと、自分が開始したtransactionの事実・server TX IDを保持する。取消前も別管理接続で同identityを確認する。新しい作業を投入しない。
2. cleanup用には、取消済みのwork tokenとは別の期限付きCancellationTokenを用意する。元接続が使用可能なら同接続でidentityと自分のtransaction状態を確認し、別のbounded commandでそのtransactionだけROLLBACK。元々の業務transactionを扱う接続は開始時に拒否する。rollback失敗／接続不通は次へ。
3. 管理接続で現在のidentityを確認。KILLにはALTER ANY CONNECTION（sysadmin/processadmin等が持つ）が必要。権限・可視性がないなら管理担当へ終了不明として引き継ぐ。一致すれば**同じadmin batch内で再照合して直後にKILL**。不一致ならKILLしない。[KILL公式](https://learn.microsoft.com/en-us/sql/t-sql/language-elements/kill-transact-sql?view=sql-server-ver17)
4. KILLはSPIDだけを受け取るため、同batchの照合＋KILLもatomic compare-and-killではない。可能な限り元接続を専用leaseのまま保ち、同時cleanupや再接続・ID再利用を減らす。raceを絶対排除できるとはしない。古いSPIDへのKILL再送は禁止。STATUSONLYも追跡中の所有rollback文脈でだけ使う。
5. 対象session／実TX ID／snapshot entry終了をpoll。元接続もDisposeし、poolへ返さない。期限内に確認できなければUNKNOWN、次表へ進まずDBAへ引継ぎ。DBAは既存incident手順で判断する。

| 経路 | 保存するもの／回復確認 |
|---|---|
| server error | SqlException.Errors全件のNumber/State/Class、経過時間、元TX状態。CATCH可否に依存せずfinally、対象TX/snapshot終了へ |
| Cancel／command timeout | Cancel呼出結果・driver例外と単調時計、post-attention sleeping/open TX、rollback/KILL結果、retention release。Cancelの戻りだけを完了にしない |
| 通信失敗 | 実際に行ったfault（Dispose/transport abort/実分断等）を記録。元接続使用不能なら管理経路。観測も不通なら終了不明として担当へ、再投入禁止 |

専用runnerはidle open TXのSqlConnection.Disposeを模擬し、別途client rollback失敗注入で管理KILL経路を検証します。実ネットワークpartition/ブラックホール時の検出・取消・復旧は未検証です。実分断で管理経路まで失われる場合、owner担当が終了不明を扱う必要があります。

### versionの保持解除、割当容量、サービス回復を区別する

DBを承認scopeへ限定し、既存collectorから以下を取得します。

```sql
SELECT database_id,reserved_page_count,reserved_space_kb
FROM sys.dm_tran_version_store_space_usage WHERE database_id=@dbid;
SELECT database_id,persistent_version_store_size_kb,oldest_active_transaction_id,
       oldest_aborted_transaction_id,secondary_low_water_mark,
       offrow_version_cleaner_start_time,offrow_version_cleaner_end_time
FROM sys.dm_tran_persistent_version_store_stats WHERE database_id=@dbid;
-- 対象DB contextでのみ。物理disk空きは別途host/storage監視が必要。
SELECT file_id,type_desc,size,FILEPROPERTY(name,'SpaceUsed') used_pages FROM sys.database_files;
SELECT database_id,log_reuse_wait_desc FROM sys.databases WHERE database_id=@dbid;
```

[tempdb version集約](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-tran-version-store-space-usage?view=sql-server-ver17)は2022以降VIEW SERVER PERFORMANCE STATE（旧VIEW SERVER STATE）、[PVS DMV](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-tran-persistent-version-store-stats?view=sql-server-ver17)は同公式のVIEW SERVER PERFORMANCE STATEを確認します。PVSはADR構成等の適用条件を確認し、非該当・rowなし・不可はゼロ扱いしません。file metadataは対象DBの可視性も確認。[database files](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-database-files-transact-sql?view=sql-server-ver17)

対象snapshot entryの消失はその保持依存の終了を示し、version store reserved KBの即時ゼロや物理割当の返却を意味しません。DB全体の容量には他transaction/GCも含まれ、各jobの使用量とはしません。保持解除、割当回収、disk余裕、cache/I/O、サービス応答の回復を別欄に記録します。既定監視に高価な`sys.dm_db_index_physical_stats`の全走査を追加しません。

AGはHA所有者の権威あるtopology／routing export・イベント・承認を§1へ記入し、意図したlistener、実server/DB、epochと採取時刻／ageを確認します。`ApplicationIntent=ReadOnly`だけでは実行先の適格性を保証しません。[routing公式](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/configure-read-only-routing-for-an-availability-group-sql-server?view=sql-server-ver17)
primary/secondary双方の既存承認済みcollectorで、対象group_idのlocal roleと対象group_database_idのis_primary_replicaを次で照合します。非AG復元先はHA所有者の対象外承認・復元来歴を使い、DMV空集合を適格roleと解釈しません。SQL Server 2022以降VIEW SERVER PERFORMANCE STATE、旧版VIEW SERVER STATEを確認します。[AG DMV](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-hadr-database-replica-states-transact-sql?view=sql-server-ver17)

```sql
SELECT group_id,replica_id,is_local,role_desc,operational_state_desc
FROM sys.dm_hadr_availability_replica_states
WHERE group_id=@approved_group_id AND is_local=1;
SELECT group_database_id,replica_id,is_local,is_primary_replica,synchronization_state_desc,
       synchronization_health_desc,is_suspended,suspend_reason_desc,
       log_send_queue_size,redo_queue_size,last_hardened_lsn,last_redone_lsn
FROM sys.dm_hadr_database_replica_states WHERE group_database_id=@approved_group_dbid;
```

実接続先のis_local=1行でrole_desc=SECONDARYかつ対象DBのis_primary_replica=0を承認トポロジと照合します。[local role DMV](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-hadr-availability-replica-states-transact-sql?view=sql-server-ver17)も同じ監視権限を確認し、部分可視・RESOLVING・stale・不可はHOLD。role変更等は§1の失効手順へ進め、照会成功だけで継続許可にしません。新規GRANT／global instrumentation／HA設定変更はしません。
primaryのghost回収・data/PVS空き、log再利用待ち、secondaryの長いsnapshot、送受信／redo／data movement・切断を一緒に追跡します。ghost量の安価な既存観測がない場合、容量／回収を未検証とし、COUNTのために全索引scanで代用しません。長いsecondary読取はprimaryのghost回収や領域再利用にも影響し得ます。[AG読取公式](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/active-secondaries-readable-secondary-replicas-always-on-availability-groups?view=sql-server-ver17)
切断やmovement停止ではAG所有者の復旧手順へ。独断でreplica削除・設定変更をしません。

## 5. SQL Server：既存自動統計とcompile/backgroundの費用

旧labの`sqlserver/setup.sql`はAUTO_CREATE_STATISTICS/AUTO_UPDATE_STATISTICSをOFFにしています。この条件の費用観測を、既存ON/async利用先へ無条件に移せません。手動UPDATE STATISTICSを禁止しても、engineによる自動作業を禁止したことにはなりません。本番設定を一律OFF/ONにせず、開始前後に以下の既存状態を記録します。

```sql
SELECT name,compatibility_level,is_auto_create_stats_on,is_auto_update_stats_on,
       is_auto_update_stats_async_on,is_read_committed_snapshot_on,
       snapshot_isolation_state,CONVERT(nvarchar(128),SERVERPROPERTY('ProductVersion')) version
FROM sys.databases WHERE database_id=DB_ID();
SELECT name,value,value_for_secondary FROM sys.database_scoped_configurations
WHERE name=N'ASYNC_STATS_UPDATE_WAIT_AT_LOW_PRIORITY';
SELECT s.name,s.stats_id,s.auto_created,s.user_created,s.has_filter,
       p.last_updated,p.rows,p.rows_sampled,p.modification_counter
FROM sys.stats s OUTER APPLY sys.dm_db_stats_properties(s.object_id,s.stats_id) p
WHERE s.object_id=@approved_object_id;
```

sys.databasesは対象DB可視性、scoped configurationsはpublic、stats propertiesは統計列SELECT／表所有者等の権限を確認します。権限追加は自動化せずNULL/空rowも意味を区別します。[config可視性](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-database-scoped-configurations-transact-sql?view=sql-server-ver17)・[stats properties](https://learn.microsoft.com/en-us/sql/relational-databases/system-dynamic-management-objects/sys-dm-db-stats-properties-transact-sql?view=sql-server-ver17)

版・compatibility・role・schema・plan・cache状態と、対応版のASYNC_STATS_UPDATE_WAIT_AT_LOW_PRIORITYを記録します（2022以降の機能。未対応／不可は非該当または未検証）。COUNTがどの統計を参照し、更新を誘発するかは実plan次第です。承認済みplan収集のOptimizerStatsUsageとnamed stats before/afterを照合し、必要なら既存承認済みの狭いauto_stats XEを担当者が使います。plan取得にSHOWPLANと実文の権限、XEには版ごとの管理権限が別途必要です。本番に新規global trace/設定を自動作成しません。[statistics仕様](https://learn.microsoft.com/en-us/sql/relational-databases/statistics/statistics?view=sql-server-ver17)・[STATISTICS XML権限](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-statistics-xml-transact-sql?view=sql-server-ver17)

compile待ち・同期統計作業と、文終了後にも残るasync backgroundのCPU/I/O/metadata待ちを予算と回復窓へ含めます。対象requestだけではbackground終了を証明できないので、既存collectorのDB scope requests/waits・stats時刻・必要時XEで確認します。狭い窓で変化なしなら「当schema/plan/state/窓で未観測」であり「COUNTでは絶対起きない」ではありません。該当性や残作業が不明なら未検証として適格独立コピー／HOLDへ戻します。

過去の[Issue 1 runner](../../sqlserver/issue1/README.md)はlab内OFF/sync ON/async ONだけを比較し、無条件COUNTのplan・stats状態と限定した後続窓を保存します。predicate付きCOUNTや無関係な統計参照で発火させません。当時の結果・sourceの受入は[修正前snapshotのIssue 1受入](../../results/reference/issue-2-pre-fix-de1c79c/results/reviews/issue-1/acceptance.json)の範囲です。現行文書は冒頭のIssue 3受入記録で別途確認します。機能検証だけで本番費用・SLOを認定しません。

所有者が2026-11-08に固定runnerを実行し、[実行・監査証跡](../../results/reviews/issue-1/owner-proof.json)と[完全log・plan・JSONの保存先](../../results/reference/issue-1-runtime-20261108T013522-6bd01cec80ff/manifest.json)を残しました。server error、client Cancel、command timeout、idle接続Dispose、管理側終了へのfallbackの5経路で、別接続による所有確認と対象session／保持したtransaction ID／snapshot entryの不在を確認しました。tag・login_time・サーバーconnection_idの各不一致は取消を拒否し、権限不足は監視不能と判定しました。

同runの4,096行fixtureではOFF／sync ON／async ONの各無条件COUNT_BIGが4,096を返し、実planを取得しました。OptimizerStatsUsage要素はなく、既知のnamed statsは取得前後と限定観測窓で不変でした。窓は累積約0・1・4秒であり、統計更新やbackground作業の一般的な不発火を証明しません。SQL Server 2025 CU10のamd64をARM64上で模擬した機能検証です。物理領域の回収時間、本番サービス回復・SLO、実ネットワークpartition、全表形式・全統計状態の費用は未検証です。

[Issue 3修正前snapshot](../../results/reference/issue-3-pre-fix-16cc9b3/manifest.json)と[歴史的Issue 2受入](../../results/reference/issue-3-pre-fix-16cc9b3/results/reviews/issue-2/acceptance.json)は保存された旧hashだけに適用します。現行Issue 3集約受入は所有者の専用実行・意味/source/後始末監査と別セッションSol 2役の最終査読の完了を条件とします。記録なし・hash不一致はHOLDであり、受入済みとは扱いません。

旧[Issue 1 Program](../../sqlserver/issue1/Program.cs)はRLSなしfixture限定の凍結実装で、Initializeの無条件CONTEXT_INFO上書きを本番例へ転用しません。上記4,096行の成功はRLS context安全性を検証していません。[Issue 3仕様](../../sqlserver/issue3/README.md)は5行RLS fixtureのcontrol可視数2/3/0と、同じ候補入口によるHOLD・業務context維持・タグ書込み0／候補COUNT送信0・所有Dispose後の解放を別記録にします。control COUNTは候補精度承認ではなく、CONTEXT_INFO-RLSでの運用COUNT＋取消の認定でもありません。新しい結果がなければ未検証です。

Issue 3の所有者実行では[固定allの証明と監査](../../results/reviews/issue-3/owner-proof.json)および[完全log・plan・JSONの保存先](../../results/reference/issue-3-runtime-20261108T105711-1869b4437314/manifest.json)を残しました。CONTEXT_INFO依存RLSを有効にした5行fixtureの対照COUNTは可視2／3／0件で、0件の主体でも物理seed・global参考値は5件でした。業務利用・UNKNOWN・承認欠落と後続業務initを含む7検査パスで、同じ候補入口と直接COUNT wrapperを拒否し、contextは元接続・別observerとも維持、tag書込み・候補COUNT送信は各0回でした。未対応RLS表への拒否だけでなく、対象表を変えた契約単独の拒否も確認しました。4つの所有接続はDispose後、request／TX／snapshot／sessionの不在を確認しています。

予約済み非RLS fixtureでは従来の5異常系・別接続照合・3識別不一致拒否・保持TX IDを含む解放確認も通りました。これらは別の検証であり、CONTEXT_INFO-RLSの運用COUNT＋取消を認定しません。SQL Server 2025 CU10のARM上amd64エミュレーションによる小規模機能観測で、native性能・本番SLO・実ネットワーク分断・物理領域回収時間は未検証です。
