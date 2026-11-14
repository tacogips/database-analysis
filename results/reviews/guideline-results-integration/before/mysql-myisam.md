# MySQL MyISAM：件数取得の手順とQ&A

MyISAMは件数を保持しているため、**確認済みのMyISAM表ならTABLE_ROWS取得や索引ページ数の調査は不要です。** `db`・`table_name`は対象名に置き換えます。

## 事前準備：作業環境を選ぶ

- **サービスからのクエリが飛ばないread replicaや分析用DBがあれば、そこを作業環境にする。** replicaでもサービスreadを受けている場合は専用の分析環境とは扱わない。
- 対象表・行が揃っていることと、データの反映時点を確認する。以降のSQLは選んだ環境で実行し、結果にはその環境と時点を記録する。
- 専用環境がなければ、サービスのSELECTも処理するread replicaを次の候補にする。DB担当者と通常負荷に対する余力・許容上限を確認し、閑散時間帯にCOUNTをまず1本だけ実行する。
- サービスのSELECTも処理するread replicaではサービス応答・CPU・I/O・複製遅延を監視し、上限超過で中止する。実行期限・監視・取消を用意できなければ、別の実行先を準備する。サービスへの影響がないことを保証する手順ではない。
- writer（primary/master）へのCOUNTはこの手順の代替先にしない。read replicaでも影響があることを理由に、writerへ切り替えない。
- 新しい専用接続を使い、途中で自動再接続しない。取消の対象照合が終わるまで接続を他用途へ返さない。

MyISAMではRDS read replicaを推奨しません。既存の自前replicaか、MyISAMに対応した独立コピーを選びます。サービスのSELECTも処理するread replicaを候補にする場合もRDSのMyISAM replicaは含めません。コピー作成時の負荷・ロックは別途確認します。

## 実行手順

### 1. MyISAMか確認する

確認済みなら省略します。不明な場合だけ次を実行します。

```sql
SELECT TABLE_SCHEMA, TABLE_NAME, ENGINE
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'db'
  AND TABLE_NAME = 'table_name'
  AND TABLE_TYPE = 'BASE TABLE';
```

