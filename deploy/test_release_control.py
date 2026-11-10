import base64
from contextlib import nullcontext
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

import bootstrap_release as bootstrap
import release_control as control
import test_release_bundle as bundles

ROOT = Path(__file__).resolve().parents[1]
CLIENT = runpy.run_path(str(ROOT/'scripts/release-client.py'))
PREVIOUS = 'calendar-release-20261101T000000Z-11111111'
CURRENT = 'calendar-release-20261102T000000Z-22222222'


class BootstrapTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'Server filesystem installation is exercised by Linux CI')
    def test_install_is_repeatable_and_preserves_admin_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source'
            source.mkdir()
            for name in ('app', 'data', 'backups', 'root/.ssh', 'usr/local/lib', 'usr/local/sbin'):
                (root/name).mkdir(parents=True, exist_ok=True)
            config = {'appRoot': str(root/'app'), 'dataRoot': str(root/'data'), 'backupRoot': str(root/'backups')}
            (source/'config.json').write_text(json.dumps(config))
            for name in ('release_control.py', 'check-release.py'):
                (source/name).write_text('# trusted fixture')
            public = 'ssh-ed25519 '+base64.b64encode(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'+b'a'*32).decode()
            (source/'deploy.pub').write_text(public)
            keys = root/'root/.ssh/authorized_keys'
            original = '# admin key\nssh-ed25519 OTHER admin\n'
            keys.write_text(original)
            with patch.object(bootstrap.os, 'geteuid', return_value=0), patch.object(bootstrap.os, 'chown'):
                bootstrap.install(source, root)
                bootstrap.install(source, root)
            self.assertEqual(keys.read_text().count('finevent-release"'), 1)
            self.assertTrue(keys.read_text().startswith(original))
            self.assertEqual(len(list(keys.parent.glob('authorized_keys.before-*'))), 1)
            self.assertEqual((root/'usr/local/sbin/finevent-release').read_bytes(), bootstrap.WRAPPER)
            self.assertEqual((root/'usr/local/lib/finevent-release/config.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual(list((root/'app').iterdir()), [])

    def test_key_is_forced_and_idempotent_without_changing_existing_access(self):
        encoded = base64.b64encode(b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'+b'a'*32).decode()
        line = bootstrap.authorized_line('ssh-ed25519 '+encoded+' test')
        self.assertTrue(line.startswith('restrict,command="/usr/local/sbin/finevent-release" '))
        existing = '# existing admin access\nssh-ed25519 OTHER admin\n'
        merged = bootstrap.merge_key(existing, line)
        self.assertTrue(merged.startswith(existing))
        self.assertEqual(bootstrap.merge_key(merged, line), merged)
        with self.assertRaisesRegex(RuntimeError, 'another'):
            bootstrap.merge_key('ssh-ed25519 '+encoded+' admin\n', line)

    def test_invalid_key_and_injected_options_rejected(self):
        for key in ('ssh-rsa AAAA', 'ssh-ed25519 AAAA', 'command="sh" ssh-ed25519 AAAA'):
            with self.subTest(key=key), self.assertRaises((RuntimeError, ValueError)):
                bootstrap.authorized_line(key)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.backups = self.root/'backups'
        self.backups.mkdir()
        (self.root/'app/current').mkdir(parents=True)
        (self.root/'data').mkdir()
        (self.root/'data/config.json').write_text('{}')
        self.controller = control.Controller({'backupRoot': str(self.backups), 'appRoot': str(self.root/'app'), 'dataRoot': str(self.root/'data')}, self.root/'state', checker=runpy.run_path(str(ROOT/'scripts/check-release.py'))['check_archive'])
        self.addCleanup(patch.stopall)
        if os.name != 'posix':
            patch.object(control, 'exclusive', lambda _: nullcontext()).start()
        patch.object(self.controller, 'system_status', return_value={'ActiveState': 'inactive'}).start()
        self.report(PREVIOUS)
        self.archive = self.root/'release.tar.gz'
        files = bundles.fixture()
        del files['deployment-template.json']
        files['release.json'] = json.dumps({'schema': 1, 'kind': 'deployment', 'target': 'linux/amd64', 'source_commit': 'a'*40, 'source_dirty': False, 'generic_sha256': 'b'*64}).encode()
        files['deploy/calendar_release.py'] = b'# check-predecessor fixture'
        files['deploy/news_release.py'] = b'# fixture'
        files['deploy/persistence_baseline.py'] = b'# read-only fixture'
        bundles.ReleaseBundleTests.write(self, files)
        files = self.controller.checker(self.archive)
        self.request = {'action': 'deploy', 'id': CURRENT, 'profile': 'news', 'after_review': PREVIOUS,
                        'replace_observation': None, 'archive_sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest(), 'content_sha256': control.content_digest(files)}
        self.launch = patch.object(self.controller, 'launch', side_effect=lambda _bundle, request, _log: self.report(request['id'], 'queued')).start()
        self.predecessor = patch.object(self.controller, 'check_predecessor').start()

    def report(self, run_id, status='completed_for_review'):
        directory = self.backups/run_id
        directory.mkdir(exist_ok=True)
        control.save(directory/'report.json', {'id': run_id, 'created_at': '2026-11-01T08:00:00+08:00', 'status': status, 'stage': 'completed',
                                               'deployment': {'release': '/private/server/path'}, 'failure': None})

    def send(self, request=None, data=None):
        return self.controller.deploy(request or self.request, io.BytesIO(self.archive.read_bytes() if data is None else data))

    def test_registration_and_retry_launch_once(self):
        first = self.send()
        again = self.send(data=b'')
        self.assertEqual(first['identity'], again['identity'])
        self.launch.assert_called_once()
        self.assertEqual(self.controller.latest(), CURRENT)

    def test_baseline_registration_is_idempotent_and_does_not_replace_latest_release(self):
        request = {key: value for key, value in self.request.items() if key in ('archive_sha256', 'content_sha256')}
        request.update(action='baseline', id='persistence-check-20261110T000000Z-11111111')
        first = self.send(request)
        again = self.send(request, data=b'')
        self.assertEqual(first['identity'], again['identity'])
        self.assertEqual(self.controller.latest(), PREVIOUS)
        self.launch.assert_called_once()
        self.predecessor.assert_not_called()

    def test_recovery_requires_latest_registered_failure_and_binds_all_evidence(self):
        self.send()
        next_id = 'calendar-release-20261110T000000Z-11111111'
        request = {**self.request, 'id': next_id, 'recover_preflight': CURRENT, 'failed_report_sha256': 'd'*64,
                   'baseline_id': 'persistence-check-20261110T000000Z-22222222', 'baseline_sha256': 'e'*64}
        result = self.send(request)
        self.assertEqual(result['identity']['failed_report_sha256'], 'd'*64)
        self.predecessor.assert_called_with(unittest.mock.ANY, request)
        with self.assertRaisesRegex(RuntimeError, 'different'):
            self.send({**request, 'baseline_sha256': 'f'*64})
        self.assertEqual(self.launch.call_count, 2)

    def test_baseline_and_recovery_options_are_validated_before_transport(self):
        bad = ({'baseline_id': '../../x', 'baseline_sha256': 'a'*64}, {'baseline_id': 'persistence-check-20261110T000000Z-11111111'},
               {'recover_preflight': PREVIOUS}, {'failed_report_sha256': 'a'*64},
               {'recover_preflight': CURRENT, 'failed_report_sha256': 'a'*64})
        for change in bad:
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                control.parse_request('finevent-release '+base64.b64encode(json.dumps({**self.request, **change}).encode()).decode())

    def test_baseline_launch_uses_only_readonly_runner(self):
        request = {'action': 'baseline', 'id': 'persistence-check-20261110T000000Z-11111111'}
        with patch.object(control.subprocess, 'run', return_value=unittest.mock.Mock(returncode=0)) as command:
            control.Controller.launch(self.controller, self.root/'bundle', request, self.root/'launch.log')
        args = command.call_args.args[0]
        self.assertTrue(args[2].endswith('persistence_baseline.py'))
        self.assertNotIn('--news', args)

    def test_unknown_launch_outcome_never_relaunches(self):
        self.launch.side_effect = TimeoutError('acknowledgement lost')
        with self.assertRaises(TimeoutError):
            self.send()
        result = self.send(data=b'')
        self.assertEqual(result['status'], 'needs_recovery')
        self.launch.assert_called_once()

    def test_same_id_different_payload_or_predecessor_rejected(self):
        self.send()
        for key, value in (('content_sha256', 'c'*64), ('after_review', CURRENT), ('profile', 'calendar')):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'different'):
                self.send({**self.request, key: value})
        self.launch.assert_called_once()

    def test_wrong_predecessor_and_corrupt_upload_do_not_register(self):
        with self.assertRaisesRegex(RuntimeError, 'latest'):
            self.send({**self.request, 'after_review': CURRENT})
        with self.assertRaisesRegex(RuntimeError, 'digest'):
            self.send(data=b'broken')
        self.assertFalse((self.controller.state/'operations'/CURRENT).exists())
        self.launch.assert_not_called()

    def test_incomplete_predecessor_does_not_create_new_operation(self):
        self.predecessor.side_effect = RuntimeError('Predecessor verification failed')
        with self.assertRaisesRegex(RuntimeError, 'Predecessor'):
            self.send()
        self.assertFalse((self.controller.state/'operations'/CURRENT).exists())
        self.launch.assert_not_called()

    def test_report_is_read_only_and_public_summary_omits_private_paths(self):
        public = self.controller.status('latest')
        self.assertNotIn('/private', json.dumps(public))
        self.assertNotIn('report', public)
        self.assertIn('/private', json.dumps(self.controller.status(PREVIOUS, private=True)))
        self.launch.assert_not_called()

    def test_stopped_pending_unit_requires_recovery(self):
        self.report(PREVIOUS, 'observing')
        self.assertTrue(self.controller.status(PREVIOUS)['needs_recovery'])

    @unittest.skipUnless(os.name == 'posix', 'fcntl is exercised by Linux CI')
    def test_registration_lock_rejects_concurrent_clients(self):
        self.controller.state.mkdir()
        with control.exclusive(self.controller.state/'register.lock'):
            with self.assertRaisesRegex(RuntimeError, 'Another'):
                self.send()
        self.launch.assert_not_called()

    def test_client_reuses_registered_operation_without_upload(self):
        result = self.send()
        calls = []
        def transport(_ssh, request, archive=None):
            calls.append(request['action'])
            return result
        response = CLIENT['deploy']([], self.archive, CURRENT, 'news', PREVIOUS, None, transport)
        self.assertEqual(response, result)
        self.assertEqual(calls, ['status'])

    def test_ssh_authentication_failure_stops_before_upload(self):
        calls = []
        def transport(_ssh, request, archive=None):
            calls.append(request['action'])
            raise RuntimeError('authentication failed')
        with self.assertRaisesRegex(RuntimeError, 'authentication'):
            CLIENT['deploy']([], self.archive, CURRENT, 'news', PREVIOUS, None, transport)
        self.assertEqual(calls, ['status'])

    def test_strict_ssh_and_input_validation(self):
        credentials = self.root/'credential'
        credentials.write_text('fixture')
        credentials.chmod(0o600)
        options = CLIENT['ssh_options']('root@example.invalid', credentials, credentials)
        for required in ('BatchMode=yes', 'IdentitiesOnly=yes', 'StrictHostKeyChecking=yes'):
            self.assertIn(required, options)
        for change in ({'id': '../../etc'}, {'profile': 'ai'}, {'extra': 'sh'}, {'after_review': None}, {'replace_observation': PREVIOUS}):
            request = {**self.request, **change}
            command = 'finevent-release '+base64.b64encode(json.dumps(request).encode()).decode()
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                control.parse_request(command)
        for command in ('sh', 'finevent-release e30=; sh', 'finevent-release e30= extra'):
            with self.assertRaises(RuntimeError):
                control.parse_request(command)


if __name__ == '__main__':
    unittest.main()
