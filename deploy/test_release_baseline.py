from contextlib import closing, ExitStack
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import ai_release as ai
import calendar_release as calendar
import news_release as news
import persistence_baseline as baselines
import test_calendar_release as fixtures
from test_persistence_baseline import PREVIOUS, FAILED, evidence, stamp


class ReleaseBaselineTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.previous, self.next = self.root/PREVIOUS, self.root/FAILED
        self.previous.mkdir()
        self.next.mkdir()
        self.now = 103900.25
        self.container = fixtures.target()
        (self.root/'config.json').write_text('{}')
        self.current = {'previous_release': str((self.root/'current').resolve()), 'finevent_pid': 456,
                        'config_sha256': calendar.recovery.sha(self.root/'config.json'),
                        'baseline': fixtures.row(), 'container': self.container}
        self.accepted = fixtures.evidence()
        self.accepted['resources']['baseline_counters'] = {'finevent_oom_kill': 0}
        (self.root/calendar.PERSIST_ID).mkdir()
        calendar.recovery.save(self.root/calendar.PERSIST_ID/'report.json', self.accepted)
        self.basis = {'id': calendar.PERSIST_ID}
        self.window = dict(evidence(), id=PREVIOUS, initial=deepcopy(self.current), process_identities=[1230, 4560])
        self.report = {'id': PREVIOUS, 'status': 'completed_for_review'}
        self.operation = {'id': FAILED, 'stage': 'preflight', 'status': 'deploying'}
        self.mock(calendar, 'ROOT', self.root)
        self.mock(calendar, 'DATA', self.root)
        self.mock(calendar, 'BACKUPS', self.root)
        self.mock(baselines, 'original', return_value=(self.accepted, self.basis))
        self.mock(calendar, 'inspect_persistence', return_value=self.current)
        self.mock(calendar, 'parent_metrics', return_value=fixtures.parent())
        self.mock(calendar.recovery, 'process_identity', side_effect=lambda pid: pid*10)
        self.mock(calendar.time, 'time', side_effect=lambda: self.now)
        self.mock(calendar.time, 'monotonic', side_effect=lambda: self.now)
        self.mock(calendar.time, 'sleep', side_effect=self.advance)
        self.mock(calendar, 'stamp', side_effect=lambda: stamp(self.now))
        with closing(sqlite3.connect(self.root/'finevent.db')) as conn:
            conn.execute('CREATE TABLE resources(at INTEGER PRIMARY KEY, available INTEGER, oom_kills INTEGER)')

    def mock(self, target, name, *args, **kwargs):
        return self.stack.enter_context(patch.object(target, name, *args, **kwargs))

    def advance(self, seconds):
        self.now += seconds

    def history(self, times):
        with closing(sqlite3.connect(self.root/'finevent.db')) as conn:
            conn.executemany('INSERT INTO resources VALUES(?,?,0)', ((at, 200*calendar.MIB) for at in times))
            conn.commit()

    def review(self):
        path = self.previous/'report.json'
        calendar.recovery.save(path, self.report)
        self.operation['previous_review'] = {'id': PREVIOUS, 'report_sha256': calendar.recovery.sha(path)}

    def preflight(self):
        return calendar.preflight(self.next, operation=self.operation)

    def sampling(self):
        self.mock(calendar.recovery, 'validate_live_processes')
        self.mock(calendar, 'sample', side_effect=lambda *_: dict(fixtures.row(), at=int(self.now)))
        self.mock(calendar, 'trend_outputs', side_effect=lambda start, end, **kwargs: {
            'status': 'passed', 'distinct_cycles': 2, 'html_mtime': end-10, 'last_import': end-5,
        })
        # The observer clock advances in-process; run read-only probe jobs on this same test clock.
        self.mock(baselines, 'Thread', side_effect=lambda target, args, daemon: SimpleNamespace(start=lambda: target(*args)))
        self.mock(calendar.recovery, 'public_health', return_value=fixtures.evidence()['health'])

    def test_comparison_separates_baseline_and_business_and_keeps_late_failure(self):
        self.sampling()
        baseline = fixtures.row()
        report = {'resources': calendar.resource_summary([baseline], baseline)}
        comparison = baselines.WindowComparison(self.next, report, self.now)
        comparison.sample(dict(baseline, at=int(self.now)), baseline)
        self.advance(3600)
        comparison.checkpoint(lambda: False)
        self.advance(5)
        comparison.sample(dict(baseline, at=int(self.now)), baseline)
        self.assertEqual(comparison.data['baseline_ready_at'], self.now)
        self.assertIsNone(comparison.data['candidate_at'])

        self.advance(60)
        comparison.checkpoint(lambda: True)
        self.advance(5)
        comparison.sample(dict(baseline, at=int(self.now)), baseline)
        candidate = self.now
        self.advance(5)
        comparison.sample(dict(baseline, at=int(self.now), finevent_current=70*calendar.MIB), baseline)
        self.advance(5)
        with self.assertRaisesRegex(RuntimeError, 'OOM'):
            comparison.sample(dict(baseline, at=int(self.now), host_oom_kill=baseline['host_oom_kill']+1), baseline)
        saved = json.loads((self.next/'report.json').read_bytes())['window_comparison']
        self.assertEqual(saved['candidate_at'], candidate)
        self.assertEqual(saved['tail']['samples'], 2)
        self.assertEqual(saved['tail']['finevent_peak_mib'], 70)
        self.assertEqual(saved['tail']['first_resource_issue']['at'], int(self.now))
        self.assertIn('OOM', saved['tail']['first_resource_issue']['reason'])

    def test_candidate_requires_a_fresh_probe_import_and_continuous_resources(self):
        self.sampling()
        baseline = fixtures.row()
        for case in ('pending', 'stale', 'import_before_html', 'resource_gap', 'https'):
            with self.subTest(case=case):
                report = {'resources': calendar.resource_summary([baseline], baseline)}
                comparison = baselines.WindowComparison(self.next, report, self.now)
                comparison.sample(dict(baseline, at=int(self.now)), baseline)
                self.advance(3600)
                html = int(self.now)-5
                imported = html-1 if case == 'import_before_html' else html+1
                with patch.object(calendar, 'trend_outputs', return_value={
                         'status': 'passed', 'distinct_cycles': 2, 'html_mtime': html, 'last_import': imported}), \
                     patch.object(calendar.recovery, 'public_health', return_value={} if case == 'https' else fixtures.evidence()['health']):
                    if case == 'pending':
                        with patch.object(baselines, 'Thread'):
                            comparison.checkpoint(lambda: True)
                    else:
                        comparison.checkpoint(lambda: True)
                self.advance(65 if case == 'stale' else 5)
                if case == 'resource_gap':
                    report['resources']['max_sample_interval_seconds'] = 16
                comparison.sample(dict(baseline, at=int(self.now)), baseline)
                self.assertIsNone(comparison.data['candidate_at'])
                self.assertIsNone(comparison.data['tail'])

    def test_comparison_probe_failure_does_not_invalidate_a_full_baseline(self):
        self.sampling()
        def trend(start, end, *, post_start_cycles=False):
            if post_start_cycles:
                raise OSError('private diagnostic details')
            return {'status': 'passed', 'distinct_cycles': 2}
        self.mock(calendar, 'trend_outputs', side_effect=trend)
        report = {'id': FAILED}
        baselines.collect(self.next, report)
        self.assertEqual(report['status'], 'completed_for_review')
        comparison = report['window_comparison']
        self.assertIsNone(comparison['candidate_at'])
        self.assertIsNone(comparison['tail'])
        self.assertGreater(comparison['probe_errors'], 0)
        self.assertEqual(comparison['last_probe_error'], 'OSError')
        self.assertNotIn('private diagnostic details', json.dumps(report))
        baselines.validate(report, FAILED, self.now, self.basis)

    def test_both_post_restart_acceptance_loops_publish_their_own_samples(self):
        self.sampling()
        self.mock(calendar, 'service_pid', return_value=self.current['finevent_pid'])
        self.mock(calendar, 'target', return_value=self.container)
        self.mock(calendar, 'calendar_state', return_value=fixtures.state())
        self.mock(calendar, 'source_acceptance', return_value={'status': 'accepted_with_source_limits'})
        self.mock(calendar, 'OBSERVATION_SECONDS', 3905)
        self.mock(news, 'probe_api', return_value={'rule_version': 'test'})
        self.mock(news, 'state', return_value={})
        self.mock(news, 'polls', return_value={})
        self.mock(news, 'checks_passed', side_effect=lambda report: report['news_api_samples'] >= 60)
        observe_again = self.mock(baselines, 'collect')
        for observer in (calendar, news):
            with self.subTest(profile=observer.__name__):
                directory = self.previous/observer.__name__
                directory.mkdir()
                report = {'id': PREVIOUS, 'initial_calendar': fixtures.state(), 'preflight': self.current,
                          'deployment': {'release': self.current['previous_release']}}
                started = self.now
                observer.observe(directory, report, self.container)
                comparison = report['window_comparison']
                self.assertLess(comparison['candidate_at']-started, 65*60)
                self.assertGreaterEqual(self.now-started, 65*60)
                self.assertGreater(comparison['tail']['samples'], 0)
                self.assertIsNone(comparison['tail']['first_resource_issue'])
                if observer is news:
                    self.assertGreaterEqual(comparison['candidate_at']-started, 59*60)
                self.assertIn('next_baseline', report)
                window = json.loads((directory/'next-baseline.json').read_bytes())
                rows = [json.loads(line) for line in (directory/'samples.jsonl').read_text().splitlines()]
                self.assertEqual((rows[0]['at'], rows[-1]['at']), (window['first_sample_at'], window['last_sample_at']))
                self.assertEqual(window['resources']['samples'], len(rows))
                self.assertEqual(window['process_identities'], [1230, 4560])
                self.assertEqual(window['initial']['previous_release'], self.current['previous_release'])
                baselines.validate(window, PREVIOUS, self.now, self.basis)
        observe_again.assert_not_called()

    def test_ai_baseline_starts_after_final_restart_without_a_second_paid_request(self):
        self.sampling()
        self.mock(ai, 'POST_SECONDS', 0)
        watch = self.mock(ai, 'Watch').return_value
        watch.take.return_value = {'available': 200*calendar.MIB, 'trend_jobs': ['natural']}
        self.mock(ai, 'trend_jobs', side_effect=[[], ['natural'], [], ['natural']])
        paid = self.mock(ai, 'one_request')
        self.mock(ai, 'pause')
        self.mock(ai, 'calls', return_value=[1])
        self.mock(ai, 'state', return_value={'counts': {'ai_calls': 1}})
        self.mock(ai, 'overview', return_value={})
        self.mock(news, 'probe_api', return_value={})
        self.mock(news, 'polls', return_value={})
        self.mock(calendar, 'snapshot')
        self.mock(calendar, 'target', return_value=self.container)
        self.mock(ai, 'checks_passed', return_value=True)
        self.mock(calendar, 'runtime_passed', return_value=True)
        def restart(*_):
            self.current['finevent_pid'] = 789
            return {'status': 'passed'}
        self.mock(calendar, 'restart_check', side_effect=restart)
        report = {'id': PREVIOUS, 'paid_model_calls': 1, 'preflight': deepcopy(self.current)}
        ai.observe(self.previous, report, self.container)
        paid.assert_called_once()
        window = json.loads((self.previous/'next-baseline.json').read_bytes())
        self.assertEqual(window['initial']['finevent_pid'], 789)
        self.assertEqual(window['process_identities'], [1230, 7890])
        self.assertEqual(report['status'], 'completed_for_review')

        # One natural cycle can pass business acceptance but cannot authorize reuse.
        self.mock(calendar, 'trend_outputs', return_value={'status': 'passed', 'distinct_cycles': 1})
        report = {'id': FAILED, 'paid_model_calls': 1, 'preflight': deepcopy(self.current)}
        ai.observe(self.next, report, self.container)
        self.assertEqual(paid.call_count, 2)
        self.assertEqual(report['status'], 'completed_for_review')
        self.assertIn('baseline_unavailable', report)
        self.assertFalse((self.next/'next-baseline.json').exists())

    def test_seven_day_boundary_reuses_evidence_without_extra_observation(self):
        self.now = 103900+7*86400
        self.history(range(103900, int(self.now)+1, 60))
        baselines.publish(self.previous, self.report, self.window)
        self.review()
        observe = self.mock(baselines, 'observe_for_operation')
        result = self.preflight()
        self.assertEqual(result['renewed_baseline'], {'id': PREVIOUS, 'source': 'release_acceptance',
                                                     'report_sha256': self.report['next_baseline']['report_sha256']})
        self.assertEqual(result['history_samples'], 10081)
        self.assertTrue((self.next/'before.db').is_file())
        observe.assert_not_called()

    def test_legacy_predecessor_collects_once_before_snapshot_and_accepts_overlapping_db_sample(self):
        self.review()
        self.sampling()
        self.history([int(self.now)+3900-30])
        with patch.object(baselines, 'collect', wraps=baselines.collect) as collect, \
             patch.object(calendar.installer, 'install') as install, patch.object(calendar.recovery, 'command') as command:
            result = self.preflight()
        collect.assert_called_once()
        install.assert_not_called()
        command.assert_not_called()
        self.assertEqual(result['renewed_baseline']['source'], 'preflight_observation')
        self.assertEqual(result['history_samples'], 1)
        path = self.next/'preflight_baseline'/'report.json'
        self.assertEqual(calendar.recovery.sha(path), result['renewed_baseline']['report_sha256'])
        self.assertEqual(json.loads(path.read_bytes())['resources']['samples'], 781)
        self.assertEqual(self.operation['preflight_baseline']['window_comparison'], json.loads(path.read_bytes())['window_comparison'])
        self.assertIn('preflight_baseline', json.loads((self.next/'report.json').read_bytes()))

    def test_invalid_or_interrupted_window_requests_one_fresh_observation(self):
        for reason in ('expired', 'config', 'release', 'pid_reuse', 'basis', 'gap', 'tampered', 'incomplete'):
            with self.subTest(reason=reason):
                window = deepcopy(self.window)
                self.now = 103901 if reason != 'expired' else 103901+7*86400
                if reason == 'config': window['initial']['config_sha256'] = 'other'
                if reason == 'release': window['initial']['previous_release'] = 'other'
                if reason == 'pid_reuse': window['process_identities'][1] -= 1
                if reason == 'basis': window['basis'] = {'id': 'old'}
                baselines.publish(self.previous, self.report, window)
                self.review()
                if reason == 'tampered': (self.previous/'next-baseline.json').write_bytes(b'changed')
                if reason == 'incomplete': self.operation['previous_review']['interrupted_for_repair'] = True
                with patch.object(baselines, 'observe_for_operation', side_effect=RuntimeError('renewal started')) as observe:
                    with self.assertRaisesRegex(RuntimeError, 'renewal started'):
                        self.preflight()
                    observe.assert_called_once()
                    expected = {'expired': 'expired', 'config': 'identity changed', 'release': 'identity changed',
                                'pid_reuse': 'process identity', 'basis': 'origin', 'gap': 'history',
                                'tampered': 'No reusable', 'incomplete': 'No reusable'}
                    self.assertIn(expected[reason], observe.call_args.args[-1])
        self.assertFalse((self.next/'before.db').exists())

    def test_known_pressure_changed_review_and_new_oom_still_block_installation(self):
        baselines.publish(self.previous, self.report, self.window)
        self.review()
        self.history([103900, 104200])
        self.now = 104201
        with closing(sqlite3.connect(self.root/'finevent.db')) as conn:
            conn.execute('UPDATE resources SET available=0 WHERE at=104200')
            conn.commit()
        observe = self.mock(baselines, 'observe_for_operation')
        with self.assertRaisesRegex(RuntimeError, 'pressure'):
            self.preflight()
        (self.previous/'report.json').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'predecessor report changed'):
            self.preflight()
        with patch.object(calendar, 'inspect_persistence', side_effect=RuntimeError('new OOM')):
            with self.assertRaisesRegex(RuntimeError, 'new OOM'):
                self.preflight()
        observe.assert_not_called()
        self.assertFalse((self.next/'before.db').exists())
