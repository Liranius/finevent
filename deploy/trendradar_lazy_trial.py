"""Backed-up, temporary LiteLLM lazy-import trial in the existing TrendRadar container."""

import argparse
import ast
from contextlib import closing
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
import traceback
from zoneinfo import ZoneInfo

import memory_recovery as recovery
import memory_high_trial as pressure
import trendradar_import_probe as probe

CLIENT = 'app/trendradar/ai/client.py'
ORIGINAL = 'a50a88022f31511201a560de47dd736b5adde7c7f8f023226d4eb3f476293b01'
EXPECTED_OUTPUT = Path('/srv/example/trendradar/output')
FILES = ('trendradar_lazy_trial.py', 'trendradar_import_probe.py', 'memory_recovery.py',
         'memory_diagnostic.py', 'memory_high_trial.py')
ZONE = ZoneInfo('Asia/Shanghai')


def candidate(source):
    expected = probe.client_tree(source, ORIGINAL, True)
    tree = ast.parse(source)
    statement = next(node for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == 'litellm')
    client = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'AIClient')
    method = next(node for node in client.body if isinstance(node, ast.FunctionDef) and node.name == 'chat')
    # Preserve comments/formatting. Verify the result is exactly the AST tested offline.
    position = method.body[0].end_lineno if ast.get_docstring(method) else method.body[0].lineno-1
    lines = source.decode('utf-8').splitlines(keepends=True)
    lines.insert(position, '        from litellm import completion\n')
    del lines[statement.lineno-1:statement.end_lineno]
    result = ''.join(lines).encode('utf-8')
    if ast.dump(ast.parse(result), include_attributes=False) != ast.dump(expected, include_attributes=False):
        raise RuntimeError('The written patch differs from the tested candidate')
    return result


def source_path(target):
    root = Path('/proc')/str(target['pid'])/'root'
    # /proc/PID/root itself is a kernel namespace link; children must be ordinary image paths.
    for name in ('app', 'app/trendradar', 'app/trendradar/ai', CLIENT):
        if (root/name).is_symlink():
            raise RuntimeError('Unexpected symlink in the production source path')
    path = root/CLIENT
    if not stat.S_ISREG(path.stat().st_mode):
        raise RuntimeError('Production client is not a regular file')
    return path


def atomic_replace(path, content, expected):
    if recovery.sha(path) != expected:
        raise RuntimeError('Source was independently changed; refusing to overwrite')
    metadata = path.stat()
    temporary = path.with_name('.finevent-client-'+str(os.getpid())+'.tmp')
    created = False
    try:
        with temporary.open('xb') as output:
            created = True
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, stat.S_IMODE(metadata.st_mode))
        os.chown(temporary, metadata.st_uid, metadata.st_gid)
        if recovery.sha(path) != expected:
            raise RuntimeError('Source changed while preparing the replacement')
        os.replace(temporary, path)
        if recovery.sha(path) != hashlib.sha256(content).hexdigest():
            raise RuntimeError('Source replacement verification failed')
    finally:
        if created and temporary.exists():
            temporary.unlink()


def read_database(path):
    if re.match(r'^/proc/(?:[0-9]+|self)/root(?:/|$)', path.as_posix()):
        raise RuntimeError('SQLite must use the verified host bind mount, not a proc namespace path')
    connection = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA cache_size=-512')
    except BaseException:
        connection.close()
        raise
    return connection


def output_directory(target):
    mounts = json.loads(recovery.command('docker', 'inspect', '--format', '{{json .Mounts}}', target['id']).stdout)
    matches = [item for item in mounts if item.get('Destination') == '/app/output']
    if len(matches) != 1 or matches[0].get('Type') != 'bind' or matches[0].get('Source') != str(EXPECTED_OUTPUT):
        raise RuntimeError('TrendRadar output mount differs from the audited host directory')
    namespace = Path('/proc')/str(target['pid'])/'root/app/output'
    if not EXPECTED_OUTPUT.is_dir() or not namespace.samefile(EXPECTED_OUTPUT):
        raise RuntimeError('Host and container output directories are not the same files')
    return EXPECTED_OUTPUT


