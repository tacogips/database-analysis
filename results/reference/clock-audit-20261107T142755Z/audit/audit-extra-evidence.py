"""Independent read-only checks for the final owner audit. Never approves a run.

No bench module is imported and no DB is contacted. Run after all has ended;
decompression and raw-window checks intentionally do not overlap measurement.
"""
from collections import Counter
from decimal import Decimal
from pathlib import Path
import argparse
import gzip
import hashlib
import json
import math
import runpy

ROOT = Path(__file__).resolve().parent.parent
TARGETS = {Decimal('.03'), Decimal('.05'), Decimal('.10')}
SEEDS = {1, 7, 42, 317, 991}
FIXTURES = {'dense', 'gaps', 'islands', 'negative', 'wide', 'moderate_gaps', 'uneven_density'}
SCALE_CASES = {('indexed', .1, 10000000), ('indexed', .3, 10000000),
               ('uniform_insert_delete', .1, 10000000), ('uniform_insert_delete', .3, 10000000),
               ('uniform_insert_delete', .1, 20000000)}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(65536), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text())


def raw_rows(results, stream):
    """Independent bounded decoder; validate both hashes, lengths and counts."""
    assert stream['format'] == 'jsonl+gzip-v1'
    cap = stream['chunk_uncompressed_byte_cap']
    assert 0 < cap <= 8 * 1024 * 1024
    count = 0
    seen = set()
    for chunk in stream['chunks']:
        p = (results / chunk['path']).resolve()
        assert results.resolve() in p.parents, ('escaped raw path', p)
        assert str(p) not in seen, ('duplicate chunk path', p)
        seen.add(str(p))
        assert p.stat().st_size == chunk['bytes']
        assert digest(p) == chunk['sha256']
        with gzip.open(p, 'rb') as f:
            data = f.read(cap + 1)
        assert len(data) <= cap and len(data) == chunk['uncompressed_bytes']
        assert hashlib.sha256(data).hexdigest() == chunk['uncompressed_sha256']
        assert data.endswith(b'\n')
        lines = data.splitlines()
        assert len(lines) == chunk['samples']
        for line in lines:
            yield json.loads(line)
            count += 1
    assert count == stream['samples'], ('raw stream count', count, stream['samples'])


def check_source(candidate):
    before, after = candidate['measurementSourceBefore'], candidate['measurementSourceAfter']
    bad = []
    if before != after or not candidate.get('measurementSourceUnchanged'):
        bad.append('recorded measurement source changed during run')
    suffixes = {'.py', '.rb', '.json', '.retired', '.versions', '.encrypted', '.outputs'}
    expected = {str(p.relative_to(ROOT)) for p in (ROOT / 'bench').rglob('*') if p.is_file() and p.suffix in suffixes}
    expected.update(str(p.relative_to(ROOT)) for p in (ROOT / 'bench/keyring').glob('*') if p.is_file())
    expected.update(str(p.relative_to(ROOT)) for p in (ROOT / 'sql').glob('*.sql'))
    expected.update(['lab.rb', 'compose.yaml', 'mysql-images.json', 'mise.toml', 'bench/Dockerfile',
                     '.riela/workflows/table-count-guideline-review/scripts/run-requested.rb',
                     '.riela/workflows/table-count-guideline-review/scripts/run-white-all.sh'])
    if set(after) != expected:
        bad.append({'sourceSetMissing': sorted(expected-set(after)), 'sourceSetExtra': sorted(set(after)-expected)})
    for rel, sha in after.items():
        p = (ROOT / rel).resolve()
        if ROOT not in p.parents or not p.is_file() or digest(p) != sha:
            bad.append({'currentSourceDiffers': rel})
    return bad


