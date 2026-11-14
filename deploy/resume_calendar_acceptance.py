"""Continue a registered calendar verification after an OOM with a completed installation."""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import calendar_release as calendar


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def preserve(path, data):
    """Create evidence once, including across an interrupted or repeated launch."""
    if path.exists():
        calendar.require(path.read_bytes() == data, 'Earlier recovery evidence differs')
        return
    with path.open('xb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def installed_backup(controller, run_id, report, commit):
    calendar.require(controller.latest() == run_id and report.get('id') == run_id
                     and report.get('unit') == f'finevent-{run_id}.service'
                     and report.get('status') == 'deploying' and report.get('stage') == 'install'
                     and report.get('verification_profile') == 'calendar'
                     and report.get('preflight', {}).get('status') == 'passed'
                     and not any(k in report for k in ('failure', 'rollback', 'deployment', 'restart', 'acceptance_recovery')),
                     'Expected the latest interrupted calendar installation')
    registration = controller.operation_path(run_id)
    record = read(registration/'operation.json')
    active = (calendar.ROOT/'current').resolve(strict=True)
    metadata = read(active/'release.json')
    backup = Path(report['installer_evidence']).resolve(strict=True)
    calendar.require(record['id'] == run_id and record['source_commit'] == metadata['source_commit'] == commit
                     and record['generic_sha256'] == metadata['generic_sha256']
                     and record['identity']['profile'] == 'calendar'
                     and Path(report['runner']) == registration/'bundle/deploy/calendar_release.py'
                     and active.parent == (calendar.ROOT/'releases').resolve()
                     and backup == controller.backups.resolve()/active.name,
                     'Registered operation, active release or installer evidence differs')
    manifest = read(active/'sha256.json')
    identity = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    calendar.require(identity == record['identity']['content_sha256'], 'Installed manifest differs from registered content')
    calendar.installer.verify_bundle(active)
    inner = read(backup/'report.json')
    calendar.require(inner.get('status') == 'verification_service_installed'
                     and inner.get('backup') == str(backup) and inner.get('release') == str(active)
                     and read(backup/'state.json')['previous'] == report['preflight']['previous_release']
                     and (backup/'database.db').is_file() and (backup/'database.db').stat().st_size > 0,
                     'Frozen backup or completed installer evidence is missing or mismatched')
    pid = calendar.service_pid()
    calendar.require(Path(f'/proc/{pid}/exe').samefile(active/'finevent')
                     and calendar.recovery.sha(calendar.DATA/'config.json') == report['preflight']['config_sha256'],
                     'Running executable or application configuration changed')
    calendar.recovery.command('systemctl', 'is-active', '--quiet', 'finevent-backup.timer')
    return backup


def recovery_baseline(report, row):
    # A cgroup OOM can also increment the host-wide counter. Account only for the
    # single confirmed observer death and preserve all original counts in preflight.
    before = report['preflight']['baseline']
    calendar.require(row['host_oom_kill'] - before['host_oom_kill'] in (0, 1),
                     'Unexpected additional or reset host OOM count')
    calendar.check_sample(row, {**before, 'host_oom_kill': row['host_oom_kill']})
    return row


def unit_status(unit):
    keys = ('ActiveState', 'Result', 'ExecMainCode', 'ExecMainStatus', 'MemoryMax', 'MainPID')
    # A reset transient unit may already have been garbage-collected. An absent
    # unit is only usable with the byte-exact saved OOM evidence below.
    result = calendar.recovery.command('systemctl', 'show', unit, *['--property='+key for key in keys], check=False)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def confirmed_oom(state):
    return (state.get('ActiveState') == 'failed' and state.get('MainPID') == '0'
            and state.get('Result') == 'oom-kill' and state.get('ExecMainCode') == '2'
            and state.get('ExecMainStatus') == '9' and state.get('MemoryMax') == str(64*calendar.MIB))


def resume(controller, args):
    import fcntl
    directory = controller.backups/args.run_id
    original = directory/'acceptance-before-oom-recovery.json'
    witness = directory/'acceptance-oom-unit.json'
    with ExitStack() as locks:
        # Match register -> transition -> observation ordering used by releases.
        register_path = controller.state/'register.lock'
        transition_path = calendar.LOCK.with_name(calendar.LOCK.name+'.transition')
        for path in (register_path, transition_path, calendar.LOCK):
            handle = locks.enter_context(path.open('a'))
            wait_for_launcher = args.worker and path == register_path
            fcntl.flock(handle, fcntl.LOCK_EX if wait_for_launcher else fcntl.LOCK_EX | fcntl.LOCK_NB)
            if path == register_path:
                register = handle
            if path == transition_path:
                transition = handle
        raw = (directory/'report.json').read_bytes()
        calendar.require(hashlib.sha256(raw).hexdigest() == args.report_sha256, 'Interrupted report changed; query this operation')
        report = json.loads(raw)
        backup = installed_backup(controller, args.run_id, report, args.commit)
        unit = report['unit']
        current = calendar.target()
        calendar.validate_target(current, report['preflight']['container'], calendar.parent_metrics())
        baseline = recovery_baseline(report, calendar.sample(current, calendar.service_pid()))
        if args.worker:
            calendar.require(unit_status(unit).get('MainPID') == str(os.getpid())
                             and original.read_bytes() == raw and confirmed_oom(read(witness)),
                             'Original failure evidence is missing')
        else:
            state = unit_status(unit)
            if confirmed_oom(state):
                preserve(original, raw)
                preserve(witness, json.dumps(state, sort_keys=True).encode())
                calendar.recovery.command('systemctl', 'reset-failed', unit)
            else:
                # Allow retry only in the reset-before-launch gap.
                calendar.require(state.get('ActiveState') == 'inactive' and state.get('MainPID') == '0'
                                 and original.read_bytes() == raw and confirmed_oom(read(witness)),
                                 'Original observer is not a stopped, confirmed OOM')
            # Keep the original unit name so existing Report clients see the actual worker.
            # --no-block prevents the new worker waiting for locks held by this launcher.
            calendar.recovery.command('systemd-run', '--no-block', '--unit='+unit, '--property=Type=exec',
                                      '--property=MemoryMax=64M', '--property=MemorySwapMax=0', '--property=Nice=10',
                                      '--property=RuntimeMaxSec=24000', '/usr/bin/python3', '-B', str(Path(__file__).resolve()),
                                      '--worker', '--run-id', args.run_id, '--commit', args.commit, '--report-sha256', args.report_sha256)
            return
        report['acceptance_recovery'] = {'started_at': calendar.stamp(), 'original_report_sha256': args.report_sha256,
                                         'original_unit': read(witness), 'baseline': baseline}
        report['stage'] = 'backup_verification'
        calendar.recovery.save(directory/'report.json', report)
        # Registration is needed only until the admission decision is durable.
        register.close()
        try:
            calendar.verify_installed(directory, report, backup, transition)
        except Exception as error:
            report.update(status='incomplete', finished_at=calendar.stamp(), failure_type=type(error).__name__,
                          failure=str(error)[:600] if isinstance(error, RuntimeError) else 'Acceptance recovery failed; inspect server evidence')
            calendar.recovery.save(directory/'report.json', report)
            raise
        calendar.recovery.save(directory/'report.json', report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--report-sha256', required=True)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    calendar.require(os.geteuid() == 0 and calendar.ID_PATTERN.fullmatch(args.run_id)
                     and re.fullmatch(r'[0-9a-f]{40}', args.commit) and re.fullmatch(r'[0-9a-f]{64}', args.report_sha256),
                     'Expected root, a registered operation, a commit and an exact report digest')
    os.umask(0o077)
    library = Path('/usr/local/lib/finevent-release')
    sys.path.insert(0, str(library))
    from release_control import Controller
    controller = Controller(read(library/'config.json'))
    calendar.require(controller.backups == calendar.BACKUPS and Path(controller.config['appRoot']) == calendar.ROOT,
                     'Acceptance tools are rendered for a different installation')
    resume(controller, args)


if __name__ == '__main__':
    main()
