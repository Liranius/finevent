from contextlib import closing, contextmanager
from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import news_release
import source_health_release as health
from test_news_release import evidence


class SourceHealthReleaseTests(unittest.TestCase):
    def test_migration_and_restart_preserve_incidents_and_read_progress(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'finevent.db'
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute('CREATE TABLE existing_news(id INTEGER)')
            old = health.state(path, 0)
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.executescript('CREATE TABLE trend_health_sync(id INTEGER,succeeded INTEGER);'
                                  'CREATE TABLE trend_health_cycles(day TEXT,record_id INTEGER);'
                                  'CREATE TABLE trend_source_observations(day TEXT,record_id INTEGER);'
                                  'CREATE TABLE trend_source_incidents(id INTEGER,day TEXT,record_id INTEGER,at INTEGER,status TEXT);'
                                  "INSERT INTO trend_health_sync VALUES(1,100); INSERT INTO trend_health_cycles VALUES('day',1);"
                                  "INSERT INTO trend_source_incidents VALUES(1,'day',1,100,'failed');")
            current = health.state(path, 0)
            self.assertTrue(health.verify(old, current)['first_install'])
            self.assertEqual(health.verify(current, current)['preserved_incidents'], 1)
            for kind in ('missing', 'changed', 'progress', 'orphan'):
                changed = deepcopy(current)
                if kind == 'missing': changed['incidents'] = {}
                if kind == 'changed': changed['incidents'][1] = 'different'
                if kind == 'progress': changed['read_success'] = 0
                if kind == 'orphan': changed['orphans'] = 1
                with self.subTest(kind=kind), self.assertRaises(RuntimeError): health.verify(current, changed)
            self.assertEqual(health.state(path, 101)['incidents'], {})
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute('DROP TABLE trend_source_incidents')
            with self.assertRaisesRegex(RuntimeError, 'incomplete'): health.state(path, 0)

    def test_new_checks_required_only_for_new_reports(self):
        report = evidence()
        self.assertTrue(news_release.checks_passed(report))
        report['source_health_required'] = True
        self.assertFalse(news_release.checks_passed(report))
        for key in ('source_health_migration', 'source_health_restart', 'source_health_api'):
            report[key] = {'status': 'passed', 'ready': True}
        self.assertTrue(news_release.checks_passed(report))
        for key in ('source_health_migration', 'source_health_restart', 'source_health_api'):
            changed = deepcopy(report); changed[key]['status'] = 'failed'
            self.assertFalse(news_release.checks_passed(changed))
        report['source_health_api']['ready'] = False
        self.assertFalse(news_release.checks_passed(report))

    def test_live_probe_distinguishes_upstream_failure_from_reader_failure(self):
        result = {'collection_enabled': True, 'trendradar_enabled': True, 'server_time': 1000,
                  'read_success': 990, 'latest_cycle': 900, 'read_error': '', 'read_stale': False,
                  'sources': [{'id': 'a', 'state': 'failed'}], 'warnings': []}
        history = {'items': [], 'next': ''}
        @contextmanager
        def client():
            yield lambda endpoint, authenticated=True: (401, None) if not authenticated else (200, result if endpoint.endswith('source-health') else history)
        with patch.object(news_release, 'api_client', client):
            self.assertFalse(health.probe()['all_upstream_sources_healthy'])
            for change in ({'sources': []}, {'read_success': 0}, {'read_stale': True}, {'read_error': 'failed'},
                           {'sources': [{'id': 'a', 'state': 'unrecognized'}]}):
                before = deepcopy(result); result.update(change)
                with self.subTest(change=change), self.assertRaises(RuntimeError): health.probe()
                result.clear(); result.update(before)
            result['sources'] = []
            self.assertFalse(health.probe(ready=False)['ready'])


if __name__ == '__main__':
    unittest.main()
