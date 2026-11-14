# MySQL：1億行・100列の全件COUNTと索引構造の比較

**DBバッファを各回リセットした1億行・100列、4条件各3回の計測を完了した。全12回が100,000,000件を返した。** 静止した専用labの追加実験であり、業務サービスへの影響を測定したものではない。

## 比較条件

同じMySQL 8.4.13で、通常の `SELECT COUNT(*) FROM lab.count_target` を次の順で各3回、計12回実行する。

| 順序 | 条件 | 索引 |
|---|---|---|
| 1 | InnoDB・PKのみ | PRIMARY(id) |
| 2 | InnoDB・単一列の副索引あり | PRIMARY(id)、k_count(c001) |
| 3 | InnoDB・複合副索引あり | PRIMARY(id)、k_count_v2(c001,c002,c003) |
| 4 | MyISAM・PKのみ | PRIMARY(id) |

InnoDBの投入は1回。PK計測後に単一列索引を追加し、実カタログ・計画・計測結果を残す。単一INT列だけでは幅のある索引の参考にならないため、単一列索引を複合索引に置き換えて追加計測する。行データは変更しない。索引の存在とoptimizerが選んだ索引を区別し、FORCE INDEXは使わない。MyISAMはInnoDBの停止・データ削除後、同じ生成式で新規投入する。

各表は100,000,000行・100列。`id BIGINT UNSIGNED NOT NULL PRIMARY KEY` と `c001`〜`c099 INT NOT NULL`。値部分は404 bytes／行で、行・ページ・索引の管理領域は別に必要。idは1〜100,000,000。

- c001：`MOD(id*104729,100000)`。100,000種類。
- c002：`CASE WHEN MOD(FLOOR(id/100000),10)<8 THEN 0 ELSE MOD(FLOOR(id/100000),1000)+10 END`。約80%が0となるブロック状の偏り。
- c003：`MOD(id*99991,1000003)`。より多くの異なる値。
- c004〜c099：列番号の定数。

全列定数の初期設計は実行前に変更しており、その設計での計測値はない。圧縮・TDEは設定しない。固定長INT主体の表であり、可変長文字列・LOB・暗号化・複数副索引など、一般の100列の表すべてを代表しない。

## COUNT前のマニュアル確認

[固定したマニュアル](../results/reviews/mysql-engine-count-100m-cold/manual-sources/mysql-innodb.md)と[取得元・hash](../results/reviews/mysql-engine-count-100m-cold/manual-sources/source.json)に沿い、再起動後の各標本でSQL・生応答・単調時計ns・観測時刻を保存する。

1. information_schema.TABLESのTABLE_ROWS概算一覧。
2. 新規専用接続のserver UUID・connection ID・READ COMMITTED・page size・stats expiry。
3. SHOW INDEXと同じCOUNTのEXPLAIN FORMAT=TRADITIONAL。EXPLAIN ANALYZEは行わない。
4. 使用予定InnoDB索引のmysql.innodb_index_statsのsize・n_leaf_pages・last_updateと、page sizeを掛けた容量参考値。

TABLE_ROWS・CARDINALITY・推定行数はCOUNTの正解ではない。欠落・未初期化はUNKNOWNであり0件ではない。永続統計のページ数は、実読取りページ数、必要I/O量、安全な行数の閾値、所要時間の上限を保証しない。MyISAMにInnoDBのページ統計を流用しない。手動ANALYZEやOS全体のキャッシュ破棄は行わない。

## 計時と検証

サーバー内のsequenceから100,000行×1000回のINSERT SELECTで投入する。INSERT直後のROW_COUNTとCOMMIT後の応答境界を保存し、全1000回が100,000行であることと、各COUNTの100,000,000を照合する。追加の全件COUNTは実行しない。MyISAMのCOMMITはトランザクション性・ロールバック・InnoDB同等の耐久性を付与しない。

