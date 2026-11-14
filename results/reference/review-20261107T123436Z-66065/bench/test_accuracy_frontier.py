"""Failure persistence regression using the actual function AST; no DB imports."""
import ast
import copy
import json
from pathlib import Path


tree = ast.parse(Path(__file__).with_name('accuracy_frontier.py').read_text())
fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'record_sample')
unexpected = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'UNEXPECTED' for t in n.targets))
namespace = {'UNEXPECTED': unexpected}
exec(compile(ast.Module(body=[fn], type_ignores=[]), '<actual record_sample>', 'exec'), namespace)
record_sample = namespace['record_sample']
budget_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'sample_with_budgets')
exec(compile(ast.Module(body=[budget_fn], type_ignores=[]), '<actual sample_with_budgets>', 'exec'), namespace)
for errno, cleanup, overall, expected in (
    (3024, 'rolled_back', 'within_budget', 'statement_budget_exhausted'),
    (3024, 'rolled_back', 'exhausted', 'statement_budget_exhausted'),
    (1317, 'rolled_back', 'within_budget', 'query_failed'),
    (3024, 'failed', 'within_budget', 'query_failed'),
):
    namespace['sample'] = lambda: dict(status='query_failed', errno=errno, cleanupStatus=cleanup, budgetStatus=overall)
    result = namespace['sample_with_budgets']()
    assert result['status'] == expected and result['budgetStatus'] == overall
    assert result['statementBudgetStatus'] == ('exhausted' if errno == 3024 else 'not_observed')

item, saved, comparison_calls = {'runs': []}, [], []


def comparison():
    comparison_calls.append(True)
    raise OSError('lost connection')


try:
    record_sample(item, {'status': 'query_failed', 'error': 'original sample failure'}, comparison,
                  lambda: saved.append(copy.deepcopy(item)))
except RuntimeError:
    pass
else:
    raise AssertionError('unexpected sampling failure must fail the experiment')
assert not comparison_calls and saved[0]['runs'][0]['error'] == 'original sample failure'

item, saved = {'runs': []}, []
try:
    record_sample(item, {'status': 'target_reached', 'estimate': '123'}, comparison,
                  lambda: saved.append(copy.deepcopy(item)))
except OSError:
    pass
else:
    raise AssertionError('failed comparison must fail the experiment')
assert saved[0]['runs'][0]['estimate'] == '123'
assert saved[-1]['runs'][0]['comparison_count']['status'] == 'query_failed'
assert saved[-1]['runs'][0]['estimate'] == '123'

print(json.dumps({'check': 'actual AST function, fake failed connection; no DB',
                  'failed_sample_saved_before_comparison': True,
                  'comparison_not_called_after_unexpected_sample_failure': True,
                  'sample_and_comparison_error_both_preserved': True,
                  'confirmed_statement_limit_distinct_from_total_budget': True,
                  'unknown_interrupt_or_failed_cleanup_not_normalized': True}))
