"""Select a successful main CI artifact and run the shared release client."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def api(repository, path):
    result = subprocess.run(['gh', 'api', 'repos/'+repository+'/'+path], capture_output=True, check=False)
    require(result.returncode == 0, 'GitHub API request failed')
    return json.loads(result.stdout)


def select_artifact(repository, run, workflow, artifacts):
    require(run.get('event') == 'push' and run.get('head_branch') == 'main' and run.get('status') == 'completed'
            and run.get('conclusion') == 'success' and run.get('repository', {}).get('full_name') == repository
            and run.get('head_repository', {}).get('full_name') == repository
            and run.get('workflow_id') == workflow.get('id') and workflow.get('path') == '.github/workflows/ci.yml',
            'Only successful main push CI runs from this repository can be deployed')
    sha = run.get('head_sha', '')
    require(re.fullmatch(r'[a-f0-9]{40}', sha), 'Invalid CI commit')
    matches = [artifact for artifact in artifacts if artifact.get('name') == 'finevent-linux-amd64-'+sha]
    require(len(matches) == 1, 'Expected exactly one matching CI artifact')
    artifact = matches[0]
    provenance = artifact.get('workflow_run', {})
    require(not artifact.get('expired') and isinstance(artifact.get('digest'), str) and re.fullmatch(r'sha256:[a-f0-9]{64}', artifact['digest'])
            and provenance.get('id') == run['id'] and provenance.get('head_sha') == sha
            and provenance.get('head_branch') == 'main'
            and provenance.get('repository_id') == run['repository']['id']
            and provenance.get('head_repository_id') == run['repository']['id'], 'Artifact expired or provenance/digest mismatch')
    return artifact


def unpack_artifact(zip_path, digest, sha, directory):
    require(hashlib.sha256(zip_path.read_bytes()).hexdigest() == digest.removeprefix('sha256:'), 'GitHub artifact digest mismatch')
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        releases = [name for name in names if re.fullmatch(r'finevent-\d{8}T\d{6}Z\.tar\.gz', name)]
        require(len(releases) == 1 and sorted(names) == sorted([releases[0], releases[0]+'.sha256']), 'Unexpected artifact files')
        require(sum(item.file_size for item in archive.infolist()) <= 128 * 1024**2, 'Artifact too large')
        filename = releases[0]
        data, sidecar = archive.read(filename), archive.read(filename+'.sha256').decode().strip()
        require(sidecar == hashlib.sha256(data).hexdigest()+'  '+filename, 'Generic archive sidecar mismatch')
        target = directory/filename
        require(not target.exists(), 'Preserve the existing archive; choose a fresh download directory')
        target.write_bytes(data)
        (directory/(filename+'.sha256')).write_text(sidecar+'\n', encoding='utf-8')
    files = runpy.run_path(str(ROOT/'scripts/check-release.py'))['check_archive'](target, 'generic')
    metadata = json.loads(files['release.json'])
    require(metadata['source_commit'] == sha and metadata['source_dirty'] is False, 'Archive was not built from the selected clean CI commit')
    return target


def operation_id(repository, run):
    stamp = datetime.fromisoformat(run['created_at'].replace('Z', '+00:00')).strftime('%Y%m%dT%H%M%SZ')
    # run_attempt is deliberately excluded: Re-run jobs must resume the same operation.
    suffix = hashlib.sha256((repository+'/'+str(run['id'])).encode()).hexdigest()[:8]
    return 'calendar-release-'+stamp+'-'+suffix


def credentials(directory):
    values = {name: os.environ.get(name, '') for name in ('FINEVENT_DEPLOY_CONFIG', 'FINEVENT_SSH_KEY', 'FINEVENT_KNOWN_HOSTS')}
    require(all(values.values()), 'Production environment secrets are missing')
    config = json.loads(values['FINEVENT_DEPLOY_CONFIG'])
    # Mask individual values as well as the multiline JSON; never print private errors.
    for value in config.values():
        if isinstance(value, str):
            print('::add-mask::'+value.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A'))
    for name, value in (('config.json', values['FINEVENT_DEPLOY_CONFIG']), ('identity', values['FINEVENT_SSH_KEY']), ('known_hosts', values['FINEVENT_KNOWN_HOSTS'])):
        path = directory/name
        path.write_text(value.rstrip()+'\n', encoding='utf-8')
        path.chmod(0o600)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('deploy', 'report', 'download'))
    parser.add_argument('--ci-run')
    parser.add_argument('--id')
    parser.add_argument('--profile', choices=('calendar', 'news'), default='calendar')
    parser.add_argument('--after-review')
    for key in ('baseline-id', 'baseline-sha256', 'recover-preflight', 'failed-report-sha256'):
        parser.add_argument('--'+key)
    parser.add_argument('--directory')
    args = parser.parse_args()
    repository = os.environ.get('GITHUB_REPOSITORY', 'Liranius/finevent')
    require(re.fullmatch(r'[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+', repository), 'Invalid repository')
    if args.mode != 'download':
        require(os.environ.get('GITHUB_REF') == 'refs/heads/main' and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', 'Deploy/Report require manual dispatch on main')
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='finevent-actions-') as temporary:
        temporary = Path(temporary)
        if args.mode in ('deploy', 'download'):
            require(args.ci_run and re.fullmatch(r'[0-9]+', args.ci_run), 'Specify a CI run ID')
            run = api(repository, 'actions/runs/'+args.ci_run)
            workflow = api(repository, 'actions/workflows/ci.yml')
            artifact = select_artifact(repository, run, workflow, api(repository, 'actions/runs/'+args.ci_run+'/artifacts')['artifacts'])
            comparison = api(repository, 'compare/'+run['head_sha']+'...main')
            require(comparison.get('status') in ('ahead', 'identical'), 'CI commit is no longer on main')
            zip_path = temporary/'artifact.zip'
            with zip_path.open('wb') as output:
                result = subprocess.run(['gh', 'api', 'repos/'+repository+'/actions/artifacts/'+str(artifact['id'])+'/zip'], stdout=output, stderr=subprocess.PIPE, check=False)
            require(result.returncode == 0, 'Artifact download failed')
            output = Path(args.directory).resolve() if args.mode == 'download' and args.directory else temporary
            output.mkdir(parents=True, exist_ok=True)
            archive = unpack_artifact(zip_path, artifact['digest'], run['head_sha'], output)
            print('Verified CI run '+str(run['id'])+', commit '+run['head_sha']+', artifact '+str(artifact['id']))
            if args.mode == 'download':
                require(args.directory, 'Download requires --directory')
                return
        config = credentials(temporary)
        client = runpy.run_path(str(ROOT/'scripts/release-client.py'))
        ssh = client['ssh_options'](config['sshTarget'], temporary/'identity', temporary/'known_hosts')
        if args.mode == 'deploy':
            dispatch = api(repository, 'actions/runs/'+os.environ['GITHUB_RUN_ID'])
            run_id = operation_id(repository, dispatch)
            print('Operation: '+run_id, flush=True)
            with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as summary:
                summary.write('Operation: `'+run_id+'`\n\nCI: `'+args.ci_run+'`; source: `'+run['head_sha']+'`.\n\n')
            # Rendering invokes only checked-in tooling; it never executes artifact scripts.
            result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File', str(ROOT/'scripts/render-release.ps1'),
                                     '-Archive', str(archive), '-OutputDirectory', str(temporary/'rendered'), '-ConfigPath', str(temporary/'config.json')], capture_output=True, check=False)
            require(result.returncode == 0, 'Private deployment rendering failed')
            response = client['deploy'](ssh, temporary/'rendered'/archive.name, run_id, args.profile, args.after_review, None,
                                       baseline_id=args.baseline_id, baseline_sha256=args.baseline_sha256,
                                       recover_preflight=args.recover_preflight, failed_report_sha256=args.failed_report_sha256)
        else:
            require(args.id, 'Report requires an operation ID or latest')
            response = client['request_remote'](ssh, {'action': 'status', 'id': args.id})
        text = json.dumps(response, indent=2, ensure_ascii=True)
        print(text)
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as summary:
            summary.write('```json\n'+text+'\n```\n\nA successful launch/query does not certify the observation or user acceptance.\n')
        require(response.get('found') and not response.get('needs_recovery') and not response.get('has_failure') and not response.get('has_rollback')
                and response.get('status') not in ('needs_recovery', 'incomplete', 'rolled_back'), 'Operation needs review; do not create another deployment to retry')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else 'Actions release failed; inspect retained operation evidence', file=sys.stderr)
        sys.exit(1)
