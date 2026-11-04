from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import retire_handy_stock as retire


def state(**changes):
    return {'Id': retire.UNIT, 'LoadState': 'loaded', 'ActiveState': 'active',
            'UnitFileState': 'enabled', 'MainPID': 100, **changes}


class RetireHandyTests(unittest.TestCase):
    def test_wrong_identity_and_other_unit_relationships_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            retire.validate_identity(state(Id='finevent.service'))
        for field in ('TriggeredBy', 'ConsistsOf', 'PropagatesStopTo'):
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, 'relationships'):
                retire.validate_identity(state(**{field: 'another.service'}))

    def test_also_rejects_cross_service_disable_without_exporting_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            unit = Path(temporary)/retire.UNIT
            unit.write_text('[Service]\nExecStart=/srv/example/legacy-app/node main.js\nEnvironment=KEY=SECRET\n[Install]\nAlso=another.service\n')
            with self.assertRaisesRegex(RuntimeError, 'additional units'):
                retire.validate_identity(state(FragmentPath=str(unit), DropInPaths=''))
            unit.write_text('[Service]\nExecStart=/srv/example/legacy-app/node main.js\nEnvironment=KEY=SECRET\n[Install]\nAlso=\nWantedBy=multi-user.target\n')
            result = retire.validate_identity(state(FragmentPath=str(unit), DropInPaths='', MainPID=0))
            self.assertNotIn('SECRET', str(result))
            self.assertEqual(len(result['unit_files'][0]['sha256']), 64)

    def test_only_named_api_is_stopped_and_restore_state_is_saved_first(self):
        after = state(ActiveState='inactive', UnitFileState='disabled', MainPID=0)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report = {}
            commands = []
            def command(*args):
                self.assertTrue((directory/'report.json').exists())
                self.assertEqual(report['restore_commands'], [['systemctl', 'enable', retire.UNIT], ['systemctl', 'start', retire.UNIT]])
                commands.append(args)
            with patch.object(retire, 'service_state', side_effect=[state(), after, after, after]), patch.object(retire, 'validate_identity', return_value={}), patch.object(retire.recovery, 'meminfo', return_value={'MemAvailable': 1}), patch.object(retire.recovery, 'command', side_effect=command), patch.object(retire.recovery, 'public_health', return_value={'finevent': {'exit': 0, 'http_status': '200'}}), patch.object(retire.time, 'sleep'):
                retire.retire(directory, report)
            self.assertEqual(commands, [('systemctl', 'disable', retire.UNIT), ('systemctl', 'stop', '--no-block', retire.UNIT)])
            self.assertEqual(report['status'], 'completed_for_review')

    def test_restart_or_enabled_unit_is_not_a_success(self):
        self.assertFalse(retire.stopped(state(ActiveState='inactive', MainPID=0)))
        self.assertFalse(retire.stopped(state(UnitFileState='disabled')))
        self.assertTrue(retire.stopped(state(ActiveState='inactive', MainPID=0, UnitFileState='disabled')))
        self.assertEqual(retire.restore_commands(state(UnitFileState='enabled-runtime')), [['systemctl', 'enable', '--runtime', retire.UNIT], ['systemctl', 'start', retire.UNIT]])
        self.assertEqual(retire.restore_commands(state(ActiveState='inactive', UnitFileState='disabled')), [])


if __name__ == '__main__':
    unittest.main()
