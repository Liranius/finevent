"""Validate local/CI release archives without extracting or executing their contents."""

import argparse
import hashlib
import json
from pathlib import PurePosixPath
import re
import struct
import tarfile


def check_archive(filename, kind='deployment'):
    files = {}
    seen = set()
    total = 0
    with tarfile.open(filename, 'r:gz') as archive:
        for member in archive:
            total += member.size
            if len(seen) >= 4096 or total > 128 * 1024**2:
                raise RuntimeError('Release archive exceeds validation limits')
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name or ':' in member.name:
                raise RuntimeError('Unsafe or non-POSIX archive path')
            if str(path) in seen:
                raise RuntimeError('Duplicate archive path')
            seen.add(str(path))
            if member.isdir():
                continue
            if not member.isfile():
                raise RuntimeError('Unexpected archive link')
            if path.parts[0] not in ('dist', 'deploy') and str(path) not in ('finevent', 'sha256.json', 'release.json', 'deployment-template.json'):
                raise RuntimeError('Unexpected release file')
            files[str(path)] = archive.extractfile(member).read()
    manifest = json.loads(files["sha256.json"].decode("utf-8-sig"))
    if set(manifest) != set(files) - {'sha256.json'}:
        raise RuntimeError('Manifest must cover every release file exactly')
    for name, digest in manifest.items():
        if "\\" in name or name not in files or hashlib.sha256(files[name]).hexdigest() != digest:
            raise RuntimeError(f"Invalid Linux manifest entry: {name}")
    for name in ('finevent', 'dist/index.html', 'deploy/finevent.service', 'deploy/install.py'):
        if name not in manifest:
            raise RuntimeError(f'Missing release file: {name}')
    metadata = json.loads(files['release.json'].decode('utf-8-sig')) if 'release.json' in files else None
    if metadata is None:
        if kind != 'deployment':
            raise RuntimeError('Generic release metadata is required')
    elif (metadata.get('schema') != 1 or metadata.get('kind') != kind
          or metadata.get('target') != 'linux/amd64'
          or not re.fullmatch(r'[a-f0-9]{40}', metadata.get('source_commit', ''))
          or not isinstance(metadata.get('source_dirty'), bool)):
        raise RuntimeError('Invalid release identity or kind; render generic archives before deployment')
    if kind == 'generic':
        template = json.loads(files['deployment-template.json'].decode('utf-8-sig'))
        if set(template) != {'sshTarget', 'siteHost', 'siteAlias', 'appRoot', 'dataRoot', 'backupRoot', 'trendradarRoot', 'hostStateRoot', 'legacyRoot', 'sshStageRoot', 'nginxSnippet'} or not all(isinstance(value, str) and value for value in template.values()):
            raise RuntimeError('Invalid deployment template')
    elif 'deployment-template.json' in files:
        raise RuntimeError('Deployment archive still contains its unrendered template')
    elif metadata is not None and not re.fullmatch(r'[a-f0-9]{64}', metadata.get('generic_sha256', '')):
        raise RuntimeError('Missing generic archive digest')
    for module in ('verification', 'verification_resources', 'memory_recovery', 'memory_diagnostic', 'calendar_review'):
        if f'deploy/{module}.py' not in manifest:
            raise RuntimeError(f"Missing verification dependency: {module}")
    if 'deploy/calendar-reviews.json' not in manifest:
        raise RuntimeError('Missing reviewed calendar evidence manifest')
    if b'--news' in files.get('deploy/calendar_release.py', b'') and 'deploy/news_release.py' not in manifest:
        raise RuntimeError('Missing news verification runner')
    if b'import persistence_baseline' in files.get('deploy/calendar_release.py', b'') and 'deploy/persistence_baseline.py' not in manifest:
        raise RuntimeError('Missing read-only baseline runner')
    if b'--ai' in files.get('deploy/calendar_release.py', b'') and 'deploy/ai_release.py' not in manifest:
        raise RuntimeError('Missing AI verification runner')
    if b'import news_push_release' in files.get('deploy/news_release.py', b'') and 'deploy/news_push_release.py' not in manifest:
        raise RuntimeError('Missing news push verification checks')
    if b'import source_health_release' in files.get('deploy/news_release.py', b'') and 'deploy/source_health_release.py' not in manifest:
        raise RuntimeError('Missing source health verification checks')
    if b'import calendar_links_release' in files.get('deploy/news_release.py', b'') and 'deploy/calendar_links_release.py' not in manifest:
        raise RuntimeError('Missing calendar link verification checks')
    if any('ai-config' in name for name in files):
        raise RuntimeError('Server AI credentials must not be bundled')
    binary = files["finevent"]
    if binary[:5] != b"\x7fELF\x02" or struct.unpack("<H", binary[18:20])[0] != 62:
        raise RuntimeError("Expected Linux amd64 ELF binary")
    for size in (192, 512):
        png = files[f"dist/icon-{size}.png"]
        if struct.unpack(">II", png[16:24]) != (size, size):
            raise RuntimeError("Unexpected install icon dimensions")
    return files


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive')
    parser.add_argument('--kind', choices=('generic', 'deployment'), default='deployment')
    args = parser.parse_args()
    checked = check_archive(args.archive, args.kind)
    print(f"Verified {len(checked)-1} files, {args.kind} archive, Linux amd64 binary, and PWA install icons.")
