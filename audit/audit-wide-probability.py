"""Read-only independent audit of completed01a/01b results; never a rollout pass.

No benchmark imports, DB access or decompression. Grid/outcome checks can run
after a requested script ends. Inline cohorts are small enough to check then;
compressed raw-stream audits wait for the all owner to end.
"""
from collections import Counter
from decimal import Decimal
from pathlib import Path
import argparse
import json
import runpy

ROOT = Path(__file__).resolve().parent.parent


def sample_problems(sample, truth):
    bad = []
    status, cleanup = sample.get('status'), sample.get('cleanupStatus')
    ordinary = status in ('target_reached', 'precision_not_reached', 'budget_exhausted')
    timer = status in ('query_failed', 'statement_budget_exhausted') and sample.get('errno') == 3024
    if not (ordinary or timer):
        bad.append('unexpected sampling status/errno')
    if cleanup != 'rolled_back' and not (status == 'budget_exhausted' and cleanup == 'not_open'):
        bad.append('sampling snapshot cleanup unconfirmed')
    if status == 'target_reached' and sample.get('budgetStatus') != 'within_budget':
        bad.append('precision success after budget exhausted')
    if sample.get('target_relative_error') != .03:
        bad.append('required3% target differs')
    if (sample.get('family_alpha') != .05 or sample.get('width') != 200
            or tuple(sample.get('looks', ())) != (512,2048,8192)):
        bad.append('declared confidence/draw protocol differs')
    if 'lower' in sample:
        lo, hi = sample['lower'], sample['upper']
        estimate = Decimal(sample['estimate'])
        if not 0 <= lo <= hi <= sample['domain']:
            bad.append('invalid interval')
        if 'interval_covers_truth' in sample and sample['interval_covers_truth'] != (lo <= truth <= hi):
            bad.append('reported truth coverage differs')
        if status == 'target_reached' or sample.get('accuracyStatus') == 'target_reached':
            if lo <= 0 or max(abs(estimate-lo), abs(Decimal(hi)-estimate)) > Decimal('.03')*lo:
                bad.append('claimed relative bound not met')
    elif status == 'target_reached':
        bad.append('precision claimed without interval')
    return bad


def arm_problems(item, role, truth, service):
    bad = []
    if item.get('errors') or item.get('unexpected_failures'):
        bad.append('recorded unexpected failure')
    if not (role == 'control' and 'method_connection_id' not in item and 'method_cleanup' not in item):
        if item.get('method_cleanup', {}).get('confirmed') is not True:
            bad.append('method cleanup unconfirmed/missing')
    if service:
        for key in ('workload_cleanup', 'sampler_cleanup'):
            if item.get(key, {}).get('confirmed') is not True:
                bad.append(key+' unconfirmed/missing')
        a = item.get('arrival_accounting', {})
        fields = ('scheduled', 'completed', 'failed', 'inflight', 'unstarted')
        if (any(type(a.get(k)) is not int or a[k] < 0 for k in fields) or
                not a.get('consistent') or a['scheduled'] != sum(a[k] for k in fields[1:])):
            bad.append('arrival conservation')
        if a.get('scheduled',0) <= 0:
            bad.append('no service traffic over whole arm')
        raw = item.get('raw_service_samples', [])
        observed = Counter(row[3] for row in raw)
        mapping = {'completed':'ok', 'failed':'error', 'inflight':'stuck', 'unstarted':'queued'}
        if (len(raw) != a.get('scheduled') or
                any(a.get(k) != observed[v] for k,v in mapping.items())):
            bad.append('raw state/accounting mismatch')
    if role == 'count':
        result = item if service else item.get('result', {})
        complete = (result.get('count_status') == 'complete' and result.get('count') == truth) if service else (result.get('status') == 'exact' and result.get('rows') == truth)
        timer = item.get('count_status') == 'error' and item.get('errno') in (1317,3024) and item.get('timer_counter_delta',0) > 0
        if not (complete or timer):
            bad.append('count neither exact nor confirmed timer')
    if role == 'sample':
        sample = item.get('sampling' if service else 'result', {})
        bad.extend(sample_problems(sample, truth))
        if sample.get('max_seconds') != (1 if service else 90):
            bad.append('declared sampling budget differs')
    return bad


def audit(data, columns):
    bad = []
    if (data.get('complete') is not True or data.get('reusable_mandatory_completion') is not True or
            data.get('columns') != columns or data.get('rows') != 750000 or
            data.get('encryption') != 'InnoDB TDE' or data.get('unexpected_sampling_failures')):
        bad.append('metadata/completion/mandatory failure differs')
    expected_static = Counter((index, rep, seed, role) for index in (False,True)
                              for rep,seed in enumerate((1,7,42,317,991)) for role in ('count','sample'))
    expected_service = Counter((index,mix,rep,role) for index in (False,True)
                               for mix in ('nonindexed','indexed') for rep in range(3)
                               for role in ('control','count','sample'))
    static, service = data.get('static', []), data.get('service', [])
    if Counter((r.get('secondary_index'),r.get('repeat'),r.get('seed'),r.get('method')) for r in static) != expected_static:
        bad.append('20static grid differs')
    if Counter((r.get('secondary_index'),r.get('write_mix'),r.get('repeat'),r.get('arm')) for r in service) != expected_service:
        bad.append('36service grid differs')
    for collection, rows in [('static',static),('service',service)]:
        for i,item in enumerate(rows):
            role = item.get('arm' if collection=='service' else 'method')
            problems = arm_problems(item,role,750000,collection=='service')
            if problems:
                bad.append({'collection':collection,'index':i,'problems':problems})
    inline = runpy.run_path(str(ROOT/'audit/audit-arrival-windows.py'))['check_inline_arm_windows'](service)
    if inline['problems']:
        bad.append({'collection':'serviceInlineWindows','problems':inline['problems']})
    return {'meaning':'Completed lab observation audit, not production confidence/SLO/final acceptance.',
            'problems':bad,'staticCases':len(static),'serviceCases':len(service),
            'inlineWindowAudit':inline,
            'staticSamplingStatuses':dict(Counter(r.get('result',{}).get('status') for r in static if r.get('method')=='sample')),
            'serviceSamplingStatuses':dict(Counter(r.get('sampling',{}).get('status') for r in service if r.get('arm')=='sample')),
            'serviceCountStatuses':dict(Counter(r.get('count_status') for r in service if r.get('arm')=='count')),
            'limitations':['Expected precision/budget/censoring observations remain separate from unexpected failures.',
                           'Observed truth/coverage in this dense fixture does not prove general confidence coverage.',
                           'Timing/order/cache and service tails need protocol-specific interpretation, not automatic ranking.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('columns', type=int, choices=(50,100))
    args = parser.parse_args()
    data = json.loads((ROOT/f'results/requested_probability_wide_{args.columns}.json').read_text())
    print(json.dumps(audit(data,args.columns),ensure_ascii=False,indent=2))
