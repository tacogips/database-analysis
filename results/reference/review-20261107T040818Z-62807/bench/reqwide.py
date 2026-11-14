"""Local lab only: TDE and opaque 50/100-column rows under a small service workload.
No payload is decrypted or returned by the count. The test isolates row width, index availability and write shape.
"""
import hashlib, time
import pymysql
from lablib import Workload, Sampler, connect, query, save, pin, METHOD_CPU

ROWS = 750_000
HOT_ROWS = 50_000
LIMIT_MS = 1000
BASE_SECONDS, TRIAL_SECONDS, RECOVERY_SECONDS = 5, 3, 7
COUNTERS = ('Innodb_data_read', 'Innodb_data_written', 'Innodb_buffer_pool_read_ahead',
            'Innodb_buffer_pool_reads', 'Innodb_buffer_pool_read_requests', 'Threads_running')


def state(c):
    out = {k: int(v) for k, v in query(c, "SELECT VARIABLE_NAME,VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME IN (" + ','.join('%s' for _ in COUNTERS) + ')', COUNTERS)}
    out['foreground_cpu_ps'] = int(query(c, 'SELECT COALESCE(SUM(SUM_CPU_TIME),0) FROM performance_schema.events_statements_summary_global_by_event_name')[0][0])
    out['hll'] = int(query(c, "SELECT `count` FROM information_schema.INNODB_METRICS WHERE name='trx_rseg_history_len'")[0][0])
    r = query(c, "SELECT SUM(SUM_TIMER_READ),SUM(SUM_TIMER_WRITE) FROM performance_schema.file_summary_by_event_name WHERE EVENT_NAME LIKE 'wait/io/file/innodb/%%'")[0]
    out['file_read_wait_ps'], out['file_write_wait_ps'] = (int(x or 0) for x in r)
    return out


def change(a, b):
    return {k: b[k]-a[k] for k in b if k not in ('hll', 'Threads_running')}


def build(c, columns):
    query(c, 'SET SESSION sql_log_bin=0')
    query(c, 'SET SESSION cte_max_recursion_depth=100001')
    query(c, 'CREATE TABLE seq (n INT PRIMARY KEY, opaque VARBINARY(32) NOT NULL)')
    query(c, "INSERT INTO seq WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n<100000) SELECT n,UNHEX(SHA2(n,256)) FROM s")
    payload = ','.join(f'c{i} VARBINARY(32) NOT NULL' for i in range(columns-3))
    for name, encryption in (('plain','N'),('enc','Y')):
        query(c, f"CREATE TABLE {name} (id BIGINT PRIMARY KEY,k BIGINT NOT NULL,v BIGINT NOT NULL,{payload}) ENGINE=InnoDB ENCRYPTION='{encryption}'")
        for off in range(0, ROWS, 25000):
            query(c, f"INSERT INTO {name} SELECT n+{off},CRC32(n+{off}),0,{','.join(['opaque']*(columns-3))} FROM seq WHERE n<=25000")
    query(c, 'CREATE TABLE evict (id INT PRIMARY KEY,pad VARBINARY(255) NOT NULL)')
    for off in range(0, 1300000, 100000):
        query(c, f"INSERT INTO evict SELECT n+{off},REPEAT(opaque,7) FROM seq")
    query(c, 'ANALYZE TABLE plain,enc,evict')