投入・2回の索引作成・各再起動はCOUNTと別計時。索引DDLは静止labのfixture作成で、本番への索引新設やDDLの無ロック保証を提案しない。COUNT(1)・COUNT(column)・WHERE付きCOUNTは測らない。

全12標本のそれぞれの前に投入・DDL・前標本・参照用接続を閉じて回収し、所有mysqldを正常停止・再起動する。新規専用のmysql CLIでautocommit=1、READ COMMITTED、lock_wait_timeout=3、max_execution_time=1200000を設定・実値確認する。boot ordinal＋server UUID＋connection IDで識別し、再起動によるconnection ID再利用は許容する。主時間caller_elapsed_nsは要求書込み直前から唯一のスカラー行の完全受信まで。受信時刻はrawログ書出し前に取得する。プロセス起動と直前の同期dfは含めない。full_request_elapsed_nsは別の境界SELECT受領までを保存する。スカラー受信後でも境界前のEOF・エラー・期限超過は失敗標本として保持する。

主時間にもCLI・Docker exec・Unix socket・ストリーム転送と、長い要求中の容量監視が含まれる。DB内だけの実行時間は未取得。短いMyISAM要求ではクライアント・輸送の影響が特に大きい。整数nsを保存し丸めで0秒にしない。runnerによる全条件の確定集計と中央値比は、全12標本の完了・正の時間・cold条件の成立を確認した場合だけ採用する。途中で個別条件の3標本を集計する場合は暫定値とし、全工程の未完了を明記して、未完了条件との比は計算しない。記述統計であり、統計的有意差を示さない。

ユーザー指定のcold-buffer基準は「各COUNT直前に専用DBプロセスを再起動し、DBバッファをリセットする」。投入後の初回・各索引作成後の初回・反復すべてに適用する。同じcontainer ID・datadir・nonceを保ち、100M行を再投入しない。InnoDBは投入boot＋9計測boot、MyISAMは投入boot＋3計測boot。停止した実container終了0とdocker start --attachの終了0・reapを確認してから再起動し、各bootの実PID・StartedAt・uptime・UUID・image・設定・readiness・client終了を残す。停止失敗・強制kill・crash recovery・reap不明はcoldリセット成功にしない。初期投入時からbuffer preload/dumpをOFF、innodb_flush_method=O_DIRECTに固定し、各bootで実global値と通常のfast_shutdown=1を確認する。

coldとは「新しいmysqld、preloadなし、そのbootに対象表の先行全走査なし」であり、対象の全ページが文字通り0枚とは主張しない。毎標本のマニュアル確認前と計測直前に、information_schema.INNODB_BUFFER_PAGEをTABLE_NAME='`lab`.`count_target`'で絞り、INDEX_NAME・PAGE_TYPE別ページ数を保存する。これはバッファメタデータのCOUNTであり、対象表への追加全件COUNTではない。メタデータ確認や背景処理で入ったroot等のページを開示し、全常駐を示す異常値ならcold条件未証明として中止する。MyISAMにInnoDBページ判定は適用せず、再起動と事前Key_*カウンタを参照する。MyISAMの保存済み行数による最適化はquery-result cacheではなく、無効化しない。 DB再起動→手順のメタデータ確認→COUNTという順序で測る。MyISAMでは先行するTABLE_ROWS・SHOW INDEX・EXPLAINによる表情報へのアクセスも含むワークフローであり、その参照による表ヘッダーの再利用はあり得る。前処理なしで初めて表へアクセスするCOUNTの時間には換算しない。

**OS・VM・virtiofs・macOS・デバイスのキャッシュは残存し得る。状態は不明。** O_DIRECTのguest設定は確認するが、SSDの物理読取りやホストキャッシュ消失の証明ではない。SQL_NO_CACHE、FLUSH TABLES、Linux drop_caches、macOS purge、VM再起動は使わない。固定順序、索引作成によるホストキャッシュの温まりも交絡する。比から索引やエンジン単独の因果的速度差、他のネイティブ環境、本番SLOを推論しない。計測前後の論理／物理engine counter差分も背景処理を含む診断用で、SSDの実読取りではない。表／索引のサイズ統計と停止後の物理ファイルサイズも区別する。

