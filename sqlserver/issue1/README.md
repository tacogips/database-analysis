# Issue 1 専用機能検証（所有者用）

この runner は Issue 1 の識別・取消／回収・自動統計の条件を確認するための**実装**です。実行証跡と承認状態は[現行受入記録](../../results/reviews/issue-1/acceptance.json)で対象hashを照合してください。author は Docker/DB/計測を実行しません。利用者がこのlabを実行する必要はありません。

所有者はリポジトリrootから前景で実行します。

```sh
cd sqlserver/issue1
mise run all
```

必要条件は Ruby 4.0.9、Docker Compose v2、既存ローカルUnix socket context、ローカルに取得済みのSQL Server digestと`mcr.microsoft.com/dotnet/sdk:10.0`です。controllerは`~/.docker/config.json`のcurrentContextからUnix endpointだけを導出し、継承されたDOCKER_HOST/DOCKER_CONTEXT/DOCKER_CONFIG/TLS/Compose上書きを使いません。TCP/SSH contextは拒否します。SQL接続先・DSN・接続文字列・credentialの外部指定は受け付けません。

SQL Server 2025 CU10とSDK 10.0はDockerfile/composeのdigestで固定し、実際のID/RepoDigestsを記録します。.NET 10 / Microsoft.Data.SqlClient **[7.1.1]**、NuGet sourceはnuget.orgだけです。間接依存Pkcsの既知警告を受け、修正版 **[10.0.14]** を明示し、NuGet脆弱性警告はbuild失敗とします。所有者が確認した`packages.lock.json`をsourceに保持し、全buildを`--locked-mode`で復元します。lockやimage/source変更後は専用allと再査読が必要です。初期buildの警告・修正経緯は所有者の証跡へ残します。

固定project/serverは`lab-sqlserver-issue1`、clientは`lab-sqlserver-issue1-client`、volumeは`lab-sqlserver-issue1-data`、networkは`lab-sqlserver-issue1-network`です。名前が既存なら削除せず開始拒否。ローカルlifecycle lockを全期間保持し、compose createはno-recreateで既存containerを置き換えません。公開port・host network・外部接続・Docker socket mountはありません。runtime networkはinternalです。build時だけNuGet通信を使います。公開の使い捨てlab passwordを使用し、鍵・行データを証跡に保存しません。

controllerがcompose **create**後のserver/clientの全寿命を所有し、SQLは`docker start`、clientは`docker start --attach`で動かします。shellの背景実行や孤児サービスを作りません。Dockerサービスはcontrollerの管理下でのみ存続し、子CLIを前景で待機・期限超過時reapし、ensureで当該run labelに一致する固定container/volume/networkだけを削除・不在確認します。global pruneや既存labの停止はしません。異常時もcleanupはbest effortで全資源を確認し、失敗・daemon喪失・強制終了時はUNKNOWNを残します。OSからcontrollerへのSIGKILL/電源断まで後始末を保証はできません。再実行で既存名前を消さず所有者へ返します。

通常工程540秒、cleanupを含む総期限約600秒、SQL memory上限3072MB/container4GiB/CPU2、client2GiB/CPU2。これはlabのCPU/memory制限でI/O/network隔離を認定しません。ARM64 Colima上のamd64 SQL ServerエミュレーションはMicrosoft非サポート構成で、機能観測だけです。native性能、本番SLO、巨大COUNTの取消時間・無劣化を証明しません。

## 観測と判定