def check_frontier(data):
    bad, summary = [], {}
    if not data.get('complete') or data.get('fixture_cleanup') != 'confirmed':
        bad.append('frontier not complete/cleaned')
    if set(data.get('cases', {})) != FIXTURES:
        bad.append('frontier fixture set differs')
    for name, case in data.get('cases', {}).items():
        truth = case['truth']
        if case['initial_truth_check'].get('rows') != truth:
            bad.append((name, 'initial truth oracle mismatch'))
        actual = Counter((r['seed'], Decimal(str(r['target_relative_error']))) for r in case['runs'])
        expected = Counter((seed, target) for seed in SEEDS for target in TARGETS)
        if actual != expected:
            bad.append((name, 'missing/duplicate target or seed'))
        states, missed = Counter(), 0
        for r in case['runs']:
            states[r['status']] += 1
            if r.get('comparison_count', {}).get('rows') != truth:
                bad.append((name, r['seed'], 'paired fullCOUNT mismatch/missing'))
            if r.get('cleanupStatus') != 'rolled_back':
                bad.append((name, r['seed'], 'snapshot cleanup unconfirmed'))
            if not math.isfinite(r['seconds']) or r['seconds'] < 0:
                bad.append((name, 'invalid elapsed time'))
            if 'lower' in r:
                lo, hi, estimate = r['lower'], r['upper'], Decimal(r['estimate'])
                if not 0 <= lo <= hi <= r['domain']:
                    bad.append((name, 'invalid interval endpoints'))
                covers = lo <= truth <= hi
                missed += not covers
                if r.get('interval_covers_truth') != covers:
                    bad.append((name, 'reported coverage differs from truth'))
                if r['accuracyStatus'] == 'target_reached':
                    if lo <= 0 or max(abs(estimate-lo), abs(Decimal(hi)-estimate)) > Decimal(str(r['target_relative_error']))*lo:
                        bad.append((name, 'claimed target bound is not met'))
            if r['status'] == 'target_reached' and r['budgetStatus'] != 'within_budget':
                bad.append((name, 'operational success despite exhausted budget'))
        summary[name] = {'runs': len(case['runs']), 'statuses': dict(states),
                         'observedNoncoverage': missed, 'truth': truth}
    return {'problems': bad, 'fixtures': summary,
            'meaning': 'Observed noncoverage is reported, not automatically a code defect: nominal confidence is probabilistic per invocation.'}


