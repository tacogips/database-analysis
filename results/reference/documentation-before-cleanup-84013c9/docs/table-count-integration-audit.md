# 文書統合監査（revision22の保持記録）

> **履歴資料**：本文の「最新」「未実行」「必須」は記録当時の状態です。現行の手順は[実務ガイド](table-count-guideline.md)・[校正](table-count-calibration.md)、実行状態は[所有者proof](../results/reviews/final-all-proof.json)を参照してください。旧網羅grid・ロック比較・SQL例を本番手順や現在の必須再実験として実行しません。過去指摘の除外は技術的合格や最終査読閉鎖ではありません。

最新は[revision23の非ロックスコープ変更](table-count-nonlocking-scope.md)です。下記はその前の文書統合時点の記録で、変更後ソースや新allの認定ではありません。

全履歴は[author21完全adapter](../results/reviews/author-revision21-output.json)、今回の完全状態は[revision22 adapter](../results/reviews/integrate-revision22-output.json)に保持します。workflowInput.currentRevision=20を変えず、最新author21から22へ進めました。fixedは著者提出、査読閉鎖は全件pendingです。DB接続・Docker操作・計測source/config編集・委譲・commit/pushはしていません。

## 今回の判定

- 候補exit130、fresh summary started/ran=[]、lab_data_intact=false、proofなし。[完全ログ](../results/reference/review-20261107T100132Z-16034/results/reviews/final-all-run.log)と[中断理由](../results/reviews/nonlocking-scope-interruption.json)を保存。
- [全artifact監査](../results/reviews/integrate-revision22-artifact-audit.json)はsnapshot536/source336 hash一致、現存112ファイルとraw42chunkを全読取。24status成功と08部分結果を別判定。
- [精度費用の再集計](../results/reviews/integrate-revision22-measured-frontier.json)を主文書§2と詳細§18へ反映。[暗号化状態の対応](../results/reviews/integrate-revision22-encryption-coverage.json)は詳細§19。最終all・所有者意味監査・別Sol2役を代替しません。

## 主張と方法論

| 主張 | 判定 | 根拠と限界 |
|---|---|---|
| 任意表3/5/10%保証・本番無影響 | unsupported | 主文書§1/2/4で保証しない。統計/条件付き区間/正確値と鮮度を分離 |
| 01cの3/5/10%費用と実現誤差 | supported within synthetic static scope | 詳細§18、原結果と再集計。固定seedの75到達/30未達を本番被覆へ外挿しない |
| 100列TDE、小索引ならexactが安い | conditional | 01b静止・01d非索引列UPDATE。索引更新では上限到達、MVCC・実行経路を分離し切れていない |
| 10%は3%より低read | supported in measured01d conditions | trial global InnoDB counter。method単体物理I/Oや一律のtail改善とは別 |
| 10%でサービス性能改善・SLO合格 | unsupported | 01dの悪化窓も併記。短窓・未着手・不均等method時間、5%service未計測 |
| 旧wideの腕間service比較 | invalid as adoption evidence | 終了未確認の旧版は診断だけ。新01a/01b/01dと混ぜない |
| 新wide終了確認/会計 | supported recorded paths; abnormal cases conditional | method/workload/sampler確認と打切りを保存。2013/observer/KILL異常は実関数fake根拠 |
| cache/dirty/flush/read-ahead/order/host同等性 | unsupported | build/truth/evict/前arm持越し。InnoDB readはhost物理readではない |
| 索引経路・MVCC因果 | conditional | EXPLAIN/FORCE/Handlerは補助。cluster/undo/parallel workerを完全分離していない |
| TDE、列暗号化、鍵なし整数PK | supported semantics / conditional structural applicability | 詳細§19。サーバー読取能力とSQL型で判定、合成payloadを実暗号性能にしない |
| 通常SELECTとINSERT SELECT等のロック | supported historical lab scope | RR/RC/autocommitと文形を区別。将来defaultは所有者の非行ロックscope記録に従い修正待ち |
| 基本36impactのcache比較・SLO | unsupported | 216窓で保存則一致、8打切り/1空窓。固定順・unequal windows・回復完了未証明 |
| 完了reqmix15scriptの全mix順位/quiet drift | unsupported | 予定機会の充足はその部分だけ。matched no-count controlなし。旧quiet保証撤回、08を除外 |
| 2013・未知1317をCOUNT上限扱い | invalid | protocol修正維持。06確認1317はtimer増分1、driver probe2013は別。原因不明を再利用成功にしない |
| source/image/output/cache来歴 | supported provenance; final reuse unresolved | 全hash一致、24status reused=false。全scope/最終整合とowner proofは未確認 |
| 全all/最終データ整合/レビュー受入 | invalid for this attempt | exit130・lab_data_intact=false・proofなし。保持済み個別観測の破損とは言わない |

