"""Integrate actual terminal figures into the mutable Japanese report only."""
from pathlib import Path
import json, statistics, subprocess, sys

root = Path(__file__).resolve().parents[4]
out = root / 'results/reviews/mysql-engine-count-100m-cold'
invocation = json.loads((out / 'canonical-invocation.json').read_text())
assert invocation['status'] == 'process_finished' and invocation['exitCode'] == 0
terminal = None
for line in reversed((out / 'canonical-invocation.log').read_text().splitlines()):
    try:
        value = json.loads(line)
    except ValueError:
        continue
    if isinstance(value, dict) and 'result' in value and 'exit_status' in value:
        terminal = value
        break
assert terminal and terminal['exit_status'] == 0
path = Path(terminal['result'])
data = json.loads(path.read_text())
assert data['exit_status'] == 0 and data['completed_valid_counts_total'] == 12 and data['cold_buffer_policy']=='passed_all_12_fresh_process_boots_terminal_clean'
doc = root / 'docs/mysql-engine-count-100m.md'
text = doc.read_text()
text = text.replace('**cold-buffer版は計測中。全条件の実行・監査と別セッションのSol 2役の査読後に結果を確定する。**',
                    '**DBバッファを各回リセットした1億行・100列、4条件各3回の計測を完了した。全12回が100,000,000件を返した。**')
text = text.replace('**実測進行中。比較の数値は、所有者の実行・監査後に反映する。**',
                    '**1億行・100列、4条件各3回の計測を完了した。全12回が100,000,000件を返した。**')
lines = [
    '## 実測結果', '',
    f"実行開始：{data['started_at']}。終了：{data['finished_at']}（UTC）。",
    '次表の単位はms。要求順の全3標本を示す。主時間はクライアントまでの応答時間であり、サーバー内だけの時間ではない。', '',
    '| 条件 | 使用予定索引（実際の推定計画） | 1回目 ms | 2回目 ms | 3回目 ms | 中央値 ms | 最小〜最大 ms |',
    '|---|---|---:|---:|---:|---:|---:|',
]
names = {
    'innodb_primary_only': 'InnoDB・PKのみ',
    'innodb_secondary_present': 'InnoDB・単一列副索引',
    'innodb_composite_secondary': 'InnoDB・複合副索引',
    'myisam_primary_only': 'MyISAM・PKのみ',
}
conditions = [c for stage in data['stages'] for c in stage['conditions']]
for condition in conditions:
    values = [sample['caller_elapsed_ns'] / 1_000_000 for sample in condition['counts']]
    key = condition['manual_reference']['selected_index'] or '選択索引なし（計画の生値を参照）'
    if condition['manual_reference']['selected_index'] is None:
        key = condition['manual_reference']['explain']['raw'][0].split('\t')[-1]
    lines.append('| ' + names[condition['id']] + ' | ' + key + ' | ' +
                 ' | '.join(f'{v:.6f}' for v in values) +
                 f' | {statistics.median(values):.6f} | {min(values):.6f}〜{max(values):.6f} |')
lines.extend(['', '### TABLE_ROWSとCOUNTの値・取得時間', '',
              '単一表を対象にしたTABLE_ROWS取得とCOUNTを、同じスカラー受信までの計時で比較する。概数はreference statisticsであり、正確値と混同しない。誤差はこの固定表の100,000,000件に対する符号付き相対誤差。NULLはUNKNOWNのまま残す。', '',
              '| 条件 | 回 | TABLE_ROWS | 取得 ms | 概数誤差 % | COUNT結果 | COUNT ms |', '|---|---:|---:|---:|---:|---:|---:|'])
for condition in conditions:
    for sample in condition['counts']:
        metadata = sample['manual_reference']['table_rows']
        value = 'UNKNOWN' if metadata['value'] is None else str(metadata['value'])
        error = 'UNKNOWN' if metadata['signed_error_pct'] is None else f"{metadata['signed_error_pct']:+.6f}"
        lines.append(f"| {names[condition['id']]} | {sample['index']} | {value} | {metadata['caller_elapsed_ns']/1e6:.6f} | {error} | {sample['value']} | {sample['caller_elapsed_ns']/1e6:.6f} |")
lines.extend(['', 'TABLE_ROWS自体の取得性能を条件別に集計する。単位はmsで、各条件3回すべてを使った記述統計。接続開始・DB再起動・先行の一覧取得は含まず、要求送信からスカラー受信までのCLI・転送時間は含む。', '',
              '| 条件 | TABLE_ROWS 最小 ms | 中央値 ms | 最大 ms | 標本数 |', '|---|---:|---:|---:|---:|'])
