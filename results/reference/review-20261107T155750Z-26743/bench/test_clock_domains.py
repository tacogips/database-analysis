#!/usr/bin/env python3
"""DB-free live-path clock jumps, scheduler/child coordinates and public audit.

Only SQL, threading dispatch and waits are replaced. No standard time function
is monkeypatched; each module receives an injected clock dependency.
"""
import copy
from contextlib import redirect_stdout
import io
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import runpy
import sys
import threading
import time
import types
from unittest.mock import patch

try:
    import pymysql
except ImportError:
    client=types.ModuleType('pymysql'); cursors=types.ModuleType('pymysql.cursors')
    cursors.Cursor=cursors.DictCursor=object
    client.cursors=cursors;client.MySQLError=type('MySQLError',(Exception,),{})
    sys.modules.update({'pymysql':client,'pymysql.cursors':cursors})
import clocklib
import lablib
import reqwide
import experiments
import reqlib
import reqmix
import reqscale

STANDARD_TIME,STANDARD_MONOTONIC=time.time,time.monotonic
ROOT=Path(__file__).resolve().parent.parent
AUDITOR_PATH=ROOT/'audit/audit-arrival-windows.py'
AUDITOR_SOURCE=''
AUDIT={}
if __name__=='__main__':
    AUDITOR_SOURCE=sys.stdin.read() if '--auditor-stdin' in sys.argv else AUDITOR_PATH.read_text()
    assert AUDITOR_SOURCE, 'missing independent public auditor source'
    exec(compile(AUDITOR_SOURCE,str(AUDITOR_PATH),'exec'),AUDIT)

class FakeTime:
    def __init__(self,jump=0): self.value=100.;self.jump=jump;self.shift=0
    def monotonic(self): return self.value
    def time(self): return 1794060000+self.value+self.shift
    def sleep(self,n): self.value+=n
    def get_clock_info(self,n): return types.SimpleNamespace(implementation='injected-system-monotonic',monotonic=True,adjustable=False)

class Connection:
    def thread_id(self): return 17
    def close(self): pass

class Worker:
    # Synchronous dispatch still executes the real worker closure/scheduler.
    def __init__(self,target,args=(),**kw): self.target=target;self.args=args
    def start(self): self.target(*self.args)
    def join(self,*a,**kw): pass
    def is_alive(self): return False

class Pipe:
    def send(self,payload): self.payload=payload


