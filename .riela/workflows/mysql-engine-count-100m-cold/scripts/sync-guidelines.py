"""Synchronize only owned result blocks in the three user-requested guide pages."""
from pathlib import Path
from datetime import datetime,timezone
import json,hashlib,statistics,re
root=Path(__file__).resolve().parents[4]
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
mysql_paths=sorted((root/'results/mysql-engine-count-100m-cold/runs').glob('*/result.json'))
mysql_path=mysql_paths[-1] if mysql_paths else None
mysql=json.loads(mysql_path.read_text()) if mysql_path else None
current_mysql_path=mysql_path
mysql_reference=False
def full_samples(d):
 conditions=[c for stage in (d or {}).get('stages',[]) for c in stage['conditions']]
 return len(conditions)==4 and all(len(c['counts'])==3 and all(x.get('status')=='valid' for x in c['counts']) for c in conditions)
# Use the current run once its complete InnoDB set is available; leave MyISAM pending.
# Until then preserve an entire preceding reference run, never mixing its cells with a new run.
current_innodb_complete=any(s.get('engine')=='InnoDB' and len(s['conditions'])==3 and all(c.get('completed_valid_counts')==3 for c in s['conditions']) for s in (mysql or {}).get('stages',[]))
if not full_samples(mysql) and not current_innodb_complete:
 for previous in reversed(mysql_paths[:-1]):
  candidate=json.loads(previous.read_text())
  if any(c.get('completed_valid_counts')==3 for st in candidate.get('stages',[]) for c in st['conditions']):
   mysql_path=previous;mysql=candidate;mysql_reference=True;break
sql_paths=sorted((root/'results/sqlserver/manual-count-100m/runs').glob('*/result.json'))
sql_path=sql_paths[-1] if sql_paths else None
sql=json.loads(sql_path.read_text()) if sql_path else None
BEGIN='<!-- count-performance-100m:start -->';END='<!-- count-performance-100m:end -->'
changes=[]
def timing(ns):
 return f'{ns/1e9:.3f}秒' if ns>=1e9 else f'{ns/1e6:.3f} ms'
def stats(values):return [timing(min(values)),timing(statistics.median(values)),timing(max(values))]
def values(raw):
 items=['UNKNOWN' if x is None else f'{x:,}' for x in raw]
 return items[0]+'（3回とも）' if len(items)==3 and len(set(items))==1 else '／'.join(items)
def write(path,body):
 p=root/path;text=p.read_text();before=sha(p);block=BEGIN+'\n'+body.strip()+'\n'+END
 if BEGIN in text:
  assert text.count(BEGIN)==text.count(END)==1
  text=re.sub(re.escape(BEGIN)+r'.*?'+re.escape(END),lambda _:block,text,flags=re.S)
 else:
  needle='## 性能調査と補足' if path.endswith(('mysql-innodb.md','mysql-myisam.md')) else '## 性能調査・判断理由'
  assert needle in text
  position=text.index(needle);stop=text.index('\n',position)+1
  text=text[:stop]+'\n'+block+'\n'+text[stop:]
 if path.endswith('sql-server.md'):
  text=text.replace('この手順自体のDB実行検証は未実施です。特にNOLOCK（D）の精度・性能はここで引用する実測の対象ではありません。','上記リンクの過去観測には、上の表に区別した1億行・100列でのA／B／C／D各分岐の比較は含まれません。特にNOLOCK（D）の精度・性能は、上記の過去観測の対象外です。')
 if path.endswith('mysql-innodb.md'):
  text=text.replace('repoの1億行細表と75万行の幅広表は別条件で、1億行×100列の性能は未計測です。','repoの1億行細表と75万行の幅広表は別条件です。追加した1億行×100列の計測条件・進捗・結果は、本ページの「1億行・100列での件数と取得時間」を参照してください。')
 p.write_text(text);changes.append({'path':path,'beforeSha256':before,'afterSha256':sha(p)})
all_conditions={c['id']:c for s in (mysql or {}).get('stages',[]) for c in s['conditions']}
reference_notice='**先行の通し実行はMyISAM開始前の容量チェックで停止。以下は完了したInnoDB標本の参考値で、現在の再実行の値とは混ぜていない。全体の完了・後始末・最終査読は未確定。**'
if mysql_reference and mysql_path.parent.name=='20261108T172604Z-ca74a6381f9f93ff0ed6ed7c87471500':
 reference_notice='**先行の通し実行はMyISAM投入中、投入期限の見直しのため中断。以下は完了したInnoDB標本の参考値で、現在の再実行の値とは混ぜていない。全体の完了・後始末・最終査読は未確定。**'
