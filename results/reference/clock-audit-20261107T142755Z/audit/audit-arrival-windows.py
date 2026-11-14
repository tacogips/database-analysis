"""Owner-side raw-to-report audit. No benchmark imports or DB connections.

Rows are assigned by scheduled arrival time, including queued/stuck lower bounds.
This validates saved descriptive aggregates; it does not approve an SLO.
"""
from collections import Counter
import math


def check_window(rows, report, begin, end, kind):
    errors = []
    if not (isinstance(begin, (int, float)) and isinstance(end, (int, float))
            and math.isfinite(begin) and math.isfinite(end) and begin <= end):
        return ['invalid observation interval']
    selected = [r for r in rows if begin <= r[0] < end and r[1] == kind]
    counts = Counter(r[3] for r in selected)
    if set(counts) - {'ok', 'error', 'stuck', 'queued'}:
        errors.append('unknown response state')
    expected = dict(n=len(selected), scheduled=len(selected),
                    started=len(selected)-counts['queued'], completed=counts['ok'],
                    failed=counts['error'], errors=counts['error'],
                    inflight_at_end=counts['stuck'], queued_at_end=counts['queued'],
                    stuck_at_end=counts['stuck']+counts['queued'])
    for key, value in expected.items():
        if type(report.get(key)) is not int or report[key] != value:
            errors.append(f'{key}: reported={report.get(key)!r}, raw={value}')
    censored = bool(counts['stuck']+counts['queued'])
    if report.get('slo_assessable') is not (not censored):
        errors.append('censoring assessability differs')
    status = 'censored_lower_bounds' if censored else 'completed_responses'
    if report.get('latency_status') != status:
        errors.append('censoring label differs')
    # Audit the documented saved order-statistic convention, not a population tail.
    for field, column in [(None, 2), ('dispatch_delay', 4), ('client_execution', 5)]:
        values = sorted(r[column] for r in selected if len(r)>column and r[column] is not None)
        target = report if field is None else report.get(field, {})
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in values):
            errors.append(f'{field}: invalid latency sample')
            continue
        derived = {'n':len(values), 'max_ms':round(values[-1],2) if values else None}
        for key, q in [('p50_ms',.5),('p95_ms',.95),('p99_ms',.99)]:
            derived[key] = round(values[min(int(len(values)*q),len(values)-1)],2) if values else None
        for key, value in derived.items():
            if target.get(key) != value:
                errors.append(f'{field or "arrival_latency"}.{key}: reported={target.get(key)!r}, raw={value!r}')
    return errors


def check_arms(runs, streams):
    problems = []
    controls = [arm for arm in runs if arm.get('arm') == 'control']
    for i, arm in enumerate(runs):
        windows = [('method', arm, arm.get('t0'), arm.get('t1'))]
        if 'write_10s_before' in arm:
            windows.append(('before', {'write':arm['write_10s_before']}, arm['t0']-10, arm['t0']))
        if 'matched_control' in arm:
            if len(controls) != 1:
                problems.append((i, 'matched control lacks unique control interval'))
            else:
                matched = arm['matched_control']
                begin = controls[0]['t0']
                end = min(controls[0]['t1'], begin+arm['seconds'])
                if not math.isclose(matched['seconds'], end-begin, abs_tol=1e-6):
                    problems.append((i, 'matched control duration differs'))
                windows.append(('matched_control', matched, begin, end))
        for label, report, begin, end in windows:
            if len(report.get('write', [])) != len(streams):
                problems.append((i, label, 'write stream/report correspondence differs'))
                continue
            for stream_index, rows in enumerate(streams):
                for error in check_window(rows, report['write'][stream_index], begin, end, 'write'):
                    problems.append((i, label, stream_index, 'write', error))
            if streams and label != 'before':
                for error in check_window(streams[0], report.get('read', {}), begin, end, 'read'):
                    problems.append((i, label, 0, 'read', error))
    return problems


def check_inline_arm_windows(arms):
    """Check saved trial cohorts and timestamped method windows in TDE reports.

    Baseline/trial/recovery boundaries are not stored as timestamps. Trial
    membership is checked against the full raw stream, but its original window
    selection cannot independently be reconstructed. Method boundaries can be.
    """
    problems = []
    method_windows = 0
    for i, arm in enumerate(arms):
        raw = arm.get('raw_service_samples', [])
        for row in raw:
            if (len(row) < 4 or not isinstance(row[0], (int, float))
                    or not math.isfinite(row[0]) or row[1] not in ('read', 'write')
                    or row[3] not in ('ok', 'error', 'stuck', 'queued')):
                problems.append((i, 'invalid raw arrival record'))
                break
        trial = arm.get('trial_service_samples')
        if not isinstance(trial, list):
            problems.append((i, 'missing saved trial cohort'))
        else:
            if Counter(map(tuple, trial)) - Counter(map(tuple, raw)):
                problems.append((i, 'trial cohort is not a multiset subset of raw arrivals'))
            begin = min((r[0] for r in trial), default=0)
            end = math.nextafter(max((r[0] for r in trial), default=0), math.inf)
            if trial and Counter(map(tuple, trial)) != Counter(tuple(r) for r in raw if begin <= r[0] < end):
                problems.append((i, 'saved trial cohort omits internal raw arrivals'))
            for kind in ('read', 'write'):
                report = arm.get('trial', {}).get('service', {}).get(kind, {})
                for error in check_window(trial, report, begin, end, kind):
                    problems.append((i, 'saved_trial_cohort', kind, error))
        prefix = 'statement' if arm.get('method', arm.get('arm')) == 'count' else 'method'
        if arm.get('method', arm.get('arm')) == 'control':
            continue
        begin, end = arm.get(prefix+'_started_at'), arm.get(prefix+'_finished_at')
        reports = arm.get('actual_'+prefix+'_service')
        if not isinstance(reports, dict):
            problems.append((i, 'missing actual method-window report'))
            continue
        method_windows += 1
        for kind in ('read', 'write'):
            for error in check_window(raw, reports.get(kind, {}), begin, end, kind):
                problems.append((i, 'timestamped_method', kind, error))
    return {'problems': problems, 'arms': len(arms), 'savedTrialCohorts': len(arms),
            'timestampedMethodWindows': method_windows,
            'limitation': 'Trial cohort quantiles/membership verified; original broad-window timestamp boundaries and baseline/recovery quantiles cannot be reconstructed from this artifact. No SLO approval.'}