## 全要求の処置

過去closed/withdrawnは履歴として保持し、未到達を新規成功にしません。result_submittedは当該測定要求への著者回答で、査読者の閉鎖ではありません。

| ID | 状態 | 結果と制約 |
|---|---|---|
| EXP-O10-PRIMARY-SCALE | unresolved_not_run | 分割後の全条件未実行。旧0h欠落・0j中断を保持。 今回も対象の現コード測定は未到達。 `bench/requested/primary-scale-plan.json` |
| EXP-O10-INDEXED-100-STALL | unresolved_current_source_not_run | 保存観測あり、現コード再測定なし。 今回も対象の現コード測定は未到達。 `results/reference/20261107-primary-coverage/results/requested_0d-EXP-O10-INDEXED-100-STALL.json` |
| EXP-O10-ROW-CEILING | unresolved_not_run | 0e/0f・3a再試験未実行。 今回も対象の現コード測定は未到達。 `bench/countlib.py` |
| EXP-O10-NOPK-FORCE | unresolved_not_run | 実経路・速度未検証。 今回も対象の現コード測定は未到達。 `bench/requested/0l-EXP-O10-NOPK-FORCE.py` |
| EXP-O6-WRITE-MIX | reopened_partial | 05/06/07各5scriptは完了。08補助は部分JSONだけでexit不明、残りmix未到達。全比較/反復は未完。 `results/reviews/integrate-revision22-artifact-audit.json` |
| EXP-O8-PKONLY-THREADS | historical_closed_preserved |  `` |
| EXP-R6-float-dst | historical_closed_preserved |  `` |
| EXP-O2-1-guideline-sql-as-written | historical_closed_preserved |  `` |
| EXP-O8-1205 | result_submitted_pending_reviewer | 04は今回exit0。MDL待ちの診断で、意図的ロック計測の今後のscopeは所有者記録と整合が必要。 `results/reference/review-20261107T100132Z-16034/results/requested_04-EXP-O8-1205.json` |
| EXP-7-coverage-gaps | historical_closed_preserved |  `` |
| EXP-S8-ENUM-DOMAIN | result_submitted_pending_reviewer | 今回8.4.13はexit0。9.7.4/26.10.0同梱は過去観測で今回成功ではない。 `results/reference/review-20261107T100132Z-16034/results/requested_01-EXP-S8-ENUM-DOMAIN-8.4.13.json` |
| EXP-S8-REPLICA-STOP-FAILURE | result_submitted_pending_reviewer | 02は今回exit0、全節の保存結果あり。期限超過0cは別の過去観測。 `results/reference/review-20261107T100132Z-16034/results/requested_02-EXP-S8-REPLICA-STOP-FAILURE.json` |
| EXP-S8-REPLICA-CHANNEL-GATE | result_submitted_pending_reviewer | 03は今回exit0。複数チャネル拒否の保存結果あり。 `results/reference/review-20261107T100132Z-16034/results/requested_03-EXP-S8-REPLICA-CHANNEL-GATE.json` |
| EXP-R9-working-set-impact | historical_closed_preserved |  `` |
| EXP-O6-CPU-CONTROL | historical_closed_preserved |  `` |
| EXP-O6-NOISE-WINDOWS | historical_closed_preserved |  `` |
| EXP-O6-LONG-UNDO | historical_closed_preserved |  `` |
| EXP-6-sql-not-executed | historical_closed_preserved |  `` |
| EXP-O6-LOOP-RULES | historical_closed_preserved |  `` |
| EXP-R15-replica-procedure | historical_closed_preserved |  `` |
| EXP-O6-ANALYZE | historical_closed_preserved |  `` |
| EXP-O6-THREADS-SCOPE | historical_closed_preserved |  `` |
| EXP-S6-MONITOR-RECHECK | historical_closed_preserved |  `` |
| EXP-S6-REPLICA-FAILURE-CLEANUP | historical_closed_preserved |  `` |
| EXP-S6-INCREMENTAL-SAME-SECOND | historical_closed_preserved |  `` |
| EXP-R13-least-privilege | historical_closed_preserved |  `` |
| EXP-3-history | historical_closed_preserved |  `` |
| EXP-SOL-9-alternative-methods | historical_closed_preserved |  `` |
| EXP-SOL-12-mdl-phase | historical_closed_preserved |  `` |
| EXP-R1-enum-scaling | historical_closed_preserved |  `` |
| EXP-O2-3-cascade-trigger-version | historical_closed_preserved |  `` |
| EXP-SOL-10-engine-states | historical_withdrawn_preserved | 70の過去skipは再起動の実測ではない。現all列挙との不整合を次authorが解消。 `` |
| EXP-O2-2-stop-vs-timeout | historical_closed_preserved |  `` |
| EXP-R5-split-drift | historical_closed_preserved |  `` |
| EXP-R7-background-reader | historical_closed_preserved |  `` |
| EXP-R8-count-repeat | historical_closed_preserved |  `` |
| EXP-R4-primary-split-impact | historical_closed_preserved |  `` |
| EXP-R10-estimate-churn | historical_closed_preserved |  `` |
| EXP-SOL-1-existing-transaction | historical_closed_preserved |  `` |
| EXP-R2-nullable-composite | historical_closed_preserved |  `` |
| EXP-R3-desc-index | historical_closed_preserved |  `` |
| EXP-R11-lock-forms | historical_closed_preserved |  `` |
| EXP-R12-flush-chain | historical_closed_preserved |  `` |
| EXP-R14-resume-failover | historical_closed_preserved |  `` |
| EXP-R16-incremental | historical_closed_preserved |  `` |
| EXP-4-fk-mdl | historical_closed_preserved |  `` |
| EXP-5-indexed-update-lock | historical_closed_preserved |  `` |
| EXP-SOL-2-read-uncommitted | historical_closed_preserved |  `` |
| EXP-SOL-3-null-inclusive-timing | historical_closed_preserved |  `` |
| EXP-SOL-4-view-semantics | historical_closed_preserved |  `` |
| EXP-SOL-5-counter-bootstrap | historical_closed_preserved |  `` |
| EXP-SOL-6-monitor-health | historical_closed_preserved |  `` |
| EXP-SOL-7-table-generation | historical_closed_preserved |  `` |
| EXP-SOL-8-timeout-context | historical_closed_preserved |  `` |
| EXP-SOL-11-lock-sampler-control | historical_closed_preserved |  `` |
| SOL-EXP-ENCRYPTED-WIDE-TRIAL | reopened_partial_required_scope | 01a/01bの今回static/serviceは完了し反映。96/97は未到達で元の全要求は保留。 `results/reviews/integrate-revision22-artifact-audit.json` |
| 94-metadata-on-replica | historical_result_submitted_pending_reviewer | 全節の保存観測あり。今回再測定ではない。 `results/reference/20261107-observations/requested_metadata_on_replica.json` |
| 0c-EXP-S8-REPLICA-STOP-LONG | historical_result_submitted_pending_reviewer | 引継ぎ後復旧を含む保存観測あり。 `results/reference/20261107-observations/requested_0c-EXP-S8-REPLICA-STOP-LONG.json` |
| SOL-EXP-STRATIFIED-SAMPLING | unresolved_optional_extension | 実装は一様復元セル。層化/KL比較は未計測・未採用。 今回も対象の現コード測定は未到達。 `docs/probability-count.md` |
| SOL-SAMP-EXP-PURE-BOUNDARIES | db_free_result_submitted_pending_reviewer | 今回allのselfcheck完了を保持。pure/fakeと実DBの全scope・異常経路を区別。 `results/reviews/integrate-revision22-artifact-audit.json` |
| SOL-SAMP-EXP-98-OWNED-COMPLETION | historical_result_submitted_pending_reviewer | 固定98のexit0、全35観測と精度未達を保存。 `results/reference/20261107-fixed-sampling/manifest.json` |
| SOL-SAMP-EXP-OPERATING-COST | result_submitted_pending_reviewer | 01a/01b/01c/01dの時間/read/precision/予算/cleanupを反映。短窓でSLO合格にしない。 `results/reviews/integrate-revision22-measured-frontier.json` |
| SOL-EXP-HARNESS-PURE | db_free_result_submitted_pending_reviewer | 今回allのselfcheck完了を保持。pure/fakeと実DBの全scope・異常経路を区別。 `results/reviews/integrate-revision22-artifact-audit.json` |
| SOL-EXP-FINAL-REPRODUCTION | reopened_failed | exit130、fresh started/ran=[]、最終verifyなし、lab_data_intact=false、proofなし。所有者記録の非行ロックscope修正後に固定runnerが必要。 `results/reference/review-20261107T100132Z-16034/results/reviews/final-all-candidate.json` |
| SOL-EXP-TDE-RESIDENCY | conditional_not_requested_without_multiplier | 因果倍率を主張せず、未統制のresident/dirty/flush比較を追加必須にはしない。 `docs/table-count-guideline.md` |
| SOL-EXP-WIDE-TAIL | reopened_unmeasured_required_condition | 新01dはTDE診断であり要求96 plain条件の代用ではない。96は今回未到達。 `results/reference/review-20261107T100132Z-16034/manifest.json` |
| SOL-EXP-SAMPLING-CONTRACT | reopened_final_98_pending | pure契約と今回wide/frontier結果を反映。98の現コード完了は未到達。 `results/reviews/integrate-revision22-artifact-audit.json` |
| SOL-FRONTIER-EXP-01 | result_submitted_pending_reviewer | 01c105標本+対応COUNT完了。75目標到達/30精度未達、全目標の費用/readを反映。 `results/reference/review-20261107T100132Z-16034/results/requested_accuracy_cost_frontier.json` |
| SOL-FRONTIER-EXP-02 | result_submitted_pending_reviewer | 01d48arm完了。標本23到達/1予算到達。3%から10%でread減少、tail改善は未証明。 `results/reference/review-20261107T100132Z-16034/results/requested_accuracy_service_100.json` |
| ROOT-WHITE-ALL-RUBY-BOOTSTRAP | fixed_startup_verified | controller/lab4.0.9。今回self-check・初期COUNT・基本測定まで到達。 `results/reference/review-20261107T065608Z-99980/results/reviews/final-all-run.log` |
| SOL-PROTOCOL-E01 | db_free_result_submitted_pending_reviewer | 今回allのselfcheck完了を保持。pure/fakeと実DBの全scope・異常経路を区別。 `results/reviews/integrate-revision22-artifact-audit.json` |
| SOL-PROTOCOL-E02 | db_free_result_submitted_pending_reviewer | 今回allのselfcheck完了を保持。pure/fakeと実DBの全scope・異常経路を区別。 `results/reviews/integrate-revision22-artifact-audit.json` |
| SOL-PROTOCOL-E03 | conditional_after_owner_audit | 旧quiet truth/drift境界は撤回。新reqmix終了確認はDB未到達。wide共通cleanupに別の欠落が判明したため、まず指定pure回帰と限定修正。必要な実DB補測は所有者が主張と不足だけから判断。 `results/reviews/sol-protocol-review.json` |
| SOL-PROTOCOL-E04 | reopened_partial_owner_audit | 完了した15reqmix scriptを読取。scale30/残mix/08終端/最終整合は未完で所有者監査を代替しない。 `results/reviews/integrate-revision22-artifact-audit.json` |
| ROOT-VM-CAPACITY-MAINTENANCE | result_submitted_pending_reviewer | 今回preflight通過。reserve/cache変更なし、完走資源の保証ではない。 `results/reference/review-20261107T100132Z-16034/results/resource_preflight.json` |
| REG-WIDE-OUTCOME-PERSISTENCE | result_submitted_pending_reviewer | author21実関数/fake回帰と今回selfcheck/期待された未達保存を保持。実DB異常注入はしていない。 `results/reviews/author-revision21-verification.json` |
| REG-WIDE-CLEANUP-GATE | result_submitted_pending_reviewer | author21実関数/fake回帰、今回wideの全arm終了確認。異常経路の頻度/実DB時間は未計測。 `results/reviews/integrate-revision22-artifact-audit.json` |
| EXP-ENCRYPTION-STATE-001 | reopened_existing_plan_partial | 01a/01b/01c/01d完了、全all未完。 `results/reviews/integrate-revision22-measured-frontier.json` |
| EXP-ENCRYPTION-STATE-002 | optional_not_run | 権限とクライアント構成の実証を追加できる。現時点の『クライアントが暗号鍵を渡さずSQLで数えられる』という仕様説明の必須条件ではない。 `docs/table-count-evidence.md#19` |
| EXP-ENCRYPTION-STATE-003 | optional_not_run | 実アプリ暗号fixtureを検証済みと主張する場合には必要。現在の行数意味論と幅・索引の限定された比較には不要で、単に暗号文セルを数えられることを新しい性能成果にしない。 `docs/table-count-evidence.md#19` |
| EXP-ENCRYPTION-STATE-004 | not_required_for_current_claims | 現主張は復旧前提・SQL読取不能時の不適用として仕様から説明できる。破壊実験をしても新しいカウント方式の選択にはつながらない。 `docs/table-count-evidence.md#19` |
| EXP-ENCRYPTION-STATE-005 | not_required_for_current_claims | 現在は物理ストレージ性能を未計測と明示しており、SQL上の型による適用判定も説明できる。全組合せは要求しない。実環境の費用順位や暗号化固有の性能を約束する段階で、判断を変え得る代表条件を選ぶ。 `docs/table-count-evidence.md#19` |

