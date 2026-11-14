# 起動と証拠保存の修正提出（revision 16）

> **履歴資料**：本文の「最新」「未実行」「必須」は記録当時の状態です。現行の手順は[実務ガイド](table-count-guideline.md)・[校正](table-count-calibration.md)、実行状態は[所有者proof](../results/reviews/final-all-proof.json)を参照してください。旧網羅grid・ロック比較・SQL例を本番手順や現在の必須再実験として実行しません。過去指摘の除外は技術的合格や最終査読閉鎖ではありません。

この工程はauthorによる修正と、DBを使わない回帰確認です。DB接続・Docker操作・計測・委譲・他workflowの起動・commit/pushは行っていません。新規の数値比較はありません。白紙allと所有者監査、別セッション2役のSol査読は次工程です。

## 修正と検証

| ID | 修正提出 | 根拠と限界 |
|---|---|---|
| ROOT-RUBY-RUNTIME / ROOT-WHITE-ALL-RUBY-BOOTSTRAP | rootが適用したRuby 4.0.9、全5taskの明示`mise exec`、POSIX bootstrapを維持。`mise.toml`をrequested fingerprintへ追加。controllerとlabの実体・版をログへ記録 | [実Rielaのguard付きprobe](../results/reviews/mise-runtime-latest-probe.json)はlauncher/child双方4.0.9。今回の構文確認と実wrapper/controller/taskを通るfake回帰は成功。probe/fakeを白紙DB成功とはしない |
| ROOT-RAW-ARTIFACT-SIZE | `reqscale`は収集と窓集計を終えてから全service/server標本を標準gzip JSONLへlossless分割。非圧縮8 MiB/chunk上限、件数と圧縮前後SHA/サイズをメインJSONへ保存。nested rawはartifact/cache/snapshotへ含め、メインJSONの参照先も検査 | 往復・境界・空stream・件数・ハッシュ・破損/欠損を純粋テスト。実Labのartifact/cacheと実runnerのsnapshot回帰も成功。[読出し方法](../README.md#生の計装データ)。実計測のrawは次runnerが作成する |
| ROOT-FAILED-ALL-SUMMARY | `last_run.json`のSHA/サイズ/inode/mtime/ctimeを前後で読み、未変更・未作成なら`not_reached, ran=[]`。旧summaryはlock前に移動/削除しない。前回candidate/logを次試行前に複写保存し、snapshotが旧ファイルを含み得ることを明記 | 旧`completed`と成功した旧`ran`が残ったまま早期exit7になる回帰で、加算なし・元ファイル不変を確認。最初の実失敗は[既存snapshot](../results/reference/review-20261107T040818Z-62807/manifest.json)にも保持。旧candidateは書き換えていない |

完全ログと全コマンドの数値終了コードは[verification](../results/reviews/author-revision16-verification.json)・[log](../results/reviews/author-revision16-verification.log)へ保存します。fake起動回帰の初回はmacOSの`/var`と`/private/var`の同一ディレクトリを文字列比較したテスト側の失敗でした。canonical pathで比較するよう直し、失敗ログを残して再実行しました。実DBの失敗を修正済みに見せたものではありません。

`test_raw_evidence.py`をallのself_checksへ追加し、既存cache/white-allの回帰も拡張しました。frontierの失敗保存・文予算分類、matrix撤去失敗伝播、disabled lockの区別も既存の実関数/fake回帰で確認しました。実撤去やサービス予算の実証は次allの対象です。

## 仮説と未計測範囲を維持

PRIMARY-scaleの方式群・各反復を分けた既存30script、300秒の文上限と150秒の分割上限は変更していません。raw表現だけを計測後に変えました。01cの7fixture×5seed×3目標、01dの48腕も変更・重複作成していません。3/5/10%の同条件時間・読取量と100列TDEの3/10%サービス影響は未計測です。5%サービス、任意分布の精度保証、本番無影響は主張しません。

旧0hの反復不足・0j中断、92の終了コード不明、旧負荷生成器、cache carryover、EXPLAINと内部読取索引の違いは引き続き制限です。`70`の再起動要求撤回とall列挙の不整合は前回からの未解決事項として保持します。今回の限定修正でスコープを拡張・削減していません。

主文書の数値は既存のハッシュ付きreferenceのままです。最終runnerのmeasurementSnapshotにある新規結果へ文書を対応付けた後、所有者が必須scope、semantic error、意図した中断、精度、予算、arrival保存則、回復、cleanupと最終ソース不変性を個別に監査します。`final-all-proof.json`はまだ作成しません。process exit0と収集だけでは合格にしません。

全stable IDの著者回答、要求実験と議論履歴は[今回adapter](../results/reviews/author-revision16-output.json)に引き継ぎます。fixedは修正提出であり、査読者による閉鎖ではありません。revision15の監査履歴はadapterに保持し、[現在の統合監査](table-count-integration-audit.md)で今回の結果と分けます。
