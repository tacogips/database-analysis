"""Independent scope/outcome/raw audit. No bench imports, DB access or approval.

Run only after the measurement owner ends. The expected 41-script/112-main-arm
scope is constructed independently of the producer's plan and success booleans.
"""
from collections import Counter
from pathlib import Path
import argparse
import json
import runpy

ROOT = Path(__file__).resolve().parent.parent


def expected_plan():
    items = []
    for prefix, mix, rate, split_only in [
        ('05-EXP-O6-WRITE-MIX-indexed-100', 'indexed', 100, False),
        ('06-EXP-O6-WRITE-MIX-indexed-800', 'indexed', 800, False),
        ('07-EXP-O6-WRITE-MIX-uniform-100', 'uniform_insert_delete', 100, False),
        ('08-EXP-O6-WRITE-MIX-uniform-800', 'uniform_insert_delete', 800, False),
        ('09-EXP-O6-WRITE-MIX-monotonic-100', 'monotonic_insert_delete', 100, False),
        ('09a-EXP-O6-WRITE-MIX-monotonic-800', 'monotonic_insert_delete', 800, False),
        ('0e-EXP-O10-ROW-CEILING-indexed-800', 'indexed', 800, True),
        ('0f-EXP-O10-ROW-CEILING-uniform-800', 'uniform_insert_delete', 800, True),
    ]:
        for group in ('splits', 'statements', 'auxiliary'):
            if split_only and group == 'statements':
                continue
            for block in range(1 if group == 'auxiliary' else 2):
                suffix = '' if group == 'splits' and block == 0 else f'-{group}-b{block+1}'
                names = ('split_k', 'split_pk') if group == 'splits' else ('statement_k', 'statement_pk')
                opportunities = [(rep, name) for rep in range(2*block, 2*block+2)
                                 for name in (names if rep % 2 == 0 else names[::-1])]
                if group == 'auxiliary':
                    opportunities = ([(0, 'client_timeout')] if rate == 800 and not split_only else []) + [(0, 'drift_split')]
                items.append(dict(id=prefix+suffix, mix=mix, rate=rate, group=group,
                                  block=block, opportunities=opportunities))
    for rep in range(5):
        opportunities = [(rep, 'control'), (rep, 'split_pk')]
        if rep % 2:
            opportunities.reverse()
        items.append(dict(id='0d-EXP-O10-INDEXED-100-STALL'+('' if rep == 0 else f'-tail-r{rep+1}'),
                          mix='indexed', rate=100, group='tail', block=0, opportunities=opportunities))
    return items


def outcome(rec):
    """Re-derive expected observation; a disconnect is never a count ceiling."""
    if rec.get('cleanup', {}).get('confirmed') is not True:
        return 'cleanup_failed'
    status = rec.get('status')
    if rec.get('arm') == 'client_timeout':
        return 'completed' if status in ('complete', 'observed_client_error') else 'unexpected_failure'
    if rec.get('arm') == 'control':
        return 'completed' if status == 'control' else 'unexpected_failure'
    if rec.get('measurement_error'):
        return 'unexpected_failure'
    if status == 'complete':
        return 'completed'
    if status in ('stopped:max_seconds', 'stopped:max_rows'):
        return 'confirmed_ceiling'
    if status in ('error_3024', 'error_1317'):
        before, after = rec.get('timer_counter_before'), rec.get('timer_counter_after')
        if type(before) is int and type(after) is int and after > before:
            return 'confirmed_ceiling'
        return 'unknown_interrupt'
    return 'unexpected_failure'