def check_service(data):
    protocols = {'sample3': {'target': .03, 'looks': [512,2048,8192]},
                 'sample10': {'target': .10, 'looks': [160,640,2560,8192]}}
    expected = Counter((index, mix, repeat, method) for index in (False, True)
                       for mix in ('nonindexed', 'indexed') for repeat in range(3)
                       for method in ('control', 'count', 'sample3', 'sample10'))
    actual = Counter((r['secondary_index'], r['write_mix'], r['repeat'], r['method']) for r in data['arms'])
    bad = []
    if (data.get('columns') != 100 or data.get('rows') != 750000 or
            data.get('encryption') != 'InnoDB TDE' or data.get('protocols') != protocols):
        bad.append('declared fixture/accuracy protocols differ')
    if data.get('unexpected_sampling_failures'):
        bad.append('recorded mandatory sampling failure')
    if (not data.get('complete') or data.get('reusable_mandatory_completion') is not True or
            actual != expected):
        bad.append('missing/duplicate48-arm service grid')
    for r in data['arms']:
        a = r['arrival_accounting']
        if not a['consistent'] or a['scheduled'] != a['completed']+a['failed']+a['inflight']+a['unstarted']:
            bad.append((r['method'], r['repeat'], 'arrival conservation'))
        raw = r.get('raw_service_samples', [])
        states = Counter(row[3] for row in raw)
        mapping = {'completed':'ok', 'failed':'error', 'inflight':'stuck', 'unstarted':'queued'}
        if (a['scheduled'] <= 0 or len(raw) != a['scheduled'] or
                any(a[k] != states[v] for k,v in mapping.items())):
            bad.append((r['method'], r['repeat'], 'raw service count/state mismatch'))
        if 'sampling' in r:
            sample = r['sampling']
            no_snapshot_budget = (sample.get('status') == 'budget_exhausted'
                                  and sample.get('cleanupStatus') == 'not_open')
            if sample.get('cleanupStatus') != 'rolled_back' and not no_snapshot_budget:
                bad.append((r['method'], r['repeat'], 'sampling cleanup'))
        for field in ('method_cleanup', 'workload_cleanup', 'sampler_cleanup'):
            if (field == 'method_cleanup' and r['method'] == 'control'
                    and field not in r and 'method_connection_id' not in r):
                continue  # Control opens no dedicated count/sampling connection.
            if r.get(field, {}).get('confirmed') is not True:
                bad.append((r['method'], r['repeat'], field+' missing/unconfirmed'))
        if r.get('errors') or r.get('unexpected_failures'):
            bad.append((r['method'], r['repeat'], 'recorded unexpected arm failure'))
        if r['method'] == 'count':
            valid_count = (r.get('count_status') == 'complete' and r.get('count') == data.get('rows'))
            valid_timer = (r.get('count_status') == 'error' and r.get('errno') in (1317, 3024)
                           and r.get('timer_counter_delta', 0) > 0)
            if not (valid_count or valid_timer):
                bad.append((r['method'], r['repeat'], 'count failure without confirmed timer'))
        if r['method'].startswith('sample'):
            sample = r.get('sampling', {})
            protocol = protocols[r['method']]
            if (sample.get('target_relative_error') != protocol['target'] or
                    sample.get('looks') != protocol['looks'] or sample.get('width') != 200 or
                    sample.get('family_alpha') != .05 or sample.get('max_seconds') != 1):
                bad.append((r['method'], r['repeat'], 'actual sampling protocol differs from method label'))
            if r.get('count_status') != sample.get('status'):
                bad.append((r['method'], r['repeat'], 'arm/sample outcome labels differ'))
            valid = sample.get('status') in ('target_reached', 'precision_not_reached', 'budget_exhausted')
            timer = sample.get('status') in ('query_failed', 'statement_budget_exhausted') and sample.get('errno') == 3024
            if not (valid or timer):
                bad.append((r['method'], r['repeat'], 'unexpected sampling outcome'))
            if sample.get('status') == 'target_reached' and sample.get('budgetStatus') != 'within_budget':
                bad.append((r['method'], r['repeat'], 'precision success beyond budget'))
            if 'lower' in sample:
                lo, hi, estimate = sample['lower'], sample['upper'], Decimal(sample['estimate'])
                if not 0 <= lo <= hi <= sample['domain']:
                    bad.append((r['method'], r['repeat'], 'invalid confidence interval'))
                if sample.get('accuracyStatus') == 'target_reached' or sample.get('status') == 'target_reached':
                    if lo <= 0 or max(abs(estimate-lo), abs(Decimal(hi)-estimate)) > Decimal(str(protocol['target']))*lo:
                        bad.append((r['method'], r['repeat'], 'claimed relative precision not met'))
            elif sample.get('status') == 'target_reached':
                bad.append((r['method'], r['repeat'], 'precision claimed without interval'))
    inline_check = runpy.run_path(str(ROOT/'audit/audit-arrival-windows.py'))['check_inline_arm_windows'](data['arms'])
    bad.extend(('inlineWindow', *problem) for problem in inline_check['problems'])
    return {'problems': bad, 'arms': len(data['arms']),
            'inlineWindowAudit': inline_check, 'notAnSloApproval': True}


def check_latency_windows(data):
    """Check independently recorded window counts; sparse tails remain descriptive."""
    bad, sparse, censored, windows = [], [], [], 0

    def walk(value, path='$'):
        nonlocal windows
        if isinstance(value, dict):
            fields = ('scheduled', 'started', 'completed', 'failed', 'inflight_at_end', 'queued_at_end')
            if all(k in value for k in fields):
                windows += 1
                counts = [value[k] for k in fields]
                if any(type(n) is not int or n < 0 for n in counts):
                    bad.append((path, 'invalid arrival counts'))
                else:
                    scheduled, started, completed, failed, inflight, queued = counts
                    if (scheduled != completed+failed+inflight+queued or
                            started != completed+failed+inflight or
                            value.get('n') != scheduled or value.get('stuck_at_end') != inflight+queued):
                        bad.append((path, 'window arrival conservation'))
                    if inflight+queued:
                        censored.append(path)
                        if value.get('slo_assessable') or value.get('latency_status') != 'censored_lower_bounds':
                            bad.append((path, 'censored responses presented as complete'))
                    if scheduled < 100:
                        sparse.append({'path': path, 'scheduled': scheduled})
            for key, child in value.items():
                walk(child, f'{path}.{key}')
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f'{path}[{i}]')

    walk(data)
    return {'problems': bad, 'windows': windows, 'sparseWindows': sparse,
            'censoredWindows': censored,
            'meaning': 'Fewer than100 responses cannot resolve a1% empirical tail usefully. This is a descriptive warning, not a100-response sufficiency rule or an SLO approval.'}


