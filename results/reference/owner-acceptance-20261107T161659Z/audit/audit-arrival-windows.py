"""Owner-side raw-to-report audit. No benchmark imports or DB connections.

Rows are assigned by scheduled arrival time, including queued/stuck lower bounds.
This validates saved descriptive aggregates; it does not approve an SLO.
"""
from collections import Counter
import math
import re


def check_window(rows, report, begin, end, kind, clock=None):
    errors = clock_problems(clock)
    if not (isinstance(begin, (int, float)) and isinstance(end, (int, float))
            and math.isfinite(begin) and math.isfinite(end) and begin < end):
        return ['invalid observation interval']
    errors.extend(boundary_problems(report.get('window'), begin, end, clock))
    if not clock_problems(clock) and any(not isinstance(r,(list,tuple)) or len(r)!=11 or r[10]!=clock['domain_id'] for r in rows):
        return errors+['unsupported/foreign arrival row clock']
    selected = [r for r in rows if len(r)>3 and numeric(r[0]) and begin <= r[0] < end and r[1] == kind]
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
    if report.get('slo_assessable') is not (bool(selected) and not censored):
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


def check_arms(runs, streams, clock=None, accounts=None, server_rows=None):
    problems = list(clock_problems(clock))
    if accounts is None or len(accounts) != len(streams):
        problems.append('missing independent arrival schedules')
    else:
        for i,(rows,account) in enumerate(zip(streams, accounts)):
            problems.extend((i,e) for e in check_arrivals(rows, account, clock))
    if server_rows is None:
        problems.append('missing server raw for method-window audit')
    controls = [arm for arm in runs if arm.get('arm') == 'control']
    for i, arm in enumerate(runs):
        problems.extend((i,e) for e in same_clock(arm.get('clock'), clock))
        if server_rows is not None:
            problems.extend((i,'server',e) for e in check_server_window(server_rows,arm.get('server',{}),arm.get('t0'),arm.get('t1'),clock))
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
                for error in check_window(rows, report['write'][stream_index], begin, end, 'write', clock):
                    problems.append((i, label, stream_index, 'write', error))
            if streams and label != 'before':
                for error in check_window(streams[0], report.get('read', {}), begin, end, 'read', clock):
                    problems.append((i, label, 0, 'read', error))
    return problems


def check_inline_arm_windows(arms):
    """Recompute every broad and actual window; legacy clocks remain unsupported."""
    problems, method_windows = [], 0
    for i, arm in enumerate(arms):
        clock = arm.get('clock')
        if 'clock_end' in arm:
            problems.extend((i,e) for e in same_clock(arm['clock_end'],clock))
        problems.extend((i,e) for e in clock_problems(clock))
        raw = arm.get('raw_service_samples', [])
        problems.extend((i,e) for e in check_arrivals(raw, arm.get('arrival_accounting', {}), clock))
        server = arm.get('server_samples', [])
        for label in ('baseline','trial','recovery'):
            report = arm.get(label, {})
            bounds = report.get('window', {})
            a,b = bounds.get('begin'), bounds.get('end')
            problems.extend((i,label,e) for e in boundary_problems(bounds,a,b,clock))
            for kind in ('read','write'):
                problems.extend((i,label,kind,e) for e in check_window(raw,report.get('service',{}).get(kind,{}),a,b,kind,clock))
            problems.extend((i,label,'server',e) for e in check_server_window(server,report.get('server',{}),a,b,clock))
            if valid_interval(a,b) and not math.isclose(report.get('seconds',-1),b-a,abs_tol=.00051):
                problems.append((i,label,'window duration differs'))
        bounds = [arm.get(label,{}).get('window',{}) for label in ('baseline','trial','recovery')]
        if bounds[0].get('end') != bounds[1].get('begin') or bounds[1].get('end') != bounds[2].get('begin'):
            problems.append((i,'broad windows are not contiguous'))
        trial = arm.get('trial_service_samples')
        a,b=bounds[1].get('begin'),bounds[1].get('end')
        if not isinstance(trial,list) or not valid_interval(a,b):
            problems.append((i,'missing/invalid saved trial cohort or boundary'))
        elif Counter(map(tuple,trial)) != Counter(tuple(r) for r in raw if a <= r[0] < b):
            problems.append((i,'saved trial cohort drops/adds arrivals'))
        role=arm.get('method',arm.get('arm'))
        if role=='control':
            continue
        prefix='statement' if role=='count' else 'method'
        a,b=arm.get(prefix+'_started_at'),arm.get(prefix+'_finished_at')
        problems.extend((i,'actual',e) for e in boundary_problems(arm.get('actual_'+prefix+'_window'),a,b,clock))
        method_windows += 1
        if valid_interval(a,b):
            if not math.isclose(arm.get(prefix+'_seconds',-1),b-a,abs_tol=.000051):
                problems.append((i,'actual method duration differs'))
            if not (valid_interval(bounds[1].get('begin'),bounds[1].get('end')) and bounds[1]['begin'] <= a < b <= bounds[1]['end']):
                problems.append((i,'actual method outside trial'))
        for kind in ('read','write'):
            problems.extend((i,'actual',kind,e) for e in check_window(raw,arm.get('actual_'+prefix+'_service',{}).get(kind,{}),a,b,kind,clock))
        problems.extend((i,'actual','server',e) for e in check_server_window(server,arm.get('actual_'+prefix+'_server',{}),a,b,clock))
    return dict(problems=problems, arms=len(arms), savedTrialCohorts=len(arms),
                timestampedMethodWindows=method_windows,
                limitation='Monotonic windows and arrival conservation audited; descriptive observations only, no SLO approval. Legacy wall-only evidence is unsupported.')


