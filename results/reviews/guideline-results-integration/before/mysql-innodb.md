# MySQL InnoDB：件数取得の手順とQ&A

`db`・`table_name`は対象のDB名・表名に置き換えます。業務接続を流用せず、新しい専用接続を使います。業務データ・表定義・DB全体の設定は変更しません。

## 事前準備：作業環境を選ぶ

- **サービスからのクエリが飛ばないread replicaや分析用DBがあれば、そこを作業環境にする。** replicaでもサービスreadを受けている場合は専用の分析環境とは扱わない。
- 対象表・行が揃っていることと、データの反映時点を確認する。以降のSQLは選んだ環境で実行し、結果にはその環境と時点を記録する。
- 専用環境がなければ、サービスのSELECTも処理するread replicaを次の候補にする。DB担当者と通常負荷に対する余力・許容上限を確認し、閑散時間帯にCOUNTをまず1本だけ実行する。
- サービスのSELECTも処理するread replicaではサービス応答・CPU・I/O・複製遅延を監視し、上限超過で中止する。実行期限・監視・取消を用意できなければ、別の実行先を準備する。サービスへの影響がないことを保証する手順ではない。
- writer（primary/master）へのCOUNTはこの手順の代替先にしない。read replicaでも影響があることを理由に、writerへ切り替えない。
- 新しい専用接続を使い、途中で自動再接続しない。取消の対象照合が終わるまで接続を他用途へ返さない。

## 実行手順

### 1. TABLE_ROWSで件数一覧を取得する

実行期限と取消方法を用意し、対象DBだけを取得します。

```sql
SELECT TABLE_SCHEMA, TABLE_NAME, ENGINE, TABLE_ROWS,
       CURRENT_TIMESTAMP(6) AS observed_at
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'db'
  AND TABLE_TYPE = 'BASE TABLE'
ORDER BY TABLE_NAME;
```

