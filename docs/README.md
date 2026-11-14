# 件数取得ドキュメント

現行の手順は次の3本です。各ファイルに実行手順・SQL・Q&Aをまとめています。

| 対象 | ガイド |
|---|---|
| MySQL InnoDB | [件数取得の手順とQ&A](guidelines/mysql-innodb.md) |
| MySQL MyISAM | [件数取得の手順とQ&A](guidelines/mysql-myisam.md) |
| SQL Server | [件数取得の手順とQ&A](guidelines/sql-server.md) |

## 計測結果とテーブル定義

| 対象 | 比較一覧・定義SQL | 全標本・環境・制約 |
|---|---|---|
| MySQL：1億行・100列 | [比較一覧](mysql-engine-count-100m-tables.md) | [詳細結果](mysql-engine-count-100m.md) |
| SQL Server：1億行・100列 | [比較一覧](sqlserver-manual-count-100m-tables.md) | [詳細結果](sqlserver-manual-count-100m.md) |

SQL ServerはRosetta上の参考値で、本番性能やMySQLとの製品順位には使いません。

## 必要な場合に読む補足

- [監視・取消・終了／回復確認と、その理由](guidelines/operations-runbook.md)
- [MySQLの統計誤差・最大／最小の観測と校正](mysql-count-accuracy.md)
- [整数主キーによる条件付き標本推定](probability-count.md)
- [検証環境の再現・開発](benchmark-development.md)

<a id="mysql-engine"></a>

## MySQL ENGINE

[ENGINE確認のSQLと振り分け](guidelines/mysql.md)を参照してください。

旧ガイド・重複資料・途中の修正メモは現行の導線から外しました。[整理前の原本とハッシュ](../results/reference/documentation-before-cleanup-84013c9/manifest.json)を証跡として保持しています。過去の査読のパス・ハッシュはその時点の文書に対応します。過去の固定ワークフローを再実行する場合は、対象commitのcheckoutを使用してください。