def check_result(d, expected, results, verify_raw=False):
    problems = []
    for field, value in [('protocol', 'partitioned-v3'), ('mix', expected['mix']),
                         ('target_writes_per_s', expected['rate']), ('group', expected['group']),
                         ('block', expected['block']), ('rows', 20000000),
                         ('split_seconds_ceiling', 150), ('statement_limit_ms', 120000)]:
        if d.get(field) != value:
            problems.append(f'{field} differs')
    # Results.finish() encodes completion through scopeComplete and process exit;
    # it does not produce a generic complete field. Exit is audited separately.
    if not d.get('scopeComplete') or d.get('section_errors'):
        problems.append('incomplete/section errors')
    cleanup = d.get('cleanup', {})
    workers = cleanup.get('workloads', [])
    if (cleanup.get('confirmed') is not True or not workers or
            cleanup.get('builder', {}).get('confirmed') is not True or
            any(w.get('confirmed') is not True for w in workers)):
        problems.append('owned cleanup unconfirmed/missing')
    if d.get('sections', {}).get('fixture_cleanup', {}).get('status') != 'dropped':
        problems.append('fixture not dropped')
    runs = d.get('sections', {}).get('arms', {}).get('runs', [])
    actual = [(r.get('rep'), r.get('arm')) for r in runs]
    if actual != expected['opportunities']:
        problems.append('missing/duplicate/reordered arm opportunities')
    declarations = d.get('opportunities', [])
    if [(r.get('rep'), r.get('arm')) for r in declarations] != expected['opportunities']:
        problems.append('opportunity declarations differ')
    outcomes = Counter()
    for i, rec in enumerate(runs):
        # Controls have no method connection; cleanup is the final workload gate.
        inspected = dict(rec)
        if rec.get('arm') == 'control':
            inspected['cleanup'] = {'confirmed': cleanup.get('confirmed')}
        observed = outcome(inspected)
        outcomes[observed] += 1
        if rec.get('outcome') != observed or observed not in ('completed', 'confirmed_ceiling'):
            problems.append((i, 'unexpected/unsubstantiated outcome', observed))
        if i < len(declarations) and (declarations[i].get('run_index') != i or declarations[i].get('outcome') != observed):
            problems.append((i, 'declaration/run correspondence differs'))
        if rec.get('arm') == 'control' and rec.get('seconds', 0) < 150:
            problems.append((i, 'tail control shortened'))
    windows = d.get('sections', {}).get('windows', {})
    streams, accounts = windows.get('raw_service_samples', []), windows.get('arrival_accounting', [])
    if not streams or len(streams) != len(accounts) or len(streams) != len(workers):
        problems.append('missing raw/workload/accounting correspondence')
    decoder = runpy.run_path(str(ROOT/'audit/audit-extra-evidence.py'))['raw_rows'] if verify_raw else None
    decoded_streams = []
    for i, (stream, a) in enumerate(zip(streams, accounts)):
        keys = ('scheduled', 'completed', 'failed', 'inflight', 'unstarted')
        if (any(type(a.get(k)) is not int or a[k] < 0 for k in keys) or
                not a.get('consistent') or a['scheduled'] != sum(a[k] for k in keys[1:]) or
                stream.get('samples') != a['scheduled']):
            problems.append((i, 'arrival/raw count conservation'))
        if verify_raw:
            clock_check=runpy.run_path(str(ROOT/'audit/audit-arrival-windows.py'))['check_stream_clock']
            problems.extend((i,e) for e in clock_check(stream,windows.get('clock'),'arrival-tuple-v2'))
            rows = list(decoder(results, stream))
            decoded_streams.append(rows)
            states = Counter(row[3] for row in rows)
            mapping = {'completed': 'ok', 'failed': 'error', 'inflight': 'stuck', 'unstarted': 'queued'}
            if sum(states.values()) != a['scheduled'] or any(a[k] != states[v] for k, v in mapping.items()):
                problems.append((i, 'raw state conservation'))
    if verify_raw:
        check_arms = runpy.run_path(str(ROOT/'audit/audit-arrival-windows.py'))['check_arms']
        problems.extend(check_arms(runs, decoded_streams, windows.get('clock'), accounts, list(decoder(results,windows['raw_server_samples'])) if 'raw_server_samples' in windows else None))
    monitor_errors = {}
    if verify_raw:
        for key in ('raw_server_samples', 'raw_stall_server_samples'):
            if key in windows:
                monitor_errors[key] = sum('error' in row for row in decoder(results, windows[key]))
    return {'problems': problems, 'armCount': len(runs), 'outcomes': dict(outcomes),
            'monitorErrorSamples': monitor_errors, 'rawVerified': verify_raw}


def audit(results, plan, verify_raw=False):
    expected = expected_plan()
    reports, problems = {}, []
    if Counter(x['id'] for x in plan['requiredScripts']) != Counter(x['id'] for x in expected):
        problems.append('declared script set differs from independent41-script scope')
    for item in expected:
        p = results / ('requested_'+item['id']+'.json')
        if not p.is_file():
            reports[item['id']] = {'problems': ['missing result']}
            continue
        try:
            reports[item['id']] = check_result(json.loads(p.read_text()), item, results, verify_raw)
        except Exception as error:
            reports[item['id']] = {'problems': ['malformed result or raw evidence: '+repr(error)]}
    return {'meaning': 'Independent owner checks, not performance/SLO/final acceptance.',
            'problems': problems, 'requiredScripts': len(expected),
            'requiredMainOpportunities': sum(len(x['opportunities']) for x in expected if x['group'] in ('splits', 'statements')),
            'results': reports}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-raw', action='store_true')
    args = parser.parse_args()
    plan = json.loads((ROOT/'bench/requested/write-mix-plan.json').read_text())
    print(json.dumps(audit(ROOT/'results', plan, args.verify_raw), ensure_ascii=False, indent=2))
