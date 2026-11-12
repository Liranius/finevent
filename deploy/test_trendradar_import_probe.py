import builtins
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import memory_recovery as recovery
import trendradar_import_probe as probe

SOURCE = b'''from litellm import completion
class AIClient:
    def chat(self, value):
        """Keep this documentation."""
        return completion(value)
'''
DIGEST = hashlib.sha256(SOURCE).hexdigest()


class ImportProbeTests(unittest.TestCase):
    def workflow_evidence(self):
        return {'rounds': 2, 'http_requests': 20, 'finevent_rows': 17, 'production_data_used': False,
                'schema_sha256': 'a'*64, 'tables_sha256': 'b'*64, 'finevent_projection_sha256': 'c'*64,
                'html_sha256': {name: 'd'*64 for name in ('first.html', 'second.html', 'daily.html', 'index.html')}}

    def test_workflow_mounts_only_runner_and_fixture_readonly(self):
        command = probe.create_command('trial', PurePosixPath('/evidence/probe.py'), 'image', 'lazy', DIGEST, 'run1', True)
        mounts = [command[index+1] for index, arg in enumerate(command) if arg == '--mount']
        self.assertEqual(len(mounts), 2)
        self.assertIn('type=bind,src=/evidence/trendradar_workflow_fixture.py,dst=/trendradar_workflow_fixture.py,readonly', mounts)
        self.assertIn('--workflow', command)
        self.assertIn('--network=none', command)
        self.assertIn('--memory-swap=192m', command)
        self.assertNotIn('/app/output', ' '.join(command))

    def test_workflow_comparison_rejects_database_or_html_difference(self):
        runs = [{'case': case, 'status': 'measured', 'cleanup': 'removed',
                 'measurement': {'workflow': self.workflow_evidence()}} for case in ('original', 'lazy')]
        self.assertTrue(probe.compare(runs, True)['outputs_identical'])
        for field, value in (('tables_sha256', 'e'*64), ('html_sha256', {str(i): 'e'*64 for i in range(4)})):
            changed = copy.deepcopy(runs)
            changed[1]['measurement']['workflow'][field] = value
            with self.assertRaisesRegex(RuntimeError, 'outputs differ'):
                probe.compare(changed, True)
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
            probe.compare(runs[:1], True)

    def test_workflow_evidence_requires_finished_rounds_and_html(self):
        for change in ({'rounds': 1}, {'finevent_rows': 0}, {'production_data_used': True},
                       {'html_sha256': {}}, {'schema_sha256': None}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                probe.validate_workflow(dict(self.workflow_evidence(), **change))
        row = {'status': 'ok', 'case': 'lazy', 'client_sha256': DIGEST, 'litellm_loaded': False,
               'main_loaded': True, 'peak_rss_bytes': 100, 'cgroup_peak_bytes': 200}
        with self.assertRaisesRegex(RuntimeError, 'Incomplete offline'):
            probe.decode_result(probe.MARKER+json.dumps(row), 'lazy', DIGEST, True)

    def test_lazy_candidate_defers_dependency_until_actual_call(self):
        calls = []
        original_import = builtins.__import__
        def importing(name, *args, **kwargs):
            if name == 'litellm':
                calls.append(name)
                return SimpleNamespace(completion=lambda value: 'answer:'+value)
            return original_import(name, *args, **kwargs)
        for lazy in (False, True):
            with self.subTest(lazy=lazy):
                calls.clear()
                code = compile(probe.client_tree(SOURCE, DIGEST, lazy), 'client.py', 'exec')
                namespace = {}
                with patch('builtins.__import__', side_effect=importing):
                    exec(code, namespace)
                    self.assertEqual(len(calls), 0 if lazy else 1)
                    self.assertEqual(namespace['AIClient']().chat('test'), 'answer:test')
                self.assertEqual(len(calls), 1)
                self.assertEqual(namespace['AIClient'].chat.__doc__, 'Keep this documentation.')

    def test_hash_or_code_shape_change_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'differs'):
            probe.client_tree(SOURCE, '0'*64, True)
        for source in (SOURCE.replace(b'import completion', b'import completion as call'), SOURCE+b'other = completion\n'):
            with self.assertRaises(RuntimeError):
                probe.client_tree(source, hashlib.sha256(source).hexdigest(), True)

    def test_container_is_offline_unprivileged_and_has_no_production_mounts(self):
        command = probe.create_command('trial', PurePosixPath('/evidence/probe.py'), 'sha256:'+'a'*64, 'lazy', DIGEST, 'run1')
        for flag in ('--pull=never', '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--user=65534:65534', '--memory=192m', '--memory-swap=192m', '--no-healthcheck', '--log-driver=none'):
            self.assertIn(flag, command)
        self.assertEqual(command.count('--mount'), 1)
        self.assertEqual(command[command.index('--mount')+1], 'type=bind,src=/evidence/probe.py,dst=/probe.py,readonly')
        self.assertNotIn('--env-file', command)
        self.assertNotIn('/app/config', ' '.join(command))
        self.assertNotIn('/app/output', ' '.join(command))

    def test_cleanup_refuses_another_container_and_uses_verified_id(self):
        with patch.object(recovery, 'command') as command:
            command.return_value.stdout = json.dumps({'id': 'a'*64, 'owner': 'another-run'})
            command.return_value.returncode = 0
            with self.assertRaisesRegex(RuntimeError, 'ownership'):
                probe.cleanup_container(recovery, 'trial', 'run1')
            self.assertEqual(command.call_count, 1)
        with patch.object(recovery, 'command') as command:
            command.return_value.stdout = json.dumps({'id': 'a'*64, 'owner': 'run1'})
            command.return_value.returncode = 0
            self.assertEqual(probe.cleanup_container(recovery, 'trial', 'run1'), 'removed')
            self.assertEqual(command.call_args.args, ('docker', 'rm', '-f', '-v', 'a'*64))

    def test_result_requires_actual_import_evidence(self):
        row = {'status': 'ok', 'case': 'lazy', 'client_sha256': DIGEST,
               'litellm_loaded': False, 'main_loaded': True, 'peak_rss_bytes': 100, 'cgroup_peak_bytes': 200}
        output = 'Unrelated log SECRET\n'+probe.MARKER+json.dumps(row)
        result = probe.decode_result(output, 'lazy', DIGEST)
        self.assertNotIn('SECRET', json.dumps(result))
        with self.assertRaisesRegex(RuntimeError, 'module imports'):
            probe.decode_result(probe.MARKER+json.dumps(dict(row, litellm_loaded=True)), 'lazy', DIGEST)
        with self.assertRaisesRegex(RuntimeError, 'unique'):
            probe.decode_result(output+'\n'+probe.MARKER+json.dumps(row), 'lazy', DIGEST)
        with self.assertRaisesRegex(RuntimeError, 'failed: ModuleNotFoundError'):
            probe.decode_result(probe.MARKER+json.dumps(dict(row, status='failed', error_type='ModuleNotFoundError')), 'lazy', DIGEST)

    def test_pressure_failure_still_cleans_only_temporary_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            process = MagicMock()
            process.poll.return_value = None
            report = {'runs': []}
            with patch.object(probe, 'check_headroom', side_effect=[300*probe.MIB, RuntimeError('headroom low')]), patch.object(recovery, 'command'), patch.object(recovery, 'meminfo', return_value={'MemAvailable': 300*probe.MIB}), patch.object(probe.subprocess, 'Popen', return_value=process), patch.object(probe, 'cleanup_container', return_value='removed') as cleanup:
                with self.assertRaisesRegex(RuntimeError, 'headroom low'):
                    probe.run_case(recovery, Path(temporary), {}, 'image', DIGEST, 'run1', 0, 'baseline', report)
            cleanup.assert_called_once_with(recovery, 'finevent-run1-0', 'run1')
            process.terminate.assert_called_once()
            self.assertEqual(report['runs'][0]['status'], 'incomplete')
            self.assertEqual(report['runs'][0]['cleanup'], 'removed')
            self.assertTrue((Path(temporary)/'report.json').exists())

    def test_comparison_does_not_make_savings_from_missing_or_unclean_runs(self):
        runs = [{'case': case, 'status': 'measured', 'cleanup': 'removed',
                 'measurement': {'peak_rss_bytes': (100 if case == 'original' else 20)*probe.MIB, 'import_seconds': 1}}
                for case in probe.CASES]
        result = probe.compare(runs)
        self.assertEqual(result['original_minus_lazy_peak_rss_mib'], 80)
        self.assertEqual(result['original']['samples'], 2)
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
            probe.compare(runs[:-1])
        runs[0]['cleanup'] = 'failed'
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
            probe.compare(runs)

    def test_headroom_requires_idle_production_and_room_before_schedule(self):
        target = {'pid': 1, 'cgroup': {'memory.events': {'oom_kill': 19}}}
        group = {'memory.current': 30*probe.MIB, 'memory.events': {'oom_kill': 19}}
        with patch.object(recovery, 'meminfo', return_value={'MemAvailable': 255*probe.MIB}), patch.object(recovery.diagnostic, 'cgroup_memory', return_value=group), patch.object(probe.time, 'time', return_value=100):
            with self.assertRaisesRegex(RuntimeError, 'headroom'):
                probe.check_headroom(recovery, target, initial=True)
            self.assertEqual(probe.check_headroom(recovery, target), 255*probe.MIB)
        with patch.object(recovery, 'meminfo', return_value={'MemAvailable': 300*probe.MIB}), patch.object(recovery.diagnostic, 'cgroup_memory', return_value=group), patch.object(probe.time, 'time', return_value=1700):
            with self.assertRaisesRegex(RuntimeError, 'next scheduled'):
                probe.check_headroom(recovery, target, initial=True)


if __name__ == '__main__':
    unittest.main()
