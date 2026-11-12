from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trendradar_persist_review as review


class PersistenceReviewTests(unittest.TestCase):
    def test_network_structure_includes_extra_options_without_private_values(self):
        config = {'name': 'finevent-trendradar', 'services': {'trend-radar': {
            'environment': {'API_KEY': 'SECRET'}, 'command': ['SECRET'], 'networks': {'default': {'aliases': ['SECRET']}}}},
            'networks': {'default': {'name': 'finevent-trendradar_default', 'driver': 'bridge',
                'external': False, 'labels': {'PRIVATE': 'SECRET'}, 'driver_opts': {'PRIVATE': 'SECRET'},
                'ipam': {'config': [{'subnet': 'SECRET'}]}, 'unexpected-secret-key': 'SECRET'}}}
        result = review.config_shape(config, 'finevent-trendradar')
        self.assertTrue(result['project_matches'])
        self.assertFalse(result['matches_original_network_assumption'])
        self.assertEqual(result['default_network']['driver'], 'bridge')
        self.assertEqual(result['default_network']['unknown_field_count'], 1)
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('unexpected-secret-key', json.dumps(result))

    def test_empty_network_and_project_mismatch_remain_distinct(self):
        result = review.config_shape({'name': 'different', 'services': {'trend-radar': {}}, 'networks': {}}, 'expected')
        self.assertFalse(result['project_matches'])
        self.assertEqual(result['network_count'], 0)
        self.assertEqual(result['default_network']['kind'], 'NoneType')
        self.assertFalse(result['matches_original_network_assumption'])

    def test_diagnosis_uses_stdin_and_never_changes_configuration_or_container(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            folder = Path(temporary); config = folder/'compose.yml'; config.write_bytes(b'original config')
            managed = folder/'managed'; managed.mkdir()
            original_pid = 27
            state = {'phase': 'restored', 'old': {'id': 'old', 'pid': original_pid}, 'files': []}
            (folder/'state.json').write_text(json.dumps(state))
            (folder/'report.json').write_text(json.dumps({'status': 'incomplete', 'rollback': {'status': 'restored'}}))
            before = {'name': review.persist.OLD_PROJECT, 'services': {'trend-radar': {}},
                      'networks': {'default': {'name': review.persist.OLD_PROJECT+'_default', 'driver': 'bridge'}}}
            after = {'name': review.persist.PROJECT, 'services': {'trend-radar': {}},
                     'networks': {'default': {'name': review.persist.PROJECT+'_default'}}}
            configs = iter([before, after]); commands = []
            stack.enter_context(patch.object(review.persist, 'COMPOSE', config))
            stack.enter_context(patch.object(review.persist, 'COMPOSE_SHA', review.persist.recovery.sha(config)))
            stack.enter_context(patch.object(review.persist, 'PATCH', managed/'client.py'))
            stack.enter_context(patch.object(review.persist, 'container', return_value={'id': 'old', 'pid': original_pid, 'cgroup': {}}))
            stack.enter_context(patch.object(review.persist, 'idle'))
            mutate = stack.enter_context(patch.object(review.persist.recovery, 'command', side_effect=AssertionError('Mutation is forbidden')))
            def command(*args, **kwargs):
                self.assertEqual(args[-3:], ('config', '--format', 'json'))
                self.assertIn('config', args)
                self.assertNotIn('up', args)
                commands.append((args, kwargs))
                return json.dumps(next(configs))
            stack.enter_context(patch.object(review.persist.audit, 'command', side_effect=command))
            stack.enter_context(patch.object(review.persist.recovery, 'public_health', return_value={}))
            report = {}; review.inspect(folder, report)
            self.assertEqual(report['status'], 'collected_for_review')
            self.assertEqual(report['candidate_validation']['status'], 'rejected')
            self.assertTrue(report['recovery']['patch_directory_empty'])
            args, kwargs = commands[1]
            self.assertEqual(args[args.index('-f')+1], '-')
            candidate = json.loads(kwargs['input_text'])
            self.assertEqual(candidate['services']['trend-radar']['extends']['file'], str(config))
            self.assertEqual(config.read_bytes(), b'original config')
            self.assertFalse((managed/'client.py').exists())
            mutate.assert_not_called()

    def test_unrestored_or_post_recreation_attempt_is_refused_before_inspection(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for report, state in [({'status': 'incomplete', 'rollback': {'status': 'needs_review'}}, {'phase': 'restored'}),
                                  ({'status': 'incomplete', 'rollback': {'status': 'restored'}}, {'phase': 'restored', 'container_change': True})]:
                (folder/'report.json').write_text(json.dumps(report)); (folder/'state.json').write_text(json.dumps(state))
                with patch.object(review.persist, 'container') as container, self.assertRaises(RuntimeError):
                    review.inspect(folder, {})
                container.assert_not_called()


if __name__ == '__main__':
    unittest.main()
