"""Measure startup imports in temporary, offline containers; leave production unchanged."""

import argparse
import ast
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time

MIB = 1024**2
MARKER = 'FINEVENT_IMPORT_RESULT='
LABEL = 'com.finevent.import-probe'
CLIENT = Path('/app/trendradar/ai/client.py')
CASES = ('baseline', 'original', 'lazy', 'lazy', 'original')


def client_tree(source, digest, lazy):
    if hashlib.sha256(source).hexdigest() != digest:
        raise RuntimeError('Installed AI client differs from the audited source')
    tree = ast.parse(source)
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == 'litellm']
    if len(imports) != 1 or [(item.name, item.asname) for item in imports[0].names] != [('completion', None)]:
        raise RuntimeError('Unexpected LiteLLM import shape')
    clients = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'AIClient']
    methods = [node for client in clients for node in client.body if isinstance(node, ast.FunctionDef) and node.name == 'chat']
    if len(clients) != 1 or len(methods) != 1:
        raise RuntimeError('Unexpected AIClient.chat shape')
    method = methods[0]
    references = {id(node) for node in ast.walk(method) if isinstance(node, ast.Name) and node.id == 'completion'}
    if not references or any(isinstance(node, ast.Name) and node.id == 'completion' and id(node) not in references for node in ast.walk(tree)):
        raise RuntimeError('completion is referenced outside AIClient.chat')
    if lazy:
        # A candidate in this disposable interpreter only; never write the installed module.
        tree.body.remove(imports[0])
        docstring = ast.get_docstring(method) is not None
        method.body.insert(1 if docstring else 0, imports[0])
    return ast.fix_missing_locations(tree)


class ClientLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, code):
        self.code = code

    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'trendradar.ai.client':
            return importlib.util.spec_from_loader(fullname, self, origin=str(CLIENT))
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.__file__ = str(CLIENT)
        exec(self.code, module.__dict__)


def proc_numbers(path, names):
    result = {}
    for line in path.read_text().splitlines():
        parts = line.replace(':', '').split()
        if parts and parts[0] in names and len(parts) >= 2 and parts[1].isdigit():
            result[parts[0]] = int(parts[1]) * (1024 if parts[-1] == 'kB' else 1)
    return result


def child(case, digest, workflow=False):
    import resource
    result = {'case': case, 'client_sha256': digest, 'python': sys.version.split()[0]}
    started = time.monotonic()
    try:
        if workflow:
            import trendradar_workflow_fixture as fixture
            fixture.verify_source(Path('/app'))
            fixture.prepare(Path('/tmp/workflow'))
        # Both variants use the same parser/loader so instrumentation is comparable.
        code = compile(client_tree(CLIENT.read_bytes(), digest, case == 'lazy'), str(CLIENT), 'exec')
        if case != 'baseline':
            sys.meta_path.insert(0, ClientLoader(code))
            sys.path.insert(0, '/app')
            importlib.import_module('trendradar.__main__')  # No main() / runpy / collection call.
        result['import_seconds'] = time.monotonic()-started
        if workflow:
            result['workflow'] = fixture.run(Path('/tmp/workflow'))
            result['total_seconds'] = time.monotonic()-started
        result.update(status='ok',
                      peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                      process=proc_numbers(Path('/proc/self/status'), {'VmRSS', 'RssAnon', 'VmSwap'}),
                      proportional=proc_numbers(Path('/proc/self/smaps_rollup'), {'Pss', 'SwapPss'}),
                      litellm_loaded='litellm' in sys.modules,
                      main_loaded='trendradar.__main__' in sys.modules)
        result['cgroup_peak_bytes'] = int(Path('/sys/fs/cgroup/memory.peak').read_text())
    except BaseException as error:
        result.update(status='failed', error_type=type(error).__name__)
    print(MARKER+json.dumps(result), flush=True)
    return 0 if result['status'] == 'ok' else 1


