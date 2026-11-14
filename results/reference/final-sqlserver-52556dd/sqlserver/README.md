# SQL Server count lab

[日本語ガイド](../docs/sqlserver-table-count-guideline.md)、[所有者が検証した実測結果](../results/sqlserver/runs/20261107T230105Z-8533a31e/result.json)、[owner proof](../results/sqlserver/owner-proof.json)、[owner audit](../results/sqlserver/owner-audit.json)。
MySQL の runner・結果・受入証跡から独立した、使い捨て専用ラボです。
[計画](../results/sqlserver/plan.json)の `planned_not_db_executed` は歴史的な予定の状態です。今回の実績は上記 run を正とします。

所有者用の実行コマンド（author は実行しません）:

```sh
mise -C sqlserver run all
```

- Ruby 4.0.9 の標準ライブラリと Docker CLI / compose plugin を使います。
- Docker 接続は設定済み context のローカル Unix socket を検査して固定します。TCP/SSH/存在しない socket は拒否し、継承した接続 override は除去します。DB 接続は `docker exec lab-sqlserver-count /opt/mssql-tools18/bin/sqlcmd -S localhost` だけです。host/DSN/接続先 override はありません。
- `lab-sqlserver-count` / `lab-sqlserver-count-data` / `lab-sqlserver-count-network` が既存なら失敗します。所有者が固定名と所有 label を照合して既存の専用ラボを片付け、白紙を確認します。runner は既存環境を reset しません。
- 同じ workspace の canonical runner は file lock で直列化します。他の workspace / 手動操作との競合は所有者が避けます。
- Developer EULA はローカル lab 用に承認済み。公開 lab 専用 credential は `CountLab_Only_2026!`。host 公開 port はありません。
- Docker RAM は 8 GB 以上、コンテナ上限 4 GiB、SQL memory 3072 MiB、CPU 2。amd64 image の ARM 上のエミュレーションは Microsoft 非サポートです。
- データ 7000 MiB / log 1024 MiB / tempdb 合計最大 2048 MiB とし、準備の間に volume 使用量を確認します。14 GB で停止する設計で、15 GB 未満を目標にします。volume 自体の hard quota ではないため、所有者もホスト空き容量を確保します。
- set-based 50,000 行 batch で再生成します。80/100 列は各 750,000 行、optional の100M細表は残時間内に試し、無断で縮小しません。optional 未成立は理由と raw failure を残して `notrun`。他の load / method / artifact エラーは非0終了です。今回の run は100Mを含む全10 fixtureが verified でした。
- TDE 証明書・master key はコンテナ内だけに生成します。秘密の passphrase / password は一時メモリ/stdin だけで、SQL 本文・テーブル・鍵の export は保存しません。再現用の非秘密 seed は結果に残します。stdout/stderr に SQL 本文を echo する設定は付けません。
- `result.json` は run ID 別ディレクトリに保存します。終了 status、全 command 生ログへのパス、source 前後 SHA、精度・実験完了・lab 期限・service 未認定・後始末を分けます。過去 run は上書きしません。
- 既存 MySQL `lab.rb` の reset は `results/reference` / `results/reviews` 以外を削除するため、`results/sqlserver` も削除対象です。所有者は MySQL all/reset 等の前に今回の証跡をコミットまたは別途保全し、保持を確認します。MySQL source・過去の実測入力bytes・受入hashは変更しません。
- 実行は foreground controller が所有します。短い workload thread とその全 child を join/reap し、専用コンテナを停止・削除し、専用 volume/network 不在まで確認します。`compose create` と `docker start` は controller 管理の service lifecycle で、shell background job は作りません。
- `Ctrl-C` は Ruby の ensure で後始末へ進みます。SIGKILL・電源断では ensure を実行できません。その場合は未完了結果を合格にせず、所有者が固定名・所有 label・run log を確認して復旧します。

今回の実行は `20261107T230105Z-8533a31e`、SQL Server `17.0.5035.5`、ARM上のamd64 emulationです。開始・最終ともTDE encryption_state=3、既存RCSI / SNAPSHOT有効を確認しました。
1億行の1列heapと各75万行の80/100列表は実測済みですが、1億行かつ80/100列かつTDEの組合せ、native性能、本番アプリ暗号化、Always Encryptedは未計測です。全fixtureがTDE有効のためon/offの因果比較もありません。
wide表の暗号文は runner の一時passphraseをサーバーの `EncryptByPassPhrase` に渡して単一値を生成・複製したものです。クライアント列鍵なしでのdriver動作を検証したものではありません。
完了・精度・lab期限・service予算・後始末は別判定です。終了コードは0、[完全なrunner log](../results/sqlserver/runs/20261107T230105Z-8533a31e/runner.log)と[外側の完全log](../results/sqlserver/reviews/canonical-invocation.log)、[終了記録](../results/sqlserver/reviews/canonical-invocation.json)を保持しています。所有者はsource/raw artifact/意味/後始末と既存MySQL入力の保持を監査済みです。
metadataの誤差上限は未認定で、36件のUPDATE診断が完了してもservice SLOは `unavailable` です。数値・反復の選別条件・生ログへのリンクはガイドを参照してください。

文書の最終採用は[final-acceptance.json](../results/sqlserver/reviews/final-acceptance.json)の受入済み記録を条件とします。所有者は、独立した別々の最終セッションのGPT-6.1 Sol 2役が完了し、ガイド・README・owner proofの正確なhashが一致した後にのみ、その受入記録を作成します。ファイルの存在・保留状態・ベンチマーク検証のowner proofは、文書受入の代わりにはなりません。
最終査読後に文書を変更した場合は、新しいhashで2役査読と受入をやり直します。authorはDB/Docker/計測を起動せず、runner・結果・計画・proofも書き換えません。
