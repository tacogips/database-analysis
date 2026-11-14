#!/usr/bin/env python3
"""Deadline and cleanup regressions, using the actual sampler with a deterministic fake SQL endpoint."""
import sys, types, json
try:
    import pymysql
except ImportError:
    client=types.ModuleType('pymysql');cursors=types.ModuleType('pymysql.cursors')
    cursors.Cursor=cursors.DictCursor=object
    class MySQLError(Exception): pass
    client.MySQLError=MySQLError;client.cursors=cursors
    sys.modules['pymysql']=client;sys.modules['pymysql.cursors']=cursors
import samplinglib
from samplinglib import sample

def run(overrun=False,cleanup_error=False):
    now=[0.0];calls=[];draws=[0]
    samplinglib.time=types.SimpleNamespace(monotonic=lambda:now[0])
    def query(c,sql,args=None):
        calls.append(sql)
        if sql.startswith('SELECT MIN'):return ((1,10000),)
        if 'information_schema.STATISTICS' in sql:return (('id','bigint'),)
        if sql.startswith('SELECT ENGINE'):return (('InnoDB',),)
        if sql.startswith('SELECT COUNT'):
            draws[0]+=1
            if overrun and draws[0]==512:now[0]=1.1
            return ((200,),)
        if sql=='ROLLBACK' and cleanup_error:raise samplinglib.pymysql.MySQLError(2013,'lost during rollback')
        return ()
    samplinglib.query=query
    r=sample(None,'bench.fixture',42,looks=(512,),max_seconds=1)
    checks=[i for i,s in enumerate(calls) if 'information_schema.STATISTICS' in s]
    target=calls.index('SELECT MIN(id),MAX(id) FROM bench.fixture')
    assert checks[0] < target < checks[-1]
    return r
normal=run();late=run(overrun=True);cleanup=run(cleanup_error=True)
assert normal['status']=='target_reached' and normal['cleanupStatus']=='rolled_back'
assert late['status']=='budget_exhausted' and late['budgetStatus']=='exhausted' and late['last_valid_checkpoint_draws']==0
assert late['attempted_draws']==late['completed_draws']==512
assert cleanup['status']=='cleanup_failed' and cleanup['cleanupStatus']=='failed'
print(json.dumps({'normal':normal,'late_final_query':late,'rollback_failure':cleanup},indent=2))
