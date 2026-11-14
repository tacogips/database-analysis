# 大規模テーブルの全件数取得ガイドライン（MySQL / SQL Server）


[全件数取得のQ&Aと精度の観測方法](guidelines/count-qa.md)

「データ量」は業務上のWHEREなしのROW COUNTです。容量bytes、アプリ有効件数、soft-delete除外件数は別指標として扱います。
MySQLはInnoDB・MyISAM別の手順とSQLを参照してください。InnoDBは概算一覧を確認してから必要な表だけCOUNTし、MyISAMは確認済みなら保持件数をCOUNTで取得します。

## 運用時の選定ガイド

- [MySQL：大規模テーブルの全件数取得ガイドライン](guidelines/mysql.md)
- [SQL Server：大規模テーブルの全件数取得ガイドライン](guidelines/sql-server.md)
- [共通運用runbook：記入例・権限付き観測SQL・停止／回復](guidelines/operations-runbook.md)

通常の物理表の共通経路、状態の組合せ、権限・暗号化・特殊形式、取得不能時の戻り先、SQL例、停止・終了・回復確認を各製品のガイドにまとめています。
未知値を0にせず、コピーのデータ同等性・正確snapshot・鮮度・資源を別判定にします。HA所有者によるrole・routing確認は継続条件で、昇格・変更・情報の期限切れで確認結果が無効になります。具体的な停止・取消・実行条件の再確認はrunbookに従います。別時点の表／シャードの合計を全体の現在正確値とは表示しません。
ガイドを読むために検証環境を起動する必要はありません。SQL例は所有者予算・driver期限・監視／取消経路を用意してから利用してください。


## 性能・精度の調査資料

- MySQL：[精度と費用の比較](table-count-calibration.md)、[詳細な検証条件と結果](table-count-guideline.md)、[COUNTと統計取得の実測JSON](../results/reference/review-20261107T155750Z-26743/results/requested_accuracy_cost_frontier.json)
- SQL Server：[検証条件と結果](../results/reference/final-sqlserver-52556dd/docs/sqlserver-table-count-guideline.md)、[実測JSON](../results/reference/final-sqlserver-52556dd/results/sqlserver/runs/20261107T230105Z-8533a31e/result.json)
- 再現・開発：[MySQL](benchmark-development.md)、[SQL Server](../sqlserver/README.md)。検証スクリプトは専用環境だけに接続します。

数値は各検証環境での観測です。本番の性能や誤差上限を保証するものではありません。SQL Serverのコンテナ検証はARM上のamd64エミュレーションで、本番性能への外挿はできません。

- [Appendix：大規模サービスの件数表示・集計の公開事例](appendix/count-in-large-services.md)（全表COUNTの性能検証とは別）