## 環境・再現・後始末

ホストは **MacBook Air（Mac16,12）、Apple M4、CPU 10コア（性能4＋効率6）、メモリ32 GB、macOS 26.5.2（25F84）**。ColimaはVZ・virtiofs、Linux ARM64、4 vCPU、Dockerが認識するメモリ8,307,171,328 bytes。MySQLはARM64ネイティブで実行する。ホスト全体の仕様と、下記のコンテナ割当を区別する。[取得したMac・VM仕様](../results/reviews/mysql-engine-count-100m-cold/host-spec.json)は旧実験中に取得した白リスト項目だけを複製したもの。author工程で新規に測定した仕様ではなく、新runnerの実行時にsysctl・sw_vers・Docker info・image/resource実値を再確認する。

```sh
mise -C mysql/engine-count-100m-cold run all
```

[専用runner](../mysql/engine-count-100m-cold/README.md)で再生成する。ルートの大規模allは実行しない。Ruby 4.0.9、MySQL 8.4.13、image digestは `sha256:a81d043930e51745522bd5c430961cf8286f0a23f0a9a2a6e0108f7ea71b0902`。サーバーは3 CPU・4 GiB、InnoDB buffer pool 1 GiB、MyISAM key buffer 128 MiB、redo capacity 1 GiB。labだけでbinlogを無効にする。その他の実設定・実バージョン・image ID・architectureを結果に残す。

データはホストの.data/engine-count-100m-cold/<owner>/<engine>/stageへbindする。実UID:GID、datadir外の所有マーカー、realpath、nonceラベル、実container IDを検査し、所有資源だけを削除する。ローカルUnix Docker endpoint、network none、skip-networking、コンテナ内Unix socketのみ。公開ポートなし、実DBへの接続なし。

ホスト空き容量は各エンジン前70 GiB、索引前24 GiBを要求し、待機中12 GiB未満で中止。全工程6時間、累計投入はInnoDB 2時間・MyISAM 4時間（各投入SQLは2時間）、各索引作成2時間、各COUNT20分、後始末120秒を上限にする。投入期限も全工程の残り時間で制限する。上限は所要時間予測ではない。実際のclient/server/CLI終了コードとreap、事後inventory、所有データ消失、空き容量を確認する。不明・強制終了・失敗を合格にしない。各bootのreadinessは120秒以内。全計測完了かつruntime消失を確認してからstageの表データを削除し、次エンジンへ進む。未完了なら所有runtimeだけを停止・除去し、stage payloadは所有者の確認用に保持する。

runtime SOURCEはlab.rb・reference_flow.rb・compose.yaml・mise.toml・README.md・verify_offline.rbと固定マニュアル・取得receiptの8ファイル。本書は実測反映後に別hashを固定する。生結果・ログ・source before/afterを保存し、新規の生結果と全bootログはresults/mysql-engine-count-100m-cold/runs/<UTC>-<nonce>/へhash付きで保持する。最初のsystem Ruby選択失敗は別途失敗として保持し、実測と混同しない。別セッションのGPT-6.1 Sol 2役で最終査読する。

## 本番への適用範囲

InnoDBの通常SELECTは行ロックを取らないが、MDL・CPU・I/O・キャッシュ競合は残る。INSERT ... SELECT COUNT(*)等のDMLへ埋め込む書き方は読み取り元をロックする場合があり、この結果から安全とは判断しない。

MyISAMの通常SELECTには暗黙の表read lockがある。静止カタログの比較だけで、サービスの非ロック候補ではない。高速化だけを目的とするMyISAM移行は提案しない。

完了・正確値・coldBufferPolicy・サービス負荷予算・後始末は別判定。この実験には業務負荷がなく、サービス負荷予算はUNAVAILABLE。「本番で影響なし」「この行数以下なら安全」は保証しない。サービスの要求が来ない分析用DB・独立コピー・適切なread replicaを選ぶ既存ガイドラインを置き換えない。