def error_details(error):
    # No exception message, local variables, SQL values, or raw journal output.
    result = {'frames': [{'file': Path(frame.filename).name, 'line': frame.lineno,
                          'function': frame.name} for frame in traceback.extract_tb(error.__traceback__)[-6:]]}
    if isinstance(error, sqlite3.Error):
        code, name = getattr(error, 'sqlite_errorcode', None), getattr(error, 'sqlite_errorname', None)
        if isinstance(code, int):
            result['sqlite_code'] = code
        if isinstance(name, str) and re.fullmatch(r'SQLITE_[A-Z_]+', name):
            result['sqlite_name'] = name
    return result


def previous_database_diagnostic(previous_id, target, output):
    previous = json.loads((recovery.BACKUPS/previous_id/'report.json').read_text())
    if previous.get('error_type') != 'OperationalError' or previous.get('rollback', {}).get('status') != 'no_changes':
        return {'status': 'not_applicable', 'previous_id': previous_id}
    state = json.loads((recovery.BACKUPS/previous_id/'state.json').read_text())
    if state.get('change_attempted'):
        raise RuntimeError('Previous database failure may have modified source; review before another trial')
    date = datetime.now(ZONE).strftime('%Y-%m-%d')+'.db'
    host = output/'news'/date
    namespace = Path('/proc')/str(target['pid'])/'root/app/output/news'/date
    if not namespace.samefile(host):
        raise RuntimeError('Host and namespace database files do not match')
    # Reproduce only the old read-only URI construction, before any source mutation.
    legacy = namespace.resolve()
    result = {'previous_id': previous_id, 'status': 'not_reproduced',
              'host_database': str(host), 'legacy_resolved_path': str(legacy),
              'same_database_before_resolve': True, 'legacy_path_exists': legacy.is_file()}
    with closing(read_database(host)) as connection:
        result['host_news_rows'] = connection.execute('SELECT COUNT(*) FROM news_items').fetchone()[0]
    try:
        with closing(sqlite3.connect(legacy.as_uri()+'?mode=ro', uri=True, timeout=2)) as connection:
            connection.execute('SELECT COUNT(*) FROM news_items').fetchone()
    except sqlite3.Error as error:
        result['legacy_error'] = error_details(error)
        code = getattr(error, 'sqlite_errorcode', None)
        if isinstance(code, int) and code & 0xff == sqlite3.SQLITE_CANTOPEN:
            result['status'] = 'legacy_path_failure_reproduced'
    return result


def backup_outputs(directory, output_root):
    date = datetime.now(ZONE).strftime('%Y-%m-%d')
    source = output_root/'news'/(date+'.db')
    if not source.is_file():
        raise RuntimeError('Current news database is missing before the trial')
    started = time.monotonic()
    def bounded(_status, _remaining, _total):
        if time.monotonic()-started > 20:
            raise RuntimeError('News backup exceeded its bounded time')
    destination = directory/'news.before.db'
    with closing(read_database(source)) as input_db, closing(sqlite3.connect(destination)) as output_db:
        input_db.backup(output_db, pages=128, progress=bounded)
        if output_db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise RuntimeError('News backup integrity check failed')
    html = output_root/'index.html'
    if not html.is_file() or html.stat().st_size > 8*recovery.MIB:
        raise RuntimeError('Expected current HTML report is missing or unexpectedly large')
    shutil.copyfile(html, directory/'index.before.html')
    return {'news_backup_sha256': recovery.sha(destination), 'html_backup_sha256': recovery.sha(directory/'index.before.html')}


def verify_workflow(report, target, image):
    if report.get('status') != 'completed_for_review' or report.get('kind') != 'workflow':
        raise RuntimeError('A completed server workflow comparison is required')
    probe.compare(report['runs'], workflow=True)
    if report.get('client_sha256') != ORIGINAL or report.get('image_id') != image:
        raise RuntimeError('The production source/image differs from the verified candidate')
    for name in ('production_before', 'production_after'):
        if report[name]['id'] != target['id']:
            raise RuntimeError('The workflow comparison used a different production container')
    if not all(value == {'exit': 0, 'http_status': '200'} for value in report['health'].values()) or len(report['health']) != 2:
        raise RuntimeError('Workflow health evidence is incomplete')


