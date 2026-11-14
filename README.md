# 大規模テーブルの全件数取得ガイド（MySQL / SQL Server）

巨大なテーブルの件数を、サービスへの影響を抑えて取得するためのガイドです。対象は業務上のWHEREなしの全件数です。

- [MySQL InnoDB：実行手順・SQL・Q&A](docs/guidelines/mysql-innodb.md)
- [MySQL MyISAM：実行手順・SQL・Q&A](docs/guidelines/mysql-myisam.md)
- [MySQL：対象表のENGINE確認](docs/README.md#mysql-engine)
- [SQL Server：実行手順・SQL・Q&A](docs/guidelines/sql-server.md)
- [詳細な運用手順：監視・取消・終了と回復の確認](docs/guidelines/operations-runbook.md)
- [最新の計測結果・テーブル定義・補足資料](docs/README.md)

InnoDBとSQL Serverでは概算の件数一覧を確認し、必要な表だけ正確に数えます。MyISAMは保持件数を使うCOUNTから進めます。サービスのクエリを受けない実行先を優先し、監視・取消・終了確認を用意します。replicaであることだけでは無影響を保証しません。

ガイドを読むために検証環境を起動する必要はありません。調査を再現する開発者向けの手順は[ベンチマークの再現・開発](docs/benchmark-development.md)と[SQL Server検証環境](sqlserver/README.md)にあります。