InnoDBなら[InnoDBの手順](mysql-innodb.md#実行手順)へ進みます。結果がない場合は対象名と既存の権限を確認し、0件とは扱いません。

### 2. COUNTの実行先を選ぶ

- サービスと資源を分けた、更新の入らない独立コピーを選ぶ。
- 既存の自前専用replicaも候補だが、COUNTのテーブルロックが複製更新を待たせる可能性がある。サービスreadを受けない環境を優先する。サービスのSELECTも来る場合は事前準備の条件を確認する。
- RDSのMyISAM replicaは推奨しない。RDS snapshot復元もMyISAMで無条件に使えるとは扱わない。
- コピーに対象表・行が揃っていることと、データの時点を確認する。コピー作成時の負荷・ロックは別途確認する。

### 3. 監視と取消方法を準備する

- 業務接続を流用せず新しい専用接続を作り、クライアント側の実行期限を設定する。業務データ・表定義・DB全体の設定は変更しない。
- CPU・I/O・サービス応答を監視する。自前replicaなら既存の複製監視で遅延・エラーも確認する。停止する上限を決める。
- 別管理接続で対象queryを確認・取消できることを確認する。接続先の役割がサービス用へ変わった場合も中止する。

```sql
SELECT @@server_uuid AS server_uuid, @@hostname AS hostname,
       CONNECTION_ID() AS connection_id;
```

接続先と接続IDを記録します。接続先や取消方法を確認できない場合は、確認できる実行先・接続を担当者に用意してもらいます。

### 4. 1表ずつCOUNTする

```sql
SELECT /* count_job_example */ COUNT(*) AS exact_rows
FROM `db`.`table_name`;
```

単一表・WHEREなし・他の列を取得しない形を使います。保持件数を利用しますが、読取りのテーブルロックは残ります。

### 5. 終了を確認して記録する

上限超過・監視不能・役割変更時は次の表の投入を止め、別管理接続で今回の接続ID・ユーザー・DB・SQLを確認します。

取消用の管理接続でも次を実行し、`server_uuid`が保存した値と一致することを確認します。不一致ならKILLせず、正しい接続先へ接続し直します。

```sql
SELECT @@server_uuid AS server_uuid;
SHOW FULL PROCESSLIST;
```

一致を確認した現在の接続IDを指定します。`12345`は例です。

```sql
KILL QUERY 12345;
```

- 取消後はserver上でCOUNTが終わったことを確認し、専用接続を閉じる。終了不明なら次表へ進まず担当者に引き継ぐ。
- CPU・I/O・サービス応答と、replicaの場合は複製の回復を確認する。
- 成功した表名・件数・実行先・開始終了時刻・コピーのデータ時点を記録する。

## 性能調査と補足

[MySQLの検証条件と限界](mysql-details.md#7-表シャードごとの記録と観測限界)／[監視・取消の詳細](operations-runbook.md#3-mysql接続所有mdlundo複製)

MyISAMのサービス稼働中の無影響は、このrepoでは実測していません。

## QA

### Q MyISAMは件数を保持しているなら、先にTABLE_ROWSを取得しなくてよいか

A **MyISAMと確認済みで正確な件数を取得するなら、TABLE_ROWSは不要です。** 単一表・WHEREなし・他の列を取得しない`SELECT COUNT(*) FROM db.t`は、エンジンが保持する正確な件数を利用できます。InnoDBのような全件の索引走査は必要ありません。[COUNT仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)

TABLE_ROWSは全表のENGINEと件数を一覧にする用途では便利ですが、キャッシュされた古い値の場合があります。COUNT前の必須手順ではありません。COUNTは実行先・監視・取消方法を確認してから実行します。保持件数を使ってもテーブルロックや待ちは残り、コピー先で数えた値はコピー時点の件数です。

### Q 暗号化されたMyISAMでもSELECT COUNTで取得できるか

A **ストレージが暗号化されていても、正常にアクセスできる状態なら同じSQLで取得できます。** アプリで暗号化した値を保存している場合も、行数を数えるための復号は不要です。

```sql
SELECT COUNT(*) FROM `db`.`table_name`;
```

単一表・WHEREなしのCOUNT(*)では、MyISAMが保持する正確な件数を使います。全行を復号して数える必要はありません。ただし、テーブルの読取りロックは残り、更新との競合に注意する点は暗号化なしと同じです。[COUNTの仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)・[テーブルロック](https://dev.mysql.com/doc/refman/8.4/en/internal-locking.html)

MySQL標準の`ENCRYPTION='Y'`はInnoDB専用で、MyISAMには使えません。RDS等のストレージ暗号化と、InnoDBの表領域暗号化を区別します。復元先では必要なストレージ暗号化鍵を利用できる状態にしてから接続します。[ENCRYPTIONの仕様](https://dev.mysql.com/doc/refman/8.4/en/create-table.html)・[RDSのストレージ暗号化](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Overview.Encryption.html)

### Q 暗号化の有無で、件数取得時に気を付けることはあるか

A **COUNTのSQLは変えません。確認するのは鍵の利用可否と実行負荷です。**

- **InnoDBの保存時暗号化**：DBサーバーが鍵を利用できれば、通常の`COUNT(*)`を使います。実行者が復号鍵をSQLに渡す必要はありません。ディスク読取り時の復号処理も含め、CPU・I/Oを監視します。[MySQL公式](https://dev.mysql.com/doc/refman/8.4/en/innodb-data-encryption.html)
- **RDSのストレージ暗号化**：SQLは同じです。コピー・復元先で必要なKMS鍵を利用できるか確認します。これはInnoDBの表領域暗号化とは別の仕組みです。[AWS公式](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Overview.Encryption.html)
- **アプリが列に暗号文を保存している場合**：WHEREなしの`COUNT(*)`に平文の復号処理は不要です。DBがその表を読めることを確認し、復号関数は追加しません。
- **MyISAM**：InnoDBの表領域暗号化の説明は適用しません。ストレージ暗号化・アプリの暗号化など、実際に使用している方式を確認します。

件数取得のために暗号化を解除したり、鍵・既存設定を変更したりしません。暗号化の有無だけでCOUNTのロック方式は変わりません。実環境での暗号化による性能差は、このrepoでは未計測です。

### Q MyISAMの専用replicaでも、CPU・I/Oと複製遅延の監視は必要か

A **必要です。** 既存の自前MySQL replicaで実行する場合、サービスのSELECTが来ない接続先を優先します。なければ事前準備の条件を満たすread replicaで実行し、サービス応答・CPU・I/O・複製遅延・複製エラーを監視します。MyISAMでは資源競合に加え、テーブルロックが複製更新を待たせる可能性があります。無条件COUNTが高速でも監視を省きません。

許容上限超過・監視不能ならCOUNTを取消し、server上の終了と複製の回復を確認してから取得方法を見直します。RDSの`ReplicaLag`はRDS用の指標名なので、自前MySQLでは既存の複製監視を使います。**RDSのMyISAM replicaを推奨する手順ではありません。** 更新の入らない独立コピーでは複製遅延の監視は不要ですが、CPU・I/Oと共有資源への影響は確認します。[MyISAMのロック](https://dev.mysql.com/doc/refman/8.4/en/internal-locking.html)・[RDSのサポート範囲](https://aws.amazon.com/rds/mysql/faqs/)

### Q COUNT(1)とCOUNT(*)は同じか。アスタリスクで遅くならないか

A **同じ対象・同じ時点なら件数は同じです。全件数には`COUNT(*)`を使います。** `COUNT(*)`の`*`は行を数える指定で、`SELECT *`のように全列の値を取得する指定ではありません。

- **InnoDB**：`COUNT(*)`と`COUNT(1)`は同じ方法で処理され、性能差はありません。`1`へ変えても高速化しません。
- **MyISAM**：単一表・WHEREなし・他の列を取得しない`COUNT(*)`は、保持件数を使う最適化の対象です。公式仕様では`COUNT(1)`が同じ最適化を受けるのは先頭列が`NOT NULL`の場合に限られます。`COUNT(1)`へ置き換える利点はありません。

`COUNT(列名)`はその列がNULLの行を数えないため、全件数の代用にはしません。[MySQL公式仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)

### Q TABLE_ROWSの値に誤差が出る原因は何か

A **InnoDBでは「一部を調べて全体を推定する誤差」と「更新からの遅れ」が主な原因です。** COUNTとの比較時点の違いも区別します。

- **標本からの推定**：全行を数えず、索引ページの一部を調べて統計を作るため、選ばれたページやデータの分布によって過大・過小の両方が起こる。統計が新しくても誤差は残る。
- **行数の変化への追従の遅れ**：INSERT・DELETE後も、統計は毎回正確に数え直されるわけではない。永続統計の自動再計算もバックグラウンド処理なので、更新直後の正確値にはならない。[統計の標本抽出と更新](https://dev.mysql.com/doc/refman/8.4/en/innodb-persistent-stats.html)
- **TABLE_ROWSのキャッシュ**：エンジン側の統計とは別に、INFORMATION_SCHEMAが以前取得した値を返す場合がある。`information_schema_stats_expiry`の既定値は24時間。これを0にしても、InnoDBの推定値が正確値に変わるわけではない。[統計キャッシュ](https://dev.mysql.com/doc/refman/8.4/en/information-schema-tables-table.html)
- **COUNTと見ている状態が違う**：COUNTはその文／transactionから見える行を数えるが、統計は同じsnapshotの件数ではない。取得の間にINSERT・DELETEがあった場合や、未COMMITの変更の扱いが違う場合も差が出る。[統計計算での削除行の扱い](https://dev.mysql.com/doc/refman/8.4/en/innodb-persistent-stats.html#innodb-persistent-stats-delete-marked)

**MyISAMは正確な件数を保持するため、InnoDBのような標本による件数の推定誤差はありません。** ただしTABLE_ROWSのキャッシュや、COUNTとの取得時点の違いによる差はあります。MyISAMの正確件数は、このガイドの通常の`COUNT(*)`で取得します。

「統計を更新すれば必ず正確」「誤差は必ず±何%以内」とは判断しません。件数取得のために本番でANALYZEや設定変更をせず、正確値が必要なら各ガイドの実行先・監視条件を満たしてCOUNTします。

### Q なぜTABLE_ROWSはInnoDBでは概算なのか。MyISAMでは正確なのか

A **エンジンが件数を管理する方法が違うためです。**

- **InnoDB**：同じ表でもトランザクションごとに見える行が違うため、全接続共通の正確な件数を保持しません。`TABLE_ROWS`は統計からの推定値です。正確な件数が必要なら、対象トランザクションから見える行を`COUNT(*)`で数えます。[MySQLのCOUNT仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)
- **MyISAM**：エンジンが正確な行数を保持しています。単一表・WHEREなしの`COUNT(*)`はその値を利用でき、全行を走査せず取得できます。[MySQLのCOUNT仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)

ただし、**MyISAMでも`information_schema.TABLES.TABLE_ROWS`が現在の正確値とは限りません**。`information_schema_stats_expiry`によるキャッシュがあり、更新前の値を返す場合があります。エンジンが保持する件数の正確さと、返された値の古さは別です。このガイドではキャッシュ設定を変更せず、時点が確認できない値は現在の正確値として使いません。高速に取得できることも、ロックやサービスへの影響がない保証にはなりません。[TABLESと統計キャッシュの仕様](https://dev.mysql.com/doc/refman/8.4/en/information-schema-tables-table.html)


### Q バックアップから別DBを作るとは。バックアップ取得でサービス性能は落ちないか

A **既存のバックアップを読み込んで、新しいDBインスタンスを作り、そのDBへ接続してCOUNTする**という意味です。元のサービスDBを上書きしません。保存済みバックアップを使う作業と、新しく本番DBのバックアップを取る作業は別です。まず必要な時点を満たす既存バックアップがあるか確認します。[RDSのsnapshot復元手順](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_RestoreFromSnapshot.html)

**新規バックアップ取得は性能へ影響し得ます。** RDSのSingle-AZ DB instanceではsnapshot取得に伴う一時的なI/O停止があり得ます。MySQLのMulti-AZ DB instanceではstandbyから取得するためprimaryのそのI/O停止は避けられますが、性能への影響全般がゼロという保証ではありません。AuroraやMulti-AZ DB clusterは別の構成として仕様を確認します。MyISAMはRDSのsnapshot復元がInnoDBと同様にサポートされるとは扱いません。[AWSのsnapshot取得時の影響](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_CreateSnapshot.html)

COUNTのために本番snapshotやreplica作成を自動実行しません。新規取得が必要なら、DB担当者が方式・取得元・時間帯・許容するサービス応答悪化・監視・中止対応を決めます。既存バックアップで足りるなら、それを使うことで新たに本番をbackupする作業を避けられます。復元先も共有storage/networkとの競合、鍵、データの対象範囲と時点を確認します。復元直後は読み込みが遅いことがあるため、復元先でのCOUNTの時間・I/O上限も別途設けます。


### Q MyISAMならCOUNTが速いのでは。InnoDBと分ける必要はあるか

A 分ける必要があります。MyISAMは行数を保持し、条件を満たす無条件COUNTを速く返せる場合があります。一方、InnoDBは読取時点に見える行を数え、両者は同じ実装ではありません。MyISAMにはtable-level lockingがあるため、InnoDBの「通常SELECTは行ロックを取らない」という説明だけを根拠にサービスDBで実行しません。[COUNTの製品仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)・[MyISAMのロック](https://dev.mysql.com/doc/refman/8.4/en/internal-locking.html)

このrepoでは非InnoDBは静止した検証表の参考情報だけを対象とし、サービスへの負荷試験はしていません。MyISAM・その他・engine不明なら、DB担当者がengine・クラウドの制約に対応した取得方法を準備して数えます。COUNTのためにengineを変更する手順ではありません。

### Q MyISAMのCOUNTでテーブルロックを取らない方法はないか

A **MyISAMの`SELECT COUNT(*)`を、SQLの書き換えでテーブルロックなしにする方法はありません。** `LOCK TABLES`を書かなくても、MyISAMは読取りにテーブル単位のロックを使います。保持件数を使って速く返せることと、ロックを取らないことは別です。[MySQLのロック仕様](https://dev.mysql.com/doc/refman/8.4/en/internal-locking.html)

- **件数情報で足りる場合**：[一覧取得SQL](mysql.md#対象dbのengineと件数情報を確認する)を使います。ただし古いキャッシュ値の場合があり、metadata照会自体もロックゼロとは保証できません。
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

### Q 重いクエリは1本だけ。それでも大きな影響が出るのか

A **必ず大きな影響が出るわけではありません。1本という本数だけでは判断できません。** 余力が十分で、scan対象がcacheに収まり、サービスとの競合が小さければ、許容範囲に収まることもあります。一方、1本でも走査がCPU・I/Oを使う、key cacheやOS cacheで競合する、テーブルロックが更新や複製適用を待たせる、といった条件でサービスreadや複製適用の余力を削り得ます。CPU全体に余裕があってもI/Oが詰まる場合があります。ただしMyISAMの単一表・WHEREなしのCOUNT(*)は保持件数を利用でき、全行走査が必要とは限りません。高速でもロックゼロは保証されません。

無条件COUNTは結果が1行でも多数のページを読む場合があり、返却行数と作業量は別です。「遅いSQLが1本ある」こと自体より、実行前後のサービス応答・error、I/O待ち、ReplicaLagの変化を予算に照らして判断します。一般の重いSELECTにはjoin/sort等もありますが、単純COUNTにそれらの費用が必ず発生するという意味ではありません。影響の大きさは未計測で、一律に危険とも安全とも認定しません。

### Q slow query logに出ることも注意すべきか

A **出力条件に該当すれば記録されるため、既存の監視運用に合わせて実行を識別できるようにします。** RDS for MySQLではslow_query_log、long_query_time、log_output等の実効設定を対象replicaで確認します。CloudWatch Logsへの出力は別途export設定も必要です。短時間でも索引を使わないqueryを記録する設定等があるので、時間だけで出力有無を断定しません。[AWSのログ仕様](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_LogAccess.MySQL.LogFileSize.html)

- 1回の長いCOUNTが、scanした行の数だけslow logへ出るわけではありません。通常は実行したstatementの記録です。1本の短いSQLを1回記録する費用だけで大きな劣化が起こると決めつけず、query本体のscan費用とログ書込費用を分けます。ログ費用はこのrepoでは未計測です。
- 定期実行・timeout後の再試行・多数表の連続実行で記録数が増える場合や、TABLE出力・大きなSQL本文・低すぎる閾値では、ログI/O・容量・CloudWatch取込／保持費用も確認します。AWSは性能面でFILE出力を推奨していますが、COUNT一回のため既存設定を自動変更しません。[AWSの性能上の推奨](https://aws.amazon.com/blogs/database/performance-optimization-strategies-for-mysql-on-amazon-rds/)
- 専用ユーザー・接続先・実行時刻・機密を含まないjob IDを台帳に残します。SQLコメントの保持やアラート側の識別可否は実際の収集経路で確認し、コメントだけへ依存しません。ログにSQL・schema名等が残ることを考慮し、コメントへ個人情報や秘密を入れません。
- slow log連携の通知がある場合は予定集計と識別します。予定queryであることだけを理由にサービス悪化の通知を抑止せず、遅延・error・複製の監視は維持します。ログに出ないようslow logを無効化したり閾値を上げたりして、安全性の代用にはしません。

slow logに載ったことは実行時間等が出力条件を満たした証拠であり、サービス性能が悪化した証拠とは別です。また、通常はstatementの実行後に記録されるため、実行中queryの監視・取消をslow logの到着だけに依存しません。[MySQL slow query log仕様](https://dev.mysql.com/doc/refman/8.4/en/slow-query-log.html)


## 性能調査と詳しい運用手順

[MySQLの検証結果と限界](mysql-details.md#7-表シャードごとの記録と観測限界)／[監視・取消の詳細](operations-runbook.md#3-mysql接続所有mdlundo複製)。このrepoのMyISAM観測は静止した表の参考情報であり、サービス稼働中の無影響やロックゼロを実証していません。

[確認する理由・用語の補足](count-check-rationale.md)
