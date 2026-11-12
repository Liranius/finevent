import copy
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest
import zipfile

import test_release_bundle as bundles

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = runpy.run_path(str(ROOT/'scripts/actions-release.py'))


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.run = {'id': 42, 'event': 'push', 'head_branch': 'main', 'status': 'completed', 'conclusion': 'success', 'head_sha': 'a'*40,
                    'repository': {'id': 7, 'full_name': 'owner/project'}, 'head_repository': {'full_name': 'owner/project'}, 'workflow_id': 8}
        self.workflow = {'id': 8, 'path': '.github/workflows/ci.yml'}
        self.artifact = {'id': 9, 'name': 'finevent-linux-amd64-'+'a'*40, 'expired': False, 'digest': 'sha256:'+'b'*64,
                         'workflow_run': {'id': 42, 'head_sha': 'a'*40, 'head_branch': 'main', 'repository_id': 7, 'head_repository_id': 7}}

    def select(self, run=None, artifacts=None):
        return ACTIONS['select_artifact']('owner/project', run or self.run, self.workflow, [self.artifact] if artifacts is None else artifacts)

    def test_successful_same_repository_main_ci(self):
        self.assertEqual(self.select()['id'], 9)

    def test_failed_pr_incomplete_and_foreign_runs_are_rejected(self):
        for change in ({'event': 'pull_request'}, {'head_branch': 'feature'}, {'status': 'in_progress'}, {'conclusion': 'failure'}, {'workflow_id': 99}, {'head_repository': {'full_name': 'other/project'}}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.select({**self.run, **change})

    def test_missing_duplicate_expired_or_wrong_provenance_artifacts_rejected(self):
        for artifacts in ([], [self.artifact, self.artifact], [{**self.artifact, 'expired': True}], [{**self.artifact, 'digest': None}]):
            with self.subTest(artifacts=artifacts), self.assertRaises(RuntimeError):
                self.select(artifacts=artifacts)
        for key, value in (('head_sha', 'c'*40), ('id', 10), ('head_repository_id', 99)):
            artifact = copy.deepcopy(self.artifact)
            artifact['workflow_run'][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                self.select(artifacts=[artifact])

    def test_retry_keeps_operation_identity_across_attempts(self):
        run = {'id': 123, 'created_at': '2026-11-09T16:30:00Z', 'run_attempt': 1}
        self.assertEqual(ACTIONS['operation_id']('owner/project', run), ACTIONS['operation_id']('owner/project', {**run, 'run_attempt': 2}))
        self.assertNotEqual(ACTIONS['operation_id']('owner/project', run), ACTIONS['operation_id']('owner/project', {**run, 'id': 124}))

    def test_zip_archive_sidecar_and_source_commit_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.archive = root/'finevent-20261109T163000Z.tar.gz'
            bundles.ReleaseBundleTests.write(self, bundles.fixture())
            data = self.archive.read_bytes()
            sidecar = hashlib.sha256(data).hexdigest()+'  '+self.archive.name
            zip_path = root/'artifact.zip'
            def write(extra=False, checksum=sidecar):
                with zipfile.ZipFile(zip_path, 'w') as zipped:
                    zipped.writestr(self.archive.name, data)
                    zipped.writestr(self.archive.name+'.sha256', checksum)
                    if extra:
                        zipped.writestr('../escape', 'no')
                return 'sha256:'+hashlib.sha256(zip_path.read_bytes()).hexdigest()
            output = root/'output'
            output.mkdir()
            digest = write()
            with self.assertRaisesRegex(RuntimeError, 'digest'):
                ACTIONS['unpack_artifact'](zip_path, 'sha256:'+'0'*64, 'a'*40, output)
            with self.assertRaisesRegex(RuntimeError, 'files'):
                ACTIONS['unpack_artifact'](zip_path, write(extra=True), 'a'*40, output)
            with self.assertRaisesRegex(RuntimeError, 'sidecar'):
                ACTIONS['unpack_artifact'](zip_path, write(checksum='wrong'), 'a'*40, output)
            with self.assertRaisesRegex(RuntimeError, 'selected clean'):
                ACTIONS['unpack_artifact'](zip_path, write(), 'c'*40, output)
            good = root/'good'
            good.mkdir()
            self.assertTrue(ACTIONS['unpack_artifact'](zip_path, write(), 'a'*40, good).is_file())


if __name__ == '__main__':
    unittest.main()
