# 少数の代表表による件数推定の校正

[実務ガイド](guidelines/mysql-innodb.md)の補助資料です。校正は**非本番の隔離計測DB**で行います。既存の観測を将来の精度保証やprimaryでの実行許可に置き換えません。

## 1. 代表表を選ぶ前に集めるもの

最初に既存の承認済み台帳・監視エクスポートを確認します。追加SQLが必要なら[ガイド§1](guidelines/mysql-innodb.md)の取得先・予算・新規接続・expiry分岐を先に満たし、対象を小分けにします。TABLE_ROWS、AVG_ROW_LENGTH、DATA_LENGTH、INDEX_LENGTH、CREATE_OPTIONSを記録し、NULL・取得不可・未初期化はUNKNOWNです。
既存のスキーマ台帳やSHOW CREATE TABLEから列数、型、LOB、PKの型・幅、二次索引、partition、暗号化方式を確認します。行データの全走査は不要です。
AVG_ROW_LENGTHは概算、DATA_LENGTHはInnoDBのクラスタ索引の割当概数、INDEX_LENGTHは二次索引の割当概数です。圧縮・外部LOB・ページ空き・索引構成に左右され、COUNTの実読取量ではありません。
`DATA_LENGTH / NULLIF(TABLE_ROWS, 0)`も統計に依存する割当バイト/推定行であり、実測した論理行幅ではありません。0で割らず、欠測時はUNKNOWNを残します。
列数だけで容量や時間を外挿しません。80列でも固定長・可変長・NULL率・外部LOBで状態が異なります。

| 観測軸 | 少数の代表表の選び方／不一致なら |
|---|---|
| 推定行数・割当量・概算行幅 | 自社対象の細い大表、幅広い大表、最大割当量の表を代表にする。最大条件を覆えなければ外挿しない |
| PK・二次索引 | 狭い二次索引あり／なし、整数／複合・文字列PKを分ける。索引が違えばCOUNT費用も別条件 |
| 暗号化・圧縮・LOB | TDE・アプリ暗号化・圧縮・外部LOBを区別。復元可能性とサーバーの鍵利用可否も確認 |
| 統計年齢・変更量 | 既存の保守記録と更新量を確認。大量DELETE、投入直後、再構築後などは別の代表条件 |
| 行幅・キー分布の偏り | 長短行の混在、欠番・島状PKを含む表を優先。均一なfixtureだけに合わせない |
| sample pages・設定・版 | 同じ統計方式・ページ数・対応版か確認。不明なら校正一致と表示しない |
| キャッシュ・書込・資源分離 | 精度条件と費用条件を分けて記録。静止表の時間から稼働中の安全性を認定しない |

代表表は上記の異なる条件を少数で覆うために選びます。全組合せgridの実行は目的にしません。対象と一致しない軸は「未校正」と残します。
統計の更新時刻は保守記録や、既存権限で参照可能な永続統計のlast_updateを使います。TABLES.UPDATE_TIMEだけでは統計年齢は分かりません。
INNODB_TABLESTATSのSTATS_INITIALIZED、MODIFIED_COUNTERは管理者の任意確認です。PROCESSを要するため通常の収集で権限昇格しません。
MODIFIED_COUNTERやsample pages、統計年齢は診断材料であり、分布に依存しない誤差上限を計算する材料ではありません。
TABLE_ROWS、SHOW TABLE STATUS、n_rows、EXPLAINが一致しても、同じ統計に依存している可能性があり独立検証ではありません。

## 2. 観測した最大・最小誤差と条件

以下は[保存済みの統計精度観測](../results/reference/20261107-observations/requested_metadata_accuracy.json)です。今回の新規実行ではありません。元JSONの真値・推定値と[DDL](../sql/01_schema.sql)、[追加DDL](../sql/02_design_variants.sql)、[ローダー](../bench/load.py)、[観測コード](../bench/requested/93-metadata-accuracy.py)を照合しました。DDL・投入条件の照合ハッシュと数値再計算は[著者の検証記録](../results/reviews/author-operational-guide-verification.json)に残します。過去値の参照先をrootの可変JSONへ戻しません。
符号付き誤差は `100 × (推定値 − 真値) / 真値`、絶対誤差はその絶対値です。最大・最小は**表に示す観測集合内**の値で、全表共通の最悪値ではありません。再計算の循環小数は小数6桁表示で、元の真値・推定値を正とします。例えば+38.731333%は正確には+(406679/10500)%、−2.131095%は−(44753/21000)%、標本の+1.154167%は+(277/240)%です。保存された3桁丸め誤差とは区別します。

