"""Render only actual successful SQL Server measurements; no DB operations."""
from pathlib import Path
import json, statistics, subprocess, sys

root = Path(__file__).resolve().parents[4]
out = root / 'results/reviews/sqlserver-manual-count-100m'
invocation = json.loads((out/'canonical-invocation.json').read_text())
assert invocation['status'] == 'process_finished' and invocation['exitCode'] == 0
terminal = None
for line in reversed((out/'canonical-invocation.log').read_text().splitlines()):
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
probe = data['probe']
assert data['exit_status'] == 0 and probe['completion'] == 'passed_all_18_commands'
assert len(probe['samples']) == 18 and all(s['accepted'] for s in probe['samples'])
conditions = probe['conditions']
samples = probe['samples']
def runtime_settings(suffix):
    found = list(path.parent.glob('raw/*-'+suffix+'.json'))
    assert len(found) == 1
    return json.loads(found[0].read_text())['raw'][0][0]
primary_runtime = runtime_settings('actual-server-settings')
secondary_runtime = runtime_settings('ag-server-identity')
assert primary_runtime['product_version'] == secondary_runtime['product_version'] and primary_runtime['product_version'].startswith('16.')
lines = [
    '# SQL Server：1億行・100列のDB状態別COUNT比較', '',
    '専用の静止labで6ケース・各3回、全18回のCOUNTを計測した。全18回が100,000,000を返した。DのNOLOCKは静止fixtureでの一致であり、更新中の正確値・開始時点のsnapshotを保証しない。', '',
    f"実行開始 {data['started_at']}、終了 {data['finished_at']}（UTC）。DB状態は実際の設定・役割で確認し、Aは実際のAG readable secondaryで実行した。", '',
    '## 返却値と取得時間', '',
    '単位はms。要求送信から最初のスカラー受信までの時間で、TCP・SqlClient・疎な管理監視の費用を含む。接続準備・DBバッファ消去・事前確認・計画作成・投入・索引作成・状態切替・backup/restore・AG同期待機を含めない。DB内だけの実行時間ではない。', '',
    '| ケース | 返却値（各3回） | 1回目 ms | 2回目 ms | 3回目 ms | 中央値 ms | 最小〜最大 ms |',
    '|---|---:|---:|---:|---:|---:|---:|',
]
for condition in conditions:
    found = [s for s in samples if s['condition'] == condition['condition']]
    assert [s['sample'] for s in found] == [1, 2, 3]
    times = [s['first_result_ns']/1e6 for s in found]
    lines.append('| '+condition['condition']+' | '+('観測 ' if condition['route']=='D' else '')+'100,000,000 | '+
                 ' | '.join(f'{t:.6f}' for t in times)+f' | {statistics.median(times):.6f} | {min(times):.6f}〜{max(times):.6f} |')
lines.extend(['', '## 実DB状態・SQL・索引', '',
              '| ケース | RCSI | SNAPSHOT状態 | AG役割 | session isolation | 使用予定索引 |',
              '|---|---|---|---|---:|---|'])
for condition in conditions:
    state = condition['actual_state']
    found = [s for s in samples if s['condition'] == condition['condition']]
    key = '／'.join(str(x.get('index')) for x in condition['planned_scans'])
    role = state.get('role_desc') or '非AG（is_ag_primary=NULL）'
    lines.append(f"| {condition['condition']} | {state['is_read_committed_snapshot_on']} | {state['snapshot_isolation_state_desc']} | {role} | {found[0]['id']['Isolation']} | {key} |")
lines.extend(['', 'BはRCSIの3索引条件を比較した。C／D／Aと比較するのはB-compositeで、4状態とも同じ複合副索引・同じ行集合を使う。全索引×全状態の組合せを計測したものではない。索引の存在と実行計画の選択を区別し、FORCE相当の指定は使っていない。', ''])
for route in ['A', 'B', 'C', 'D']:
    condition = next(c for c in conditions if c['route'] == route)
    lines.extend([f"### SQL {route}", '', '```sql', condition['selected_sql'], '```', ''])
lines.extend(['これらは計時したCOUNT本体。実行先・DB状態・transaction・接続identityの確認は、固定マニュアルのprefixとして別に取得・記録している。Aはrole=SECONDARY・読取り許可・同期・redo queue等を確認し、primaryへ自動で切り替えない。CはSNAPSHOTをSETした後の実分離レベル5を確認した。CONTEXT_INFO／SESSION_CONTEXTへ書き込まない。', '',
              '## DBバッファの条件と整数ns', '',
              '全18回の直前に専用DBのデータバッファを消去し、対象表のbuffer descriptorが0ページであることを確認した。B／C／DはCHECKPOINT後にDBCC DROPCLEANBUFFERS、Aはreadable secondaryのdirty target pages=0を確認してからDBCCを実行した。これは専用labの制御で、本番の件数取得手順には加えない。', '',
              'SQL plan cacheは消去していない。OS・VM・Macのファイルキャッシュも未制御であり、全層cold・物理SSDからの読出し量を保証しない。', '',
              '| ケース・回 | scalar ns | full completion ns | target cached/dirty pages |', '|---|---:|---:|---|'])
