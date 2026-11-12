from contextlib import closing, contextmanager, ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import io
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import calendar_release as calendar
import news_release as news


def database(path, upgraded=False):
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript('''
            CREATE TABLE news(id INTEGER PRIMARY KEY,source TEXT,source_id TEXT,title TEXT,content TEXT,url TEXT,
                published_at INTEGER,first_seen INTEGER,last_seen INTEGER,hash TEXT,version INTEGER);
            CREATE TABLE news_revisions(news_id INTEGER,version INTEGER,title TEXT,content TEXT,saved_at INTEGER,PRIMARY KEY(news_id,version));
            CREATE TABLE sessions(token TEXT PRIMARY KEY,expires INTEGER);
            CREATE TABLE polls(source TEXT,started INTEGER,completed INTEGER,success INTEGER,added INTEGER,changed INTEGER,gap TEXT);
            INSERT INTO news VALUES(1,'wallstreetcn','source-1','original','source body','https://example.org',0,1,9999999999,'hash',1);
            INSERT INTO news_revisions VALUES(1,0,'earlier','earlier body',1);
        ''')
        if upgraded:
            conn.executescript('''ALTER TABLE news ADD COLUMN ingested_at INTEGER DEFAULT 0;
                ALTER TABLE news ADD COLUMN updated_at INTEGER DEFAULT 0;
                CREATE TABLE news_features(news_id INTEGER REFERENCES news(id),item_version INTEGER,rule_version TEXT,payload TEXT);
                CREATE TABLE news_labels(news_id INTEGER REFERENCES news(id));
                CREATE TABLE news_processing(news_id INTEGER REFERENCES news(id));
                CREATE TABLE news_revision_evidence(news_id INTEGER,version INTEGER,url TEXT,published_at INTEGER,
                    FOREIGN KEY(news_id,version) REFERENCES news_revisions(news_id,version));
                CREATE TABLE news_identity(singleton INTEGER PRIMARY KEY,last_id INTEGER);
                CREATE TABLE news_changes(seq INTEGER PRIMARY KEY);
                INSERT INTO news_identity VALUES(1,1);
                INSERT INTO news_changes VALUES(1);
                INSERT INTO news_features VALUES(1,1,'topics-v1','{}');''')


def evidence():
    metrics = {'samples': 780, 'host_available_min_mib': 250, 'max_sample_interval_seconds': 6,
               'samples_below_64_mib': 0, 'host_oom_kill_delta': 0, 'trend_oom_kill_delta': 0,
               'finevent_oom_kill_delta': 0, 'finevent_current_peak_mib': 45}
    started = datetime(2026, 11, 1, tzinfo=timezone.utc)
    return {'id': 'calendar-release-20261101T010000Z-1234abcd', 'verification_profile': 'news',
            'stage': 'completed', 'status': 'completed_for_review', 'deployment': {'release': 'current'},
            'observation_started': started.isoformat(), 'finished_at': (started+timedelta(seconds=3901)).isoformat(),
            'restart': {'status': 'passed'}, 'backup_verification': {'status': 'passed'},
            'resources': metrics, 'news_initial_resources': deepcopy(metrics),
            'trendradar': {'status': 'passed'}, 'health': {p: {'exit': 0, 'http_status': '200'} for p in ('/finevent/healthz', '/TrendRadar/content/')},
            'news_migration': {'status': 'passed'}, 'news_restart': {'status': 'passed'}, 'news_api': {'status': 'passed'},
            'news_index': {'items': 100, 'backlog': 0, 'stale': 0}, 'news_installation_available_mib': [250]*6,
            'news_api_samples': 64, 'news_poll_end': 4000, 'news_acceptance': {'status': 'passed'},
            'news_polls': {s: {'attempts': 30, 'successes': 30, 'changes': 2, 'gaps': 0, 'last_success': 3900}
                           for s in ('wallstreetcn', 'trendradar')}}