| 観測集合・極値 | 真値 → 推定値（行） | 符号付き誤差／絶対誤差 | 条件・反復と適用範囲 |
|---|---:|---:|---|
| TABLE_ROWS・ANALYZE前の最大 | t_strpk: 1,000,000 → 1,150,349 | +15.0349%／15.0349% | 有効な非空InnoDB 55表、既定キャッシュ、20 pages、各表1観測。2列、文字列PK、可変長キーが混在 |
| TABLE_ROWS・ANALYZE後の最大 | d_str_bin: 500,000 → 384,385 | −23.123%／23.123% | 同じ55表、既定キャッシュ、20 pages、各表1観測。2列、大小文字の接頭辞を持つ文字列PK |
| 上記2集合それぞれの最小（両phase同値） | tags: 1,000 → 1,000 | 0%／0%（実値一致） | 各phaseで最小の表はtagsのみ。2列、小さい整数PK表。巨大表へ適用しない |
| 行幅偏りfixture・15観測の最大 | req_uneven.t: 2,100,000 → 2,913,358 | +38.731333%／38.731333% | ANALYZE後・キャッシュ無効化読取。20 pagesの5回中2・4・5回目が同値最大 |
| 同fixture・15観測の最小（同値あり） | 2,100,000 → 2,055,247 | −2.131095%／2.131095% | 200 pagesの5回全て、および2000 pagesの1・3・4回目。同表での改善例に限定 |
| pages sweep・120観測の最大 | t_uuid: 5,000,000 → 推定値未保存 | −21.590%／21.590%（保存丸め値） | ANALYZE後、8表×3ページ設定×5回、キャッシュ無効化読取。20 pages・4回目。BINARY(16)ランダムPK＋INT、二次索引なし |
| 同sweepの最小（5同値） | t_part: 10,000,000 → 推定値未保存 | 0.000%／0.000%（保存丸め値） | 2000 pages・5回全て。BIGINT＋DATE複合PK、INT、6 RANGE partition、二次索引なし。行数差0とは断定不可 |

TABLE_ROWSの前後比較は`table_rows_default_cache`の値から再計算しました。元の58表集計にはMyISAM・MEMORY・ARCHIVEの静止カタログ3表が含まれるため、上表のInnoDB極値から除外しています。NULL・取得不可・未初期化統計も候補から除外し、0%として扱いません。空表の相対誤差は定義しません。
元集計のANALYZE前中央値0.208%、10%以内57/58は歴史的な設計集合の記述統計であり、本番の成功確率や個別表の誤差上限ではありません。ANALYZE後に最大が大きくなったため、更新すれば必ず改善するとも言えません。

条件の読み方は次のとおりです。

- t_strpkは`VARCHAR(40) utf8mb4_general_ci`のPKとINTで、数字文字列・`Ab`・`Äb-`・`z… x`を投入。d_str_binは`VARCHAR(40) utf8mb4_bin`のPKとINTで、`A/a`＋数字を投入。tagsは`INT UNSIGNED`のPKと`VARCHAR(32)`に`1…1000`と短い名前を投入。いずれも二次索引なし。宣言幅は実際の行バイト幅ではありません。
- 偏りfixtureはBIGINT PKと`VARCHAR(7000) NOT NULL`、二次索引なし。**200万行がbody='x'、10万行が6000文字**で、PK順に短行群・長行群を投入しています。観測コードの「half/half」というdocstringよりINSERTループを優先しました。20 pagesでは+4.560429〜+38.731333%、200 pagesは5回とも−2.131095%、2000 pagesは−2.811905〜−2.131095%。ページ数を増やした個々の結果は単調には改善しません。
- sweepは各ANALYZE後に`information_schema_stats_expiry=0`で読み、誤差だけを小数3桁に丸めて保存しています。t_partの0.000%は実値一致を確認したtagsの0%とは別です。t_uuidはMD5由来のキーをバッチ内でソート投入しています。
- 上記メタデータfixtureのDDLにはTDE指定がありませんが、当時の実効暗号化状態・物理キャッシュ状態・統計年齢・実測行幅をこのJSONから確定できません。既定キャッシュは統計キャッシュの読取設定であり、cold/warmの証明ではありません。PK、行幅、partition、暗号化の**単独の因果効果を分離した実験ではなく**、文字列PKなら常に悪い、小表なら常に0%、2000 pagesなら安全とは言えません。

