"""Actual sampler, wide arm/callers and Workload.finish with fake SQL only.
No imports of lab DB clients, sockets, worker processes, real waits or Docker.
"""
import ast
import clocklib
import copy
import hashlib
import io
import json
import random
import sys
import types
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS
from protocol_outcomes import close_owned
from dependencies import local_dependencies

ROOT = Path(__file__).parent

def functions(filename, names, ns):
    tree = ast.parse((ROOT/filename).read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, 'exec'), ns)

class SqlError(Exception): pass

# Exercise real sampling logic including post-precision ROLLBACK failure.
clock = [0.0]
mode = 'ok'
def sampler_query(c, sql, args=None):
    if 'information_schema.STATISTICS' in sql: return (('id','bigint'),)
    if 'SELECT ENGINE' in sql: return (('InnoDB',),)
    if 'SELECT MIN' in sql: return ((1,10000),)
    if sql.startswith('SELECT COUNT'):
        if mode == '2013': raise SqlError(2013,'lost; every following query also fails')
        if mode in ('unknown1317','deadline'): raise SqlError(1317 if mode=='unknown1317' else 3024,'interrupted')
        if mode == 'budget': clock[0] = 2
        return ((200,),)
    if sql == 'ROLLBACK' and mode in ('2013','rollback'): raise SqlError(2013,'rollback failed')
    return ()
sample_ns = dict(Decimal=Decimal,random=random,time=NS(monotonic=lambda:clock[0]),
                 pymysql=NS(MySQLError=SqlError),query=sampler_query)
functions('samplinglib.py',['sample'],sample_ns)
real_samples = {}
for mode in ('ok','2013','rollback','budget','unknown1317','deadline','precision'):
    clock[0] = 0
    r = sample_ns['sample'](None,'bench.enc',42,looks=(2,) if mode=='precision' else (512,),max_seconds=1)
    real_samples[mode] = r
assert real_samples['2013']['errno'] == real_samples['2013']['cleanup_errno'] == 2013
assert real_samples['2013']['status'] == 'cleanup_failed'
assert real_samples['rollback']['accuracyStatus'] == 'target_reached'
assert real_samples['rollback']['status'] == 'cleanup_failed'
assert real_samples['budget']['status'] == 'budget_exhausted'
assert real_samples['precision']['status'] == 'precision_not_reached'

# Actual Workload.finish with child/registry/observer/KILL failures, including
# combined failure; accounting consistency never substitutes for cleanup.
tree=ast.parse((ROOT/'lablib.py').read_text())
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Workload')
fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='finish')
finish_ns={'clocklib':clocklib,'time':NS(monotonic=lambda:1)}
exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual_finish','exec'),finish_ns)
assert fn.args.defaults[0].value is True
class Conn:
    def __init__(self, role='method'): self.role=role
    def thread_id(self): return 17
    def close(self): pass
cleanup_mode='ok'
def finish_query(c,sql,args=None):
    if cleanup_mode in ('observer','combined'): raise SqlError(2013,'observer failed')
    if sql.startswith('KILL'): raise SqlError(1227,'KILL denied')
    return [(17,)] if cleanup_mode=='kill' else []
def admin_connect(*a,**kw):
    if cleanup_mode=='connect': raise OSError('observer connect failed')
    return Conn('observer')
finish_ns.update(connect=admin_connect,query=finish_query)
def workload_object():
    return NS(arrival_cutoff=NS(value=0),stop_flag=NS(set=lambda:None),
              parent=NS(poll=lambda n:True,recv=lambda:dict(samples=[(1,'write',9,'stuck',0,None)],
                  clock=clocklib.metadata(),accounting={'consistent':True,'scheduled':1,'started':1,'completed':0,'failed':0,'inflight':1,'unstarted':0},
                  connection_ids=[17],connection_registry_complete=cleanup_mode not in ('registry','combined'))),
              proc=NS(join=lambda n:None,is_alive=lambda:cleanup_mode=='alive',terminate=lambda:None,
                      exitcode=7 if cleanup_mode in ('child','combined') else 0),cfg={'host':'primary','clock':clocklib.metadata()},samples=[],accounting={})
cleanup_records={}
for cleanup_mode in ('ok','registry','alive','child','observer','connect','kill','combined'):
    w=workload_object()
    try:
        finish_ns['finish'](w)  # Deliberately exercises the new safe DEFAULT.
        assert cleanup_mode=='ok'
    except RuntimeError:
        assert cleanup_mode!='ok'
    assert w.cleanup['confirmed'] == (cleanup_mode=='ok')
    assert w.accounting['consistent'] and w.accounting['inflight']==1
    assert w.samples[0][3]=='stuck'
    cleanup_records[cleanup_mode]=copy.deepcopy(w.cleanup)
assert cleanup_records['combined']['child_exitcode']==7
assert 'observer' in cleanup_records['combined']['error']

