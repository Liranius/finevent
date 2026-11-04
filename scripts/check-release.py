"""Validate the Windows-built artifact using Linux path semantics, without extraction."""

import hashlib
import json
from pathlib import PurePosixPath
import struct
import sys
import tarfile


with tarfile.open(sys.argv[1], "r:gz") as archive:
    files = {}
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or "\\" in member.name:
            raise RuntimeError("Unsafe or non-POSIX archive path")
        if member.isdir():
            continue
        if not member.isfile():
            raise RuntimeError("Unexpected archive link")
        files[str(path)] = archive.extractfile(member).read()
    manifest = json.loads(files["sha256.json"].decode("utf-8-sig"))
    for name, digest in manifest.items():
        if "\\" in name or name not in files or hashlib.sha256(files[name]).hexdigest() != digest:
            raise RuntimeError(f"Invalid Linux manifest entry: {name}")
    for module in ('verification', 'verification_resources', 'memory_recovery', 'memory_diagnostic', 'calendar_review'):
        if f'deploy/{module}.py' not in manifest:
            raise RuntimeError(f"Missing verification dependency: {module}")
    if 'deploy/calendar-reviews.json' not in manifest:
        raise RuntimeError('Missing reviewed calendar evidence manifest')
    if b'--news' in files.get('deploy/calendar_release.py', b'') and 'deploy/news_release.py' not in manifest:
        raise RuntimeError('Missing news verification runner')
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
    print(f"Verified {len(manifest)} files, Linux amd64 binary, and PWA install icons.")
