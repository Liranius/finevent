from contextlib import closing, contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import install
import news_push_release as push
import news_release
from test_news_release import evidence


class NewsPushReleaseTests(unittest.TestCase):
    def test_real_state_read_and_missing_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'finevent.db'
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.executescript('CREATE TABLE ai_settings(singleton INTEGER,enabled INTEGER,automatic INTEGER);'
                                   'INSERT INTO ai_settings VALUES(1,1,0);')
            old = push.state(path)
            self.assertFalse(old['present'])
            self.assertEqual(old['ai'], {'enabled': 1, 'automatic': 0})
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.executescript('CREATE TABLE news_push_settings(id INTEGER,payload TEXT,cursor INTEGER,enabled_at INTEGER);')
            with self.assertRaisesRegex(RuntimeError, 'missing tables'): push.state(path)
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.executescript('CREATE TABLE news_push_events(id INTEGER); CREATE TABLE news_push_links(id INTEGER);'
                                   'CREATE TABLE news_push_aliases(id INTEGER); CREATE TABLE news_push_jobs(delivery_id INTEGER);'
                                   'CREATE TABLE deliveries(id INTEGER,scenario TEXT,status TEXT);')
            state = push.state(path)
            self.assertEqual(push.verify(old, state)['status'], 'passed')
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("INSERT INTO deliveries VALUES(1,'news','queued')")
            with self.assertRaisesRegex(RuntimeError, 'linkage'): push.verify(old, push.state(path))

    def test_migration_restart_and_ai_preferences(self):
        old = {'present': False, 'ai': {'enabled': 1, 'automatic': 1}}
        new = {'present': True, 'ai': old['ai'], 'orphans': 0, 'pending': 0, 'cursor': 3,
               'settings': {'enabled': False, 'devices': [], 'categories': [], 'industries': []}}
        self.assertEqual(push.verify(old, new)['status'], 'passed')
        self.assertEqual(push.verify(new, new)['status'], 'passed')
        for key, value in [('ai', {'enabled': 0, 'automatic': 1}), ('orphans', 1), ('pending', 1)]:
            bad = deepcopy(new); bad[key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError): push.verify(old, bad)
        bad = deepcopy(new); bad['settings']['enabled'] = True
        with self.assertRaises(RuntimeError): push.verify(old, bad)
        bad = deepcopy(new); bad['cursor'] = 1
        with self.assertRaises(RuntimeError): push.verify(new, bad)

    def test_news_profile_requires_new_gates_without_invalidating_old_evidence(self):
        report = evidence()
        self.assertTrue(news_release.checks_passed(report))
        report['news_push_required'] = True
        self.assertFalse(news_release.checks_passed(report))
        for key in ('news_push_migration', 'news_push_restart', 'news_push_api'):
            report[key] = {'status': 'passed'}
        self.assertTrue(news_release.checks_passed(report))
        report['news_push_api']['status'] = 'failed'
        self.assertFalse(news_release.checks_passed(report))

    def test_rollback_pauses_pending_only_preserving_settings_and_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'finevent.db'
            self.assertEqual(install.pause_news_push(path), 0)
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.executescript("CREATE TABLE news_push_settings(id INTEGER,payload TEXT);"
                                   "CREATE TABLE deliveries(id INTEGER,scenario TEXT,status TEXT,receipt_token TEXT,error TEXT);"
                                   "INSERT INTO deliveries VALUES(1,'news','queued','one',''),(2,'news','sending','two',''),(3,'news','accepted','',''),(4,'calendar','queued','cal','');")
                conn.execute('INSERT INTO news_push_settings VALUES(1,?)', (json.dumps({'enabled':True,'devices':[1],'categories':['macro'],'industries':[]}),))
            self.assertEqual(install.pause_news_push(path), 2)
            self.assertEqual(install.pause_news_push(path), 0)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute('SELECT status,receipt_token FROM deliveries ORDER BY id').fetchall(), [('cancelled',''),('cancelled',''),('accepted',''),('queued','cal')])
                self.assertEqual(json.loads(conn.execute('SELECT payload FROM news_push_settings').fetchone()[0]), {'enabled':False,'devices':[1],'categories':['macro'],'industries':[]})

    def test_api_rejects_missing_metadata_and_credentials(self):
        value = {'settings': {'enabled': False}, 'categories': [{}]*10, 'industries': [{}]*12, 'backlog': 0, 'history': []}
        @contextmanager
        def client():
            yield lambda endpoint, authenticated=True: (200, value) if authenticated else (401, None)
        for change in ({'categories': []}, {'history': [{'receipt_token': 'hidden'}]}, {'backlog': -1}):
            with self.subTest(change=change), patch.object(news_release, 'api_client', client):
                prior = deepcopy(value); value.update(change)
                with self.assertRaises(RuntimeError): push.probe()
                value.clear(); value.update(prior)


if __name__ == '__main__':
    unittest.main()