## 実測結果

実行開始：2026-11-08T18:36:39Z。終了：2026-11-08T21:31:16Z（UTC）。
次表の単位はms。要求順の全3標本を示す。主時間はクライアントまでの応答時間であり、サーバー内だけの時間ではない。

| 条件 | 使用予定索引（実際の推定計画） | 1回目 ms | 2回目 ms | 3回目 ms | 中央値 ms | 最小〜最大 ms |
|---|---|---:|---:|---:|---:|---:|
| InnoDB・PKのみ | PRIMARY | 275416.380000 | 272020.612000 | 273707.153000 | 273707.153000 | 272020.612000〜275416.380000 |
| InnoDB・単一列副索引 | k_count | 7217.420000 | 5968.324000 | 6263.160000 | 6263.160000 | 5968.324000〜7217.420000 |
| InnoDB・複合副索引 | k_count_v2 | 8747.319000 | 7800.472000 | 8011.625000 | 8011.625000 | 7800.472000〜8747.319000 |
| MyISAM・PKのみ | Select tables optimized away | 0.691000 | 0.564000 | 0.624000 | 0.624000 | 0.564000〜0.691000 |

### TABLE_ROWSとCOUNTの値・取得時間

単一表を対象にしたTABLE_ROWS取得とCOUNTを、同じスカラー受信までの計時で比較する。概数はreference statisticsであり、正確値と混同しない。誤差はこの固定表の100,000,000件に対する符号付き相対誤差。NULLはUNKNOWNのまま残す。

| 条件 | 回 | TABLE_ROWS | 取得 ms | 概数誤差 % | COUNT結果 | COUNT ms |
|---|---:|---:|---:|---:|---:|---:|
| InnoDB・PKのみ | 1 | 87972570 | 0.870000 | -12.027430 | 100000000 | 275416.380000 |
| InnoDB・PKのみ | 2 | 87972570 | 1.078000 | -12.027430 | 100000000 | 272020.612000 |
| InnoDB・PKのみ | 3 | 87972570 | 0.962000 | -12.027430 | 100000000 | 273707.153000 |
| InnoDB・単一列副索引 | 1 | 87972570 | 0.924000 | -12.027430 | 100000000 | 7217.420000 |
| InnoDB・単一列副索引 | 2 | 87972570 | 0.862000 | -12.027430 | 100000000 | 5968.324000 |
| InnoDB・単一列副索引 | 3 | 87972570 | 1.006000 | -12.027430 | 100000000 | 6263.160000 |
| InnoDB・複合副索引 | 1 | 87972570 | 0.909000 | -12.027430 | 100000000 | 8747.319000 |
| InnoDB・複合副索引 | 2 | 87972570 | 0.865000 | -12.027430 | 100000000 | 7800.472000 |
| InnoDB・複合副索引 | 3 | 87972570 | 1.522000 | -12.027430 | 100000000 | 8011.625000 |
| MyISAM・PKのみ | 1 | 100000000 | 1.031000 | +0.000000 | 100000000 | 0.691000 |
| MyISAM・PKのみ | 2 | 100000000 | 1.376000 | +0.000000 | 100000000 | 0.564000 |
| MyISAM・PKのみ | 3 | 100000000 | 0.961000 | +0.000000 | 100000000 | 0.624000 |

TABLE_ROWS自体の取得性能を条件別に集計する。単位はmsで、各条件3回すべてを使った記述統計。接続開始・DB再起動・先行の一覧取得は含まず、要求送信からスカラー受信までのCLI・転送時間は含む。

| 条件 | TABLE_ROWS 最小 ms | 中央値 ms | 最大 ms | 標本数 |
|---|---:|---:|---:|---:|
| InnoDB・PKのみ | 0.870000 | 0.962000 | 1.078000 | 3 |
| InnoDB・単一列副索引 | 0.862000 | 0.924000 | 1.006000 | 3 |
| InnoDB・複合副索引 | 0.865000 | 0.909000 | 1.522000 | 3 |
| MyISAM・PKのみ | 0.961000 | 1.031000 | 1.376000 | 3 |

