"""Read-only evidence inventory. Never opens a DB or declares final acceptance.

Run after final all, then inspect every flagged item against its experiment protocol.
Source-derived coverage is deliberately separate from semantic correctness.
"""
import ast
import hashlib
import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def source(name):
    return ast.parse((ROOT / 'bench' / name).read_text())


def literal_assignment(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(name)


def read(relative):
    p = ROOT / relative
    return json.loads(p.read_text()) if p.is_file() else None


def flagged(value, path='$'):
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            here = f'{path}.{k}'
            if (k in ('errors', 'section_errors', '_error', 'crash', 'failed', 'arm_error', 'arm_timeout', 'not_run_near_limit') and v) or (k in ('complete', 'correct', 'consistent') and v is False) or (k == 'skipped' and v):
                out.append({'path': here, 'value': str(v)[:1200]})
            out.extend(flagged(v, here))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.extend(flagged(v, f'{path}[{i}]'))
    return out


def main():
    tree = source('experiments.py')
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'exp' and node.args and isinstance(node.args[0], ast.Constant):
            names.add(node.args[0].value)
    # LOCK_TESTS values reference setup variables; only keys are needed.
    lock_tests = next(n.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'LOCK_TESTS' for t in n.targets))
    names.update(ast.literal_eval(k) for k in lock_tests.keys)
    scope = read('bench/nonlocking-scope.json')
    selected_names = set(scope['baseImpactNames'])
    skipped_names = {x['name'] for x in scope['skippedBaseImpacts']}
    if selected_names & skipped_names or selected_names | skipped_names != names:
        raise ValueError('impact scope inventory does not partition the full source registry')
    names = selected_names
    tables = literal_assignment(source('count_shapes.py'), 'TABLES')
    lock_tree = source('lock_factors.py')
    designs = literal_assignment(lock_tree, 'DESIGNS')
    isolations = literal_assignment(lock_tree, 'ISOLATIONS')
    statements = literal_assignment(lock_tree, 'STATEMENTS')
    expected_locks = {(d, i, s) for d in designs for i in isolations for s in statements}
    matrix = read('mysql-images.json')['images']
    script_paths = sorted((ROOT / 'bench/requested').glob('*.py'))
    available_scripts = {p.stem for p in script_paths}
    scripts = {s['id'] for s in scope['requestedScripts']}
    skipped_scripts = {s['id'] for s in scope['skippedRequestedScripts']}
    if scripts & skipped_scripts or scripts | skipped_scripts != available_scripts:
        raise ValueError('requested scope inventory does not partition the available scripts')
    metadata_problems = []
    for item in scope['requestedScripts'] + scope['skippedRequestedScripts']:
        p = ROOT / 'bench/requested' / (item['id'] + '.py')
        versions = p.with_suffix('.versions').read_text().split() if p.with_suffix('.versions').exists() else [None]
        outputs = p.with_suffix('.outputs').read_text().split() if p.with_suffix('.outputs').exists() else []
        if versions != item['versions'] or outputs != item['requiredOutputs'] or p.with_suffix('.encrypted').exists() != item['encrypted']:
            metadata_problems.append(item['id'])
    requested_versions = {s['id']: s['versions'] for s in scope['requestedScripts']}
    summary = read('results/requested/last_run.json') or {}
    all_ran = {r['id']: r for r in summary.get('ran', [])}
    ran = {n:r for n,r in all_ran.items() if n in scripts}
    shapes = read('results/count_shapes.json') or {}
    locks = read('results/lock_factors.json') or []
    actual_locks = {(r['design'], r['isolation'], r['statement']) for r in locks}
    artifact_problems = []
    for name, result in ran.items():
        for item in result.get('artifacts', []):
            p = ROOT / item['path']
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != item['sha256'] or p.stat().st_size != item.get('bytes'):
                artifact_problems.append({'experiment': name, 'artifact': item['path']})
    semantic_flags = {}
    selected_results = {'results/count_shapes.json', 'results/lock_factors.json'}
    selected_results.update(f'results/impact_{n}.json' for n in names)
    selected_results.update(f'results/matrix_{v}.json' for v in scope['matrixVersions'])
    selected_results.update(p for s in scope['requestedScripts'] for p in s['requiredOutputs'])
    selected_results.update(a['path'] for r in ran.values() for a in r.get('artifacts', []) if a['path'].endswith('.json'))
    for relative in sorted(selected_results):
        data = read(relative)
        if data is not None:
            items = flagged(data)
            if items:
                semantic_flags[relative] = items
    window_audit = runpy.run_path(str(ROOT/'audit/audit-arrival-windows.py'))['check_recorded_windows']
    impact_clock_problems = {name:window_audit(read(f'results/impact_{name}.json') or {}) for name in sorted(names)}
    report = {
        'meaning': 'Current selected scope inventory only. Skipped historical/optional cases are not missing, measured or passed. Not an all-run proof or acceptance. Flags require protocol-specific classification, including expected time-boxed cancellations and intentionally unsupported forms.',
        'scope': {'path': 'bench/nonlocking-scope.json', 'revision': scope['revision'],
                  'sha256': hashlib.sha256((ROOT/'bench/nonlocking-scope.json').read_bytes()).hexdigest(),
                  'status': scope['status'], 'priorScope': scope['priorScope']},
        'skippedByUserScope': {'impact': scope['skippedBaseImpacts'], 'requested': scope['skippedRequestedScripts']},
        'scopeMetadataProblems': metadata_problems,
        'summaryScopeMatchesCurrent': summary.get('scope_sha256') == hashlib.sha256((ROOT/'bench/nonlocking-scope.json').read_bytes()).hexdigest(),
        'summaryPlannedScriptsMatchCurrent': summary.get('planned_scripts') == [s['id'] for s in scope['requestedScripts']],
        'summarySkipInventoryMatchesCurrent': summary.get('skipped_by_user_scope') == scope['skippedRequestedScripts'],
        'expected': {'impactExperiments': len(names), 'tableShapes': len(tables), 'lockCases': len(expected_locks), 'matrixVersions': scope['matrixVersions'], 'requestedScripts': len(scripts), 'requestedProcesses': sum(map(len, requested_versions.values()))},
        'missing': {'impact': sorted(n for n in names if not (ROOT / f'results/impact_{n}.json').is_file()), 'shapes': sorted(set(tables) - set(shapes)), 'locks': sorted(expected_locks - actual_locks), 'matrix': sorted(v for v in scope['matrixVersions'] if not (ROOT / f'results/matrix_{v}.json').is_file()), 'requested': sorted(scripts - set(ran))},
        'unexpected': {'impact': sorted(p.stem.removeprefix('impact_') for p in (ROOT/'results').glob('impact_*.json') if p.stem.removeprefix('impact_') not in names | skipped_names), 'shapes': sorted(set(shapes)-set(tables)), 'locks': sorted(actual_locks-expected_locks), 'requested': sorted(set(all_ran)-scripts-skipped_scripts)},
        'historicalOrOptionalPresentNotCredited': {
            'impact': sorted(n for n in skipped_names if (ROOT/f'results/impact_{n}.json').is_file()),
            'requested': sorted(set(all_ran) & skipped_scripts)},
        'matrixScopeMatchesPinnedImages': set(scope['matrixVersions']) == set(matrix),
        'requestedVersionMismatches': {n: {'expected': requested_versions[n], 'actual': [r.get('version') for r in ran[n].get('runs', [])]} for n in scripts & set(ran) if [r.get('version') for r in ran[n].get('runs', [])] != requested_versions[n]},
        'nonlockingCountViolations': [{'design': r.get('design'), 'isolation': r.get('isolation'), 'statement': r.get('statement'), 'record_locks': r.get('record_locks'), 'outcome': r.get('outcome')} for r in locks if r.get('outcome') != 'ok' or r.get('record_locks') != 0 or r.get('update_row', {}).get('blocked') is not False or r.get('insert_row', {}).get('blocked') is not False],
        'duplicateLockCases': len(locks) - len(actual_locks),
        'requestedSummaryStatus': summary.get('status'),
        'requestedNotSuccessful': sorted(n for n, r in ran.items() if not r.get('ok') or not r.get('runs') or any(x.get('exit_code') != 0 for x in r.get('runs', []))),
        'requestedReused': sorted(n for n, r in ran.items() if r.get('reused')),
        'requestedArtifactProblems': artifact_problems,
        'semanticFlagsToClassify': semantic_flags,
        'impactClockWindowProblems': impact_clock_problems,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
