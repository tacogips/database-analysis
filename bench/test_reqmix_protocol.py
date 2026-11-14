"""Actual classifiers/scheduler/body with fake clocks and DBs. No client import."""
import ast
import clocklib
from contextlib import contextmanager, redirect_stdout
import io
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from protocol_outcomes import (classify_outcome, close_owned, confirm_connections,
                               opportunity_schedule, scope_complete)

ROOT = Path(__file__).parent

def load(names, namespace):
    tree = ast.parse((ROOT/'reqmix.py').read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'actual_reqmix', 'exec'), namespace)

clean = {'confirmed': True}
for status, extra, expected in [
    ('complete', {}, 'completed'), ('stopped:max_seconds', {}, 'confirmed_ceiling'),
    ('stopped:max_rows', {}, 'confirmed_ceiling'),
    ('error_3024', {'timer_counter_before':0,'timer_counter_after':1}, 'confirmed_ceiling'),
    ('error_1317', {'timer_counter_before':2,'timer_counter_after':3}, 'confirmed_ceiling'),
    ('error_1317', {}, 'unknown_interrupt'), ('error_3024', {}, 'unknown_interrupt'),
    ('error_2013', {}, 'unexpected_failure'), ('stopped:connection_lost', {}, 'unexpected_failure'),
    ('rejected:plan', {}, 'unexpected_failure'),
    ('complete', {'cleanup':{'confirmed':False}}, 'cleanup_failed'),
    ('not_started', {}, 'not_started')]:
    record = dict(status=status, cleanup=clean, **{k:v for k,v in extra.items() if k != 'cleanup'})
    if 'cleanup' in extra: record['cleanup']=extra['cleanup']
    assert classify_outcome(record) == expected, record

# Exercise actual owned-ID cleanup: delayed session and transaction disappearance,
# observation failure, failed KILL, timeout, and rollback failure followed by proof.
class Connection:
    def thread_id(self): return 17
    def close(self): self.closed=True
for mode in ('success','observe_failure','kill_failure','timeout','rollback_failure'):
    kills=[]; polls=[0]
    def fake_query(conn, sql, args=None):
        if sql=='ROLLBACK':
            if mode=='rollback_failure': raise OSError('lost connection')
            return []
        if sql.startswith('KILL'):
            kills.append(sql)
            if mode=='kill_failure': raise OSError('denied')
            return []
        if mode=='observe_failure': raise OSError('monitor unavailable')
        if sql.startswith('SELECT PROCESSLIST_ID'): polls[0]+=1
        return [(17,)] if mode=='timeout' or polls[0]==1 else []
    c=Connection()
    out=close_owned(object(),c,17,fake_query) if mode=='rollback_failure' else confirm_connections(object(),[17],fake_query,timeout=0 if mode=='timeout' else 1)
    assert out['confirmed'] == (mode in ('success','rollback_failure')), (mode,out)
    assert all(k=='KILL CONNECTION 17' for k in kills)
    if mode=='rollback_failure': assert out['rollback']=='failed' and c.closed

# Static reviewer counterexample: actual schedule, all durations near caps too.
for durations in ({'split_k':149,'split_pk':150,'statement_k':120,'statement_pk':1},
                  {'split_k':150,'split_pk':150,'statement_k':120,'statement_pk':120}):
    seen=[]
    for group in ('splits','statements'):
        for block in (0,1):
            schedule=opportunity_schedule(group,block)
            reserve=sum(150 if r['arm'].startswith('split') else 120 for r in schedule)+90
            assert reserve+400 < 1140  # synthetic400s setup; not a measured bound
            assert sum(durations[r['arm']] for r in schedule) <= reserve-90
            seen.extend((r['arm'],r['rep']) for r in schedule)
    assert len(set(seen))==16
    assert not scope_complete([dict(outcome='completed'),dict(outcome='budget_insufficient')])

# Actual Workload.finish must fail closed on an incomplete ID registry, an
# unjoined process, or missing payload, even when all known DB IDs disappeared.
finish_tree=ast.parse((ROOT/'lablib.py').read_text())
finish_cls=next(n for n in finish_tree.body if isinstance(n,ast.ClassDef) and n.name=='Workload')
finish_fn=next(n for n in finish_cls.body if isinstance(n,ast.FunctionDef) and n.name=='finish')
finish_ns={'clocklib':clocklib,'connect':lambda *a,**kw:Connection(), 'query':lambda *a:[]}
exec(compile(ast.Module(body=[finish_fn],type_ignores=[]),'actual_Workload_finish','exec'),finish_ns)
for registry,alive,received in ((True,False,True),(False,False,True),(True,True,True),(True,False,False)):
    obj=SimpleNamespace(arrival_cutoff=SimpleNamespace(value=0),stop_flag=SimpleNamespace(set=lambda:None),
        parent=SimpleNamespace(poll=lambda n:received,recv=lambda:dict(clock=clocklib.metadata(),samples=[],accounting={'consistent':True},connection_ids=[17],connection_registry_complete=registry)),
        proc=SimpleNamespace(join=lambda n:None,is_alive=lambda:alive,terminate=lambda:None,exitcode=0),
        cfg={'host':'primary','clock':clocklib.metadata()},accounting={})
    # The function's wall clock is supplied rather than importing lablib.
    finish_ns['time']=SimpleNamespace(monotonic=lambda:1)
    try:
        finish_ns['finish'](obj,confirm_server=True)
        assert registry and not alive and received
    except RuntimeError:
        assert not registry or alive or not received
        assert obj.cleanup['confirmed'] is False

