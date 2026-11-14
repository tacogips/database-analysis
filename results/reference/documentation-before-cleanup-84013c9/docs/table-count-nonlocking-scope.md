# 非ロックスコープへの変更（revision24・重要条件のみ）

最新の利用者指定「全行lockは絶対に避けるので計測しなくていい。lockを発生させない方法で計測」を、過去のロック比較要求より優先しました。COUNT候補と意図的な競合注入を変更しました。通常SELECTのMDL、fixture作成・投入・保守・撤去の自然なロック、サービスの通常業務に相当するUPDATE/INSERT/DELETEは別です。

## 選択範囲と除外の扱い

[機械可読の範囲台帳](../bench/nonlocking-scope.json)が既定の明示allowlistと、全available対象の選択・スキップを保持します。最新指定は広いエンジン調査でなく、実務判断に必要な代表条件だけです。基本impactは9件、requestedは7スクリプト・版別9プロセスを選択します。

- 基本impact: `baseline`、`baseline_end`、`count_default`、`count_estimate`、`count_force_primary`、`count_force_secondary`、`count_on_replica_t_repl`、`replica_control_no_count`、`count_timeout_hint`。
- requested: `00-budget-start`、`01a-PROB-WIDE-50`、`01c-ACCURACY-COST`、`01d-ACCURACY-SERVICE-100`、`91-encrypted-count`、`93-metadata-accuracy`、`94-metadata-on-replica`。91は3版、他の版指定は台帳どおりです。

100Mの基本fixture、初期・最終整合、静止58設計、RC/RRのplain COUNT12ケース、固定digestのmatrix5版は維持します。基本impactの残り13件とrequestedの残り137スクリプト・149プロセスは全件を`skipped_by_user_scope`として台帳に保持し、不足でも成功測定でもありません。旧revision23の22件・144スクリプト予定は[版付きprior scope](../bench/nonlocking-scope-v23.json)へ保存し、現台帳からSHA-256で参照します。availableなスクリプトはすべて明示IDで実行可能です。旧150スクリプトのうち全節が非ロックスコープ外の02/04/41/42/43/50は引き続きimport前に停止する`.py.retired`であり、実行対象ではありません。過去観測は今回の成功へ加算しません。

混在した0c/13/14/25/3a/3b/40/45/55/57/59/91/94は非ロック部分を残しました。特に40はRC/RR・3索引設計・既知のread-only6形式、57はcontrol/plain/split、91はTDE/plainのSELECT比較・各索引・分割・keyring検出を維持します。91のCOUNT埋込みINSERTと鍵rotation競合は除外します。20の先行段階も非ロックplain COUNTへ変わるため、旧ロック試験後の持越しを再現したとは呼びません。

49/52/52aの初期件数・再照合は、クライアントで通常COUNTを受けてから、別のパラメータ付きDMLでカウンタへ保存します。静止条件の元の検証目的を維持し、書込中のCOUNTと保存の原子性は主張しません。46/3c/3aの世代・索引変更は短い文の間のfixture変更で、保留DDLを意図的に作って待ちを測る比較ではありません。54の索引作成と93のANALYZE校正も比較の前後の保守であり、ロックしない自動カウント手順ではありません。非InnoDBのCOUNTは静止カタログだけで、サービス影響候補にはしません。

選択した代表広幅、105静的精度機会、48サービスarmのプロトコルは維持します。重複100列grid、write-mix41スクリプトと112主機会・補助/tail、primary-scale30スクリプト等の計画・上限は任意の明示ID範囲として保持し、既定allの必須条件にしません。標本法の数学・SQL方式・精度目標を変えて通過させていません。

## 実行経路とガード