for engine,ids,file in [('InnoDB',['innodb_primary_only','innodb_secondary_present','innodb_composite_secondary'],'mysql-innodb.md'),('MyISAM',['myisam_primary_only'],'mysql-myisam.md')]:
 lines=['', '### 1億行・100列での件数と取得時間', '',
        'MySQL 8.4.13、BIGINT主キー1列＋INT NOT NULL 99列の静止表。MacBook Air／M4・CPU 10コア・32 GB・macOS 26.5.2、VM 4 vCPU・約8 GB、MySQLコンテナ3 CPU・4 GiB（InnoDB buffer pool 1 GiB）での参考値。', '',
        '各COUNT前に専用MySQLを正常再起動し、DBバッファの自動復元を無効化する。その後、手順のメタデータ・索引確認を行ってからCOUNTを測る。OS・VM・Mac側のファイルキャッシュは未制御。取得時間は要求送信からスカラー受信までで、CLI・転送時間を含み、接続開始・再起動・投入・索引作成を含まない。', '',
        reference_notice if mysql_reference else ('**全体は未完了で、完了・後始末・最終査読は未確定。**' if not mysql or mysql.get('exit_status')!=0 else '以下は専用labの取得結果。完了・正確値・DBバッファ方針・後始末の判定は根拠ファイルを参照し、サービスへの無影響とは区別する。'), '',
        '| 条件・取得法 | 返却件数 | 最小 | 中央値 | 最大 |','|---|---:|---:|---:|---:|']
 labels={'innodb_primary_only':'PKのみ','innodb_secondary_present':'単一列副索引あり','innodb_composite_secondary':'複合副索引あり','myisam_primary_only':'PKのみ'}
 for name in ids:
  condition=all_conditions.get(name);samples=condition.get('counts',[]) if condition else []
  complete=len(samples)==3 and all(s.get('status')=='valid' for s in samples)
  if not complete:
   for method in ['TABLE_ROWS','COUNT(*)']:lines.append(f'| {labels[name]}・{method} | 未完了 | 未計測・未確定 | 未計測・未確定 | 未計測・未確定 |')
   continue
  metadata=[s['manual_reference']['table_rows'] for s in samples]
  for method,raw,times in [('TABLE_ROWS',[m['value'] for m in metadata],[m['caller_elapsed_ns'] for m in metadata]),('COUNT(*)',[s['value'] for s in samples],[s['caller_elapsed_ns'] for s in samples])]:
   lines.append('| '+labels[name]+'・'+method+' | '+values(raw)+' | '+' | '.join(stats(times))+' |')
  errors=[m['signed_error_pct'] for m in metadata]
  error_text='／'.join('UNKNOWN' if e is None else f'{e:+.6f}%' for e in errors)
  lines.extend(['',f'{labels[name]}のTABLE_ROWS誤差（既知の1億件に対する取得順の3値）：{error_text}。この表での観測であり、他の表の精度保証ではない。',''])
 # Keep explanations outside the Markdown table, after all its rows.
 rows=[x for x in lines if x.startswith('|')];other=[x for x in lines if not x.startswith('|')]
 header_end=next(i for i,x in enumerate(lines) if x.startswith('|'))
 lines=lines[:header_end]+rows+['']+[x for x in lines[header_end:] if x and not x.startswith('|')]
 lines.extend(['','[全3回の生値・実行計画・索引ページ数・Mac仕様と限界](../mysql-engine-count-100m.md)／[生結果](../../'+str(mysql_path.relative_to(root))+')。NULL・不明を0にせず、統計の0を空表の証明にしない。サービス負荷予算は未評価。'])
 if engine=='MyISAM':lines.extend(['','MyISAMは静止カタログでの比較に限る。短時間でも暗黙の表read lockは残り、この数値からサービス稼働中の無影響やロックゼロを認定しない。'])
 write('docs/guidelines/'+file,'\n'.join(lines))
