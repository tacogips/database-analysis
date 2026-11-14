# 巨大MySQLテーブルの件数取得ガイド

まず[実務ガイドライン](docs/table-count-guideline.md)を参照してください。既存統計から始め、精度・鮮度・資源分離に応じて取得方法を選ぶSQLと判断フローです。[少数の代表表による校正](docs/table-count-calibration.md)には、既存観測と適用・棄却条件をまとめています。利用者がガイドを読むために`mise run all`を実行する必要はありません。

以下は根拠を再現・追加検証する開発者向けの任意のlabです。長時間と大きな保存容量を要します。**bench/とスクリプトの接続先はこのcompose検証環境だけです。実データベースには接続しないでください。**

縮小した固定runnerの白紙allは終了コード0で完了し、[所有者proof](results/reviews/final-all-proof.json)で意味・最終source・初期/最終整合・後始末を監査済みです。[固定snapshot](results/reference/review-20261107T155750Z-26743/manifest.json)と[固定監査bundle](results/reference/owner-acceptance-20261107T161659Z/manifest.json)を根拠とします。精度未達・時間上限を残し、サービスSLOや全条件最速は認定していません。benchmarkの実行証明だけでは文書を受け入れません。文書の受入れは、執筆とは別セッションで完了した最終Sol 2役それぞれのレビューが最終文書のSHA-256と所有者proofに一致し、[文書受入記録](results/reviews/final-operational-review-acceptance.json)に受入れが記録されることを条件とします。

現行の入口は上の実務ガイドと校正です。旧網羅grid・修正計画・過去レビューは履歴で、[非ロックスコープ](docs/table-count-nonlocking-scope.md)を超える調査の再開を利用者に要求しません。除外は成功測定や査読閉鎖ではありません。

## 検証環境の再現手順（任意・長時間）