def run_case(jump, role='count'):
    fake=FakeTime(jump); wall_method=[]
    def query(c,sql,args=None):
        if 'Max_execution_time_exceeded' in sql: return [('timer',0)]
        if sql.startswith('SELECT /*+'):
            wall_method.append(fake.time()); fake.sleep(.25);fake.shift+=jump;wall_method.append(fake.time())
            return [(reqwide.ROWS,)]
        if 'SELECT VARIABLE_NAME' in sql: return [('Innodb_rows_read',fake.value*10)]
        if 'INNODB_METRICS' in sql: return [(1,)]
        if 'INNODB_TRX' in sql: return [(0,0)]
        return [(0,)]

    class ReplayLoad:
        window=lablib.Workload.window
        def __init__(self,*a,**cfg): self.cfg=cfg;self.clock=clocklib.metadata();self.samples=[];self.accounting={}
        def start(self): self.origin=fake.value
        def finish(self,confirm_server=True):
            end=fake.value+.01; saved=fake.value
            fake.value=self.origin
            stop=threading.Event();pipe=Pipe()
            cutoff=types.SimpleNamespace(value=end)
            def worker_query(c,sql,args=None):
                if not sql.startswith('SET '):
                    fake.value=end;stop.set()
                return []
            cfg=dict(host='primary',readers=1,writers=1,rate=5,head=1,max_id=1,read_sql='SELECT',write_sql='UPDATE',clock=self.clock)
            with patch.object(lablib,'query',worker_query),patch.object(lablib,'threading',types.SimpleNamespace(Thread=Worker,Lock=threading.Lock)),patch.object(lablib,'random',types.SimpleNamespace(seed=lambda n:None,random=lambda:0,randint=lambda a,b:a)):
                lablib._workload_main(cfg,stop,pipe,cutoff)
            self.samples=pipe.payload['samples'];self.accounting=pipe.payload['accounting'];self.clock=pipe.payload['clock']
            self.cleanup={'confirmed':True};fake.value=saved

    class ReplaySampler(lablib.Sampler):
        def start(self): self.origin=fake.value
        def finish(self):
            saved=fake.value;fake.value=self.origin
            class Stop:
                def is_set(self): return fake.value>saved
                def wait(self,n): fake.sleep(n)
            self.stop=Stop()
            self._run()  # Actual server sample timestamps and cadence.
            self.thread=types.SimpleNamespace(is_alive=lambda:False)
            fake.value=saved

    def sample(c):
        wall_method.append(fake.time());fake.sleep(.25);fake.shift+=jump;wall_method.append(fake.time())
        return dict(status='precision_not_reached',accuracyStatus='precision_not_reached',budgetStatus='within_budget',cleanupStatus='rolled_back')

    with patch.object(clocklib,'time',fake),patch.object(reqwide,'time',fake),patch.object(lablib,'time',fake),patch.object(lablib,'connect',lambda *a,**kw:Connection()),patch.object(lablib,'pin',lambda *a:None),patch.object(lablib,'query',query),patch.object(reqwide,'query',query),patch.object(reqwide,'connect',lambda *a,**kw:Connection()),patch.object(reqwide,'close_owned',lambda *a:{'confirmed':True}),patch.object(reqwide,'state',lambda c:{'reads':fake.value}),patch.object(reqwide,'Workload',ReplayLoad),patch.object(reqwide,'Sampler',ReplaySampler):
        arm=reqwide.arm('primary',Connection(),'enc',False,'indexed',role,sampling_method=sample)
        assert not arm['errors'],arm['errors']
        assert not arm['unexpected_failures'],arm['unexpected_failures']
        checked=AUDIT['check_inline_arm_windows']([arm])
        assert not checked['problems'],checked
        # Actual selection rejects invalid/empty windows, and empty valid windows
        # never claim assessability. Workload.window is the production method.
        load=ReplayLoad();load.clock=arm['clock'];load.samples=arm['raw_service_samples']
        for a,b in [(1,1),(2,1),(float('nan'),2),(None,2)]:
            try: load.window(a,b)
            except ValueError: pass
            else: raise AssertionError('invalid live window accepted')
        empty=load.window(1,2)
        assert all(not v['slo_assessable'] for v in empty.values())
        assert not AUDIT['check_window'](load.samples,empty['read'],1,2,'read',arm['clock'])
        empty['read']['slo_assessable']=True
        assert AUDIT['check_window'](load.samples,empty['read'],1,2,'read',arm['clock'])
        # Method, child workload and server sampler retain a single domain.
        domain=arm['clock']['domain_id']
        assert {r[10] for r in arm['raw_service_samples']}=={domain}
        assert {r['clock_domain'] for r in arm['server_samples']}=={domain}
        # The same saved shape also exercises the public impact-window consumer.
        prefix='statement' if role=='count' else 'method'
        impact=dict(clock=arm['clock'],raw_service_samples=arm['raw_service_samples'],arrival_accounting=arm['arrival_accounting'],server_samples=arm['server_samples'],
            windows={new:arm[old]['window'] for new,old in [('baseline','baseline'),('during','trial'),('recovery','recovery')]},
            latency={new:arm[old]['service'] for new,old in [('baseline','baseline'),('during','trial'),('recovery','recovery')]},
            server={new:arm[old]['server'] for new,old in [('baseline','baseline'),('during','trial'),('recovery','recovery')]},
            actual_method_window=arm['actual_'+prefix+'_window'],actual_method_service=arm['actual_'+prefix+'_service'],actual_method_server=arm['actual_'+prefix+'_server'])
        assert not AUDIT['check_recorded_windows'](impact)
        optional=dict(clock=arm['clock'],t0=arm[prefix+'_started_at'],t1=arm[prefix+'_finished_at'],seconds=arm[prefix+'_seconds'],arm='statement',write=[arm['actual_'+prefix+'_service']['write']],read=arm['actual_'+prefix+'_service']['read'],server=arm['actual_'+prefix+'_server'])
        assert not AUDIT['check_arms']([optional],[arm['raw_service_samples']],arm['clock'],[arm['arrival_accounting']],arm['server_samples'])
        assert not AUDIT['check_stream_clock']({'clock':arm['clock'],'row_format':'arrival-tuple-v2'},arm['clock'],'arrival-tuple-v2')
        assert AUDIT['check_stream_clock']({},arm['clock'],'arrival-tuple-v2')
        # The selected impact runner and optional measured_run execute their
        # actual boundary code too, with the same live window/sampler functions.
        with patch.object(experiments,'time',fake),patch.object(experiments,'Workload',ReplayLoad),patch.object(experiments,'Sampler',ReplaySampler),patch.object(experiments,'connect',lambda *a,**kw:Connection()),patch.object(experiments,'pin',lambda *a:None),patch.object(experiments,'save',lambda *a:None),patch.dict(experiments.EXPERIMENTS,{'clock-test':dict(wl={'table':'enc'},wl_host='primary',window=None,prepare=None,cleanup=None,replica=False,data_locks=False,doc='DB-free regression',fn=lambda ctx:sample(None))}),redirect_stdout(io.StringIO()):
            impact=experiments.run('clock-test',3,base=5,recover=7)
            assert not AUDIT['check_recorded_windows'](impact)
        with patch.object(reqlib,'time',fake),patch.object(reqlib,'Workload',ReplayLoad),patch.object(reqlib,'Sampler',ReplaySampler),patch.object(reqlib,'pin',lambda *a:None):
            measured=reqlib.measured_run('clock-test',{'table':'enc'},lambda notes:sample(None),base=5,recover=7,during=3)
            assert not AUDIT['check_recorded_windows'](measured)
    return arm,wall_method


