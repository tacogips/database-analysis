# revision21：wideの失敗保存と所有処理の終了確認

> **履歴資料**：本文の「最新」「未実行」「必須」は記録当時の状態です。現行の手順は[実務ガイド](table-count-guideline.md)・[校正](table-count-calibration.md)、実行状態は[所有者proof](../results/reviews/final-all-proof.json)を参照してください。旧網羅grid・ロック比較・SQL例を本番手順や現在の必須再実験として実行しません。過去指摘の除外は技術的合格や最終査読閉鎖ではありません。

この工程はSOL-WIDE-ERROR-001/002への限定修正提出です。実DB接続、Docker操作、DB計測、他workflow起動、委譲、commit/pushは行っていません。過去の測定JSONは変更しません。最新の白紙allは要求実験より前に所有者が中断した状態のままです。現コードでの白紙all、所有者の意味・最終ソース監査と最終proof、別セッションのSol最終査読が必要です。fixedという著者回答は査読閉鎖ではありません。

## 保存してから止める

`reqwide.persist_outcome`を01a/01b、96/97、01dのcallerへ接続しました。staticとservice双方から予期しない標本失敗を集計し、元のstatus・errno・cleanup情報を変更せず保存します。失敗armと生標本、到着会計、完了した窓と実method区間、method/workload/samplerそれぞれの後始末を保存した後に例外を伝播し、wrapperは非0終了します。`complete=false`、`reusable_mandatory_completion=false`のまま後続armへ進みません。Rubyの実キャッシュ判定で非0終了を拒否する回帰も追加しました。共有コードの変更は再帰的fingerprintに含まれ、旧成功statusの再利用も無効になります。

`precision_not_reached`、全体の`budget_exhausted`は有効な未達観測です。標本の3024＋ROLLBACK成功も確認した文上限として有効なまま残します。COUNTの1317/3024は所有sessionのtimer counter増加が保存できた場合だけ予定上限へ分類します。2013、timer根拠のない1317、`cleanup_failed`、想定外の適用不能やobserver失敗は有効な必須完了へ変換しません。静的COUNTも元エラーを先に記録し、timer観測に失敗しても失いません。

最後まで予定観測を終えた場合の`complete=true`と`reusable_mandatory_completion=true`は、計測プロトコルの完了です。精度達成・時間予算達成・サービスSLO合格を意味しません。期待された打切りを完走の中央値・順位へ入れません。

## arm間の後始末

wideのmethod接続は所有IDを保存し、既存の`close_owned`でROLLBACK・close・別接続からのsession/transaction消滅を確認します。確認できなければ次の比較を開始しません。workloadはfinallyで`finish(confirm_server=True)`を呼び、失敗時にも会計と全service/server標本をcallerへ戻します。複数の失敗が重なっても標本の原errnoとworkloadの失敗は別フィールドに残します。Samplerのjoin失敗も別に記録します。

同じ既定Falseの漏れを持つ反復負荷caller（`experiments.py`、`reqlib.py`、`reqruns.py`）も監査し、共有`Workload.finish`の既定をTrueへ変更しました。既に明示Trueのreqmix/reqscaleは維持します。共有finishは到着会計、registry完結、子プロセスの終了コード/残存、observer接続・観測・KILL結果を別に保存し、未確認なら例外で後続比較を止めます。他callerの測定方法やSQLは再設計していません。サーバー終了後もstuck/queuedの打切り観測を成功要求へ書き換えません。

## DB不要の検証と適用範囲

`bench/test_wide_outcomes.py`をallのself_checksへ登録しました。実sampler、実wide arm/caller、実Workload.finishをASTから実行し、SQL・時刻・プロセスだけをfakeへ置き換えています。実ネットワークやDBには接続しません。

- genuine2013＋ROLLBACK失敗、精度達成後のROLLBACK失敗、未知1317、確認済みdeadline、精度未達、全体予算、正常sample/countを検査します。
- accounting整合＋inflightでもregistry不完全、child非0/未終了、observer接続/観測失敗、KILL失敗、複合失敗で後始末確認を失敗させます。
- 各callerについて失敗結果が保存されてから例外になること、最後の失敗arm以降へ進まないことを検査します。正常・精度未達・予算到達・確認済みdeadlineでは予定腕数を保って最後まで走るfake回帰も行います。
- cacheの実Ruby判定、再帰的依存、既存の会計・標本・protocol・raw・起動・matrix撤去・resource検査を維持します。

全コマンド・終了コード・完全ログは[検証JSON](../results/reviews/author-revision21-verification.json)と[完全ログ](../results/reviews/author-revision21-verification.log)へ保存します。開発中のテスト側の誤りも失敗ログへ残し、修正後の最終チェックと区別します。fakeは実MySQLのキャンセル時間・発生頻度・負荷・回復を実証しません。

数学、sampling/countのSQL方式、statement/total caps、fixture、順序と反復、要求script一覧、write-mix/scale計画は維持しました。追加SQLは終了確認と静的COUNTのtimer確認です。3/5/10%の精度・cost比較と100列TDEの3/10%サービス比較は新規計測していません。ホストcache未統制、5%サービス未計測、MVCC/鮮度と推定誤差の区別、TDEと列暗号化の区別を維持します。VM余力・Docker設定・cacheは変更していません。

全stable ID・要求実験・議論履歴は[revision21完全adapter](../results/reviews/author-revision21-output.json)へ引き継ぎます。元のworkflowInputは変更せず、出力revisionだけを進めます。