probe=(sql or {}).get('probe',{});samples=probe.get('samples',[])
post_capacity_path=root/'results/reviews/sqlserver-manual-count-100m/owner-post-terminal-capacity.json'
post_capacity=json.loads(post_capacity_path.read_text()) if post_capacity_path.exists() else None
sql_post_capacity=bool(sql_path and post_capacity and post_capacity['rawRun']==str(sql_path.relative_to(root)) and post_capacity['rawRunSha256']==sha(sql_path) and post_capacity['status']=='passed_post_terminal_owned_cleanup_and_capacity' and sql.get('exit_status')==1 and sql.get('error') is None and sql.get('cleanup_errors')==['RuntimeError: host capacity not restored within 1 GiB (shared-host observation)'] and probe.get('completion')=='passed_all_18_commands' and len(samples)==18 and all(s.get('accepted') for s in samples) and post_capacity['capacityRestoredWithin1GiBAtAdditionalCheck'])
lines=['','### 1億行・100列でのDB状態別SQLの比較','',
       'Rosetta上の専用SQL Server検証。Bでは主キーのみ・単一列副索引・複合副索引を比較し、A／B／C／Dの状態比較は同じ複合副索引・同じ1億行で行う。各ケース3回、計18回。COUNT前にDBデータバッファを消去・確認し、OS・VM・Mac側のキャッシュは未制御とする。','',
       'SQL Serverの実測は未完了。未計測値を0秒・0件として表示しない。' if not sql or (sql.get('exit_status')!=0 and not sql_post_capacity) else '各状態を実際の専用DBで確認した取得結果。NOLOCKのDは静止labでの観測値であり、更新中の正確値を保証しない。','',
       '| DB状態・索引 | COUNTのSQL | 返却件数 | 最小 | 中央値 | 最大 |','|---|---|---:|---:|---:|---:|']
for case,label in [('B-clustered','B：RCSI・主キー'),('B-narrow','B：RCSI・単一列副索引'),('B-composite','B：RCSI・複合副索引'),('C-composite','C：SNAPSHOT・複合副索引'),('D-composite','D：NOLOCK・複合副索引'),('A-composite','A：実AG secondary・複合副索引')]:
 found=[s for s in samples if s.get('condition')==case];method='`SELECT COUNT_BIG(*) AS observed_rows FROM [dbo].[Target] WITH (NOLOCK);`' if case.startswith('D-') else '`SELECT COUNT_BIG(*) AS exact_rows FROM [dbo].[Target];`'
 if len(found)==3 and all(s.get('accepted') is True for s in found):
  row=values([s['value'] for s in found]);result=stats([s['first_result_ns'] for s in found]);lines.append('| '+label+' | '+method+' | '+row+' | '+' | '.join(result)+' |')
 else:lines.append('| '+label+' | '+method+' | 未完了 | 未計測・未確定 | 未計測・未確定 | 未計測・未確定 |')
lines.extend(['','[各状態の実設定・SQL・全標本・Mac仕様・限界](../sqlserver-manual-count-100m.md)。SQL Server amd64をARM MacのRosettaで動かす非サポート構成の診断値であり、ネイティブ環境への換算やMySQLとの製品速度順位には使わない。RCSI／SNAPSHOT／AG／NOLOCKでもSch-Sと資源競合は残り、サービス無劣化の実測ではない。'])
if sql_post_capacity:
 lines.extend(['','全18回の計測とSQL両ノード・クライアントの正常終了0は確認済み。ただし、直後の容量回復の観測だけが失敗し、runnerの終了コード1は変更していない。資源・パスの消去と同じ1 GiB条件での後からの容量回復は[追加の所有者確認](../../results/reviews/sqlserver-manual-count-100m/owner-post-terminal-capacity.json)を参照。runnerの終了コード0を主張する結果ではない。'])
elif sql and sql.get('exit_status')==1:
 lines.extend(['','この実行は終了コード1で未完了。計測済み標本と未計測枠を区別し、失敗原因と後始末の詳細は上の実測レポートを参照。'])
write('docs/guidelines/sql-server.md','\n'.join(lines))
out=root/'results/reviews/guideline-results-integration';out.mkdir(parents=True,exist_ok=True)
(out/'latest-sync.json').write_text(json.dumps({'syncedAt':datetime.now(timezone.utc).isoformat(),'scriptSha256':sha(Path(__file__)),'mysqlResult':str(mysql_path.relative_to(root)) if mysql_path else None,'currentMySQLResult':str(current_mysql_path.relative_to(root)) if current_mysql_path else None,'mysqlReferenceWhileRetrying':mysql_reference,'sqlResult':str(sql_path.relative_to(root)) if sql_path else None,'documents':changes,'scope':'Only owned marker blocks; final source receipts/reviews must bind these three guide pages too.'},indent=2)+'\n')
print(json.dumps({'documents':changes,'mysqlStatus':(mysql or {}).get('status'),'sqlStatus':(sql or {}).get('status')}))
