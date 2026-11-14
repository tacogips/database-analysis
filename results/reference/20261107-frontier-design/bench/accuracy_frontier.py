"""Compose-only accuracy/cost comparisons; opaque synthetic TDE fixtures.

Targets and look schedules are fixed before observing a fixture. Each invocation
uses the same 5% family error budget; this is not a joint guarantee for all runs.
"""
from collections import Counter
from decimal import Decimal
import hashlib
import time

from lablib import connect, query, save, pin, METHOD_CPU
from samplinglib import sample

PROTOCOLS = (
    (.03, (512, 2048, 8192)),
    (.05, (320, 1280, 5120, 8192)),
    (.10, (160, 640, 2560, 8192)),
)
SEEDS = (1, 7, 42, 317, 991)
FIXTURES = (
    ('dense', 'n+{off}', 32, 250000),
    ('gaps', '(n+{off})*10000', 32, 250000),
    ('islands', 'CASE WHEN n+{off}<=125000 THEN n+{off} ELSE n+{off}+10000000000 END', 32, 250000),
    ('negative', 'n+{off}-125001', 32, 250000),
    ('wide', 'n+{off}', 1000, 3000000),
    ('moderate_gaps', 'n+{off}', 1000, 3000000),
    ('uneven_density', 'n+{off}', 1000, 3000000),
)
COUNTERS = ('Innodb_data_read', 'Innodb_buffer_pool_reads', 'Innodb_buffer_pool_read_ahead')
UNEXPECTED = ('query_failed', 'cleanup_failed', 'inapplicable')


def source_hashes():
    return {name: hashlib.sha256(open('/work/bench/' + name, 'rb').read()).hexdigest()
            for name in ('accuracy_frontier.py', 'samplinglib.py', 'sampling_bounds.py', 'lablib.py')}


def counters(c):
    return {k: int(v) for k, v in query(c, 'SELECT VARIABLE_NAME,VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME IN (' + ','.join('%s' for _ in COUNTERS) + ')', COUNTERS)}


def delta(a, b):
    return {k: b[k] - a[k] for k in a}


def exact(q, observer, table, truth):
    query(q, 'SET SESSION max_execution_time=30000')
    before = counters(observer)
    start = time.monotonic()
    actual = query(q, f'SELECT COUNT(*) FROM {table}')[0][0]
    seconds = time.monotonic() - start
    assert actual == truth, (table, actual, truth)
    return {'status': 'exact', 'rows': actual, 'seconds': round(seconds, 4), 'counters': delta(before, counters(observer))}


def static_frontier(output_id, host, version):
    pin(METHOD_CPU)
    admin = connect(host, db='mysql')
    query(admin, 'CREATE DATABASE IF NOT EXISTS bench')
    admin.close()
    c = connect(host, read_timeout=900)
    query(c, 'SET SESSION sql_log_bin=0')
    query(c, 'DROP DATABASE IF EXISTS req_frontier')
    query(c, 'CREATE DATABASE req_frontier')
    query(c, 'SET SESSION cte_max_recursion_depth=100001')
    query(c, 'CREATE TABLE bench.seq_frontier (n INT PRIMARY KEY)')
    query(c, 'INSERT INTO bench.seq_frontier WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM s WHERE n<100000) SELECT n FROM s')
    out = {'id': output_id, 'version': version, 'encryption': 'InnoDB TDE',
           'buffer_pool_bytes': query(c, 'SELECT @@innodb_buffer_pool_size')[0][0],
           'innodb_parallel_read_threads': 1,
           'source_sha256': source_hashes(), 'protocols': [{'target': t, 'looks': list(ls)} for t, ls in PROTOCOLS],
           'alpha_per_invocation': .05, 'cases': {}, 'complete': False,
           'limitations': ['No service workload; host/VM cache uncontrolled. Truth count and previous arms carry cache forward.',
                           'Alternating order reduces one fixed-order bias; it does not establish equal physical cache states.',
                           'Fixed seeds reproduce synthetic fixtures; finite observed coverage is not a production confidence guarantee.',
                           'Target-specific first looks are predeclared. Comparison includes this protocol choice, not only a label change.',
                           'Each sample invocation has its own family confidence; no joint 95% statement across all fixtures/retries.',
                           'Same single-thread setting for exact COUNT and sample queries; connection setup excluded from method timing.']}
    save(output_id, out)
    try:
        for name, expression, pad, rows in FIXTURES:
            table = f'req_frontier.{name}'
            query(c, f"CREATE TABLE {table} (id BIGINT PRIMARY KEY,pad VARBINARY({pad}) NOT NULL) ENCRYPTION='Y'")
            inserted = 0
            for off in range(0, rows, 25000):
                predicate = 'n<=25000'
                if name == 'moderate_gaps':
                    predicate += f' AND MOD(CRC32(n+{off}),5)<>0'
                if name == 'uneven_density':
                    predicate += f' AND (n+{off}<=1500000 OR MOD(CRC32(n+{off}),2)=0)'
                query(c, f"INSERT INTO {table} SELECT {expression.format(off=off)},REPEAT('x',{pad}) FROM bench.seq_frontier WHERE {predicate}")
                inserted += query(c, 'SELECT ROW_COUNT()')[0][0]
            query(c, f'ANALYZE TABLE {table}')
            query(c, 'SET SESSION information_schema_stats_expiry=0')
            q = connect(host, read_timeout=120)
            query(q, 'SET SESSION innodb_parallel_read_threads=1')
            item = {'truth': inserted, 'initial_truth_check': exact(q, c, table, inserted),
                    'metadata': [list(r) for r in query(c, "SELECT TABLE_ROWS,DATA_LENGTH,INDEX_LENGTH,CREATE_OPTIONS FROM information_schema.TABLES WHERE TABLE_SCHEMA='req_frontier' AND TABLE_NAME=%s", (name,))], 'runs': []}
            out['cases'][name] = item
            save(output_id, out)
            try:
                for repeat, seed in enumerate(SEEDS):
                    rotation = repeat % len(PROTOCOLS)
                    ordered = PROTOCOLS[rotation:] + PROTOCOLS[:rotation]
                    for position, (target, looks) in enumerate(ordered):
                        first_count = (repeat + position) % 2 == 0
                        comparison = exact(q, c, table, inserted) if first_count else None
                        before = counters(c)
                        result = sample(q, table, seed, looks=looks, target=target, max_seconds=60)
                        result['counters'] = delta(before, counters(c))
                        if 'estimate' in result:
                            result['realized_abs_error_percent'] = str(abs(Decimal(result['estimate']) - inserted) * 100 / inserted)
                            result['interval_covers_truth'] = result['lower'] <= inserted <= result['upper']
                        result['comparison_count'] = comparison if comparison is not None else exact(q, c, table, inserted)
                        result.update(repeat=repeat, order='count_then_sample' if first_count else 'sample_then_count')
                        item['runs'].append(result)
                        save(output_id, out)
                        print(name, target, seed, result['status'], result['seconds'], result.get('completed_draws'), flush=True)
                assert len(item['runs']) == len(SEEDS) * len(PROTOCOLS)
            finally:
                q.close()
        out['status_counts'] = dict(Counter(r['status'] for v in out['cases'].values() for r in v['runs']))
        out['unexpected_failures'] = [{'case': n, 'target': r['target_relative_error'], 'seed': r['seed'], 'status': r['status']}
                                      for n, v in out['cases'].items() for r in v['runs'] if r['status'] in UNEXPECTED]
        out['complete'] = True
        save(output_id, out)
    finally:
        try:
            query(c, 'SET SESSION lock_wait_timeout=5')
            query(c, 'DROP DATABASE IF EXISTS req_frontier')
            query(c, 'DROP TABLE IF EXISTS bench.seq_frontier')
            out['fixture_cleanup'] = 'confirmed'
        except Exception:
            out['fixture_cleanup'] = 'failed'
            raise
        finally:
            save(output_id, out)
            c.close()
    if out['unexpected_failures']:
        raise RuntimeError('unexpected sampling failures; inspect saved results')