各TABLE_ROWSのSQL・生応答・整数ns（スカラー／全境界）・取得時刻は各sample.manual_reference.table_rowsに保存した。既存の概算一覧の取得時間はinventoryとして別に保存している。0の参考統計は空表の証明ではなく、この表での誤差を別の表へ保証しない。

### 各回のバッファリセットと残存ページ

| 条件 | 回 | count boot | リセット秒 | 対象cache pages（手順前／COUNT直前） |
|---|---:|---:|---:|---|
| InnoDB・PKのみ | 1 | 2 | 3.288815 | 0／0 |
| InnoDB・PKのみ | 2 | 3 | 2.207920 | 0／0 |
| InnoDB・PKのみ | 3 | 4 | 2.380447 | 0／0 |
| InnoDB・単一列副索引 | 1 | 5 | 3.237685 | 0／0 |
| InnoDB・単一列副索引 | 2 | 6 | 2.855693 | 0／0 |
| InnoDB・単一列副索引 | 3 | 7 | 3.012565 | 0／0 |
| InnoDB・複合副索引 | 1 | 8 | 2.934910 | 0／0 |
| InnoDB・複合副索引 | 2 | 9 | 3.221605 | 0／0 |
| InnoDB・複合副索引 | 3 | 10 | 3.163975 | 0／0 |
| MyISAM・PKのみ | 1 | 12 | 3.382172 | 対象外／対象外 |
| MyISAM・PKのみ | 2 | 13 | 2.474822 | 対象外／対象外 |
| MyISAM・PKのみ | 3 | 14 | 3.060758 | 対象外／対象外 |

全14boot（投入2＋COUNT12）の正常終了・reapと、各COUNT前のpreload OFF／dump OFFを監査する。上記は対象表のDBバッファページで、OS・VM・Macのファイルキャッシュではない。メタデータ取得が触れたページもそのまま開示する。リセット・readiness・メタデータ時間はCOUNTに含めない。全層coldや物理SSD読み出しの保証ではない。

全境界時間も別途保存している。以下の各セルは1〜3回目の整数nsで、丸め前の主時間／全境界時間を示す。

| 条件 | caller_elapsed_ns | full_request_elapsed_ns |
|---|---|---|
| InnoDB・PKのみ | 275416380000, 272020612000, 273707153000 | 275416380000, 272020612000, 273707153000 |
| InnoDB・単一列副索引 | 7217420000, 5968324000, 6263160000 | 7217420000, 5968324000, 6263160000 |
| InnoDB・複合副索引 | 8747319000, 7800472000, 8011625000 | 8747319000, 7800472000, 8011625000 |
| MyISAM・PKのみ | 691000, 564000, 624000 | 691000, 564000, 674000 |

### COUNT前に取得した索引の参考値

| 条件 | size pages | leaf pages | size × page size（bytes） |
|---|---:|---:|---:|
| InnoDB・PKのみ | 2516288 | 2513502 | 41226862592 |
| InnoDB・単一列副索引 | 123455 | 107775 | 2022686720 |
| InnoDB・複合副索引 | 179839 | 157008 | 2946482176 |
| MyISAM・PKのみ | 対象外 | 対象外 | 対象外 |

上記は各条件の第1回に取得した永続統計。実際の走査量・読み出しバイト数ではない。全12回のSQL・生の統計・last_update・取得時間は各sample.manual_referenceに保存した。

### 投入と索引作成（COUNTとは別）

| 工程 | 秒 |
|---|---:|
| InnoDB 1億行の投入 | 1395.042746 |
| 単一列副索引の作成 | 312.210155 |
| 単一列削除と複合副索引の作成 | 394.764126 |
| MyISAM 1億行の投入 | 7450.350661 |

