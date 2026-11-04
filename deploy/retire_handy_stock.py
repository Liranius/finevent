"""Stop the user-retired handy-stock API; preserve its program, data and unit files."""

import argparse
import hashlib
import os
from pathlib import Path
import re
import time

import memory_recovery as recovery

UNIT = 'handy-stock-api.service'
FIELDS = ('Id', 'LoadState', 'ActiveState', 'SubState', 'UnitFileState', 'MainPID',
          'ControlGroup', 'FragmentPath', 'DropInPaths', 'TriggeredBy', 'ConsistsOf', 'PropagatesStopTo', 'KillMode')


def service_state():
    body = recovery.command('systemctl', 'show', UNIT, '--no-pager', '--property='+','.join(FIELDS)).stdout
    values = dict(line.split('=', 1) for line in body.splitlines() if '=' in line)
    result = {name: values.get(name, '') for name in FIELDS}
    result['MainPID'] = int(result['MainPID'] or '0')
    return result


def validate_identity(state, proc=Path('/proc')):
    if state['Id'] != UNIT or state['LoadState'] != 'loaded':
        raise RuntimeError('The requested handy-stock API unit is not loaded under its exact identity')
    if any(state.get(name) for name in ('TriggeredBy', 'ConsistsOf', 'PropagatesStopTo')):
        raise RuntimeError('The API has activation or stop-propagation relationships requiring review')
    paths = [Path(state['FragmentPath']), *(Path(value) for value in state['DropInPaths'].split())]
    files = []
    has_app_command = False
    for path in paths:
        body = path.read_bytes()
        if len(body) > 65536:
            raise RuntimeError('Unexpected unit file size')
        text = body.decode('utf-8')
        # disable can follow Install.Also; refuse rather than disable another application.
        if re.search(r'^[ \t]*Also[ \t]*=[ \t]*[^ \t\r\n]', text, re.MULTILINE):
            raise RuntimeError('Unit Also= would disable additional units; no changes made')
        has_app_command |= bool(re.search(r'^\s*ExecStart\s*=.*?/srv/example/legacy-app/', text, re.MULTILINE))
        files.append({'path': str(path), 'sha256': hashlib.sha256(body).hexdigest()})
    if not has_app_command:
        raise RuntimeError('The unit no longer identifies an executable under /srv/example/legacy-app')
    identity = {'unit_files': files}
    if state['MainPID']:
        entry = proc/str(state['MainPID'])
        executable = os.readlink(entry/'exe')
        arguments = (entry/'cmdline').read_bytes().split(b'\0')
        if not executable.startswith('/srv/example/legacy-app/') or Path(executable).name != 'node' or b'/srv/example/legacy-app/current/api/dist/main.js' not in arguments:
            raise RuntimeError('The running process differs from the audited handy-stock API')
        if state['ControlGroup'] != '/system.slice/'+UNIT or state['KillMode'] not in ('control-group', 'mixed'):
            raise RuntimeError('Unexpected API cgroup or stop behavior')
        identity.update(executable=executable,
                        memory=recovery.diagnostic.read_numbers(entry/'smaps_rollup', {'Pss', 'SwapPss'}))
    return identity


def restore_commands(before):
    commands = []
    if before['UnitFileState'] == 'enabled':
        commands.append(['systemctl', 'enable', UNIT])
    elif before['UnitFileState'] == 'enabled-runtime':
        commands.append(['systemctl', 'enable', '--runtime', UNIT])
    if before['ActiveState'] == 'active':
        commands.append(['systemctl', 'start', UNIT])
    return commands


def stopped(state):
    return (state['Id'] == UNIT and state['ActiveState'] == 'inactive' and state['MainPID'] == 0
            and state['UnitFileState'] in ('disabled', 'static', 'indirect', 'masked', 'masked-runtime'))


def retire(directory, report):
    before = service_state()
    identity = validate_identity(before)
    report.update(before=before, identity=identity, host_before=recovery.meminfo(),
                  restore_commands=restore_commands(before), status='validated')
    recovery.save(directory/'report.json', report)
    if before['UnitFileState'] not in ('enabled', 'enabled-runtime', 'disabled', 'static', 'indirect', 'masked', 'masked-runtime'):
        raise RuntimeError('Unexpected enablement state; retain the unit and review before changing it')
    report['status'] = 'stopping'
    recovery.save(directory/'report.json', report)
    if before['UnitFileState'] in ('enabled', 'enabled-runtime'):
        flags = ('--runtime',) if before['UnitFileState'] == 'enabled-runtime' else ()
        recovery.command('systemctl', 'disable', *flags, UNIT)
    recovery.command('systemctl', 'stop', '--no-block', UNIT)
    deadline = time.monotonic()+45
    stable = 0
    while time.monotonic() < deadline:
        after = service_state()
        report['after'] = after
        stable = stable+1 if stopped(after) else 0
        if stable >= 3:
            break
        time.sleep(1)
    if stable < 3:
        raise RuntimeError('The API has not remained inactive and disabled; inspect the saved state')
    group = Path('/sys/fs/cgroup/system.slice')/UNIT/'cgroup.procs'
    if group.exists() and group.read_text().strip():
        raise RuntimeError('Processes remain in the API cgroup after stop')
    report['host_after'] = recovery.meminfo()
    report['health'] = recovery.public_health()
    report['status'] = 'completed_for_review' if all(value == {'exit': 0, 'http_status': '200'} for value in report['health'].values()) else 'needs_review'
    report['memory_interpretation'] = 'Before/after host snapshots include concurrent workloads; not a causal measurement of reclaimed RAM.'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'retire-handy-\d{8}T\d{6}Z-[a-f0-9]{8}', args.run_id) or os.geteuid() != 0:
        raise RuntimeError('Root and a valid retirement ID are required')
    import fcntl
    os.umask(0o077)
    with Path('/run/lock/finevent-memory-recovery.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directory = recovery.BACKUPS/args.run_id
        directory.mkdir(mode=0o700)
        report = {'unit': UNIT, 'status': 'preflight', 'created_at': recovery.diagnostic.stamp(time.time())}
        try:
            retire(directory, report)
        except BaseException as error:
            report.update(status='incomplete', error_type=type(error).__name__,
                          error=str(error) if isinstance(error, RuntimeError) else 'Operation interrupted; inspect the saved service state.')
            # Do not restart an app the user explicitly retired merely because a later check failed.
            raise
        finally:
            report['finished_at'] = recovery.diagnostic.stamp(time.time())
            recovery.save(directory/'report.json', report)
            print('Evidence: '+str(directory/'report.json'), flush=True)


if __name__ == '__main__':
    main()