for condition in conditions:
    timings = [sample['manual_reference']['table_rows']['caller_elapsed_ns']/1e6 for sample in condition['counts']]
    lines.append(f"| {names[condition['id']]} | {min(timings):.6f} | {statistics.median(timings):.6f} | {max(timings):.6f} | {len(timings)} |")
lines.extend(['', '各TABLE_ROWSのSQL・生応答・整数ns（スカラー／全境界）・取得時刻は各sample.manual_reference.table_rowsに保存した。既存の概算一覧の取得時間はinventoryとして別に保存している。0の参考統計は空表の証明ではなく、この表での誤差を別の表へ保証しない。', '',
              '### 各回のバッファリセットと残存ページ', '',
              '| 条件 | 回 | count boot | リセット秒 | 対象cache pages（手順前／COUNT直前） |', '|---|---:|---:|---:|---|'])
for condition in conditions:
    for sample in condition['counts']:
        def pages(field):
            snapshot = sample[field]
            return str(snapshot['target_cached_pages']) if 'target_cached_pages' in snapshot else '対象外'
        lines.append(f"| {names[condition['id']]} | {sample['index']} | {sample['boot_ordinal']} | {sample['reset']['elapsed_ns']/1e9:.6f} | {pages('buffer_at_start')}／{pages('buffer_before_count')} |")
lines.extend(['', '全14boot（投入2＋COUNT12）の正常終了・reapと、各COUNT前のpreload OFF／dump OFFを監査する。上記は対象表のDBバッファページで、OS・VM・Macのファイルキャッシュではない。メタデータ取得が触れたページもそのまま開示する。リセット・readiness・メタデータ時間はCOUNTに含めない。全層coldや物理SSD読み出しの保証ではない。'])
lines.extend(['', '全境界時間も別途保存している。以下の各セルは1〜3回目の整数nsで、丸め前の主時間／全境界時間を示す。', '',
              '| 条件 | caller_elapsed_ns | full_request_elapsed_ns |', '|---|---|---|'])
for condition in conditions:
    lines.append('| ' + names[condition['id']] + ' | ' + ', '.join(str(s['caller_elapsed_ns']) for s in condition['counts']) +
                 ' | ' + ', '.join(str(s['full_request_elapsed_ns']) for s in condition['counts']) + ' |')
lines.extend(['', '### COUNT前に取得した索引の参考値', '',
              '| 条件 | size pages | leaf pages | size × page size（bytes） |', '|---|---:|---:|---:|'])
for condition in conditions:
    stats = condition['manual_reference']['index_page_stats']
    def show(field):
        value = stats.get(field)
        return str(value) if value is not None else ('対象外' if stats['status'] == 'not_applicable_non_InnoDB' else 'UNKNOWN')
    lines.append('| ' + names[condition['id']] + ' | ' + ' | '.join(show(k) for k in ['size_pages', 'leaf_pages', 'size_bytes_from_stats']) + ' |')
lines.extend(['', '上記は各条件の第1回に取得した永続統計。実際の走査量・読み出しバイト数ではない。全12回のSQL・生の統計・last_update・取得時間は各sample.manual_referenceに保存した。', '',
              '### 投入と索引作成（COUNTとは別）', '', '| 工程 | 秒 |', '|---|---:|'])
for stage in data['stages']:
    lines.append(f"| {stage['engine']} 1億行の投入 | {stage['load_elapsed_ns']/1e9:.6f} |")
    for field, label in [('index_build', '単一列副索引の作成'), ('composite_index_build', '単一列削除と複合副索引の作成')]:
        if field in stage:
            lines.append(f"| {label} | {stage[field]['elapsed_ns']/1e9:.6f} |")
lines.extend(['', 'これらの投入時間は1000回の投入・受領・記録処理を含む。索引時間はDDLのクライアント応答時間。いずれもCOUNTの時間には含めていない。', '',
              '### 観測された中央値比', '', '| 比の分子／分母 | 倍 |', '|---|---:|'])
