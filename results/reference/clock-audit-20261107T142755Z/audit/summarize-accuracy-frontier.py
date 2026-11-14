"""Independent owner aggregation of a COMPLETE measured frontier; never a benchmark.

No bench imports, database access or synthetic measurements. Run after measurement
has ended; incomplete files are rejected. Median Pareto labels are descriptive.
"""
from collections import Counter
from decimal import Decimal
from pathlib import Path
import argparse
import hashlib
import json
import runpy
import statistics


def span(values):
    return {'min': min(values), 'median': statistics.median(values), 'max': max(values)}


def aggregate(data):
    output = {}
    for name, case in data['cases'].items():
        targets, candidates = {}, []
        for target in (Decimal('.03'), Decimal('.05'), Decimal('.10')):
            runs = [r for r in case['runs'] if Decimal(str(r['target_relative_error'])) == target]
            exact = [r['comparison_count'] for r in runs]
            eligible = [r for r in runs if r['status'] == 'target_reached'
                        and r['accuracyStatus'] == 'target_reached'
                        and r['budgetStatus'] == 'within_budget'
                        and r['cleanupStatus'] == 'rolled_back']
            realized, interval_bounds = [], []
            for r in runs:
                if 'estimate' not in r:
                    continue
                estimate = Decimal(r['estimate'])
                error = abs(estimate - case['truth']) * 100 / case['truth']
                # Recompute from the actual COUNT oracle, rather than copy the report.
                assert error == Decimal(r['realized_abs_error_percent'])
                realized.append(float(error))
                if r['lower'] > 0:
                    margin = max(abs(estimate-r['lower']), abs(Decimal(r['upper'])-estimate))
                    interval_bounds.append(float(margin * 100 / r['lower']))
            item = {'attempts': len(runs), 'eligibleObservedRuns': len(eligible),
                    'statuses': dict(Counter(r['status'] for r in runs)),
                    'sampleSecondsAllOutcomes': span([r['seconds'] for r in runs]),
                    'sampleSecondsEligibleOnly': span([r['seconds'] for r in eligible]) if eligible else None,
                    'pairedExactCountSeconds': span([r['seconds'] for r in exact]),
                    'realizedAbsErrorPercentWhenEstimateExists': span(realized) if realized else None,
                    'reportedRelativeIntervalBoundPercent': span(interval_bounds) if interval_bounds else None,
                    'intervalCoversTruth': sum(r.get('interval_covers_truth') is True for r in runs),
                    'estimateAbsent': sum('estimate' not in r for r in runs),
                    'completedDraws': span([r['completed_draws'] for r in runs]),
                    'lastValidCheckpointDraws': span([r['last_valid_checkpoint_draws'] for r in runs]),
                    'sampleInnodbDataReadBytesAllOutcomes': span([r['counters']['Innodb_data_read'] for r in runs]),
                    'pairedExactCountInnodbDataReadBytes': span([r['counters']['Innodb_data_read'] for r in exact])}
            targets[str(target)] = item
            if len(eligible) == len(runs):
                candidates.append({'method': f'sample_target_{target}', 'targetPercent': float(target*100),
                                   'medianSeconds': item['sampleSecondsEligibleOnly']['median'],
                                   'medianInnodbDataReadBytes': item['sampleInnodbDataReadBytesAllOutcomes']['median']})
        all_exact = [r['comparison_count'] for r in case['runs']]
        exact_summary = {'seconds': span([r['seconds'] for r in all_exact]),
                         'innodbDataReadBytes': span([r['counters']['Innodb_data_read'] for r in all_exact])}
        candidates.append({'method': 'exact_count', 'targetPercent': 0,
                           'medianSeconds': exact_summary['seconds']['median'],
                           'medianInnodbDataReadBytes': exact_summary['innodbDataReadBytes']['median']})
        dimensions = ('targetPercent', 'medianSeconds', 'medianInnodbDataReadBytes')
        for candidate in candidates:
            candidate['descriptivelyDominatedBy'] = [other['method'] for other in candidates
                if all(other[k] <= candidate[k] for k in dimensions)
                and any(other[k] < candidate[k] for k in dimensions)]
        output[name] = {'truth': case['truth'], 'targets': targets, 'allPairedExactCounts': exact_summary,
                        'descriptiveCandidates': candidates,
                        'descriptiveMedianFrontier': [r['method'] for r in candidates if not r['descriptivelyDominatedBy']]}
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path)
    args = parser.parse_args()
    raw = args.result.read_bytes()
    data = json.loads(raw)
    checker = runpy.run_path(str(Path(__file__).with_name('audit-extra-evidence.py')))
    checked = checker['check_frontier'](data)
    assert not checked['problems'], checked['problems']
    report = {'source': str(args.result), 'sourceSha256': hashlib.sha256(raw).hexdigest(),
              'meaning': 'Owner-derived measured summary only; no service SLO or universal fastest-method claim.',
              'limitations': [
                  'Target labels are nominal per-invocation interval goals, not realized error guarantees.',
                  'Five fixed seeds do not establish production coverage or a joint95% guarantee.',
                  'Pareto labels compare observed medians and declared targets, without statistical significance.',
                  'Unknown host/VM cache and order carryover remain; counters include background server activity.',
                  'Failed/unfinished samples are described but excluded from candidate ranking.',
                  'Exact zero error refers to the static fixture snapshot population; freshness is separate.',
                  'Timing follows the implementation: sampling includes checks and rollback; COUNT times its query.',
              ], 'fixtures': aggregate(data)}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
