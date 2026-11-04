from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import calendar_release as release


def target():
    return {'id': release.CONTAINER, 'pid': 123, 'restarts': 0, 'image': release.IMAGE,
            'project': 'finevent-trendradar-managed', 'restart': 'unless-stopped', 'parent': 'trendradar.slice',
            'memory': 192*release.MIB, 'memory_swap': 448*release.MIB,
            'cgroup': {'path': '/trendradar.slice/docker-example.scope', 'memory.high': 'max',
                       'memory.max': 192*release.MIB, 'memory.swap.max': 256*release.MIB}}


def parent():
    return {'memory.high': str(160*release.MIB), 'memory.max': str(192*release.MIB),
            'memory.swap.max': str(256*release.MIB), 'oom_kill': 0}


def evidence():
    return {'id': release.PERSIST_ID, 'status': 'completed_for_review', 'stage': 'completed',
            'finished_at': datetime.fromtimestamp(100000, timezone.utc).isoformat(),
            'resources': {'samples': 721, 'host_available_min_mib': 267, 'max_sample_interval_seconds': 6,
                          'samples_below_64_mib': 0, 'host_oom_kill_delta': 0, 'trend_oom_kill_delta': 0,
                          'finevent_oom_kill_delta': 0},
            'outputs': {'records': [{'at': 95000, 'items': 235}, {'at': 96800, 'items': 235}],
                        'source_statuses': [{'status': 'success'}], 'source_failures': 0,
                        'finevent_poll_count': 56, 'finevent_poll_failures': 0},
            'health': {path: {'exit': 0, 'http_status': '200'} for path in ('/finevent/healthz', '/TrendRadar/content/')}}


def state(at=100000, failed=None):
    return {'events': 74, 'drafts': 1, 'revisions': 74, 'events_sha256': 'content-hash',
            'sources': {source: {'id': source, 'last_attempt': at, 'last_success': at if source != failed else 0,
                                 'error': '' if source != failed else 'HTTP 403', 'count': 10, 'coverage_end': '2027-01-01'}
                        for source in release.SOURCE_IDS}}


def row():
    return {'at': 1, 'available': 250*release.MIB, 'host_swap_used': 100*release.MIB,
            'trend_memory': 70*release.MIB, 'trend_swap': release.MIB,
            'trend_oom_kill': 0, 'finevent_oom_kill': 0, 'host_oom_kill': 19,
            'finevent_current': 35*release.MIB, 'finevent_kernel_peak': 42*release.MIB,
            'finevent_rss': 28*release.MIB}


