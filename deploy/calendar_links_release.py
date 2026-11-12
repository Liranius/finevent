"""Read-only checks for human-confirmed news/calendar links."""
from contextlib import closing
import hashlib
import json
import time
from datetime import datetime

import calendar_release as calendar


def state(path, cutoff):
    with closing(calendar.readonly(path)) as conn:
        conn.execute('BEGIN')
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_calendar_links'").fetchone():
            return {'present': False, 'links': {}, 'sequence': 0}
        links = {row['id']: hashlib.sha256(json.dumps(dict(row), sort_keys=True).encode()).hexdigest()
                 for row in conn.execute('SELECT l.* FROM news_calendar_links l JOIN news n ON n.id=l.news_id WHERE n.last_seen>=?', (cutoff,))}
        orphan = conn.execute('SELECT COUNT(*) FROM news_calendar_links l LEFT JOIN news n ON n.id=l.news_id LEFT JOIN calendar_events e ON e.id=l.event_id WHERE n.id IS NULL OR e.id IS NULL').fetchone()[0]
        sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='news_calendar_links'").fetchone()
        return {'present': True, 'links': links, 'orphans': orphan, 'sequence': sequence[0] if sequence else 0}


def verify(before, after):
    calendar.require(after.get('present') and after.get('orphans') == 0, 'Calendar link schema or references invalid')
    calendar.require(all(after['links'].get(key) == value for key, value in before['links'].items()),
                     'Confirmed calendar links or evidence changed during migration/restart')
    calendar.require(after['sequence'] >= before['sequence'], 'Calendar link revision sequence regressed')
    return {'status': 'passed', 'first_install': not before['present'], 'preserved_links': len(before['links'])}


def probe():
    import news_release
    with closing(calendar.readonly(calendar.DATA/'finevent.db')) as conn:
        news = conn.execute('SELECT id FROM news ORDER BY last_seen DESC,id DESC LIMIT 1').fetchone()
        event = conn.execute('SELECT id,start_day,end_day FROM calendar_events ORDER BY start_day DESC LIMIT 1').fetchone()
    calendar.require(news and event, 'Existing news and calendar records are required for link API verification')
    day = datetime.now(calendar.ZONE).date().isoformat()
    paths = (f'news/{news[0]}/calendar-links', f'calendar/{event[0]}/news-links',
             f'calendar-link-search?from={day}&to={day}')
    with news_release.api_client() as get:
        for path in paths:
            calendar.require(get(path, False)[0] == 401, 'Anonymous calendar link access allowed')
            code, result = get(path)
            calendar.require(code == 200 and isinstance(result.get('items'), list) and len(result['items']) <= 30,
                             'Calendar link API invalid')
            if path.startswith('calendar-link-search'):
                calendar.require(isinstance(result.get('more'), bool), 'Calendar link search limit flag missing')
            else:
                calendar.require(isinstance(result.get('next'), int) and all(
                    isinstance(row.get('changed'), bool) and row.get('note') and row.get('news_snapshot')
                    and row.get('event_snapshot') for row in result['items']), 'Calendar link evidence invalid')
    return {'status': 'passed', 'read_endpoints': 3, 'anonymous_status': 401, 'writes': 0}


def checks_passed(report):
    return all(report.get(key, {}).get('status') == 'passed' for key in
               ('calendar_links_migration', 'calendar_links_restart', 'calendar_links_api'))


def retention_cutoff():
    return int(time.time())-90*86400+300
