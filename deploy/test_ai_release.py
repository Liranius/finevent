from contextlib import closing, contextmanager, nullcontext
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import ai_release as ai
import calendar_release as calendar
from test_news_release import evidence as news_evidence


def evidence():
    report = news_evidence()
    report.update(verification_profile='ai', ai_acceptance={'status': 'passed'}, ai_migration={'status': 'passed'},
                  ai_restart={'status': 'passed'}, paid_model_calls=1, ai_overlap_samples=3,
                  ai_result={'status': 'succeeded', 'attempts': 1, 'stale': False, 'facts': 1, 'evidence': 1},
                  ai_call={'status': 'succeeded', 'cost_upper_nano': 1000, 'reserved_nano': 100000,
                           'http_status': 200, 'month': '2026-11'},
                  ai_budget_before={'estimated_nano': 2000, 'reserved_nano': 3000, 'probe_nano': 5000, 'month': '2026-11'},
                  ai_budget_after={'estimated_nano': 3000, 'reserved_nano': 3000, 'probe_nano': 5000, 'month': '2026-11'})
    return report


def database(path, upgraded=True):
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript('''CREATE TABLE sessions(token TEXT PRIMARY KEY,expires INTEGER);
            CREATE TABLE ai_probe_runs(id TEXT PRIMARY KEY,status TEXT,cost_upper_nano INTEGER,reserved_nano INTEGER);
            INSERT INTO ai_probe_runs VALUES('old','succeeded',123,456);''')
        if upgraded:
            conn.executescript('''CREATE TABLE ai_settings(singleton INTEGER,enabled INTEGER,automatic INTEGER);
                INSERT INTO ai_settings VALUES(1,0,0);
                CREATE TABLE ai_jobs(id TEXT PRIMARY KEY,status TEXT);
                CREATE TABLE ai_links(news_id INTEGER,version INTEGER,job_id TEXT);
                CREATE TABLE ai_calls(id INTEGER PRIMARY KEY,status TEXT);
                CREATE TABLE ai_call_timings(call_id INTEGER PRIMARY KEY,started_ms INTEGER,completed_ms INTEGER);
                CREATE VIEW ai_budget_entries AS SELECT * FROM ai_probe_runs;''')


