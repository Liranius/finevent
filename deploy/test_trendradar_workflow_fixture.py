import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trendradar_workflow_fixture as fixture


class WorkflowFixtureTests(unittest.TestCase):
    def test_new_directory_and_environment_do_not_inherit_production_settings(self):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {
            'AI_API_KEY': 'do-not-export', 'CONFIG_PATH': '/app/config/config.yaml',
            'STORAGE_BACKEND': 'remote', 'AI_ANALYSIS_ENABLED': 'true', 'FEISHU_WEBHOOK_URL': 'secret',
        }):
            directory = Path(temporary)/'fixture'
            try:
                fixture.prepare(directory)
                self.assertNotIn('AI_API_KEY', os.environ)
                self.assertNotIn('AI_ANALYSIS_ENABLED', os.environ)
                self.assertNotIn('STORAGE_BACKEND', os.environ)
                self.assertNotIn('FEISHU_WEBHOOK_URL', os.environ)
                self.assertEqual(Path(os.environ['CONFIG_PATH']), directory/'config.yaml')
                config = json.loads((directory/'config.yaml').read_text())
                self.assertFalse(config['notification']['enabled'])
                self.assertFalse(config['ai_analysis']['enabled'])
                self.assertEqual(config['storage']['local']['data_dir'], 'output')
                self.assertEqual(len(config['platforms']['sources']), 9)
                with self.assertRaises(FileExistsError):
                    fixture.prepare(directory)
            finally:
                os.chdir(original_cwd)

    def test_changed_installed_source_cannot_use_stale_workflow_fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first = directory/next(iter(fixture.PINNED))
            first.parent.mkdir(parents=True)
            first.write_bytes(b'different upstream implementation')
            with self.assertRaisesRegex(RuntimeError, 'audited 6.11.0'):
                fixture.verify_source(directory)


if __name__ == '__main__':
    unittest.main()
