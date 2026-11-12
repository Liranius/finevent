"""Persist the reviewed TrendRadar deployment; retain its original container for rollback."""

import argparse
from contextlib import closing
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import stat
import time

import memory_recovery as recovery
import memory_high_trial as pressure
import trendradar_audit as audit
import trendradar_lazy_trial as lazy
import verification_resources as resources

ROOT = Path('/srv/example/trendradar/docker')
COMPOSE = ROOT/'docker-compose.yml'
BASE = ROOT/'finevent-trendradar-base.yml'
PATCH = Path('/srv/example/finevent-host/trendradar/client.py')
UNIT = Path('/etc/systemd/system/trendradar.slice')
GROUP = Path('/sys/fs/cgroup/trendradar.slice')
OLD_PROJECT = 'finevent-trendradar'
PROJECT = 'finevent-trendradar-managed'
IMAGE = 'sha256:da2e26b6811b714180f1c2ee72fc0f4ff76ca8ea676847fc23c80bb35e02f536'
TAG = 'finevent/trendradar:6.11.0-base-da2e26b6811b'
CLIENT_SHA = 'c405f46e99559221b8e082f8165a96c7b08122c9ab66656ac9aa33feefc39b45'
COMPOSE_SHA = '02ff3e3fa90df6e1391c3652d5ed5f8331dc1aeab6e67b0371554c68380b916f'
FILES = (*lazy.FILES, 'trendradar_persist.py', 'trendradar_audit.py', 'verification_resources.py')
LAUNCH = ('{"environment":{{json .Config.Env}},"command":{{json .Config.Cmd}},'
          '"entrypoint":{{json .Config.Entrypoint}},"user":{{json .Config.User}},'
          '"working_dir":{{json .Config.WorkingDir}}}')
