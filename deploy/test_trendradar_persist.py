from copy import deepcopy
from contextlib import closing, ExitStack
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import trendradar_persist as persist


class PersistenceTests(unittest.TestCase):
    def configs(self):
        before = {'name': persist.OLD_PROJECT, 'networks': {'default': {'name': persist.OLD_PROJECT+'_default'}},
                  'services': {'trend-radar': {'image': persist.IMAGE, 'container_name': 'trend-radar',
                      'restart': 'unless-stopped', 'environment': {'PRIVATE': 'SECRET'}, 'command': ['SECRET'],
                      'mem_limit': '167772160', 'memswap_limit': '167772160',
                      'volumes': [{'type': 'bind', 'source': '/srv/example/trendradar/config', 'target': '/app/config', 'read_only': True},
                                  {'type': 'bind', 'source': '/srv/example/trendradar/output', 'target': '/app/output'}]}}}
        after = deepcopy(before)
        after.update(name=persist.PROJECT, networks={'default': {'name': persist.PROJECT+'_default'}})
        service = after['services']['trend-radar']
        service.update({k: v for k, v in persist.wrapper()['services']['trend-radar'].items() if k not in ('extends', 'volumes')})
        service['volumes'].append(persist.patch_volume())
        return before, after

    def test_only_reviewed_compose_changes_allowed(self):
        before, after = self.configs()
        persist.validate_config(before, after)
        changed = deepcopy(after)
        changed['services']['trend-radar']['volumes'][-1]['bind'] = {}
        persist.validate_config(before, changed)  # Compose omits the false default.
        variants = []
        for key, value in [('environment', {'PRIVATE': 'DIFFERENT SECRET'}), ('command', ['rm']),
                           ('mem_limit', 512), ('cgroup_parent', 'system.slice')]:
            bad = deepcopy(after); bad['services']['trend-radar'][key] = value; variants.append(bad)
        bad = deepcopy(after); bad['services']['trend-radar']['volumes'][0]['read_only'] = False; variants.append(bad)
        bad = deepcopy(after); bad['services']['trend-radar']['volumes'][-1]['bind']['create_host_path'] = True; variants.append(bad)
        bad = deepcopy(after); bad['secrets'] = {'private': {'file': 'SECRET'}}; variants.append(bad)
        bad = deepcopy(after); bad['networks']['default']['external'] = True; variants.append(bad)
        for bad in variants:
            with self.subTest(bad=bad), self.assertRaises(RuntimeError) as raised:
                persist.validate_config(before, bad)
            self.assertNotIn('SECRET', str(raised.exception))

    def test_compose_5_empty_ipam_matches_the_observed_server_configuration(self):
        before, after = self.configs()
        before['networks']['default']['ipam'] = {}
        after['networks']['default']['ipam'] = {}
        saved = deepcopy((before, after))
        persist.validate_config(before, after)
        self.assertEqual((before, after), saved)
        del after['networks']['default']['ipam']
        persist.validate_config(before, after)

    def test_nonempty_ipam_and_other_network_changes_are_still_rejected(self):
        for settings in ({'ipam': {'config': [{'subnet': '10.0.0.0/24'}]}}, {'ipam': {'driver': 'custom'}},
                         {'external': True}, {'internal': True}, {'driver': 'bridge'}, {'name': 'another-network'}):
            before, after = self.configs()
            after['networks']['default'].update(settings)
            with self.subTest(settings=settings), self.assertRaises(RuntimeError):
                persist.validate_config(before, after)

    def test_file_intent_saved_before_mutation_and_rollback_preserves_new_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); state = {}; config = folder/'config'; config.write_bytes(b'old')
            database = folder/'news'; database.write_bytes(b'newly collected')
            original = persist.replace_file
            def recording(path, data, expected, metadata=None):
                saved = json.loads((folder/'state.json').read_text())
                self.assertEqual(saved['files'][-1]['after'], persist.digest(data))
                original(path, data, expected, metadata)
            with patch.object(persist, 'replace_file', side_effect=recording):
                persist.install_file(folder, state, config, b'new')
            persist.install_file(folder, state, folder/'created', b'patch')
            persist.restore_files(folder, state)
            self.assertEqual(config.read_bytes(), b'old')
            self.assertFalse((folder/'created').exists())
            self.assertEqual(database.read_bytes(), b'newly collected')
            persist.restore_files(folder, state)  # Idempotent after a partial rollback.

    def test_all_file_drift_checked_before_rollback_stops_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); state = {'container_change': True}; config = folder/'config'
            persist.install_file(folder, state, config, b'patch')
            config.write_bytes(b'independent change')
            with patch.object(persist.recovery, 'command') as command, self.assertRaises(RuntimeError):
                persist.restore(folder, state)
            command.assert_not_called()
            self.assertEqual(config.read_bytes(), b'independent change')

    def test_corrupt_backup_refuses_restore_before_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); state = {}; config = folder/'config'; config.write_bytes(b'original')
            persist.install_file(folder, state, config, b'new')
            (folder/state['files'][0]['backup']).write_bytes(b'bad')
            with self.assertRaises(RuntimeError):
                persist.restore_files(folder, state)
            self.assertEqual(config.read_bytes(), b'new')

    def test_preexisting_temporary_file_is_not_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); config = folder/'config'; config.write_bytes(b'original')
            collision = folder/('config.finevent-'+str(os.getpid())+'.tmp')
            collision.write_bytes(b'independent file')
            with self.assertRaises(FileExistsError):
                persist.replace_file(config, b'new', persist.digest(b'original'))
            self.assertEqual(collision.read_bytes(), b'independent file')
            self.assertEqual(config.read_bytes(), b'original')

    def test_swappiness_alias_resolved_once_and_other_settings_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); (folder/'etc/sysctl.d').mkdir(parents=True)
            config = folder/'etc/sysctl.conf'
            config.write_bytes(b'# comment\nvm.swappiness = 0 # retain\nother.key=17\n')
            # Alias resolution is mocked to work on Windows without symlink privileges.
            alias = folder/'etc/sysctl.d/99-sysctl.conf'; alias.write_bytes(config.read_bytes())
            original = Path.resolve
            def resolve(path, *args, **kwargs):
                return original(config if path == alias else path, *args, **kwargs)
            with patch.object(Path, 'resolve', resolve):
                edits = persist.swappiness_edits(folder)
            self.assertEqual(len(edits), 1)
            self.assertEqual(next(iter(edits.values())), b'# comment\nvm.swappiness = 60 # retain\nother.key=17\n')
            (folder/'etc/sysctl.d/50-unreviewed.conf').write_text('vm.swappiness=20\n')
            with self.assertRaises(RuntimeError):
                persist.swappiness_edits(folder)

    def test_parent_limits_and_oom_cannot_be_baselined_away(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            values = {'memory.high': persist.pressure.HIGH, 'memory.max': persist.recovery.MEMORY,
                      'memory.swap.max': 256*persist.recovery.MIB}
            for key, value in values.items():
                (folder/key).write_text(str(value))
            (folder/'memory.events').write_text('oom 0\noom_kill 0\n')
            self.assertEqual(persist.check_parent(folder), values)
            (folder/'memory.events').write_text('oom 1\noom_kill 0\n')
            with self.assertRaises(RuntimeError):
                persist.check_parent(folder)
            (folder/'memory.events').write_text('oom 0\noom_kill 0\n')
            (folder/'memory.high').write_text('max')
            with self.assertRaises(RuntimeError):
                persist.check_parent(folder)

    def test_rollback_after_partial_container_change_keeps_original(self):
        old = {'id': 'a'*64, 'project': persist.OLD_PROJECT, 'image': persist.IMAGE, 'name': '/retained'}
        new = {'id': 'b'*64, 'project': persist.PROJECT, 'image': persist.IMAGE}
        for current in (None, new, {**old, 'name': '/trend-radar'}):
            state = {'old': old, 'container_change': True, 'retained_name': 'retained', 'slice_attempted': True}
            calls = []
            health = {path: {'exit': 0, 'http_status': '200'} for path in ('/finevent/healthz', '/TrendRadar/content/')}
            with tempfile.TemporaryDirectory() as temporary, patch.object(persist, 'container', side_effect=[old, current, old]), \
                 patch.object(persist.recovery, 'command', side_effect=lambda *args, **kw: calls.append(args)), \
                 patch.object(persist.recovery, 'validate_limits'), patch.object(persist.recovery, 'public_health', return_value=health):
                result = persist.restore(Path(temporary), state)
            self.assertEqual(result['status'], 'restored')
            self.assertIn(('docker', 'start', old['id']), calls)
            self.assertNotIn(('docker', 'rm', old['id']), calls)
            self.assertFalse(any('-v' in args for args in calls))
            if current is new:
                self.assertIn(('docker', 'rm', new['id']), calls)

    def test_rollback_never_removes_unowned_container(self):
        old = {'id': 'a'*64, 'project': persist.OLD_PROJECT, 'image': persist.IMAGE, 'name': '/retained'}
        state = {'container_change': True, 'old': old, 'retained_name': 'retained'}
        with patch.object(persist, 'container', side_effect=[old, {'id': 'b'*64, 'project': 'someone-else'}]), \
             patch.object(persist.recovery, 'command') as command, self.assertRaises(RuntimeError):
            persist.restore(Path('.'), state)
        command.assert_not_called()

    def test_missing_original_prevents_stopping_current_service(self):
        state = {'container_change': True, 'old': {'id': 'a'*64}, 'retained_name': 'retained'}
        with patch.object(persist, 'container', side_effect=RuntimeError('Missing original')), \
             patch.object(persist.recovery, 'command') as command, self.assertRaises(RuntimeError):
            persist.restore(Path('.'), state)
        command.assert_not_called()

    def test_environment_order_is_irrelevant_but_values_and_command_order_matter(self):
        before = {'environment': ['A=1', 'B=2'], 'command': ['python', 'main.py']}
        self.assertTrue(persist.same_launch(before, {**before, 'environment': ['B=2', 'A=1']}))
        self.assertFalse(persist.same_launch(before, {**before, 'environment': ['B=3', 'A=1']}))
        self.assertFalse(persist.same_launch(before, {**before, 'command': ['main.py', 'python']}))

    def test_new_container_checks_identity_restart_patch_and_readonly_mount(self):
        target = {'id': 'new', 'image': persist.IMAGE, 'project': persist.PROJECT, 'running': True,
                  'restart': 'unless-stopped', 'parent': 'trendradar.slice', 'pid': 2,
                  'cgroup': {'path': '/trendradar.slice/docker-new.scope', 'memory.events': {'oom_kill': 0}}}
        state = {'old': {'id': 'old'}, 'new_id': 'new', 'observed_pid': 2}
        mounts = [{'Type': 'bind', 'Destination': '/app/config', 'Source': '/srv/example/trendradar/config', 'RW': False},
                  {'Type': 'bind', 'Destination': '/app/output', 'Source': '/srv/example/trendradar/output', 'RW': True},
                  {'Type': 'bind', 'Destination': '/app/trendradar/ai/client.py', 'Source': str(persist.PATCH), 'RW': False}]
        with patch.object(persist, 'container', return_value=target), patch.object(persist, 'check_parent'), \
             patch.object(persist.recovery, 'validate_limits'), patch.object(persist.lazy, 'source_path'), \
             patch.object(persist.recovery, 'sha', return_value=persist.CLIENT_SHA) as sha, \
             patch.object(persist.audit, 'command', side_effect=lambda *args: json.dumps(mounts)):
            self.assertEqual(persist.new_target(state), target)
            for key, value in [('id', 'changed'), ('image', 'changed'), ('pid', 3), ('parent', 'system.slice')]:
                previous = target[key]; target[key] = value
                with self.assertRaises(RuntimeError):
                    persist.new_target(state)
                target[key] = previous
            mounts[-1]['RW'] = True
            with self.assertRaises(RuntimeError):
                persist.new_target(state)
            mounts[-1]['RW'] = False; sha.return_value = 'changed'
            with self.assertRaises(RuntimeError):
                persist.new_target(state)

    def test_full_apply_and_rollback_at_each_transition(self):
        for failure in ('preview', 'stop', 'rename', 'up', 'restart', 'observe', 'source', None):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                folder = Path(temporary); evidence_dir = folder/'audit'; evidence_dir.mkdir()
                verification = folder/'verification'; verification.mkdir()
                verification_data = {'ai_observation': {'baseline': {'trend_oom_kill': 19, 'host_oom_kill': 19, 'finevent_oom_kill': 0}}}
                (verification/'report.json').write_text(json.dumps(verification_data))
                (evidence_dir/'report.json').write_text(json.dumps({'status': 'collected_for_review',
                    'container': {'id': persist.recovery.EXPECTED_CONTAINER}, 'verification': {'id': 'verification'},
                    'verification_sha256': persist.recovery.sha(verification/'report.json')}))
                directory = folder/'operation'; directory.mkdir()
                config = folder/'compose.yml'; config.write_bytes(b'original compose')
                source = folder/'source.py'; source.write_bytes(b'validated patch')
                sysctl = folder/'sysctl.conf'; sysctl.write_bytes(b'vm.swappiness=0\n')
                loader = folder/'loader'; loader.touch()
                for name, value in {'COMPOSE': config, 'COMPOSE_SHA': persist.recovery.sha(config),
                    'BASE': folder/'base.yml', 'PATCH': folder/'managed/client.py', 'UNIT': folder/'slice',
                    'GROUP': folder/'group', 'CLIENT_SHA': persist.recovery.sha(source)}.items():
                    stack.enter_context(patch.object(persist, name, value))
                stack.enter_context(patch.object(persist.recovery, 'BACKUPS', folder))
                stack.enter_context(patch.object(persist, 'sysctl_loader', return_value=str(loader)))
                old = {'id': persist.recovery.EXPECTED_CONTAINER, 'image': persist.IMAGE, 'project': persist.OLD_PROJECT,
                       'restart': 'unless-stopped', 'parent': '', 'name': '/trend-radar', 'pid': 1}
                live = {'current': old}; calls = []; fired = []
                new = {'id': 'b'*64, 'image': persist.IMAGE, 'project': persist.PROJECT, 'pid': 2}
                def inspect(name='trend-radar', optional=False):
                    return dict(old if name == old['id'] else live['current']) if (name == old['id'] or live['current']) else None
                def command(*args, **kwargs):
                    calls.append(args)
                    action = args[1] if args[0] == 'docker' else ''
                    if 'up' in args:
                        action = 'up'
                        live['current'] = dict(new)  # Include failure after Docker created the container.
                    if action == failure and not fired:
                        fired.append(True); raise RuntimeError('Injected command failure')
                    if action == 'rename':
                        old['name'] = '/'+args[-1]; live['current'] = old if args[-1] == 'trend-radar' else None
                    if action == 'rm':
                        live['current'] = None
                    if action == 'restart':
                        live['current']['pid'] = 3
                    return SimpleNamespace(stdout='enabled\n', returncode=1 if args[:3] == ('docker', 'image', 'inspect') else 0)
                stack.enter_context(patch.object(persist, 'container', side_effect=inspect))
                stack.enter_context(patch.object(persist, 'new_target', side_effect=lambda state: dict(live['current'])))
                stack.enter_context(patch.object(persist.recovery, 'command', side_effect=command))
                stack.enter_context(patch.object(persist, 'idle'))
                stack.enter_context(patch.object(persist, 'check_parent', return_value={}))
                stack.enter_context(patch.object(persist, 'swappiness_edits', return_value={sysctl: b'vm.swappiness=60\n'}))
                stack.enter_context(patch.object(persist, 'database_backup', return_value='backup'))
                stack.enter_context(patch.object(persist.lazy, 'source_path', return_value=source))
                stack.enter_context(patch.object(persist.lazy, 'output_directory', return_value=folder))
                stack.enter_context(patch.object(persist.lazy, 'backup_outputs', return_value={}))
                stack.enter_context(patch.object(persist.pressure, 'read_high', return_value=str(persist.pressure.HIGH)))
                stack.enter_context(patch.object(persist.pressure, 'read_swappiness', return_value=60))
                stack.enter_context(patch.object(persist.pressure, 'pressure_guard'))
                stack.enter_context(patch.object(persist.recovery, 'validate_limits'))
                sample = {**verification_data['ai_observation']['baseline'], 'available': 200*persist.recovery.MIB}
                stack.enter_context(patch.object(persist.recovery, 'sample', side_effect=[sample, {**sample, 'trend_oom_kill': 0}]))
                stack.enter_context(patch.object(persist.audit, 'overlap_evidence', return_value={}))
                stack.enter_context(patch.object(persist.audit, 'launch_comparison', return_value={'environment_equal': True}))
                before, after = self.configs()
                before['networks']['default']['ipam'] = {}; after['networks']['default']['ipam'] = {}
                preview = deepcopy(after)
                if failure == 'preview':
                    preview['services']['trend-radar']['environment'] = {'PRIVATE': 'DRIFT'}
                configurations = iter([before, preview, after])
                def audit_command(*args, **kwargs):
                    if 'config' in args:
                        if kwargs.get('input_text'):
                            self.assertEqual(config.read_bytes(), b'original compose')
                            self.assertFalse((folder/'managed').exists())
                            self.assertNotIn(('docker', 'stop', '--time', '45', old['id']), calls)
                            if failure == 'preview':
                                fired.append(True)
                        return json.dumps(next(configurations))
                    if 'MainPID' in args:
                        return '5'
                    if 'is-enabled' in args:
                        return 'enabled'
                    return '{}'
                stack.enter_context(patch.object(persist.audit, 'command', side_effect=audit_command))
                def observe(_directory, report, **kwargs):
                    if failure == 'observe':
                        fired.append(True)
                        raise RuntimeError('Injected observation failure')
                    report.update(status='completed_for_review', observation_started='2026-10-30T09:00:00+08:00',
                        resources={'max_sample_interval_seconds': 5, 'baseline_counters': {**sample, 'trend_oom_kill': 0}})
                stack.enter_context(patch.object(persist.recovery, 'observe', side_effect=observe))
                def outputs(*args):
                    if failure == 'source':
                        fired.append(True)
                    return {'source_failures': 1 if failure == 'source' else 0}
                stack.enter_context(patch.object(persist.lazy, 'verify_outputs', side_effect=outputs))
                health = {path: {'exit': 0, 'http_status': '200'} for path in ('/finevent/healthz', '/TrendRadar/content/')}
                stack.enter_context(patch.object(persist.recovery, 'public_health', return_value=health))
                state = {}; report = {'id': 'test', 'audit_id': 'audit'}
                if failure is None:
                    persist.run(directory, state, report)
                    self.assertEqual(state['phase'], 'accepted')
                    self.assertEqual(report['status'], 'completed_for_review')
                    self.assertNotIn('rollback', report)
                    self.assertEqual(live['current']['id'], new['id'])
                    self.assertEqual(json.loads(config.read_text()), persist.wrapper())
                    self.assertEqual(sysctl.read_bytes(), b'vm.swappiness=60\n')
                    continue
                with self.assertRaises(RuntimeError):
                    persist.run(directory, state, report)
                if failure == 'preview':
                    self.assertEqual(report['rollback']['status'], 'no_changes')
                    self.assertEqual(report['configuration_error']['fields'], ['environment'])
                    self.assertNotIn('DRIFT', json.dumps(report))
                    self.assertEqual(config.read_bytes(), b'original compose')
                    self.assertFalse((folder/'managed').exists())
                    self.assertTrue(fired)
                    continue
                self.assertEqual(report['rollback']['status'], 'restored', report)
                self.assertEqual(config.read_bytes(), b'original compose')
                self.assertEqual(sysctl.read_bytes(), b'vm.swappiness=0\n')
                self.assertEqual(live['current']['id'], old['id'])
                self.assertIn(('docker', 'start', old['id']), calls)
                self.assertNotIn(('docker', 'rm', old['id']), calls)
                self.assertTrue(fired, 'Test must reach the intended failure point')

    def test_retry_evidence_must_match_restored_files_and_original_container(self):
        review_id = 'trendradar-persist-review-20261030T080945Z-48d601e0'
        previous_id = 'trendradar-persist-20261030T080043Z-44a77382'
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary); previous = root/previous_id; previous.mkdir()
            review = root/review_id; review.mkdir()
            config = root/'compose.yml'; config.write_bytes(b'original')
            base = root/'base.yml'; candidate = root/'managed/client.py'
            target = {'id': 'original', 'pid': 27}
            old_report = {'status': 'incomplete', 'rollback': {'status': 'restored'}}
            (previous/'report.json').write_text(json.dumps(old_report))
            (previous/'compose.before').write_bytes(b'original')
            state = {'phase': 'restored', 'old': target, 'files': [
                {'path': str(config), 'before': persist.digest(b'original'), 'after': persist.digest(b'candidate'), 'backup': 'compose.before'},
                {'path': str(base), 'before': None, 'after': persist.digest(b'base')},
                {'path': str(candidate), 'before': None, 'after': persist.CLIENT_SHA}]}
            (previous/'state.json').write_text(json.dumps(state))
            review_data = {'id': review_id, 'status': 'collected_for_review', 'stage': 'completed',
                           'previous_run': previous_id, 'previous_report_sha256': persist.recovery.sha(previous/'report.json')}
            (review/'report.json').write_text(json.dumps(review_data))
            for name, value in [('COMPOSE', config), ('BASE', base), ('PATCH', candidate)]:
                stack.enter_context(patch.object(persist, name, value))
            stack.enter_context(patch.object(persist.recovery, 'BACKUPS', root))
            self.assertFalse(persist.reviewed_retry({}, target))
            report = {'review_id': review_id}
            self.assertTrue(persist.reviewed_retry(report, target))
            self.assertEqual(report['retry_review']['previous_run'], previous_id)
            with self.assertRaises(RuntimeError):
                persist.reviewed_retry({'review_id': review_id}, {**target, 'pid': 28})
            for changes in ({'container_change': True}, {'phase': 'applied'}, {'files': state['files'][:-1]}):
                (previous/'state.json').write_text(json.dumps({**state, **changes}))
                with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                    persist.reviewed_retry({'review_id': review_id}, target)
            (previous/'state.json').write_text(json.dumps(state))
            config.write_bytes(b'independent change')
            with self.assertRaises(RuntimeError):
                persist.reviewed_retry({'review_id': review_id}, target)
            config.write_bytes(b'original')
            (previous/'report.json').write_text(json.dumps({**old_report, 'modified': True}))
            with self.assertRaises(RuntimeError):
                persist.reviewed_retry({'review_id': review_id}, target)

    def test_only_reviewed_empty_root_owned_directory_can_be_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)/'managed'
            with patch.object(persist, 'PATCH', directory/'client.py'):
                self.assertFalse(persist.check_patch_directory(False))
                directory.mkdir()
                original = Path.stat
                metadata = SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o700)
                def metadata_for(path, *args, **kwargs):
                    return metadata if path == directory else original(path, *args, **kwargs)
                with patch.object(Path, 'stat', metadata_for):
                    self.assertTrue(persist.check_patch_directory(True))
                    with self.assertRaises(RuntimeError):
                        persist.check_patch_directory(False)
                    metadata.st_uid = 1000
                    with self.assertRaises(RuntimeError):
                        persist.check_patch_directory(True)
                    metadata.st_uid = 0; metadata.st_mode = stat.S_IFDIR | 0o755
                    with self.assertRaises(RuntimeError):
                        persist.check_patch_directory(True)
                    metadata.st_mode = stat.S_IFDIR | 0o700
                    (directory/'independent').write_bytes(b'keep')
                    with self.assertRaises(RuntimeError):
                        persist.check_patch_directory(True)
                self.assertEqual((directory/'independent').read_bytes(), b'keep')
                with patch.object(Path, 'is_symlink', return_value=True), self.assertRaises(RuntimeError):
                    persist.check_patch_directory(True)

    def test_config_failure_diagnostic_omits_unknown_keys_and_values(self):
        before, after = self.configs()
        after['services']['trend-radar']['environment'] = {'API_KEY': 'SECRET-VALUE'}
        after['services']['trend-radar']['SECRET-KEY'] = 'SECRET-VALUE'
        with self.assertRaises(persist.ConfigurationMismatch) as raised:
            persist.validate_config(before, after)
        self.assertEqual(raised.exception.details, {'code': 'unexpected_service_changes', 'fields': ['environment'], 'other_field_count': 1})
        self.assertNotIn('SECRET', json.dumps(raised.exception.details))

    def test_observation_failure_rolls_back_and_redacts_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = {}; report = {}
            with patch.object(persist, 'apply', side_effect=ValueError('SECRET')), \
                 patch.object(persist, 'restore', return_value={'status': 'restored'}) as undo, self.assertRaises(ValueError):
                persist.run(Path(temporary), state, report)
            undo.assert_called_once()
            self.assertEqual(report['status'], 'incomplete')
            self.assertEqual(report['rollback']['status'], 'restored')
            self.assertNotIn('SECRET', json.dumps(report))

    def test_launch_is_detached_and_saves_evidence_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)/'run'
            report = {'id': 'test', 'status': 'queued'}
            def command(*args):
                self.assertEqual(json.loads((directory/'report.json').read_text()), report)
                self.assertIn('--property=MemoryMax=128M', args)
                self.assertIn('--property=RuntimeMaxSec=4800', args)
                self.assertIn('systemd-run', args)
            with patch.object(persist.recovery, 'command', side_effect=command):
                persist.launch(directory, report)
            for name in persist.FILES:
                self.assertTrue((directory/name).is_file())

    def test_backup_includes_committed_wal_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary); source = folder/'live.db'; destination = folder/'backup.db'
            with closing(sqlite3.connect(source)) as connection:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('CREATE TABLE news (id INTEGER)')
                connection.execute('INSERT INTO news VALUES (1)'); connection.commit()
                persist.database_backup(source, destination)
                with closing(sqlite3.connect(destination)) as backup:
                    self.assertEqual(backup.execute('SELECT id FROM news').fetchall(), [(1,)])

    def test_optional_container_does_not_hide_inspection_failure(self):
        failure = SimpleNamespace(returncode=1, stdout='')
        present = SimpleNamespace(returncode=0, stdout='a'*64+'\n')
        with patch.object(persist.recovery, 'command', side_effect=[failure, present]), self.assertRaises(RuntimeError):
            persist.container(optional=True)
        with patch.object(persist.recovery, 'command', side_effect=[failure, SimpleNamespace(returncode=0, stdout='')]):
            self.assertIsNone(persist.container(optional=True))


if __name__ == '__main__':
    unittest.main()