def corruptions(arm):
    prefix='statement' if arm['arm']=='count' else 'method'
    changes={
        'inverted_method':lambda a:a.update({prefix+'_finished_at':a[prefix+'_started_at']-1}),
        'missing_method':lambda a:a.pop(prefix+'_started_at'),
        'unsupported_clock':lambda a:a['clock'].update(clock='time.time'),
        'unsupported_system':lambda a:a['clock'].update(system={'foreign':'unknown'}),
        'missing_clock':lambda a:a.pop('clock'),
        'foreign_window':lambda a:a['trial']['window'].update(clock_domain='foreign'),
        'inverted_broad':lambda a:a['baseline']['window'].update(end=a['baseline']['window']['begin']-1),
        'empty_broad':lambda a:a['trial']['window'].update(end=a['trial']['window']['begin']),
        'foreign_actual_boundary':lambda a:a['actual_'+prefix+'_window'].update(clock_domain='foreign'),
        'missing_broad':lambda a:a['recovery'].pop('window'),
        'foreign_arrival':lambda a:a['raw_service_samples'][0].__setitem__(10,'foreign'),
        'foreign_server':lambda a:a['server_samples'][0].update(clock_domain='foreign'),
        'inverted_server':lambda a:a['server_samples'][0].update(finished_at=a['server_samples'][0]['t']-1),
        'shifted_arrival':lambda a:a['raw_service_samples'][0].__setitem__(0,a['raw_service_samples'][0][0]+1),
        'duplicate_arrival':lambda a:a['raw_service_samples'].append(a['raw_service_samples'][0]),
        'missing_server_end':lambda a:a['server_samples'][0].pop('finished_at'),
        'dropped_arrival':lambda a:a['raw_service_samples'].pop(),
        'dropped_trial':lambda a:a['trial_service_samples'].pop(),
        'missing_schedule':lambda a:a['arrival_accounting'].pop('schedules'),
        'legacy_tuple':lambda a:a['raw_service_samples'].__setitem__(0,a['raw_service_samples'][0][:6]),
    }
    for name,change in changes.items():
        a=json.loads(json.dumps(arm));change(a)
        assert AUDIT['check_inline_arm_windows']([a])['problems'],name
    # Even rewriting accounting and all aggregates cannot hide a dropped arrival:
    # the independent saved schedule still requires its worker/sequence.
    a=copy.deepcopy(arm);a['raw_service_samples'].pop()
    a['arrival_accounting']['scheduled']-=1;a['arrival_accounting']['unstarted']-=1
    assert 'dropped/additional scheduled arrivals' in AUDIT['check_arrivals'](a['raw_service_samples'],a['arrival_accounting'],a['clock'])
    return list(changes)+['dropped_arrival_with_rewritten_accounting']