class CalendarReleaseTests(unittest.TestCase):
    def test_supplement_and_snapshot_do_not_turn_primary_failure_into_success(self):
        first, final = state(), state(121601, failed='bls')
        final['sources']['nbs'].update(last_success=0, error='timeout', count=0)
        expected = release.snapshot_manifest()
        final['snapshots'] = [{'source': 'nbs', 'id': expected['id'], 'metadata': expected,
                               'body_sha256': expected['sha256'], 'active_entries': 47,
                               'active_coverage_end': '2026-12-31'}]
        result = release.calendar_results(first, final)
        accepted = release.source_acceptance(result, final, '2026-10-31')
        self.assertEqual(accepted['status'], 'accepted_with_source_limits')
        self.assertFalse(result['nbs']['healthy'])
        self.assertFalse(result['bls']['healthy'])
        for kind in ('checksum', 'review', 'empty', 'expired', 'supplement_failed', 'not_due'):
            changed, results, today = deepcopy(final), deepcopy(result), '2026-10-31'
            if kind == 'checksum': changed['snapshots'][0]['body_sha256'] = 'tampered'
            if kind == 'review': changed['snapshots'][0]['metadata']['reviewed_at'] += 1
            if kind == 'empty': changed['snapshots'][0]['active_entries'] = 0
            if kind == 'expired': today = '2027-01-01'
            if kind == 'supplement_failed': results['nyfed']['healthy'] = False
            if kind == 'not_due': results['nbs']['synced_again'] = False
            with self.subTest(kind=kind):
                self.assertEqual(release.source_acceptance(results, changed, today)['status'], 'needs_review')

    def test_successor_requires_finished_observation_and_runtime_evidence(self):
        run_id = 'calendar-release-20261030T105422Z-9819e472'
        report = {'id': run_id, 'status': 'needs_review', 'stage': 'completed',
                  'deployment': {'release': 'current'}, 'restart': {'status': 'passed'},
                  'backup_verification': {'status': 'passed'},
                  'observation_started': '2026-10-30T18:55:00+08:00', 'finished_at': '2026-10-31T01:10:01+08:00',
                  'resources': {**evidence()['resources'], 'finevent_current_peak_mib': 40},
                  'trendradar': {'status': 'passed'}, 'health': evidence()['health'],
                  'calendar': release.calendar_results(state(), state(121601, failed='bls'))}
        release.validate_previous(report, run_id, 'current')
        for kind in ('unfinished', 'short', 'rollback', 'memory', 'oom', 'trend', 'https', 'source', 'supplement', 'different_release'):
            changed = deepcopy(report)
            if kind == 'unfinished': changed['stage'] = 'scheduled_sync_and_resources'
            if kind == 'short': changed['finished_at'] = '2026-10-30T20:00:00+08:00'
            if kind == 'rollback': changed['rollback'] = {'status': 'restored'}
            if kind == 'memory': changed['resources']['host_available_min_mib'] = 63
            if kind == 'oom': changed['resources']['host_oom_kill_delta'] = 1
            if kind == 'trend': changed['trendradar']['status'] = 'needs_review'
            if kind == 'https': changed['health']['/finevent/healthz']['http_status'] = '502'
            if kind == 'source': changed['calendar']['bea']['healthy'] = False
            if kind == 'supplement': changed['calendar']['nyfed']['healthy'] = False
            if kind == 'different_release': changed['deployment']['release'] = 'another'
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                release.validate_previous(changed, run_id, 'current')

    def test_snapshot_evidence_exports_hash_and_counts_without_raw_body(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'db'
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript("CREATE TABLE calendar_sources(id TEXT);CREATE TABLE calendar_events(id TEXT,payload TEXT);"
                                   "CREATE TABLE calendar_revisions(id TEXT);CREATE TABLE fund_drafts(id TEXT);"
                                   "CREATE TABLE calendar_snapshots(id TEXT,source TEXT,metadata TEXT,body BLOB);")
                conn.execute('INSERT INTO calendar_snapshots VALUES(?,?,?,?)', ('snapshot', 'nbs', '{}', b'original bytes'))
                conn.execute('INSERT INTO calendar_events VALUES(?,?)', ('event', json.dumps({'end': '2026-12-31', 'provenance': {'snapshot_id': 'snapshot'}})))
                conn.commit()
            result = release.calendar_state(path)
            self.assertEqual(result['snapshots'][0]['active_entries'], 1)
            self.assertEqual(result['snapshots'][0]['active_coverage_end'], '2026-12-31')
            self.assertEqual(result['snapshots'][0]['body_sha256'], release.hashlib.sha256(b'original bytes').hexdigest())
            self.assertNotIn('original bytes', json.dumps(result))

    def test_retired_rollover_allowance_no_longer_accepts_failed_supplement(self):
        report = {'id': 'calendar-release-20261031T080853Z-0d262c79', 'status': 'needs_review', 'stage': 'completed',
                  'deployment': {'release': '/srv/example/finevent/releases/20261031T080910229491Z'}, 'restart': {'status': 'passed'},
                  'backup_verification': {'status': 'passed'},
                  'observation_started': '2026-10-31T16:10:27+08:00', 'finished_at': '2026-10-31T22:25:28+08:00',
                  'resources': {**evidence()['resources'], 'finevent_current_peak_mib': 43},
                  'trendradar': {'status': 'passed'}, 'health': evidence()['health'],
                  'calendar': release.calendar_results(state(), state(121601, failed='nyfed'))}
        report['calendar']['nyfed']['latest'].update(error='纽约联储当前页月份未更新，保留已有日程', count=0)
        with self.assertRaises(RuntimeError):
            release.validate_previous(report, report['id'], report['deployment']['release'])
        for kind in ('other_operation', 'other_release', 'other_error', 'partial_import', 'still_observing', 'missing_cycle', 'low_memory', 'oom'):
            changed = deepcopy(report)
            if kind == 'other_operation': changed['id'] = 'calendar-release-20261031T090000Z-12345678'
            if kind == 'other_release': changed['deployment']['release'] = 'different'
            if kind == 'other_error': changed['calendar']['nyfed']['latest']['error'] = 'HTTP 503'
            if kind == 'partial_import': changed['calendar']['nyfed']['latest']['count'] = 1
            if kind == 'still_observing': changed['status'] = 'observing'
            if kind == 'missing_cycle': changed['calendar']['nyfed']['synced_again'] = False
            if kind == 'low_memory': changed['resources']['host_available_min_mib'] = 63
            if kind == 'oom': changed['resources']['finevent_oom_kill_delta'] = 1
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                release.validate_previous(changed, changed['id'], changed['deployment']['release'])

    def test_initial_sync_allows_bounded_slow_calendar_before_checking_sources(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(release.time, 'monotonic', side_effect=[0, 300]), \
                patch.object(release, 'service_pid', return_value=123), \
                patch.object(release, 'sample', return_value=row()), \
                patch.object(release, 'calendar_state', return_value=state()):
            report = {}
            result = release.wait_initial_sources(Path(directory), report, target())
            self.assertEqual(result, state())
            self.assertEqual(report['initial_resources']['samples'], 2)

    def test_new_container_and_parent_reclaim_are_the_accepted_configuration(self):
        release.validate_target(target(), target(), parent())
        for kind in ('old_id', 'wrong_parent_limit', 'child_limit', 'restart', 'parent_oom'):
            current, expected, group = target(), target(), parent()
            if kind == 'old_id':
                current['id'] = 'eca05-old-container'
            elif kind == 'wrong_parent_limit':
                group['memory.high'] = 'max'
            elif kind == 'child_limit':
                current['cgroup']['memory.high'] = 160*release.MIB
            elif kind == 'restart':
                current['pid'] += 1
            else:
                group['oom_kill'] = 1
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                release.validate_target(current, expected, group)

    def test_failed_or_stale_persistence_cannot_admit_a_new_release(self):
        release.validate_report(evidence(), 100001)
        for kind in ('failed', 'rolled_back', 'oom', 'low_memory', 'missing_cycle', 'source_error', 'stale'):
            report, now = evidence(), 100001
            if kind == 'failed': report['status'] = 'incomplete'
            if kind == 'rolled_back': report['rollback'] = {'status': 'restored'}
            if kind == 'oom': report['resources']['host_oom_kill_delta'] = 1
            if kind == 'low_memory': report['resources']['host_available_min_mib'] = 63
            if kind == 'missing_cycle': report['outputs']['records'] = []
            if kind == 'source_error': report['outputs']['source_failures'] = 1
            if kind == 'stale': now += 8*86400
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                release.validate_report(report, now)

    def test_scheduled_attempt_is_not_equivalent_to_success(self):
        first = state()
        self.assertFalse(any(v['synced_again'] for v in release.calendar_results(first, first).values()))
        second = state(121601, failed='bls')
        result = release.calendar_results(first, second)
        self.assertTrue(all(v['synced_again'] for v in result.values()))
        self.assertFalse(result['bls']['healthy'])
        self.assertTrue(result['nbs']['healthy'])
        self.assertFalse(release.calendar_results(first, {'sources': {}})['bls']['healthy'])

    def test_restart_must_preserve_events_drafts_and_cursors(self):
        release.verify_restart(state(), state())
        for kind in ('events_sha256', 'drafts', 'revisions', 'sources'):
            changed = deepcopy(state())
            changed[kind] = {} if kind == 'sources' else 'changed'
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                release.verify_restart(state(), changed)

    def test_missing_or_changed_oom_counters_do_not_get_treated_as_zero(self):
        release.check_sample(row(), row())
        for key, value in [('host_oom_kill', 20), ('host_oom_kill', 0), ('trend_oom_kill', 1),
                           ('available', 63*release.MIB), ('finevent_current', 97*release.MIB)]:
            changed = {**row(), key: value}
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                release.check_sample(changed, row())

    def test_rss_uses_bytes_returned_by_proc_reader(self):
        group = {'memory.max': 96*release.MIB, 'memory.swap.max': 0,
                 'memory.current': 30*release.MIB, 'memory.peak': 40*release.MIB}
        with patch.object(release.recovery, 'sample', return_value=row()), \
             patch.object(release.recovery.diagnostic, 'cgroup_memory', return_value=group), \
             patch.object(release.recovery.diagnostic, 'read_numbers', return_value={'VmRSS': 27*release.MIB}):
            result = release.sample(target(), 5)
        self.assertEqual(result['finevent_rss'], 27*release.MIB)

    def test_wal_snapshot_and_offline_restore_preserve_calendar_and_ledger(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source, backup, restored = folder/'source.db', folder/'backup.db', folder/'restore.db'
            with closing(sqlite3.connect(source)) as conn:
                conn.executescript("PRAGMA journal_mode=WAL; CREATE TABLE calendar_events(id TEXT,payload TEXT);"
                                   "CREATE TABLE ai_probe_runs(id TEXT,reserved_nano INTEGER);"
                                   "INSERT INTO calendar_events VALUES('event','private notice');"
                                   "INSERT INTO ai_probe_runs VALUES('charge',20000000);")
                release.snapshot(source, backup)
            release.snapshot(backup, restored)
            with closing(release.readonly(restored)) as conn:
                self.assertEqual(conn.execute('SELECT reserved_nano FROM ai_probe_runs').fetchone()[0], 20000000)
                self.assertEqual(conn.execute('SELECT payload FROM calendar_events').fetchone()[0], 'private notice')
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute('DELETE FROM calendar_events')

    def test_calendar_report_does_not_export_private_business_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'state.db'
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript("CREATE TABLE calendar_sources(id TEXT,last_attempt INTEGER);"
                                   "CREATE TABLE calendar_events(id TEXT,payload TEXT);"
                                   "CREATE TABLE calendar_revisions(id TEXT);CREATE TABLE fund_drafts(id TEXT);"
                                   "INSERT INTO calendar_events VALUES('id','private notice');")
            result = release.calendar_state(path)
            self.assertEqual(result['events'], 1)
            self.assertNotIn('private notice', json.dumps(result))

    def test_rollback_uses_latest_data_instead_of_the_predeployment_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            root, data, backup, operation = [folder/name for name in ('root','data','backup','operation')]
            for path in (root, data, backup, operation): path.mkdir()
            # The pointer is mocked to work on Windows without symlink privileges.
            current = root/'current'
            state_data = {'previous': 'old-release', 'files': [], 'timer_active': True}
            (backup/'state.json').write_text(json.dumps(state_data))
            with closing(sqlite3.connect(data/'finevent.db')) as conn:
                conn.executescript("CREATE TABLE events(value TEXT);INSERT INTO events VALUES('new user event');")
            report = {'deployment': {'backup': str(backup), 'release': str(current.resolve())}}
            def restore(check_dir):
                with closing(release.readonly(check_dir/'database.db')) as conn:
                    self.assertEqual(conn.execute('SELECT value FROM events').fetchone()[0], 'new user event')
                self.assertEqual(json.loads((check_dir/'state.json').read_text()), state_data)
                return {'previous_release': 'old-release'}
            with patch.object(release, 'ROOT', root), patch.object(release, 'DATA', data), \
                 patch.object(release.recovery, 'command') as command, \
                 patch.object(release.installer, 'rollback', side_effect=restore), \
                 patch.object(release.installer, 'wait_for_https'):
                result = release.rollback_current(operation, report)
            self.assertEqual(command.call_args_list[0].args, ('systemctl', 'stop', 'finevent-backup.timer', 'finevent.service'))
            self.assertTrue(result['latest_data_preserved'])

    def test_wrong_active_release_is_never_rolled_back(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(release.installer, 'rollback') as rollback:
            with self.assertRaises(RuntimeError):
                release.rollback_current(Path(temporary), {'deployment': {'backup': temporary, 'release': 'unrelated'}})
            rollback.assert_not_called()

    def test_summary_separates_sampled_and_kernel_peak(self):
        result = release.resource_summary([row(), {**row(), 'at': 6}], row())
        self.assertEqual(result['finevent_current_peak_mib'], 35)
        self.assertEqual(result['finevent_kernel_peak_mib'], 42)
        self.assertEqual(result['finevent_rss_peak_mib'], 28)
        self.assertEqual(result['max_sample_interval_seconds'], 5)

    def test_preflight_failure_never_invokes_installer(self):
        fake_lock = SimpleNamespace(flock=Mock(), LOCK_EX=1, LOCK_NB=2)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report = {'id': 'test', 'status': 'queued'}
            with patch.dict('sys.modules', {'fcntl': fake_lock}), patch.object(release, 'LOCK', directory/'lock'), \
                 patch.object(release, 'preflight', side_effect=RuntimeError('gate failed')), \
                 patch.object(release.installer, 'install') as install, \
                 patch.object(release, 'rollback_current') as rollback:
                with self.assertRaisesRegex(RuntimeError, 'gate failed'):
                    release.run(directory, report)
            install.assert_not_called()
            rollback.assert_not_called()
            result = json.loads((directory/'report.json').read_text())
            self.assertEqual(result['status'], 'incomplete')
            self.assertEqual(result['stage'], 'preflight')

    def test_predecessor_failure_is_saved_before_any_deployment(self):
        fake_lock = SimpleNamespace(flock=Mock(), LOCK_EX=1, LOCK_NB=2)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report = {'id': 'test', 'status': 'queued', 'after_review': 'previous'}
            with patch.dict('sys.modules', {'fcntl': fake_lock}), patch.object(release, 'LOCK', directory/'lock'), \
                 patch.object(release, 'review_previous', side_effect=RuntimeError('previous observer active')), \
                 patch.object(release, 'preflight') as preflight, patch.object(release.installer, 'install') as install:
                with self.assertRaisesRegex(RuntimeError, 'previous observer active'):
                    release.run(directory, report)
            preflight.assert_not_called()
            install.assert_not_called()
            saved = json.loads((directory/'report.json').read_text())
            self.assertEqual(saved['status'], 'incomplete')
            self.assertEqual(saved['after_review'], 'previous')

    def test_failure_after_install_rolls_back_and_retains_original_evidence(self):
        fake_lock = SimpleNamespace(flock=Mock(), LOCK_EX=1, LOCK_NB=2)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report = {'id': 'test', 'status': 'queued'}
            def install(*, evidence_callback):
                evidence_callback(directory/'deployment')
                return directory/'deployment'
            with patch.dict('sys.modules', {'fcntl': fake_lock}), patch.object(release, 'LOCK', directory/'lock'), \
                 patch.object(release, 'preflight', return_value={'container': target()}), \
                 patch.object(release.installer, 'install', side_effect=install), \
                 patch.object(release, 'snapshot', return_value='snapshot-sha'), \
                 patch.object(release, 'target', return_value=target()), \
                 patch.object(release, 'parent_metrics', return_value=parent()), \
                 patch.object(release, 'wait_initial_sources', side_effect=RuntimeError('memory pressure')), \
                 patch.object(release, 'rollback_current', return_value={'status': 'restored', 'latest_data_preserved': True}) as rollback:
                with self.assertRaisesRegex(RuntimeError, 'memory pressure'):
                    release.run(directory, report)
            rollback.assert_called_once()
            result = json.loads((directory/'report.json').read_text())
            self.assertEqual(result['status'], 'incomplete')
            self.assertEqual(result['installer_evidence'], str(directory/'deployment'))
            self.assertEqual(result['rollback']['status'], 'restored')
            self.assertIn('rollback_command', result['deployment'])


if __name__ == '__main__':
    unittest.main()
