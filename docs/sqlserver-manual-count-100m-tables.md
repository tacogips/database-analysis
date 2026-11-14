# SQL Server：1億行・100列COUNTの比較一覧とテーブル定義

全18回が100,000,000件。各条件3回のCOUNT応答時間の中央値を、速い順に示す。単位はすべて秒。DBデータバッファは毎回リセットし、OS・VM・Mac側のキャッシュは未制御。

| 条件 | COUNT中央値（秒） | 保存列数 | 定義SQL |
|---|---:|---:|---|
| D：NOLOCK・複合副索引（観測値） | 1.296 | 100 | [composite.sql](sqlserver-manual-count-100m-tables/composite.sql) |
| C：SNAPSHOT・複合副索引 | 1.998 | 100 | [composite.sql](sqlserver-manual-count-100m-tables/composite.sql) |
| B：RCSI・単一列副索引 | 2.105 | 100 | [secondary.sql](sqlserver-manual-count-100m-tables/secondary.sql) |
| B：RCSI・複合副索引 | 2.855 | 100 | [composite.sql](sqlserver-manual-count-100m-tables/composite.sql) |
| A：AG読取りセカンダリ・複合副索引 | 4.615 | 100 | [composite.sql](sqlserver-manual-count-100m-tables/composite.sql) |
| B：RCSI・PKのみ | 98.158 | 100 | [primary-only.sql](sqlserver-manual-count-100m-tables/primary-only.sql) |

**今回のCOUNT最速はNOLOCK＋複合副索引の1.296秒。正確値として扱うA／B／Cの最短は、SNAPSHOT＋複合副索引の1.998秒だった。** NOLOCKは静止fixtureで一致した観測値であり、更新中の正確な件数を保証しない。速さだけを理由に正確値の取得方法として選ばない。

列はid BIGINT NOT NULLとc001〜c099 INT NOT NULL。PKはid、単一列副索引はk_count(c001)、複合副索引はk_count_v2(c001,c002,c003)。A／B／C／Dの状態比較には同じ複合副索引を使った。各SQLは計測直前の構造を確認する独立した参照定義で、実際のfixture生成・索引変更・DB状態の確認は[Fixture.cs](../sqlserver/manual-count-100m/Fixture.cs)と[runner](../sqlserver/manual-count-100m/README.md)を参照。参照SQLはrunnerの実行入力には含まれない。

SQL Server 2022（16.0.4305.5）をM4 MacのRosettaで動かした非サポート構成の参考値。固定の実行順・索引・キャッシュ・稼働ノード数等の影響があるため、状態だけによる速度差や本番の最速手法を保証しない。サービス負荷予算は未計測。

[全標本・状態別SQL・実行計画・索引容量・Mac仕様・制約・終了と後始末](sqlserver-manual-count-100m.md)／[生の計測結果](../results/sqlserver/manual-count-100m/runs/20261108T234303Z-7e3f51b7e5a312af1e695029bd48dcb5/result.json)。COUNTとSQL両ノード・クライアントは正常終了0。runner全体の終了1（直後の容量回復確認）と、後続の同じ基準での容量回復確認は詳細結果で区別している。