def numeric(v):
    return type(v) in (int,float) and math.isfinite(v)


def valid_interval(a,b):
    return numeric(a) and numeric(b) and a < b


def clock_problems(clock):
    if not isinstance(clock,dict):
        return ['missing clock provenance; legacy records are not monotonic evidence']
    anchor=clock.get('anchor',{})
    if not isinstance(anchor,dict):
        return ['unsupported/missing clock anchor']
    system=clock.get('system',{})
    supported_system=isinstance(system,dict) and (
        (bool(re.fullmatch(r'[0-9a-fA-F-]{36}',str(system.get('boot_id','')))) and bool(re.fullmatch(r'time:\[[0-9]+\]',str(system.get('time_namespace','')))))
        or (system.get('platform')=='darwin' and isinstance(system.get('host'),str) and bool(system['host'])))
    if (clock.get('format') != 'lab-monotonic-v2' or clock.get('clock') != 'time.monotonic'
            or clock.get('unit') != 'seconds' or not isinstance(clock.get('domain_id'),str)
            or not clock['domain_id'] or not supported_system or not clock.get('implementation')
            or clock.get('monotonic') is not True or clock.get('adjustable') is not False
            or any(not numeric(anchor.get(k)) for k in ('monotonic','wall_before','wall_after'))):
        return ['unsupported/incomplete clock provenance']
    return []


def same_clock(other, clock):
    errors=clock_problems(other)+clock_problems(clock)
    if not errors and any(other[k] != clock[k] for k in ('domain_id','system','implementation','clock','unit','format')):
        errors.append('foreign clock domain')
    return errors


def boundary_problems(bounds,a,b,clock):
    if not valid_interval(a,b):
        return ['invalid/empty observation interval']
    if (not isinstance(bounds,dict) or bounds.get('begin') != a or bounds.get('end') != b
            or not isinstance(clock,dict) or bounds.get('clock_domain') != clock.get('domain_id')):
        return ['missing/foreign/inconsistent saved window boundary']
    return []


def check_arrivals(rows, account, clock):
    """Recover every expected worker/sequence from the saved open-loop schedule."""
    errors=same_clock(account.get('clock'),clock)
    if errors:
        return errors
    origin,cutoff,step=account.get('origin'),account.get('cutoff'),account.get('interval')
    plans=account.get('schedules')
    if not valid_interval(origin,cutoff) or not numeric(step) or step<=0 or not isinstance(plans,list) or not plans:
        return ['missing/invalid arrival schedule boundaries']
    expected={}
    workers=set()
    for plan in plans:
        idx,kind,first=plan.get('worker'),plan.get('kind'),plan.get('first_at')
        if type(idx) is not int or idx in workers or kind not in ('read','write') or not numeric(first) or not origin<=first<=origin+step:
            return ['invalid/duplicate worker schedule']
        workers.add(idx)
        seq=0
        while first+seq*step < cutoff:
            expected[idx,seq]=(first+seq*step,kind)
            seq+=1
            if len(expected)>10_000_000:
                return ['arrival schedule exceeds audit bound; unsupported']
    seen=set()
    for r in rows:
        if not isinstance(r,(list,tuple)) or len(r)!=11:
            errors.append('legacy/invalid raw tuple; v2 requires eleven columns')
            continue
        sched,kind,latency,state,dispatch,execution,idx,seq,started,observed,domain=r
        if type(idx) is not int or type(seq) is not int:
            errors.append('invalid arrival identity');continue
        key=(idx,seq)
        if key in seen or key not in expected:
            errors.append('duplicate/foreign arrival identity');continue
        seen.add(key)
        if (domain!=clock['domain_id'] or (sched,kind)!=expected[key] or state not in ('ok','error','stuck','queued')
                or not numeric(observed) or observed<sched or not numeric(latency)
                or not math.isclose(latency,(observed-sched)*1000,abs_tol=1e-6)):
            errors.append('foreign/inconsistent arrival coordinate or elapsed time');continue
        if state=='queued':
            if started is not None or dispatch is not None or execution is not None or observed!=cutoff:
                errors.append('invalid queued/censored arrival')
        elif (not numeric(started) or not sched<=started<=observed or started>=cutoff
              or not numeric(dispatch) or not math.isclose(dispatch,(started-sched)*1000,abs_tol=1e-6)):
            errors.append('invalid dispatch coordinate')
        elif state=='stuck':
            if execution is not None or observed<cutoff:
                errors.append('invalid inflight censoring')
        elif not numeric(execution) or not math.isclose(execution,(observed-started)*1000,abs_tol=1e-6):
            errors.append('invalid completion elapsed time')
    if any(r[3]=='queued' and r[8] is not None for r in rows if len(r)==11):
        errors.append('queued arrival has dispatch')
    if seen!=set(expected) or len(rows)!=len(expected):
        errors.append('dropped/additional scheduled arrivals')
    states=Counter(r[3] for r in rows if isinstance(r,(list,tuple)) and len(r)>3)
    derived=dict(scheduled=len(expected),started=states['ok']+states['error']+states['stuck'],
                 completed=states['ok'],failed=states['error'],inflight=states['stuck'],unstarted=states['queued'])
    if account.get('consistent') is not True or any(account.get(k)!=v for k,v in derived.items()):
        errors.append('arrival schedule/accounting differs')
    if not numeric(account.get('arrival_window_seconds')) or not math.isclose(account['arrival_window_seconds'],cutoff-origin,abs_tol=1e-9):
        errors.append('arrival duration differs')
    return errors