これらの投入時間は1000回の投入・受領・記録処理を含む。索引時間はDDLのクライアント応答時間。いずれもCOUNTの時間には含めていない。

### 観測された中央値比

| 比の分子／分母 | 倍 |
|---|---:|
| InnoDB PK／MyISAM PK | 438633.258013 |
| InnoDB 単一列副索引／MyISAM PK | 10037.115385 |
| InnoDB PK／単一列副索引 | 43.701127 |
| InnoDB PK／複合副索引 | 34.163750 |
| InnoDB 複合副索引／単一列副索引 | 1.279167 |

この比は本環境・固定順序・3標本に限る観測。狭い副索引を使う走査と、幅広いクラスタ化主キーの走査を区別する参考にする。索引新設の推奨、他環境の速度予測、エンジンだけの因果的な速度差には使わない。MyISAMの短い応答にはクライアント・輸送の下限があるため、大きな比もDB内部だけの速度比ではない。

### 根拠と受入の区別

- [新規実行のresult.json](../results/mysql-engine-count-100m-cold/runs/20261108T183639Z-18e383e6549af749ec1c4409b210c4a4/result.json) と同ディレクトリの生ログ。
- [hash付き保存先](../results/reference/mysql-engine-count-100m-cold-20261108T183639Z-18e383e6549af749ec1c4409b210c4a4/manifest.json)。
- [所有者監査](../results/reviews/mysql-engine-count-100m-cold/owner-audit.json)、[最終source・文書の固定](../results/reviews/mysql-engine-count-100m-cold/owner-proof.json)。
- [Mac・VM仕様](../results/reviews/mysql-engine-count-100m-cold/host-spec.json)。

実行完了：passed_two_stages_four_conditions_twelve_counts。正確値：passed_exact_100000000_all_four_conditions。DBバッファ方針：passed_all_12_fresh_process_boots_terminal_clean。後始末：passed_owned_runtime_and_hostdata_absent。サービス負荷予算：UNAVAILABLE。最終査読の合否は独立したreview結果で確認する。

### 過去の未完了参考値との区別

先行するキャッシュ未制御runは、ユーザーのDBバッファリセット指定を受けてSIGINTで中断した。PKのみの3回は275066854000、274050766000、278455706000 nsで、いずれも1億件だったが、新しい比較値には混ぜていない。先行runはstatus=incomplete・exit_status=1で、stage cleanupにはclient EOF/reap timeoutが記録されている。最終的な所有資源の消失を独立確認したことは、実験の完了・成功を意味しない。

- [旧runの中断・資源消失監査](../results/reviews/mysql-engine-count-100m/interrupted-run-audit.json)。
- [旧キャッシュ方針と変更理由](../results/reviews/mysql-engine-count-100m/cache-steering.json)。

cold-buffer版の先行runも、InnoDBの9標本を完了後、MyISAM開始前の容量チェックでexit 1となった。VM共有層がホストで削除済みのファイルを保持しており、対象パスのguest側メタデータ再確認で大きな保持ファイルを解放した。VM再起動・全体のcache purgeはしていない。十分な容量を確保して同じ固定コードを白紙から再実行し、本書の主比較にはその新規runの全12標本だけを使う。

- [容量停止runのhash付き保存](../results/reference/mysql-engine-count-100m-cold-capacity-stopped-20261108T162849Z-0e82949c4ce394e3baab969403f8b6c1/manifest.json)。
- [容量回復の記録](../results/reviews/mysql-engine-count-100m-cold/capacity-recovery.json)／[新規runの削除済みFD確認](../results/reviews/mysql-engine-count-100m-cold/host-deleted-descriptor-cleanup.json)。

容量回復後のrunも、InnoDBの9標本完了後、MyISAM投入の直近50バッチが約7.3秒／10万行となり、累計2時間の投入期限を超える見込みだったため、所有者がSIGINTで停止した。これは実際の期限切れではなく予防的な中断で、exit 1・未完了として保存した。Sol59が期限だけの修正案を作成し、所有者は停止・後始末・保存後に適用した。MyISAMの累計投入を4時間とし、個別SQL2時間・全体6時間・COUNT20分・データ・索引・cache条件は変えていない。主比較はその後の白紙再実行だけで、先行9標本は混ぜない。

