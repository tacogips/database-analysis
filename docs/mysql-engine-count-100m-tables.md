# 1億行・100列COUNT：比較一覧とテーブル定義

全12回が100,000,000件。各条件3回、DBバッファを毎回リセットし、メタデータ確認後のCOUNT応答時間を測定した。OS・VM・Mac側のキャッシュは未制御。

| 条件 | COUNT中央値 | 保存列数 | 定義SQL |
|---|---:|---:|---|
| InnoDB・PKのみ | 273.707秒 | 100 | [innodb-primary-only.sql](mysql-engine-count-100m-tables/innodb-primary-only.sql) |
| InnoDB・単一列副索引あり | 6.263秒 | 100 | [innodb-secondary.sql](mysql-engine-count-100m-tables/innodb-secondary.sql) |
| InnoDB・複合副索引あり | 8.012秒 | 100 | [innodb-composite.sql](mysql-engine-count-100m-tables/innodb-composite.sql) |
| MyISAM・PKのみ | 0.624ms | 100 | [myisam-primary-only.sql](mysql-engine-count-100m-tables/myisam-primary-only.sql) |

各SQLは計測直前の構造を確認するための独立した定義。実際のfixtureは[lab.rb](../mysql/engine-count-100m-cold/lab.rb)で生成し、PKのみ→単一列副索引→複合副索引の順に変更した。参照SQLは計測runnerの実行入力には含まれない。

列はid BIGINT UNSIGNEDとc001〜c099 INT NOT NULL。PKはid、単一列副索引はk_count(c001)、複合副索引はk_count_v2(c001,c002,c003)。値の生成式・実行計画・全標本・Mac仕様は[詳細結果](mysql-engine-count-100m.md)を参照。

InnoDBのTABLE_ROWSは87,972,570件（誤差−12.027430%）、取得中央値は条件順に0.962／0.924／0.909ms。MyISAMは100,000,000件、中央値1.031msだった。MyISAMは静止カタログの比較で、暗黙の表read lockがあり、本番の非ロックCOUNT候補にはしない。
