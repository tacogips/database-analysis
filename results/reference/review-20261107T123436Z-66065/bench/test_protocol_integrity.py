"""Actual functions with synthetic observations; never imports DB clients."""
import argparse
import ast
from contextlib import redirect_stderr
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from protocol_outcomes import classify_outcome

ROOT = Path(__file__).parent

def functions(filename, names, namespace):
    tree = ast.parse((ROOT / filename).read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, 'exec'), namespace)

ns = {'classify_outcome': classify_outcome}
functions('lablib.py', ['validate_lab_host'], ns)
assert ns['validate_lab_host']('primary') == 'primary'
assert ns['validate_lab_host']('lab-matrix-8-4-13') == 'lab-matrix-8-4-13'
try:
    ns['validate_lab_host']('production.example')
except ValueError:
    pass
else:
    raise AssertionError('external DB host accepted')
functions('reqscale.py', ['arm_order', 'arm_expected'], ns)
for mix in ('indexed', 'uniform_insert_delete'):
    arms = [ns['arm_order'](mix, group, repeat) for group in ('core', 'secondary') for repeat in range(3)]
    flat = [name for group in arms for name in group]
    expected = ['statement_k', 'statement_pk', 'split_pk'] + ([] if mix == 'indexed' else ['split_k'])
    assert all(flat.count(name) == 3 for name in expected)
    assert ns['arm_order'](mix, 'core', 0) != ns['arm_order'](mix, 'core', 1)
    for name in expected:
        assert ns['arm_expected'](dict(status='complete', cleanup={'confirmed':True}))
    assert ns['arm_expected'](dict(status='error_3024', cleanup={'confirmed':True}, timer_counter_before=0, timer_counter_after=1))
    assert not ns['arm_expected'](dict(status='error_1317', cleanup={'confirmed':True}))
    assert not ns['arm_expected'](dict(status='complete', cleanup={'confirmed':False}))
# Every actual wrapper declares one of each group/repetition for every config.
for prefix in ('0g', '0h', '0i', '0j', '0k'):
    calls = []
    for path in (ROOT/'requested').glob(prefix+'-*.py'):
        tree = ast.parse(path.read_text())
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'scale_body')
        kw = {x.arg: ast.literal_eval(x.value) for x in call.keywords}
        calls.append((kw['repeat'], kw['group']))
        assert path.with_suffix('.versions').read_text().strip() == '8.4.13'
        assert path.with_suffix('.outputs').read_text().strip() == f'results/requested_{path.stem}.json'
    assert sorted(calls) == [(r, g) for r in range(3) for g in ('core', 'secondary')]
# Exercise the actual aggregate with a disabled observation and with true zero.
tree = ast.parse((ROOT/'lablib.py').read_text())
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Sampler')
window = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'window')
ns = {}
exec(compile(ast.Module(body=[window], type_ignores=[]), 'Sampler.window', 'exec'), ns)
obj = SimpleNamespace(data_locks=False, replica_host=None, rows=[{'t':1,'hll':0}, {'t':2,'hll':0}])
r = ns['window'](obj, 0, 3)
assert r['peak_row_locks_seen'] is None and r['peak_table_level_locks'] is None
assert r['observation_capabilities']['table_data_locks'] == 'not_measured'
obj.data_locks=True
for row in obj.rows:
    row.update(row_locks_seen=0, table_is_locks=0)
r=ns['window'](obj,0,3)
assert r['peak_row_locks_seen'] == 0 and r['table_data_lock_samples'] == 2
# Semantic experiment errors must reach CLI exit, while a joined intentional
# cancellation is not an error and must not force full COUNT completion.
ns = {'argparse':argparse, 'sys':sys, 'EXPERIMENTS':{'fake':{}}, 'run': lambda *a: {'errors':[], 'worker_terminated':True}}
functions('experiments.py', ['main'], ns)
old=sys.argv
try:
    sys.argv=['fake','all']
    assert ns['main']() == 0
    errors = io.StringIO()
    with redirect_stderr(errors):
        ns['run']=lambda *a: {'errors':['cleanup failed'], 'worker_terminated':True}
        assert ns['main']() == 1
        ns['run']=lambda *a: {'errors':[], 'worker_terminated':False}
        assert ns['main']() == 1
    assert 'semantic experiment failures: fake' in errors.getvalue()
finally:
    sys.argv=old
print(json.dumps({'actual_functions_only':True,'db_access':False,'three_repeats_and_partition_outputs':True,
                  'disabled_locks_distinct_from_zero':True,'semantic_exit_status':True}))
