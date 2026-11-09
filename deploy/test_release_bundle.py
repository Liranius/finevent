import hashlib
import io
import json
from pathlib import Path
import runpy
import struct
import tarfile
import tempfile
import unittest

import install


ROOT = Path(__file__).resolve().parents[1]
check_archive = runpy.run_path(str(ROOT/'scripts/check-release.py'))['check_archive']


def fixture():
    files = {f'deploy/{name}.py': b'# fixture' for name in ('install', 'verification', 'verification_resources', 'memory_recovery', 'memory_diagnostic', 'calendar_review')}
    files.update({'finevent': b'\x7fELF\x02'+b'\0'*13+struct.pack('<H', 62),
                  'dist/index.html': b'<html></html>', 'dist/.vite/manifest.json': b'{}', 'deploy/finevent.service': b'[Service]',
                  'deploy/calendar-reviews.json': b'{}',
                  'deployment-template.json': (ROOT/'.deployment.example.json').read_bytes(),
                  'release.json': json.dumps({'schema': 1, 'kind': 'generic', 'target': 'linux/amd64', 'source_commit': 'a'*40, 'source_dirty': False}).encode()})
    for size in (192, 512):
        files[f'dist/icon-{size}.png'] = b'\0'*16+struct.pack('>II', size, size)
    return files


class ReleaseBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.archive = Path(self.temporary.name)/'release.tar.gz'

    def write(self, files, *, manifest=None, extra=None):
        files = dict(files)
        if manifest is None:
            manifest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        files['sha256.json'] = json.dumps(manifest).encode()
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, data in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
            if extra:
                archive.addfile(extra, io.BytesIO(b''))

    def test_generic_is_valid_but_cannot_be_deployed(self):
        self.write(fixture())
        self.assertIn('finevent', check_archive(self.archive, 'generic'))
        with self.assertRaisesRegex(RuntimeError, 'kind'):
            check_archive(self.archive)

    def test_legacy_deployment_archives_remain_valid(self):
        files = fixture()
        del files['release.json'], files['deployment-template.json']
        self.write(files)
        check_archive(self.archive)
        with self.assertRaisesRegex(RuntimeError, 'metadata'):
            check_archive(self.archive, 'generic')

    def test_missing_or_unlisted_content_is_rejected(self):
        files = fixture()
        manifest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        for name in ('finevent', 'dist/index.html', 'dist/.vite/manifest.json', 'deploy/install.py'):
            with self.subTest(name=name):
                changed = dict(files)
                del changed[name]
                self.write(changed, manifest=manifest)
                with self.assertRaisesRegex(RuntimeError, 'cover every'):
                    check_archive(self.archive, 'generic')
        self.write(files, manifest={name: digest for name, digest in manifest.items() if name != 'finevent'})
        with self.assertRaisesRegex(RuntimeError, 'cover every'):
            check_archive(self.archive, 'generic')

    def test_corrupted_binary_checksum_is_rejected(self):
        files = fixture()
        manifest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        files['finevent'] += b'changed'
        self.write(files, manifest=manifest)
        with self.assertRaisesRegex(RuntimeError, 'manifest entry'):
            check_archive(self.archive, 'generic')

    def test_unsafe_paths_links_and_duplicates_are_rejected(self):
        for name in ('../escape', '/absolute', 'dist/../../escape', 'dist\\escape', 'C:/escape'):
            with self.subTest(name=name):
                self.write({**fixture(), name: b''})
                with self.assertRaisesRegex(RuntimeError, 'Unsafe'):
                    check_archive(self.archive, 'generic')
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            entry = tarfile.TarInfo('dist/link')
            entry.type, entry.linkname = kind, '../finevent'
            self.write(fixture(), extra=entry)
            with self.assertRaisesRegex(RuntimeError, 'link'):
                check_archive(self.archive, 'generic')
        self.write(fixture(), extra=tarfile.TarInfo('./finevent'))
        with self.assertRaisesRegex(RuntimeError, 'Duplicate'):
            check_archive(self.archive, 'generic')

    def test_credentials_wrong_target_and_identity_are_rejected(self):
        files = fixture()
        for name in ('deploy/ai-config.json', '.deployment.local.json'):
            self.write({**files, name: b'private'})
            with self.assertRaises(RuntimeError):
                check_archive(self.archive, 'generic')
        self.write({**files, 'finevent': files['finevent'][:18]+struct.pack('<H', 183)})
        with self.assertRaisesRegex(RuntimeError, 'amd64'):
            check_archive(self.archive, 'generic')
        metadata = json.loads(files['release.json'])
        metadata['source_commit'] = 'unknown'
        self.write({**files, 'release.json': json.dumps(metadata).encode()})
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            check_archive(self.archive, 'generic')

    def test_installer_rejects_generic_bundle_before_changes(self):
        directory = Path(self.temporary.name)/'bundle'
        files = fixture()
        for name, data in files.items():
            target = directory/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (directory/'sha256.json').write_text(json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in files.items()}))
        with self.assertRaisesRegex(RuntimeError, 'Render the generic'):
            install.verify_bundle(directory)


if __name__ == '__main__':
    unittest.main()