- `CountIssue1`は新規DBだけ。既存なら拒否。RCSI/SNAPSHOTをlab作成時に有効化。disk rowstoreの既知4096行（InnoDBはSQL Serverへ該当しない）。通常COUNT_BIGだけを採用し、snapshot materialize後の小UPDATEでversionを生成します。
- 新規`Pooling=false; Enlist=false; MultipleActiveResultSets=false`のownerと別control接続で、サーバーのSPID/login_time/connection_id/CONTEXT_INFO/login/app/DB/serverを照合します。ClientConnectionIdとDMV IDを同一と仮定しません。wrong tag/time/connection IDでは同batch guarded KILLが拒否しownerが使用可能なままか確認します。
- sleeping/open TXをsessions・tran_session・active_snapshotから記録。server error 51010、Cancel、command timeout -2、idle open TXでの`SqlConnection.Dispose`、元接続rollback経路の意図的なclient側失敗（SQL未送信・元接続lease維持）による管理KILLを確認します。WAITFORはattention注入用で、巨大COUNTの代替費用測定ではありません。Disposeは物理接続終了の模擬であり実TCP分断・ブラックホールは未検証です。
- client finallyが使える元接続の**自分の**transactionだけをbounded rollback。使えない／失敗時は管理経路で照合・同batch再照合してKILL、元TX IDをsys.dm_tran_active_transactionsでも追い、snapshot entry/sessionの消失をpoll。KILLはSPID指定なので照合とKILLは原子的ではなくTOCTOUが残ります。未知の所有者にはKILLせず停止・エスカレーションします。監視不能を0へ変換しません。lab SA成功は最小権限運用の認定ではありません。restricted observerも試験します。
- statsは新lab内のOFF/sync ON/async ON各profile。初期fixture統計を作り変更後、無条件COUNTの前後で設定・named stats更新時刻/modification_counter・actual plan/OptimizerStatsUsage（あれば）を保存。count後の即時・約1秒・さらに3秒の窓で状態とDB requestsを観測します。XEは収集せず「イベントなし」と主張しません。変化なしは当schema/plan/state/窓で未観測。asyncの窓外動作、productionの費用・回復は未認定です。旧`sqlserver/setup.sql`のauto CREATE/UPDATE OFFを変更しません。

`results/issue-1/runs/<unique>/result.json`と`controller.log`、`probe.json`、plan XML、packages lock、dotnet情報が成果です。controller logにclient stdout/server log/全CLI終了statusと最終controller終了statusを保存します。source_before/afterはrunner/C#project/Dockerfile/compose/config/例入力/READMEの固定一覧と任意のsource packages.lock.jsonの有無（mutable docsを除外）。完了・所有確認・精度・TX保持解除・容量/本番回復・サービス予算・cleanupは別欄です。失敗logや未完了をpassにしません。

所有者は終了code・完全log・意味・最終source・SQL/volume/network不在を確認してから、別セッションGPT-6.1 Solの最終2役レビューを実施します。旧受入を上書きせず[Issue 1受入](../../results/reviews/issue-1/acceptance.json)を作成します。[事前アーカイブ](../../results/reference/issue-1-pre-fix-c6396cc/manifest.json)の受入はアーカイブhashだけに適用します。

仕様根拠：[SqlClient Cancel](https://learn.microsoft.com/en-us/dotnet/api/microsoft.data.sqlclient.sqlcommand.cancel)、[TRY/CATCH](https://learn.microsoft.com/en-us/sql/t-sql/language-elements/try-catch-transact-sql?view=sql-server-ver17)、[SQL Linux containerサポート](https://learn.microsoft.com/en-us/sql/linux/containers/deploy?view=sql-server-ver17)、[NuGet lock](https://learn.microsoft.com/en-us/nuget/consume-packages/package-references-in-project-files#locking-dependencies)、[運用SQLと権限](../../docs/guidelines/operations-runbook.md)。

作者のoffline確認は`python3 sqlserver/issue1/verify_author.py`（rootから）で行い、`author-verification.json`と完全logへ実終了codeを保存します。Ruby構文・設定構造・架空入力算術・旧保護hashを確認するもので、C# compileやDB実行の代替ではありません。

cleanupは取消済みwork tokenを再利用せず、独立した期限付きtokenを使います。所有者が固定したSDK digestと依存lockによりビルドし、実際のimage/lock/source hashを証跡へ残します。
