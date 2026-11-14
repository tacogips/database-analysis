"""Lab-only outcome classification and owned-connection termination evidence.

No elapsed-time inference of a timer. Cleanup does not turn a failed query into
an expected observation. Helpers accept the lab connection/query explicitly.
"""
import time


def classify_outcome(rec):
    status = rec.get('status', 'not_started')
    if status == 'not_started':
        return 'not_started'
    if rec.get('cleanup', {}).get('confirmed') is not True:
        return 'cleanup_failed'
    if status == 'complete':
        return 'completed'
    if status in ('stopped:max_seconds', 'stopped:max_rows'):
        return 'confirmed_ceiling'
    if status in ('error_3024', 'error_1317'):
        if rec.get('timer_counter_before') is not None and rec.get('timer_counter_after') is not None:
            if rec['timer_counter_after'] > rec['timer_counter_before']:
                return 'confirmed_ceiling'
        return 'unknown_interrupt'
    return 'unexpected_failure'


def confirm_connections(admin, ids, query_fn, timeout=10):
    """Check exact IDs we created, terminate remaining sessions, confirm no trx.

    Closing the client is not the proof. No inspection/KILL of unowned sessions.
    Missing observation or failed KILL fails closed, even if a later check clears.
    """
    ids = sorted(set(int(i) for i in ids))
    out = {'connection_ids': ids, 'confirmed': False, 'kill_attempts': [], 'observations': []}
    if not ids:
        out.update(confirmed=True, status='no_connections_created')
        return out
    marks = ','.join(['%s'] * len(ids))
    deadline = time.monotonic() + timeout
    try:
        while True:
            sessions = query_fn(admin, f'SELECT PROCESSLIST_ID FROM performance_schema.threads WHERE PROCESSLIST_ID IN ({marks})', tuple(ids))
            transactions = query_fn(admin, f'SELECT trx_mysql_thread_id FROM information_schema.INNODB_TRX WHERE trx_mysql_thread_id IN ({marks})', tuple(ids))
            out['observations'].append({'sessions': [int(r[0]) for r in sessions], 'transactions': [int(r[0]) for r in transactions]})
            if not sessions and not transactions:
                out.update(confirmed=True, status='confirmed_terminated')
                return out
            already = {r['id'] for r in out['kill_attempts']}
            for pid in sorted({int(r[0]) for r in sessions + transactions} - already):
                attempt = {'id': pid}
                out['kill_attempts'].append(attempt)
                try:
                    query_fn(admin, f'KILL CONNECTION {pid}')
                    attempt['status'] = 'sent'
                except Exception as error:
                    # 1094 is a race with natural completion; still verify below.
                    attempt['status'] = 'already_gone' if error.args and error.args[0] == 1094 else 'failed'
                    attempt['error'] = repr(error)
                    if attempt['status'] == 'failed':
                        raise
            if time.monotonic() >= deadline:
                out['status'] = 'termination_unconfirmed'
                return out
            time.sleep(.1)
    except Exception as error:
        out.update(status='observation_or_kill_failed', error=repr(error))
        return out


def close_owned(admin, connection, pid, query_fn):
    out = {}
    try:
        query_fn(connection, 'ROLLBACK')
        out['rollback'] = 'ok'
    except Exception as error:
        out.update(rollback='failed', rollback_error=repr(error))
    try:
        connection.close()
        out['close'] = 'ok'
    except Exception as error:
        out.update(close='failed', close_error=repr(error))
    out.update(confirm_connections(admin, [pid], query_fn))
    return out


def opportunity_schedule(group, block):
    """Two rounds per method group; no slow arm can consume another script."""
    if group == 'splits':
        names = ['split_k', 'split_pk']
    elif group == 'statements':
        names = ['statement_k', 'statement_pk']
    else:
        raise ValueError(group)
    if block not in (0, 1):
        raise ValueError(block)
    return [{'rep': rep, 'arm': name, 'outcome': 'not_started'}
            for rep in range(2 * block, 2 * block + 2)
            for name in (names if rep % 2 == 0 else names[::-1])]


def scope_complete(opportunities):
    return bool(opportunities) and all(r.get('outcome') in ('completed', 'confirmed_ceiling') for r in opportunities)
