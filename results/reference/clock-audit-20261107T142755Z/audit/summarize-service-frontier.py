"""Aggregate completed01d only: descriptive costs/latencies, never SLO approval.

Standard library only, no benchmark imports, DB access or raw decompression.
"""
from collections import Counter
from pathlib import Path
import argparse
import hashlib
import json
import runpy
import statistics


def span(values):
    values = [v for v in values if v is not None]
    return {'min': min(values), 'median': statistics.median(values), 'max': max(values)} if values else None


def aggregate(data):
    groups = {}
    for indexed in (False,True):
        for mix in ('nonindexed','indexed'):
            group = {}
            for method in ('control','count','sample3','sample10'):
                rows = [r for r in data['arms'] if (r['secondary_index'],r['write_mix'],r['method']) == (indexed,mix,method)]
                eligible = [r for r in rows if (r.get('count_status')=='complete' if method=='count' else
                            r.get('sampling',{}).get('status')=='target_reached')]
                timing = 'statement_seconds' if method=='count' else 'method_seconds'
                metrics = {}
                for kind in ('read','write'):
                    windows = [r['trial']['service'][kind] for r in rows]
                    metrics[kind] = {'scheduled': [w['scheduled'] for w in windows],
                                     'completed': [w['completed'] for w in windows],
                                     'failed': [w['failed'] for w in windows],
                                     'unfinished': [w['inflight_at_end']+w['queued_at_end'] for w in windows],
                                     'latencyStatus': dict(Counter(w['latency_status'] for w in windows)),
                                     'p50Ms': span([w['p50_ms'] for w in windows]),
                                     'p99Ms': span([w['p99_ms'] for w in windows]),
                                     'maxMs': span([w['max_ms'] for w in windows])}
                group[method] = {'observations': len(rows), 'eligibleMethodCompletions': None if method=='control' else len(eligible),
                                 'outcomes': dict(Counter(r.get('count_status','control') for r in rows)),
                                 'methodTimeField': timing if method!='control' else None,
                                 'methodSecondsAllOutcomes': span([r.get(timing) for r in rows]),
                                 'methodSecondsEligibleOnly': span([r.get(timing) for r in eligible]),
                                 'samplingAccuracyStatus': dict(Counter(r.get('sampling',{}).get('accuracyStatus') for r in rows if 'sampling' in r)),
                                 'samplingBudgetStatus': dict(Counter(r.get('sampling',{}).get('budgetStatus') for r in rows if 'sampling' in r)),
                                 'trialSeconds': span([r['trial']['seconds'] for r in rows]),
                                 'trialService': metrics,
                                 'trialInnodbDataReadBytes': span([r['trial']['counter_delta'].get('Innodb_data_read') for r in rows])}
            groups[f'secondary_index={indexed},write_mix={mix}'] = group
    return groups


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result',type=Path)
    args = parser.parse_args()
    raw = args.result.read_bytes()
    data = json.loads(raw)
    check = runpy.run_path(str(Path(__file__).with_name('audit-extra-evidence.py')))['check_service'](data)
    assert not check['problems'],check['problems']
    report = {'source': str(args.result), 'sourceSha256': hashlib.sha256(raw).hexdigest(),
              'meaning': 'Completed48-arm descriptive summary, not SLO/production/global-optimum approval.',
              'writeMixMeaning': {'nonindexed': 'UPDATE v, a non-key column',
                                  'indexed': 'UPDATE k; secondary-index maintenance applies only when that index exists'},
              'groups': aggregate(data), 'limitations': [
                  'Three observations per index/write-shape/method; quantiles and their min/median/max are descriptive, not confidence intervals.',
                  'Time-boxed COUNT outcomes are censored query durations, not completed COUNT speed.',
                  'COUNT times its SQL statement; sampling callback includes its checks and rollback. Connection setup and owned-session confirmation are outside these method intervals.',
                  'COUNT uses a server statement timer; sampling has a cooperative aggregate budget. These are not identical hard end-to-end deadlines.',
                  'Trial windows include remaining work/cleanup and can exceed the nominal3s. Use actual durations, arrival counts and unfinished markers.',
                  'Unfinished-response quantiles are lower bounds; absent quantiles are unobserved, never zero.',
                  'Latency cohorts contain arrivals scheduled in the trial and observed through workload recording/cleanup. Saved unfinished markers are not a point-in-time count at the trial endpoint.',
                  'InnoDB read counters are global server deltas, not host physical I/O or method-only attribution.',
                  'Cache/order/carryover/host activity are uncontrolled. UPDATE shapes and a750000-row synthetic100-column TDE fixture do not cover arbitrary production traffic.',
                  'There is no5% service arm; static105-case comparison supplies the3/5/10% evidence.'
              ]}
    print(json.dumps(report,ensure_ascii=False,indent=2))
