# AGENTS.md

## SQL Server追加スコープ（2026-11-08ユーザー指定）

- 最新ユーザー依頼により、SQL Serverのコンテナ検証と日本語ガイドラインも対象に追加する。以下の「MySQL以外は対象外」は既存MySQL成果物についての範囲であり、この追加依頼を除外しない。
- SQL Serverの現行ガイドは `docs/guidelines/sql-server.md`。検証ソース・結果・過去の査読記録は `sqlserver/`、`results/sqlserver/`、`.riela/workflows/` に保持する。過去の証跡・受入hashは書き換えず、文書整理前の原本は `results/reference/documentation-before-cleanup-84013c9/` に保持する。
- SQL Serverの検証接続先はこのリポジトリの専用コンテナだけ。実DBに接続しない。データ・鍵はコミットせず、結果JSONと再生成スクリプトを保存する。
- MySQLの「通常SELECTは行ロックを取らない」をSQL Serverへ転用しない。既定のlocking READ COMMITTED、既存RCSI、既存SNAPSHOTを区別する。COUNT候補は行versioningを満たす実行先に限定し、NOLOCK/READPAST/意図的競合注入や全表ロックを採用しない。Sch-S等が残ることを明記する。
- 少数の判断に必要な検証に絞り、実験完了・精度・負荷・終了確認を別々に判定する。エミュレーションの場合は非サポート構成であることを明記し、本番性能へ外挿しない。
- 執筆・実装のauthor工程はDB/Docker/計測を起動しない。所有者が専用runnerを実行し、根拠・source・後始末を確認した後、別セッションのGPT-6.1 Sol 2役で敵対的レビューする。

## 目的

巨大な MySQL テーブルの**全件カウント(業務上の WHERE なし)を、サービス性能を落とさず、テーブルをロックせず、できるだけ速く取得する方法**を明らかにし、複数の巨大データサービスを運用する開発者向けのガイドラインにする。

対象外: 行データの取得、条件付きカウント、MySQL 以外の製品、サポートが切れた MySQL。

## 成果物

- 現行ガイド: `docs/guidelines/mysql-innodb.md`、`docs/guidelines/mysql-myisam.md`、`docs/guidelines/sql-server.md`(日本語)。入口は `docs/README.md`。
- 検証環境: `compose.yaml` と `bench/`。`mise run all` で白紙の状態から再現する
- 敵対的レビュー: `.riela/workflows/table-count-guideline-review/`(GPT-6.1 Solのみ。執筆と査読は別セッション)

## エージェントの規則

- `bench/` とそのスクリプトを接続してよいのは、この compose の検証環境だけ。**実データベースに接続しない。**
- テーブルのデータはコミットしない。スクリプトから再生成できるようにする。計測結果(`results/*.json`)は根拠としてコミットする。
- 数値は `results/` のファイルに基づいて書き、計測していないことは未計測と明記する。絶対値は環境に依存するので、方法どうしの比較として扱う。
- 本番を想定した助言では、次を必ず区別する。
  - 通常の `SELECT COUNT(*)`: 行ロックを取らない。
  - `INSERT ... SELECT COUNT(*)` などの書き方: 読み取り元の行をロックする場合がある。
- サポート対象のバージョンは定期的に確認し、`lab.rb` の `MATRIX` と `mysql-images.json` のdigestを更新する。
- 未初期化統計・取得不可を0件と扱わない。参考統計、精度を検証した推定、正確値を区別する。
- 実験の完了、精度達成、負荷予算、後始末を別々に判定する。未完了リクエストを落としてSLO合格にしない。
- 引用する過去観測は `results/reference/` へハッシュ付きで保持し、新規実行と区別する。終了コード不明の観測を実行成功と扱わない。

## 最新ユーザー指定の非ロックスコープ

- 既定`mise run all`はCOUNT候補として通常のInnoDB SELECT、参考統計、条件付き標本だけを使う。`bench/nonlocking-scope.json`を現行の予定範囲・除外台帳とする。
- `FOR SHARE`/`FOR UPDATE`、COUNTを埋め込むDML/CTAS/SET、`LOCK TABLES`/FTWRL、SERIALIZABLEのCOUNT、意図的なpending-DDL・FLUSH・ANALYZE競合注入を既定経路へ戻さない。過去の必須要求よりこの指定を優先する。除外は成功測定ではない。
- 通常SELECTにもMDLはある。fixtureのCREATE/INSERT/ANALYZE/DROPと業務負荷のUPDATE/INSERT/DELETEの自然なロックは別であり、正常業務負荷は維持する。非InnoDBは静止カタログに限定し、サービス影響候補にしない。
- `bench/nonlocking_policy.py`はSQL送信前の追加ガード。意図的な競合注入の除去はcallerを監査し、語句検索やこのガードだけで証明しない。無効化した`.py.retired`は実行用ではない。
- 非ロックスコープ修正後も固定runnerの白紙all、所有者の意味・最終source・後始末監査と最終Sol2役が必要。author工程ではDB/Docker/計測を起動しない。
