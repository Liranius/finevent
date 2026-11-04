"""Read-only diagnosis of a restored persistence attempt; never apply or retry it."""

import argparse
import json
import os
from pathlib import Path
import re
import time

import trendradar_persist as persist


def config_shape(config, project):
    """Export deployment structure only, not environment, labels, driver options or commands."""
    def token(value):
        return value if value is None or isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', value) else 'redacted'
    networks = config.get('networks') or {}
    default = networks.get('default')
    projection = {'kind': type(default).__name__}
    if isinstance(default, dict):
        known = {'name', 'driver', 'external', 'internal', 'attachable', 'enable_ipv4', 'enable_ipv6', 'ipam', 'driver_opts', 'labels'}
        projection.update(present_fields=sorted(known.intersection(default)), unknown_field_count=len(set(default)-known))
        for key in ('name', 'driver'):
            if key in default:
                projection[key] = token(default[key])
        for key in ('external', 'internal', 'attachable', 'enable_ipv4', 'enable_ipv6'):
            if key in default:
                projection[key] = default[key] if isinstance(default[key], bool) else 'redacted'
        for key in ('driver_opts', 'labels', 'ipam'):
            if key in default:
                projection[key+'_entry_count'] = len(default[key]) if isinstance(default[key], dict) else None
    services = config.get('services', {})
    service = services.get('trend-radar', {})
    memberships = service.get('networks') or {}
    return {'project': token(config.get('name')), 'project_matches': config.get('name') == project,
            'only_expected_service': set(services) == {'trend-radar'},
            'other_top_level_field_count': len(set(config)-{'name', 'services', 'networks'}),
            'network_count': len(networks), 'other_network_count': len(set(networks)-{'default'}),
            'default_network': projection,
            'matches_original_network_assumption': networks == {'default': {'name': project+'_default'}},
            'service_uses_default': 'default' in memberships,
            'service_other_network_count': len(set(memberships)-{'default'})}


def inspect(previous_directory, report):
    previous = json.loads((previous_directory/'report.json').read_text())
    state = json.loads((previous_directory/'state.json').read_text())
    if previous.get('status') != 'incomplete' or previous.get('rollback', {}).get('status') != 'restored' or state.get('phase') != 'restored':
        raise RuntimeError('Expected a failed and restored persistence operation')
    if state.get('container_change') or state.get('slice_attempted'):
        raise RuntimeError('This diagnosis applies only to the pre-recreation failure')
    target = persist.container()
    persist.idle(target)
    if target['id'] != state['old']['id'] or target['pid'] != state['old']['pid']:
        raise RuntimeError('Original production container changed after the failure')
    allowed = {str(persist.COMPOSE), str(persist.BASE), str(persist.PATCH)}
    if any(entry['path'] not in allowed for entry in state.get('files', [])):
        raise RuntimeError('Unexpected modified file in the failed attempt')
    persist.check_restore_files(previous_directory, state)
    restored = all((persist.recovery.sha(Path(entry['path'])) if Path(entry['path']).exists() else None) == entry['before']
                   for entry in state.get('files', []))
    if not restored or persist.recovery.sha(persist.COMPOSE) != persist.COMPOSE_SHA:
        raise RuntimeError('Original Compose configuration was not fully restored')
    report.update(stage='expand_config', previous_report_sha256=persist.recovery.sha(previous_directory/'report.json'),
                  recovery={'original_container_same_id_and_pid': True, 'managed_files_restored': restored,
                            'patch_directory_exists': persist.PATCH.parent.is_dir(),
                            'patch_directory_empty': persist.PATCH.parent.is_dir() and not any(persist.PATCH.parent.iterdir())},
                  container={'id': target['id'], 'pid': target['pid'], 'cgroup': target['cgroup']})
    before = json.loads(persist.audit.command(*persist.compose_args(project=persist.OLD_PROJECT), 'config', '--format', 'json'))
    report['before'] = config_shape(before, persist.OLD_PROJECT)
    # Read the existing base through extends, and send the candidate via stdin. No deployment
    # file is created or replaced; Compose config only expands and validates its model.
    candidate = persist.wrapper()
    candidate['services']['trend-radar']['extends']['file'] = str(persist.COMPOSE)
    after = json.loads(persist.audit.command(*persist.compose_args(path=Path('-')), 'config', '--format', 'json',
                                            input_text=json.dumps(candidate)))
    report['candidate_preview'] = config_shape(after, persist.PROJECT)
    report['stage'] = 'compare_config'
    try:
        persist.validate_config(before, after)
        report['candidate_validation'] = {'status': 'passed'}
    except RuntimeError as error:
        report['candidate_validation'] = {'status': 'rejected', 'details': persist.lazy.error_details(error)}
    current = persist.container()
    if current['id'] != target['id'] or current['pid'] != target['pid'] or persist.recovery.sha(persist.COMPOSE) != persist.COMPOSE_SHA:
        raise RuntimeError('Production identity or configuration changed during diagnosis')
    report.update(status='collected_for_review', stage='completed', health=persist.recovery.public_health(),
                  changes='None to deployment files, containers or runtime settings; only this diagnostic report was written.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--previous-run', required=True)
    args = parser.parse_args()
    if (not re.fullmatch(r'trendradar-persist-review-\d{8}T\d{6}Z-[a-f0-9]{8}', args.run_id)
            or not re.fullmatch(r'trendradar-persist-\d{8}T\d{6}Z-[a-f0-9]{8}', args.previous_run)
            or os.geteuid() != 0):
        raise RuntimeError('Root and valid evidence IDs are required')
    import fcntl
    os.umask(0o077)
    directory = persist.recovery.BACKUPS/args.run_id
    directory.mkdir(mode=0o700)
    report = {'id': args.run_id, 'previous_run': args.previous_run, 'status': 'collecting', 'stage': 'preflight',
              'created_at': persist.recovery.diagnostic.stamp(time.time())}
    try:
        with Path('/run/lock/finevent-memory-recovery.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            inspect(persist.recovery.BACKUPS/args.previous_run, report)
    except BaseException as error:
        report.update(status='incomplete', error_type=type(error).__name__, error_details=persist.lazy.error_details(error))
        raise
    finally:
        report['finished_at'] = persist.recovery.diagnostic.stamp(time.time())
        persist.recovery.save(directory/'report.json', report)
        print('Read-only diagnosis evidence: '+str(directory/'report.json'))


if __name__ == '__main__':
    main()