def check_scale(results, plan, verify_raw):
    bad, reports, repetitions = [], {}, Counter()
    for name in plan['requiredScripts']:
        path = results / f'requested_{name}.json'
        if not path.is_file():
            bad.append((name, 'missing result'))
            continue
        d = read(path)
        key = (d['mix'], d['share_of_capacity'], d['rows'])
        if key not in SCALE_CASES or d['repeat'] not in (0, 1, 2) or d['group'] not in ('core', 'secondary'):
            bad.append((name, 'unexpected case/group/repetition'))
        repetitions[(key, d['group'], d['repeat'])] += 1
        methods = ['statement_k'] if d['group'] == 'secondary' else ['statement_pk', 'split_pk'] + ([] if d['mix'] == 'indexed' else ['split_k'])
        expected = Counter(methods + ['control'])
        arms = d.get('sections', {}).get('arms', {})
        runs = arms.get('runs', [])
        if (not d.get('scopeComplete') or d.get('section_errors') or
                Counter(r['arm'] for r in runs) != expected or arms.get('not_run_near_limit')):
            bad.append((name, 'incomplete planned scope'))
        windows = d.get('sections', {}).get('windows', {})
        streams, accounting = windows.get('raw_service_samples', []), windows.get('arrival_accounting', [])
        if len(streams) != len(accounting) or not streams:
            bad.append((name, 'missing raw/conservation streams'))
        decoded_streams = []
        for i, (stream, a) in enumerate(zip(streams, accounting)):
            if not a['consistent'] or stream['samples'] != a['scheduled']:
                bad.append((name, i, 'serialized count differs from scheduled'))
            if verify_raw:
                decoded = list(raw_rows(results, stream))
                decoded_streams.append(decoded)
                states = Counter(row[3] for row in decoded)
                observed = {'completed': states['ok'], 'failed': states['error'], 'inflight': states['stuck'], 'unstarted': states['queued']}
                if any(a[k] != v for k, v in observed.items()) or sum(states.values()) != a['scheduled']:
                    bad.append((name, i, 'raw state conservation mismatch'))
        if verify_raw:
            check_arms = runpy.run_path(str(ROOT/'audit/audit-arrival-windows.py'))['check_arms']
            bad.extend((name, *problem) for problem in check_arms(runs, decoded_streams))
        server_errors = None
        if verify_raw and 'raw_server_samples' in windows:
            server_errors = sum('error' in row for row in raw_rows(results, windows['raw_server_samples']))
        reports[name] = {'arms': len(runs), 'outcomes': dict(Counter(r['status'] for r in runs)),
                         'rawSamples': [s['samples'] for s in streams], 'rawVerified': verify_raw,
                         'monitorErrorSamples': server_errors,
                         'shortMatchedControls': [{'arm': r['arm'], 'methodSeconds': r['seconds'],
                                                  'control': r.get('matched_control', {}).get('seconds')}
                                                 for r in runs if 'matched_control' in r and not r['matched_control']['full_method_duration_covered']]}
    expected_reps = Counter((case, group, repeat) for case in SCALE_CASES for group in ('core', 'secondary') for repeat in range(3))
    if repetitions != expected_reps:
        bad.append('required5cases x2groups x3repetitions differ')
    return {'problems': bad, 'results': reports,
            'meaning': 'Experiment completion differs from completed COUNT. Censored outcomes, monitor errors and shorter controls require explicit interpretation; this is not an SLO pass.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-raw', action='store_true')
    args = parser.parse_args()
    results = ROOT / 'results'
    candidate = read(results / 'reviews/final-all-candidate.json')
    out = {'meaning': 'Owner evidence checks only, never automatic acceptance.',
           'candidateState': {k: candidate.get(k) for k in ('status', 'exitCode', 'controllerRuby', 'requestedSummaryFresh')},
           'sourceProblems': check_source(candidate), 'missing': []}
    for filename, key, checker in [('requested_accuracy_cost_frontier.json', 'accuracyFrontier', check_frontier),
                                   ('requested_accuracy_service_100.json', 'accuracyService', check_service)]:
        p = results / filename
        if p.is_file():
            out[key] = checker(read(p))
        else:
            out['missing'].append(filename)
    out['primaryScale'] = check_scale(results, read(ROOT / 'bench/requested/primary-scale-plan.json'), args.verify_raw)
    out['latencyWindows'] = {p.name: check_latency_windows(read(p))
                             for p in sorted(results.glob('*.json'))}
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
