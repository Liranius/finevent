from contextlib import closing, contextmanager
from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import calendar_links_release as links
import news_release
from test_news_release import evidence


class CalendarLinksTests(unittest.TestCase):
    def test_real_client_allows_only_scoped_calendar_gets(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'finevent.db'
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute('CREATE TABLE sessions(token TEXT,expires INTEGER)')
            calls = []
            @contextmanager
            def open_response(request, timeout):
                calls.append(request.full_url)
                yield SimpleNamespace(status=200, headers={'Cache-Control': 'no-store'}, read=lambda limit: b'{"items": []}')
            with patch.object(news_release, 'build_opener', return_value=SimpleNamespace(open=open_response)):
                with news_release.api_client(path) as get:
                    for endpoint in ('news/1/calendar-links', 'calendar/event/news-links',
                                     'calendar-link-search?from=2026-11-01&to=2026-11-02'):
                        self.assertEqual(get(endpoint)[0], 200)
                    for endpoint in ('calendar/event', 'calendar/../news-links',
                                     'calendar-link-search?from=2026-11-01&to=2026-11-02&extra=1',
                                     'calendar/event/news-links#fragment'):
                        with self.subTest(endpoint=endpoint), self.assertRaisesRegex(RuntimeError, 'Invalid local'):
                            get(endpoint)
            self.assertEqual(len(calls), 3)

    def test_migration_restart_and_retention(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'finevent.db'
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.executescript('CREATE TABLE news(id INTEGER,last_seen INTEGER); CREATE TABLE calendar_events(id TEXT);')
            old = links.state(path, 0)
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.executescript('CREATE TABLE news_calendar_links(id INTEGER PRIMARY KEY AUTOINCREMENT,news_id INTEGER,event_id TEXT,note TEXT);'
                                  "INSERT INTO news VALUES(1,100); INSERT INTO calendar_events VALUES('event');"
                                  "INSERT INTO news_calendar_links(news_id,event_id,note) VALUES(1,'event','confirmed evidence');")
            current = links.state(path, 0)
            self.assertTrue(links.verify(old, current)['first_install'])
            self.assertEqual(links.verify(current, current)['preserved_links'], 1)
            for kind in ('missing', 'changed', 'orphan', 'sequence'):
                broken = deepcopy(current)
                if kind == 'missing': broken['links'] = {}
                if kind == 'changed': broken['links'][1] = 'changed'
                if kind == 'orphan': broken['orphans'] = 1
                if kind == 'sequence': broken['sequence'] = 0
                with self.subTest(kind=kind), self.assertRaises(RuntimeError): links.verify(current, broken)
            self.assertEqual(links.state(path, 101)['links'], {})

    def test_opt_in_does_not_weaken_existing_checks(self):
        report = evidence()
        self.assertTrue(news_release.checks_passed(report))
        report['calendar_links_required'] = True
        self.assertFalse(news_release.checks_passed(report))
        keys = ('calendar_links_migration', 'calendar_links_restart', 'calendar_links_api')
        for key in keys: report[key] = {'status': 'passed'}
        self.assertTrue(news_release.checks_passed(report))
        for key in keys:
            failed = deepcopy(report); failed[key]['status'] = 'failed'
            self.assertFalse(news_release.checks_passed(failed))
        report['news_api']['status'] = 'failed'
        self.assertFalse(news_release.checks_passed(report))

    def test_probe_reads_only_and_checks_auth(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'finevent.db'
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.executescript('CREATE TABLE news(id INTEGER,last_seen INTEGER); CREATE TABLE calendar_events(id TEXT,start_day TEXT,end_day TEXT);'
                                  "INSERT INTO news VALUES(1,100); INSERT INTO calendar_events VALUES('event','2026-11-01','2026-11-01');")
            calls = []
            @contextmanager
            def client():
                def get(endpoint, authenticated=True):
                    calls.append((endpoint, authenticated))
                    return (200, {'items': [], 'next': 0, 'more': False}) if authenticated else (401, None)
                yield get
            with patch.object(links.calendar, 'DATA', Path(temp)), patch.object(news_release, 'api_client', client):
                self.assertEqual(links.probe()['writes'], 0)
                self.assertEqual(len(calls), 6)
