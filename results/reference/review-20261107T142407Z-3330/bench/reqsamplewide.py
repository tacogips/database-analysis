"""TDE + 50/100-column COUNT and bounded sampling, with and without a small secondary index.
The service windows diagnose resource/latency changes; they do not certify an SLO.
"""
import hashlib, time
import pymysql
from lablib import connect, query, save, pin, METHOD_CPU
from reqwide import build, arm, state, change, ROWS, persist_outcome
from protocol_outcomes import close_owned
from samplinglib import sample


def run(script_id, host, version, columns):
    pin(METHOD_CPU)
    query(connect(host,db='mysql'),'CREATE DATABASE IF NOT EXISTS bench')
    c=connect(host,read_timeout=600)
    query(c,"UPDATE performance_schema.setup_consumers SET ENABLED='YES' WHERE NAME='events_statements_cpu'")
    out={'id':script_id,'version':version,'columns':columns,'rows':ROWS,'encryption':'InnoDB TDE',
         'buffer_pool_bytes':query(c,'SELECT @@innodb_buffer_pool_size')[0][0],
         'static':[],'service':[],'complete':False,'reusable_mandatory_completion':False,'source_sha256':hashlib.sha256(open('/work/bench/reqsamplewide.py','rb').read()).hexdigest(),
         'limitations':['Host/VM cache uncontrolled. Eviction scan is an operation, not proof of a physical cold state.',
                        'Static ordering alternates but initial truth count/build and prior runs affect cache.',
                        'Synthetic opaque fixed-width payload; application decryption cost is not measured.',
                        'Service arrivals are 100 reads/s plus 100 writes/s on the configured hot set, not all production traffic.',
                        'Short diagnostic windows and finite repeats cannot certify an SLO or absence of impact.',
                        'COUNT and sampling have separate precision and time-budget outcomes; unfinished requests are censored.']}
    save(script_id,out)
    build(c,columns)
    for indexed in (False,True):
        if indexed:query(c,'ALTER TABLE enc ADD KEY k_small(k)')
        query(c,'ANALYZE TABLE enc')
        query(c,'SET SESSION information_schema_stats_expiry=0')
        out.setdefault('sizes',{})[str(indexed)]=query(c,"SELECT TABLE_ROWS,DATA_LENGTH,INDEX_LENGTH,CREATE_OPTIONS FROM information_schema.TABLES WHERE TABLE_SCHEMA='bench' AND TABLE_NAME='enc'")
        for repeat,seed in enumerate((1,7,42,317,991)):
            for method in (('count','sample') if repeat%2==0 else ('sample','count')):
                query(c,'SELECT COUNT(*) FROM evict FORCE INDEX(PRIMARY)')
                q=None;pid=None
                item={'secondary_index':indexed,'repeat':repeat,'seed':seed,'method':method,'errors':[]}
                begin=time.monotonic()
                try:
                    q=connect(host,read_timeout=120)
                    pid=q.thread_id();item['method_connection_id']=pid
                    query(q,'SET SESSION innodb_parallel_read_threads=1')
                    before=state(c);begin=time.monotonic()
                    if method=='count':
                        query(q,'SET SESSION max_execution_time=30000')
                        initial=int(query(q,"SHOW SESSION STATUS LIKE 'Max_execution_time_exceeded'")[0][1])
                        try:
                            value=query(q,'SELECT COUNT(*) FROM enc')[0][0]
                            assert value==ROWS
                            result={'status':'exact','rows':value}
                        except pymysql.MySQLError as error:
                            result={'status':'query_failed','errno':error.args[0],'error':repr(error)}
                            item.update(count_status='error',errno=error.args[0])
                        # Preserve the original error even if reading timer evidence fails.
                        item['result']=result
                        item['timer_counter_delta']=int(query(q,"SHOW SESSION STATUS LIKE 'Max_execution_time_exceeded'")[0][1])-initial
                    else:
                        result=sample(q,'bench.enc',seed,max_seconds=90)
                        if 'lower' in result:result['interval_covers_truth']=result['lower']<=ROWS<=result['upper']
                    item.update(seconds=round(time.monotonic()-begin,4),result=result)
                    item['counter_delta']=change(before,state(c))
                except Exception as error:
                    item['errors'].append(repr(error))
                    item.setdefault('result',{'status':'query_failed','error':repr(error),
                                             'errno':error.args[0] if error.args else None})
                finally:
                    if q is not None:
                        if pid is None:
                            q.close();item['method_cleanup']={'confirmed':False,'status':'missing_owned_connection_id'}
                        else:
                            item['method_cleanup']=close_owned(c,q,pid,query)
                persist_outcome(out,'static',item,lambda:save(script_id,out))
                print(script_id,'static',indexed,repeat,method,item['seconds'],result['status'],flush=True)
        for mix in ('nonindexed','indexed'):
            for repeat in range(3):
                order=('control','count','sample') if repeat%2==0 else ('sample','count','control')
                for method in order:
                    item=arm(host,c,'enc',indexed,mix,method,
                             sampling_method=lambda q,seed=repeat:sample(q,'bench.enc',seed,max_seconds=1))
                    item['repeat']=repeat
                    persist_outcome(out,'service',item,lambda:save(script_id,out))
                    print(script_id,'service',indexed,mix,repeat,method,item.get('count_status'),flush=True)
    out.update(complete=True,reusable_mandatory_completion=True)
    save(script_id,out)
