"""Install the fixed release controller and one restricted root SSH key. No app restart."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys


WRAPPER = b"#!/usr/bin/python3 -I\nimport runpy\nrunpy.run_path('/usr/local/lib/finevent-release/release_control.py', run_name='__main__')\n"
KEY_PREFIX = 'restrict,command="/usr/local/sbin/finevent-release" '


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def authorized_line(public):
    parts = public.strip().split()
    require(len(parts) in (2, 3) and parts[0] == 'ssh-ed25519', 'Expected an Ed25519 public key')
    decoded = base64.b64decode(parts[1], validate=True)
    require(len(decoded) == 51 and decoded[:19] == b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20', 'Invalid public key')
    return KEY_PREFIX + parts[0] + ' ' + parts[1] + ' finevent-release'


def merge_key(existing, line):
    key = line.split()[-2]
    matches = [entry for entry in existing.splitlines() if key in entry.split()]
    require(not matches or matches == [line], 'This key already has another authorized_keys entry; use a fresh dedicated key')
    return existing if matches else existing.rstrip('\n') + ('\n' if existing else '') + line + '\n'


def install(source, root=Path('/')):
    require(os.geteuid() == 0, 'Run bootstrap through the existing root login')
    os.umask(0o077)
    config = json.loads((source/'config.json').read_text(encoding='utf-8-sig'))
    for key in ('backupRoot', 'appRoot', 'dataRoot'):
        value = config[key]
        require(isinstance(value, str) and re.fullmatch(r'/[a-zA-Z0-9_./-]+', value) and '..' not in value.split('/') and '//' not in value, 'Invalid deployment path')
        require(Path(value).is_dir(), 'Existing application directories are required')
    line = authorized_line((source/'deploy.pub').read_text())
    ssh = root/'root/.ssh'
    require(not ssh.is_symlink(), 'Unexpected SSH directory link')
    ssh.mkdir(mode=0o700, exist_ok=True)
    ssh.chmod(0o700)
    os.chown(ssh, 0, 0)
    keys = ssh/'authorized_keys'
    require(not keys.is_symlink(), 'Unexpected authorized_keys link')
    existing = keys.read_text() if keys.exists() else ''
    updated = merge_key(existing, line)
    library = root/'usr/local/lib/finevent-release'
    entrypoint = root/'usr/local/sbin/finevent-release'
    require(not library.is_symlink() and not entrypoint.is_symlink(), 'Unexpected controller link')
    library.mkdir(mode=0o700, exist_ok=True)
    library.chmod(0o700)
    os.chown(library, 0, 0)
    for name in ('release_control.py', 'check-release.py', 'config.json'):
        destination = library/name
        require(not destination.is_symlink(), 'Unexpected controller file link')
        temporary = library/(name+'.new')
        require(not temporary.exists(), 'Unfinished bootstrap file requires review')
        with temporary.open('xb') as stream:
            stream.write((source/name).read_bytes())
        temporary.chmod(0o600)
        temporary.replace(destination)
    temporary = entrypoint.with_suffix('.new')
    require(not temporary.exists(), 'Unfinished entrypoint requires review')
    with temporary.open('xb') as stream:
        stream.write(WRAPPER)
    temporary.chmod(0o700)
    temporary.replace(entrypoint)
    if updated != existing:
        if keys.exists():
            backup = ssh/('authorized_keys.before-finevent-'+hashlib.sha256(existing.encode()).hexdigest()[:12])
            if not backup.exists():
                shutil.copyfile(keys, backup)
                backup.chmod(0o600)
        temporary = ssh/'authorized_keys.finevent-new'
        require(not temporary.exists(), 'Unfinished key update requires review')
        with temporary.open('x') as stream:
            stream.write(updated)
        temporary.chmod(0o600)
        temporary.replace(keys)
    keys.chmod(0o600)
    print('Restricted deployment key and release controller installed. Application unchanged.')


if __name__ == '__main__':
    try:
        install(Path(sys.argv[1]))
    except Exception:
        print('Bootstrap failed; review the private bootstrap directory on the server.', file=sys.stderr)
        sys.exit(1)