- [投入期限見直しによる中断の保存](../results/reference/mysql-engine-count-100m-cold-duration-stopped-20261108T172604Z-ca74a6381f9f93ff0ed6ed7c87471500/manifest.json)／[停止・後始末監査](../results/reviews/mysql-engine-count-100m-cold/load-duration-stop-audit.json)／[Sol修正案の適用記録](../results/reviews/mysql-engine-count-100m-cold/load-duration-owner-application.json)。

さらにその前のsystem Ruby選択失敗ではDB・投入を開始していない。失敗の呼出し記録はresults/reviews/mysql-engine-count-100m/prior-invocation-*に保持した。上記の主比較は、新規に投入して毎回DBバッファをリセットしたrunだけに基づく。


### 同じ生成条件での統計値の変動（参考）

以下はPKのみの第1標本をrun別に示す。先行2件は全体がexit 1で終わった未完了参考であり、主比較や中央値比には含めない。表データは同じ生成式の1億行だが、新規構築した別の表・統計である。

| runの区別 | TABLE_ROWS | 真値に対する誤差 % | 根拠 |
|---|---:|---:|---|
| 先行・容量停止／全体未完了 | 90,586,755 | -9.413245 | [hash付き保存](../results/reference/mysql-engine-count-100m-cold-capacity-stopped-20261108T162849Z-0e82949c4ce394e3baab969403f8b6c1/manifest.json) |
| 先行・投入期限見直しで中断／全体未完了 | 93,121,560 | -6.878440 | [hash付き保存](../results/reference/mysql-engine-count-100m-cold-duration-stopped-20261108T172604Z-ca74a6381f9f93ff0ed6ed7c87471500/manifest.json) |
| 今回の完了run／主比較 | 87,972,570 | -12.027430 | [生結果](../results/mysql-engine-count-100m-cold/runs/20261108T183639Z-18e383e6549af749ec1c4409b210c4a4/result.json) |

統計の同じ値が繰り返し返ることも、固定した行数・行幅・索引設定も、他の表や次回構築の誤差率の保証にはならない。上の少数観測から分布や信頼区間、誤差の上限は作らない。誤差原因を単独に特定した実験ではなく、必要な精度は対応する固定コピーの正確値との校正で確認する。


### 所有資源の削除と共有VMキャッシュの残存

所有DBコンテナ・接続・プロセスの終了と、所有データパスの削除は独立確認した。ホスト上の削除済みファイル参照は別に観測し、対象nonceだけをguest側から読み取り専用で再確認した。共有VMの再起動・全体キャッシュ破棄は行っていない。

再確認後の残存は38本、1,136,656,440 bytes（1.058594 GiB）。分類は `readonly_shared_VZ_known_engine_cache_retained_not_purged`。
参照が残る場合、共有VZプロセスの読み取り専用参照であること、既知のredo・undo・システムファイルだけであること、対象テーブルの.ibd／.MYD／.MYIへの参照がないことと空き容量を監査する。残存がある場合の物理キャッシュ解放は未達であり、所有パス・runtimeの消失と混同しない。これは計測後の観測で、各COUNT前のDBプロセス再起動の証拠とは別である。

[実際の参照一覧・PID確認・空き容量](../results/reviews/mysql-engine-count-100m-cold/host-deleted-descriptor-cleanup.json)。ユーザー指定は「DBバッファを毎回リセットし、OS・Mac側の残存は明記する」。初回の所有者監査は共有VMの削除済み参照ゼロを要求してexit 1になったため、その試行も[hash付きで保存](../results/reviews/mysql-engine-count-100m-cold/before-cache-scope-correction/manifest.json)し、上の区別と残存を最終Sol 2役の査読対象に含める。