# Actual arm: count timer evidence, method cleanup, workload diagnostics, and
# both original sample + concurrent cleanup failure must survive to caller save.
scenario='ok'; timer=0; phase='static'; calls=[];count_failure=True
class Load:
    def __init__(self,*a,**kw): self.obj=workload_object();self.samples=[];self.accounting={}
    def start(self): pass
    def finish(self,confirm_server=False):
        assert confirm_server
        try: finish_ns['finish'](self.obj,confirm_server=confirm_server)
        finally:
            self.cleanup=self.obj.cleanup;self.samples=self.obj.samples;self.accounting=self.obj.accounting
    def window(self,*a): return {'write':{'scheduled':1,'inflight_at_end':1,'latency_status':'censored_lower_bounds'}}
class Observer:
    def __init__(self,*a,**kw): self.rows=[{'t':1,'hll':0}];self.thread=NS(is_alive=lambda:False)
    def start(self): pass
    def finish(self): pass
    def window(self,*a): return {'samples':1,'peak_row_locks_seen':None}
def fake_query(c,sql,args=None):
    global timer
    if 'Max_execution_time_exceeded' in sql: return [('Max_execution_time_exceeded',timer)]
    if 'buffer_pool_size' in sql: return [(1,)]
    if sql.startswith('SELECT /*+'):
        calls.append('count')
        if count_failure and scenario in ('2013','unknown1317','deadline'):
            if scenario=='deadline': timer+=1
            raise SqlError(2013 if scenario=='2013' else 1317,'method failure')
        return [(750000,)]
    if sql.startswith('SELECT COUNT'): return [(750000,)]
    if 'SELECT PROCESSLIST_ID' in sql or 'SELECT trx_mysql' in sql: return []
    if sql.startswith('EXPLAIN'): return [('index',)]
    return []
def fake_sample(q,*a,**kw):
    calls.append('sample')
    if phase=='static': return copy.deepcopy(real_samples['ok'])
    return copy.deepcopy(real_samples[scenario] if scenario in real_samples else {
        'status':scenario,'cleanupStatus':'rolled_back','accuracyStatus':'precision_not_reached','budgetStatus':'within_budget'})
ns=dict(clocklib=clocklib,time=NS(time=lambda:1,monotonic=lambda:1,sleep=lambda n:None),pymysql=NS(MySQLError=SqlError),
        Workload=Load,Sampler=Observer,connect=lambda *a,**kw:Conn(),query=fake_query,
        close_owned=close_owned,state=lambda c:{'reads':1},change=lambda a,b:{'reads':0},
        ROWS=750000,HOT_ROWS=50000,LIMIT_MS=1000,BASE_SECONDS=5,TRIAL_SECONDS=3,RECOVERY_SECONDS=7,
        pin=lambda *a:None,METHOD_CPU=3,build=lambda *a:None,hashlib=hashlib,
        open=lambda p,*a,**kw:open(ROOT/Path(p).name,*a,**kw))
functions('reqwide.py',['sample_expected','outcome_failures','persist_outcome','arm','run'],ns)
cleanup_mode='ok'
arm_records={}
for name,scenario in [('count','ok'),('count','2013'),('count','unknown1317'),('count','deadline'),
                      ('sample','ok'),('sample','2013'),('sample','rollback'),('sample','precision'),('sample','budget'),
                      ('sample','unknown1317'),('sample','deadline')]:
    phase='service';timer=0
    r=ns['arm']('primary',Conn('observer'),'enc',False,'indexed',name,sampling_method=fake_sample)
    expected=scenario in ('ok','deadline','precision','budget')
    assert (not r['unexpected_failures'])==expected,(name,scenario,r)
    assert r['workload_cleanup']['confirmed'] and r['method_cleanup']['confirmed']
    if name=='count' and scenario=='unknown1317': assert r['errno']==1317 and r['timer_counter_delta']==0
    if name=='sample' and scenario=='unknown1317': assert r['sampling']['errno']==1317
    if name=='sample' and scenario=='2013': assert r['sampling']['errno']==2013
    arm_records[name+'/'+scenario]=r
# Precision reached then workload failure, and combined sample/child/observer failure.
for cleanup_mode,scenario in [('registry','ok'),('combined','2013')]:
    r=ns['arm']('primary',Conn(),'enc',False,'indexed','sample',sampling_method=fake_sample)
    assert r['unexpected_failures'] and not r['workload_cleanup']['confirmed']
    assert r['arrival_accounting']['consistent'] and r['raw_service_samples'][0][3]=='stuck'
    assert r['server_samples'] and 'baseline' in r['windows_completed']
    arm_records[cleanup_mode]=r