ratios = {
    'innodb_primary_over_myisam_primary': 'InnoDB PK／MyISAM PK',
    'innodb_secondary_present_over_myisam_primary': 'InnoDB 単一列副索引／MyISAM PK',
    'innodb_primary_over_innodb_secondary_present': 'InnoDB PK／単一列副索引',
    'innodb_primary_over_composite': 'InnoDB PK／複合副索引',
    'composite_over_narrow': 'InnoDB 複合副索引／単一列副索引',
}
for key, label in ratios.items():
    lines.append(f"| {label} | {data['caller_median_ratios'][key]:.6f} |")
lines.extend(['', 'この比は本環境・固定順序・3標本に限る観測。狭い副索引を使う走査と、幅広いクラスタ化主キーの走査を区別する参考にする。索引新設の推奨、他環境の速度予測、エンジンだけの因果的な速度差には使わない。MyISAMの短い応答にはクライアント・輸送の下限があるため、大きな比もDB内部だけの速度比ではない。', '',
              '### 根拠と受入の区別', '',
              f"- [新規実行のresult.json](../{path.relative_to(root)}) と同ディレクトリの生ログ。",
              f"- [hash付き保存先](../results/reference/mysql-engine-count-100m-cold-{path.parent.name}/manifest.json)。",
              '- [所有者監査](../results/reviews/mysql-engine-count-100m-cold/owner-audit.json)、[最終source・文書の固定](../results/reviews/mysql-engine-count-100m-cold/owner-proof.json)。',
              '- [Mac・VM仕様](../results/reviews/mysql-engine-count-100m-cold/host-spec.json)。',
              '',
              f"実行完了：{data['completion']}。正確値：{data['precision']}。DBバッファ方針：{data['cold_buffer_policy']}。後始末：{data['cleanup']}。サービス負荷予算：UNAVAILABLE。最終査読の合否は独立したreview結果で確認する。",
              '', '### 過去の未完了参考値との区別', '',
              '先行するキャッシュ未制御runは、ユーザーのDBバッファリセット指定を受けてSIGINTで中断した。PKのみの3回は275066854000、274050766000、278455706000 nsで、いずれも1億件だったが、新しい比較値には混ぜていない。先行runはstatus=incomplete・exit_status=1で、stage cleanupにはclient EOF/reap timeoutが記録されている。最終的な所有資源の消失を独立確認したことは、実験の完了・成功を意味しない。', '',
              '- [旧runの中断・資源消失監査](../results/reviews/mysql-engine-count-100m/interrupted-run-audit.json)。',
              '- [旧キャッシュ方針と変更理由](../results/reviews/mysql-engine-count-100m/cache-steering.json)。', '',
              'cold-buffer版の先行runも、InnoDBの9標本を完了後、MyISAM開始前の容量チェックでexit 1となった。VM共有層がホストで削除済みのファイルを保持しており、対象パスのguest側メタデータ再確認で大きな保持ファイルを解放した。VM再起動・全体のcache purgeはしていない。十分な容量を確保して同じ固定コードを白紙から再実行し、本書の主比較にはその新規runの全12標本だけを使う。', '',
              '- [容量停止runのhash付き保存](../results/reference/mysql-engine-count-100m-cold-capacity-stopped-20261108T162849Z-0e82949c4ce394e3baab969403f8b6c1/manifest.json)。',
              '- [容量回復の記録](../results/reviews/mysql-engine-count-100m-cold/capacity-recovery.json)／[新規runの削除済みFD確認](../results/reviews/mysql-engine-count-100m-cold/host-deleted-descriptor-cleanup.json)。', '',
              '容量回復後のrunも、InnoDBの9標本完了後、MyISAM投入の直近50バッチが約7.3秒／10万行となり、累計2時間の投入期限を超える見込みだったため、所有者がSIGINTで停止した。これは実際の期限切れではなく予防的な中断で、exit 1・未完了として保存した。Sol59が期限だけの修正案を作成し、所有者は停止・後始末・保存後に適用した。MyISAMの累計投入を4時間とし、個別SQL2時間・全体6時間・COUNT20分・データ・索引・cache条件は変えていない。主比較はその後の白紙再実行だけで、先行9標本は混ぜない。', '',
              '- [投入期限見直しによる中断の保存](../results/reference/mysql-engine-count-100m-cold-duration-stopped-20261108T172604Z-ca74a6381f9f93ff0ed6ed7c87471500/manifest.json)／[停止・後始末監査](../results/reviews/mysql-engine-count-100m-cold/load-duration-stop-audit.json)／[Sol修正案の適用記録](../results/reviews/mysql-engine-count-100m-cold/load-duration-owner-application.json)。', '',
              'さらにその前のsystem Ruby選択失敗ではDB・投入を開始していない。失敗の呼出し記録はresults/reviews/mysql-engine-count-100m/prior-invocation-*に保持した。上記の主比較は、新規に投入して毎回DBバッファをリセットしたrunだけに基づく。', ''])
