# Issue 3: CONTEXT_INFO競合はCOUNT前にHOLDする専用lab

これは新規の使い捨てfixtureだけを扱う機能検証です。本番のCOUNT／取消サービス実装ではありません。実DB・外部DSN・行データ・鍵は扱いません。旧`sqlserver/issue1/`と既存結果は凍結し、そのRLSなし4,096行fixtureの成功をRLS context安全性の証明へ転用しません。author／reviewerはDocker・DB・build・計測を起動しません。

所有者がauthor終了後にrepository rootから実行する固定コマンド：

```sh
mise -C sqlserver/issue3 run all
```

Ruby 4.0.9、既存local Unix Docker context、固定digestのSQL Server 2025 CU10 amd64とSDK 10.0 imageが必要です。controllerは環境由来のendpoint／compose上書きを除外し、Unix socket以外を拒否します。SQL imageは`sha256:245f6b1c8903edae52cb20fa31419cefcde1a49938bb8c3447099011fc871ca3`、SDKは`sha256:212fe4cb74627d5adf9817a4088906ae3edff1bd853bb27565d4b46731efe61e`です。NuGet lockはSqlClient 7.1.1とPkcs 10.0.14を直接固定し、restoreは`--locked-mode`、NU1901–NU1904はerrorです。所有者の固定allがcompile／DB実行を行います。

`lab.rb`はflockを全寿命で保持し、予約名のcontainer／network／volume不在を成功したinventoryで確認してから`create --no-recreate --no-build --pull never`します。全resourceにランダムowner labelを照合します。公開port・Docker socket mount・外部接続引数はありません。serverはinternal network内、controllerが終了まで所有します。CLI子プロセスは期限付きで待機・回収し、失敗・signalでもensureで所有物だけ削除、最後のinventoryで不在を確認します。共通pruneや既存resourceの削除はしません。初期化に失敗しても予約済みの別所有物には触れません。

## Lab contract

ユーザー指定の専用fixtureスコープを、所有者がこのsource hashの固定allを実行するときだけ承認契約として渡します。`FixtureApproval`は対象`CountIssue3.dbo.Target`（このrunで新規作成する非RLS表）だけのJOB_EXCLUSIVEです。承認元は本節と所有者の固定all記録、寿命は初期化完了後の1物理leaseからDispose／解放確認までです。この表にtenant／業務初期化・後続SET・RLS policy／依存先・監査・triggerによるCONTEXT_INFO使用はないことを、固定fixture source全体で限定します。空の業務初期化は完了・監査済みというlab契約です。これはNULL検査、sys.security_policies照会、非pool、SA権限から推定した許可ではありません。本番への承認として流用できません。

`Initialize(connection, contract, target, counters)`、`PrepareAndCount`、`GuardedCandidateCount`はすべて同じ承認gateを持ちます。契約欠落／UNKNOWN（既定）／BUSINESS_USE、scope・寿命・監査の欠落はHoldExceptionとなり、接続初期化・タグ書込み・候補COUNT送信より前に拒否します。raw COUNTは承認済みwrapper内部だけにあり、候補callerはwrapperを通ります。別のraw SQLとして残るseed／RLS可視数／統計profileのCOUNTは、明示したfixture control診断であって候補入口や候補精度承認ではありません。

JOB_EXCLUSIVEではtagを一度だけ初期化し、保存したleaseの契約とidentityをCOUNT前に再照合します。読取／observer／KILLはserver・SPID・login_time・server connection_id・CONTEXT_INFO・login/original login・app・DBの同一契約です。再初期化・driftは拒否し、取消を通すため再tagしません。SESSION_CONTEXTへの代替、独断のcontext連結／prefix／復元、RLS policy変更／無効化は行いません。CONTEXT_INFO依存RLSの運用COUNT＋取消は未対応で、HOLD／承認済みexportへ戻します。

## 所有者の実行で確認する範囲