# Actual three caller functions must persist a failing arm before raising and
# never enter another comparison. 01a/01b also exercise all static samples first.
functions('reqsamplewide.py',['run'],sample_caller := dict(ns,sample=fake_sample))
frontier_ns=dict(ns,source_hashes=lambda:{'fake':'only'},PROTOCOLS=((.03,(512,)),(.05,(320,)),(.10,(160,))))
functions('accuracy_frontier.py',['wide_service_frontier'],frontier_ns)
arm_calls=[]
original_arm=ns['arm']
def service_arm(*a,**kw):
    global phase
    phase='service';arm_calls.append(True)
    return original_arm(*a,**kw)
module=types.ModuleType('reqwide')
module.build=ns['build'];module.ROWS=750000;module.arm=service_arm;module.persist_outcome=ns['persist_outcome']
sys.modules['reqwide']=module
caller_records={}
for caller,columns in [('sample50',50),('sample100',100),('wide50',50),('wide100',100),('frontier100',100)]:
    for scenario,cleanup_mode in [('2013','ok'),('ok','registry'),('2013','combined')]:
        count_failure=caller.startswith('wide')
        phase='static';timer=0;arm_calls.clear();snapshots=[]
        space=sample_caller if caller.startswith('sample') else ns if caller.startswith('wide') else frontier_ns
        space['arm']=service_arm
        space['save']=lambda _,out:snapshots.append(copy.deepcopy(out))
        space['sample_with_budgets']=fake_sample
        try:
            with redirect_stdout(io.StringIO()):
                if caller=='frontier100': space['wide_service_frontier']('fake','primary','8.4.13')
                else: space['run']('fake','primary','8.4.13',columns)
        except RuntimeError: pass
        else: raise AssertionError('failure became normal return: '+caller)
        saved=snapshots[-1];collection='service' if caller.startswith('sample') else 'arms'
        assert len(saved[collection])==len(arm_calls) and not saved['complete'] and not saved['reusable_mandatory_completion']
        assert all(not r['unexpected_failures'] for r in saved[collection][:-1])
        item=saved[collection][-1]
        assert item['raw_service_samples'] and item['arrival_accounting']['consistent']
        assert item['unexpected_failures']
        if caller.startswith('sample') and scenario=='2013' and cleanup_mode=='ok': assert saved['unexpected_sampling_failures']
        caller_records[caller+'/'+scenario+'/'+cleanup_mode]=saved

# Static failures must likewise save before stopping; no service arm can start.
cleanup_mode='ok';phase='static';arm_calls.clear();snapshots=[]
original_sample=sample_caller['sample']
sample_caller['sample']=lambda *a,**kw:copy.deepcopy(real_samples['2013'])
try:
    with redirect_stdout(io.StringIO()): sample_caller['run']('fake','primary','8.4.13',100)
except RuntimeError: pass
else: raise AssertionError('static failure returned normally')
assert not arm_calls and snapshots[-1]['static'][-1]['result']['errno']==2013
assert snapshots[-1]['unexpected_sampling_failures'] and not snapshots[-1]['complete']
sample_caller['sample']=original_sample

successful_callers=[]
for caller,columns in [('sample50',50),('sample100',100),('wide50',50),('wide100',100),('frontier100',100)]:
    for scenario in ('ok','precision','budget','deadline'):
        cleanup_mode='ok';phase='static';timer=0;count_failure=True
        arm_calls.clear();snapshots=[]
        space=sample_caller if caller.startswith('sample') else ns if caller.startswith('wide') else frontier_ns
        space['save']=lambda _,out:snapshots.append(copy.deepcopy(out))
        with redirect_stdout(io.StringIO()):
            if caller=='frontier100': space['wide_service_frontier']('fake','primary','8.4.13')
            else: space['run']('fake','primary','8.4.13',columns)
        result=snapshots[-1]
        assert result['complete'] and result['reusable_mandatory_completion']
        expected_arms=36 if caller.startswith('sample') else 48
        assert len(arm_calls)==expected_arms
        if caller.startswith('sample'): assert len(result['static'])==20
        successful_callers.append((caller,scenario,expected_arms))

# Every mandatory wrapper's recursive cache closure sees changed shared code.
wrappers=('01a-PROB-WIDE-50','01b-PROB-WIDE-100','96-encrypted-wide-50','97-encrypted-wide-100','01d-ACCURACY-SERVICE-100')
for script in wrappers:
    closure=local_dependencies(ROOT/'requested'/f'{script}.py',ROOT)
    assert all('bench/'+p in closure for p in ('reqwide.py','lablib.py','protocol_outcomes.py'))
print(json.dumps(dict(db_access=False,actual_functions=True,sampler_cases=len(real_samples),
    workload_cleanup_cases=len(cleanup_records),arm_cases=len(arm_records),caller_failure_cases=len(caller_records),
    static_failure_saved=True,no_next_arm=True,nonzero_exception_reaches_wrappers=True,
    recursive_cache_closure=wrappers,expected_observations_preserved=True,
    combined_failures_preserved=True,successful_callers=successful_callers)))
