"""Try runtime reclaim policies inside the authorized RAM/swap limits."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import time

import memory_recovery as recovery

HIGH = 160*recovery.MIB
SWAPPINESS = 60
SWAPPINESS_FILE = Path('/proc/sys/vm/swappiness')
SCOPE = 'docker-'+recovery.EXPECTED_CONTAINER+'.scope'
CGROUP = Path('/sys/fs/cgroup/system.slice')/SCOPE


def read_high():
    value = (CGROUP/'memory.high').read_text().strip()
    if value != 'max' and not value.isdigit():
        raise RuntimeError('Unrecognized memory.high value')
    return value


def set_high(value):
    setting = 'infinity' if value == 'max' else value
    recovery.command('systemctl', 'set-property', '--runtime', SCOPE, 'MemoryHigh='+setting)
    if read_high() != value:
        raise RuntimeError('The requested memory.high did not take effect')


def read_swappiness():
    value = SWAPPINESS_FILE.read_text().strip()
    if not value.isdigit() or not 0 <= int(value) <= 200:
        raise RuntimeError('Unrecognized vm.swappiness value')
    return int(value)


def set_swappiness(value):
    if not isinstance(value, int) or not 0 <= value <= 200:
        raise RuntimeError('Invalid vm.swappiness value')
    recovery.command('sysctl', '-w', 'vm.swappiness='+str(value))
    if read_swappiness() != value:
        raise RuntimeError('The requested vm.swappiness did not take effect')


def full_pressure(path=None):
    path = CGROUP/'memory.pressure' if path is None else path
    for line in path.read_text().splitlines():
        fields = line.split()
        if fields and fields[0] == 'full':
            values = dict(field.split('=', 1) for field in fields[1:])
            return int(values['total'])
    raise RuntimeError('Memory pressure metrics unavailable: '+str(path))


def restore_high(state):
    if not state.get('change_attempted'):
        return {'status': 'no_changes'}
    current = read_high()
    previous = state['previous_high']
    if current not in (str(HIGH), previous):
        raise RuntimeError('memory.high was changed independently; refusing to overwrite')
    if current != previous:
        set_high(previous)
    return {'status': 'restored', 'memory_high': previous,
            'ram_and_swap': '192 MiB RAM, 256 MiB container swap and host swap retained'}


def restore_policy(state):
    # Release throttling before restoring the old preference for avoiding swap.
    result = restore_high(state)
    if state.get('swappiness_change_attempted'):
        current = read_swappiness()
        previous = state['previous_swappiness']
        if current not in (SWAPPINESS, previous):
            raise RuntimeError('vm.swappiness was changed independently; refusing to overwrite')
        if current != previous:
            set_swappiness(previous)
        result.update(status='restored', vm_swappiness=previous)
    state['restored'] = True
    return result


def pressure_guard(report, monitor_host=False, cgroup=None):
    get_high = read_high if cgroup is None else lambda: (cgroup/'memory.high').read_text().strip()
    get_pressure = full_pressure if cgroup is None else lambda: full_pressure(cgroup/'memory.pressure')
    previous_time = time.monotonic()
    previous_total = get_pressure()
    previous_host_total = full_pressure(Path('/proc/pressure/memory')) if monitor_host else None
    consecutive = 0
    host_consecutive = 0
    metrics = {'highest_full_stall_fraction': 0, 'pressure_samples': 0}
    if monitor_host:
        metrics['highest_host_full_stall_fraction'] = 0
    report['reclaim_pressure'] = metrics

    def check(_sample):
        nonlocal previous_time, previous_total, consecutive, previous_host_total, host_consecutive
        if get_high() != str(HIGH):
            return 'Early reclaim boundary changed during observation'
        if monitor_host and read_swappiness() != SWAPPINESS:
            return 'vm.swappiness changed during observation'
        now = time.monotonic(); total = get_pressure()
        if total < previous_total:
            return 'Memory pressure counter reset during observation'
        elapsed = max(1, (now-previous_time)*1000000)
        fraction = (total-previous_total)/elapsed
        if monitor_host:
            host_total = full_pressure(Path('/proc/pressure/memory'))
            if host_total < previous_host_total:
                return 'Host memory pressure counter reset during observation'
            host_fraction = (host_total-previous_host_total)/elapsed
            metrics['highest_host_full_stall_fraction'] = max(metrics['highest_host_full_stall_fraction'], host_fraction)
            host_consecutive = host_consecutive+1 if host_fraction >= 0.5 else 0
            previous_host_total = host_total
        metrics['pressure_samples'] += 1
        metrics['highest_full_stall_fraction'] = max(metrics['highest_full_stall_fraction'], fraction)
        consecutive = consecutive+1 if fraction >= 0.5 else 0
        previous_time, previous_total = now, total
        if consecutive >= 3:
            return 'TrendRadar stalled on memory for at least half of three consecutive sampling intervals'
        if host_consecutive >= 3:
            return 'Host stalled on memory for at least half of three consecutive sampling intervals'
        return None
    return check


def run_trial(directory, state, report):
    previous_report = json.loads((recovery.BACKUPS/state['previous_run']/'report.json').read_text())
    if previous_report.get('status') not in ('needs_review', 'completed_for_review') or not previous_report.get('finished_at'):
        raise RuntimeError('A completed preceding memory trial is required')
    recovery.secure_swap_path(require_file=True)
    swap = recovery.swap_usage().get(str(recovery.SWAP_FILE))
    if not swap or swap['size_bytes'] < recovery.SWAP_SIZE-recovery.MIB:
        raise RuntimeError('The authorized host swap buffer is not active')
    target = recovery.wait_for_headroom()
    recovery.validate_limits(target, recovery.MEMORY, recovery.TOTAL_MEMORY_SWAP)
    if target['cgroup']['path'] != '/system.slice/'+SCOPE:
        raise RuntimeError('Unexpected cgroup path')
    previous = read_high()
    if previous != 'max':
        raise RuntimeError('An early reclaim policy already exists; review it before another trial')
    full_pressure()  # Require the watchdog metrics before changing the policy.
    previous_swappiness = read_swappiness()
    enable_swap_policy = state.get('enable_swap_policy', False)
    if enable_swap_policy:
        if previous_swappiness != 0:
            raise RuntimeError('The diagnosed vm.swappiness=0 has changed; review before another trial')
        full_pressure(Path('/proc/pressure/memory'))
    state.update(previous_high=previous, change_attempted=True)
    if enable_swap_policy:
        state.update(previous_swappiness=previous_swappiness, swappiness_change_attempted=True)
    report['before'] = {'container': target, 'vm_swappiness': previous_swappiness,
                        'host_memory': recovery.meminfo()}
    recovery.save(directory/'state.json', state)
    recovery.save(directory/'report.json', report)
    if enable_swap_policy:
        # Historical runtime trial. Production sysctl/slice persistence was accepted on
        # 2026-10-30 through trendradar_persist.py; this helper itself changes runtime state only.
        set_swappiness(SWAPPINESS)
    set_high(str(HIGH))
    report['status'] = 'observing'
    report['applied_at'] = recovery.diagnostic.stamp(time.time())
    def undo():
        report['rollback'] = restore_policy(state)
        recovery.save(directory/'state.json', state)
    recovery.observe(directory, report, sample_guard=pressure_guard(report, monitor_host=enable_swap_policy), on_failure=undo)
    if report['status'] == 'completed_for_review':
        state['retained'] = True
        report['configuration'] = '160 MiB early reclaim boundary retained for review; existing 192 MiB RAM and 256 MiB swap caps unchanged'
        if enable_swap_policy:
            report['configuration'] += '; runtime vm.swappiness=60 retained for review, no boot configuration written'
    else:
        report['rollback'] = restore_policy(state)
        report['configuration'] = 'runtime reclaim policies reverted; preceding RAM/swap trial retained'
    report['effective_high_after_trial'] = read_high()
    report['effective_swappiness_after_trial'] = read_swappiness()
    recovery.save(directory/'state.json', state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--previous-run', default='memory-recovery-20261028T122540Z-c9e93876')
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--enable-swap-policy', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'memory-high-\d{8}T\d{6}Z-[a-f0-9]{8}', args.run_id):
        raise RuntimeError('Invalid run ID')
    if not re.fullmatch(r'memory-recovery-\d{8}T\d{6}Z-[a-f0-9]{8}', args.previous_run) or os.geteuid() != 0:
        raise RuntimeError('Root and a valid preceding trial ID are required')
    import fcntl
    os.umask(0o077)
    signal.signal(signal.SIGHUP, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with Path('/run/lock/finevent-memory-recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directory = recovery.BACKUPS/args.run_id
        if args.rollback:
            state = json.loads((directory/'state.json').read_text())
            report = json.loads((directory/'report.json').read_text())
            report['rollback'] = restore_policy(state)
            report['status'] = 'rolled_back'
            recovery.save(directory/'state.json', state)
            recovery.save(directory/'report.json', report)
            print('Runtime reclaim policies restored; existing RAM/swap limits retained.')
            return
        directory.mkdir(mode=0o700)
        for name in ('memory_high_trial.py', 'memory_recovery.py', 'memory_diagnostic.py'):
            shutil.copy2(Path(__file__).parent/name, directory/name)
        state = {'id': args.run_id, 'previous_run': args.previous_run, 'enable_swap_policy': args.enable_swap_policy}
        report = {'id': args.run_id, 'previous_run': args.previous_run, 'enable_swap_policy': args.enable_swap_policy, 'status': 'preflight',
                  'created_at': recovery.diagnostic.stamp(time.time()),
                  'rollback_command': f'python3 {directory}/memory_high_trial.py --run-id {args.run_id} --rollback'}
        recovery.save(directory/'state.json', state)
        recovery.save(directory/'report.json', report)
        try:
            run_trial(directory, state, report)
        except BaseException as error:
            report['status'] = 'incomplete'
            report['error_type'] = type(error).__name__
            report['error'] = str(error) if isinstance(error, RuntimeError) else 'Observation interrupted; inspect saved evidence.'
            if state.get('change_attempted') and not state.get('restored'):
                try:
                    report['rollback'] = restore_policy(state)
                except BaseException as restore_error:
                    report['rollback'] = {'status': 'needs_review', 'error_type': type(restore_error).__name__}
            raise
        finally:
            report['finished_at'] = recovery.diagnostic.stamp(time.time())
            recovery.save(directory/'state.json', state)
            recovery.save(directory/'report.json', report)
            print('Trial status: '+report['status'], flush=True)
            print('Evidence: '+str(directory/'report.json'), flush=True)
            print('Rollback after review: '+report['rollback_command'], flush=True)


if __name__ == '__main__':
    main()