def wide_service_frontier(output_id, host, version):
    from reqwide import build, arm, ROWS
    pin(METHOD_CPU)
    admin = connect(host, db='mysql')
    query(admin, 'CREATE DATABASE IF NOT EXISTS bench')
    admin.close()
    c = connect(host, read_timeout=600)
    query(c, "UPDATE performance_schema.setup_consumers SET ENABLED='YES' WHERE NAME='events_statements_cpu'")
    out = {'id': output_id, 'version': version, 'columns': 100, 'rows': ROWS, 'encryption': 'InnoDB TDE',
           'buffer_pool_bytes': query(c, 'SELECT @@innodb_buffer_pool_size')[0][0],
           'source_sha256': source_hashes(), 'arms': [], 'complete': False,
           'protocols': {'sample3': dict(target=PROTOCOLS[0][0], looks=PROTOCOLS[0][1]), 'sample10': dict(target=PROTOCOLS[2][0], looks=PROTOCOLS[2][1])},
           'limitations': ['Short paired diagnostics, not an SLO certification; host/VM cache and carryover uncontrolled.',
                           '100 reads/s and100 updates/s on the configured hot set; INSERT/DELETE not covered.',
                           'Each target uses its predeclared look schedule and the same per-call confidence/budget.',
                           '5% service impact not measured here; static frontier includes5%. Synthetic opaque payload; no application decryption.']}
    save(output_id, out)
    try:
        build(c, 100)
        for indexed in (False, True):
            if indexed:
                query(c, 'ALTER TABLE enc ADD KEY k_small(k)')
            query(c, 'ANALYZE TABLE enc')
            for mix in ('nonindexed', 'indexed'):
                for repeat in range(3):
                    methods = ('control', 'count', 'sample3', 'sample10')
                    rotation = repeat % len(methods)
                    for method in methods[rotation:] + methods[:rotation]:
                        target, looks = PROTOCOLS[0] if method == 'sample3' else PROTOCOLS[2]
                        callback = lambda q, t=target, ls=looks, seed=repeat: sample(q, 'bench.enc', seed, looks=ls, target=t, max_seconds=1)
                        result = arm(host, c, 'enc', indexed, mix, 'sample' if method.startswith('sample') else method, sampling_method=callback)
                        result.update(method=method, repeat=repeat)
                        out['arms'].append(result)
                        save(output_id, out)
                        if result.get('sampling', {}).get('status') in UNEXPECTED:
                            raise RuntimeError('unexpected service sampling failure')
                        print(output_id, indexed, mix, repeat, method, result.get('count_status'), flush=True)
        assert len(out['arms']) == 48
        out['complete'] = True
        save(output_id, out)
    finally:
        c.close()
