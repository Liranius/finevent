"""Shared local/Actions SSH transport. Private output is saved locally, never printed."""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
controller = runpy.run_path(str(ROOT/'deploy/release_control.py'))


def ssh_options(target, identity, known_hosts, interactive=False):
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+', target):
        raise RuntimeError('Invalid SSH target')
    paths = [known_hosts] if interactive else [identity, known_hosts]
    for path in paths:
        if not path or not Path(path).is_file() or Path(path).is_symlink():
            raise RuntimeError('Missing regular SSH credential file; run bootstrap-deployment.ps1 first')
        if not interactive and os.name != 'nt' and Path(path).stat().st_mode & 0o077:
            raise RuntimeError('SSH credential files must have mode 0600')
    args = ['ssh', '-F', os.devnull, '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile='+str(Path(known_hosts).resolve()),
            '-o', 'GlobalKnownHostsFile='+os.devnull, '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3']
    if interactive:
        args += ['-o', 'PubkeyAuthentication=no', '-o', 'PreferredAuthentications=keyboard-interactive,password']
    else:
        args += ['-i', str(Path(identity).resolve()), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes']
    return args + [target]


def request_remote(ssh, request, archive=None):
    controller['parse_request']('finevent-release '+base64.b64encode(json.dumps(request).encode()).decode())
    command = ssh + ['finevent-release '+base64.b64encode(json.dumps(request).encode()).decode()]
    stream = Path(archive).open('rb') if archive else subprocess.DEVNULL
    try:
        result = subprocess.run(command, stdin=stream, capture_output=True, timeout=300, check=False)
    finally:
        if archive:
            stream.close()
    try:
        response = json.loads(result.stdout)
    except (ValueError, UnicodeDecodeError):
        if result.returncode:
            # SSH stderr includes private hostnames/IPs. Never print it in public logs.
            raise RuntimeError('SSH request failed; check authentication/host fingerprint or query the same operation before retrying') from None
        raise RuntimeError('Invalid controller response; query the same operation before retrying') from None
    if isinstance(response, dict) and response.get('error_type') == 'ControllerError':
        raise RuntimeError('Server: '+response['error'])
    if result.returncode or not isinstance(response, dict) or response.get('error'):
        raise RuntimeError('Controller rejected the request; inspect private server evidence')
    return response


def deploy(ssh, archive, run_id, profile, after_review, replace_observation, transport=request_remote,
           baseline_id=None, baseline_sha256=None, recover_preflight=None, failed_report_sha256=None, action='deploy'):
    files = runpy.run_path(str(ROOT/'scripts/check-release.py'))['check_archive'](archive)
    request = {'action': 'deploy', 'id': run_id, 'profile': profile, 'after_review': after_review,
               'replace_observation': replace_observation, 'content_sha256': controller['content_digest'](files),
               'archive_sha256': hashlib.sha256(Path(archive).read_bytes()).hexdigest()}
    request.update({key: value for key, value in zip(controller['EVIDENCE_OPTIONS'],
                    (baseline_id, baseline_sha256, recover_preflight, failed_report_sha256)) if value})
    if action == 'baseline':
        request = {key: value for key, value in request.items() if key not in ('profile', 'after_review', 'replace_observation')}
        request['action'] = action
    # Validate before any network access, including malformed IDs or contradictory options.
    controller['parse_request']('finevent-release '+base64.b64encode(json.dumps(request).encode()).decode())
    previous = transport(ssh, {'action': 'status', 'id': run_id})
    if previous.get('found'):
        if previous.get('identity') != controller['Controller'].identity(request):
            raise RuntimeError('Operation ID is already bound to different content or options')
        return previous
    return transport(ssh, request, archive)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('preflight', 'status', 'deploy', 'baseline', 'bootstrap'))
    parser.add_argument('--target', required=True)
    parser.add_argument('--identity', default=os.environ.get('FINEVENT_SSH_KEY_FILE', str(ROOT/'.validation/deploy-access/id_ed25519')))
    parser.add_argument('--known-hosts', default=os.environ.get('FINEVENT_KNOWN_HOSTS_FILE', str(ROOT/'.validation/deploy-access/known_hosts')))
    parser.add_argument('--archive')
    parser.add_argument('--id')
    parser.add_argument('--profile', choices=('calendar', 'news'), default='calendar')
    parser.add_argument('--after-review')
    parser.add_argument('--replace-observation')
    for key in controller['EVIDENCE_OPTIONS']:
        parser.add_argument('--'+key.replace('_', '-'))
    parser.add_argument('--private-report', action='store_true')
    parser.add_argument('--output')
    args = parser.parse_args()
    os.umask(0o077)
    ssh = ssh_options(args.target, args.identity, args.known_hosts, args.mode == 'bootstrap')
    if args.mode == 'bootstrap':
        # Static command; the archive contains only locally prepared bootstrap sources.
        command = 'umask 077; d=$(mktemp -d /tmp/finevent-bootstrap.XXXXXXXX) || exit 1; trap \'rm -rf -- "$d"\' EXIT; tar -xzf - -C "$d" && python3 -I "$d/bootstrap_release.py" "$d"'
        with Path(args.archive).open('rb') as stream:
            result = subprocess.run(ssh+[command], stdin=stream, check=False)
        if result.returncode:
            raise RuntimeError('One-time server initialization failed')
        return
    if args.mode in ('deploy', 'baseline'):
        if not args.archive:
            raise RuntimeError('Archive is required')
        if args.mode == 'baseline' and (args.after_review or args.replace_observation or any(getattr(args, key) for key in controller['EVIDENCE_OPTIONS'])):
            raise RuntimeError('Read-only baseline does not accept deployment options')
        prefix = 'persistence-check' if args.mode == 'baseline' else 'calendar-release'
        args.id = args.id or prefix+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        # Save the request cursor before SSH: a lost acknowledgement must not lose its identity.
        write_json(ROOT/('.validation/'+prefix+'-latest.json'), {'id': args.id, 'archive': Path(args.archive).name, 'verification_profile': args.profile})
        print('Operation: '+args.id, flush=True)
        result = deploy(ssh, args.archive, args.id, args.profile, args.after_review, args.replace_observation,
                        **{key: getattr(args, key) for key in controller['EVIDENCE_OPTIONS']}, action=args.mode)
    elif args.mode == 'status':
        if args.archive or args.after_review or args.replace_observation or any(getattr(args, key) for key in controller['EVIDENCE_OPTIONS']):
            raise RuntimeError('Status cannot be combined with deployment options')
        result = request_remote(ssh, {'action': 'status', 'id': args.id or 'latest', 'private': args.private_report})
    else:
        result = request_remote(ssh, {'action': 'preflight'})
    if args.private_report and result.get('found'):
        if 'report' not in result:
            result = request_remote(ssh, {'action': 'status', 'id': result['id'], 'private': True})
        private = result.pop('report', None)
        if private:
            write_json(ROOT/'.validation'/result['id']/'report.json', private)
            prefix = 'persistence-check' if controller['BASELINE_ID'].fullmatch(result['id']) else 'calendar-release'
            write_json(ROOT/('.validation/'+prefix+'-latest.json'), {'id': result['id'], 'verification_profile': result.get('verification_profile')})
    if args.output:
        write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if result.get('needs_recovery') or result.get('has_failure') or result.get('has_rollback') or result.get('status') in ('needs_recovery', 'incomplete', 'rolled_back'):
        sys.exit(2)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else 'Release client failed; no automatic retry', file=sys.stderr)
        sys.exit(1)
