# revision18：計測前のプロトコル修正

> **履歴資料**：本文の「最新」「未実行」「必須」は記録当時の状態です。現行の手順は[実務ガイド](table-count-guideline.md)・[校正](table-count-calibration.md)、実行状態は[所有者proof](../results/reviews/final-all-proof.json)を参照してください。旧網羅grid・ロック比較・SQL例を本番手順や現在の必須再実験として実行しません。過去指摘の除外は技術的合格や最終査読閉鎖ではありません。

この文書はrevision18のreqmix/scale修正提出の記録です。最新の固定runnerでDB不要self-checkは再実行されましたが、当該DB計測は基本負荷試験中の中断により未到達です（[最新の統合記録](table-count-evidence.md#16-最新の白紙実行基本負荷試験で中断revision-20)）。共通wide経路には別の未解決SOL-WIDE-ERROR-001/002があります。以下の終了確認・失敗伝播の説明をwideへ一般化しません。`results/reviews/sol-protocol-review.json`の静的反例への修正であり、過去の測定JSONは変更しません。白紙allの所有者中断（exit130）は実験成功ではなく、最終proofもありません。全変更後の固定runnerによる白紙`mise run all`、所有者による意味・source・全scope監査、別セッションのSol最終査読が残ります。

## 結果をどう分類するか

`bench/protocol_outcomes.py`は、`completed`、`confirmed_ceiling`、`unknown_interrupt`、`unexpected_failure`、`cleanup_failed`、`not_started`を分けます。`stopped:max_seconds/max_rows`はアプリ側で確認した上限です。3024/1317はその所有sessionの`Max_execution_time_exceeded`増加を保存できた場合だけ予定上限に分類します。経過時間では認定しません。2013、接続断、計画拒否は予定上限にしません。明示したdriver timeout probeの2006/2013観測だけは別の仮説として保存し、COUNT完了や予定COUNT上限には加算しません。probeでも切断原因そのものを証明したとは扱いません。

予定上限は打切り観測です。COUNTの完走・精度達成・負荷予算達成・後始末とは別です。非完了から中央値や完走順位は作りません。新分類で過去のタイマーカウンタ欠落を補完してはならず、所有者が既存結果を再分類・除外します。

## 必須反復を押し出さない構成

`bench/requested/write-mix-plan.json`の41 scriptに、旧05〜09a・0d〜0fの仮説を対応させています。これは実行予定数で、完了観測数ではありません。

- 05〜09aはsplit群とstatement群を分け、各scriptは2反復。合計4反復を維持します。索引更新、ランダムINSERT/DELETE、末尾INSERT/先頭DELETE、各負荷設定を残します。
- 0e/0fは行数上限によるsplit群の4反復を同様に分割します。
- 0dの5つのPRIMARY/control pairは別scriptにし、順序を交替します。対照はsplitの上限を覆う150秒です。実際のmethod区間を抽出して比較し、短いmethodと対照全期間を同一視しません。共通のindexed100 main比較は05に統合します。
- 補助scriptはmoving-populationのdrift splitと、該当負荷でのdriver timeout診断です。旧quiet truthと`within_write_statements`の境界主張は撤回します。未完了writeの成否や窓外dispatchを推測してdrift上限にしません。

split150秒・statement120秒を保持します。別のPRIMARY-scale計画の300秒/150秒、5条件×2群×3反復も変更しません。3/5/10%比較の105 staticと100列48 serviceの実装・仮説は変更しません。ホストcacheは未制御です。独立した私有fixtureの再構築はcache同等性の証明ではなく、従来と履歴蓄積の度合いも異なるので分割前後を同一試験としてpoolしません。

各`opportunities`はsetup前に列挙・保存します。全方式群の上限時間と後始末用予備をsetup後に確保できなければ、全機会を`budget_insufficient`にして非0とします。開始後の失敗では後続を`not_started`と理由付きで保持します。two-hitによる黙った反復省略はありません。合成149/150/120/1秒と全arm上限の反例を実関数で確認します。この予算計算は実測したsetup時間上限や20分内完了の保証ではありません。runner timeoutでも未完了として扱い、上限を短縮して穴埋めしません。

## 後始末と停止

method接続は接続IDを保存し、rollback・closeの成否、別接続からのsession/transaction消滅観測を保存します。残存する所有接続だけへKILL CONNECTIONを送り、消滅確認まで次armを開始しません。確認失敗は非0です。methodの`t0/t1`とcleanup終了時刻を分けます。

負荷停止はfinallyで全workloadに対して試みます。子workerが作成した接続IDとregistryの完結状態を保存し、未終了workerがあればregistryが完全だったと扱いません。保存された未完了要求は消さず、KILL後にサービス成功へ書き換えません。停止時のsession/transaction確認は負荷窓のSLO合格とは別です。Samplerとstall logも終了を試みます。通常のwarmup10秒・回復5秒は設定値であり、状態復元の保証ではありません。

script非0またはmatrix teardown失敗なら、固定CLIは後続scriptと最終データCOUNT検証を開始しません。予定script・未着手理由・失敗statusを保持します。`lab_data_intact=false`は検証未到達を含みます。中断経路の実DBでの確認は固定runnerに残り、DB不要fakeの通過だけで終了確認を実証したとはしません。

## 容量と証拠

all/prepareは固定イメージ準備→reset→up/build→空き容量確認→self-check→setup以降の順です。measureも計測前に確認します。選定した予備容量（VM2 GiB・host40 GiB）を下回るか取得不能なら、`results/resource_preflight.json`へ保存して計測前に失敗します。最小必要容量の実測ではありません。`results/reviews/build-cache-cleanup-evidence.json`に実際のVM満杯と停止後の空き容量を保持し、自動pruneは実装しません。

service/server全rawを8 MiB上限のlossless gzip JSONLへ保存し、従来のfingerprint・artifact SHA・immutable snapshot経路を使います。`bench/test_reqmix_protocol.py`と`test_resource_preflight.rb`をallのself-checkへ登録しました。全DB不要検証の完全ログと最終exitは`results/reviews/author-revision18-verification.json`を参照します。測定の終了コード・全出力・意味・source不変性は、次の固定結果で別に監査します。
