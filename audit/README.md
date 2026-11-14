# 保存済み結果の監査と集計

DB に接続せず、`results/` の保存済み測定だけを読みます。ベンチマークの実装を import せず、件数・観測機会・終了理由・raw のハッシュと集計を照合します。テーブルデータは扱いません。

現行の利用手順は[実務ガイド](../docs/guidelines/mysql-innodb.md)と[校正](../docs/mysql-count-accuracy.md)です。利用者がlabや本監査を実行する必要はありません。`bench/count_preflight.py`の推薦ラベルは静止labの研究診断で、本番実行許可ではありません。

計測所有者が終了してから、選択範囲の基本確認を実行します。以下はいずれもDB不要です。

```sh
python3 audit/audit-final-results.py
python3 audit/summarize-accuracy-frontier.py results/requested_accuracy_cost_frontier.json
python3 audit/summarize-service-frontier.py results/requested_accuracy_service_100.json
python3 audit/audit-wide-probability.py 50
```

`audit-final-results.py`は[現行scope](../bench/nonlocking-scope.json)の基本9件・静止設計58件・通常RC/RR COUNT 12ケース・matrix 5版・追加7スクリプト/版別9プロセスを照合します。基本の残り13件・追加の残り137スクリプト/149プロセスはユーザー指定のskipで、不足・測定成功・技術的な査読閉鎖に数えません。scope内の不足、成果物不一致や非ロック違反は無視しません。scopeの静的statusは実行前宣言で、実行状態はrunnerとowner proofで確認します。

静止精度は105ケース（3/5/10%目標）、サービス比較は100列TDEの48arm（3/10%目標）、選択wide gridは50列です。100列の重複wide gridは既定対象外です。各中央値や分位点は記述統計で、本番の精度保証・SLO・普遍的な最速手法の認定ではありません。

**終了コードだけで合格にせず、出力JSONの各`problems`、未達・取消・終了確認を確認します。** [所有者proof](../results/reviews/final-all-proof.json)は縮小白紙allのexit0・意味・最終source・整合・後始末を監査済みです。[固定測定manifest](../results/reference/review-20261107T155750Z-26743/manifest.json)と[固定owner bundle](../results/reference/owner-acceptance-20261107T161659Z/manifest.json)が対応します。文書のみの最終統合後も測定sourceと7つの公開Python監査sourceを照合します。文書受入れの別セッションSol 2役は未実施です。

旧22基本比較/144追加スクリプトの計画は履歴です。`audit-write-mix.py --verify-raw`（41スクリプト・112主比較と補助/tail）、`audit-extra-evidence.py --verify-raw`のmain（primary-scale 30比較を含む）、`audit-wide-probability.py 100`は任意の旧研究監査です。既定縮小allの合格に全旧gridを揃えるためのコマンドではありません。範囲外不足が報告されても追加測定を自動要求しません。旧計画・結果・指摘は削除せず、特定の現推薦を変える重大な欠陥だけを再評価します。

選択された105/48の検算だけが必要な場合、`audit-extra-evidence.py`の`check_frontier`/`check_service`/`check_source`を保存JSONへ適用できます（[ownerチェック](../results/reference/owner-acceptance-20261107T161659Z/reports/owner-essential-evidence-checks.json)）。mainの全履歴scale走査と区別します。raw解凍を伴う重い監査は計測中に行いません。

rawはgzipのサイズ・SHA-256、展開後のサイズ・SHA-256・件数を照合します。`audit-arrival-windows.py`は予定到着時刻で区間を選び、完了・失敗・未完了・未開始と遅延集計を再計算します。未完了を含む遅延は下限で、欠測をゼロと扱いません。実験完了・精度・サービス予算・後始末を別々に判定します。

意図的なロック・pending-DDL/FLUSH/ANALYZE競合注入は既定から除外済みです。通常SELECTのMDL、fixture保守や通常業務書込の自然なロックとは別です。初回縮小実行の壁時計窓却下は[固定却下記録](../results/reference/owner-acceptance-20261107T161659Z/reports/owner-essential-semantic-rejection.json)に残し、旧・affected/時計不明窓を性能/SLO根拠にしません。

時計修正後の `lab-monotonic-v2` は、予定到着・実メソッド・baseline/trial/recovery（基本比較はduring）・server標本を同一ホストの `time.monotonic()` 秒で記録します。`clock` の domain_id、実装、Linux boot_id/time namespace、開始アンカーと `clock_end` の終了アンカーを保持します。アンカー内の wall_before/wall_after は来歴だけで、逆転しても補正・変換・窓選択には使いません。子workloadは親のdomainを明示的に継承し、異なるboot/namespaceを拒否します。

`audit-arrival-windows.py` は全rawと保存された各 `window.{begin,end,clock_domain}` から広い窓と実メソッド窓の件数・状態・遅延、server標本の選択と集計を再計算します。サービスrawの先頭6列は従来の順序を保ちますが、第0列はv2では単調時計です。v2は11列で、後ろにworker番号、到着連番、実開始、完了/打切り観測、domain_idを加えます。予定表 `arrival_accounting.{origin,cutoff,interval,schedules}` から全到着を独立に復元し、集計を書き換えても到着欠落は合格にしません。serverの `t` と `finished_at` も同じ座標です。標本の窓への帰属は観測開始 `t` によります。DB内部のNOWやtrx ageをこの時計へ変換することはしません。

旧形式の壁時計記録は変更せず保持します。旧6列を黙ってv2として解釈したり、時刻を並べ替えて修復したりしません。時計来歴や元の窓境界がない旧記録は独立したサービス窓検証を通過しません。修正前の正に見える窓も再測定前の性能/SLO根拠にしません。空窓・逆転窓は合格にならず、未完了は収集終了時点の下限値です。要求が窓終了後に完了する場合も、予定到着の窓に最終観測結果を帰属させる既存の定義を維持します。実験完了・精度・負荷予算・後始末の判定は引き続き別です。

DBなし回帰は `bench/test_clock_domains.py`。実際のarm/runner/workload/samplerとこの独立監査を呼び、壁時計±3600秒、到着欠落、foreign/missing/inverted区間、censoringを検証します。benchコンテナにはauditディレクトリをmountしていないため、labのselfcheckは独立監査の実ソースをstdinで渡します。検証結果には渡されたソースのSHA-256も記録します。
