"""Local lab only: TDE and opaque 50/100-column rows under a small service workload.
No payload is decrypted or returned by the count. The test isolates row width, index availability and write shape.
"""
import clocklib
import hashlib, time
import pymysql
from lablib import Workload, Sampler, connect, query, save, pin, METHOD_CPU
from protocol_outcomes import close_owned

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


def sample_expected(result):
    """Expected observations keep their raw status, errno and cleanup dimensions."""
    status = result.get('status')
    if status in ('query_failed', 'statement_budget_exhausted'):
        return result.get('errno') == 3024 and result.get('cleanupStatus') == 'rolled_back'
    return status in ('target_reached', 'exact_empty', 'precision_not_reached', 'budget_exhausted') and result.get('cleanupStatus') in ('rolled_back', 'not_open')


def outcome_failures(item):
    failures = list(item.get('errors', []))
    sampling = item.get('sampling') or (item.get('result') if item.get('method') == 'sample' else None)
    if sampling is not None and not sample_expected(sampling):
        failures.append('unexpected_sampling:' + str(sampling.get('status')))
    if item.get('count_status') == 'error' and not (
            item.get('errno') in (1317, 3024) and item.get('timer_counter_delta', 0) > 0):
        failures.append('unexpected_or_unknown_count_error')
    for field in ('method_cleanup', 'workload_cleanup', 'sampler_cleanup'):
        if field in item and item[field].get('confirmed') is not True:
            failures.append(field + '_unconfirmed')
    return failures


def persist_outcome(out, collection, item, persist):
    """Save the complete failing arm before raising; no next arm/cacheable green."""
    item['unexpected_failures'] = outcome_failures(item)
    out[collection].append(item)
    out['unexpected_sampling_failures'] = [
        {'collection': key, 'index': i, 'result': r.get('sampling', r.get('result'))}
        for key in ('static', 'service', 'arms') for i, r in enumerate(out.get(key, []))
        if (r.get('sampling') is not None or r.get('method') == 'sample')
        and not sample_expected(r.get('sampling', r.get('result', {})))]
    if item['unexpected_failures']:
        out.update(complete=False, reusable_mandatory_completion=False)
    persist()
    if item['unexpected_failures']:
        raise RuntimeError('unexpected wide outcome; diagnostics saved; later comparisons blocked: ' + repr(item['unexpected_failures']))


