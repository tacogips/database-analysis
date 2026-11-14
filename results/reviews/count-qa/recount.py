"""Offline only: reproduce descriptive accuracy aggregates from frozen evidence."""
from pathlib import Path
import hashlib,json,statistics
root=Path(__file__).resolve().parents[3]
mysql=root/'results/reference/review-20261107T155750Z-26743/results/requested_metadata_accuracy.json'
sqlserver=root/'results/reference/final-sqlserver-52556dd/results/sqlserver/runs/20261107T230105Z-8533a31e/result.json'
result={'classification':'offline_reaggregation_not_new_measurement','sources':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [mysql,sqlserver]},'mysql':{},'sqlserver':{}}
def summary(values):
 return {'observations':len(values),'median_absolute_percent':statistics.median(map(abs,values)), 'signed_min_percent':min(values),'signed_max_percent':max(values),'within_percent':{str(k):sum(abs(v)<=k for v in values) for k in [1,5,10]}}
d=json.loads(mysql.read_text())
for phase in ['before_analyze','after_analyze']:
 errors=[];excluded=[]
 for name,row in d[phase].items():
  truth=d['before_analyze'][name]['truth']
  if name in {'t_myisam','t_memory','t_archive'} or truth<=0:
   excluded.append(name);continue
  errors.append(100*(int(row['table_rows_default_cache']['value'])-truth)/truth)
 result['mysql'][phase]={**summary(errors),'excluded':excluded,'method':'table_rows_default_cache','sample_pages':d['default_sample_pages']}
d=json.loads(sqlserver.read_text())
for method in ['dmv','partitions','optimizer:lab_id']:
 rows=[r for r in d['errors'] if r['method']==method and r['truth']>0]
 result['sqlserver'][method]={**summary([100*(r['value']-r['truth'])/r['truth'] for r in rows]),'tables':len({r['table'] for r in rows})}
print(json.dumps(result,ensure_ascii=False,indent=2))
