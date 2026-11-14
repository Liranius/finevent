import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import resume_calendar_acceptance as resume
from test_calendar_release import row


class AcceptanceRecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.args = SimpleNamespace(run_id='calendar-release-20261113T010000Z-12345678',
                                    commit='a'*40, worker=False)
        self.directory = self.root/self.args.run_id
        self.directory.mkdir()
        self.controller = Mock(state=self.root, backups=self.root)
        self.report = {'id': self.args.run_id, 'unit': 'finevent-'+self.args.run_id+'.service',
                       'status': 'deploying', 'stage': 'install', 'verification_profile': 'calendar',
                       'preflight': {'status': 'passed', 'container': {}, 'baseline': row()}}
        self.raw = json.dumps(self.report).encode()
        (self.directory/'report.json').write_bytes(self.raw)
        self.args.report_sha256 = hashlib.sha256(self.raw).hexdigest()
        self.oom = {'ActiveState': 'failed', 'MainPID': '0', 'Result': 'oom-kill', 'ExecMainCode': '2',
                    'ExecMainStatus': '9', 'MemoryMax': str(64*1024**2)}
        self.start_patch(patch.object(resume.calendar, 'LOCK', self.root/'lock'))
        self.start_patch(patch.dict('sys.modules', {'fcntl': Mock(LOCK_EX=2, LOCK_NB=4)}))
        self.start_patch(patch.object(resume.calendar, 'target'))
        self.start_patch(patch.object(resume.calendar, 'parent_metrics'))
        self.start_patch(patch.object(resume.calendar, 'validate_target'))
        self.start_patch(patch.object(resume.calendar, 'service_pid', return_value=123))
        self.start_patch(patch.object(resume.calendar, 'sample', return_value=row()))

    def start_patch(self, patcher):
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_rejects_changed_report_and_unrelated_death_before_launch(self):
        command = self.start_patch(patch.object(resume.calendar.recovery, 'command'))
        with patch.object(resume, 'installed_backup'), patch.object(resume, 'unit_status', return_value={**self.oom, 'Result': 'timeout'}):
            with self.assertRaisesRegex(RuntimeError, 'confirmed OOM'):
                resume.resume(self.controller, self.args)
            self.args.report_sha256 = '0'*64
            with self.assertRaisesRegex(RuntimeError, 'report changed'):
                resume.resume(self.controller, self.args)
        command.assert_not_called()

    def test_launcher_preserves_evidence_and_only_worker_runs_acceptance(self):
        backup = self.directory/'frozen'
        self.start_patch(patch.object(resume, 'installed_backup', return_value=backup))
        command = self.start_patch(patch.object(resume.calendar.recovery, 'command'))
        self.start_patch(patch.object(resume.calendar.installer, 'install', side_effect=AssertionError('unexpected install')))
        with patch.object(resume, 'unit_status', return_value=self.oom):
            resume.resume(self.controller, self.args)
        self.assertEqual([c.args[0] for c in command.call_args_list], ['systemctl', 'systemd-run'])
        self.assertIn('--unit='+self.report['unit'], command.call_args.args)
        self.assertEqual((self.directory/'acceptance-before-oom-recovery.json').read_bytes(), self.raw)
        self.assertEqual((self.directory/'report.json').read_bytes(), self.raw)
        sample = {**row(), 'host_oom_kill': row()['host_oom_kill']+1}
        self.args.worker = True
        def verify(directory, report, received_backup, transition):
            self.assertEqual(received_backup, backup)
            self.assertEqual(report['preflight'], self.report['preflight'])
            self.assertEqual(resume.calendar.observation_baseline(report), sample)
            report.update(stage='completed', status='completed_with_source_limits_for_review')
        with patch.object(resume, 'unit_status', return_value={'MainPID': str(os.getpid())}), \
             patch.object(resume.calendar, 'sample', return_value=sample), \
             patch.object(resume.calendar, 'verify_installed', side_effect=verify) as verifier:
            resume.resume(self.controller, self.args)
            with self.assertRaisesRegex(RuntimeError, 'report changed'):
                resume.resume(self.controller, self.args)
            verifier.assert_called_once()
        self.assertEqual(resume.read(self.directory/'report.json')['stage'], 'completed')
        self.assertEqual((self.directory/'acceptance-before-oom-recovery.json').read_bytes(), self.raw)

    def test_recovery_rejects_another_release_or_wrong_stage(self):
        self.controller.latest.return_value = 'other'
        with self.assertRaisesRegex(RuntimeError, 'latest interrupted'):
            resume.installed_backup(self.controller, self.args.run_id, self.report, self.args.commit)
        self.controller.latest.return_value = self.args.run_id
        for change in ({'deployment': {}}, {'stage': 'restart_check'}, {'rollback': {}}, {'verification_profile': 'news'}):
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, 'latest interrupted'):
                resume.installed_backup(self.controller, self.args.run_id, {**self.report, **change}, self.args.commit)

    @unittest.skipUnless(os.name == 'posix', 'Requires symlinks and a Linux release layout')
    def test_installer_evidence_and_registered_content_must_match(self):
        def write(path, obj):
            path.write_text(json.dumps(obj))
        register = self.root/'operations'/self.args.run_id
        register.mkdir(parents=True)
        app = self.root/'app'
        active = app/'releases'/'installation'
        (active/'deploy').mkdir(parents=True)
        (active/'dist').mkdir()
        (app/'current').symlink_to(active)
        (active/'finevent').write_bytes(b'current binary')
        for relative in ('dist/index.html', 'deploy/finevent.service', 'deploy/install.py'):
            (active/relative).write_bytes(b'test')
        metadata = {'source_commit': self.args.commit, 'kind': 'deployment', 'generic_sha256': 'b'*64}
        write(active/'release.json', metadata)
        manifest = {p.relative_to(active).as_posix(): resume.calendar.recovery.sha(p) for p in active.rglob('*') if p.is_file()}
        write(active/'sha256.json', manifest)
        digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        write(register/'operation.json', {**metadata, 'id': self.args.run_id,
                                         'identity': {'profile': 'calendar', 'content_sha256': digest}})
        self.controller.backups = self.root
        self.controller.latest.return_value = self.args.run_id
        self.controller.operation_path.return_value = register
        backup = self.root/active.name
        backup.mkdir()
        (backup/'database.db').write_bytes(b'frozen')
        write(backup/'state.json', {'previous': 'old'})
        inner = {'status': 'verification_service_installed', 'release': str(active), 'backup': str(backup)}
        write(backup/'report.json', inner)
        (self.root/'config.json').write_text('{}')
        self.report.update(runner=str(register/'bundle/deploy/calendar_release.py'), installer_evidence=str(backup))
        self.report['preflight'].update(previous_release='old', config_sha256=resume.calendar.recovery.sha(self.root/'config.json'))
        with patch.object(resume.calendar, 'ROOT', app), patch.object(resume.calendar, 'DATA', self.root), \
             patch.object(resume.calendar, 'service_pid', return_value=123), patch.object(Path, 'samefile', return_value=True), \
             patch.object(resume.calendar.recovery, 'command'):
            self.assertEqual(resume.installed_backup(self.controller, self.args.run_id, self.report, self.args.commit), backup)
            write(backup/'report.json', {**inner, 'release': 'different'})
            with self.assertRaisesRegex(RuntimeError, 'installer evidence'):
                resume.installed_backup(self.controller, self.args.run_id, self.report, self.args.commit)
            write(backup/'report.json', inner)
            (active/'finevent').write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                resume.installed_backup(self.controller, self.args.run_id, self.report, self.args.commit)

    def test_only_accounts_for_known_worker_death(self):
        baseline = row()
        for key, delta in (('host_oom_kill', 2), ('host_oom_kill', -1), ('finevent_oom_kill', 1), ('trend_oom_kill', 1)):
            with self.subTest(key=key, delta=delta), self.assertRaises(RuntimeError):
                resume.recovery_baseline(self.report, {**baseline, key: baseline[key]+delta})
