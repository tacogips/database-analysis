# 追加実験

レビューで要求された実験を置きます。`mise exec -- ruby lab.rb requested` が、新規または編集されたスクリプトだけを `lab-bench` コンテナ内で実行し、結果を `results/requested/` と `results/requested_<スクリプト名>.json` に保存します。

## 実行順と制限

- `lab.rb`が`.lab.lock`を所有して名前順に実行します。接続先はComposeのprimary/replicaと、所有するmatrixサーバーだけです。
- 固定runnerは白紙DBから`mise run all`を実行します。1スクリプト20分、ワークフロー12時間で、allには集計スキップ予算を設けません。診断用guardの終了コード3は成功ではありません。
- requestedの再利用には成功終了コード、依存・設定・immutable imageのfingerprint、実image ID、必須成果物SHAが必要です。mtimeだけでは判断しません。resetはreference/reviewsを保持し、それ以外の生成結果を消すため、白紙allでは過去結果を再利用しません。
- 節エラーは保存して終了コード1へ伝播します。部分出力、予定反復不足、予算到達、精度未達、後始末は別判定です。matrixコンテナとデータの撤去はrunnerが別記録を残し、失敗を成功にしません。
- 0g〜0kは各条件をcore（PRIMARY単文と分割）/secondary（二次索引単文）へ分割し、各3反復を別スクリプトにしました。元名はcore反復1、`-core-r2/r3`と`-secondary-r1/r2/r3`が残りです。300秒/150秒の上限と元の仮説を維持し、二度上限に達したことによる省略を廃止します。各スクリプトの完走と全30スクリプトの充足は別です。
- core/secondaryごとに表を再生成し容量を再校正します。方法順を反復で回し、対照の前後も交替します。対照の生データから方法と同じ長さの窓を作り、不足も明記します。warmupと回復の待ちはキャッシュ同等性・完全回復の証明ではありません。
- 3e/3fの無上限プロトコルは`.py.retired`として保存し、実行対象から外しました。SHA・置換05〜08・仮説は`retired-protocols.json`にあります。置換先の全範囲・意味上の成功は所有者が監査します。11と60〜63の歴史的置換記録も新測定ではありません。
- 90〜94、96〜98は過去観測と今回の再実行を分けます。92の旧64観測は終了コード不明です。01a/01bは50/100列の標本、01cは3/5/10%静的コスト、01dは100列TDEで対照/正確COUNT/3%/10%のサービス診断です。新しい01c/01dはDB実行待ちです。
- 最終候補は`results/reviews/final-all-candidate.json`、完全ログは`results/reviews/final-all-run.log`に残ります。文書反映後の所有者監査まで最終proofを作りません。

## スクリプトと要求の対応