for sample in samples:
    cold = sample['cold_buffer_proof']
    lines.append(f"| {sample['condition']}・{sample['sample']} | {sample['first_result_ns']} | {sample['full_completion_ns']} | {cold['cached_target_pages']}／{cold['dirty_target_pages']} |")
lines.extend(['', '## COUNT前の計画・索引ページ数', '',
              '各ケースで、概数一覧・接続と状態のguard・推定SHOWPLAN XML・索引のlock設定とkey列・使用予定索引の全partitionページ数を保存した。推定計画はCOUNTを実行する実際のplan取得ではない。ページ数×8192は容量の参考であり、実走査IOや所要時間の上限ではない。NULL・取得不可を0にしない。', '',
              '| ケース | planned index | used bytes（容量参考） | reserved bytes（容量参考） |', '|---|---|---:|---:|'])
for condition in conditions:
    pages = condition.get('pages') or {}
    key = '／'.join(str(x.get('index')) for x in condition['planned_scans'])
    lines.append(f"| {condition['condition']} | {key} | {pages.get('used_bytes','UNKNOWN')} | {pages.get('reserved_bytes','UNKNOWN')} |")
lines.extend(['', '## 参考統計の値と取得時間', '',
              '各ケースのCOUNT前に取得したsys.partitions（index_id 0／1の合計）の観測値。正確値・誤差保証とは区別する。時間は一覧SQL全体の送信から全結果受領までであり、対象表だけのSQL時間ではない。各ケース1回の参考測定で、反復COUNTの中央値と同列の統計ではない。', '',
              '| ケース | dbo.Target approximate_rows | 一覧SQL全体 ms |', '|---|---:|---:|'])
for condition in conditions:
    found = [f for f in path.parent.glob('raw/*.json') if f.name.endswith('-'+condition['condition']+'-inventory.json')]
    assert len(found) == 1
    inventory = json.loads(found[0].read_text())
    rows = [r for result_set in inventory['raw'] for r in result_set
            if r.get('schema_name') == 'dbo' and r.get('table_name') == 'Target']
    assert len(rows) == 1
    value = rows[0].get('approximate_rows')
    lines.append(f"| {condition['condition']} | {value if value is not None else 'UNKNOWN'} | {inventory['elapsed_ns']/1e6:.6f} |")
lines.extend(['', '一覧SQLの全文・取得時刻は各ケースのraw inventory JSONと[固定SQL](../sqlserver/manual-count-100m/sql/inventory.sql)を参照。', ''])
lines.extend(['', '## 投入・設定変更・索引・AG構築の別時間', '',
              '主キー付き表への投入は100,000行×1000回のCOMMITを1度だけ実行した。各回のROW_COUNT相当・ID範囲・transaction終了を保存し、全COUNTの1億件と照合した。Aはこの同じDBを圧縮・checksum付きfull/log backupとNORECOVERY restoreでsecondaryへ渡した。2度目の1億行INSERTはしていない。', '',
              '| 処理（raw記録名） | 秒 |', '|---|---:|'])
load_ns = sum(r['end_ns']-r['start_ns'] for r in probe['commits'])
lines.append(f'| 1000回の投入・受領・記録の合計 | {load_ns/1e9:.6f} |')
for raw_file in sorted(path.parent.glob('raw/*.json')):
    if any(word in raw_file.name for word in ['fixture-index-ddl', '-ddl.json', 'ag-create-and-backup', 'ag-manual-restore-join']):
        raw = json.loads(raw_file.read_text())
        elapsed = raw.get('elapsed_ns', raw['end_ns']-raw['start_ns'])
        lines.append(f'| {raw_file.name} | {elapsed/1e9:.6f} |')
