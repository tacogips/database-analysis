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

TDE の50/100列比較と48アーム比較では、保存trial群が全rawの連続した部分集合であること、件数・状態・遅延分位点が一致することを確認します。時刻が保存された実COUNT／標本メソッド区間は、全rawから区間を選び直します。baseline/trial/recoveryの元の開始・終了時刻は保存されていないため、広い窓の元の境界やbaseline/recovery分位点を独立再構成したとは主張しません。
