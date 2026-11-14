# 大規模テーブルの全件数取得ガイドライン（MySQL / SQL Server）

「データ量」は業務上のWHEREなしのROW COUNTです。容量bytes、アプリ有効件数、soft-delete除外件数は別指標として扱います。
最初に必要精度・鮮度・サービス予算を決め、既存の承認済みexport・参考metadataを優先します。正確値は事前適格化した資源隔離先で取得します。

## 運用時の選定ガイド

- [MySQL：大規模テーブルの全件数取得ガイドライン](guidelines/mysql.md)
- [SQL Server：大規模テーブルの全件数取得ガイドライン](guidelines/sql-server.md)
- [共通運用runbook：記入例・権限付き観測SQL・停止／回復](guidelines/operations-runbook.md)

通常の物理表の共通経路、状態の組合せ、権限・暗号化・特殊形式、取得不能時の戻り先、SQL例、停止・終了・回復確認を各製品のガイドにまとめています。
未知値を0にせず、参考値・正確snapshot・鮮度を分けます。別時点の表／シャードの合計を全体の現在正確値とは表示しません。
利用のために検証labやRielaを実行する必要はありません。SQL例は所有者予算・driver期限・監視／取消経路を用意してから利用してください。

## 凍結した根拠・詳細・再現資料

- MySQL：[旧詳細ガイド](table-count-guideline.md)、[校正・精度と費用の観測](table-count-calibration.md)、[条件付き標本prototype](probability-count.md)
- MySQL：[所有者の実行証明](../results/reviews/final-all-proof.json)、[固定観測snapshot](../results/reference/review-20261107T155750Z-26743/manifest.json)、[所有者監査bundle](../results/reference/owner-acceptance-20261107T161659Z/manifest.json)、[旧文書受入](../results/reviews/final-operational-review-acceptance.json)
- SQL Server：[旧詳細ガイド](../results/reference/final-sqlserver-52556dd/docs/sqlserver-table-count-guideline.md)、[所有者proof](../results/reference/final-sqlserver-52556dd/results/sqlserver/owner-proof.json)、[実測JSON](../results/reference/final-sqlserver-52556dd/results/sqlserver/runs/20261107T230105Z-8533a31e/result.json)、[owner audit](../results/reference/final-sqlserver-52556dd/results/sqlserver/owner-audit.json)、[旧文書受入](../results/reference/final-sqlserver-52556dd/results/sqlserver/reviews/final-acceptance.json)
- 所有者向け再現：[MySQLの既存手順](../README.md)、[SQL Serverの専用runner手順](../sqlserver/README.md)。ベンチマークは専用検証環境だけに接続し、実DBへ向けません。

実験完了・精度・サービス予算・後始末は別判定です。既存proofや旧文書受入は、新しい選定ガイドの最終査読完了を意味しません。
現行ガイド・runbook・Issue 1実装の受入可否は[現行受入記録](../results/reviews/issue-1/acceptance.json)の対象hashを照合します。[修正前アーカイブ](../results/reference/issue-1-pre-fix-c6396cc/manifest.json)に保存された旧受入はアーカイブhashだけに適用し、現行版へ転用しません。所有者の終了code・完全log・意味・最終source・後始末監査と別セッションGPT-6.1 Sol 2役の最終査読が必要です。最終査読後の変更hashは再査読します。
[Issue 1専用runner](../sqlserver/issue1/README.md)は小fixtureの識別・取消・統計条件だけの機能検証です。エミュレーション上の機能確認や架空runbook例から、本番無劣化・native性能・SLO合格は導けません。

SQL Serverの引用証跡は[固定snapshot](../results/reference/final-sqlserver-52556dd/manifest.json)にも保存しています。MySQLの再現時のresetに影響されず、同じ根拠を参照できます。snapshotは読取用で、再現は現行runner手順に従います。