| スクリプト | 答える要求 |
|---|---|
| `10-EXP-O2-1-all-tables` | EXP-O2-1, EXP-SOL-3, FIND-PREFLIGHT-01 |
| `3c-EXP-O2-1-versions`(5 版、`11` の後継) | EXP-O2-1, EXP-R2, EXP-R3, EXP-R6, EXP-7(設計), FIND-R3-LOOPEDGE-01 |
| `12-EXP-6`, `3b-EXP-6-monitor` | EXP-6, FIND-R3-HINT-01, EXP-S6-MONITOR-RECHECK, EXP-O6-THREADS-SCOPE |
| `13-EXP-O2-2` | EXP-O2-2, EXP-SOL-8, FIND-R2-KILL-01 |
| `3a-EXP-O6-LOOP-RULES` | EXP-O6-LOOP-RULES, FIND-STEP-01, FIND-R6-MDL-AS-TIMEOUT, FIND-O6-WALK-CEILING-01 |
| `14-EXP-SOL-1` | EXP-SOL-1, EXP-SOL-2 |
| `20-EXP-R7` | EXP-R7, FIND-LAB-CONTAM-01 |
| `21-EXP-R8-a`, `22-EXP-R8-b` | EXP-R8, FIND-FAST-01 |
| `23-EXP-R5` | EXP-R5, FIND-DRIFT-01 |
| `24-EXP-R10` | EXP-R10, FIND-EST-01 |
| `25-EXP-R15` | EXP-R15, FIND-REPL-01 |
| `3i-EXP-S6-REPLICA-FAILURE-CLEANUP` | EXP-S6-REPLICA-FAILURE-CLEANUP |
| `26-EXP-R4-dense`, `27-EXP-R4-wide` | EXP-R4, FIND-PK-01 |
| `30`〜`35`(`EXP-R9-rep*`), `36`, `37`(`EXP-R9-control-*`) | EXP-R9, FIND-IMPACT-01, FIND-O6-NOISE-FLOOR-01 |
| `38`, `39`(`EXP-O6-CPU-CONTROL-*`) | EXP-O6-CPU-CONTROL, FIND-O6-INSTRUMENT-01 |
| `39a-EXP-O6-NOISE-WINDOWS` | EXP-O6-NOISE-WINDOWS, FIND-O6-NOISE-FLOOR-01 |
| `3d`(非索引列), `05`〜`09a`(`EXP-O6-WRITE-MIX-*`、第 2 の設計、`3e`, `3f` の後継) | EXP-O6-WRITE-MIX, FIND-O6-WRITE-SHAPE-01, FIND-O8-CLIENT-TIMEOUT |
| `0c-EXP-S8-REPLICA-STOP-LONG` | EXP-S8-REPLICA-STOP-FAILURE(再開分), FIND-S8-REPLICA-STOP-FAILURE-01 |
| `0d-EXP-O10-INDEXED-100-STALL` | EXP-O10-INDEXED-100-STALL, FIND-O10-INDEXED-100-STALL |
| `0e`, `0f`(`EXP-O10-ROW-CEILING-*`)、`3a` | EXP-O10-ROW-CEILING, FIND-O10-STEP-CEILING |
| `0g`〜`0k`と各core/secondary反復（計30本、8.4.13の小さいサーバー） | EXP-O10-PRIMARY-SCALE, FIND-O10-PRIMARY-SCALE |
| `0l-EXP-O10-NOPK-FORCE` | EXP-O10-NOPK-FORCE, FIND-O10-NOPK-FORCE |
| `0a`, `0b`(`EXP-O8-PKONLY-THREADS-*`) | EXP-O8-PKONLY-THREADS, FIND-O8-PKONLY-PARALLEL |
| `01-EXP-S8-ENUM-DOMAIN`(3 版) | EXP-S8-ENUM-DOMAIN, FIND-S8-ENUM-DOMAIN-01 |
| `02-EXP-S8-REPLICA-STOP-FAILURE` | EXP-S8-REPLICA-STOP-FAILURE, FIND-S8-REPLICA-STOP-FAILURE-01 |
| `03-EXP-S8-REPLICA-CHANNEL-GATE` | EXP-S8-REPLICA-CHANNEL-GATE, FIND-S8-REPLICA-CHANNEL-01 |
| `04-EXP-O8-1205` | EXP-O8-1205, FIND-O8-1205 |
| `3g`, `3h`(`EXP-O6-LONG-UNDO-*`) | EXP-O6-LONG-UNDO, FIND-O6-LONG-UNDO-01 |
| `40-EXP-R11` | EXP-R11, EXP-5, FIND-LOCK-FORMS-01 |
| `41-EXP-R12` | EXP-R12, FIND-MDL-FLUSH-01 |
| `42-EXP-4` | EXP-4 |
| `43-EXP-SOL-12` | EXP-SOL-12, FIND-MDL-TRIAL-01 |
| `44-EXP-R13` | EXP-R13, FIND-PRIV-01 |
| `45-EXP-SOL-6` | EXP-SOL-6, FIND-SOL-MONITOR-01 |
| `46-EXP-SOL-7` | EXP-SOL-7, EXP-R14, FIND-SOL-DDL-01, FIND-RESUME-01 |
| `48-EXP-SOL-4` | EXP-SOL-4, FIND-SOL-VIEW-01 |
| `49-EXP-SOL-5` | EXP-SOL-5, FIND-SOL-COUNTER-01 |
| `50-EXP-SOL-11` | EXP-SOL-11, FIND-LOCK-SAMPLER-01 |
| `51-EXP-3` | EXP-3 |
| `52-EXP-R16`, `52a-EXP-S6-INCREMENTAL-SAME-SECOND` | EXP-R16, EXP-S6-INCREMENTAL-SAME-SECOND, FIND-BETTER-01, FIND-S6-INCREMENTAL-DETECT-01 |
| `53-EXP-R1` | EXP-R1, FIND-KEY-ENUM-01 |
| `54-EXP-SOL-9` | EXP-SOL-9, FIND-BETTER-01 |
| `55-EXP-SOL-10`, `70-EXP-SOL-10-restart` | EXP-SOL-10(再起動の要求は取り下げ済み。`70` は再実行しない) |
| `56-EXP-7-cpu`, `57-EXP-7-impact`(3 版) | EXP-7(CPU 飽和、主要影響) |
| `58-EXP-O2-3`(5 版) | EXP-O2-3, FIND-R2-CASCADE-VER-01 |
| `59-EXP-O6-ANALYZE`(3 版) | EXP-O6-ANALYZE, FIND-O6-ANALYZE-01 |

| `01a`, `01b`, `01c`, `01d` | SOL-ROLLOUT-ACCURACY/ENCRYPTED-WIDE, SOL-SAMP-98-COMPARISON, SOL-FRONTIER-001〜004、3/5/10%の精度・コスト・負荷比較 |

PRIMARY-scaleのraw計装は収集終了後にlossless gzip JSONL chunkへ分割します。仕様・SHA/件数検証・snapshotからの読出しは[README](../../README.md#生の計装データ)参照。`mise.toml`も再利用fingerprintへ含めます。固定runnerは前後summary署名で古い結果を除外し、前回candidate/logを保存します。

## partitioned-v3 write-mix

`write-mix-plan.json`は05〜09a、0e/0fの全4反復を方式群・2反復blockへ分割し、drift診断/client disconnect probeを補助scriptへ分離します。0dの5対照/PRIMARY分割pairは各scriptへ分け、共通main比較は05を参照します。各scriptは同じ20M行の私有fixtureを再構築します。元の150秒split・120秒statement上限は保持し、two-hitによる省略は使いません。scaleの別計画300秒/150秒、3/5/10%の105 static・48 serviceも変更しません。

`opportunities`をsetup前に保存し、全機会のoutcomeと未着手理由を残します。予算不足・未確認中断・2013・計画拒否・cleanup失敗は非0です。所有接続のrollback/close、別接続からの終了確認と必要なKILL CONNECTIONを保存します。確認できないscriptの後はrunnerも次実験を開始しません。旧quiet truth/write数によるdrift境界は採用しません。詳細は`docs/table-count-protocol-repair.md`へ。

現行defaultは`../nonlocking-scope.json`に列挙した144スクリプト・版別158プロセスです。COUNT候補のロック取得と意図的な競合注入を除外し、通常の業務更新・静的精度・TDE SELECT比較は維持します。`.py.retired`のユーザー除外ソースは実行入口を無効化してあり、要求成功として数えません。混在scriptの除外sectionは同台帳を参照してください。
