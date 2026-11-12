"""Fixed SSH entrypoint for releases; installed independently of application bundles."""

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile

ID = re.compile(r'calendar-release-\d{8}T\d{6}Z-[a-f0-9]{8}')
BASELINE_ID = re.compile(r'persistence-check-\d{8}T\d{6}Z-[a-f0-9]{8}')
DIGEST = re.compile(r'[a-f0-9]{64}')
MAX_ARCHIVE = 128 * 1024**2
EVIDENCE_OPTIONS = ('baseline_id', 'baseline_sha256', 'recover_preflight', 'failed_report_sha256')


class ControllerError(RuntimeError):
    """Fixed public diagnostics, never subprocess output or private paths."""


def require(condition, message):
    if not condition:
        raise ControllerError(message)


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    # Acknowledged operation registration must survive a host crash, not just SSH loss.
    if os.name == 'posix':
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def content_digest(files):
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items() if name != 'sha256.json'}
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def parse_request(command):
    parts = shlex.split(command)
    require(len(parts) == 2 and parts[0] == 'finevent-release' and len(parts[1]) < 8192, 'Invalid command')
    request = json.loads(base64.b64decode(parts[1], validate=True))
    require(isinstance(request, dict), 'Invalid request')
    action = request.get('action')
    allowed = {'action', 'id', 'private'} if action == 'status' else {'action'}
    if action == 'deploy':
        allowed = {'action', 'id', 'profile', 'after_review', 'replace_observation', 'archive_sha256', 'content_sha256', *EVIDENCE_OPTIONS}
    if action == 'baseline':
        allowed = {'action', 'id', 'archive_sha256', 'content_sha256'}
    require(action in ('preflight', 'status', 'deploy', 'baseline') and not set(request) - allowed, 'Unsupported request')
    if action != 'preflight':
        pattern = BASELINE_ID if action == 'baseline' else ID
        require(isinstance(request.get('id'), str) and (pattern.fullmatch(request['id']) or action == 'status' and (request['id'] == 'latest' or BASELINE_ID.fullmatch(request['id']))), 'Invalid operation ID')
    if action == 'status':
        require(isinstance(request.get('private', False), bool), 'Invalid report option')
    if action in ('deploy', 'baseline'):
        for key in ('archive_sha256', 'content_sha256'):
            require(isinstance(request.get(key), str) and DIGEST.fullmatch(request[key]), 'Invalid release digest')
    if action == 'deploy':
        require(request.get('profile') in ('calendar', 'news'), 'Interactive AI initialization is a separate local operation')
        previous = [request.get('after_review'), request.get('replace_observation')]
        require(sum(bool(item) for item in previous) == 1 and all(not item or isinstance(item, str) and ID.fullmatch(item) for item in previous), 'Specify one predecessor')
        require(not request.get('replace_observation') or request['profile'] == 'calendar', 'Only calendar observations can be replaced')
        require(request['id'] not in previous, 'Operation cannot replace itself')
        for key, digest, pattern in (('baseline_id', 'baseline_sha256', BASELINE_ID), ('recover_preflight', 'failed_report_sha256', ID)):
            value, checksum = request.get(key), request.get(digest)
            require(bool(value) == bool(checksum), 'Evidence IDs and digests must be paired')
            if value:
                require(isinstance(value, str) and pattern.fullmatch(value) and isinstance(checksum, str) and DIGEST.fullmatch(checksum)
                        and request.get('after_review') and value != request['id'], 'Invalid reviewed evidence')
    return request


@contextmanager
def exclusive(path):
    import fcntl
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ControllerError('Another release request is being registered') from None
        yield