def create_command(name, runner, image, case, digest, run_id, workflow=False):
    fixture_mount = ('--mount', 'type=bind,src='+str(runner.parent/'trendradar_workflow_fixture.py')+',dst=/trendradar_workflow_fixture.py,readonly') if workflow else ()
    return ('docker', 'create', '--pull=never', '--name', name, '--label', LABEL+'='+run_id,
            '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--user=65534:65534', '--pids-limit=32', '--memory=192m', '--memory-swap=192m',
            '--cpus=0.5', '--no-healthcheck', '--log-driver=none',
            '--tmpfs', '/tmp:rw,noexec,nosuid,size=16m,mode=1777',
            '--env', 'HOME=/tmp', '--env', 'PYTHONDONTWRITEBYTECODE=1',
            '--mount', 'type=bind,src='+str(runner)+',dst=/probe.py,readonly',
            *fixture_mount,
            '--workdir=/app', '--entrypoint=python', image,
            '-B', '/probe.py', '--child', case, '--client-sha256', digest,
            *(('--workflow',) if workflow else ()))


def cleanup_container(recovery, name, run_id):
    template = '{"id":{{json .Id}},"owner":{{json (index .Config.Labels "'+LABEL+'")}}}'
    inspection = recovery.command('docker', 'inspect', '--format', template, name, check=False, timeout=10)
    if inspection.returncode:
        # Distinguish absence from a daemon failure; never treat an inspection error as cleanup success.
        matches = recovery.command('docker', 'ps', '-aq', '--filter', 'name=^/'+name+'$', timeout=10).stdout.strip()
        if not matches:
            return 'absent'
        raise RuntimeError('Could not verify the temporary container for cleanup')
    target = json.loads(inspection.stdout)
    if target.get('owner') != run_id or not re.fullmatch('[a-f0-9]{64}', target.get('id', '')):
        raise RuntimeError('Temporary container ownership mismatch; refusing removal')
    recovery.command('docker', 'rm', '-f', '-v', target['id'], timeout=15)
    return 'removed'


