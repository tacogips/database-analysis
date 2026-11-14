#!/usr/bin/env python3
"""DB-free dispatch barrier and default-plan regression using actual functions.
No DB client is imported, no sockets are opened and no benchmark body is run.
"""
import argparse
import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from nonlocking_policy import ExcludedLockProbe, assert_nonlocking_sql
from dependencies import local_dependencies

ROOT = Path(__file__).resolve().parent
scope = json.loads((ROOT/'nonlocking-scope.json').read_text())

def function(path, names, namespace):
    tree=ast.parse((ROOT/path).read_text())
    nodes=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes)==len(names)
    exec(compile(ast.Module(body=nodes,type_ignores=[]), str(path), 'exec'),namespace)

# The real query helper must reject before obtaining a cursor, even when the
# caller assembles SQL at runtime or a prepared statement contains the count.
class Fake:
    def __init__(self): self.sent=[]; self.description=True
    def cursor(self): return self
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def execute(self,sql,args): self.sent.append((sql,args))
    def fetchall(self): return [(42,)]
ns={'assert_nonlocking_sql':assert_nonlocking_sql}
function('lablib.py', ['query'], ns)
c=Fake()
blocked=[
 'SELECT COUNT(*) FROM t FOR SHARE', 'SELECT COUNT(*) FROM t FOR UPDATE',
 'SELECT COUNT(*) FROM t LOCK IN SHARE MODE', 'LOCK TABLES t READ',
 'FLUSH TABLES', 'FLUSH TABLES WITH READ LOCK', 'FLUSH NO_WRITE_TO_BINLOG TABLES t',
 "SET SESSION transaction_isolation='SERIALIZABLE'", 'SET TRANSACTION ISOLATION LEVEL SERIALIZABLE',
 'INSERT INTO s SELECT COUNT(*) FROM t', 'REPLACE INTO s VALUES ((SELECT COUNT(*) FROM t))',
 'UPDATE s SET c=(SELECT COUNT(*) FROM t)', 'SET @c=(SELECT COUNT(*) FROM t)',
 'CREATE TABLE s AS SELECT COUNT(*) FROM t', 'CREATE TEMPORARY TABLE s AS SELECT COUNT(*) FROM t',
 "PREPARE st FROM 'SET @c=(SELECT COUNT(*) FROM t)'", 'PREPARE st FROM @unknown',
 'SELECT COUNT(*) FROM t FOR/**/UPDATE', 'SELECT COUNT(*) FROM t /*! FOR UPDATE */',
 'WITH x AS (SELECT COUNT(*) AS c FROM t) INSERT INTO s SELECT c FROM x',
]
for sql in blocked:
    before=len(c.sent)
    try: ns['query'](c,sql)
    except ExcludedLockProbe: pass
    else: raise AssertionError('dispatched excluded SQL: '+sql)
    assert len(c.sent)==before
allowed=[
 'SELECT /*+ MAX_EXECUTION_TIME(1000) */ COUNT(*) FROM t',
 'SELECT COUNT(*) FROM t WHERE id >= %s AND id < %s',
 "SELECT COUNT(*) FROM performance_schema.metadata_locks WHERE LOCK_STATUS='PENDING'",
 "SELECT NAME FROM information_schema.INNODB_TABLES WHERE NAME LIKE 'db/t#p#%'",
 "SELECT SQL_TEXT FROM performance_schema.events_statements_history WHERE SQL_TEXT LIKE '%FOR UPDATE%'",
 'UPDATE t SET v=v+1 WHERE id=%s', 'INSERT INTO t VALUES (%s,0)', 'DELETE FROM t WHERE id=%s',
 'CREATE TABLE t (id INT PRIMARY KEY)', 'INSERT INTO t SELECT n FROM seq', 'ANALYZE TABLE t',
 'DROP TABLE t', "SET SESSION transaction_isolation='READ-COMMITTED'",
 "PREPARE st FROM 'SELECT COUNT(*) FROM t'",
 "CREATE PROCEDURE p() SELECT COUNT(*) FROM t", 'SELECT c FROM (SELECT COUNT(*) AS c FROM t) x',
]
for sql in allowed: assert ns['query'](c,sql)==[(42,)]
assert len(c.sent)==len(allowed)