class NewsReleaseTests(unittest.TestCase):
    def test_migration_preserves_unknown_time_and_accepts_traced_concurrent_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            old, live = Path(temporary)/'old.db', Path(temporary)/'live.db'
            database(old)
            database(live, True)
            self.assertEqual(news.verify_preservation(old, live)['checked_retained_items'], 1)
            with closing(sqlite3.connect(live)) as conn:
                conn.executescript("INSERT INTO news_revisions SELECT id,version,title,content,2 FROM news;"
                                   "INSERT INTO news_revision_evidence SELECT id,version,url,published_at FROM news;"
                                   "UPDATE news SET title='revised',version=2,hash='new';")
            self.assertEqual(news.verify_preservation(old, live)['status'], 'passed')
            with closing(sqlite3.connect(live)) as conn:
                conn.execute('UPDATE news SET ingested_at=99')
                conn.commit()
            with self.assertRaisesRegex(RuntimeError, 'Existing news'):
                news.verify_preservation(old, live)

    def test_migration_rejects_lost_or_silently_changed_evidence(self):
        for change in ("DELETE FROM news", "UPDATE news SET title='changed'", "DELETE FROM news_revisions",
                       "UPDATE news SET version=2,title='untraced'", "UPDATE news SET first_seen=10", "UPDATE news SET updated_at=10"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                old, live = Path(temporary)/'old.db', Path(temporary)/'live.db'
                database(old)
                database(live, True)
                with closing(sqlite3.connect(live)) as conn:
                    conn.executescript(change)
                with self.assertRaises(RuntimeError):
                    news.verify_preservation(old, live)

    def test_index_state_detects_stale_versions_and_invalid_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'live.db'
            database(path, True)
            self.assertEqual(news.state('topics-v1', path)['stale'], 0)
            self.assertEqual(news.state('topics-v2', path)['stale'], 1)
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('UPDATE news_identity SET last_id=0')
                conn.commit()
            with self.assertRaisesRegex(RuntimeError, 'identity'):
                news.state('topics-v1', path)

    def test_temporary_api_session_is_hashed_and_always_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'live.db'
            database(path)
            with self.assertRaisesRegex(RuntimeError, 'intentional'):
                with news.api_client(path):
                    with closing(sqlite3.connect(path)) as conn:
                        token, expires = conn.execute('SELECT token,expires FROM sessions').fetchone()
                        self.assertRegex(token, '^[a-f0-9]{64}$')
                        self.assertGreater(expires, 0)
                    raise RuntimeError('intentional')
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)
            self.assertIsNone(news.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://external.example'))

    def test_api_validation_covers_auth_pagination_and_pending_index(self):
        item = {'id': 1, 'source': 'wallstreetcn', 'source_id': 'one', 'version': 2, 'content': '', 'classification': {'rule_version': 'topics-v1'}}
        responses = {'news': (401, None), 'news/meta': (200, {'rule_version': 'topics-v1', 'collection_enabled': True, 'trendradar_enabled': True}),
                     'news?order=discovered': (200, {'items': [item], 'watermark': 2, 'backlog': 0, 'next': 'next'}),
                     'news/1': (200, {'item': item, 'revisions': [], 'related': []}),
                     'news?order=discovered&cursor=next': (200, {'items': [{**item, 'id': 2}]}),
                     'news?source=wallstreetcn&order=discovered': (200, {'items': [item]}),
                     'news/changes?after=2': (200, {'watermark': 2})}
        @contextmanager
        def client():
            yield lambda endpoint, authenticated=True: deepcopy(responses[endpoint])
        with patch.object(news, 'api_client', client):
            self.assertEqual(news.probe_api()['pages'], 2)
            item['classification'] = None
            self.assertEqual(news.probe_api(ready=False)['status'], 'passed')
            with self.assertRaisesRegex(RuntimeError, 'classification'):
                news.probe_api()
            item['classification'] = {'rule_version': 'topics-v1'}
            responses['news'] = (200, {})
            with self.assertRaisesRegex(RuntimeError, 'Anonymous'):
                news.probe_api()

    def test_unauthorized_probe_closes_response_and_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'live.db'
            database(path)
            body = io.BytesIO(b'{}')
            failure = HTTPError('http://127.0.0.1:8787/finevent/api/v1/news', 401, 'Unauthorized', {}, body)
            with patch.object(news, 'build_opener', return_value=Mock(open=Mock(side_effect=failure))):
                with news.api_client(path) as get:
                    self.assertEqual(get('news', False), (401, None))
                    self.assertTrue(body.closed)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)

    def test_news_profile_does_not_bypass_runtime_backup_or_duration_gates(self):
        report = evidence()
        calendar.validate_previous(report, report['id'], 'current')
        for kind in ('short', 'unknown_profile', 'index', 'api', 'oom', 'initial_memory', 'gap', 'stale_poll', 'no_changes', 'few_probes', 'backup'):
            changed = deepcopy(report)
            if kind == 'short': changed['finished_at'] = changed['observation_started']
            if kind == 'unknown_profile': changed['verification_profile'] = 'other'
            if kind == 'index': changed['news_index']['stale'] = 1
            if kind == 'api': changed['news_api']['status'] = 'failed'
            if kind == 'oom': changed['resources']['host_oom_kill_delta'] = 1
            if kind == 'initial_memory': changed['news_initial_resources']['host_available_min_mib'] = 63
            if kind == 'gap': changed['news_polls']['wallstreetcn']['gaps'] = 1
            if kind == 'stale_poll': changed['news_polls']['wallstreetcn']['last_success'] = 1
            if kind == 'no_changes':
                for row in changed['news_polls'].values(): row['changes'] = 0
            if kind == 'few_probes': changed['news_api_samples'] = 1
            if kind == 'backup': changed['backup_verification']['status'] = 'failed'
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                calendar.validate_previous(changed, changed['id'], 'current')

    def test_news_prepare_failure_uses_existing_rollback(self):
        lock = SimpleNamespace(flock=Mock(), LOCK_EX=1, LOCK_NB=2)
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            directory = Path(temporary)
            stack.enter_context(patch.dict('sys.modules', {'fcntl': lock}))
            stack.enter_context(patch.object(calendar, 'LOCK', directory/'lock'))
            stack.enter_context(patch.object(calendar, 'ROOT', directory))
            for name, value in {'preflight': {'container': {}}, 'snapshot': 'sha', 'target': {}, 'parent_metrics': {},
                                'validate_target': None, 'wait_initial_sources': {'reminders': {}}, 'readonly': Mock(close=Mock()),
                                'reminder_state': {}, 'verify_reminder_migration': {'status': 'passed'}}.items():
                stack.enter_context(patch.object(calendar, name, return_value=value))
            stack.enter_context(patch.object(calendar.installer, 'install', return_value=directory/'backup'))
            stack.enter_context(patch.object(news, 'prepare', side_effect=RuntimeError('news gate failed')))
            rollback = stack.enter_context(patch.object(calendar, 'rollback_current', return_value={'status': 'restored'}))
            report = {'id': 'news-test', 'status': 'queued', 'verification_profile': 'news'}
            with self.assertRaisesRegex(RuntimeError, 'news gate failed'):
                calendar.run(directory, report)
            rollback.assert_called_once()
            self.assertEqual(json.loads((directory/'report.json').read_text())['rollback']['status'], 'restored')


if __name__ == '__main__':
    unittest.main()