# Every actual wrapper matches its immutable plan, outputs and declared scope.
plan=json.loads((ROOT/'requested/write-mix-plan.json').read_text())
counts={}
for item in plan['requiredScripts']:
    path=ROOT/'requested'/f"{item['id']}.py"
    call=next(n for n in ast.walk(ast.parse(path.read_text())) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='write_mix2_body')
    kw={k.arg:ast.literal_eval(k.value) for k in call.keywords}
    assert [ast.literal_eval(n) for n in call.args[1:]] == [item['mix'],item['rate']]
    assert kw['group']==item['group']
    assert path.with_suffix('.outputs').read_text().strip()==f"results/requested_{item['id']}.json"
    if item['group'] in ('splits','statements'):
        assert kw['block']==item['block']
        for row in opportunity_schedule(kw['group'],kw['block']):
            key=(item['mix'],item['rate'],item['split_only'],row['arm'])
            counts.setdefault(key,[]).append(row['rep'])
assert all(sorted(v)==[0,1,2,3] for v in counts.values())
assert sorted(i['tail_repeat'] for i in plan['requiredScripts'] if i['group']=='tail')==list(range(5))

# Execute actual body including Results.section exception swallowing and finally.
class Result:
    instances=[]
    def __init__(self,script_id,answers,**meta):
        self.data=dict(sections={},**meta);self.errors=[];self.instances.append(self)
    def save(self): pass
    def time_left(self): return remaining[0]
    @contextmanager
    def section(self,name):
        d=self.data['sections'].setdefault(name,{})
        try: yield d
        except Exception as e: self.errors.append(name+':'+repr(e))
    def finish(self): self.finished=True
class Load:
    instances=[]
    def __init__(self,*a,**kw): self.clock=clocklib.metadata();self.samples=[];self.accounting={'consistent':True};self.instances.append(self)
    def start(self): pass
    def finish(self,confirm_server=False):
        assert confirm_server
        self.stopped=True;self.cleanup={'confirmed':not (mode=='workload_stop_failure' and self is self.instances[0])}
        if not self.cleanup['confirmed']: raise RuntimeError('worker unresolved')
    def window(self,*args): return dict(write={},read={})
class Sample:
    def __init__(self,*a,**kw): self.clock=clocklib.metadata();self.rows=[]
    def start(self): pass
    def finish(self): pass
    def window(self,*a): return {}
remaining=[1140];calls=[];dropped=[]; clock=[0]
ns=dict(clocklib=clocklib,Results=Result,MIXES={'test':{'loads':[('UPDATE',1,1),('DELETE',1,1)],'k':'n+{off}','note':'fake'}},
    ROWS=1_000_000,SPLIT_SECONDS=150,STATEMENT_LIMIT_MS=120000,GS=('reads','disk'),
    threading=SimpleNamespace(Event=lambda:SimpleNamespace(set=lambda:None)),
    time=SimpleNamespace(time=lambda:clock[0],sleep=lambda n:clock.__setitem__(0,clock[0]+n)),
    builder=lambda:Connection(),reset_schema=lambda *a:None,connect=lambda **kw:Connection(),
    query=lambda c,s,*a: dropped.append(s) or [] if s.startswith('DROP') else [],
    Workload=Load,Sampler=Sample,pin=lambda *a:None,METHOD_CPU=3,session_setup=lambda *a:None,
    forced_plan=lambda *a:{'index':a[-1]},opportunity_schedule=opportunity_schedule,
    close_owned=lambda *a:{"confirmed":True},
    scope_complete=scope_complete,write_samples=lambda *a,**kw:{'fake':True},_window_of=lambda *a:None)
load(['write_mix2_body'],ns)
for mode in ('normal','error2013','cleanup_failure','section_exception','budget_absence','workload_stop_failure'):
    remaining[0]=1 if mode=='budget_absence' else 1140
    calls.clear();dropped.clear();Load.instances.clear()
    def arm(*a,**kw):
        calls.append(True)
        if mode=='section_exception': raise RuntimeError('injected section exception')
        return dict(status='error_2013' if mode=='error2013' else 'complete',
                    outcome='unexpected_failure' if mode=='error2013' else 'cleanup_failed' if mode=='cleanup_failure' else 'completed',
                    cleanup={'confirmed':mode!='cleanup_failure'},t0=0,t1=1,seconds=1)
    ns['_arm']=arm
    with redirect_stdout(io.StringIO()):
        ns['write_mix2_body']('fake','test',100)
    r=Result.instances[-1]
    assert r.finished
    assert r.data['scopeComplete']==(mode=='normal'), (mode,r.data,r.errors)
    assert all(getattr(w,'stopped',False) for w in Load.instances)
    if mode in ('normal','workload_stop_failure'): assert len(calls)==4
    elif mode=='budget_absence': assert not calls and all(x['outcome']=='budget_insufficient' for x in r.data['opportunities'])
    else:
        assert len(calls)==1
        assert all(x['outcome']=='not_started' and 'reason' in x for x in r.data['opportunities'][1:])
        assert r.errors
    if mode=='cleanup_failure': assert not dropped
print(json.dumps(dict(db_access=False,actual_function_tests=True,classification_cases=12,
    static_149_150_120_1_counterexample=True,all_caps=True,all_wrappers_match_plan=True,
    declared_scripts=len(plan['requiredScripts']),body_scenarios=6,owned_cleanup_scenarios=5,workload_registry_scenarios=4)))