def arm(host, c, table, indexed, mix, name, sampling_method=None):
    query(c, 'SELECT COUNT(*) FROM evict FORCE INDEX(PRIMARY)')
    query(c, f'SELECT COUNT(*) FROM {table} FORCE INDEX(PRIMARY) WHERE id<={HOT_ROWS}')
    write = f'UPDATE {table} SET ' + ('k=k+1' if mix=='indexed' else 'v=v+1') + ' WHERE id=%s'
    w = Workload(table, max_id=HOT_ROWS, head=HOT_ROWS, host=host, readers=2, writers=2, rate=50, write_sql=write,
                 read_sql=f'SELECT v,c0 FROM {table} WHERE id=%s')
    sampler = Sampler(table, host=host, data_locks=False)
    w.start(); sampler.start()
    t0=time.time(); start=state(c)
    result={'table':table,'secondary_index':indexed,'write_mix':mix,'arm':name}
    try:
        time.sleep(BASE_SECONDS)
        t1=time.time(); before=state(c)
        if name=='count':
            q=connect(host, read_timeout=30)
            query(q,'SET SESSION max_execution_time=1000')
            query(q,'SET SESSION lock_wait_timeout=2')
            query(q,'SET SESSION innodb_parallel_read_threads=1')
            initial=int(query(q,"SHOW SESSION STATUS LIKE 'Max_execution_time_exceeded'")[0][1])
            begin=time.monotonic()
            result["statement_started_at"]=time.time()
            try:
                result['count']=query(q,f'SELECT /*+ MAX_EXECUTION_TIME({LIMIT_MS}) */ COUNT(*) FROM {table}')[0][0]
                assert result['count']==ROWS
                result['count_status']='complete'
            except pymysql.MySQLError as e:
                result['count_status']='error';result['errno']=e.args[0]
                if e.args[0] not in (1317,3024):raise
            result['statement_seconds']=round(time.monotonic()-begin,4)
            result['statement_finished_at']=time.time()
            result['timer_counter_delta']=int(query(q,"SHOW SESSION STATUS LIKE 'Max_execution_time_exceeded'")[0][1])-initial
            result['plan']=[list(r) for r in query(q,f'EXPLAIN SELECT COUNT(*) FROM {table}')]
            q.close()
        elif name=='sample':
            if sampling_method is None:raise ValueError('sampling callback required')
            q=connect(host,read_timeout=30)
            query(q,'SET SESSION innodb_parallel_read_threads=1')
            begin=time.monotonic()
            result['method_started_at']=time.time()
            try:
                result['sampling']=sampling_method(q)
                result['count_status']=result['sampling']['status']
            finally:
                result['method_finished_at']=time.time()
                result['method_seconds']=round(time.monotonic()-begin,4)
                q.close()
        time.sleep(max(0,TRIAL_SECONDS-(time.time()-t1)))
        t2=time.time(); after=state(c)
        time.sleep(RECOVERY_SECONDS)
        t3=time.time(); end=state(c)
    finally:
        try:w.finish()
        finally:sampler.finish()
    for label,a,b,x,y in (('baseline',t0,t1,start,before),('trial',t1,t2,before,after),('recovery',t2,t3,after,end)):
        result[label]={'service':w.window(a,b),'server':sampler.window(a,b),'counter_delta':change(x,y),'state_start':x,'state_end':y,'seconds':round(b-a,3)}
    result['arrival_accounting']=w.accounting
    result['trial_service_samples']=[r for r in w.samples if t1<=r[0]<t2]
    if 'statement_started_at' in result:
        result['actual_statement_service']=w.window(result['statement_started_at'],result['statement_finished_at'])
    if 'method_started_at' in result:
        result['actual_method_service']=w.window(result['method_started_at'],result['method_finished_at'])
    result['server_samples']=sampler.rows
    return result


def run(script_id, host, version, columns):
    pin(METHOD_CPU)
    query(connect(host,db='mysql'),'CREATE DATABASE IF NOT EXISTS bench')
    c=connect(host,read_timeout=600)
    query(c,"UPDATE performance_schema.setup_consumers SET ENABLED='YES' WHERE NAME='events_statements_cpu'")
    out={'id':script_id,'version':version,'columns':columns,'rows':ROWS,'buffer_pool_bytes':query(c,'SELECT @@innodb_buffer_pool_size')[0][0],
         'limit_ms':LIMIT_MS,'repeat_pairs':3,'service_write_per_s':100,'service_read_per_s':100,'hot_rows':HOT_ROWS,
         'baseline_seconds':BASE_SECONDS,'trial_seconds':TRIAL_SECONDS,'recovery_seconds':RECOVERY_SECONDS,
         'source_sha256':hashlib.sha256(open('/work/bench/reqwide.py','rb').read()).hexdigest(),'arms':[],
         'limitations':['Synthetic opaque binary payload, not application column-encryption performance. No payload decryption.',
                        'Host cache cannot be cleared. Same Docker VM and host filesystem; no physical isolation inference.',
                        'CPU counter covers instrumented foreground statements, not total server CPU or every parallel worker.',
                        'Only 7 seconds recovery is observed. Short service windows and tails are noisy; this is not a universal safe time limit.',
                        '100 writes/s touches existing rows only; INSERT/DELETE and saturated traffic are not covered.']}
    save(script_id,out)
    build(c,columns)
    for indexed in (False,True):
        if indexed:
            for table in ('plain','enc'):query(c,f'ALTER TABLE {table} ADD KEY k_small(k)')
        query(c,'ANALYZE TABLE plain,enc')
        query(c,'SET SESSION information_schema_stats_expiry=0')
        out.setdefault('sizes',{})[str(indexed)]=[list(r) for r in query(c,"SELECT TABLE_NAME,DATA_LENGTH,INDEX_LENGTH,CREATE_OPTIONS FROM information_schema.TABLES WHERE TABLE_SCHEMA='bench' AND TABLE_NAME IN ('plain','enc')")]
        for mix in ('nonindexed','indexed'):
            for repeat in range(3):
                for table in (('plain','enc') if repeat%2==0 else ('enc','plain')):
                    for name in (('control','count') if repeat%2==0 else ('count','control')):
                        r=arm(host,c,table,indexed,mix,name);r['repeat']=repeat
                        out['arms'].append(r);save(script_id,out)
                        print(script_id,indexed,mix,repeat,table,name,r.get('count_status'),r.get('statement_seconds'),flush=True)
    out['complete']=True
    save(script_id,out)
