"""Source health API and immutable incident preservation; no upstream fetch or model call."""

from contextlib import closing
import hashlib
import json
import time

import calendar_release as calendar


def state(path, cutoff):
    with closing(calendar.readonly(path)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {'trend_health_sync', 'trend_health_cycles', 'trend_source_observations', 'trend_source_incidents'}
        if not (required & tables):
            return {'present': False, 'incidents': {}, 'read_success': 0}
        calendar.require(required <= tables, 'Source health schema is incomplete')
        incidents = {row['id']: hashlib.sha256(json.dumps(dict(row), sort_keys=True).encode()).hexdigest()
                     for row in conn.execute('SELECT * FROM trend_source_incidents WHERE at>=?', (cutoff,))}
        orphan = sum(conn.execute(f'''SELECT COUNT(*) FROM {table} s LEFT JOIN trend_health_cycles c
                    ON c.day=s.day AND c.record_id=s.record_id WHERE c.day IS NULL''').fetchone()[0]
                     for table in ('trend_source_incidents', 'trend_source_observations'))
        row = conn.execute('SELECT succeeded FROM trend_health_sync WHERE id=1').fetchone()
        return {'present': True, 'incidents': incidents, 'orphans': orphan, 'read_success': row[0] if row else 0}


def verify(before, after):
    calendar.require(after.get('present') and after.get('orphans') == 0, 'Source health schema/linkage invalid')
    calendar.require(all(after['incidents'].get(key) == value for key, value in before['incidents'].items()),
                     'Recorded source incidents changed or disappeared')
    calendar.require(after['read_success'] >= before['read_success'], 'Source health read progress regressed')
    return {'status': 'passed', 'first_install': not before['present'], 'preserved_incidents': len(before['incidents'])}


def probe(ready=True):
    import news_release
    with news_release.api_client() as get:
        for endpoint in ('news/source-health', 'news/source-failures'):
            calendar.require(get(endpoint, False)[0] == 401, 'Anonymous source health access was not rejected')
        code, result = get('news/source-health')
        calendar.require(code == 200 and result.get('collection_enabled') and result.get('trendradar_enabled')
                         and isinstance(result.get('sources'), list) and isinstance(result.get('warnings'), list),
                         'Source health API invalid')
        states = {'healthy', 'failed', 'missing', 'unknown', 'stale'}
        calendar.require(all(row.get('id') and row.get('state') in states for row in result['sources'])
                         and len({row['id'] for row in result['sources']}) == len(result['sources']),
                         'Source health identity/status invalid')
        if ready:
            calendar.require(result['sources'] and not result.get('read_error') and not result.get('read_stale')
                             and 0 <= result['server_time']-result.get('read_success', 0) <= 180
                             and 0 < result.get('latest_cycle', 0) <= result['server_time'],
                             'Source health collection is not ready')
        code, history = get('news/source-failures')
        calendar.require(code == 200 and isinstance(history.get('items'), list) and len(history['items']) <= 30
                         and isinstance(history.get('next'), str), 'Source incident history API invalid')
        return {'status': 'passed', 'ready': ready, 'sources': len(result['sources']),
                'states': {state: sum(row['state'] == state for row in result['sources']) for state in states},
                'read_success': result.get('read_success', 0), 'latest_cycle': result.get('latest_cycle', 0),
                'history_page_items': len(history['items']), 'anonymous_status': 401,
                'all_upstream_sources_healthy': all(row['state'] == 'healthy' for row in result['sources']) if result['sources'] else False}


def checks_passed(report):
    return (all(report.get(key, {}).get('status') == 'passed' for key in
                ('source_health_migration', 'source_health_restart', 'source_health_api'))
            and report.get('source_health_api', {}).get('ready') is True)


def retention_cutoff():
    # Exclude rows due for normal retention during the bounded restart check.
    return int(time.time())-30*86400+300
