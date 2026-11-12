"""Read-only news notification migration/API checks; never sends push or calls a model."""

from contextlib import closing
import json
import time

import calendar_release as calendar


def state(path):
    with closing(calendar.readonly(path)) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        ai_row = conn.execute('SELECT enabled,automatic FROM ai_settings WHERE singleton=1').fetchone() if 'ai_settings' in tables else None
        ai = dict(ai_row) if ai_row else None
        if 'news_push_settings' not in tables:
            return {'present': False, 'ai': ai}
        required = {'news_push_settings', 'news_push_events', 'news_push_links', 'news_push_aliases', 'news_push_jobs'}
        calendar.require(required <= tables, 'News push migration has missing tables')
        row = conn.execute('SELECT payload,cursor,enabled_at FROM news_push_settings WHERE id=1').fetchone()
        settings = json.loads(row['payload']) if row else {'enabled': False, 'devices': [], 'categories': [], 'industries': []}
        pending = conn.execute("SELECT COUNT(*) FROM deliveries WHERE scenario='news' AND status IN ('queued','sending')").fetchone()[0]
        orphan = conn.execute("SELECT COUNT(*) FROM deliveries d LEFT JOIN news_push_jobs j ON j.delivery_id=d.id WHERE d.scenario='news' AND j.delivery_id IS NULL").fetchone()[0]
        return {'present': True, 'settings': settings, 'cursor': row['cursor'] if row else 0,
                'enabled_at': row['enabled_at'] if row else 0, 'pending': pending, 'orphans': orphan, 'ai': ai}


def verify(before, after):
    calendar.require(after.get('present') and after.get('orphans') == 0, 'News push schema or delivery linkage invalid')
    if before.get('ai') is not None:
        calendar.require(before['ai'] == after.get('ai'), 'AI user switches changed during news push deployment')
    else:
        calendar.require(after.get('ai') in (None, {'enabled': 0, 'automatic': 0}), 'AI was enabled by news push deployment')
    if before.get('present'):
        calendar.require(before['settings'] == after['settings'] and after['cursor'] >= before['cursor'], 'News push preferences or cursor changed across migration/restart')
    else:
        calendar.require(not after['settings']['enabled'] and after['pending'] == 0, 'First news push installation must remain disabled')
    return {'status': 'passed', 'first_install': not before.get('present'), 'enabled': after['settings']['enabled'],
            'ai_settings_preserved': True, 'cursor': after['cursor'], 'device_delivery_tested': False}


def probe():
    import news_release
    with news_release.api_client() as get:
        calendar.require(get('news-push', False)[0] == 401, 'Anonymous news push settings exposed')
        code, result = get('news-push')
        calendar.require(code == 200 and isinstance(result.get('settings', {}).get('enabled'), bool)
                         and len(result.get('categories', [])) == 10 and len(result.get('industries', [])) == 12
                         and 0 <= result.get('backlog', -1) and len(result.get('history', [])) <= 50, 'News push API invalid')
        calendar.require(all('receipt_token' not in row and 'payload' not in row for row in result['history']), 'News push API exposes credentials')
        with closing(calendar.readonly(calendar.DATA/'finevent.db')) as conn:
            overdue = conn.execute("SELECT COUNT(*) FROM deliveries WHERE scenario='news' AND status IN ('queued','sending') AND expires<?", (int(time.time())-60,)).fetchone()[0]
        calendar.require(overdue == 0, 'News push expiration worker is stalled')
        return {'status': 'passed', 'enabled': result['settings']['enabled'], 'backlog': result['backlog'],
                'history_count': len(result['history']), 'anonymous_status': 401, 'device_delivery_tested': False}


def checks_passed(report):
    return all(report.get(key, {}).get('status') == 'passed' for key in
               ('news_push_migration', 'news_push_restart', 'news_push_api'))