def decode_result(output, case, digest, workflow=False):
    lines = [line[len(MARKER):] for line in output.splitlines() if line.startswith(MARKER)]
    if len(lines) != 1:
        raise RuntimeError('No unique structured result; raw module output is not exported')
    result = json.loads(lines[0])
    if result.get('case') != case or result.get('client_sha256') != digest:
        raise RuntimeError('Import measurement returned mismatched evidence')
    if result.get('status') != 'ok':
        error_type = result.get('error_type', '')
        if not isinstance(error_type, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', error_type):
            error_type = 'unknown'
        raise RuntimeError('Import measurement failed: '+error_type)
    if result.get('litellm_loaded') is not (case == 'original') or result.get('main_loaded') is not (case != 'baseline'):
        raise RuntimeError('The observed module imports do not match the candidate')
    for field in ('peak_rss_bytes', 'cgroup_peak_bytes'):
        if not isinstance(result.get(field), int) or result[field] <= 0:
            raise RuntimeError('Missing import memory measurement')
    if workflow:
        validate_workflow(result.get('workflow'))
    return result


def validate_workflow(value):
    if not isinstance(value, dict) or value.get('rounds') != 2 or value.get('http_requests') != 20 or value.get('finevent_rows') != 17 or value.get('production_data_used') is not False:
        raise RuntimeError('Incomplete offline workflow evidence')
    for name in ('schema_sha256', 'tables_sha256', 'finevent_projection_sha256'):
        if not isinstance(value.get(name), str) or not re.fullmatch('[a-f0-9]{64}', value[name]):
            raise RuntimeError('Missing workflow database digest')
    html = value.get('html_sha256')
    if not isinstance(html, dict) or len(html) < 4 or any(not isinstance(item, str) or not re.fullmatch('[a-f0-9]{64}', item) for item in html.values()):
        raise RuntimeError('Missing workflow HTML evidence')


def check_headroom(recovery, target, initial=False):
    memory = recovery.meminfo()
    current = recovery.diagnostic.cgroup_memory(target['pid'])
    if current.get('memory.events', {}).get('oom_kill') != target['cgroup']['memory.events']['oom_kill']:
        raise RuntimeError('Production TrendRadar OOM counter changed')
    if not isinstance(current.get('memory.current'), int) or current['memory.current'] >= 64*MIB:
        raise RuntimeError('Production collection is active; stop the import probe')
    threshold = (256 if initial else 96)*MIB
    if memory['MemAvailable'] < threshold:
        raise RuntimeError('Host headroom is too low for this isolated import measurement')
    # The established production schedule runs at minute 00 and 30.
    if 1800-time.time()%1800 < (240 if initial else 60):
        raise RuntimeError('Too close to the next scheduled production collection')
    return memory['MemAvailable']


def run_case(recovery, directory, target, image, digest, run_id, index, case, report, workflow=False):
    check_headroom(recovery, target, initial=True)
    name = 'finevent-'+run_id+'-'+str(index)
    row = {'case': case, 'status': 'started', 'temporary_container': name}
    report['runs'].append(row)
    recovery.save(directory/'report.json', report)
    process = None
    try:
        recovery.command(*create_command(name, directory/Path(__file__).name, image, case, digest, run_id, workflow), timeout=15)
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(('docker', 'start', '-a', name), stdout=stdout, stderr=stderr)
            start = time.monotonic()
            minimum = recovery.meminfo()['MemAvailable']
            while process.poll() is None:
                minimum = min(minimum, check_headroom(recovery, target))
                row['host_available_min_mib'] = minimum/MIB
                if time.monotonic()-start > 45:
                    raise RuntimeError('Import measurement exceeded 45 seconds')
                if stdout.tell() > MIB or stderr.tell() > MIB:
                    raise RuntimeError('Module output exceeded the bounded probe limit')
                time.sleep(0.5)
            row['host_available_min_mib'] = minimum/MIB
            row['exit_code'] = process.returncode
            state = json.loads(recovery.command('docker', 'inspect', '--format',
                '{"oom_killed":{{.State.OOMKilled}},"exit_code":{{.State.ExitCode}}}', name).stdout)
            row['container_exit'] = state
            stdout.seek(0)
            output = stdout.read(MIB).decode(errors='replace')
            if state['oom_killed']:
                raise RuntimeError('Isolated import failed; inspect exit/OOM evidence without increasing limits blindly')
            row['measurement'] = decode_result(output, case, digest, workflow)
            if process.returncode or state['exit_code']:
                raise RuntimeError('Import returned evidence but its process exited unsuccessfully')
            row['status'] = 'measured'
    except BaseException as error:
        row.update(status='incomplete', error_type=type(error).__name__)
        raise
    finally:
        try:
            row['cleanup'] = cleanup_container(recovery, name, run_id)
        except BaseException as cleanup_error:
            row['cleanup'] = {'status': 'needs_review', 'error_type': type(cleanup_error).__name__}
            raise
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
            recovery.save(directory/'report.json', report)


def compare(runs, workflow=False):
    cases = ('original', 'lazy') if workflow else CASES
    if [row.get('case') for row in runs] != list(cases) or any(row.get('status') != 'measured' or row.get('cleanup') != 'removed' for row in runs):
        raise RuntimeError('Incomplete measurements cannot produce a savings estimate')
    if workflow:
        values = [row['measurement'].get('workflow') for row in runs]
        for value in values:
            validate_workflow(value)
        if values[0] != values[1]:
            raise RuntimeError('Original and lazy workflow outputs differ; inspect saved hashes')
        return {'outputs_identical': True, 'fixture': values[0],
                'limits': 'Synthetic HTTP responses and fixed time; not live source coverage, production configuration or real collection peak.'}
    groups = {case: [row['measurement'] for row in runs if row['case'] == case] for case in set(CASES)}
    result = {}
    for case, rows in groups.items():
        result[case] = {'samples': len(rows),
                        'median_peak_rss_mib': statistics.median(row['peak_rss_bytes'] for row in rows)/MIB,
                        'peak_rss_range_mib': [min(row['peak_rss_bytes'] for row in rows)/MIB, max(row['peak_rss_bytes'] for row in rows)/MIB],
                        'median_import_seconds': statistics.median(row['import_seconds'] for row in rows)}
    result['original_minus_lazy_peak_rss_mib'] = result['original']['median_peak_rss_mib']-result['lazy']['median_peak_rss_mib']
    result['limits'] = 'Offline startup-import comparison only; not production collection peak, HTML correctness, or Docker removal savings.'
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id')
    parser.add_argument('--audit-id')
    parser.add_argument('--child', choices=tuple(set(CASES)))
    parser.add_argument('--client-sha256')
    parser.add_argument('--workflow', action='store_true')
    args = parser.parse_args()
    if args.child:
        if not re.fullmatch('[a-f0-9]{64}', args.client_sha256 or ''):
            raise RuntimeError('Invalid client digest')
        if args.workflow and args.child == 'baseline':
            raise RuntimeError('A workflow comparison requires an application variant')
        return child(args.child, args.client_sha256, args.workflow)
    prefix = 'workflow' if args.workflow else 'import'
    if not re.fullmatch(prefix+r'-probe-\d{8}T\d{6}Z-[a-f0-9]{8}', args.run_id or '') or not re.fullmatch(r'trendradar-audit-\d{8}T\d{6}Z-[a-f0-9]{8}', args.audit_id or '') or os.geteuid() != 0:
        raise RuntimeError('Root and valid run/audit IDs are required')
    import fcntl
    import memory_recovery as recovery
    os.umask(0o077)
    for sig in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(sig, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with Path('/run/lock/finevent-memory-recovery.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directory = recovery.BACKUPS/args.run_id
        directory.mkdir(mode=0o700)
        runner = directory/Path(__file__).name
        shutil.copyfile(__file__, runner); runner.chmod(0o644)
        if args.workflow:
            fixture_runner = directory/'trendradar_workflow_fixture.py'
            shutil.copyfile(Path(__file__).with_name(fixture_runner.name), fixture_runner)
            fixture_runner.chmod(0o644)
        report = {'status': 'preflight', 'created_at': recovery.diagnostic.stamp(time.time()),
                  'audit_id': args.audit_id, 'kind': prefix, 'runs': []}
        try:
            audit = json.loads((recovery.BACKUPS/args.audit_id/'report.json').read_text())
            if audit.get('status') != 'collected_for_review':
                raise RuntimeError('A completed audit is required')
            target = recovery.inspect_target()
            image = recovery.command('docker', 'inspect', '--format', '{{.Image}}', target['id']).stdout.strip()
            audited = next(row for row in audit['containers'] if row['id'] == target['id'])
            if image != audited['image_id'] or not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
                raise RuntimeError('Production image changed since the audit')
            digest = audit['source']['files']['trendradar/ai/client.py']['sha256']
            if not re.fullmatch('[a-f0-9]{64}', digest):
                raise RuntimeError('Missing audited client digest')
            report.update(image_id=image, client_sha256=digest, production_before=target, status='measuring')
            cases = ('original', 'lazy') if args.workflow else CASES
            for index, case in enumerate(cases):
                print('Isolated '+prefix+' '+str(index+1)+'/'+str(len(cases))+': '+case, flush=True)
                run_case(recovery, directory, target, image, digest, args.run_id, index, case, report, args.workflow)
            report['comparison'] = compare(report['runs'], args.workflow)
            report['production_after'] = recovery.inspect_target()
            if report['production_after']['pid'] != target['pid'] or report['production_after']['cgroup']['memory.events']['oom_kill'] != target['cgroup']['memory.events']['oom_kill']:
                raise RuntimeError('Production process or OOM counter changed')
            report['health'] = recovery.public_health()
            report['status'] = 'completed_for_review' if all(item == {'exit': 0, 'http_status': '200'} for item in report['health'].values()) else 'needs_review'
        except BaseException as error:
            report.update(status='incomplete', error_type=type(error).__name__,
                          error=str(error) if isinstance(error, RuntimeError) else 'Probe interrupted; review saved evidence.')
            raise
        finally:
            report['finished_at'] = recovery.diagnostic.stamp(time.time())
            recovery.save(directory/'report.json', report)
            print('Evidence: '+str(directory/'report.json'), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