エンジニアは長短行の混在、キーの偏り、統計更新前後を別の代表条件にして校正します。小さい均一表の一致や反復の安定だけで巨大な別表を合格にしません。事前統計だけで「必ず10%以内」と証明する方法は、この観測からは得られていません。

### 新しい縮小白紙実行の統計（旧集合と別）

[新93の元JSON](../results/reference/review-20261107T155750Z-26743/results/requested_metadata_accuracy.json)と[所有者の新極値監査](../results/reference/owner-acceptance-20261107T161659Z/reports/owner-essential-fresh-metadata-extrema.json)に対応します。同じ55非空InnoDB表、既定キャッシュ・20 pages、各phase各表1観測です。

| 新観測集合・極値 | 真値 → 推定値（行） | 符号付き誤差／絶対誤差 | 条件・同値 |
|---|---:|---:|---|
| ANALYZE前の最大 | d_str_bin: 500,000 → 621,877 | +24.3754%／24.3754% | 旧前集合の最大より大きい。文字列PK、二次索引なし |
| ANALYZE後の最大 | t_strpk: 1,000,000 → 1,195,881 | +19.5881%／19.5881% | 旧後集合の最大とは表・符号とも異なる |
| 前後それぞれの最小 | tags: 1,000 → 1,000 | 0%／0%（実値一致） | 各phaseの最小はtagsのみ |

新しい行幅偏り15観測でも最大は+38.731333%／38.731333%（20 pagesの2〜5回目、2,913,358行）でした。最小は−2.131095%／2.131095%（200 pagesの5回全て、2,055,247行）。2000 pagesは5回全て−2.811905%／2.811905%で、旧集合の同値条件と異なります。ここでも更新・ページ増量は精度保証になりません。

### 別の履歴：初回縮小実行の静的メタデータ

[初回93の保存値](../results/reference/review-20261107T142407Z-3330/results/requested_metadata_accuracy.json)では、ANALYZE後のd_str_binは真値500,000行に対し361,075行、符号付き誤差−27.785%でした（[manifest](../results/reference/review-20261107T142407Z-3330/manifest.json)、[初期COUNT](../results/reference/review-20261107T142407Z-3330/results/data_check.json)、[93のexit0](../results/reference/review-20261107T142407Z-3330/results/requested/93-metadata-accuracy.status.json)を照合）。上記の旧集合・新集合とは別の静的COUNT/統計比です。初回のサービス壁時計窓は却下されており、この比率はサービス性能/SLOの証拠でも、却下された実行の昇格でもありません。

### 統計メタデータとは別：条件付き標本prototypeの極値

[旧105静的fixture観測の元データ](../results/reference/review-20261107T123436Z-66065/results/requested_accuracy_cost_frontier.json)の真値と保存推定値から再計算しました。更新中サービスの真値とは混ぜません。