SLICE = b'''[Unit]
Description=TrendRadar memory budget
Before=docker.service

[Slice]
MemoryAccounting=yes
MemoryHigh=160M
MemoryMax=192M
MemorySwapMax=256M

[Install]
WantedBy=multi-user.target
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def remember(directory, state):
    recovery.save(directory/'state.json', state)


def compose_args(path=None, project=PROJECT):
    path = COMPOSE if path is None else path
    return ['docker', 'compose', '--project-directory', str(ROOT), '-p', project, '-f', str(path)]


def patch_volume():
    return {'type': 'bind', 'source': str(PATCH), 'target': '/app/trendradar/ai/client.py',
            'read_only': True, 'bind': {'create_host_path': False}}


def wrapper():
    return {'name': PROJECT, 'services': {'trend-radar': {
        'extends': {'file': BASE.name, 'service': 'trend-radar'}, 'image': TAG,
        'pull_policy': 'never', 'mem_limit': recovery.MEMORY,
        'memswap_limit': recovery.TOTAL_MEMORY_SWAP, 'cgroup_parent': 'trendradar.slice',
        'volumes': [patch_volume()]}}}


class ConfigurationMismatch(RuntimeError):
    def __init__(self, code, fields=()):
        super().__init__(code)
        known = {'image', 'environment', 'command', 'entrypoint', 'user', 'working_dir', 'volumes',
                 'networks', 'mem_limit', 'memswap_limit', 'cgroup_parent', 'pull_policy',
                 'restart', 'container_name', 'ports', 'labels', 'healthcheck', 'deploy'}
        self.details = {'code': code, 'fields': sorted(set(fields) & known),
                        'other_field_count': len(set(fields)-known)}


def validate_config(before, after):
    """Compare private resolved configurations without including their contents in errors."""
    for config, project in ((before, OLD_PROJECT), (after, PROJECT)):
        if set(config) - {'name', 'services', 'networks'} or set(config.get('services', {})) != {'trend-radar'}:
            raise ConfigurationMismatch('unexpected_resources_or_services')
        networks = deepcopy(config.get('networks', {}))
        default = networks.get('default') if isinstance(networks, dict) else None
        # Compose 5.0.0 emits this empty object for the audited default network.
        # A configured IPAM object or any other network attribute remains a mismatch.
        if isinstance(default, dict) and default.get('ipam') == {}:
            del default['ipam']
        if config.get('name') != project or networks != {'default': {'name': project+'_default'}}:
            raise ConfigurationMismatch('unexpected_project_or_network')
    wanted = deepcopy(before['services']['trend-radar'])
    wanted.update({k: v for k, v in wrapper()['services']['trend-radar'].items() if k not in ('extends', 'volumes')})
    volumes = wanted.setdefault('volumes', [])
    if {v.get('target') for v in volumes} != {'/app/output', '/app/config'} or len(volumes) != 2:
        raise ConfigurationMismatch('unexpected_production_volumes', ['volumes'])
    volumes.append(patch_volume())
    actual = deepcopy(after['services']['trend-radar'])
    # Compose may normalize numeric limits as decimal strings.
    for item in (wanted, actual):
        for key in ('mem_limit', 'memswap_limit'):
            item[key] = int(item[key])
        for volume in item['volumes']:
            if volume.get('bind', {}).get('create_host_path') is False:
                del volume['bind']['create_host_path']
            if volume.get('bind') == {}:
                del volume['bind']
        item['volumes'] = sorted(item['volumes'], key=lambda row: row['target'])
    if wanted != actual:
        raise ConfigurationMismatch('unexpected_service_changes', [k for k in set(wanted) | set(actual) if wanted.get(k) != actual.get(k)])


def replace_file(path, data, expected, metadata=None):
    """Atomic replacement with independent-change protection; callers resolve symlinks first."""
    if path.is_symlink() or (recovery.sha(path) if path.exists() else None) != expected:
        raise RuntimeError('Managed file changed independently; refusing replacement')
    temporary = path.with_name(path.name+'.finevent-'+str(os.getpid())+'.tmp')
    created = False
    try:
        with temporary.open('xb') as output:
            created = True
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, metadata['mode'] if metadata else 0o644)
        if metadata and hasattr(os, 'chown'):
            os.chown(temporary, metadata['uid'], metadata['gid'])
        if path.is_symlink() or (recovery.sha(path) if path.exists() else None) != expected:
            raise RuntimeError('Managed file changed while preparing replacement')
        os.replace(temporary, path)
    finally:
        if created and temporary.exists():
            temporary.unlink()


def install_file(directory, state, path, data, create_metadata=None):
    if path.is_symlink():
        raise RuntimeError('Resolve managed file aliases before writing')
    old = path.read_bytes() if path.exists() else None
    metadata = path.stat() if old is not None else None
    entry = {'path': str(path), 'before': digest(old) if old is not None else None,
             'after': digest(data), 'backup': 'file-'+str(len(state.setdefault('files', [])))+'.before',
             'metadata': {'mode': stat.S_IMODE(metadata.st_mode), 'uid': metadata.st_uid, 'gid': metadata.st_gid} if metadata else None}
    if old is not None:
        (directory/entry['backup']).write_bytes(old)
    state['files'].append(entry)
    remember(directory, state)  # Write intent before changing any production file.
    replace_file(path, data, entry['before'], entry['metadata'] or create_metadata)


def check_restore_files(directory, state):
    for entry in state.get('files', []):
        path = Path(entry['path'])
        current = recovery.sha(path) if path.exists() else None
        if path.is_symlink() or current not in (entry['before'], entry['after']):
            raise RuntimeError('Managed file changed independently; rollback requires review')
        if entry['before'] is not None and recovery.sha(directory/entry['backup']) != entry['before']:
            raise RuntimeError('Rollback backup changed')


def restore_files(directory, state):
    check_restore_files(directory, state)
    for entry in reversed(state.get('files', [])):
        path = Path(entry['path'])
        if path.is_symlink():
            raise RuntimeError('Managed file became a symlink; rollback requires review')
        current = recovery.sha(path) if path.exists() else None
        if current == entry['before']:
            continue
        if current != entry['after']:
            raise RuntimeError('Managed file changed independently; rollback requires review')
        if entry['before'] is None:
            path.unlink()
        else:
            data = (directory/entry['backup']).read_bytes()
            if digest(data) != entry['before']:
                raise RuntimeError('Rollback backup changed')
            replace_file(path, data, current, entry['metadata'])


def swappiness_edits(root=Path('/')):
    inventory = audit.swappiness_configuration(root)['entries']
    allowed = {str(root/'etc/sysctl.conf'), str(root/'etc/sysctl.d/99-sysctl.conf')}
    if not inventory or any(row.get('path') not in allowed or row.get('value') != 0 for row in inventory):
        raise RuntimeError('Swappiness definitions changed since the reviewed audit')
    edits = {}
    for row in inventory:
        path = Path(row['path']).resolve(strict=True)
        if not path.is_relative_to((root/'etc').resolve()) or not path.is_file() or path.stat().st_nlink != 1:
            raise RuntimeError('Unexpected sysctl target or hard link')
        data = path.read_bytes()
        changed, count = re.subn(rb'(?m)^(\s*-?vm[./]swappiness\s*=\s*)0(?=\s*(?:[#;]|$))', rb'\g<1>60', data)
        if not count:
            raise RuntimeError('Could not locate the reviewed swappiness assignment')
        edits[path] = changed  # /etc/sysctl.d alias may resolve to /etc/sysctl.conf.
    return edits


def container(name='trend-radar', optional=False):
    result = recovery.command('docker', 'inspect', '--format',
        '{"id":{{json .Id}},"name":{{json .Name}},"pid":{{.State.Pid}},"running":{{.State.Running}},'
        '"image":{{json .Image}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
        '"memory":{{.HostConfig.Memory}},"memory_swap":{{.HostConfig.MemorySwap}},'
        '"restarts":{{.RestartCount}},"restart":{{json .HostConfig.RestartPolicy.Name}},'
        '"parent":{{json .HostConfig.CgroupParent}}}', name, check=False)
    if result.returncode:
        # Distinguish a missing object from a Docker daemon failure.
        filters = ['--filter', 'name=^/trend-radar$'] if name == 'trend-radar' else []
        ids = recovery.command('docker', 'ps', '-aq', '--no-trunc', *filters).stdout.split()
        if optional and (name == 'trend-radar' or re.fullmatch(r'[a-f0-9]{64}', name)):
            if (not ids if name == 'trend-radar' else name not in ids):
                return None
        raise RuntimeError('Container inspection failed')
    target = json.loads(result.stdout)
    if target['running']:
        target['cgroup'] = recovery.diagnostic.cgroup_memory(target['pid'])
    return target


def check_parent(group=GROUP):
    expected = {'memory.high': pressure.HIGH, 'memory.max': recovery.MEMORY,
                'memory.swap.max': recovery.TOTAL_MEMORY_SWAP-recovery.MEMORY}
    if any((group/key).read_text().strip() != str(value) for key, value in expected.items()):
        raise RuntimeError('Persistent parent slice resource limits are not effective')
    events = recovery.diagnostic.read_numbers(group/'memory.events', {'oom', 'oom_kill'})
    if events != {'oom': 0, 'oom_kill': 0}:
        raise RuntimeError('New deployment parent slice has memory exhaustion events')
    return expected


def same_launch(before, after):
    def normalized(value):
        result = deepcopy(value)
        result['environment'] = dict(item.split('=', 1) for item in result.get('environment') or [])
        return result
    return normalized(before) == normalized(after)


def new_target(state):
    target = container()
    if target['id'] == state['old']['id'] or target['project'] != PROJECT or target['image'] != IMAGE:
        raise RuntimeError('New container ownership or image mismatch')
    if state.get('new_id') and state['new_id'] != target['id']:
        raise RuntimeError('Managed container identity changed')
    if state.get('observed_pid') and state['observed_pid'] != target['pid']:
        raise RuntimeError('Managed container restarted during observation')
    if not target['running'] or target['restart'] != 'unless-stopped' or target['parent'] != 'trendradar.slice':
        raise RuntimeError('Managed container is not running with the persistent restart policy')
    recovery.validate_limits(target, recovery.MEMORY, recovery.TOTAL_MEMORY_SWAP)
    if not str(target['cgroup']['path']).startswith('/trendradar.slice/'):
        raise RuntimeError('Managed container is outside the persistent parent slice')
    check_parent()
    if target['cgroup']['memory.events']['oom_kill'] or recovery.sha(lazy.source_path(target)) != CLIENT_SHA:
        raise RuntimeError('New container exhausted memory or lost the tested patch')
    mounts = json.loads(audit.command('docker', 'inspect', '--format', '{{json .Mounts}}', target['id']))
    expected = {('/app/config', '/srv/example/trendradar/config', False),
                ('/app/output', '/srv/example/trendradar/output', True),
                ('/app/trendradar/ai/client.py', str(PATCH), False)}
    if len(mounts) != 3 or {(v['Destination'], v['Source'], v['RW']) for v in mounts if v['Type'] == 'bind'} != expected:
        raise RuntimeError('New container bind mounts differ from the reviewed deployment')
    return target


def idle(target):
    deadline = time.monotonic()+240
    while True:
        try:
            audit.require_idle(target)
            group = recovery.diagnostic.cgroup_memory(target['pid'])['path']
            if resources.trend_jobs(group):
                raise RuntimeError('TrendRadar collection is still running')
            if time.time() % 1800 > 1680:
                raise RuntimeError('Next scheduled collection is too close')
            return
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(5)


def database_backup(source, destination):
    started = time.monotonic()
    def bounded(*_):
        if time.monotonic()-started > 30:
            raise RuntimeError('Database backup deadline exceeded')
    with closing(lazy.read_database(source)) as input_db, closing(sqlite3.connect(destination)) as output_db:
        input_db.backup(output_db, pages=128, progress=bounded)
        if output_db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise RuntimeError('Database backup integrity check failed')
    return recovery.sha(destination)


def sysctl_loader():
    for name in ('/usr/lib/systemd/systemd-sysctl', '/lib/systemd/systemd-sysctl'):
        if Path(name).is_file():
            return name
    raise RuntimeError('The systemd boot-time sysctl loader is unavailable')


def reviewed_retry(report, target):
    review_id = report.get('review_id')
    if not review_id:
        return False
    if not re.fullmatch(r'trendradar-persist-review-\d{8}T\d{6}Z-[a-f0-9]{8}', review_id):
        raise RuntimeError('Invalid retry review ID')
    review_path = recovery.BACKUPS/review_id/'report.json'
    review = json.loads(review_path.read_text())
    previous_id = review.get('previous_run', '')
    if (review.get('id') != review_id or review.get('status') != 'collected_for_review' or review.get('stage') != 'completed'
            or not re.fullmatch(r'trendradar-persist-\d{8}T\d{6}Z-[a-f0-9]{8}', previous_id)):
        raise RuntimeError('Retry requires a completed diagnosis of the preceding failure')
    folder = recovery.BACKUPS/previous_id
    if recovery.sha(folder/'report.json') != review['previous_report_sha256']:
        raise RuntimeError('Preceding failure evidence changed after diagnosis')
    previous = json.loads((folder/'report.json').read_text())
    state = json.loads((folder/'state.json').read_text())
    if (previous.get('status') != 'incomplete' or previous.get('rollback', {}).get('status') != 'restored'
            or state.get('phase') != 'restored' or state.get('container_change') or state.get('slice_attempted')):
        raise RuntimeError('Preceding failure was not restored before container recreation')
    if any(state['old'][key] != target[key] for key in ('id', 'pid')):
        raise RuntimeError('Original container changed since the reviewed failure')
    allowed = {str(COMPOSE), str(BASE), str(PATCH)}
    entries = state.get('files', [])
    if (len(entries) != 3 or {row['path'] for row in entries} != allowed
            or not any(row['path'] == str(PATCH) and row['before'] is None and row['after'] == CLIENT_SHA for row in entries)):
        raise RuntimeError('Preceding file intents do not establish ownership of the patch directory')
    check_restore_files(folder, state)
    if any((recovery.sha(Path(row['path'])) if Path(row['path']).exists() else None) != row['before'] for row in entries):
        raise RuntimeError('Preceding deployment files are not restored')
    report['retry_review'] = {'id': review_id, 'previous_run': previous_id, 'sha256': recovery.sha(review_path)}
    return True


def check_patch_directory(retry_allowed):
    path = PATCH.parent
    if path.is_symlink():
        raise RuntimeError('Managed patch directory must not be a symlink')
    if not path.exists():
        return False
    metadata = path.stat()
    if (not retry_allowed or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o700 or any(path.iterdir())):
        raise RuntimeError('Only the reviewed empty root-owned patch directory may be reused')
    return True


def restore(directory, state):
    if state.get('phase') == 'restored':
        return {'status': 'restored'}
    if not state.get('files') and not state.get('container_change'):
        return {'status': 'no_changes'}
    check_restore_files(directory, state)
    if state.get('container_change'):
        old = container(state['old']['id'])
        if old['project'] != OLD_PROJECT or old['image'] != IMAGE or old['name'] not in ('/trend-radar', '/'+state['retained_name']):
            raise RuntimeError('Original container identity changed; rollback requires review')
        current = container(optional=True)
        if current and current['id'] != state['old']['id']:
            if current['project'] != PROJECT or current['image'] != IMAGE or (state.get('new_id') and current['id'] != state['new_id']):
                raise RuntimeError('Unowned container occupies the production name; rollback requires review')
            recovery.command('docker', 'stop', '--time', '45', current['id'], timeout=60)
            recovery.command('docker', 'rm', current['id'])  # Never remove volumes/data.
    if state.get('slice_attempted') and not state.get('slice_disabled'):
        recovery.command('systemctl', 'disable', '--now', 'trendradar.slice')
        state['slice_disabled'] = True
        remember(directory, state)
    # Check and restore all managed files before starting the original deployment.
    restore_files(directory, state)
    if state.get('slice_attempted'):
        recovery.command('systemctl', 'daemon-reload')
    if state.get('container_change'):
        if old['name'] != '/trend-radar':
            recovery.command('docker', 'rename', old['id'], 'trend-radar')
        recovery.command('docker', 'update', '--restart=unless-stopped', old['id'])
        recovery.command('docker', 'start', old['id'], timeout=60)
        recovery.command('systemctl', 'set-property', '--runtime', 'docker-'+old['id']+'.scope', 'MemoryHigh='+str(pressure.HIGH))
        recovery.validate_limits(container(old['id']), recovery.MEMORY, recovery.TOTAL_MEMORY_SWAP)
    if state.get('files'):
        recovery.command('sysctl', '-w', 'vm.swappiness=60')  # Pre-change runtime policy was already 60.
    health = recovery.public_health()
    if any(health.get(path) != {'exit': 0, 'http_status': '200'} for path in ('/finevent/healthz', '/TrendRadar/content/')):
        raise RuntimeError('Original deployment restored but HTTPS recovery needs review')
    state['phase'] = 'restored'
    remember(directory, state)
    return {'status': 'restored', 'health': health,
            'data': 'New SQLite and HTML data retained; database snapshots were not restored.'}


def apply(directory, state, report):
    report.update(status='applying', stage='preflight')
    recovery.save(directory/'report.json', report)
    evidence = json.loads((recovery.BACKUPS/report['audit_id']/'report.json').read_text())
    if evidence.get('status') != 'collected_for_review' or evidence.get('container', {}).get('id') != recovery.EXPECTED_CONTAINER:
        raise RuntimeError('Reviewed persistence audit is missing or refers to another container')
    verification_path = recovery.BACKUPS/evidence['verification']['id']/'report.json'
    if recovery.sha(verification_path) != evidence['verification_sha256']:
        raise RuntimeError('The successful concurrent verification evidence changed')
    report['verification'] = audit.overlap_evidence(json.loads(verification_path.read_text()))
    old = container()
    if (old['id'], old['image'], old['project'], old['restart'], old['parent']) != (recovery.EXPECTED_CONTAINER, IMAGE, OLD_PROJECT, 'unless-stopped', ''):
        raise RuntimeError('Production container no longer matches the reviewed deployment')
    recovery.validate_limits(old, recovery.MEMORY, recovery.TOTAL_MEMORY_SWAP)
    if pressure.read_high() != str(pressure.HIGH) or pressure.read_swappiness() != pressure.SWAPPINESS:
        raise RuntimeError('Previously validated runtime resource policies changed')
    if recovery.sha(COMPOSE) != COMPOSE_SHA or COMPOSE.is_symlink():
        raise RuntimeError('Production Compose file changed since review')
    retry_allowed = reviewed_retry(report, old)
    check_patch_directory(retry_allowed)
    if any(p.exists() or p.is_symlink() for p in (BASE, UNIT)) or GROUP.exists():
        raise RuntimeError('A managed deployment path already exists; review before reapplying')
    if recovery.command('systemctl', 'is-enabled', 'docker.service', check=False).stdout.strip() != 'enabled':
        raise RuntimeError('Docker is not enabled for host startup')
    idle(old)
    client = lazy.source_path(old).read_bytes()
    if digest(client) != CLIENT_SHA:
        raise RuntimeError('The validated source patch changed')
    sysctl_edits = swappiness_edits()
    before = json.loads(audit.command(*compose_args(project=OLD_PROJECT), 'config', '--format', 'json'))
    actual_launch = json.loads(audit.command('docker', 'inspect', '--format', LAUNCH, old['id']))
    image_launch = json.loads(audit.command('docker', 'image', 'inspect', '--format', LAUNCH, IMAGE))
    comparison = audit.launch_comparison(actual_launch, image_launch, before['services']['trend-radar'])
    if not all(comparison[k] for k in comparison if k.endswith('_equal')):
        raise RuntimeError('Current launch environment or command differs from Compose')
    report['launch_comparison'] = comparison
    report['stage'] = 'validate_compose_preview'
    recovery.save(directory/'report.json', report)
    preview_input = wrapper()
    preview_input['services']['trend-radar']['extends']['file'] = str(COMPOSE)
    preview = json.loads(audit.command(*compose_args(path=Path('-')), 'config', '--format', 'json', input_text=json.dumps(preview_input)))
    validate_config(before, preview)
    report['compose_preview_validated'] = True
    report['stage'] = 'backups'
    state.update(old=old, retained_name='trend-radar-before-'+report['id'], phase='backups')
    remember(directory, state)
    output = lazy.output_directory(old)
    report['backups'] = lazy.backup_outputs(directory, output)
    report['backups']['finevent_sha256'] = database_backup(Path('/srv/example/finevent-data/finevent.db'), directory/'finevent.before.db')
    (directory/'client.before.py').write_bytes(client)
    finevent_pid = int(audit.command('systemctl', 'show', 'finevent.service', '-p', 'MainPID', '--value'))
    baseline = recovery.sample(old, finevent_pid)
    previous_baseline = json.loads(verification_path.read_text())['ai_observation']['baseline']
    if any(baseline[k] != previous_baseline[k] for k in ('trend_oom_kill', 'finevent_oom_kill', 'host_oom_kill')):
        raise RuntimeError('New OOM since the successful verification; persistence requires review')
    report['before'] = baseline
    # Exact image, no build and no network pull. A local tag is necessary because the audited
    # reference may be a local image ID rather than a registry manifest digest.
    tagged = recovery.command('docker', 'image', 'inspect', '--format', '{{.Id}}', TAG, check=False)
    if tagged.returncode == 0 and tagged.stdout.strip() != IMAGE:
        raise RuntimeError('Managed image tag already points at another image')
    recovery.command('docker', 'image', 'tag', IMAGE, TAG)
    reused = check_patch_directory(retry_allowed)
    if not reused:
        PATCH.parent.mkdir(mode=0o700)
    report['reused_reviewed_patch_directory'] = reused
    install_file(directory, state, PATCH, client)
    metadata = COMPOSE.stat()
    install_file(directory, state, BASE, COMPOSE.read_bytes(),
                 {'mode': stat.S_IMODE(metadata.st_mode), 'uid': metadata.st_uid, 'gid': metadata.st_gid})
    install_file(directory, state, COMPOSE, (json.dumps(wrapper(), indent=2)+'\n').encode())
    report['stage'] = 'validate_installed_compose'
    after = json.loads(audit.command(*compose_args(), 'config', '--format', 'json'))
    validate_config(before, after)
    report['stage'] = 'persist_resource_policy'
    for path, data in sysctl_edits.items():
        install_file(directory, state, path, data)
    recovery.command(sysctl_loader(), '--prefix=vm.swappiness')
    if pressure.read_swappiness() != 60:
        raise RuntimeError('Persisted sysctl definitions do not produce swappiness 60')
    install_file(directory, state, UNIT, SLICE)
    state['slice_attempted'] = True
    remember(directory, state)
    recovery.command('systemctl', 'daemon-reload')
    recovery.command('systemctl', 'enable', '--now', 'trendradar.slice')
    check_parent()
    idle(old)
    report['stage'] = 'recreate_container'
    recovery.save(directory/'report.json', report)
    state['container_change'] = True
    remember(directory, state)
    recovery.command('docker', 'update', '--restart=no', old['id'])
    recovery.command('docker', 'stop', '--time', '45', old['id'], timeout=60)
    recovery.command('docker', 'rename', old['id'], state['retained_name'])
    recovery.command(*compose_args(), 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'trend-radar', timeout=120)
    target = new_target(state)
    state['new_id'] = target['id']
    remember(directory, state)
    new_launch = json.loads(audit.command('docker', 'inspect', '--format', LAUNCH, target['id']))
    if not same_launch(actual_launch, new_launch):
        raise RuntimeError('Recreated container launch configuration differs from the original')
    # A restart exercises re-mounting and the existing boot entrypoint without restarting Docker.
    idle(target)
    recovery.command('docker', 'restart', '--time', '45', target['id'], timeout=90)
    restarted = new_target(state)
    if restarted['pid'] == target['pid']:
        raise RuntimeError('Container restart was not observed')
    after_start = recovery.sample(restarted, finevent_pid)
    if any(after_start[k] != baseline[k] for k in ('host_oom_kill', 'finevent_oom_kill')):
        raise RuntimeError('OOM occurred during recreation or restart')
    if audit.command('systemctl', 'is-enabled', 'trendradar.slice').strip() != 'enabled':
        raise RuntimeError('Parent slice is not enabled at startup')
    state['observed_pid'] = restarted['pid']
    remember(directory, state)
    report['startup'] = {'recreated': True, 'restarted': True, 'host_reboot_tested': False,
                         'parent_limits': check_parent(), 'swappiness': pressure.read_swappiness(),
                         'new_container_id': restarted['id'], 'retained_container': state['retained_name']}
    report.update(status='observing', stage='observe')
    recovery.save(directory/'report.json', report)
    recovery.observe(directory, report, target_provider=lambda: new_target(state),
        sample_guard=pressure.pressure_guard(report, monitor_host=True, cgroup=GROUP),
        on_failure=lambda: recovery.command('docker', 'stop', '--time', '30', state['new_id'], timeout=45))
    if report['status'] != 'completed_for_review':
        raise RuntimeError('Recreated deployment failed the resource or collection observation')
    if report['resources']['max_sample_interval_seconds'] > 15 or any(
            report['resources']['baseline_counters'][key] != after_start[key]
            for key in ('trend_oom_kill', 'host_oom_kill', 'finevent_oom_kill')):
        raise RuntimeError('Observation has a sampling gap or unaccounted memory event')
    report['stage'] = 'verify_outputs'
    start = int(datetime.fromisoformat(report['observation_started']).timestamp())
    report['outputs'] = lazy.verify_outputs(output, Path('/srv/example/finevent-data/finevent.db'), start, int(time.time()))
    if report['outputs']['source_failures']:
        raise RuntimeError('A news source failed during the two-cycle acceptance')
    for entry in state['files']:
        if recovery.sha(Path(entry['path'])) != entry['after']:
            raise RuntimeError('Managed deployment file changed during observation')
    new_target(state)
    state['phase'] = 'accepted'
    report.update(stage='completed', configuration='Persistent Compose, read-only source patch, slice and sysctl retained; original container stopped for rollback.')
    # TODO: This bind patch targets exactly TrendRadar 6.11.0 / IMAGE / CLIENT_SHA. Rebase and
    # rerun workflow + collection/resource validation before upgrading the base image.


def run(directory, state, report):
    try:
        apply(directory, state, report)
    except BaseException as error:
        report.update(status='incomplete', error_type=type(error).__name__, error_details=lazy.error_details(error))
        if isinstance(error, ConfigurationMismatch):
            report['configuration_error'] = error.details
        # Do not export exception strings: third-party errors can embed resolved configuration.
        try:
            report['rollback'] = restore(directory, state)
        except BaseException as rollback_error:
            report['rollback'] = {'status': 'needs_review', 'error_type': type(rollback_error).__name__,
                                  'error_details': lazy.error_details(rollback_error)}
        raise
    finally:
        remember(directory, state)


def launch(directory, report):
    directory.mkdir(mode=0o700)
    for name in FILES:
        shutil.copy2(Path(__file__).with_name(name), directory/name)
    recovery.save(directory/'report.json', report)
    try:
        recovery.command('systemd-run', '--unit=finevent-'+report['id']+'.service', '--property=Type=exec',
            '--property=RuntimeMaxSec=4800', '--property=TimeoutStopSec=180',
            '--property=MemoryMax=128M', '--property=MemorySwapMax=0', '--property=UMask=0077',
            '/usr/bin/python3', '-B', str(directory/'trendradar_persist.py'), '--run-id', report['id'])
    except BaseException:
        recovery.save(directory/'launch-error.json', {'status': 'launch_uncertain'})
        raise
    print('Persistence verification started in the background. Evidence: '+str(directory/'report.json'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--audit-id')
    parser.add_argument('--review-id')
    actions = parser.add_mutually_exclusive_group()
    for name in ('launch', 'status', 'rollback'):
        actions.add_argument('--'+name, action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'trendradar-persist-\d{8}T\d{6}Z-[a-f0-9]{8}', args.run_id) or os.geteuid() != 0:
        raise RuntimeError('Root and a valid persistence ID are required')
    os.umask(0o077)
    directory = recovery.BACKUPS/args.run_id
    if args.status:
        lazy.export_status(directory, args.run_id)
        return
    if args.launch:
        if not re.fullmatch(r'trendradar-audit-\d{8}T\d{6}Z-[a-f0-9]{8}', args.audit_id or ''):
            raise RuntimeError('A valid persistence audit ID is required')
        if args.review_id and not re.fullmatch(r'trendradar-persist-review-\d{8}T\d{6}Z-[a-f0-9]{8}', args.review_id):
            raise RuntimeError('A valid retry review ID is required')
        launch(directory, {'id': args.run_id, 'audit_id': args.audit_id, 'review_id': args.review_id, 'status': 'queued',
            'unit': 'finevent-'+args.run_id+'.service', 'created_at': recovery.diagnostic.stamp(time.time()),
            'rollback_command': 'python3 -B '+str(directory/'trendradar_persist.py')+' --run-id '+args.run_id+' --rollback'})
        return
    import fcntl
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    report = json.loads((directory/'report.json').read_text())
    state = json.loads((directory/'state.json').read_text()) if (directory/'state.json').exists() else {}
    owns = False
    try:
        with Path('/run/lock/finevent-memory-recovery.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.rollback:
                owns = True
                report['rollback'] = restore(directory, state)
                report['status'] = 'rolled_back'
            else:
                if report['status'] != 'queued' or state:
                    raise RuntimeError('This persistence run already started; use status or rollback')
                owns = True
                run(directory, state, report)
    except BlockingIOError:
        if args.rollback or report['status'] != 'queued':
            raise RuntimeError('Observation is active; stop its recorded systemd unit to request rollback')
        report.update(status='incomplete', stage='lock', error_type='BlockingIOError', rollback={'status': 'no_changes'})
        owns = True
    finally:
        if owns:
            report['finished_at'] = recovery.diagnostic.stamp(time.time())
            recovery.save(directory/'report.json', report)
            print('Evidence: '+str(directory/'report.json'), flush=True)


if __name__ == '__main__':
    main()