class Controller:
    def __init__(self, config, state=Path('/var/lib/finevent-release'), checker=None):
        self.config, self.state = config, state
        self.backups = Path(config['backupRoot'])
        self.checker = checker or runpy.run_path(str(Path(__file__).with_name('check-release.py')))['check_archive']

    def operation_path(self, run_id):
        return self.state/('baselines' if BASELINE_ID.fullmatch(run_id) else 'operations')/run_id

    def latest(self):
        # Include legacy interactive operations: a stale local pointer cannot bypass them.
        ids = {p.name for p in self.backups.glob('calendar-release-*') if ID.fullmatch(p.name)}
        ids.update(p.name for p in (self.state/'operations').glob('calendar-release-*') if ID.fullmatch(p.name))
        if not ids:
            return None
        def created(run_id):
            operation = self.state/'operations'/run_id/'operation.json'
            report = self.backups/run_id/'report.json'
            if operation.exists():
                return read(operation)['registered_at']
            require(report.is_file(), 'Unresolved legacy operation; inspect server evidence')
            # Existing reports use an ISO timestamp with a timezone offset.
            return datetime.fromisoformat(read(report)['created_at'].replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
        return max(ids, key=lambda run_id: (created(run_id), run_id))

    def system_status(self, run_id):
        result = subprocess.run(['/usr/bin/systemctl', 'show', 'finevent-'+run_id+'.service', '-p', 'LoadState', '-p', 'ActiveState', '-p', 'ExecMainStatus'], capture_output=True, text=True, timeout=15, check=False)
        require(result.returncode == 0, 'Cannot query observer status')
        return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)

    def status(self, run_id, private=False):
        if run_id == 'latest':
            run_id = self.latest()
        if not run_id:
            return {'found': False}
        operation_path = self.operation_path(run_id)/'operation.json'
        report_path = self.backups/run_id/'report.json'
        operation = read(operation_path) if operation_path.is_file() else None
        if not report_path.is_file():
            return {'found': bool(operation), 'id': run_id, 'status': 'needs_recovery' if operation else 'not_found', 'identity': operation['identity'] if operation else None}
        report = read(report_path)
        require(report.get('id') == run_id, 'Report identity mismatch')
        observer = self.system_status(run_id)
        pending = report.get('status') in ('queued', 'deploying', 'observing')
        interrupted = pending and observer.get('ActiveState') not in ('active', 'activating')
        result = {'found': True, 'id': run_id, 'status': report.get('status'), 'stage': report.get('stage'),
                  'verification_profile': report.get('verification_profile', 'calendar'),
                  'planned_end': report.get('planned_end'), 'finished_at': report.get('finished_at'),
                  'needs_recovery': interrupted, 'has_failure': bool(report.get('failure')),
                  'has_rollback': bool(report.get('rollback')), 'user_acceptance': 'pending',
                  'report_sha256': hashlib.sha256(report_path.read_bytes()).hexdigest(),
                  'identity': operation['identity'] if operation else None,
                  'source_commit': operation.get('source_commit') if operation else None,
                  'generic_sha256': operation.get('generic_sha256') if operation else None,
                  'checks': {key: report.get(key, {}).get('status') for key in ('restart', 'backup_verification')}}
        if BASELINE_ID.fullmatch(run_id):
            result['baseline_checks'] = {'history_scope': report.get('history_scope'), 'trendradar': report.get('trendradar', {}).get('status'),
                                         'distinct_cycles': report.get('trendradar', {}).get('distinct_cycles'),
                                         'resources': {key: report.get('resources', {}).get(key) for key in
                                                       ('samples', 'host_available_min_mib', 'max_sample_interval_seconds', 'host_oom_kill_delta', 'trend_oom_kill_delta', 'finevent_oom_kill_delta')}}
        if private:
            report['observer_status'] = observer
            if interrupted:
                report['observer_status']['warning'] = 'Worker stopped; review evidence before retrying.'
            result['report'] = report
        return result

    def preflight(self):
        require((Path(self.config['appRoot'])/'current').is_dir(), 'Initialize the application separately before using Actions')
        require((Path(self.config['dataRoot'])/'config.json').is_file(), 'Application configuration is missing')
        require(self.backups.is_dir(), 'Backup directory is missing')
        return {'protocol': 2, 'ready': True, 'latest': self.status('latest')}

    @staticmethod
    def identity(request):
        identity = {key: request.get(key) for key in ('content_sha256', 'profile', 'after_review', 'replace_observation')}
        identity.update({key: request[key] for key in EVIDENCE_OPTIONS if request.get(key)})
        return identity

    def launch(self, bundle, request, log):
        runner = 'persistence_baseline.py' if request['action'] == 'baseline' else 'calendar_release.py'
        args = ['/usr/bin/python3', '-B', str(bundle/'deploy'/runner), '--run-id', request['id'], '--launch']
        if request.get('profile') == 'news':
            args.append('--news')
        for key in ('after_review', 'replace_observation', *EVIDENCE_OPTIONS):
            if request.get(key):
                args.extend(['--'+key.replace('_', '-'), request[key]])
        # Full installer output remains private on the server, never in Actions logs.
        with log.open('ab') as output:
            result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=output, stderr=output, timeout=120, check=False)
        require(result.returncode == 0, 'Launch needs review; query this operation, do not create another')

    def check_predecessor(self, bundle, request):
        key = 'after_review' if request.get('after_review') else 'replace_observation'
        # Call the existing gate without launching, also supporting legacy deployment bundles.
        script = ('import sys, json; sys.path.insert(0, sys.argv[1]); import calendar_release as c\n'
                  'if sys.argv[2] == "after_review": c.review_previous(sys.argv[3])\n'
                  'else:\n'
                  ' p = json.loads((c.BACKUPS/sys.argv[3]/"report.json").read_text())\n'
                  ' c.validate_interruptible(p, sys.argv[3], str((c.ROOT/"current").resolve()))\n'
                  ' c.validate_live_predecessor(p)\n'
                  'r = json.loads(sys.argv[4])\n'
                  'if r.get("recover_preflight"): c.review_failed_preflight(r["recover_preflight"], r["failed_report_sha256"], r["after_review"])\n'
                  'if r.get("baseline_id"):\n'
                  ' import persistence_baseline as b\n'
                  ' p = b.load(r["baseline_id"], r["baseline_sha256"])\n'
                  ' b.validate_current(p, c.inspect_persistence(b.original()[0]))\n')
        result = subprocess.run(['/usr/bin/python3', '-B', '-c', script, str(bundle/'deploy'), key, request[key], json.dumps(request)],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=120, check=False)
        require(result.returncode == 0, 'Predecessor verification failed; no operation registered')

    def deploy(self, request, stream):
        self.preflight()
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        baseline = request['action'] == 'baseline'
        operations = self.state/('baselines' if baseline else 'operations')
        operations.mkdir(mode=0o700, exist_ok=True)
        with exclusive(self.state/'register.lock'):
            destination = operations/request['id']
            record = destination/'operation.json'
            if record.is_file():
                require(read(record)['identity'] == self.identity(request), 'Operation ID is already bound to different content or options')
                return self.status(request['id'])
            require(not destination.exists() and not (self.backups/request['id']).exists(), 'Operation ID is already in use')
            if not baseline:
                require(self.latest() == (request.get('recover_preflight') or request.get('after_review') or request.get('replace_observation')), 'Predecessor is not the latest server operation')
                if request.get('recover_preflight'):
                    failed = self.state/'operations'/request['recover_preflight']/'operation.json'
                    require(failed.is_file() and read(failed)['identity'].get('after_review') == request['after_review'], 'Failed operation is not a registered successor of the reviewed release')
            with tempfile.TemporaryDirectory(prefix='receive-', dir=self.state) as temporary:
                temporary = Path(temporary)
                archive = temporary/'release.tar.gz'
                digest, size = hashlib.sha256(), 0
                with archive.open('wb') as output:
                    while chunk := stream.read(1024 * 1024):
                        size += len(chunk)
                        require(size <= MAX_ARCHIVE, 'Archive is too large')
                        output.write(chunk)
                        digest.update(chunk)
                require(digest.hexdigest() == request['archive_sha256'], 'Archive transfer digest mismatch')
                files = self.checker(archive)
                require(content_digest(files) == request['content_sha256'], 'Release content identity mismatch')
                require('deploy/calendar_release.py' in files, 'Missing release runner')
                if baseline:
                    require('deploy/persistence_baseline.py' in files, 'Missing read-only baseline runner')
                if request.get('profile') == 'news':
                    require('deploy/news_release.py' in files, 'Missing news verification runner')
                metadata = json.loads(files.get('release.json', b'{}'))
                bundle = temporary/'bundle'
                for name, data in files.items():
                    target = bundle/name
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    target.write_bytes(data)
                if not baseline:
                    self.check_predecessor(bundle, request)
                # Register the immutable bundle and identity before any launch side effect.
                destination.mkdir(mode=0o700)
                shutil.move(str(bundle), str(destination/'bundle'))
                save(record, {'id': request['id'], 'identity': self.identity(request),
                              'registered_at': datetime.now(timezone.utc).isoformat(),
                              'archive_sha256': request['archive_sha256'],
                              'source_commit': metadata.get('source_commit'), 'generic_sha256': metadata.get('generic_sha256')})
            # Never launch again for an existing record, including ambiguous SSH/systemd failures.
            self.launch(destination/'bundle', request, destination/'launch.log')
            return self.status(request['id'])


def main():
    require(os.geteuid() == 0, 'Root entrypoint required')
    os.umask(0o077)
    request = parse_request(os.environ.get('SSH_ORIGINAL_COMMAND', ''))
    controller = Controller(read(Path(__file__).with_name('config.json')))
    if request['action'] in ('deploy', 'baseline'):
        result = controller.deploy(request, sys.stdin.buffer)
    elif request['action'] == 'status':
        result = controller.status(request['id'], request.get('private', False))
    else:
        result = controller.preflight()
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # OS/subprocess exceptions may contain private host paths. Keep them off stdout.
        print(json.dumps({'error': str(error) if isinstance(error, ControllerError) else 'Controller failed; inspect private server evidence', 'error_type': type(error).__name__}))
        sys.exit(1)