- `ENGINE = InnoDB`の表を対象にする。MyISAMは[MyISAMの手順](mysql-myisam.md#実行手順)へ。
- 一覧に対象表が揃っているか確認する。NULL・取得失敗・一覧の欠落を0件にしない。

### 2. 概算でよければ終了する

- 一覧を見て、概算のまま使う表と正確に数える表を選ぶ。
- 概算でよい表は、表名・TABLE_ROWS・取得時刻を記録して終了する。取得時刻は統計の更新時刻ではない。
- 正確値が必要な表、値を取得できなかった表は次へ進む。概算0件も空表の証明にはしない。

### 3. COUNTの実行先を選ぶ

- 事前準備で選んだ実行先を確認する。専用replica・分析用DB・独立コピーを優先し、ない場合は上記の条件を確認したサービスのSELECTも処理するread replicaを使う。同じDBインスタンスの別schemaでは負荷を分けられない。
- 対象表・行がコピーから除外されていないことと、復元・更新反映の時点をDB担当者に確認する。
- CPU・ストレージなどの共有箇所と、障害切替でサービス用DBになる可能性を確認する。切替時にCOUNTを止められない実行先は使わない。
- 選んだDBへ新規専用接続を作る。以下はその接続だけの設定例。待ち時間・実行時間は運用上の上限に置き換え、クライアント側にも期限を設定する。

```sql
SET SESSION autocommit = 1;
SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED;
SET SESSION lock_wait_timeout = 3;       -- 例：メタデータロック待ち上限（秒）
SET SESSION max_execution_time = 10000; -- 例：SELECT実行上限（ミリ秒）
SELECT @@server_uuid AS server_uuid, @@hostname AS hostname,
       CONNECTION_ID() AS connection_id,
       @@session.transaction_isolation AS isolation_level;
```

返されたserver・接続IDを記録し、想定した実行先か確認します。新規コピーの作成は、この読取り手順とは別に作成時の負荷を確認して行います。

### 4. その実行先で、使用予定の索引を確認する

```sql
EXPLAIN FORMAT=TRADITIONAL
SELECT COUNT(*) FROM `db`.`table_name`;
```

- `key`に表示された索引名を記録する。`PRIMARY`なら主キーのクラスタ化索引。
- `partitions`に値があれば、次の手順ではそのpartitionごとの統計が必要。
- `EXPLAIN ANALYZE`は使わない。通常のEXPLAINはCOUNTを実行しないが、計画が実行時の物理読取りを完全に保証するわけではない。

### 5. 索引のページ数・統計更新時刻を確認する

非partition表では、`index_name`を手順4の索引名に置き換えます。

```sql
SELECT index_name, stat_name, stat_value, last_update
FROM mysql.innodb_index_stats
WHERE database_name = 'db'
  AND table_name = 'table_name'
  AND index_name = 'index_name'
  AND stat_name IN ('size', 'n_leaf_pages')
ORDER BY stat_name;
```

- `size`は索引全体、`n_leaf_pages`はリーフのページ数。`last_update`と併せて規模の目安にする。
- partition表ではこの表名一致SQLを流用せず、DB担当者が対象partitionに対応する統計行を確認する。欠落を0ページとして合計しない。
- 権限がない・統計がない場合は「ページ数未取得」と記録し、手順6で負荷を監視する。確認のためのANALYZE・索引作成・権限変更はしない。
- ページ数から安全性や所要時間を断定しない。キャッシュや更新状況で実際の読取り量は変わる。

### 6. 監視・中止条件を準備してCOUNTする

- CPU・I/O・サービス応答を監視する。replicaでは複製遅延・エラーも確認する。RDSでは`ReplicaLag`を使う。
- 上限値と実行期限を決める。別管理接続から対象queryを確認・取消できることを確認してから、1表ずつ実行する。
- 手順3と同じ専用接続で、次を実行する。`/* count_job_example */`は、実行中SQLの一覧で今回のCOUNTを見つけやすくするための任意のコメント。`count_job_example`は`count_20261109_01`など今回の識別名へ置き換える。MySQLの特別な機能ではなく、省略しても件数やロック方式は変わらない。取消対象はコメントだけで判断せず、下記の接続ID・ユーザー・DBも確認する。

```sql
SELECT /* count_job_example */ COUNT(*) AS exact_rows
FROM `db`.`table_name`;
```

上限超過・監視不能・接続先の役割変更時は、次の表の投入を止めます。別管理接続で、記録した接続ID・ユーザー・DB・実行中SQLが今回のCOUNTと一致することを確認します。

取消用の管理接続でも次を実行し、`server_uuid`が保存した値と一致することを確認します。不一致ならKILLせず、正しい接続先へ接続し直します。

```sql
SELECT @@server_uuid AS server_uuid;
SHOW FULL PROCESSLIST;
```

一致を確認した現在の接続IDで、次のどちらかを使います。`12345`は例です。古い接続IDをそのまま再利用しません。

```sql
-- 自前のMySQL
KILL QUERY 12345;
```

```sql
-- RDS for MySQL
CALL mysql.rds_kill_query(12345);
```

- 取消後はserver上でCOUNTが終わったことを確認し、専用接続を閉じる。クライアントのtimeoutだけで終了と判断しない。
- CPU・I/O・サービス応答・複製遅延の回復を確認する。終了が確認できなければ次表へ進まず、DB担当者に引き継ぐ。
- 成功した表名・件数・実行先・開始終了時刻・データの時点を記録する。コピーの件数を本番の現在値とは表示しない。

## 性能調査と補足

[性能・精度の実測](../table-count-calibration.md)／[運用上の詳しい確認](operations-runbook.md#3-mysql接続所有mdlundo複製)／[判断理由と用語](count-check-rationale.md)

以下は必要な疑問だけ参照してください。

## QA

### Q InnoDBが小さい二次索引でCOUNTするのは、どのバージョンからか

A **5.7系では5.7.18で、小さい二次索引を使う動作に戻されました。** 新規導入ではなく、5.7.2で入ったクラスタ化索引走査への変更を取り消したものです。これは変更履歴の説明で、5.7を新規採用する推奨ではありません。[5.7.18リリースノート](https://dev.mysql.com/doc/relnotes/mysql/5.7/en/news-5-7-18.html)

8.4の公式説明では、通常は最小の利用可能な二次索引、なければクラスタ化索引を走査します。hint・版・実行条件による違いがあるため、必ず同じ索引を使う保証ではありません。[COUNT仕様](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)

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

### Q 暗号化されたInnoDBでもTABLE_ROWSは使えるか

A **使えます。正常に鍵を利用できる稼働環境なら、暗号化を解除せず同じSQLで取得できます。** アプリ側で暗号化した列の値を復号する必要もありません。

```sql
SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_ROWS
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'db' AND TABLE_NAME = 'table_name';
```

暗号化の有無にかかわらず、InnoDBのTABLE_ROWSは概算で、キャッシュされた古い値が返る場合があります。復元先で鍵が欠けている場合まで取得を保証するものではなく、TABLE_ROWSが返っても実データを正常に読める証明にはなりません。[暗号化の仕様](https://dev.mysql.com/doc/refman/8.4/en/innodb-data-encryption.html)・[TABLE_ROWSの仕様](https://dev.mysql.com/doc/refman/8.4/en/information-schema-tables-table.html)

### Q 行ロックと共有ロックは何が違うか。COUNTでも共有ロックを取るか

A **行ロックは対象の範囲、共有ロックはロックの種類です。** InnoDBの行ロックは実際には索引レコードに掛かります。行への共有ロック（S）は他の共有ロックと共存しますが、その行の更新に必要な排他ロック（X）とは競合します。

READ COMMITTED／REPEATABLE READの通常の`SELECT COUNT(*)`は、この行の共有ロックを取りません。過去の行バージョンを利用して読み取るため、COUNTが読んでいる行へのUPDATE・DELETEを行ロックで待たせません。ただし、表定義を保護するメタデータロック（MDL）は別に取り、DDLと競合し得ます。[行ロックの仕様](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking.html)・[非ロック読取り](https://dev.mysql.com/doc/refman/8.4/en/innodb-consistent-read.html)・[MDL](https://dev.mysql.com/doc/refman/8.4/en/metadata-locking.html)

### Q ページを読んでいる間、そのページ内の全PKがロックされてwriteできなくなるか

A **いいえ。ページを読むことと、そのページ内の行をロックすることは別です。** READ COMMITTED／REPEATABLE READの通常COUNTは行ロックを取らず、別接続は同じページ内の行をUPDATE・DELETEできます。

「索引レコードをロックする」はUPDATEやlocking readなどの説明です。それらでも、同じページにあるという理由だけで全PKをロックするわけではありません。ロック範囲は検索・走査する索引レコードや条件によって変わり、レコード間の範囲を含むこともあります。通常COUNTでもCPU・I/Oの競合によってwriteが遅くなる可能性はあります。[非ロック読取り](https://dev.mysql.com/doc/refman/8.4/en/innodb-consistent-read.html)・[レコードと範囲のロック](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking.html)

### Q READ COMMITTEDでCOUNT中に別transactionがDELETEしてCOMMITしたらどうなるか

A **そのCOUNTは、文の読取り時点で見えていた行を数え続けます。** READ COMMITTEDでは通常SELECTごとに新しいsnapshotを使います。

ほかに更新がなく、最初に100行ある場合の例です。

| 順番 | COUNTする接続A | 削除する接続B |
|---|---|---|
| 1 | COUNTの読取り時点が決まる：100行が見える | |
| 2 | カウント中 | 1行をDELETEしてCOMMIT |
| 3 | **100を返す** | |
| 4 | 次のCOUNTは**99を返す** | |

削除された行をまだ読んでいなくても、必要ならundoから削除前の行バージョンを参照します。COUNT終了までDELETEのCOMMITを行ロックで待たせるわけではありません。削除のCOMMITがCOUNTの読取り時点より前なら、そのCOUNTは99を返します。結果はCOUNT終了時点の最新件数ではありません。

REPEATABLE READでは、同じtransaction内の次の通常SELECTも原則として最初のsnapshotを使うため、この表の手順4とは異なります。長いCOUNTは古い行バージョンの回収を遅らせる場合があり、行ロックがなくても負荷・容量の監視は必要です。[整合性読取りの仕様](https://dev.mysql.com/doc/refman/8.4/en/innodb-consistent-read.html)・[行バージョンと回収](https://dev.mysql.com/doc/refman/8.4/en/innodb-multi-versioning.html)

### Q InnoDBのCOUNTはロックしないか。FOR UPDATE・FOR SHARE・SERIALIZABLEならどうか

A **READ COMMITTED／REPEATABLE READの通常の`SELECT COUNT(*)`は行ロックを取りません。** MyISAMのような読取り用テーブルロックも使いません。ただし、表定義を保護するメタデータロック（MDL）は取り、`ALTER TABLE`などと競合する場合があります。CPU・I/Oの負荷も残ります。[整合性読取り](https://dev.mysql.com/doc/refman/8.4/en/innodb-consistent-read.html)・[MDL](https://dev.mysql.com/doc/refman/8.4/en/metadata-locking.html)

- **`FOR SHARE`付き**：読み取る索引レコードに共有ロックを取るlocking readです。
- **`FOR UPDATE`付き**：読み取る索引レコードに排他ロックを取るlocking readです。
- **`SERIALIZABLE`**：SQL末尾の句ではなく接続・transactionの分離レベルです。autocommit無効では通常SELECTも`FOR SHARE`相当になります。一方、autocommit有効でSELECT単独のtransactionなら、非ロックの整合性読取りが可能です。「SERIALIZABLEなら常に行ロック」とは限りません。

locking readのロック範囲は索引・検索条件・分離レベルで変わり、範囲のロックを含む場合があります。明示transactionではCOMMIT／ROLLBACKまで保持されます。このガイドでは**新規専用接続をREAD COMMITTED・autocommit=1にし、FOR UPDATE／FOR SHAREを付けない通常COUNTだけを使います**。SERIALIZABLEは採用しません。[locking read](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)・[分離レベル](https://dev.mysql.com/doc/refman/8.4/en/innodb-transaction-isolation-levels.html)

### Q MyISAMのCOUNTでテーブルロックを取らない方法はないか

A **MyISAMの`SELECT COUNT(*)`を、SQLの書き換えでテーブルロックなしにする方法はありません。** `LOCK TABLES`を書かなくても、MyISAMは読取りにテーブル単位のロックを使います。保持件数を使って速く返せることと、ロックを取らないことは別です。[MySQLのロック仕様](https://dev.mysql.com/doc/refman/8.4/en/internal-locking.html)

- **件数情報で足りる場合**：[一覧取得SQL](mysql.md#対象dbのengineと件数情報を確認する)を使います。ただし古いキャッシュ値の場合があり、metadata照会自体もロックゼロとは保証できません。
- **サービス側の表にCOUNTのロックを掛けたくない場合**：更新が入らない、サービスと資源を分けた別DBのコピーで`SELECT COUNT(*)`を実行します。コピー側のロックは元DBの表をロックしません。コピー作成時の負荷・ロックは別途確認し、RDSのMyISAM対応範囲は下のQ&Aに従います。
- **専用replicaで実行する場合**：サービスreadを分けても、同じ表への複製更新と競合する可能性は残ります。replicaに変えるだけで解決とはしません。

`concurrent_insert`は条件付きで読取りと追記を並行できる機能で、ロックをなくす設定ではありません。UPDATE・DELETEとの競合まで解消しないため、この件数取得手順では変更しません。[同時INSERTの仕様](https://dev.mysql.com/doc/refman/8.4/en/concurrent-inserts.html)

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


### Q InnoDBとは何か。普通のMySQLなら気にしなくてよいか

A InnoDBは、MySQLでテーブルの保存・索引・transaction・ロックを扱う仕組み（storage engine）です。MySQL 8.4では標準設定なので、engineを指定せず作成した一般的なテーブルはInnoDBになります。ただし設定変更や既存テーブル、他engineもあるため、「みんなInnoDB」とは断定しません。普及率はこの調査では確認していません。[MySQL公式の標準設定](https://dev.mysql.com/doc/refman/8.4/en/storage-engine-setting.html)

このガイドで確認する理由は、通常のSELECT COUNT(*)が行ロックを取らないという説明の対象をInnoDBへ限定するためです。InnoDBでもI/O・CPU・MDL等の影響はあります。手順1の件数一覧SQLのENGINE欄がInnoDBか確認します。default_storage_engineだけでは既存の対象表のengineを証明できません。違う場合はengineを変更せず、該当engineのマニュアルへ進みます。SQL ServerにはこのInnoDB確認はありません。


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

専用環境がない場合は、事前準備の条件を満たすサービスのSELECTも処理するread replicaを次の候補にします。閑散時間帯にまず1本ずつ実行し、サービス応答・CPU・I/O・複製遅延を監視して上限超過で取消します。無影響の保証ではなく、条件を確認できなければ別の実行先を準備します。

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

A 元表との対応が証明できれば使えます。ETLで重複排除、削除行除外、履歴追加、tenant限定、joinによる増幅があると同じ件数ではありません。元表世代、変換規則、削除の反映、最終取込点、欠落・再送の扱いを確認します。集計表ならその行数をCOUNTするのではなく、元表との対応・取得時点・精度を確認できる件数指標を読みます。対応を確認できない場合は分析側の件数と表示し、元表と対応する取得先を準備します。

### Q 実行するCOUNTのSQLは

A [手順3の専用接続準備](#3-countの実行先を選ぶ)と[手順6の監視・取消準備](#6-監視中止条件を準備してcountする)を済ませた同じ接続で、次の1文を使います。

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