## 全指摘への回答

| ID | 著者回答 | 節と根拠 |
|---|---|---|
| FIND-O10-PRIMARY-SCALE | unresolved: 0g〜0kの現コード測定は今回未到達。保存済み部分結果を全条件・反復の充足へ代用しない。 | 主文書§4/7、詳細§18; `results/reference/review-20261107T100132Z-16034/manifest.json` |
| FIND-O10-INDEXED-100-STALL | unresolved: 0dは今回未到達。過去stall診断を保持し、原因・回復・対照の再評価待ち。 | 詳細§11/18; `results/reference/review-20261107T100132Z-16034/manifest.json` |
| FIND-O10-STEP-CEILING | unresolved: 0e/0fは今回未到達。行数上限のソースを保持し、全予定反復の結果待ち。 | 詳細SQL4/§18; `results/reference/review-20261107T100132Z-16034/manifest.json` |
| FIND-O10-NOPK-FORCE | unresolved: PRIMARY不存在時の強制禁止を維持。0lの現コード確認は未到達。 | 主文書§4、詳細§18; `results/reference/review-20261107T100132Z-16034/manifest.json` |
| FIND-O10-ENCRYPTED-STALE | fixed: 保存済み91観測と92の終了コード不明を区別。現コードの再測定済みとはしない。 | §3/7、詳細§6/11/13; `results/reference/20261107-observations/manifest.json`, `docs/table-count-integration-audit.md`, `results/reviews/integrate-revision20-artifact-audit.json` |
| FIND-O10-P100-BOLD | fixed: 範囲が重なる100/sでPRIMARY優位を断定しない。 | 詳細§3/10; `docs/table-count-evidence.md`, `docs/table-count-integration-audit.md`, `results/reviews/integrate-revision20-artifact-audit.json` |
| FIND-DOC-01 | fixed: 保存済み公式確認記録に基づく系列表へ修正。原文全体の再閲覧済みとは表示しない。 | 前提環境、詳細§12; `docs/table-count-guideline.md`, `docs/table-count-evidence.md`, `docs/table-count-integration-audit.md`, `results/reviews/integrate-revision20-artifact-audit.json` |
| FIND-S8-REPLICA-STOP-FAILURE-01 | fixed: STOP遅効、未解決状態、継続担当と復旧確認を分離。過去の正常終了観測を保持。 | §6、詳細§7; `results/reference/20261107-observations/requested_0c-EXP-S8-REPLICA-STOP-LONG.json`, `docs/table-count-integration-audit.md`, `results/reviews/integrate-revision20-artifact-audit.json` |
| SOL-ROLLOUT-ACCURACY | fixed: 参考統計・条件付き区間・正確値を分離。01c105/01d48の精度・費用・未達を反映。任意表の3/5/10%や本番無影響を保証しない。 | 主文書§1/2、詳細§18; `results/reviews/integrate-revision22-measured-frontier.json`, `results/reference/review-20261107T100132Z-16034/results/requested_accuracy_cost_frontier.json`, `results/reference/review-20261107T100132Z-16034/results/requested_accuracy_service_100.json` |
| SOL-ROLLOUT-TRIAL-BUDGET | fixed: 初回試行にもtail、CPU/I/O、回復、中断確認を要求。共通安全行数や1000msの安全保証を置かない。 | §4; `docs/table-count-guideline.md`, `docs/table-count-integration-audit.md`, `results/reviews/integrate-revision20-artifact-audit.json` |
| SOL-ROLLOUT-ENCRYPTED-WIDE | unresolved: 01a/01bの現コード測定と状態表を反映。TDEと合成payload/列暗号化を区別。要求96/97は今回未到達で、全要求完了は保留。 | 主文書§2/3/7、詳細§18/19; `results/reviews/integrate-revision22-encryption-coverage.json`, `results/reference/review-20261107T100132Z-16034/results/requested_probability_wide_50.json`, `results/reference/review-20261107T100132Z-16034/results/requested_probability_wide_100.json` |
| SOL-ROLLOUT-METADATA-COMPARISON | fixed: 精度・鮮度・権限・待機・replicaを比較し、Uninitializedを0件として扱わない。 | §2; `results/reference/20261107-observations/requested_metadata_on_replica.json`, `docs/table-count-integration-audit.md`, `results/reviews/integrate-revision20-artifact-audit.json` |
| SOL-ROLLOUT-ANALYZE-COST | fixed: ANALYZEを初回参照から分離。ページ増量による単調改善を保証しない。 | §2; `docs/table-count-guideline.md`, `docs/table-count-integration-audit.md`, `results/reviews/integrate-revision20-artifact-audit.json` |
| SOL-ROLLOUT-OPERATING-MODEL | fixed: 責任者、監視・停止権限、共有資源、DDL調整、復旧担当を導入票に保持。 | §1/4/6、詳細§10; `docs/table-count-guideline.md`, `docs/table-count-integration-audit.md`, `results/reviews/integrate-revision20-artifact-audit.json` |
| SOL-SAMP-DEADLINE | fixed: 01dの1件は全体予算到達・精度未評価として保持。精度/文予算/全体予算/cleanupを分離し、協調的期限を厳密停止とは説明しない。異常分岐はpure/fake根拠に限定。 | 確率推定仕様、詳細§18; `results/reference/review-20261107T100132Z-16034/results/requested_accuracy_service_100.json`, `results/reviews/author-revision21-verification.json` |
| SOL-SAMP-DOC-CONTRACT | fixed: 整数単一PK、固定snapshot、一様復元セル、疎領域の未達、全対象へのalpha割当を明示。 | §2、確率推定仕様; `docs/probability-count.md`, `docs/table-count-integration-audit.md`, `results/reviews/integrate-revision20-artifact-audit.json` |
| SOL-SAMP-EVIDENCE-DRIFT | fixed: 通常は適用中replica。取得先変更・再試行・適用停止には所有者判断と累積予算を要求。 | §1/4/6、詳細§1/7/10/11; `docs/table-count-evidence.md`, `docs/table-count-integration-audit.md`, `results/reviews/integrate-revision20-artifact-audit.json` |
| SOL-SAMP-98-COMPARISON | unresolved: 今回98未到達。固定版の過去観測・exit0を現ソースの新規完了とはしない。 | 確率推定仕様、詳細§18; `results/reference/review-20261107T100132Z-16034/manifest.json`, `results/reference/20261107-fixed-sampling/manifest.json` |
| SOL-VALIDATION-RUN-CACHE | unresolved: 24statusはreused=false・artifact一致。失敗再利用拒否のソース/pure修正を保持したが、最終scopeのall・所有者監査・proofが未完。 | 主文書§7、統合監査; `results/reviews/integrate-revision22-artifact-audit.json`, `results/reviews/author-revision21-verification.json` |
| SOL-VALIDATION-TIMER-TARGET | fixed: 固定対象ID、cancel/joinと純粋回帰、保存済み実DB観測を保持。 | 詳細§8、実験12; `bench/test_kill_timer.py`, `docs/table-count-integration-audit.md`, `results/reviews/integrate-revision20-artifact-audit.json` |
| SOL-VALIDATION-EVIDENCE-LIFECYCLE | fixed: ハッシュ付き履歴と新規結果を分離し、終了コード不明を成功扱いしない。 | §3/7、証拠保存説明; `results/reference/review-20261107T040818Z-62807/manifest.json`, `bench/raw_evidence.py`, `results/reviews/author-revision16-verification.json`, `docs/table-count-integration-audit.md`, `results/reviews/integrate-revision20-artifact-audit.json` |
| SOL-VALIDATION-TDE-CACHE | fixed: 操作名と読取量を扱い、cache同等性や暗号処理の一律倍率を主張しない。 | §3、詳細§11/13; `docs/table-count-guideline.md`, `docs/table-count-integration-audit.md`, `results/reviews/integrate-revision20-artifact-audit.json` |
| SOL-VALIDATION-UNSTARTED-ARRIVALS | fixed: 全rawを照合し到着会計と打切りを保持。基本216窓に8打切り/1空窓、01a/01b/01d終了時未着手4/6/12をSLO合格から除外。 | 主文書§2/4/7、詳細§18; `results/reviews/integrate-revision22-artifact-audit.json`, `results/reference/review-20261107T100132Z-16034/results/requested_accuracy_service_100.json` |
| SOL-VALIDATION-SERVICE-ACCEPTANCE | fixed: 新service比較は短窓診断と明示。10%のread低下とtail悪化を併記し、method実区間とtrial、精度達成とSLOを区別。 | 主文書§2/4/7、詳細§18; `results/reviews/integrate-revision22-measured-frontier.json`, `results/reference/review-20261107T100132Z-16034/results/requested_accuracy_service_100.json` |
| SOL-FRONTIER-001 | fixed: 標本先行保存と失敗保存のpure回帰を保持。今回01c105結果・対応COUNT完了、予期しない失敗なし。失敗注入の実DB検証とはしない。 | 詳細§18、wide修正仕様; `results/reference/review-20261107T100132Z-16034/results/requested_accuracy_cost_frontier.json`, `results/reviews/author-revision21-verification.json` |
| SOL-FRONTIER-002 | fixed: 01dは確認済み1317タイマー9件を文上限として保存し全arm完了。全体予算到達1件と未知中断を区別。 | 詳細§18、確率推定仕様; `results/reference/review-20261107T100132Z-16034/results/requested_accuracy_service_100.json`, `results/reviews/author-revision21-verification.json` |
| SOL-FRONTIER-003 | fixed: 完了したmatrix要求のstatusにcontainer/data撤去exit0と確認記録あり。最終all・08部分結果や途中armへの遡及保証とは区別。 | 詳細§18、統合監査; `results/reviews/integrate-revision22-artifact-audit.json`, `results/reference/review-20261107T100132Z-16034/results/requested/01d-ACCURACY-SERVICE-100.status.json` |
| SOL-FRONTIER-004 | fixed: disabled lock/lagはnull/not_measuredと能力ラベルで保持。0観測に読み替えない。 | 詳細§18、統合監査; `results/reference/review-20261107T100132Z-16034/results/requested_accuracy_service_100.json`, `results/reviews/integrate-revision22-artifact-audit.json` |
| ROOT-WHITE-ALL-RUBY-BOOTSTRAP | fixed: controller/labともRuby4.0.9で初期照合・要求実験まで起動。今回exit130は所有者によるscope変更中断。 | 主文書§7、詳細§18; `results/reference/review-20261107T100132Z-16034/results/reviews/final-all-run.log` |
| ROOT-RUBY-RUNTIME | fixed: 正確にpinした4.0.9で実起動。システムRuby互換を追加せず全all成功とは区別。 | 主文書§7、詳細§18; `results/reference/review-20261107T100132Z-16034/results/reviews/final-all-run.log` |
| ROOT-RAW-ARTIFACT-SIZE | fixed: 今回raw42chunk/986653行をgzip展開・JSON解析しhash一致を確認。上限付きrawを損失なく保持する根拠でSLO合格ではない。 | 詳細§18、全ファイル台帳; `results/reviews/integrate-revision22-artifact-audit.json` |
| ROOT-FAILED-ALL-SUMMARY | fixed: 今回summaryはfreshなstarted/ran=[]で最終結果なし。24個別statusの完了と全体中断を区別し、古い成功summaryを加算しない。 | 主文書§7、詳細§18; `results/reference/review-20261107T100132Z-16034/results/requested/last_run.json`, `results/reference/review-20261107T100132Z-16034/results/reviews/final-all-candidate.json` |
| ROOT-ALL-SEMANTIC-ERRORS | unresolved: 完了部分の意味と08未完了を区別した。最終scope・全artifact・最終整合・owner audit/proofは未完了。 | 主文書§7、詳細§18; `results/reviews/integrate-revision22-artifact-audit.json`, `results/reference/review-20261107T100132Z-16034/manifest.json` |
| ROOT-SUPERSEDED-UNBOUNDED-PROTOCOLS | unresolved: 旧引退台帳を保持。05/06/07各5scriptは完了したが08補助は終了記録なし、残りmixは未到達。全置換範囲未完。 | 詳細§18; `results/reviews/integrate-revision22-artifact-audit.json`, `bench/requested/retired-protocols.json` |
| ROOT-REVIEW-BUDGET-SCOPE | fixed: aggregate capなしと各script上限を区別。timeoutや部分結果を完了扱いしない。 | §7、修正説明; `results/reviews/final-all-candidate.json`, `results/reviews/white-all-interruption.json`, `docs/table-count-integration-audit.md`, `results/reviews/integrate-revision20-artifact-audit.json` |
| ROOT-INTRA-ARM-BUDGET-PARTIAL | unresolved: 05/06/07の宣言機会は埋まったが、他mixとscale全30scriptの実DB充足は未確認。部分成功で全scopeを閉じない。 | 詳細§18; `results/reviews/integrate-revision22-artifact-audit.json`, `bench/requested/primary-scale-plan.json` |
| SOL-PROTOCOL-001 | fixed: 06の1317はtimer増分1・cleanup確認付きで上限分類。明示driver-timeout probeの2013は別観測でCOUNT ceilingに足さない。異常分岐の根拠はpure/fakeを保持。 | 詳細§18、protocol修正仕様; `results/reference/review-20261107T100132Z-16034/results/requested_06-EXP-O6-WRITE-MIX-indexed-800-statements-b1.json`, `results/reference/review-20261107T100132Z-16034/results/requested_06-EXP-O6-WRITE-MIX-indexed-800-auxiliary-b1.json`, `results/reviews/author-revision18-verification.json` |
| SOL-PROTOCOL-002 | fixed: 05/06/07各5scriptの予定機会と完了/確認上限を照合。未着手scopeと08未完を成功にしない。全範囲検証はROOT-INTRA-ARM-BUDGET-PARTIALとして保留。 | 詳細§18、protocol修正仕様; `results/reviews/integrate-revision22-artifact-audit.json`, `results/reviews/author-revision18-verification.json` |
| SOL-PROTOCOL-003 | fixed: 今回wide各armと完了reqmixの所有session/workload終了確認を保存。旧quiet truth/drift保証は撤回したまま、08途中診断を採用しない。異常cleanupはfake根拠。 | 詳細§18、wide修正仕様; `results/reviews/integrate-revision22-artifact-audit.json`, `results/reviews/author-revision21-verification.json` |
| ROOT-VM-CAPACITY-MAINTENANCE | fixed: 今回resource preflight通過とimage固定を記録。reserve/cache/VM設定は変更せず、完走最小資源の保証をしない。 | 主文書§7、詳細§18; `results/reference/review-20261107T100132Z-16034/results/resource_preflight.json`, `results/reference/review-20261107T100132Z-16034/results/reviews/final-all-run.log` |
| SOL-WIDE-ERROR-001 | fixed: static/serviceの失敗保存・非0伝播・再利用拒否のpure回帰を保持。今回01a/01b/01dはcomplete=true、異常集計空、期待された予算未達を保持。genuine2013の実DB発生は主張しない。 | 詳細§18、wide修正仕様; `results/reviews/integrate-revision22-artifact-audit.json`, `results/reviews/author-revision21-verification.json` |
| SOL-WIDE-ERROR-002 | fixed: 今回01a/01b/01dの各armでmethod/workload/sampler終了確認あり。未着手到着は打切りのまま保存。異常時の継続禁止は実関数/fake回帰、最終proof/査読閉鎖は未了。 | 詳細§18、wide修正仕様; `results/reviews/integrate-revision22-artifact-audit.json`, `results/reviews/author-revision21-verification.json` |
| SOL-ENCRYPTION-STATE-001 | fixed: SQL上の単一整数idは暗号由来でも構造上候補とし、非整数/UUID/二進型/複合を分離。欠番・偏在の精度費用は別条件。 | 主文書§3、確率推定仕様、詳細§19; `results/reviews/sol-encryption-state-review.json`, `results/reviews/integrate-revision22-encryption-coverage.json` |
| SOL-ENCRYPTION-STATE-002 | fixed: 鍵の所在・サーバー読取能力・SQLアクセス・PK型ごとにCOUNT/参考統計/標本と根拠種別を表へ統合。鍵喪失の破壊実験は要求しない。 | 主文書§3、詳細§19; `results/reviews/sol-encryption-state-review.json`, `results/reviews/integrate-revision22-encryption-coverage.json` |
| SOL-ENCRYPTION-STATE-003 | fixed: 現ソースを再読しrunnerのSHAへ対応。accuracy_frontier SHA2389e371…を含む訂正版を作成し、凍結時点の旧coverageと区別。hash置換だけを意味監査にしない。 | 詳細§19、訂正coverage; `results/reviews/sol-encryption-state-review.json`, `results/reviews/integrate-revision22-encryption-coverage.json` |
