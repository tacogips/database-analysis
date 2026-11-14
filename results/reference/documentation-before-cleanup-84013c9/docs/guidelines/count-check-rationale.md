# 件数取得：確認する理由と用語

実行手順は[InnoDB](mysql-innodb.md#実行手順)・[MyISAM](mysql-myisam.md#実行手順)・[SQL Server](sql-server.md#実行手順)を参照してください。このファイルは判断理由を調べたい場合の補足です。

## コピーしたデータを確認する理由

コピー先でCOUNTが成功しても、コピーから除外された行や、コピー先だけで追加・削除した行があれば元表の件数にはなりません。初期コピーとその後の変更記録で、対象表が欠けずに複製されていることを確認します。これを詳細手順では「データ同等性」と呼びます。

現在のSQLの結果だけでは、過去の欠落や独自変更を証明できません。[DB担当者向けの詳細確認](operations-runbook.md#コピー元とコピー先の確認)を使い、本番の全表走査を確認手段にしません。

## 複製位置と件数の時点

GTIDはMySQLで複製するトランザクションの識別子です。行数ではありません。複製対象から除外された更新にも識別子が記録されるため、GTIDの一致だけではコピー内容の一致を証明できません。[MySQL公式](https://dev.mysql.com/doc/refman/8.4/en/replication-gtids-lifecycle.html)

複製遅延が0でも、COUNTが読む時点と本番の現在時点が同じとは限りません。複数表のCOUNTを別々に実行すると、その間の更新も入るため、合計を同時点の件数にはできません。全表を同じ時点で数える用途では、その時点のデータを復元した別DBを使います。

## COUNTのSQLを変えない理由

`COUNT(*)`はNULLを含む行も数えます。`COUNT(列)`はその列がNULLの行を除外するため代用できません。InnoDBの通常SELECTは行ロックを取りませんが、表定義を保護するmetadata lock（MDL）は残り、DDLと競合し得ます。[COUNT](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html#function_count)・[MDL](https://dev.mysql.com/doc/refman/8.4/en/metadata-locking.html)

SQL Serverでは通常のREAD COMMITTEDが読取りロックを使う構成もあります。RCSI（文単位の行versioning）・SNAPSHOT（transaction単位の行versioning）などを優先します。使えない場合、未COMMIT・重複・数え漏れを許容する件数には[SQL Server手順6のSQL D](sql-server.md#6-監視中止条件を準備してcountする)でNOLOCKを使います。NOLOCKの結果は開始時点の正確値ではありません。どの方式でもスキーマロックとCPU・I/O負荷は残ります。[SQL Server公式](https://learn.microsoft.com/en-us/sql/t-sql/queries/hints-transact-sql-table#readuncommitted)

## 概算件数が少なくても実行先を確認する理由

InnoDBのTABLE_ROWSは過小にもずれます。また、件数が同じでも索引・キャッシュ・同時実行負荷でCOUNTの費用は変わります。そのため「何件以下ならサービス用DBで安全」という一律の基準にはしません。概算値を見てCOUNT対象を絞り、選んだ表だけをサービスと負荷を分けたDBで数えます。

## SQL ServerのCOUNTとロック

[COUNTの走査・集計](sql-server.md#q-countはどうやって数えるか)、[READ COMMITTEDの解放時点](sql-server.md#q-locking-read-committedではcount終了まで表全体がロックされるのか)をQ&Aにまとめています。
