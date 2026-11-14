# 保存済み結果の監査と集計

DB に接続せず、`results/` の保存済み測定だけを読みます。ベンチマークの実装を import せず、件数・観測機会・終了理由・raw のハッシュと集計を照合します。テーブルデータは扱いません。

```sh
python3 audit/summarize-accuracy-frontier.py results/requested_accuracy_cost_frontier.json
python3 audit/summarize-service-frontier.py results/requested_accuracy_service_100.json
python3 audit/audit-wide-probability.py 50
python3 audit/audit-wide-probability.py 100
```

精度集計は静止テーブルの 105 ケース、サービス集計は 100 列 TDE の 48 アームが対象です。前者の 3/5/10% と後者の 3/10% を混同しません。表示する中央値や分位点は観測値の記述であり、本番の精度保証・SLO 合格・普遍的な最速手法の認定ではありません。

全対象の結果が揃い、計測プロセスが終了してから、次の重い raw 監査を実行します。計測中には実行しません。

```sh
python3 audit/audit-final-results.py
python3 audit/audit-write-mix.py --verify-raw
python3 audit/audit-extra-evidence.py --verify-raw
```

`audit-final-results.py` は実ソースから対象を組み立て、基本比較・設計カタログ・通常 COUNT ケース・版別の追加実行と結果を照合します。現行の予定は基本 22 件・設計 58 件・通常 COUNT 12 ケース・追加 144 スクリプト／版別 158 実行です。不足・余分な対象・反復欠落・通常 COUNT の行ロック観測を報告します。

`audit-write-mix.py` は 41 スクリプトと 112 回の主比較に加え、補助・長時間対照の観測機会を独立に組み立てます。`audit-extra-evidence.py` は精度・サービス・30 件の primary-scale 比較とソースの照合を扱います。全結果がない途中実行では不足が報告されます。

**終了コードだけで合格と判定せず、出力 JSON の各 `problems` を確認してください。** これらの監査は、既定の全実験の網羅性、禁止したロック試験の除外、最終データ整合性、文書の主張まで一括認定するものではありません。最終完了には、それぞれの確認が必要です。

raw は gzip のサイズ・SHA-256、展開後のサイズ・SHA-256・件数を照合します。`audit-arrival-windows.py` は予定到着時刻で計測区間を選び、完了・失敗・未完了・未開始件数と保存された遅延集計を再計算します。未完了を含む遅延は下限値であり、分位点がない場合をゼロと扱いません。

ロックを取る COUNT の対照試験は、利用者の指示で既定の検証から除外する方針です。過去の中断実行の記録と、今後の最終実行の証拠は区別してください。

時計修正後の `lab-monotonic-v2` は、予定到着・実メソッド・baseline/trial/recovery（基本比較はduring）・server標本を同一ホストの `time.monotonic()` 秒で記録します。`clock` の domain_id、実装、Linux boot_id/time namespace、開始アンカーと `clock_end` の終了アンカーを保持します。アンカー内の wall_before/wall_after は来歴だけで、逆転しても補正・変換・窓選択には使いません。子workloadは親のdomainを明示的に継承し、異なるboot/namespaceを拒否します。

`audit-arrival-windows.py` は全rawと保存された各 `window.{begin,end,clock_domain}` から広い窓と実メソッド窓の件数・状態・遅延、server標本の選択と集計を再計算します。サービスrawの先頭6列は従来の順序を保ちますが、第0列はv2では単調時計です。v2は11列で、後ろにworker番号、到着連番、実開始、完了/打切り観測、domain_idを加えます。予定表 `arrival_accounting.{origin,cutoff,interval,schedules}` から全到着を独立に復元し、集計を書き換えても到着欠落は合格にしません。serverの `t` と `finished_at` も同じ座標です。標本の窓への帰属は観測開始 `t` によります。DB内部のNOWやtrx ageをこの時計へ変換することはしません。

旧形式の壁時計記録は変更せず保持します。旧6列を黙ってv2として解釈したり、時刻を並べ替えて修復したりしません。時計来歴や元の窓境界がない旧記録は独立したサービス窓検証を通過しません。修正前の正に見える窓も再測定前の性能/SLO根拠にしません。空窓・逆転窓は合格にならず、未完了は収集終了時点の下限値です。要求が窓終了後に完了する場合も、予定到着の窓に最終観測結果を帰属させる既存の定義を維持します。実験完了・精度・負荷予算・後始末の判定は引き続き別です。

DBなし回帰は `bench/test_clock_domains.py`。実際のarm/runner/workload/samplerとこの独立監査を呼び、壁時計±3600秒、到着欠落、foreign/missing/inverted区間、censoringを検証します。benchコンテナにはauditディレクトリをmountしていないため、labのselfcheckは独立監査の実ソースをstdinで渡します。検証結果には渡されたソースのSHA-256も記録します。