- 非RLSのJOB_EXCLUSIVE fixtureは従来の5経路（server error、Cancel、command timeout、idle接続Dispose、client rollback失敗から管理KILL）を保持します。実際の2接続でidentity一致、tag／login_time／connection_idの3不一致拒否とowner使用継続、restricted observerの監視不能を確認します。finallyは独立cleanup tokenを持ち、捕捉したTX IDもsession消失後まで追跡します。WAITFORはattention用で巨大COUNT費用測定ではなく、Disposeは実ネットワークpartitionではありません。KILLのSPID-only照合raceは残ります。
- 同DBの別表`dbo.RlsTarget`に5行（tenant 1が2行、tenant 2が3行）をseedし、その後は変更しません。専用`issue3_security`schema、schemabound inline predicateはCONTEXT_INFO先頭4byteをintへ変換、policyはONです。SA/dboにも適用し、bypassを設けません。
- `fixture_control_seeded_physical_count`はpolicy作成前のseed件数、`fixture_control_count`は既存RCSI下の業務context 1/2/99で無条件plain COUNT_BIGの可視2/3/0を検査します。global partition参考値5を前後で照合します。可視0は物理空ではなく、global metadataはtenant countではありません。controlは候補精度承認ではありません。
- BUSINESS_USE/context 1、UNKNOWN/初期NULLと既知の業務値2、欠落契約/初期NULLが、正経路と同じ`PrepareAndCount`入口を呼びます。UNKNOWN／欠落から後続業務initでcontext 1/2を設定した後も再拒否します。各拒否でtag writes 0／candidate count dispatches 0、元ownerと別observerの実context／identityの前後一致を記録します。`GuardedCandidateCount`への直接呼出も拒否し、初期化の迂回を許しません。同じ未承認契約で通常のTarget指定でも両入口の拒否を確認し、RLS対象の未対応分岐だけに依存した拒否試験にしません。
- 拒否後は実際の業務identityを捕捉し、所有する非pool接続だけDisposeします。誤ったjob tagでKILLせず、独立cleanup期限でsession／request／TX／snapshotと保持TX IDの不在をpollします。これらは**HOLD拒否だけの検証**で、RLS下の運用COUNT＋取消の認定ではありません。
- 従来の小さな非RLS統計診断（OFF／sync ON／async ONと限定観測窓）を保持します。新しい巨大件数gridや意図的locking／DDL競合は追加しません。

`results/sqlserver/issue3/runs/<unique>/`に`result.json`、`controller.log`、`probe.json`、plan XML、lock、dotnet情報を保存します。controller.logはclient stdout、server log、全CLIの終了状態、最終controller終了状態を含み、resultは完全log／artifact hashとsource_before／afterを保持します。source一覧はcontroller、C#、project、lock、Docker／NuGet／compose／mise設定、README、offline verifierを各1回含みます。失敗log・未完了はpassにしません。完了、精度、所有確認、TX保持解除、容量／本番回復、service budget、cleanupは独立欄です。

SA成功は最小権限の認定ではありません。ARM上のamd64エミュレーションはMicrosoft非サポート構成で、機能確認のみです。native性能、本番SLO、実ネットワーク分断、物理領域回収時間は未検証です。所有者が実終了code・完全log・意味・source不変性・所有resource不在を監査し、その後に別セッションGPT-6.1 Sol 2役の査読を行います。[現行Issue 3受入記録](../../results/reviews/issue-3/acceptance.json)がない／対象hash不一致ならHOLDです。集約受入は所有者実行・監査と2役査読の完了を条件とし、author確認だけでは受入済みにしません。[直前snapshotと歴史的Issue 2受入](../../results/reference/issue-3-pre-fix-16cc9b3/manifest.json)は旧hash限定です。

DBなしの確認はrootで`python3 sqlserver/issue3/verify_static.py`です。完全logと最終exit statusを`author-verification.log`／`author-verification.json`へ保存します。Ruby構文、設定、固定依存、source台帳、保護hash、caller/gateの静的確認であり、C# compile／DB実行の代替ではありません。

根拠：[RLS公式仕様](https://learn.microsoft.com/en-us/sql/relational-databases/security/row-level-security?view=sql-server-ver17)・[Microsoft CONTEXT_INFO RLSパターン](https://techcommunity.microsoft.com/blog/sqlserver/row-level-security-performance-and-common-patterns/384367/)・[SqlClient Cancel](https://learn.microsoft.com/en-us/dotnet/api/microsoft.data.sqlclient.sqlcommand.cancel)・[TRY/CATCH](https://learn.microsoft.com/en-us/sql/t-sql/language-elements/try-catch-transact-sql?view=sql-server-ver17)・[SQL containerサポート](https://learn.microsoft.com/en-us/sql/linux/containers/deploy?view=sql-server-ver17)。
