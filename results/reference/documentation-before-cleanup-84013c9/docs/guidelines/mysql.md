# MySQL：engine別の件数取得ガイド

同じMySQLでもInnoDBとMyISAMでは、TABLE_ROWSの意味とロックの扱いが違います。対象表のENGINEを確認し、該当するガイドへ進んでください。各ガイドに手順・SQL・Q&Aをまとめています。

- [InnoDB：概算件数を確認し、必要な表だけCOUNT](mysql-innodb.md)
- [MyISAM：保持件数・cache・ロック・RDSの制約を確認](mysql-myisam.md)

## 対象DBのengineと件数情報を確認する

既存の表定義や取得済み一覧があれば利用できます。SQLで確認する場合は対象DBを限定し、専用接続・実行期限・監視と取消を用意します。metadata取得も無負荷・ロックゼロではありません。業務データや既存設定の変更、ANALYZE、LOCK TABLESは行いません。

```sql
SELECT TABLE_SCHEMA, TABLE_NAME, ENGINE, TABLE_ROWS
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'db' AND TABLE_TYPE = 'BASE TABLE'
ORDER BY TABLE_NAME;
```

TABLE_ROWSはInnoDBでは概算、MyISAMでは保持件数に基づく値ですが、どちらにも統計cacheの影響があります。必要な精度と時点を満たすかは各ガイドで確認します。権限で見えない表、temporary table、viewはこの一覧だけで全件を網羅できません。対象一覧との照合が必要です。

その他のengineやENGINEを取得できない表は、DB担当者が表定義と対応する取得方法を確認します。InnoDBやMyISAMの手順をそのまま流用しません。

[性能調査・実測の詳細](mysql-details.md#読み方と性能調査へのリンク)／[SQL Serverガイド](sql-server.md)
