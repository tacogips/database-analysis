# MySQL：1億行・100列での MyISAM / InnoDB 全件 COUNT 比較

## 状態と範囲

**所有者の実測待ち。計測値・速度比・本番性能の結論はまだない。** この文書は既存の非ロック検証やガイドラインの受入とは独立した、静止カタログの追加実験である。author と reviewer は Docker・DB・ビルド・計測を起動しない。所有者が author 完了後の新しいセッションで専用 runner を実行する。

```
mise -C mysql/engine-count-100m run all
```

ルートの巨大な `all` は実行しない。実装と受入条件は [専用 README](../mysql/engine-count-100m/README.md)、結果の保存先は `results/mysql-engine-count-100m/runs/<UTC>-<nonce>/result.json`。失敗途中もログと終了コードを保存する。所有者の根拠・最終 source・後始末確認、実測結果の本書への反映、新規セッションの GPT-6.1 Sol 2役による最終敵対的レビューを経て結論を確定する。

## テーブルと投入

比較対象は MySQL 8.4.13 の同一 digest 固定イメージを使う **正確に100,000,000行・100列** のテーブルである。`id BIGINT UNSIGNED NOT NULL PRIMARY KEY` と `c001`〜`c099 INT NOT NULL` だけを持ち、二次索引・圧縮・TDEは設定しない。ENGINE 以外の DDL と値は同じで、id は1〜100,000,000、各 c 列は列番号の定数を全行に入れる。値部分は 8 + 99 × 4 = 404 bytes で、行・ページ・索引の管理領域は別に必要になる。一般の100列、可変長文字列、LOB、暗号化テーブルへは一般化できない。

小さな InnoDB sequence をサーバー内で作り、100,000行ずつ1000回、主キー昇順の `INSERT … SELECT` で投入する。各 INSERT 直後の実際の `ROW_COUNT()` と COMMIT 後の応答境界を1000件保存し、合計1億行、100列の型・NOT NULL・主キーのみ・実際の ENGINE を確認する。MyISAM の START/COMMIT は投入完了の区切りであり、トランザクション性やロールバックを付加しない。途中失敗は成功扱いせず破棄する。投入時間は COUNT と分けて記録する。

## 計測方法

InnoDB を新規データ領域で投入し、通常の `SELECT COUNT(*) FROM lab.count_target` を3回計測する。クライアント EOF と reap、サーバー停止・reap・コンテナ削除・所有データ削除・空き容量確認を終えた後に、MyISAM を別の新規サーバーで同様に実行する。エンジン間でテーブル・サーバー・データを共存／再利用しない。ENGINE の順序は固定なので順序交絡がある。

業務 WHERE のない COUNT(*) だけが対象で、取り消された COUNT(1)、COUNT(column)、WHERE 付き COUNT や他の COUNT 変種は測定しない。追加の巨大な検証 COUNT も実行せず、3回とも1億という実スカラーを投入受領記録と照合する。従来形式の EXPLAIN を別途取得し、メタデータ最適化／走査の実際の計画を確認する。EXPLAIN ANALYZE は実行しない。

同一の永続 mysql CLI に対し、Ruby の整数単調時計 ns で要求書込み前から実スカラーと応答境界の受領までを計る。プロセス起動は COUNT タイマーの外だが、CLIストリーム・Docker exec・Unix socket・制御用境界 SELECT・周期的容量監視のオーバーヘッドを含む呼出側時間であり、DB実行時間そのものではない。3桁丸めでゼロにしない。サーバー側 event 時間は取得せず unavailable と明記する。3標本すべての中央値・最小・最大と生値を保持し、両エンジンが完了し正の中央値を持つ場合に限って呼出側中央値比を計算する。

第1回は「投入後初回」、残りは「反復」とする。データはバッファプールより大きく、OS・VM・ファイルキャッシュは制御せず、投入後のフラッシュも残り得るため cold / fully warm とは呼ばない。FLUSH、ANALYZE、追加索引、エンジン変換、キャッシュ破棄、意図的競合や業務負荷は加えない。計測前後の status counters は診断用の生値で、バックグラウンド処理も含み得る。メタデータ上の表／索引サイズと、停止後のファイルのバイト数・割当バイト数を区別する。MyISAM は .MYD／.MYI、InnoDB はデータとクラスタ化主キーを含む .ibd で、物理形式の管理領域も含む。

## 容量・接続・後始末

VM 内の空き容量が小さいため、大データに Docker named volume は使わない。ホストのリポジトリ内 `.data/engine-count-100m/<乱数owner>/<engine>/stage` を bind し、`data` は初期状態で空、`tmp` は兄弟にする。所有マーカーは datadir の外の private な親に置く。ホストの実 uid:gid でイメージ既定 entrypoint を動かし、削除用 root helper や無関係な権限変更は使わない。初期化の権限エラーは実測前に所有者が専用パスに限定して解消する。

各エンジン開始前にホスト70 GiB以上を要求し、待機中は数秒ごとに安全な argv で `/bin/df -Pk` を実行する。12 GiB未満なら所有作業を中止し、所有資源だけを後始末する。ホストと guest の `/`・`/labdata` の容量、CPU・メモリ、実イメージ ID・参照・アーキテクチャと実バージョンを証跡に残す。ARMネイティブを想定するが、実際の host/daemon/image を照合する。エミュレーションならネイティブ性能の根拠にはできない。空き容量確保のために他のキャッシュ・イメージ・データを削除したり VM を拡張したりしない。

サーバー制限は4 GiB・3 CPU、InnoDB buffer pool 1 GiB、MyISAM key buffer 128 MiB、redo capacity 1 GiB。lab 内のみ binary log を無効にし、他の durability 設定は既定値と実測値を記録する。全工程6時間、投入は各2時間、COUNTは各20分、readiness 120秒、後始末120秒を上限にする。session max_execution_time は対象 SELECT 用で、全作業の上限は controller が持つ。低容量・タイムアウト・SQLエラー・不完全応答を合格にしない。

Docker は固定ユーザーの `.docker/config.json` の現行 context から既存のローカル Unix socket だけを選び、継承した接続・Compose設定の上書きを除く。サーバーは network_mode:none / skip-networking、公開ポートなし、固定の `/labdata/mysql.sock` への専用コンテナ内 CLI 接続だけを許す。実DB・TCP・SSHには接続しない。前後の source hash、run nonceラベル、事前／事後inventory、実際の全CLI／client／container終了コードを残す。所有者が foreground controller を終了まで保持し、ログ末尾と result の数値終了コードを確認する。強制終了やホスト障害の後は自動的に終了確認済みと扱わない。

## 本番の非ロック要件との区別

MyISAM は通常の SELECT にも暗黙の表read lock がある。静止表の比較として扱い、本番の非ロック候補やサービスSLO合格にしない。InnoDB の通常 SELECT は行ロックを取らない一方で MDL は残り、COUNT を組み込む DML のロック特性とも区別する。

この実験には正常業務負荷やフェイルオーバー試験がないため、service budget は常に **UNAVAILABLE** である。完了、1億件という精度、サービス負荷予算、後始末は別々に判定する。3標本・固定順序・投入後フラッシュ・キャッシュ・ホストbindを含む観測から、エンジンだけによる因果的な速度差や本番のゼロ影響を主張しない。

## 実測結果（所有者実行後に反映）

未計測。成功・失敗を問わず run の result.json と完全な raw log を保持し、実環境・投入時間・3回の生スカラーと整数ns・統計値・計画・I/O診断・物理サイズ・終了確認を追記する。数値は当該 run に基づくものだけを記す。