# Inspect real DB-sink literals/templates across current default source. Dynamic
# SQL is checked by the query barrier at dispatch; parser-only fixtures are not
# DB sinks. This caught a nested COUNT in the counter initialization path.
static_sinks=0
for path in ROOT.rglob('*.py'):
    if path.name.startswith('test_'): continue
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Name): continue
        index={'query':1,'sql_try':1,'literal':2,'attempt':1,'attempt_sql':1}.get(node.func.id)
        if index is None or len(node.args)<=index: continue
        value=node.args[index]
        sql=None
        if isinstance(value,ast.Constant) and isinstance(value.value,str):sql=value.value
        elif isinstance(value,ast.JoinedStr):sql=''.join(x.value if isinstance(x,ast.Constant) else '__identifier__' for x in value.values)
        if sql:
            assert_nonlocking_sql(sql)
            static_sinks+=1

# Register actual experiment decorators and dynamic plain-count arms, then run
# the actual all dispatcher with a recorder in place of the benchmark body.
ns={'EXPERIMENTS':{},'DENSE':{},'HOT':{},'REPL':{},'COUNTED':{},'PRIMARY':'primary',
    'argparse':argparse,'sys':sys,'make_counted':lambda *args: None,'drop_counted':lambda *args: None}
function('experiments.py',['exp'],ns)
tree=ast.parse((ROOT/'experiments.py').read_text())
nodes=[]
for n in tree.body:
    if isinstance(n,ast.FunctionDef) and n.decorator_list: nodes.append(n)
    elif isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'RR','RC','LOCK_TESTS'} for t in n.targets): nodes.append(n)
    elif isinstance(n,ast.FunctionDef) and n.name=='add_lock_tests': nodes.append(n)
    elif isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and (
         isinstance(n.value.func,ast.Name) and n.value.func.id=='add_lock_tests' or
         isinstance(n.value.func,ast.Call) and isinstance(n.value.func.func,ast.Name) and n.value.func.func.id=='exp'):nodes.append(n)
exec(compile(ast.Module(body=nodes,type_ignores=[]),'actual_experiment_registration','exec'),ns)
assert sorted(ns['EXPERIMENTS'])==scope['baseImpactNames']
sent=[]
ns['run']=lambda name,*args: (sent.append(name) or {'errors':[],'worker_terminated':True})
function('experiments.py',['main'],ns)
old=sys.argv
try:
    sys.argv=['experiments.py','all']
    assert ns['main']()==0
finally:sys.argv=old
assert sorted(sent)==scope['baseImpactNames']
# The exact requested glob, per-version invocations and preserved plans must match.
paths=sorted((ROOT/'requested').glob('*.py'))
assert [p.stem for p in paths]==[x['id'] for x in scope['requestedScripts']]
for p,expected in zip(paths,scope['requestedScripts']):
    for suffix,key in [('.versions','versions'),('.outputs','requiredOutputs')]:
        q=p.with_suffix(suffix);actual=q.read_text().split() if q.exists() else ([None] if suffix=='.versions' else [])
        assert actual==expected[key],(p,key)
    closure=local_dependencies(p,ROOT)
    # Every requested DB caller sees the policy in its cache fingerprint.
    if 'bench/lablib.py' in closure:assert 'bench/nonlocking_policy.py' in closure
for e in scope['exclusions']:
    p=ROOT.parent/e['path']
    if e['path'].endswith('.py.retired'):
        assert p.exists() and p.with_suffix('').stem not in [x.stem for x in paths]
        assert isinstance(ast.parse(p.read_text()).body[0], ast.Raise), 'legacy entrypoint must stop before imports/DB'
plan=json.loads((ROOT/'requested/write-mix-plan.json').read_text())
assert len(plan['requiredScripts'])==41
assert all((ROOT/'requested'/(x['id']+'.py')).exists() for x in plan['requiredScripts'])
plan=json.loads((ROOT/'requested/primary-scale-plan.json').read_text())
assert len(plan['requiredScripts'])==30
assert all((ROOT/'requested'/(x+'.py')).exists() for x in plan['requiredScripts'])
print(json.dumps({'blocked_before_cursor':len(blocked),'allowed_dispatch':len(allowed),
 'static_sink_templates_checked':static_sinks,'baseImpactCount':len(sent),'requestedScripts':len(paths),'requestedProcesses':sum(len(x['versions']) for x in scope['requestedScripts']),
 'writeMixScripts':41,'primaryScaleScripts':30,'db_operations':0,'scope_catalog_matches':True}))