def child_coordinate(pipe,clock):
    clocklib.adopt(clock)
    lablib.pin=lambda *a:None
    lablib.connect=lambda *a,**kw:Connection()
    lablib.query=lambda *a,**kw:time.sleep(.001) or []
    stop=threading.Event();cutoff=types.SimpleNamespace(value=0)
    def halt():
        cutoff.value=clocklib.now();stop.set()
    timer=threading.Timer(.06,halt);timer.start()
    try:
        cfg=dict(clock=clock,host='primary',readers=1,writers=1,rate=100,head=1,max_id=1,read_sql='SELECT',write_sql='UPDATE')
        lablib._workload_main(cfg,stop,pipe,cutoff)
    finally:
        timer.cancel();timer.join();pipe.close()


def check_process_coordinate():
    # Real spawned workload child plus parent method/sampler, no database.
    ctx=mp.get_context('spawn');parent,child=ctx.Pipe()
    clock=clocklib.metadata();begin=clocklib.now()
    def query(c,sql,args=None):
        if 'INNODB_METRICS' in sql:return [(0,)]
        if 'INNODB_TRX' in sql:return [(0,0)]
        if 'VARIABLE_NAME' in sql:return []
        return [(0,)]
    with patch.object(lablib,'connect',lambda *a,**kw:Connection()),patch.object(lablib,'query',query):
        sampler=lablib.Sampler('enc',data_locks=False);sampler.start()
        proc=ctx.Process(target=child_coordinate,args=(child,clock));proc.start();child.close()
        try:
            assert parent.poll(10),'child did not return clock'
            result=parent.recv()
        finally:
            proc.join(10)
            if proc.is_alive():proc.terminate();proc.join(5)
            sampler.finish()
    end=clocklib.now()
    assert proc.exitcode==0 and begin<=result['accounting']['origin']<result['accounting']['cutoff']<=end
    assert not AUDIT['same_clock'](result['clock'],clock)
    assert not AUDIT['check_arrivals'](result['samples'],result['accounting'],clock)
    assert result['samples'] and {r[10] for r in result['samples']}=={clock['domain_id']}
    assert sampler.rows and {r['clock_domain'] for r in sampler.rows}=={clock['domain_id']}
    assert all(begin<=r['t']<=r['finished_at']<=end for r in sampler.rows)
    report=sampler.window(begin,end)
    assert not AUDIT['check_server_window'](sampler.rows,report,begin,end,clock)
    foreign=copy.deepcopy(clock);foreign['system']={'foreign':'boot'}
    try:clocklib.adopt(foreign)
    except ValueError:pass
    else:raise AssertionError('foreign system adopted')
    return dict(exit_code=proc.exitcode,begin=begin,child_origin=result['accounting']['origin'],child_cutoff=result['accounting']['cutoff'],end=end,domain_id=clock['domain_id'],arrivals=len(result['samples']),server_samples=len(sampler.rows))