Docker Compose と [mise](https://mise.jdx.dev/) が必要です。MySQLの参照digestは `mysql-images.json` に固定し、実際のイメージ識別子と終了コードをstatusへ記録します。起動時に全5版の固定イメージを準備し、benchのbuild後、計測前にDocker VM内2 GiB・ホスト40 GiBの空きを確認します。これは選定した予備容量で、実測した最小要件ではありません。容量不足と保守の過去経緯は`results/reviews/build-cache-cleanup-evidence.json`に保持しています。自動pruneはしません。DBデータはホストbind mountです。

```bash
mise install       # mise.toml で固定した Ruby を導入
mise run all       # 初期化 → 投入 → 件数検証 → 重要条件に絞った非ロックスコープ → results/summary.md
```

個別に実行する場合:

```bash
mise run prepare                   # 初期化から投入、件数検証まで
mise run measure                   # 投入済みデータで現行allowlistを実行
mise run experiment count_default  # 選択済みの基本比較を1件再実行（再投入しない）
mise exec -- ruby lab.rb requested 93-metadata-accuracy 94-metadata-on-replica # 指定した追加実験のみ
docker exec lab-bench python3 /work/bench/experiments.py list   # 実験の一覧
```

`mise run reset` はこのプロジェクトのコンテナ、`.data`、生成した計測結果を削除します。文書が参照する過去の根拠 `results/reference/` とレビュー記録 `results/reviews/` は保持します。投入・計測はこれらを再利用せず、白紙のDBから再生成します。

追加実験は、成功した終了コード・入力fingerprint・成果物ハッシュが一致する場合だけ結果を再利用します。静的importを再帰的に追い、共通Pythonコード・設定・イメージの変更、欠けた成果物、失敗したstatusは再試験します。再利用した結果も全体の合否へ含めます。指定した実験を強制再試験する場合は、該当する `results/requested/<id>.status.json` を削除してください。計測の競合を避けるため、実験とレビューの `lab-runner` は同時に実行しません。バージョン比較用MySQLの一時データは `.data/lab-matrix-*` に置き、コンテナ削除後に消します。Docker VM内の匿名ボリュームとは別に、ホスト側の空き容量を使います。

`mise run all` の追加実験には合計時間の制限を設けません。各スクリプトの上限は20分です。Rielaの固定 `lab-runner` も白紙から同じallを実行し、ソースの前後ハッシュ、終了コード、ログを `results/reviews/final-all-candidate.json` と同ディレクトリのログへ記録します。ワークフローの測定工程には12時間の上限があります。候補記録だけでは合格にせず、必要な実験の完了と結果の意味・最終source不変性・後始末を所有者が確認し、文書だけの統合後に最終査読へ進みます。今回のproofはこのbenchmark範囲の確認であり、統合後の文書の受入れではありません。実験の失敗・スキップやデータ検証失敗は成功扱いにしません。

## 構成

- `lab.rb`: 実行手順(Ruby)。起動後にスケジューラ・時間予算・依存関係・KILL対象・結果キャッシュのセルフチェックを実施。`mise.toml` の全タスクが`mise exec -- ruby`で固定したRuby 4.0.9を呼び出す
- `compose.yaml`: primary、replica(`t_repl` のみ複製)、bench(計測クライアント)
- `sql/01_schema.sql`: 検証するテーブル形式
- `bench/`: Compose専用。保存された`count_preflight.py`の`COUNT_WITH_LIMIT_THEN_SPLIT`等は静止labの研究診断で、primary実行許可や現ガイドの代用ではない。投入(`load.py`)、件数検証(`verify_data.py`)、事前診断(`count_preflight.py`)、テーブル設計別のカウント(`count_shapes.py`)、ロック要因(`lock_factors.py`)、影響計測(`experiments.py`)、バージョン比較(`matrix_probe.py`)、追加実験(`requested/`)
- `.riela/workflows/table-count-guideline-review/`: 敵対的レビューのワークフロー

データは決定的に生成します。負荷の乱数はシード固定(`LAB_SEED`、既定 42)ですが、遅延の絶対値は実行環境と実行ごとに変わります。

数値を引用した履歴は `results/reference/20261107-observations/manifest.json` に保存しています。旧負荷生成器には未着手リクエストの集計漏れがあり、過去の飽和時の裾・未完了率からSLO合格を判定しません。修正版は予定・開始・完了・失敗・実行中・未着手を保存し、未完了を含む遅延分布は打切り観測として表示します。

## 任意の旧scale/write-mix研究用の生計装データ

PRIMARY-scaleとpartitioned-v3 write-mixの各計測では収集・窓集計を終えてから、サービス要求とserver計装の全標本を`results/raw/<結果名>/<stream>/*.jsonl.gz`へ保存します。テーブルの行データは含みません。標準gzip内は1行1標本のJSONLで、1chunkの非圧縮サイズ上限は8 MiBです。メインJSONの`sections.windows.raw_service_samples`（streamごとの配列）と`raw_server_samples`は、標本総数・chunk件数・圧縮前後のSHA256とサイズ・パスを保持します。要約と予定数保存則はメインJSONに残ります。圧縮は計測終了後の表現変更で、標本を間引きません。

```bash
# 任意のPRIMARY-scale結果を指定。全chunkのサイズ・SHA・件数を検証
python3 bench/raw_evidence.py results/requested_0g-EXP-O10-PRIMARY-SCALE-indexed-10pct.json
# 保存snapshotを検証するときは、その中のresultsを--results-rootへ渡す
python3 bench/raw_evidence.py path/to/snapshot/results/requested_example.json --results-root path/to/snapshot/results
# --emitで全標本をstream番号付きJSONLとして読み出せる
```

chunkはメインJSONと一緒に保持します。削除・変更するとrequestedの再利用判定は失敗します。固定runnerのsnapshotにも全chunkを含めます。`bench/test_raw_evidence.py`、`test_runner_cache.rb`、`test_white_all_runner.rb`が、DBを使わず往復・境界・破損・欠損・保存経路を確認します。

Rielaの起動はPOSIX wrapper → `mise exec -- ruby` → controller → `mise run all` → 明示`mise exec -- ruby lab.rb all`です。controllerとlabはRubyの版・実体を完全ログへ残します。前後の`last_run.json`署名が変わらなければ`not_reached`、`ran=[]`として旧結果を今回へ加算しません。元のsummaryはCLI lock取得前に移動・削除しません。早期失敗時のsnapshotには以前の結果が混在し得るため、収集だけを実行証拠にしません。前回のcandidate/logは次試行前にreviewsへ複写し、最終proofは自動作成しません。

write-mixは既定allの対象外です。明示IDで任意に実行する場合の全機会は`bench/requested/write-mix-plan.json`に保持します。失敗・後始末未確認なら後続scriptと最終COUNT検証を開始せず、`last_run.json.not_run`に未着手を残します。`lab_data_intact=false`はその最終検証に未到達の場合も含み、破損の認定ではありません。分類・分割・停止確認の仕様は[修正説明](docs/table-count-protocol-repair.md)を参照してください。

## 現行の非ロックスコープ

最新ユーザー指定により、既定の`all`と`measure`は[範囲・除外台帳](bench/nonlocking-scope.json)の明示allowlistを使います。基本impactは9件（baseline、baseline_end、count_default、count_estimate、count_force_primary、count_force_secondary、count_on_replica_t_repl、replica_control_no_count、count_timeout_hint）、requestedは次の7スクリプト・版別9プロセスです。

- `00-budget-start`
- `01a-PROB-WIDE-50`
- `01c-ACCURACY-COST`
- `01d-ACCURACY-SERVICE-100`
- `91-encrypted-count`（8.4.13 / 9.7.4 / 26.10.0）
- `93-metadata-accuracy`
- `94-metadata-on-replica`

100Mの基本fixture、初期・最終の整合確認、静止58設計、通常COUNTのRC/RR 12ケース、固定digestのmatrix 5版、通常業務負荷、未完了到着の会計、取消・後始末確認は維持します。基本impactの残り13件とrequestedの残り137スクリプト・149プロセスは`skipped_by_user_scope`として全件を台帳に保持し、不足や成功に数えません。全実験のregistryと版・outputsは保持しており、任意の既存IDを`lab.rb requested <id>...`、基本impact名を`lab.rb experiment <name>...`で明示実行できます。不明requested IDは実行前に失敗します。旧予定は[revision23台帳](bench/nonlocking-scope-v23.json)へハッシュ付き参照で保存しました。

意図的な行・表・globalロック取得とpending-DDL/FLUSH/ANALYZEの競合注入の除外も継続します。対象外6スクリプトの`.py.retired`はimport前に停止する履歴ソースです。通常SELECTのMDL、fixture保守と業務負荷の自然なロックは残ります。非InnoDBは静止カタログだけで、サービス影響候補ではありません。50/100列の重複grid、write-mix・primary-scale等の履歴計画は任意範囲であり、既定の完了条件ではありません。

`test_nonlocking_scope.py`と`test_nonlocking_runner.rb`はDBなしでSQL送信ガード、全registryと選択・スキップ台帳の分割、実際の既定選択・dispatcher、版・暗号化・outputs、不明IDの拒否を確認します。`test_scope_audit.py`は所有者監査が範囲外を不足や成功へ数えず、scope由来とcached成果物の不一致を報告することを確認します。ガードだけでcallerの安全性は証明しません。縮小後の固定runner白紙allと所有者の意味・最終source・後始末監査は上記proofの範囲で完了しています。旧exit130や初回縮小実行の壁時計窓却下は履歴のまま残し、今回の成功へ昇格しません。文書の受入れには、上記の[文書受入記録](results/reviews/final-operational-review-acceptance.json)の条件を満たす最終Sol 2役のレビューが必要です。

時計修正後の計測は `lab-monotonic-v2` を使います。窓・到着・server標本は単調時計で統一し、壁時計は来歴アンカーだけです。旧データの数値や時刻は修正しません。11列rawの定義、保存境界、旧形式の監査上の制限、DBなし回帰の実行経路は [audit/README.md](audit/README.md) を参照してください。圧縮manifestの `jsonl+gzip-v1` はコンテナ形式の版であり、時計の版ではありません。新しいmanifestは `clock` と `row_format` を併記し、旧形式も汎用decoderで読み出せますが、時計来歴のない記録は単調時計の性能根拠として合格しません。
