"""Publish static files and switch the Nginx document root without touching the API."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import quote


def public_directory(path):
    path.mkdir(exist_ok=True)
    path.chmod(0o755)


def stage(web, version, files):
    """Only dist members of a checked package enter the public directory."""
    public_directory(web)
    releases = web/'releases'
    public_directory(releases)
    assets = web/'assets'
    public_directory(assets)
    destination = releases/version
    with tempfile.TemporaryDirectory(dir=releases) as temporary:
        staged = Path(temporary)/'site'
        public_directory(staged)
        for name, data in files.items():
            if not name.startswith('dist/'):
                continue
            relative = Path(name.removeprefix('dist/'))
            target = staged/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o644)
            if relative.parts[0] == 'assets':
                # Keep immutable URLs usable by already open pages across deployments.
                shared = assets/relative.relative_to('assets')
                shared.parent.mkdir(parents=True, exist_ok=True)
                for parent in shared.parents:
                    if parent == assets:
                        break
                    parent.chmod(0o755)
                try:
                    os.link(target, shared)
                except FileExistsError:
                    if shared.read_bytes() != data:
                        raise RuntimeError('Immutable web asset name was reused with different bytes')
        for directory in staged.rglob('*'):
            if directory.is_dir():
                directory.chmod(0o755)
        if destination.exists():
            existing = {p.relative_to(destination): p.read_bytes() for p in destination.rglob('*') if p.is_file()}
            expected = {p.relative_to(staged): p.read_bytes() for p in staged.rglob('*') if p.is_file()}
            if existing != expected:
                raise RuntimeError('Web version already exists with different files')
        else:
            staged.rename(destination)
    return destination


def current(web):
    link = web/'current'
    if not link.is_symlink():
        if link.exists():
            raise RuntimeError('Web current must be a release symlink')
        return None
    target = link.resolve(strict=True)
    if target.parent != (web/'releases').resolve() or not target.is_dir():
        raise RuntimeError('Unexpected active web release')
    return target.name


def switch(web, version):
    if version is None:
        (web/'current').unlink(missing_ok=True)
        return
    target = web/'releases'/version
    if not (target/'index.html').is_file():
        raise RuntimeError('Missing web release')
    with tempfile.TemporaryDirectory(dir=web) as temporary:
        link = Path(temporary)/'next'
        link.symlink_to(target.resolve(), target_is_directory=True)
        link.replace(web/'current')


def verify(host, directory):
    # Verify the bytes through the actual HTTPS server, including the PWA update files.
    prefix = '/finevent/'
    urls = json.loads((directory/'precache.json').read_bytes())['urls']
    names = {'index.html', 'sw.js', 'precache.json'}
    for url in urls:
        path = Path(url.removeprefix(prefix))
        if not url.startswith(prefix) or path.is_absolute() or '..' in path.parts:
            raise RuntimeError('Invalid precache URL')
        names.add(path.as_posix())
    for name in sorted(names):
        result = subprocess.run(['curl', '--disable', '--noproxy', '*', '--fail', '--silent',
                                 '--max-time', '5', '--resolve', f'{host}:443:127.0.0.1',
                                 f'https://{host}/finevent/{quote(name)}'], capture_output=True, check=True)
        if hashlib.sha256(result.stdout).digest() != hashlib.sha256((directory/name).read_bytes()).digest():
            raise RuntimeError('Static HTTPS response differs from the selected release')