def restore(directory, state):
    if not state.get('change_attempted'):
        return {'status': 'no_changes'}
    target = recovery.inspect_target()
    if target['id'] != state['container_id'] or target['pid'] != state['container_pid']:
        raise RuntimeError('Container identity changed; manual rollback review required')
    source = source_path(target)
    current = recovery.sha(source)
    original = (directory/'client.before.py').read_bytes()
    if hashlib.sha256(original).hexdigest() != ORIGINAL:
        raise RuntimeError('Original source backup was modified')
    if current == state['candidate_sha256']:
        atomic_replace(source, original, state['candidate_sha256'])
    elif current != ORIGINAL:
        raise RuntimeError('Source changed independently; automatic rollback refused')
    state['phase'] = 'restored'
    recovery.save(directory/'state.json', state)
    return {'status': 'restored', 'source_sha256': ORIGINAL,
            'data': 'Collection data and HTML are retained; no database restoration was performed.'}


def verify_outputs(output_root, finevent_db, start, end):
    dates = {datetime.fromtimestamp(value, ZONE).strftime('%Y-%m-%d') for value in (start, end)}
    records = []
    sources = []
    for date in sorted(dates):
        with closing(read_database(output_root/'news'/(date+'.db'))) as connection:
            if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise RuntimeError('Production news database integrity check failed')
            for identity, crawl_time, total in connection.execute('SELECT id,crawl_time,total_items FROM crawl_records ORDER BY id'):
                at = int(datetime.strptime(date+' '+crawl_time, '%Y-%m-%d %H-%M').replace(tzinfo=ZONE).timestamp())
                if start <= at <= end:
                    records.append({'at': at, 'items': total})
                    sources.extend({'at': at, 'source': source, 'status': status} for source, status in connection.execute(
                        'SELECT platform_id,status FROM crawl_source_status WHERE crawl_record_id=? ORDER BY platform_id', (identity,)))
    if len({row['at']//1800 for row in records if row['items'] > 0}) < 2:
        raise RuntimeError('Two production collection cycles are not present in SQLite')
    if any(sum(row['at'] == record['at'] for row in sources) != 9 for record in records):
        raise RuntimeError('Expected nine source statuses per production collection')
    latest_crawl = max(row['at'] for row in records)
    html = output_root/'index.html'
    if not html.is_file() or html.stat().st_mtime < latest_crawl or html.stat().st_size == 0:
        raise RuntimeError('The current HTML report was not refreshed after collection')
    with closing(read_database(finevent_db)) as connection:
        polls = connection.execute("SELECT COUNT(*),SUM(CASE WHEN success=0 THEN 1 ELSE 0 END),MAX(CASE WHEN success=1 THEN completed END) FROM polls WHERE source='trendradar' AND started>=?", (start,)).fetchone()
        observed = connection.execute("SELECT MAX(source_observed_at) FROM news WHERE source LIKE 'trendradar:%'").fetchone()[0]
    if not polls[0] or polls[1] or not polls[2] or polls[2] < latest_crawl or not observed or observed < latest_crawl:
        raise RuntimeError('Finevent did not successfully import the new production collections')
    return {'records': records, 'source_statuses': sources,
            'source_failures': sum(row['status'] != 'success' for row in sources),
            'html_mtime': html.stat().st_mtime, 'html_sha256': recovery.sha(html),
            'finevent_poll_count': polls[0], 'finevent_poll_failures': polls[1],
            'finevent_last_success': polls[2], 'finevent_latest_source_observed': observed}


def apply_and_observe(directory, state, report, workflow):
    report['stage'] = 'preflight'
    target = recovery.inspect_target()
    recovery.validate_limits(target, recovery.MEMORY, recovery.TOTAL_MEMORY_SWAP)
    image = recovery.command('docker', 'inspect', '--format', '{{.Image}}', target['id']).stdout.strip()
    verify_workflow(workflow, target, image)
    probe.check_headroom(recovery, target, initial=True)
    if pressure.read_high() != str(pressure.HIGH) or pressure.read_swappiness() != pressure.SWAPPINESS:
        raise RuntimeError('The verified host/container memory policies changed')
    source = source_path(target)
    original = source.read_bytes()
    changed = candidate(original)
    report['stage'] = 'resolve_output_mount'
    output = output_directory(target)
    report['output_directory'] = str(output)
    if report.get('previous_run'):
        report['stage'] = 'diagnose_previous_database_failure'
        report['previous_database_diagnostic'] = previous_database_diagnostic(report['previous_run'], target, output)
        recovery.save(directory/'report.json', report)
        if report['previous_database_diagnostic']['status'] == 'not_reproduced':
            raise RuntimeError('Previous database failure was not explained by the old path; review diagnostic before applying')
    state.update(container_id=target['id'], container_pid=target['pid'],
                 candidate_sha256=hashlib.sha256(changed).hexdigest(), phase='preparing_backups')
    (directory/'client.before.py').write_bytes(original)
    (directory/'client.candidate.py').write_bytes(changed)
    report['stage'] = 'backup_outputs'
    report['backups'] = backup_outputs(directory, output)
    state['phase'] = 'backed_up'
    report.update(production_before=target, image_id=image, candidate_sha256=state['candidate_sha256'])
    recovery.save(directory/'state.json', state)
    recovery.save(directory/'report.json', report)
    # Recheck after backups; never modify the source during the next scheduled import.
    probe.check_headroom(recovery, target, initial=True)
    state['change_attempted'] = True
    recovery.save(directory/'state.json', state)
    report['stage'] = 'replace_source'
    atomic_replace(source, changed, ORIGINAL)
    state['phase'] = 'applied'
    recovery.save(directory/'state.json', state)
    report.update(status='observing', stage='observe', applied_at=recovery.diagnostic.stamp(time.time()))
    recovery.save(directory/'report.json', report)
    def undo():
        if state.get('phase') != 'restored':
            report['rollback'] = restore(directory, state)
    recovery.observe(directory, report, sample_guard=pressure.pressure_guard(report, monitor_host=True), on_failure=undo)
    if report['status'] == 'completed_for_review':
        report['stage'] = 'verify_outputs'
        start = int(datetime.fromisoformat(report['observation_started']).timestamp())
        report['outputs'] = verify_outputs(output, Path('/srv/example/finevent-data/finevent.db'), start, int(time.time()))
        if recovery.sha(source) != state['candidate_sha256']:
            raise RuntimeError('Candidate source changed during observation')
        # Historical writable-layer trial. Production adopted a read-only host bind through
        # trendradar_persist.py on 2026-10-30; retain this report wording for the old trial workflow.
        report['configuration'] = 'Candidate retained in current container only; deployment persistence pending review.'
        report['stage'] = 'completed'
    else:
        undo()
        report['configuration'] = 'Original client restored; existing RAM/swap policies retained.'


def launch(directory, run_id, workflow_id, previous_run=None):
    directory.mkdir(mode=0o700)
    for name in FILES:
        shutil.copy2(Path(__file__).with_name(name), directory/name)
    report = {'id': run_id, 'workflow_id': workflow_id, 'status': 'queued',
              'created_at': recovery.diagnostic.stamp(time.time()), 'unit': 'finevent-'+run_id+'.service',
              'rollback_command': 'python3 '+str(directory/'trendradar_lazy_trial.py')+' --run-id '+run_id+' --rollback'}
    if previous_run:
        report['previous_run'] = previous_run
    recovery.save(directory/'report.json', report)
    try:
        recovery.command('systemd-run', '--unit='+report['unit'], '--property=Type=exec',
                         '--property=RuntimeMaxSec=4500', '--property=TimeoutStopSec=45',
                         '--property=MemoryMax=96M', '--property=MemorySwapMax=0', '--property=UMask=0077',
                         '/usr/bin/python3', '-B', str(directory/'trendradar_lazy_trial.py'), '--run-id', run_id)
    except BaseException:
        report['status'] = 'launch_uncertain'
        recovery.save(directory/'launch-error.json', report)
        raise
    print('Background trial started; SSH disconnection will not stop it. Evidence: '+str(directory/'report.json'))


def run_trial(directory, state, report):
    try:
        workflow = json.loads((recovery.BACKUPS/report['workflow_id']/'report.json').read_text())
        apply_and_observe(directory, state, report, workflow)
    except BaseException as error:
        report.update(status='incomplete', error_type=type(error).__name__,
                      error_details=error_details(error),
                      error=str(error) if isinstance(error, RuntimeError) else 'Trial interrupted; review saved evidence.')
        try:
            report['rollback'] = restore(directory, state)
        except BaseException as restore_error:
            report['rollback'] = {'status': 'needs_review', 'error_type': type(restore_error).__name__}
        raise
    finally:
        recovery.save(directory/'state.json', state)


def export_status(directory, run_id):
    report = json.loads((directory/'report.json').read_text())
    fields = ('LoadState', 'ActiveState', 'SubState', 'Result', 'ExecMainStatus', 'MainPID')
    result = recovery.command('systemctl', 'show', 'finevent-'+run_id+'.service',
                              '--property='+','.join(fields), check=False)
    status = dict(line.split('=', 1) for line in result.stdout.splitlines() if line.partition('=')[0] in fields)
    report['observer_status'] = {'checked_at': recovery.diagnostic.stamp(time.time()),
                                 'systemctl_exit': result.returncode, 'service': status}
    if report['status'] in ('queued', 'applying', 'observing') and status.get('ActiveState') != 'active':
        report['observer_status']['warning'] = 'Observer is not active; the saved report is not a completed acceptance. Review state/rollback before retrying.'
    recovery.save(directory/'download.json', report)
    print('Saved report status: '+report['status'])
    if report.get('planned_end'):
        print('Planned observation end: '+report['planned_end'])
    if report.get('error_type'):
        print('Failure: '+report.get('stage', 'unknown stage')+' / '+report['error_type'])
        if report.get('error_details', {}).get('sqlite_name'):
            print('SQLite: '+report['error_details']['sqlite_name'])
        print('Rollback status: '+report.get('rollback', {}).get('status', 'unknown'))
    if report['observer_status'].get('warning'):
        print(report['observer_status']['warning'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--workflow-id')
    parser.add_argument('--previous-run')
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'trendradar-lazy-\d{8}T\d{6}Z-[a-f0-9]{8}', args.run_id) or os.geteuid() != 0:
        raise RuntimeError('Root and a valid trial ID are required')
    os.umask(0o077)
    directory = recovery.BACKUPS/args.run_id
    if args.status:
        if args.launch or args.rollback:
            raise RuntimeError('Status cannot be combined with a mutation')
        export_status(directory, args.run_id)
        return
    if args.launch:
        if args.rollback or not re.fullmatch(r'workflow-probe-\d{8}T\d{6}Z-[a-f0-9]{8}', args.workflow_id or ''):
            raise RuntimeError('A valid workflow evidence ID is required')
        if args.previous_run and not re.fullmatch(r'trendradar-lazy-\d{8}T\d{6}Z-[a-f0-9]{8}', args.previous_run):
            raise RuntimeError('Invalid preceding trial ID')
        launch(directory, args.run_id, args.workflow_id, args.previous_run)
        return
    import fcntl
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    report = json.loads((directory/'report.json').read_text())
    state = {'phase': 'preflight'}
    owns_report = False
    try:
        with Path('/run/lock/finevent-memory-recovery.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.rollback:
                state = json.loads((directory/'state.json').read_text())
                report['rollback'] = restore(directory, state)
                report['status'] = 'rolled_back'
                owns_report = True
            else:
                if report['status'] != 'queued':
                    raise RuntimeError('This trial has already started; do not reapply')
                owns_report = True
                run_trial(directory, state, report)
    except BlockingIOError:
        # Do not overwrite the active report when a manual rollback races the live trial.
        if args.rollback or report['status'] != 'queued':
            raise RuntimeError('Observation is running; stop its recorded systemd unit to request rollback')
        report.update(status='incomplete', error='Another memory operation holds the trial lock; no patch applied.')
        owns_report = True
    finally:
        if owns_report:
            report['finished_at'] = recovery.diagnostic.stamp(time.time())
            recovery.save(directory/'report.json', report)
            print('Evidence: '+str(directory/'report.json'), flush=True)


if __name__ == '__main__':
    main()
