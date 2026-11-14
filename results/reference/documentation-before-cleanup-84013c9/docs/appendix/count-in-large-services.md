# Appendix：大規模サービスの件数表示・集計の公開事例

これらはページング、条件付き件数、APIの件数表示、バッチ集計の事例です。このrepoの対象である「数億行の単一表を、業務上のWHEREなしで一度COUNTする」性能試験ではありません。MySQL／SQL Serverの全件COUNTに掛かる時間やサービス悪化率の根拠には使いません。

| サービス | 公開事例 | 今回の全件COUNTとの違い |
|---|---|---|
| GitHub | スター一覧のページ取得のたびに総件数を数えていた問題を報告。制限前の1週間に約37,000件の遅いCOUNT、平均約3秒、保護機構による取消1,000件超が記録された。[技術記事](https://github.blog/engineering/architecture-optimization/from-2500-requests-to-24-rebuilding-star-history-without-the-stargazers/) | 特定repositoryのスターを数えるAPI・ページングの反復負荷。単発の全表COUNTの結果ではない |
| GitLab | ページ番号を表示するCOUNTを省き、前後ページへのリンクだけにする方法を案内。[開発ガイド](https://docs.gitlab.com/development/database/pagination_guidelines/) | PostgreSQLのアプリ一覧・条件付き件数の設計 |
| Shopify | ページング対象の件数を25,000件まで正確に返し、それを超えると25,001で上限超過を示す。[API仕様](https://shopify.dev/docs/api/usage/pagination-graphql#pagination-limits) | API仕様として正確な総件数を返さない選択。統計による概算やDBの安全行数上限ではない |
| GitLab | バッチで数え、途中停止・再開できる仕組みを提供。Service Pingの新規counterにはBatchCountを推奨。[集計手順](https://docs.gitlab.com/development/database/iterating_tables_in_batches/) | PostgreSQLの分割集計。更新中の複数バッチの合計が同一時点の正確値になるとは、この事例から判断できない |

## 参考にできる点

- ORMやページングが意図せずCOUNTを発行していないか確認する。
- 同じCOUNTの重複実行・高頻度の再実行を避ける。
- 利用目的によっては、正確な総件数を返さない仕様を選べる。
- 集計には実行期限と停止方法を用意する。

これらの事例から、全件COUNTに必ず専用replicaが必要、あるいは専用replicaなら無影響、とは結論しません。

[MySQLの件数取得手順](../guidelines/mysql.md)／[SQL Serverの件数取得手順](../guidelines/sql-server.md)
