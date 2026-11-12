from copy import deepcopy
import hashlib
import json
import sqlite3
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import calendar_release as release
from test_calendar_release import evidence, parent, row, state, target


OLD = 'calendar-release-20261031T080853Z-0d262c79'
NEW = 'calendar-release-20261031T090000Z-12345678'


def observing(root):
    return {**evidence(), 'id': OLD, 'status': 'observing', 'stage': 'scheduled_sync_and_resources',
            'unit': 'finevent-'+OLD+'.service', 'deployment': {'release': str((root/'current').resolve())},
            'preflight': {'status': 'passed', 'container': target(), 'baseline': row(), 'config_sha256': 'config'},
            'restart': {'status': 'passed', 'after_pid': 123}, 'backup_verification': {'status': 'passed'},
            'resources': release.resource_summary([row()], row()), 'planned_end': 'original deadline',
            'calendar': {'nyfed': {'latest': {'error': 'original source failure'}}}}


class ReplacementTests(unittest.TestCase):
    def test_only_named_healthy_observation_can_be_interrupted(self):
        valid = observing(Path('test'))
        active = valid['deployment']['release']
        release.validate_interruptible(valid, OLD, active)
        for kind in ('other_id', 'other_unit', 'installing', 'failed', 'rollback', 'restart', 'oom', 'low_memory', 'gap', 'release'):
            changed = deepcopy(valid)
            if kind == 'other_id': changed['id'] = NEW
            if kind == 'other_unit': changed['unit'] = 'finevent.service'
            if kind == 'installing': changed['stage'] = 'install'
            if kind == 'failed': changed['failure'] = 'unexpected'
            if kind == 'rollback': changed['rollback'] = {'status': 'restored'}
            if kind == 'restart': changed['restart']['status'] = 'pending'
            if kind == 'oom': changed['resources']['host_oom_kill_delta'] = 1
            if kind == 'low_memory': changed['resources']['host_available_min_mib'] = 63
            if kind == 'gap': changed['resources']['max_sample_interval_seconds'] = 16
            if kind == 'release': changed['deployment']['release'] = 'other'
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                release.validate_interruptible(changed, OLD, active)

    def test_preserves_original_evidence_and_stops_only_observer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old, new = root/OLD, root/NEW
            old.mkdir(); new.mkdir()
            previous = observing(root)
            raw = json.dumps(previous, indent=2).encode()
            (old/'report.json').write_bytes(raw)
            report = {'id': NEW, 'replace_observation': OLD}
            with patch.object(release, 'ROOT', root), patch.object(release, 'BACKUPS', root), \
                 patch.object(release, 'validate_live_predecessor'), \
                 patch.object(release.recovery, 'public_health', return_value=evidence()['health']), \
                 patch.object(release, 'observer_state', side_effect=[{'ActiveState': 'active', 'MainPID': '1234'}, {'ActiveState': 'inactive', 'MainPID': '0'}]), \
                 patch.object(release.recovery, 'command') as command:
                release.prepare_replacement(new, report)
                result = release.finish_replacement(new, report)
            command.assert_called_once_with('systemctl', 'stop', 'finevent-'+OLD+'.service', timeout=40)
            self.assertEqual((new/'previous-observation-before-stop.json').read_bytes(), raw)
            self.assertEqual(report['replacement']['evidence_sha256'], hashlib.sha256(raw).hexdigest())
            final = json.loads((old/'report.json').read_text())
            self.assertEqual(final['status'], 'interrupted_for_repair')
            self.assertEqual(final['observation_acceptance'], 'incomplete')
            for key in ('resources', 'calendar', 'planned_end', 'deployment'):
                self.assertEqual(final[key], previous[key])
            self.assertFalse(result['observation_complete'])

    def test_stale_checkpoint_and_live_fault_do_not_stop_any_service(self):
        for kind in ('stale', 'live_fault', 'unhealthy'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                old, new = root/OLD, root/NEW
                old.mkdir(); new.mkdir()
                (old/'report.json').write_text(json.dumps(observing(root)))
                if kind == 'stale': os.utime(old/'report.json', (1, 1))
                with patch.object(release, 'ROOT', root), patch.object(release, 'BACKUPS', root), \
                     patch.object(release, 'validate_live_predecessor', side_effect=RuntimeError('live fault') if kind == 'live_fault' else None), \
                     patch.object(release.recovery, 'public_health', return_value={} if kind == 'unhealthy' else evidence()['health']), \
                     patch.object(release.recovery, 'command') as command:
                    with self.assertRaises(RuntimeError):
                        release.prepare_replacement(new, {'id': NEW, 'replace_observation': OLD})
                command.assert_not_called()
                self.assertEqual(json.loads((old/'report.json').read_text())['status'], 'observing')

    def test_failed_or_running_worker_is_not_relabelled_as_interrupted(self):
        for kind in ('running', 'failed_report', 'no_stop'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                old, new = root/OLD, root/NEW
                old.mkdir(); new.mkdir()
                previous = observing(root)
                if kind == 'failed_report': previous.update(status='incomplete', failure='pressure')
                (old/'report.json').write_text(json.dumps(previous))
                report = {'id': NEW, 'replace_observation': OLD, 'replacement': {'observer_stopped': kind != 'no_stop'}}
                with patch.object(release, 'ROOT', root), patch.object(release, 'BACKUPS', root), \
                     patch.object(release, 'validate_live_predecessor'), \
                     patch.object(release, 'observer_state', return_value={'ActiveState': 'active' if kind == 'running' else 'inactive', 'MainPID': '0'}):
                    with self.assertRaises(RuntimeError): release.finish_replacement(new, report)
                self.assertEqual(json.loads((old/'report.json').read_text()), previous)

    def test_completed_race_preserves_report_and_requires_completed_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old, new = root/OLD, root/NEW
            old.mkdir(); new.mkdir()
            previous = {**observing(root), 'stage': 'completed', 'status': 'needs_review'}
            raw = json.dumps(previous)
            (old/'report.json').write_text(raw)
            with patch.object(release, 'ROOT', root), patch.object(release, 'BACKUPS', root), \
                 patch.object(release, 'validate_live_predecessor'), patch.object(release, 'validate_previous') as review, \
                 patch.object(release, 'observer_state', return_value={'ActiveState': 'inactive', 'MainPID': '0'}):
                result = release.finish_replacement(new, {'id': NEW, 'replace_observation': OLD})
            review.assert_called_once()
            self.assertTrue(result['observation_complete'])
            self.assertEqual((old/'report.json').read_text(), raw)

    def test_live_identity_change_blocks_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous = observing(root)
            for kind in ('pid', 'config', 'oom', 'container'):
                current = target()
                if kind == 'container': current['id'] = 'other'
                with self.subTest(kind=kind), patch.object(release, 'ROOT', root), \
                     patch.object(release, 'service_pid', return_value=999 if kind == 'pid' else 123), \
                     patch.object(release, 'target', return_value=current), patch.object(release, 'parent_metrics', return_value=parent()), \
                     patch.object(release, 'sample', return_value={**row(), 'host_oom_kill': 20 if kind == 'oom' else 19}), \
                     patch.object(release.recovery, 'sha', return_value='other' if kind == 'config' else 'config'):
                    with self.assertRaises(RuntimeError): release.validate_live_predecessor(previous)

    def test_failed_stop_prevents_install_and_saves_failure(self):
        fake_lock = SimpleNamespace(flock=Mock(), LOCK_EX=1, LOCK_NB=2)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report = {'id': NEW, 'status': 'queued', 'replace_observation': OLD}
            with patch.dict('sys.modules', {'fcntl': fake_lock}), patch.object(release, 'LOCK', directory/'lock'), \
                 patch.object(release, 'prepare_replacement', side_effect=RuntimeError('stop failed')), \
                 patch.object(release, 'preflight') as preflight, patch.object(release.installer, 'install') as install:
                with self.assertRaisesRegex(RuntimeError, 'stop failed'): release.run(directory, report)
            preflight.assert_not_called(); install.assert_not_called()
            self.assertEqual(json.loads((directory/'report.json').read_text())['status'], 'incomplete')

    def test_initial_sync_waits_for_claimed_retry_to_finish(self):
        pending, final = state(), state(100002)
        pending['repairs'] = [{'source': 'nyfed', 'requested_at': 100001}]
        final['repairs'] = pending['repairs']
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(release.time, 'monotonic', side_effect=[0, 1, 2]), patch.object(release.time, 'sleep') as sleep, \
             patch.object(release, 'service_pid', return_value=123), patch.object(release, 'sample', return_value=row()), \
             patch.object(release, 'calendar_state', side_effect=[pending, final]):
            self.assertEqual(release.wait_initial_sources(Path(temporary), {}, target()), final)
            sleep.assert_called_once_with(5)

    def test_transition_lock_is_released_before_long_observation(self):
        locks = []
        fake_lock = SimpleNamespace(flock=lambda handle, flags: locks.append(handle), LOCK_EX=1, LOCK_NB=2)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory/'backup').mkdir()
            sqlite3.connect(directory/'backup/database.db').close()
            initial = {**state(), 'reminders': {'schema': 'present', 'enabled': False, 'watches': 0, 'jobs': 0}}
            def observe(*args):
                self.assertTrue(locks[0].closed)
                self.assertFalse(locks[1].closed)
            def prepare(*args):
                self.assertEqual(len(locks), 1)
            def finish(*args):
                self.assertEqual(len(locks), 2)
                return {'observation_complete': False}
            with patch.dict('sys.modules', {'fcntl': fake_lock}), patch.object(release, 'LOCK', directory/'lock'), \
                 patch.object(release, 'prepare_replacement', side_effect=prepare), patch.object(release, 'finish_replacement', side_effect=finish), \
                 patch.object(release, 'preflight', return_value={'container': target()}), \
                 patch.object(release.installer, 'install', return_value=directory/'backup'), \
                 patch.object(release, 'snapshot', return_value='sha'), patch.object(release, 'target', return_value=target()), \
                 patch.object(release, 'parent_metrics', return_value=parent()), \
                 patch.object(release, 'wait_initial_sources', return_value=initial), patch.object(release, 'restart_check', return_value={'status': 'passed'}), \
                 patch.object(release, 'observe', side_effect=observe):
                release.run(directory, {'id': NEW, 'status': 'queued', 'replace_observation': OLD})
            self.assertTrue(all(lock.closed for lock in locks))

    def test_status_wait_returns_ready_report_without_redeploying(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report = {'status': 'deploying', 'unit': 'observer'}
            (directory/'report.json').write_text(json.dumps(report))
            def ready(*args):
                (directory/'report.json').write_text(json.dumps({**report, 'status': 'observing'}))
            with patch.object(release.time, 'monotonic', side_effect=[0, 1]), patch.object(release.time, 'sleep', side_effect=ready), \
                 patch.object(release.recovery, 'command', return_value=SimpleNamespace(stdout='ActiveState=active\n')) as command:
                result = release.export_status(directory, 180)
            self.assertEqual(result['status'], 'observing')
            self.assertEqual(command.call_args.args[:2], ('systemctl', 'show'))


if __name__ == '__main__':
    unittest.main()