`lab.rb all/measure`は通常COUNT・設計カタログ・matrixを維持し、基本impactは`experiments.py run <選択名...>`、requestedは既定allowlistを実行します。全Python registryを削らず、明示名/IDは従来の実行順・版・outputs・再利用判定を使います。台帳が空またはrequested IDが不明なら、summary書込・Docker照会より前に失敗します。各callerから意図的なDDL/FLUSH/ANALYZE待ち注入を除き、read-only MDL・runtime状態の監視を維持しました。`lablib.query`はcursor取得前に`nonlocking_policy.assert_nonlocking_sql`を呼び、明示ロック、SERIALIZABLE設定、COUNTを埋め込むDML/CTAS/SET、検査不能なPREPAREを拒否します。ガード例外はMySQLの予定上限へ変換しません。変更されたpolicyは依存ハッシュへ、範囲台帳はrunnerのfingerprintへ含めます。

この字句ガードはSQL一般の安全性証明ではありません。保存routine・eventの本文と動的SQLを含むcaller監査が必要です。現40のprocedure/PREPARE、13のhint-contextのCOUNTは既知のread-only文で、結果保存は独立文です。直接cursor.executeを使うload/consistencyはfixtureと通常業務、他はSHOW REPLICA STATUS/SHOW TABLE STATUSと残データ削除です。ここに除外COUNTはありません。12の禁止SQL文字列はPython正規表現のテスト入力であり、DBへ送信しません。

`test_nonlocking_scope.py`は実際のSQL送信関数・Python dispatcher、選択とスキップの完全な分割、全availableスクリプトの版・暗号化・outputs、prior scopeハッシュと保持計画をDBなしで確認します。`test_nonlocking_runner.rb`は実際のLabの既定`experiments`・引数なし`requested()`、明示ID、不明ID・空allowlist拒否と最終verifyを、隔離filesystemとfake実行関数で検証します。語句検索だけを実行経路の証明にしません。

`audit/audit-final-results.py`は現行の選択集合から期待数を導き、スキップ台帳・scope SHA-256・summaryの予定集合・成果物ハッシュ/サイズを別々に報告します。範囲外の過去結果は存在しても未加算です。`test_scope_audit.py`は不足・過去optional・版違い・cached成果物の破損/欠損を隔離fixtureで検証します。監査の出力自体は実行成功や意味上の合格を認定しません。

## 文書と証拠の境界

ランダムINSERT/DELETEはCOUNTの準備に不要です。ラボでサービスの通常業務を模擬し、MVCC・行数変動・サービス影響を確認する外部負荷です。静止精度と同時負荷を分けます。任意範囲のwrite-mix主比較には一致したno-count対照がなく、quiet truthも同時点真値やdrift上限を保証しません。実DBには接続しません。

暗号化状態のSol指摘001/002/003はrevision22の主文書§3・詳細§19・訂正coverageを引き継ぎました。サーバーのTDE鍵利用とクライアントの鍵保有を分け、SQL上の単一整数idは暗号由来でも構造上の標本候補とします。二進/UUID/複合PKは現整数セル実装の対象外です。合成バイナリを実アプリ暗号性能にせず、鍵喪失・ストレージ暗号の破壊実験を追加要件にしません。

範囲修正時の[DBなし検証ログ](../results/reviews/author-essential-scope-verification.log)と[終了記録](../results/reviews/author-essential-scope-verification.json)はselfcheckと構文の証拠だけです。その後の縮小白紙allは[所有者proof](../results/reviews/final-all-proof.json)でexit0・意味・最終source・整合・後始末を監査済みです。静的台帳の`planned_not_db_executed`は実行前の宣言であり、実行状態の根拠はproofです。精度未達・予定上限とサービスSLO未認定を分け、統合後の文書に対する別セッションSol最終2役は未実施です。旧exit130や壁時計窓の却下記録は書き換えません。

本書はlabの範囲・実行経路の説明です。利用者の取得手順は[実務ガイド](table-count-guideline.md)と[校正](table-count-calibration.md)を優先します。`bench/count_preflight.py`の旧推薦ラベルは静止labの研究診断で、本番の無制限COUNTや分割の許可ではありません。
