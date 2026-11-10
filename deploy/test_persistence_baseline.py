from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import calendar_release as calendar
import persistence_baseline as baseline
import test_calendar_release as fixtures

BASELINE = 'persistence-check-20261110T000000Z-11111111'
FAILED = 'calendar-release-20261110T000000Z-22222222'
PREVIOUS = 'calendar-release-20261101T000000Z-33333333'


def stamp(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def evidence():
    return {'id': BASELINE, 'status': 'completed_for_review', 'stage': 'completed', 'basis': {'id': calendar.PERSIST_ID},
            'history_scope': baseline.SCOPE, 'observation_started': stamp(100000), 'observation_ended': stamp(103900), 'finished_at': stamp(103900),
            'resources': {**fixtures.evidence()['resources'], 'finevent_current_peak_mib': 40},
            'trendradar': {'status': 'passed', 'distinct_cycles': 2}, 'health': fixtures.evidence()['health'],
            'first_sample_at': 100000, 'last_sample_at': 103900}


class BaselineTests(unittest.TestCase):
    def test_fresh_evidence_requires_full_window_resources_sources_and_origin(self):
        good = evidence()
        baseline.validate(good, BASELINE, 103901, good['basis'])
        for kind in ('expired', 'future', 'short', 'samples', 'first_gap', 'last_gap', 'gap', 'oom', 'memory', 'source', 'cycles', 'https', 'basis', 'failed', 'scope'):
            changed = deepcopy(good)
            now = 103901
            if kind == 'expired': now += 7*86400
            if kind == 'future': now = 103899
            if kind == 'short': changed['observation_started'] = stamp(100001)
            if kind == 'samples': changed['resources']['samples'] = 10
            if kind == 'first_gap': changed['first_sample_at'] += 16
            if kind == 'last_gap': changed['last_sample_at'] -= 16
            if kind == 'gap': changed['resources']['max_sample_interval_seconds'] = 16
            if kind == 'oom': changed['resources']['host_oom_kill_delta'] = 1
            if kind == 'memory': changed['resources']['host_available_min_mib'] = 63
            if kind == 'source': changed['trendradar']['status'] = 'needs_review'
            if kind == 'cycles': changed['trendradar']['distinct_cycles'] = 1
            if kind == 'https': changed['health']['/finevent/healthz']['http_status'] = '502'
            if kind == 'basis': changed['basis'] = {'id': 'other'}
            if kind == 'failed': changed['failure'] = 'failed'
            if kind == 'scope': changed['history_scope'] = 'all_history'
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                baseline.validate(changed, BASELINE, now, good['basis'])

    def test_readonly_observer_completes_without_installer_or_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            now = [100000]
            def sample(*_):
                now[0] += 5
                return {**fixtures.row(), 'at': now[0]}
            current = {'container': fixtures.target(), 'finevent_pid': 456, 'baseline': fixtures.row(),
                       'previous_release': 'accepted', 'config_sha256': 'a'*64}
            report = {'id': BASELINE, 'status': 'queued', 'stage': 'queued'}
            fake_lock = SimpleNamespace(flock=Mock(), LOCK_EX=1, LOCK_NB=2)
            with patch.dict('sys.modules', {'fcntl': fake_lock}), patch.object(calendar, 'LOCK', directory/'lock'), \
                 patch.object(baseline, 'original', return_value=({}, {'id': calendar.PERSIST_ID})), \
                 patch.object(calendar, 'inspect_persistence', return_value=current), \
                 patch.object(calendar.recovery, 'process_identity', return_value=1), \
                 patch.object(calendar.recovery, 'validate_live_processes'), patch.object(calendar, 'parent_metrics', return_value=fixtures.parent()), \
                 patch.object(calendar, 'sample', side_effect=sample), patch.object(baseline.time, 'time', side_effect=lambda: now[0]), \
                 patch.object(baseline.time, 'monotonic', side_effect=lambda: now[0]), patch.object(baseline.time, 'sleep'), \
                 patch.object(calendar, 'stamp', side_effect=lambda: stamp(now[0])), \
                 patch.object(calendar, 'trend_outputs', return_value={'status': 'passed', 'distinct_cycles': 2}), \
                 patch.object(calendar.recovery, 'public_health', return_value=fixtures.evidence()['health']), \
                 patch.object(calendar.installer, 'install') as install, patch.object(calendar.recovery, 'command') as command:
                baseline.run(directory, report)
            install.assert_not_called()
            command.assert_not_called()
            self.assertEqual(report['status'], 'completed_for_review')
            self.assertEqual(report['history_scope'], baseline.SCOPE)
            self.assertEqual(len((directory/'samples.jsonl').read_text().splitlines()), 780)
            self.assertFalse((directory/'before.db').exists())

    def test_stopped_or_failed_baseline_cannot_be_resumed(self):
        with self.assertRaisesRegex(RuntimeError, 'already started'):
            baseline.run(Path('unused'), {'status': 'incomplete'})

    def test_digest_binding_rejects_changed_report(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(calendar, 'BACKUPS', Path(temporary)):
            folder = Path(temporary)/BASELINE
            folder.mkdir()
            (folder/'report.json').write_text(json.dumps(evidence()))
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                baseline.load(BASELINE, '0'*64)

    def test_renewed_evidence_still_requires_complete_resource_history_after_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/calendar.PERSIST_ID
            source.mkdir()
            accepted = fixtures.evidence()
            accepted['resources']['baseline_counters'] = {'finevent_oom_kill': 0}
            (source/'report.json').write_text(json.dumps(accepted))
            with closing(sqlite3.connect(root/'finevent.db')) as conn:
                conn.execute('CREATE TABLE resources(at INTEGER,available INTEGER,oom_kills INTEGER)')
                conn.executemany('INSERT INTO resources VALUES(?,?,?)', [(103900, 128*calendar.MIB, 0), (104000, 128*calendar.MIB, 0)])
                conn.commit()
            with patch.object(calendar, 'DATA', root), patch.object(calendar, 'BACKUPS', root), \
                 patch.object(baseline, 'load', return_value=evidence()), patch.object(baseline, 'validate_current'), \
                 patch.object(calendar, 'inspect_persistence', return_value={}), patch.object(calendar, 'snapshot', return_value='snapshot'), \
                 patch.object(calendar.time, 'time', return_value=104001):
                passed = calendar.preflight(root, BASELINE, 'a'*64)
                self.assertEqual(passed['history_samples'], 2)
                with closing(sqlite3.connect(root/'finevent.db')) as conn:
                    conn.execute('UPDATE resources SET at=103000 WHERE at=103900')
                    conn.execute('UPDATE resources SET at=104100 WHERE at=104000')
                    conn.commit()
                with self.assertRaisesRegex(RuntimeError, 'history'):
                    calendar.preflight(root, BASELINE, 'a'*64)

    def test_current_configuration_release_or_process_change_invalidates_renewal(self):
        current = {'container': fixtures.target(), 'finevent_pid': 456, 'previous_release': 'accepted', 'config_sha256': 'a'*64}
        report = {'initial': deepcopy(current), 'process_identities': [1, 1]}
        with patch.object(calendar, 'parent_metrics', return_value=fixtures.parent()), patch.object(calendar.recovery, 'process_identity', return_value=1):
            baseline.validate_current(report, current)
            for key in ('finevent_pid', 'previous_release', 'config_sha256'):
                with self.subTest(key=key), self.assertRaises(RuntimeError):
                    baseline.validate_current(report, {**current, key: 'changed'})
        with patch.object(calendar, 'parent_metrics', return_value=fixtures.parent()), patch.object(calendar.recovery, 'process_identity', return_value=2):
            with self.assertRaisesRegex(RuntimeError, 'process identity'):
                baseline.validate_current(report, current)


class PreflightRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.folder = self.directory/FAILED
        self.folder.mkdir()
        self.previous = {'id': PREVIOUS, 'report_sha256': 'a'*64, 'trend_source_review': None}
        self.report = {'id': FAILED, 'status': 'incomplete', 'stage': 'preflight', 'failure': 'expired',
                       'finished_at': stamp(100000), 'after_review': PREVIOUS, 'previous_review': self.previous,
                       'verification_profile': 'news'}
        self.addCleanup(patch.stopall)
        patch.object(calendar, 'BACKUPS', self.directory).start()
        self.observer = patch.object(calendar, 'observer_state', return_value={'ActiveState': 'failed', 'MainPID': '0'}).start()
        self.review = patch.object(calendar, 'review_previous', return_value=self.previous).start()

    def write(self):
        raw = json.dumps(self.report).encode()
        (self.folder/'report.json').write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def test_recovery_binds_immutable_failure_and_rechecks_original_acceptance(self):
        checksum = self.write()
        result = calendar.review_failed_preflight(FAILED, checksum, PREVIOUS)
        self.assertEqual(result['previous_review'], self.previous)
        self.assertEqual(calendar.recovery.sha(self.folder/'report.json'), checksum)
        self.review.assert_called_once_with(PREVIOUS)

    def test_unsafe_or_ambiguous_failures_remain_blocked(self):
        for kind in ('install', 'installer_marker', 'deployment', 'rollback', 'replacement', 'ai', 'wrong_parent', 'changed_review', 'unfinished', 'active', 'pid', 'hash'):
            original = deepcopy(self.report)
            if kind == 'install': self.report['stage'] = 'install'
            if kind == 'installer_marker': self.report['installer_evidence'] = ''
            if kind in ('deployment', 'rollback'): self.report[kind] = {}
            if kind == 'replacement': self.report['replace_observation'] = PREVIOUS
            if kind == 'ai': self.report['verification_profile'] = 'ai'
            if kind == 'wrong_parent': self.report['after_review'] = FAILED
            if kind == 'changed_review': self.report['previous_review'] = {'id': PREVIOUS}
            if kind == 'unfinished': self.report.pop('finished_at')
            if kind == 'active': self.observer.return_value = {'ActiveState': 'active', 'MainPID': '0'}
            if kind == 'pid': self.observer.return_value = {'ActiveState': 'failed', 'MainPID': '12'}
            checksum = self.write()
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                calendar.review_failed_preflight(FAILED, '0'*64 if kind == 'hash' else checksum, PREVIOUS)
            self.report = original
            self.observer.return_value = {'ActiveState': 'failed', 'MainPID': '0'}

    def test_recovery_rejection_occurs_before_install_and_preserves_failed_report(self):
        checksum = self.write()
        self.observer.return_value = {'ActiveState': 'active', 'MainPID': '7'}
        queued = {'id': 'new', 'status': 'queued', 'after_review': PREVIOUS,
                  'recover_preflight': FAILED, 'failed_report_sha256': checksum}
        fake_lock = SimpleNamespace(flock=Mock(), LOCK_EX=1, LOCK_NB=2)
        with patch.dict('sys.modules', {'fcntl': fake_lock}), patch.object(calendar, 'LOCK', self.directory/'lock'), \
             patch.object(calendar.installer, 'install') as install:
            with self.assertRaises(RuntimeError):
                calendar.run(self.directory, queued)
        install.assert_not_called()
        self.assertEqual(calendar.recovery.sha(self.folder/'report.json'), checksum)


if __name__ == '__main__':
    unittest.main()