def arm(host, c, table, indexed, mix, name, sampling_method=None):
    result={'clock':clocklib.metadata(),'table':table,'secondary_index':indexed,'write_mix':mix,'arm':name,'errors':[]}
    w = sampler = q = None
    pid = None
    windows = []
    try:
        query(c, 'SELECT COUNT(*) FROM evict FORCE INDEX(PRIMARY)')
        query(c, f'SELECT COUNT(*) FROM {table} FORCE INDEX(PRIMARY) WHERE id<={HOT_ROWS}')
        write = f'UPDATE {table} SET ' + ('k=k+1' if mix=='indexed' else 'v=v+1') + ' WHERE id=%s'
        w = Workload(table, max_id=HOT_ROWS, head=HOT_ROWS, host=host, readers=2, writers=2, rate=50, write_sql=write,
                     read_sql=f'SELECT v,c0 FROM {table} WHERE id=%s')
        sampler = Sampler(table, host=host, data_locks=False)
        w.start(); sampler.start()
        t0=clocklib.now(); start=state(c)
        time.sleep(BASE_SECONDS)
        t1=clocklib.now(); before=state(c)
        windows.append(('baseline',t0,t1,start,before))
        try:
            if name in ('count', 'sample'):
                q=connect(host, read_timeout=30)
                pid=q.thread_id()
                result['method_connection_id']=pid
                if name=='count':
                    query(q,'SET SESSION max_execution_time=1000')
                    query(q,'SET SESSION lock_wait_timeout=2')
                    query(q,'SET SESSION innodb_parallel_read_threads=1')
                    initial=int(query(q,"SHOW SESSION STATUS LIKE 'Max_execution_time_exceeded'")[0][1])
                    result['statement_started_at']=clocklib.now()
                    try:
                        result['count']=query(q,f'SELECT /*+ MAX_EXECUTION_TIME({LIMIT_MS}) */ COUNT(*) FROM {table}')[0][0]
                        assert result['count']==ROWS
                        result['count_status']='complete'
                    except pymysql.MySQLError as error:
                        result.update(count_status='error', errno=error.args[0], error=repr(error))
                    finally:
                        result['statement_finished_at']=clocklib.now()
                        result['statement_seconds']=result['statement_finished_at']-result['statement_started_at']
                    result['timer_counter_delta']=int(query(q,"SHOW SESSION STATUS LIKE 'Max_execution_time_exceeded'")[0][1])-initial
                    # Do not run another method query after an unexplained interruption.
                    if not outcome_failures(result):
                        result['plan']=[list(r) for r in query(q,f'EXPLAIN SELECT COUNT(*) FROM {table}')]
                else:
                    if sampling_method is None:raise ValueError('sampling callback required')
                    query(q,'SET SESSION innodb_parallel_read_threads=1')
                    result['method_started_at']=clocklib.now()
                    try:
                        result['sampling']=sampling_method(q)
                        result['count_status']=result['sampling']['status']
                    finally:
                        result['method_finished_at']=clocklib.now()
                        result['method_seconds']=result['method_finished_at']-result['method_started_at']
        finally:
            if q is not None:
                if pid is None:
                    q.close()
                    result['method_cleanup']={'confirmed':False,'status':'missing_owned_connection_id'}
                else:
                    result['method_cleanup']=close_owned(c,q,pid,query)
        if outcome_failures(result):
            raise RuntimeError('method outcome requires stop')
        time.sleep(max(0,TRIAL_SECONDS-(clocklib.now()-t1)))
        t2=clocklib.now(); after=state(c)
        windows.append(('trial',t1,t2,before,after))
        time.sleep(RECOVERY_SECONDS)
        t3=clocklib.now(); end=state(c)
        windows.append(('recovery',t2,t3,after,end))
    except Exception as error:
        result['errors'].append(repr(error))
        result['exception_errno']=error.args[0] if error.args and isinstance(error.args[0],int) else None
    finally:
        if w is not None:
            try:
                w.finish(confirm_server=True)
            except Exception as error:
                result['errors'].append('workload_stop:' + repr(error))
            result['workload_cleanup']=getattr(w,'cleanup',{'confirmed':False,'status':'missing'})
            result['arrival_accounting']=w.accounting
            result['raw_service_samples']=w.samples
            result['raw_service_sample_format']='arrival-tuple-v2'
        if sampler is not None:
            try:
                sampler.finish()
                alive=sampler.thread.is_alive()
                result['sampler_cleanup']={'confirmed':not alive,'status':'thread_alive' if alive else 'joined'}
            except Exception as error:
                result['sampler_cleanup']={'confirmed':False,'error':repr(error)}
                result['errors'].append('sampler_stop:' + repr(error))
            result['server_samples']=sampler.rows
    # Raw/censored arrivals survive even when a normal window was not completed.
    result['windows_completed']=[label for label,*_ in windows]
    if w is not None and sampler is not None:
        try:
            for label,a,b,x,y in windows:
                result[label]={'window':clocklib.interval(a,b),'service':w.window(a,b),'server':sampler.window(a,b),'counter_delta':change(x,y),'state_start':x,'state_end':y,'seconds':round(b-a,3)}
            if 't1' in locals():
                trial_end=locals().get('t2',w.accounting.get('cutoff',t1))
                result['trial_service_samples']=[r for r in w.samples if t1<=r[0]<trial_end]
            for prefix in ('statement','method'):
                if prefix+'_started_at' in result and prefix+'_finished_at' in result:
                    result['actual_'+prefix+'_window']=clocklib.interval(result[prefix+'_started_at'],result[prefix+'_finished_at'])
                    result['actual_'+prefix+'_service']=w.window(result[prefix+'_started_at'],result[prefix+'_finished_at'])
                    result['actual_'+prefix+'_server']=sampler.window(result[prefix+'_started_at'],result[prefix+'_finished_at'])
        except Exception as error:
            result['errors'].append('window_summary:' + repr(error))
    result['clock_end']=clocklib.metadata()
    result['unexpected_failures']=outcome_failures(result)
    return result


def run(script_id, host, version, columns):
    pin(METHOD_CPU)
    query(connect(host,db='mysql'),'CREATE DATABASE IF NOT EXISTS bench')
    c=connect(host,read_timeout=600)
    query(c,"UPDATE performance_schema.setup_consumers SET ENABLED='YES' WHERE NAME='events_statements_cpu'")
    out={'id':script_id,'version':version,'columns':columns,'rows':ROWS,'buffer_pool_bytes':query(c,'SELECT @@innodb_buffer_pool_size')[0][0],
         'limit_ms':LIMIT_MS,'repeat_pairs':3,'service_write_per_s':100,'service_read_per_s':100,'hot_rows':HOT_ROWS,
         'baseline_seconds':BASE_SECONDS,'trial_seconds':TRIAL_SECONDS,'recovery_seconds':RECOVERY_SECONDS,
         'complete':False,'reusable_mandatory_completion':False,
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
                        persist_outcome(out,'arms',r,lambda:save(script_id,out))
                        print(script_id,indexed,mix,repeat,table,name,r.get('count_status'),r.get('statement_seconds'),flush=True)
    out.update(complete=True,reusable_mandatory_completion=True)
    save(script_id,out)
