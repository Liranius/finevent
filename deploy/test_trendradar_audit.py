import ast
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trendradar_audit as audit


class TrendRadarAuditTests(unittest.TestCase):
    def passed_overlap(self):
        counters = {'trend_oom_kill': 19, 'finevent_oom_kill': 0, 'host_oom_kill': 19}
        job = {'pid': 10, 'start_ticks': 100}
        return {'id': 'verified', 'finished_at': '2026-10-29T21:00:21+08:00', 'status': 'completed_for_review',
                'ai': {'status': 'succeeded', 'timing': {'model_started_ms': 1000, 'model_completed_ms': 2348,
                    'collector_started_ms': 1002, 'collector_completed_ms': 1700, 'collector_success': 1}},
                'ai_observation': {'three_way_overlap': 'observed', 'baseline': counters, 'triggered_jobs': [job],
                    'samples': [{**counters, 'available': 271*audit.MIB, 'trend_memory': 89*audit.MIB,
                                 'sample_started_ms': 1100, 'sample_completed_ms': 1110, 'trend_jobs': [job]}],
                    'collection': {'exit': 0, 'report_times': ['2026-10-29T21:00:20+08:00'], 'error_times': [], 'may_be_truncated': False},
                    'health': {p: {'exit': 0, 'http_status': '200'} for p in ('/finevent/healthz', '/TrendRadar/content/')}}}

    def test_persistence_requires_observed_same_job_and_completed_collection(self):
        report = self.passed_overlap()
        result = audit.overlap_evidence(report)
        self.assertEqual(result['common_interval_ms'], 698)
        self.assertEqual(result['common_samples'], 1)
        variants = []
        for field, value in [('available', 63*audit.MIB), ('host_oom_kill', 20), ('trend_jobs', [{'pid': 10, 'start_ticks': 101}]), ('sample_completed_ms', 1701)]:
            changed = deepcopy(report)
            changed['ai_observation']['samples'][0][field] = value
            variants.append(changed)
        changed = deepcopy(report); changed['ai_observation']['collection']['error_times'] = ['failed']; variants.append(changed)
        changed = deepcopy(report); changed['ai_observation']['health']['/TrendRadar/content/']['http_status'] = '503'; variants.append(changed)
        for changed in variants:
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                audit.overlap_evidence(changed)

    def test_compose_projection_omits_credentials_and_runtime_commands(self):
        service = {'image': 'sansan0/trendradar:6.11.0', 'mem_limit': 160*audit.MIB,
                   'environment': {'API_KEY': 'SECRET'}, 'command': ['SECRET'], 'labels': {'private': 'SECRET'},
                   'secrets': ['SECRET'], 'memswap_limit': {'password': 'SECRET'},
                   'volumes': [{'type': 'bind', 'source': '/srv/example/trendradar/output', 'target': '/app/output', 'read_only': False},
                               {'type': 'volume', 'source': 'https://SECRET', 'target': '/private'}]}
        projected = audit.selected_compose_service(service)
        self.assertNotIn('SECRET', json.dumps(projected))
        self.assertNotIn('environment', projected)
        self.assertEqual(projected['mem_limit'], 160*audit.MIB)
        self.assertFalse(projected['volumes'][0]['read_only'])

    def test_compose_file_scope_refuses_external_or_relative_paths(self):
        path = '/srv/example/trendradar/docker/docker-compose.yml'
        self.assertEqual(audit.compose_paths(path), [path])
        for value in [None, '/etc/private.yml', 'docker-compose.yml', '/srv/example/trendradar/docker/../private.yml', path+',/etc/private.yml']:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                audit.compose_paths(value)

    def test_recreation_comparison_detects_drift_without_exporting_secrets(self):
        image = {'environment': ['PATH=/usr/bin', 'KEY=OLD'], 'command': ['run'], 'entrypoint': ['start'], 'user': '', 'working_dir': '/app'}
        service = {'environment': {'KEY': 'SECRET', 'EMPTY': ''}, 'command': ['run', 'SECRET']}
        actual = {**image, 'environment': ['PATH=/usr/bin', 'KEY=SECRET', 'EMPTY='], 'command': ['run', 'SECRET']}
        result = audit.launch_comparison(actual, image, service)
        self.assertTrue(result['environment_equal'])
        self.assertTrue(result['command_equal'])
        self.assertNotIn('SECRET', json.dumps(result))
        actual['environment'].append('ONE_OFF=SECRET')
        changed = audit.launch_comparison(actual, image, service)
        self.assertFalse(changed['environment_equal'])
        self.assertEqual(changed['different_environment_count'], 1)
        self.assertNotIn('ONE_OFF', json.dumps(changed))

    def test_sysctl_inventory_only_reports_swappiness_values_without_claiming_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); folder = root/'etc/sysctl.d'; folder.mkdir(parents=True)
            (root/'etc/sysctl.conf').write_text('other.key=SECRET\nvm.swappiness = 0 # SECRET\n')
            (folder/'90-custom.conf').write_text('-vm/swappiness=60\nvm.swappiness=SECRET\n')
            result = audit.swappiness_configuration(root)
            self.assertEqual([r['value'] for r in result['entries']], [0, 60, 'unrecognized_value_redacted'])
            self.assertNotIn('SECRET', json.dumps(result))
            self.assertIn('require review', result['interpretation'])

    def test_operational_fields_are_allowlisted_and_unspecified_is_not_false(self):
        config = {'ai': {'api_key': 'SECRET', 'api_base': 'https://SECRET'},
                  'ai_analysis': {'enabled': False}, 'rss': {'enabled': True, 'feeds': [
                      {'id': 'finance', 'url': 'https://SECRET', 'name': 'SECRET'},
                      {'id': 'disabled', 'enabled': False}]},
                  'notification': {'channels': {'password': 'SECRET'}}}
        result = audit.selected_config(config, {'AI_API_KEY': 'SECRET', 'ENABLE_NOTIFICATION': 'false'})
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertIsNone(result['yaml_fields']['platforms.enabled'])
        self.assertFalse(result['yaml_fields']['ai_analysis.enabled'])
        self.assertTrue(result['yaml_fields']['rss.enabled'])
        self.assertIsNone(result['sources']['rss'][0]['enabled_explicit'])
        self.assertFalse(result['sources']['rss'][1]['enabled_explicit'])
        self.assertEqual(result['environment_overrides'], {'ENABLE_NOTIFICATION': False})

    def test_wrong_types_do_not_leak_arbitrary_values(self):
        result = audit.selected_config({'rss': {'enabled': 'SECRET value'}, 'platforms': {'sources': [
            {'id': 'https://SECRET?key=SECRET', 'enabled': {'password': 'SECRET'}}]}}, {'CRON_SCHEDULE': 'SECRET', 'REPORT_MODE': {'secret': 'SECRET'}})
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(result['yaml_fields']['rss.enabled']['status'], 'unrecognized_value_redacted')
        self.assertEqual(audit.allowed_value('*/30 * * * *', 'cron'), '*/30 * * * *')

    def test_static_import_audit_never_executes_application(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'__main__.py'
            path.write_text('''__version__ = '6.11.0'
from trendradar.ai import AIAnalyzer
raise RuntimeError('THIS MUST NOT RUN')
if enabled:
    from litellm import completion
def later():
    import openai
os.getenv('AI_ANALYSIS_ENABLED', 'SECRET')
''')
            result = audit.import_inventory(path)
            self.assertEqual(result['version_literal'], '6.11.0')
            rows = result['imports']
            self.assertFalse(rows[0]['guarded'])
            self.assertFalse(rows[0]['function_scope'])
            self.assertTrue(rows[1]['guarded'])
            self.assertTrue(rows[2]['function_scope'])
            self.assertEqual(result['configuration_key_references'], [{'line': 8, 'key': 'AI_ANALYSIS_ENABLED'}])
            self.assertNotIn('SECRET', json.dumps(result))

    def test_process_identity_omits_arguments_and_inline_code(self):
        self.assertEqual(audit.application_identity(['/usr/bin/python3', '-u', '/opt/app/main.py', '--api-key', 'SECRET']), {'script': '/opt/app/main.py'})
        self.assertEqual(audit.application_identity(['python', '-m', 'trendradar', 'SECRET']), {'module': 'trendradar'})
        self.assertEqual(audit.application_identity(['python', '-c', 'SECRET']), {'inline_code': 'redacted'})
        self.assertIsNone(audit.application_identity(['unknown', 'SECRET']))
        self.assertIsNone(audit.application_identity(['python', '-X', 'SECRET']))

    def test_nginx_audit_does_not_export_credentials_or_other_locations(self):
        result = audit.nginx_trend_routes('''
location /private/ { proxy_set_header Authorization SECRET; }
location /TrendRadar/content/ {
    proxy_pass http://user:SECRET@127.0.0.1:8080/output/?token=SECRET;
    proxy_set_header Authorization SECRET;
}
location /TrendRadar/static/ { alias /srv/example/trendradar/output/; }
''')
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(len(result['targeted_locations']), 2)
        target = result['targeted_locations'][0]['directives'][0]['target']
        self.assertEqual(target, {'scheme': 'http', 'hostname': '127.0.0.1', 'port': 8080, 'path': '/output/'})

    def test_idle_gate_rejects_peak_and_imminent_cycle(self):
        target = {'pid': 1}
        with patch.object(audit.diagnostic, 'read_numbers', return_value={'MemAvailable': 127*audit.MIB}), patch.object(audit.diagnostic, 'cgroup_memory', return_value={'memory.current': 15*audit.MIB}):
            with self.assertRaisesRegex(RuntimeError, 'headroom'):
                audit.require_idle(target)
        with patch.object(audit.diagnostic, 'read_numbers', return_value={'MemAvailable': 180*audit.MIB}), patch.object(audit.diagnostic, 'cgroup_memory', return_value={'memory.current': 15*audit.MIB}), patch.object(audit.time, 'time', return_value=1790):
            with self.assertRaisesRegex(RuntimeError, 'one minute'):
                audit.require_idle(target)

    def test_container_payload_uses_only_yaml_and_metadata_not_application_imports(self):
        with patch.object(audit, 'command', return_value='{"status":"selected_fields_read"}') as command:
            audit.read_config_in_container('a'*64)
        payload = command.call_args.kwargs['input_text']
        tree = ast.parse(payload, feature_version=(3, 11))
        modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        modules.update(item.name for node in ast.walk(tree) if isinstance(node, ast.Import) for item in node.names)
        self.assertEqual(modules, {'os', 're', 'json', 'sys', 'pathlib', 'yaml', 'importlib'})
        self.assertEqual(command.call_args.args[:5], ('docker', 'exec', '-i', '-e', 'PYTHONDONTWRITEBYTECODE=1'))

    def test_source_inventory_keeps_versions_without_requirement_urls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); package = root/'trendradar'; package.mkdir()
            (package/'__init__.py').write_text('__version__ = "6.11.0"\n')
            (root/'requirements.txt').write_text('litellm>=1.0\nhttps://user:SECRET@example.com/package.whl\n--extra-index-url https://SECRET\n')
            result = audit.source_inventory(root)
            self.assertTrue(result['package_exists'])
            self.assertEqual(result['requirements'], ['litellm>=1.0'])
            self.assertNotIn('SECRET', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
