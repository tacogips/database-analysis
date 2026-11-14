"""Integrate actual terminal figures into the mutable Japanese report only."""
from pathlib import Path
import json, statistics

root = Path(__file__).resolve().parents[4]
out = root / 'results/reviews/mysql-engine-count-100m'
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
assert data['exit_status'] == 0 and data['completed_valid_counts_total'] == 12
doc = root / 'docs/mysql-engine-count-100m.md'
text = doc.read_text()
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
lines.extend(['', '上記は取得時点の永続統計。実際の走査量・読み出しバイト数ではない。SQL・生の統計・last_update・取得時間は各condition.manual_referenceに保存した。', '',
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
              f"- [hash付き保存先](../results/reference/mysql-engine-count-100m-{path.parent.name}/manifest.json)。",
              '- [所有者監査](../results/reviews/mysql-engine-count-100m/owner-audit.json)、[最終source・文書の固定](../results/reviews/mysql-engine-count-100m/owner-proof.json)。',
              '- [Mac・VM仕様](../results/reviews/mysql-engine-count-100m/host-spec.json)。',
              '',
              f"実行完了：{data['completion']}。正確値：{data['precision']}。後始末：{data['cleanup']}。サービス負荷予算：UNAVAILABLE。最終査読の合否は独立したreview結果で確認する。",
              '', '最初のsystem Ruby選択失敗ではDB・投入を開始していない。専用mise taskを明示的なmise execへ修正した後の、上記新規実行だけを比較値に用いた。失敗時の呼出し記録はresults/reviews/mysql-engine-count-100m/prior-invocation-*に保持した。', ''])
head = text.split('## 実測結果\n', 1)[0]
doc.write_text(head + '\n'.join(lines))
print(json.dumps({'document': str(doc.relative_to(root)), 'run': str(path.relative_to(root)), 'conditions': len(conditions)}))