def check_server_window(rows,report,a,b,clock):
    errors=clock_problems(clock)+boundary_problems(report.get('window'),a,b,clock)
    if not valid_interval(a,b) or clock_problems(clock):
        return errors
    previous=None
    for r in rows:
        t,end=r.get('t'),r.get('finished_at')
        if (r.get('clock_domain')!=clock['domain_id'] or not numeric(t) or not numeric(end) or end<t
                or (previous is not None and t<previous)):
            errors.append('foreign/inverted/unordered server sample');break
        previous=t
    selected=[r for r in rows if numeric(r.get('t')) and a<=r['t']<b and 'hll' in r]
    if report.get('samples')!=len(selected):
        errors.append('server sample selection differs')
    if selected:
        first,last=selected[0],selected[-1]
        span=max(last['t']-first['t'],1)
        delta=lambda k:last.get(k,0)-first.get(k,0)
        peak=lambda k:max((r[k] for r in selected if r.get(k) is not None),default=None)
        derived=dict(seconds=round(span,1),table_data_lock_samples=sum(r.get('row_locks_seen') is not None for r in selected),
                     rows_read_per_s=round(delta('Innodb_rows_read')/span),
                     bp_disk_reads_per_s=round(delta('Innodb_buffer_pool_reads')/span),
                     bp_miss_pct=round(100*delta('Innodb_buffer_pool_reads')/delta('Innodb_buffer_pool_read_requests'),3) if delta('Innodb_buffer_pool_read_requests') else None,
                     row_lock_time_ms=round(delta('Innodb_row_lock_time')),row_lock_waits=round(delta('Innodb_row_lock_waits')),
                     table_locks_waited=round(delta('Table_locks_waited')),hll_start=first['hll'],hll_end=last['hll'])
        fields={'peak_threads_running':'Threads_running','peak_row_lock_current_waits':'Innodb_row_lock_current_waits',
                'hll_peak':'hll','peak_trx_age_s':'max_trx_age_s','peak_mdl_waiters':'mdl_waiters',
                'peak_table_mdl_exclusive':'table_mdl_exclusive','peak_global_read_lock':'global_read_lock',
                'peak_row_locks_seen':'row_locks_seen','peak_table_level_locks':'table_is_locks','peak_replica_lag_s':'replica_lag_s'}
        derived.update({k:peak(v) for k,v in fields.items()})
        errors.extend('server '+k+' differs' for k,v in derived.items() if report.get(k)!=v)
    return errors


def check_recorded_windows(data):
    """Impact/measured_run records use the same public raw-window audit."""
    clock=data.get('clock')
    raw=data.get('raw_service_samples',[])
    errors=check_arrivals(raw,data.get('arrival_accounting',{}),clock)
    if 'clock_end' in data:
        errors.extend(same_clock(data['clock_end'],clock))
    windows=data.get('windows',{})
    for label in ('baseline','during','recovery'):
        bounds=windows.get(label,{})
        a,b=bounds.get('begin'),bounds.get('end')
        errors.extend(boundary_problems(bounds,a,b,clock))
        for kind in ('read','write'):
            errors.extend((label,kind,e) for e in check_window(raw,data.get('latency',{}).get(label,{}).get(kind,{}),a,b,kind,clock))
        errors.extend((label,'server',e) for e in check_server_window(data.get('server_samples',[]),data.get('server',{}).get(label,{}),a,b,clock))
    bounds=data.get('actual_method_window',{})
    a,b=bounds.get('begin'),bounds.get('end')
    errors.extend(boundary_problems(bounds,a,b,clock))
    for kind in ('read','write'):
        errors.extend(('actual',kind,e) for e in check_window(raw,data.get('actual_method_service',{}).get(kind,{}),a,b,kind,clock))
    errors.extend(('actual','server',e) for e in check_server_window(data.get('server_samples',[]),data.get('actual_method_server',{}),a,b,clock))
    return errors


def check_stream_clock(stream, clock, row_format):
    errors=same_clock(stream.get('clock'),clock)
    if stream.get('row_format')!=row_format:
        errors.append('unsupported/missing raw stream row format')
    return errors