lines.extend(['', '## 環境・再現・解釈の限界', '',
              'MacBook Air（Mac16,12）、Apple M4、CPU 10コア（性能4＋効率6）、32 GB、macOS 26.5.2（25F84）。ColimaはVZ・virtiofs、4 vCPU、Docker認識メモリ8,307,171,328 bytes。各SQL Serverコンテナは3 CPU・4 GiB、SQL memory 2048 MB、ARM64クライアントは1 CPU・1 GiB。実設定・image/server version・dotnet情報・locked dependency graphを記録した。', '',
              f"今回の両ノードの実行バージョンはSQL Server 2022 {primary_runtime['product_version']}、editionは{primary_runtime['edition']}。image tagだけでなく、各ノードの実サーバーのProductVersionと@@VERSIONを保存・照合した。", '',
              'SQL Server Linux amd64はRosetta経由の非サポート診断構成。両instanceのbinfmt登録と実PID/executableを確認し、全18回後にも再確認した。QEMUへ切り替えていない。native性能へ換算せず、MySQLとの製品速度順位にも使わない。', '',
              'AGはCLUSTER_TYPE=NONEのread-scale用で、HA・自動failoverを検証したものではない。B／C／DとAでは稼働instance数が異なる。固定順序・OS/host cache・compile・温度や背景負荷等は制御しておらず、3標本は記述統計だけである。状態だけの因果的な速度差、全設定での速度、本番での安全な行数閾値は保証しない。', '',
              '表はBIGINT主キー＋INT NOT NULL 99列の固定長・404 bytesの値部分を持つ生成データ。idは1〜1億、c001は100k種類、c002は約80%が0、c003は高cardinality、c004〜c099は列番号の定数。圧縮・TDE・LOB等を代表しない。MySQLのidはUNSIGNED、SQL Serverはsignedで、対象の正の値は同じ。RLSを持たない新規fixtureであり、SAで任意のRLSを無視できるという意味ではない。', '',
              '行versioningやNOLOCKでもSch-Sと資源競合は残る。業務負荷はなく、サービス負荷予算はUNAVAILABLE。完了・fixture一致・Dの観測値・buffer消去・接続解放・資源消去を区別する。', '',
              '```sh', 'mise -C sqlserver/manual-count-100m run all', '```', '',
              '[固定マニュアル](../sqlserver/manual-count-100m/manual/sql-server.md)／[再現用README](../sqlserver/manual-count-100m/README.md)／[Mac・VM仕様](../results/reviews/sqlserver-manual-count-100m/host-spec.json)。', '',
              '## 根拠と判定', '',
              f"- [新規runのresult.json](../{path.relative_to(root)}) と同ディレクトリのprobe・raw・plan・monitor・cleanup・CLIログ。",
              '- [所有者監査](../results/reviews/sqlserver-manual-count-100m/owner-audit.json)／[source・文書の固定](../results/reviews/sqlserver-manual-count-100m/owner-proof.json)。', '',
              f"実験完了：{data['completion']}。fixture照合：{data['precision']}。接続解放：{data['transaction_release']}。資源消去：{data['cleanup']}。サービス負荷予算：UNAVAILABLE。最終Sol 2役の判定は別のreview記録を参照。", ''])
lines.extend(['', '## 先行する失敗実行（今回の標本に含めない）', '',
              '初回20261108T213706Zは1億行の投入後、監視SQLのWHEREと条件式の連結不備でCOUNT前に失敗した。20261108T224058Zは起動時のSQL PID集合の遷移を確認処理が拒否し、投入・COUNT前に失敗した。両実行は終了コード1、primaryの強制終了137を失敗のまま保存している。独立した所有者の後始末確認は、この失敗を計測成功へ変えるものではない。', '',
              '修正提案はSol author62・author63の別セッションに保存し、所有者が適用した。監視SQLは空のfixtureで投入前に実行確認し、起動PID遷移は元の期限内で全実行体の確認をやり直す。今回の実行とはsource・archiveを区別する。', '',
              '3回目の20261108T225157Z（SQL Server 2025）はB／C／Dの15回が完了したが、AG復元中に両ノードがSQLPALのseenByMonitor < NonYieldThreshold assertで終了した。実終了コードは両方1、OOMKilledはfalseで、Aの3回は未計測。fatalログ時刻は23:23:28と23:23:32 UTCの4秒差であり、同時性・原因は未確定。15回を今回のSQL Server 2022の標本へ混ぜていない。', '',
              'SQL Server 2022への変更は、類似する外部報告を参考に行った制御した再実行であり、SQL Server 2025の不具合原因や一般的な修正を証明しない。Rosettaは引き続き非サポート構成。失敗した3回目の所有者後始末では、対象資源・データ用パスの削除は確認できたが、空き容量が実行前の1 GiB以内まで戻る条件は満たさず、helperの終了コード1を保持した。今回の終了・後始末の判定とは分ける。', '',
              '- [SQL2025のAG復元失敗・分析](../results/reviews/sqlserver-manual-count-100m/ag-restore-failure-analysis.json)／[時刻の訂正](../results/reviews/sqlserver-manual-count-100m/ag-restore-failure-timing-correction.json)',
              '- [SQL2022切替・Sol提案](../results/reviews/sqlserver-manual-count-100m/engine-repair-author-result.json)／[所有者適用](../results/reviews/sqlserver-manual-count-100m/engine-repair-owner-application.json)',
              '- [3回目の失敗後の所有者後始末](../results/reviews/sqlserver-manual-count-100m/ag-restore-failure-owner-cleanup.json)',
              '- [監視SQL修正・所有者適用](../results/reviews/sqlserver-manual-count-100m/runtime-repair-owner-application.json)',
              '- [起動・終了処理修正・所有者適用](../results/reviews/sqlserver-manual-count-100m/entrypoint-repair-owner-application.json)',
              '- [初回の失敗後の所有者後始末](../results/reviews/sqlserver-manual-count-100m/monitor-syntax-failure-owner-cleanup.json)',
              '- [起動確認失敗後の所有者後始末](../results/reviews/sqlserver-manual-count-100m/startup-proof-failure-owner-cleanup.json)', ''])
(root/'docs/sqlserver-manual-count-100m.md').write_text('\n'.join(lines))
subprocess.run([sys.executable,str(root/'.riela/workflows/mysql-engine-count-100m-cold/scripts/sync-guidelines.py')],cwd=root,check=True)
print(json.dumps({'report':'docs/sqlserver-manual-count-100m.md','run':str(path.relative_to(root))}))
