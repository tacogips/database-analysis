#!/usr/bin/env python3
"""Test a distribution-free estimator using uniform key cells as sampling units.
Each draw has a known selection probability; a hard per-cell count bound
prevents a misleading narrow interval when a sample misses dense islands. Local synthetic data only.
"""
import hashlib, time, sys, math
from collections import Counter
from decimal import Decimal
from lablib import connect, query, save
from samplinglib import sample

ID='requested_probability_sampling'
host,version=sys.argv[1:3]
query(connect(host,db="mysql"),"CREATE DATABASE IF NOT EXISTS bench")
c=connect(host,read_timeout=900)
query(c,'SET SESSION sql_log_bin=0')
query(c,'DROP DATABASE IF EXISTS req_sampling')
query(c,'CREATE DATABASE req_sampling')
query(c,'SET SESSION cte_max_recursion_depth=100001')
query(c,'CREATE TABLE bench.seq1m (n INT PRIMARY KEY)')
query(c,'INSERT INTO bench.seq1m WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n<100000) SELECT n FROM s')
out={'id':ID,'version':version,'encryption':'InnoDB TDE','buffer_pool_bytes':query(c,'SELECT @@innodb_buffer_pool_size')[0][0],'source_sha256':hashlib.sha256(open('/work/bench/samplinglib.py','rb').read()).hexdigest(),
     'bounds_sha256':hashlib.sha256(open('/work/bench/sampling_bounds.py','rb').read()).hexdigest(),
     'cases':{},'confidence':.95,'target_relative_error':.03,
     'limitations':['A consistent snapshot holds table MDL and can retain undo. This is not a default primary procedure.',
                    'Requires a single integer primary key. Unknown payload contents need not be inspected.',
                    'PRNG uniform draws model is assumed. Empirical coverage in a finite fixture set does not prove all production conditions.',
                    'No host-cache reset or service-impact measurement. Runtime is a local comparison, not a safe time threshold.',
                    'The hard key-domain upper bound can make intervals very wide on sparse domains; never replace failure with a narrow observed-variance interval.']}
from samplinglib import bounds
out['bound_checks']={'empty_sample':bounds(0,512,1000,200,200000,Decimal('0.05')),'binomial_miss_probabilities':{}}
assert out['bound_checks']['empty_sample']['lower']==0 and out['bound_checks']['empty_sample']['upper']>0
for probability in (.001,.1,.5,.9):
 missed=0;truth=round(10000*probability)
 for k in range(65):
  band=bounds(k*10,64,1000,10,10000,Decimal('0.05'))
  if not band['lower']<=truth<=band['upper']:missed+=math.comb(64,k)*probability**k*(1-probability)**(64-k)
 assert missed<=.05
 out['bound_checks']['binomial_miss_probabilities'][str(probability)]=missed
COUNTERS=('Innodb_data_read' ,'Innodb_buffer_pool_reads','Innodb_buffer_pool_read_ahead')
def counters():
 return {k:int(v) for k,v in query(c,"SELECT VARIABLE_NAME,VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME IN ("+','.join('%s' for _ in COUNTERS)+')',COUNTERS)}
def delta(a,b):return {k:b[k]-a[k] for k in a}
try:
 for name,expression,pad,rows in (('dense','n+{off}',32,250000),('gaps','(n+{off})*10000',32,250000),
                                  ('islands','CASE WHEN n+{off}<=125000 THEN n+{off} ELSE n+{off}+10000000000 END',32,250000),
                                  ('negative','n+{off}-125001',32,250000),('wide','n+{off}',1000,3000000),
                                  ('moderate_gaps','n+{off}',1000,3000000),('uneven_density','n+{off}',1000,3000000)):
  query(c,f'CREATE TABLE req_sampling.{name} (id BIGINT PRIMARY KEY,pad VARBINARY({pad}) NOT NULL) ENCRYPTION=\'Y\'')
  inserted=0
  for off in range(0,rows,25000):
   predicate='n<=25000'
   if name=='moderate_gaps':predicate+=f' AND MOD(CRC32(n+{off}),5)<>0'
   if name=='uneven_density':predicate+=f' AND (n+{off}<=1500000 OR MOD(CRC32(n+{off}),2)=0)'
   query(c,f"INSERT INTO req_sampling.{name} SELECT {expression.format(off=off)},REPEAT('x',{pad}) FROM bench.seq1m WHERE {predicate}")
   inserted+=query(c,'SELECT ROW_COUNT()')[0][0]
  query(c,f'ANALYZE TABLE req_sampling.{name}')
  query(c,'SET SESSION information_schema_stats_expiry=0')
  q=connect(host,read_timeout=900)
  table=f'req_sampling.{name}'
  before=counters();t=time.monotonic();truth=query(q,f'SELECT COUNT(*) FROM {table}')[0][0]
  item={'truth':truth,'inserted_rows_oracle':inserted,'full_count_seconds':round(time.monotonic()-t,4),'full_count_counters':delta(before,counters()),
        'metadata':query(c,"SELECT TABLE_ROWS,DATA_LENGTH,INDEX_LENGTH FROM information_schema.TABLES WHERE TABLE_SCHEMA='req_sampling' AND TABLE_NAME=%s",(name,))[0], 'runs':[]}
  assert truth==inserted
  out['cases'][name]=item;save(ID,out)
  for order,seed in enumerate((1,7,42,317,991)):
   def exact_comparison():
    query(q,"SET SESSION max_execution_time=0")
    before=counters();begin=time.monotonic();actual=query(q,f"SELECT COUNT(*) FROM {table}")[0][0]
    assert actual==truth
    return {"seconds":round(time.monotonic()-begin,4),"counters":delta(before,counters())}
   comparison=exact_comparison() if order%2==0 else None
   before=counters();r=sample(q,table,seed);r['counters']=delta(before,counters());r['comparison_count']=comparison or exact_comparison();r['order']='count_then_sample' if order%2==0 else 'sample_then_count'
   if 'lower' in r:
    r['interval_covers_truth']=r['lower']<=truth<=r['upper']
    r['actual_error_pct']=float(abs(Decimal(r['estimate'])-truth)*100/truth)
   item['runs'].append(r);save(ID,out)
   print(name,seed,r['status'],r.get('draws'),r.get('actual_error_pct'),r.get('relative_error_bound'),r['seconds'],flush=True)
  q.close()
 out['status_counts']=dict(Counter(r['status'] for case in out['cases'].values() for r in case['runs']))
 out['unexpected_failures']=[{'case':name,'seed':r['seed'],'status':r['status']} for name,case in out['cases'].items() for r in case['runs'] if r['status'] in ('query_failed','cleanup_failed','inapplicable')]
 out['complete']=True  # all fixture iterations ended; this does not mean all runs met the precision target
 save(ID,out)
finally:
 try:
  query(c,'SET SESSION lock_wait_timeout=5')
  query(c,'DROP DATABASE IF EXISTS req_sampling')
  out['fixture_cleanup']='confirmed'
  save(ID,out)
 except Exception:
  out['fixture_cleanup']='failed'
  save(ID,out)
  raise
if out.get('unexpected_failures'):
 raise SystemExit(1)