class AIReleaseTests(unittest.TestCase):
    def test_ai_acceptance_requires_result_billing_overlap_and_unchanged_resource_gates(self):
        report = evidence()
        calendar.validate_previous(report, report['id'], 'current')
        for kind in ('no_overlap', 'no_usage', 'invalid_output', 'extra_call', 'too_expensive', 'lost_reservation',
                     'probe_cost_changed', 'stale_result', 'restart', 'month_boundary', 'oom', 'source_gap'):
            changed = deepcopy(report)
            if kind == 'no_overlap': changed['ai_overlap_samples'] = 0
            if kind == 'no_usage': changed['ai_call']['cost_upper_nano'] = None
            if kind == 'invalid_output': changed['ai_result']['status'] = 'invalid_output'
            if kind == 'extra_call': changed['paid_model_calls'] = 2
            if kind == 'too_expensive': changed['ai_call']['reserved_nano'] = 500_000_001
            if kind == 'lost_reservation': changed['ai_budget_after']['reserved_nano'] = 0
            if kind == 'probe_cost_changed': changed['ai_budget_after']['probe_nano'] += 1
            if kind == 'stale_result': changed['ai_result']['stale'] = True
            if kind == 'restart': changed['ai_restart']['status'] = 'failed'
            if kind == 'month_boundary': changed['ai_budget_after']['month'] = '2026-12'
            if kind == 'oom': changed['resources']['host_oom_kill_delta'] = 1
            if kind == 'source_gap': changed['news_polls']['wallstreetcn']['gaps'] = 1
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                calendar.validate_previous(changed, changed['id'], 'current')

    def test_overlap_requires_whole_sample_inside_request_and_same_process_identity(self):
        job = {'pid': 123, 'start_ticks': 555}
        row = {'sample_started_ms': 101, 'sample_completed_ms': 109, 'trend_jobs': [job]}
        call = {'started_ms': 100, 'completed_ms': 110}
        self.assertEqual(ai.overlap([row], call, [job]), 1)
        for changed in ({**row, 'sample_started_ms': 99}, {**row, 'sample_completed_ms': 110},
                        {**row, 'trend_jobs': [{'pid': 123, 'start_ticks': 556}]}):
            self.assertEqual(ai.overlap([changed], call, [job]), 0)
        self.assertEqual(ai.overlap([row], {**call, 'completed_ms': 0}, [job]), 0)

    def test_resource_sample_requires_job_identity_on_both_sides_of_measurement(self):
        first = {'pid': 5, 'start_ticks': 10}
        reused = {'pid': 5, 'start_ticks': 20}
        watch = object.__new__(ai.Watch)
        watch.current, watch.pid, watch.identities = {'cgroup': {'path': '/test'}}, 1, (2, 3)
        watch.rows, watch.stream, watch.baseline = [], io.StringIO(), {}
        with patch.object(ai.recovery, 'validate_live_processes'), patch.object(calendar, 'sample', return_value={}), \
             patch.object(calendar, 'check_sample'), patch.object(ai, 'trend_jobs', side_effect=[[first], [reused]]):
            self.assertEqual(watch.take()['trend_jobs'], [])
        with patch.object(ai.recovery, 'validate_live_processes'), patch.object(calendar, 'sample', return_value={}), \
             patch.object(calendar, 'check_sample'), patch.object(ai, 'trend_jobs', side_effect=[[first], [first]]):
            self.assertEqual(watch.take()['trend_jobs'], [first])

    def test_preflight_rejects_existing_key_jobs_or_running_probe_without_mutation(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(calendar, 'DATA', Path(temporary)):
            path = Path(temporary)/'finevent.db'
            database(path)
            ai.preflight()
            for statement in ("UPDATE ai_probe_runs SET status='running'", "INSERT INTO ai_jobs VALUES('one','queued')",
                              'UPDATE ai_settings SET automatic=1'):
                with closing(sqlite3.connect(path)) as conn:
                    conn.execute(statement)
                    conn.commit()
                with self.assertRaises(RuntimeError): ai.preflight()
                with closing(sqlite3.connect(path)) as conn:
                    conn.executescript("UPDATE ai_probe_runs SET status='succeeded'; DELETE FROM ai_jobs; UPDATE ai_settings SET automatic=0;")
            (Path(temporary)/'ai-config.json').write_text('private', encoding='utf-8')
            with self.assertRaises(RuntimeError): ai.preflight()
            self.assertEqual((Path(temporary)/'ai-config.json').read_text(), 'private')

    def test_migration_and_restart_hash_preserve_ledger_without_exporting_content(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(calendar, 'DATA', Path(temporary)):
            path = Path(temporary)/'finevent.db'
            old = Path(temporary)/'before.db'
            database(old, False)
            database(path)
            self.assertEqual(ai.migration(old)['prior_probe_entries'], 1)
            before = ai.state()
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("INSERT INTO ai_jobs VALUES('private source text','queued')")
                conn.commit()
            self.assertNotEqual(before['sha256'], ai.state()['sha256'])
            self.assertNotIn('private source text', json.dumps(ai.state()))
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript('DELETE FROM ai_jobs; UPDATE ai_probe_runs SET cost_upper_nano=0;')
            with self.assertRaisesRegex(RuntimeError, 'ledger changed'): ai.migration(old)

    def test_session_cleanup_and_write_allowlist_even_when_network_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'finevent.db'
            database(path)
            error = HTTPError('local', 401, '', {}, io.BytesIO(b'private-error'))
            opener = SimpleNamespace(open=Mock(side_effect=error))
            with patch.object(ai, 'build_opener', return_value=opener):
                with ai.api_client(path) as request:
                    with closing(sqlite3.connect(path)) as conn:
                        self.assertEqual(len(conn.execute('SELECT token FROM sessions').fetchone()[0]), 64)
                    self.assertEqual(request('ai', authenticated=False), (401, None))
                    with self.assertRaises(RuntimeError): request('ai/settings', 'PUT', {'enabled': True, 'automatic': True})
                    with self.assertRaises(RuntimeError): request('news/1/analysis', 'POST', {'retry': True})
                    with self.assertRaises(RuntimeError): request('https://example.org')
                    opener.open.assert_called_once()
                with closing(sqlite3.connect(path)) as conn:
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)
                self.assertTrue(error.fp.closed)

    def test_key_prompt_is_inherited_terminal_not_captured_or_in_arguments(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(ai.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            (Path(temporary)/'finevent').write_bytes(b'verified-test-binary')
            ai.stage_key(Path(temporary), bundle=Path(temporary))
            args, kwargs = run.call_args
            self.assertEqual(args[0][1:3], ['-configure-ai', '-data'])
            self.assertEqual(kwargs, {'check': False})

    def test_private_config_moves_to_service_owner_without_exporting_key_or_overwriting(self):
        config = {'model': 'deepseek-flash', 'api_key': 'unit-test-private-key', 'prices_valid_until': '2026-12-01',
                  'peak_pricing': {'version': 'deepseek-2026-11-01', 'input_nano_per_token': 2000,
                                   'cached_nano_per_token': 40, 'output_nano_per_token': 8000}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root/'private-ai-input'
            stage.mkdir()
            live = root/'data'
            live.mkdir()
            source = stage/'ai-config.json'
            source.write_text(json.dumps(config), encoding='utf-8')
            original_stat = Path.stat
            def private_stat(path, *args, **kwargs):
                result = original_stat(path, *args, **kwargs)
                if path == source:
                    # Linux permission/ownership operations are mocked on the Windows host.
                    fields = list(result)
                    fields[0] = 0o100600
                    return os.stat_result(fields)
                return result
            account = SimpleNamespace(pw_uid=123, pw_gid=456)
            with patch.dict('sys.modules', {'pwd': SimpleNamespace(getpwnam=lambda _: account)}), \
                 patch.object(calendar, 'DATA', live), patch.object(Path, 'stat', private_stat), \
                 patch.object(os, 'fchown', create=True) as chown:
                result = ai.install_key(root)
                self.assertFalse(source.exists())
                self.assertEqual(json.loads((live/'ai-config.json').read_text())['api_key'], config['api_key'])
                self.assertNotIn(config['api_key'], json.dumps(result))
                self.assertEqual(chown.call_args.args[1:], (123, 456))
                source.write_text(json.dumps({**config, 'api_key': 'different-private-key'}), encoding='utf-8')
                with self.assertRaises(FileExistsError): ai.install_key(root)
                self.assertEqual(json.loads((live/'ai-config.json').read_text())['api_key'], config['api_key'])

    def test_single_request_pauses_on_network_error_without_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            request = Mock(side_effect=[(200, {}), TimeoutError('local request outcome unknown')])
            @contextmanager
            def client(): yield request
            watch = SimpleNamespace(fast=nullcontext, save=Mock())
            report = {}
            with patch.object(ai, 'candidate', return_value=123), patch.object(ai, 'calls', return_value=[]), \
                 patch.object(ai, 'pause') as pause, patch.object(ai, 'api_client', client):
                with self.assertRaises(TimeoutError):
                    ai.one_request(Path(temporary), report, watch, [{'pid': 1, 'start_ticks': 2}])
                self.assertEqual(request.call_count, 2)
                self.assertEqual(request.call_args.args, ('news/123/analysis', 'POST', {'retry': False}))
                pause.assert_called_once()
                self.assertEqual(report['paid_model_calls'], 0)

    def test_existing_business_call_is_never_repeated(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(ai, 'candidate', return_value=123), \
             patch.object(ai, 'calls', return_value=[{'id': 1}]), patch.object(ai, 'api_client') as request:
            with self.assertRaisesRegex(RuntimeError, 'already exists'):
                ai.one_request(Path(temporary), {}, Mock(), [])
            request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