| 観測集合・極値 | 真値 → 推定値（行） | 符号付き誤差／絶対誤差 | 条件・反復と採否 |
|---|---:|---:|---|
| target_reached 75観測中の最大 | uneven_density: 2,250,000 → 2,275,968.75 | +1.154167%／1.154167% | 10%目標、seed=991（5回目）、640 draws。前半密・後半CRC32で約半数保持、幅200のセル。区間目標到達時の実現誤差 |
| 同集合の最小（45同値） | dense / negative: 250,000 → 250,000、wide: 3,000,000 → 3,000,000 | 0%／0%（実値一致） | 各fixture×3目標×5 seeds。密な連続整数PKで全セル200行、標本セル分散0。3/5/10%目標でそれぞれ512/320/160 draws |
| 未達30観測も含む105観測中の最大 | islands: 250,000 → 0（途中推定） | −100%／100% | 3目標×5 seeds全て8192 drawsでもprecision_not_reached。PK二群の間に100億の空隙。**採用推定値ではなく、要求精度を満たす値はUNKNOWN** |

静的標本はMySQL 8.4.13、InnoDB TDE、BIGINT PK＋VARBINARY、二次索引なし。dense/negative/islandsのpayloadは32 bytes、wide/uneven_densityは1000 bytesの合成値です。negativeは負数を含む連続PKで、負数自体の効果を示しません。各方式前後のCOUNTや実行順のキャッシュ持越し、host/VMキャッシュは未制御です。
[新105静的観測](../results/reference/review-20261107T155750Z-26743/results/requested_accuracy_cost_frontier.json)でも75到達・30未達、到達集合最大+1.154167%／1.154167%、実値一致の最小45同値、未採用islands最大−100%／100%でした。旧値を新実行で上書きせず、同じ結果だった集合として区別します。
5固定seedsは将来の被覆率の実証ではありません。実現誤差0%でも区間幅は0ではなく、標本数・統計sample pagesの数だけで精度を保証できません。標本の適用条件・停止規則を満たしたかを別判定します。

## 3. 非本番で真値と照合する

1. 所有者の承認済みbackup/replica・構築手順と準備作業の負荷予算を使い、資源を隔離した固定時点の少数代表表を用意します。backup取得や新設自体のprimary影響も管理します。復元位置、版、索引、統計設定、暗号化、割当量を記録します。
2. **何の統計を校正するか**を先に決めます。復元表の統計なら、更新を止めた対象でANALYZE等の変更前の統計と通常COUNTを対応付け、「復元表の校正」と記録します。SQL・監視・取消・回復確認はガイドの隔離先手順に従います。
   元の本番キャッシュ統計を評価するには、その統計の取得時点・対象世代と一致する行集合の真値が必要です。本番から既存の承認済み経路で採取した統計・時刻と、その位置の隔離snapshotの真値を対応付けます。一致を確認できなければ未校正です。
   論理restore、再構築、索引再作成、ANALYZEや統計再生成はページ配置・統計を変えます。物理復元でも統計の保存・更新状態を確認します。**復元後統計の誤差上限を、元の古い本番キャッシュ統計へ転用しません**。同じ表名・同じ行数だけでは対応を証明できません。
3. 真値Nが正なら符号付き誤差を`100 × (推定値 − N) / N`、絶対誤差をその絶対値として記録します。N=0は相対誤差を定義せず、空表の一致を別判定します。
4. 精度、実行時間、I/O、未完了、後始末を別欄に保存します。統計が0でも真値を確認するまで空表と認定しません。
5. 必要な場合だけ隔離先でsample pagesやANALYZEの前後を比較します。複数回の誤差範囲と費用を残し、primaryへ自動適用しません。

受入規則は事前に決めます。例えば「10%程度の経験的見込みでよい」なら、対象と代表表の上記条件が一致し、対応する観測が全て許容範囲内である場合だけ、観測範囲を添えて経験値として受け入れます。
代表条件で一度でも要求を超えた、条件が不明・不一致、大量更新後、未初期化の場合はその校正を棄却します。粗い参考値を許容するか、隔離先のCOUNTへ進みます。
**誤差上限が契約上必須なら、少数例が全て合格しても統計だけでは採用しません。** 要求時点を満たす隔離先の正確COUNT、または条件を満たす別の認定方式が必要です。
費用の受入れも別判定です。精度合格でもサービス・I/O・遅延・DDL予算を超える取得は採用しません。取得先がなければ設備・精度・鮮度のいずれかを見直します。