def check_inflight():
    # Suspend inside the actual worker query before the completion append. The
    # production collector must retain this request plus all undispatched ones.
    fake=FakeTime(-3600);stop=threading.Event();pipe=Pipe()
    class Suspend(BaseException):pass
    class SuspendedWorker(Worker):
        def start(self):
            try:self.target(*self.args)
            except Suspend:pass
        def is_alive(self):return True
    def query(c,sql,args=None):
        if not sql.startswith('SET '):
            fake.sleep(.055);fake.shift=-3600;stop.set();raise Suspend()
        return []
    with patch.object(clocklib,'time',fake),patch.object(lablib,'time',fake),patch.object(lablib,'pin',lambda *a:None),patch.object(lablib,'connect',lambda *a,**kw:Connection()),patch.object(lablib,'query',query),patch.object(lablib,'threading',types.SimpleNamespace(Thread=SuspendedWorker,Lock=threading.Lock)),patch.object(lablib,'random',types.SimpleNamespace(seed=lambda s:None,random=lambda:0,randint=lambda a,b:a)):
        lablib._workload_main(dict(host='primary',readers=1,writers=0,rate=100,head=1,max_id=1,read_sql='SELECT',write_sql='UPDATE'),stop,pipe)
        p=pipe.payload
        assert not AUDIT['check_arrivals'](p['samples'],p['accounting'],p['clock'])
        assert [r[3] for r in p['samples']].count('stuck')==1
        assert [r[3] for r in p['samples']].count('queued')==5
        w=lablib.Workload.__new__(lablib.Workload);w.clock=p['clock'];w.samples=p['samples']
        report=w.window(100,100.06)['read']
        assert not report['slo_assessable'] and report['latency_status']=='censored_lower_bounds'
        assert not AUDIT['check_window'](p['samples'],report,100,100.06,'read',p['clock'])
        report['slo_assessable']=True
        assert AUDIT['check_window'](p['samples'],report,100,100.06,'read',p['clock'])
        assert not p['connection_registry_complete']
    return dict(inflight=1,queued=5,censoring_enforced=True,unresolved_registry_not_cleanup_success=True)


def optional_callers():
    results=[]
    for module,call in ((reqmix,lambda:reqmix._arm(Connection(),'statement',index='PRIMARY')),
                        (reqscale,lambda:reqscale.run_arm('primary',Connection(),'statement_pk',{},1,1))):
        for jump in (-3600,3600):
            fake=FakeTime(jump)
            def query(c,sql,args=None):
                if sql.startswith('SELECT /*+'):
                    fake.sleep(.25);fake.shift+=jump;return [(1,)]
                if 'Max_execution_time_exceeded' in sql:return [('timer',0)]
                return []
            with patch.object(clocklib,'time',fake),patch.object(module,'time',fake),patch.object(module,'connect',lambda *a,**kw:Connection()),patch.object(module,'query',query),patch.object(module,'global_status',lambda c,*names:{n:0 for n in names}),patch.object(module,'session_setup',lambda *a:None),patch.object(module,'close_owned',lambda *a:{'confirmed':True}):
                rec=call()
                assert rec['t0']==100 and rec['t1']==100.25 and rec['seconds']==.25,rec
                assert rec['outcome']=='completed',rec
                assert not AUDIT['clock_problems'](rec['clock'])
                results.append(dict(caller=module.__name__,wall_jump=jump,begin=rec['t0'],end=rec['t1'],seconds=rec['seconds']))
    return results


if __name__=='__main__':
    observations=[]
    for role in ('count','sample'):
        reference=None
        for jump in (0,-3600,3600):
            arm,wall=run_case(jump,role)
            signatures={label:arm[label]['service'] for label in ('baseline','trial','recovery')}
            if reference is None:reference=signatures
            else:assert signatures==reference,'wall jump changed actual service selection'
            rejected=corruptions(arm)
            observations.append(dict(role=role,wall_jump_seconds=jump,wall_method_begin=wall[0],wall_method_end=wall[1],
                actual_begin=arm[('statement' if role=='count' else 'method')+'_started_at'],actual_end=arm[('statement' if role=='count' else 'method')+'_finished_at'],
                arrivals=len(arm['raw_service_samples']),states=dict(__import__('collections').Counter(r[3] for r in arm['raw_service_samples'])),server_samples=len(arm['server_samples']),public_audit_problems=0,rejected_mutations=rejected))
    assert time.time is STANDARD_TIME and time.monotonic is STANDARD_MONOTONIC
    print(json.dumps(dict(db_access=False,standard_time_unmodified=True,live_functions=['reqwide.arm','lablib._workload_main','Workload.window','Sampler._run','Sampler.window','experiments.run','reqlib.measured_run','reqmix._arm','reqscale.run_arm'],
        inflight=check_inflight(),optional_callers=optional_callers(),
        process_coordinate=check_process_coordinate(),observations=observations,
        public_auditor_sha256=hashlib.sha256(AUDITOR_SOURCE.encode()).hexdigest(),
        hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'bench/clocklib.py',ROOT/'bench/lablib.py',ROOT/'bench/reqwide.py',Path(__file__)]}),indent=2))