lines.extend(['', '### 同じ生成条件での統計値の変動（参考）', '',
              '以下はPKのみの第1標本をrun別に示す。先行2件は全体がexit 1で終わった未完了参考であり、主比較や中央値比には含めない。表データは同じ生成式の1億行だが、新規構築した別の表・統計である。', '',
              '| runの区別 | TABLE_ROWS | 真値に対する誤差 % | 根拠 |', '|---|---:|---:|---|'])
for directory, label in [
    ('mysql-engine-count-100m-cold-capacity-stopped-20261108T162849Z-0e82949c4ce394e3baab969403f8b6c1', '先行・容量停止／全体未完了'),
    ('mysql-engine-count-100m-cold-duration-stopped-20261108T172604Z-ca74a6381f9f93ff0ed6ed7c87471500', '先行・投入期限見直しで中断／全体未完了'),
]:
    prior = json.loads((root / 'results/reference' / directory / 'result.json').read_text())
    assert prior['exit_status'] == 1
    sample = prior['stages'][0]['conditions'][0]['counts'][0]
    assert sample['status'] == 'valid' and sample['value'] == 100000000
    meta = sample['manual_reference']['table_rows']
    lines.append(f"| {label} | {meta['value']:,} | {meta['signed_error_pct']:+.6f} | [hash付き保存](../results/reference/{directory}/manifest.json) |")
current_meta = data['stages'][0]['conditions'][0]['counts'][0]['manual_reference']['table_rows']
lines.append(f"| 今回の完了run／主比較 | {current_meta['value']:,} | {current_meta['signed_error_pct']:+.6f} | [生結果](../{path.relative_to(root)}) |")
lines.extend(['', '統計の同じ値が繰り返し返ることも、固定した行数・行幅・索引設定も、他の表や次回構築の誤差率の保証にはならない。上の少数観測から分布や信頼区間、誤差の上限は作らない。誤差原因を単独に特定した実験ではなく、必要な精度は対応する固定コピーの正確値との校正で確認する。', ''])
cache_path = out / 'host-deleted-descriptor-cleanup.json'
if cache_path.exists():
    cache = json.loads(cache_path.read_text())
    residual = cache['after']
    residual_bytes = sum(x['bytes'] for x in residual)
    lines.extend(['', '### 所有資源の削除と共有VMキャッシュの残存', '',
                  '所有DBコンテナ・接続・プロセスの終了と、所有データパスの削除は独立確認した。ホスト上の削除済みファイル参照は別に観測し、対象nonceだけをguest側から読み取り専用で再確認した。共有VMの再起動・全体キャッシュ破棄は行っていない。', '',
                  f"再確認後の残存は{len(residual)}本、{residual_bytes:,} bytes（{residual_bytes/1024**3:.6f} GiB）。分類は `{cache.get('physicalCacheStatus','未分類')}`。",
                  '参照が残る場合、共有VZプロセスの読み取り専用参照であること、既知のredo・undo・システムファイルだけであること、対象テーブルの.ibd／.MYD／.MYIへの参照がないことと空き容量を監査する。残存がある場合の物理キャッシュ解放は未達であり、所有パス・runtimeの消失と混同しない。これは計測後の観測で、各COUNT前のDBプロセス再起動の証拠とは別である。', '',
                  '[実際の参照一覧・PID確認・空き容量](../results/reviews/mysql-engine-count-100m-cold/host-deleted-descriptor-cleanup.json)。ユーザー指定は「DBバッファを毎回リセットし、OS・Mac側の残存は明記する」。初回の所有者監査は共有VMの削除済み参照ゼロを要求してexit 1になったため、その試行も[hash付きで保存](../results/reviews/mysql-engine-count-100m-cold/before-cache-scope-correction/manifest.json)し、上の区別と残存を最終Sol 2役の査読対象に含める。', ''])
head = text.split('## 実測結果\n', 1)[0]
doc.write_text(head + '\n'.join(lines))
subprocess.run([sys.executable,str(Path(__file__).with_name('sync-guidelines.py'))],cwd=root,check=True)
print(json.dumps({'document': str(doc.relative_to(root)), 'run': str(path.relative_to(root)), 'conditions': len(conditions)}))