## 4. 代表fixtureと条件付き標本の位置付け

[旧初期件数](../results/reference/review-20261107T123436Z-66065/results/data_check.json)で確認した1億行の細い表、75万行の50/100列TDE表は別の条件です。**1億行×80列×TDEは未計測**であり、組み合わせた費用・容量・誤差は推定していません。
旧50/100列結果は、合成payload・限定的なUPDATE負荷の観測です。列数やTDEの因果効果、本番の全条件は保証しません。
対応する[50列結果](../results/reference/review-20261107T123436Z-66065/results/requested_probability_wide_50.json)と[100列結果](../results/reference/review-20261107T123436Z-66065/results/requested_probability_wide_100.json)の`sizes`には、MySQL 8.4.13のTABLES割当量が残っています。

| TDE・75万行の条件 | DATA_LENGTH（bytes） | INDEX_LENGTH（bytes、二次索引なし／あり） |
|---|---:|---:|
| 50列 | 1,367,343,104 | 0／19,447,808 |
| 100列 | 3,076,521,984 | 0／19,447,808 |

対象の割当量・PK・索引・統計方式・更新条件をこの記録に照合します。列数が近いだけでは一致としません。これらは割当量の観測であり、性能を保証する許容範囲や80列への補間式ではありません。
同じ条件を確認できない場合は費用未校正とし、代表復元表での確認か資源を隔離したCOUNTへ進みます。100M薄い表の件数確認から幅広い表の費用を認定しません。
[旧105静的観測](../results/reference/review-20261107T123436Z-66065/results/requested_accuracy_cost_frontier.json)では標本prototypeの目標到達75、精度未達30。固定seedの反復を将来の被覆率の実証とはしません。
[旧48サービス観測](../results/reference/review-20261107T123436Z-66065/results/requested_accuracy_service_100.json)では、3%目標は12機会中5到達・7予算切れ、10%目標は12到達、COUNTは3完了・9時間上限でした。未達・打切りを精度成功や完了時間として扱いません。
[新48サービス観測](../results/reference/review-20261107T155750Z-26743/results/requested_accuracy_service_100.json)では、3%・10%目標とも各12到達、COUNTは6完了・6時間上限でした。標本は全24推定が750,000行の真値と一致し、実現誤差0%でしたが、密な固定fixture・限定UPDATE負荷での観測です。新50列の標本12到達とも整合します（[新50列結果](../results/reference/review-20261107T155750Z-26743/results/requested_probability_wide_50.json)）。旧48の到達率を新しい結果へ書き換えず、予算・実行環境の差がある観測として残します。
旧サービス窓の壁時計来歴は性能/SLO根拠に採用しません。新しい単調時計窓も疎・打切り観測があるため、短いクエリが低いサービスp99を意味するとは限りません。5%目標のサービスarmは未計測です。

### COUNTと標本の小さな費用比較

以下は**旧105静的集合**の`cases.<fixture>.runs`から、各目標5反復の`comparison_count.seconds`と`seconds`の中央値を別々に再計算したものです（[固定元JSON](../results/reference/review-20261107T123436Z-66065/results/requested_accuracy_cost_frontier.json)）。全て目標到達した反復だけの集合で、未達を落として順位を作ったものではありません。

| 固定fixture（TDE、二次索引なし） | 精度目標 | 正確COUNT中央値（秒） | 標本中央値（秒） |
|---|---:|---:|---:|
| dense・250,000行、payload 32 bytes | 3% | 0.0074 | 0.0453 |
| wide・3,000,000行、payload 1000 bytes | 3% | 8.0437 | 0.2483 |
| 同wide | 10% | 8.0285 | 0.0834 |

COUNTは1文の実行、標本は用意済み接続への最初のSQLからROLLBACKまでを含み、計測区間は同一ではありません。版8.4.13、buffer pool 256 MiB、parallel read threads=1で、実行順・先行COUNT・host/VMキャッシュは未制御です。索引・行幅・キャッシュの因果分離や普遍的な順位ではありません。
新105でもdenseの3%はCOUNT 0.0076秒／標本0.0518秒、wideの3%は7.6753秒／0.2287秒、10%は7.9857秒／0.0894秒という同じ向きの中央値でした（[新元JSON](../results/reference/review-20261107T155750Z-26743/results/requested_accuracy_cost_frontier.json)）。いずれも自社の時間予測には使いません。
隔離した取得先で通常EXPLAINと既存索引を確認し、既定COUNTを候補にします。狭い既存索引なら正確COUNTが標本に勝つ場合があり、全走査が高価で標本の全受入条件を満たす場合には標本を比較します。EXPLAINだけで実際の読取や安全性を認定せず、COUNTのためだけのprimary索引新設や、推定行数による無制限primary走査へ進みません。
標本を採用するなら[prototype仕様](probability-count.md)の固定snapshot、一意整数PK、セル上限、無作為抽出、停止規則と誤差確率配分を全て確認します。区間認定と今回の実現誤差を分け、未達時は要求精度を満たす値をUNKNOWNとし、途中推定値があれば未認定の別欄に残します。
この条件を満たせない表では参考統計か隔離先COUNTへ戻ります。標本はprimary全表台帳の既定ではありません。

## 5. 証跡と受入条件

[過去統計manifest](../results/reference/20261107-observations/manifest.json)、[旧105/48/50/100列snapshot](../results/reference/review-20261107T123436Z-66065/manifest.json)、[新白紙all snapshot](../results/reference/review-20261107T155750Z-26743/manifest.json)を分けます。引用ファイルのSHA-256と数値検算は[今回の著者検証](../results/reviews/author-operational-guide-verification.json)に保持します。引用済み過去ファイルはroot resultsの再生成で置き換えません。
[所有者proof](../results/reviews/final-all-proof.json)と[固定監査bundle](../results/reference/owner-acceptance-20261107T161659Z/manifest.json)は、縮小範囲の白紙allのexit0・意味/source・整合・後始末を認めています。105の30未達やCOUNTの上限は残り、サービス予算/SLOは未認定です。scopeの静的`planned_not_db_executed`は事前宣言で、実行状態はproofで判断します。
初回縮小実行の壁時計逆転は[所有者の却下記録](../results/reference/owner-acceptance-20261107T161659Z/reports/owner-essential-semantic-rejection.json)に保存し、affected/時計不明の窓を性能根拠へ昇格しません。旧gridの除外はユーザー指定による範囲変更であり、技術的成功や査読閉鎖ではありません。
新91の3版ではTDE鍵利用可のplain SELECTを確認しています（[8.4.13](../results/reference/review-20261107T155750Z-26743/results/requested_encrypted_count_8.4.13.json)、[9.7.4](../results/reference/review-20261107T155750Z-26743/results/requested_encrypted_count_9.7.4.json)、[26.10.0](../results/reference/review-20261107T155750Z-26743/results/requested_encrypted_count_26.10.0.json)）。cold/warmの等条件や鍵喪失時の復元性能は検証していません。保存された`preflight.recommendation`は旧lab診断であり本番許可ではありません。
[過去指摘台帳](../results/reviews/finding-register.json)は履歴として保持し、重要な精度・予算・来歴・後始末の懸念は現ガイドとproofで再評価します。[今回の処置台帳](../results/reviews/author-operational-guide-dispositions.json)は著者提出であり、最終Solの閉鎖判定ではありません。benchmarkの実行証明だけでは文書を受け入れません。文書の受入れは、執筆とは別セッションで完了した最終Sol 2役それぞれのレビューが最終文書のSHA-256と所有者proofに一致し、[文書受入記録](../results/reviews/final-operational-review-acceptance.json)に受入れが記録されることを条件とします。

公式仕様: [TABLESの推定値・割当量](https://dev.mysql.com/doc/refman/8.4/en/information-schema-tables-table.html)、[TABLESTATSとPROCESS](https://dev.mysql.com/doc/refman/8.4/en/information-schema-innodb-tablestats-table.html)、[統計関連設定](https://dev.mysql.com/doc/refman/8.4/en/innodb-parameters.html)。
