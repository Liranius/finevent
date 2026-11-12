"""Reversible, explicitly authorized host-swap and TrendRadar memory trial."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import time

import memory_diagnostic as diagnostic

MIB = 1024**2
SWAP_SIZE = 1024*MIB
MEMORY = 192*MIB
TOTAL_MEMORY_SWAP = (192+256)*MIB
SWAP_FILE = Path('/srv/example/finevent-host/swapfile')
FSTAB = Path('/etc/fstab')
BACKUPS = Path('/srv/example/finevent-backups')
EXPECTED_CONTAINER = 'eca05c9c9f17aea221098e184714772ebeb60d88962bbd2d0183c5b9753f3f9f'


def command(*args, check=True, timeout=30):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        # Command output may contain source/configuration text; keep it out of reports and the terminal.
        raise RuntimeError(f'{args[0]} failed with exit {result.returncode}')
    return result


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.chmod(0o600)
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def meminfo():
    result = diagnostic.read_numbers(Path('/proc/meminfo'), {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'})
    if not result or 'MemAvailable' not in result:
        raise RuntimeError('Host memory metrics unavailable')
    return result


def inspect_target():
    template = '{"id":{{json .Id}},"pid":{{.State.Pid}},"running":{{.State.Running}},"restarts":{{.RestartCount}},"memory":{{.HostConfig.Memory}},"memory_swap":{{.HostConfig.MemorySwap}}}'
    result = json.loads(command('docker', 'inspect', '--format', template, 'trend-radar').stdout)
    if result['id'] != EXPECTED_CONTAINER or not result['running'] or result['pid'] <= 0:
        raise RuntimeError('The diagnosed TrendRadar container changed or is not running')
    result['cgroup'] = diagnostic.cgroup_memory(result['pid'])
    if not isinstance(result['cgroup'].get('memory.current'), int) or not result['cgroup'].get('memory.events'):
        raise RuntimeError('Readable cgroup v2 memory metrics are required')
    return result


def validate_limits(container, memory, total):
    group = container['cgroup']
    if (container['memory'], container['memory_swap']) != (memory, total):
        raise RuntimeError('Docker did not apply the requested limits')
    if (group.get('memory.max'), group.get('memory.swap.max')) != (memory, total-memory):
        raise RuntimeError('Actual cgroup memory/swap limits do not match Docker settings')


def swap_usage():
    rows = {}
    for line in Path('/proc/swaps').read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 5:
            rows[fields[0]] = {'size_bytes': int(fields[2])*1024, 'used_bytes': int(fields[3])*1024}
    return rows


def secure_swap_path(require_file=False):
    if SWAP_FILE.parent.parent.resolve() != Path('/var/lib'):
        raise RuntimeError('Unexpected host swap parent')
    if SWAP_FILE.parent.exists():
        info = SWAP_FILE.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise RuntimeError('Host swap directory must be root-owned and private')
    elif SWAP_FILE.parent.is_symlink():
        raise RuntimeError('Host swap directory cannot be a symlink')
    if require_file:
        info = SWAP_FILE.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise RuntimeError('Managed swap file changed ownership, type or permissions')


def add_fstab_entry(original):
    if str(SWAP_FILE) in original:
        raise RuntimeError('An existing fstab entry refers to the managed swap path')
    return original.rstrip('\n')+'\n\n# finevent managed host buffer\n'+str(SWAP_FILE)+' none swap sw,nofail 0 0\n'


def replace_fstab(content, original_stat):
    # Exclusive staging in the same directory preserves ownership/mode and makes replacement atomic.
    import tempfile
    with tempfile.NamedTemporaryFile(dir=FSTAB.parent, prefix='.finevent-fstab-', delete=False) as output:
        staged = Path(output.name)
        output.write(content); output.flush(); os.fsync(output.fileno())
    try:
        staged.chmod(stat.S_IMODE(original_stat.st_mode))
        os.chown(staged, original_stat.st_uid, original_stat.st_gid)
        staged.replace(FSTAB)
    finally:
        staged.unlink(missing_ok=True)


def wait_for_headroom():
    deadline = time.monotonic()+120
    while True:
        target = inspect_target()
        if meminfo()['MemAvailable'] >= 128*MIB and target['cgroup']['memory.current'] < 64*MIB:
            return target
        if time.monotonic() >= deadline:
            raise RuntimeError('No idle/headroom window in two minutes; no trial started')
        print('Waiting for an idle TrendRadar cycle and 128 MiB host headroom...', flush=True)
        time.sleep(10)


def allocate_swap(directory, state, size=SWAP_SIZE):
    # Only claim ownership after exclusive creation; never remove a pre-existing file on failure.
    with SWAP_FILE.open('xb') as output:
        os.chmod(SWAP_FILE, 0o600)
        identity = os.fstat(output.fileno())
        state.update(created_swap=True, swap_inode=identity.st_ino, swap_device=identity.st_dev)
        save(directory/'state.json', state)
        block = bytes(MIB)
        for offset in range(0, size, MIB):
            output.write(block[:min(MIB, size-offset)])
        output.flush(); os.fsync(output.fileno())


def apply_trial(directory, state):
    secure_swap_path()
    if SWAP_FILE.exists() or SWAP_FILE.is_symlink() or meminfo()['SwapTotal'] != 0:
        raise RuntimeError('Existing swap or managed file requires review; refusing to create a duplicate')
    if FSTAB.is_symlink() or not FSTAB.is_file():
        raise RuntimeError('A regular /etc/fstab is required')
    mount = json.loads(command('findmnt', '--json', '--target', '/var/lib', '--output', 'FSTYPE,OPTIONS').stdout)['filesystems'][0]
    if mount['fstype'] not in ('ext4', 'xfs') or 'rw' not in mount['options'].split(','):
        raise RuntimeError('Only writable ext4/xfs is supported by this swap-file trial')
    if shutil.disk_usage('/var/lib').free < 3*SWAP_SIZE:
        raise RuntimeError('At least 3 GiB free disk is required before allocating the 1 GiB buffer')
    previous = wait_for_headroom()
    if (previous['memory'], previous['memory_swap']) != (160*MIB, 160*MIB):
        raise RuntimeError('TrendRadar limits changed since diagnosis; review before applying')
    original = FSTAB.read_bytes()
    planned = add_fstab_entry(original.decode('utf-8')).encode('utf-8')
    shutil.copy2(FSTAB, directory/'fstab.before')
    state.update(previous=previous, fstab_before_sha=hashlib.sha256(original).hexdigest(),
                 fstab_planned_sha=hashlib.sha256(planned).hexdigest(), phase='preparing')
    save(directory/'state.json', state)
    SWAP_FILE.parent.mkdir(mode=0o700, exist_ok=True)
    # Full writes avoid sparse files and filesystem-dependent fallocate swap semantics.
    allocate_swap(directory, state)
    command('mkswap', '--label', 'finevent-buffer', str(SWAP_FILE))
    wait_for_headroom()
    state['limits_attempted'] = True
    save(directory/'state.json', state)
    # Restrict swap in the container before enabling host swap. memory-swap is RAM + swap.
    # Historical trial only: production persistence was accepted on 2026-10-30 through
    # trendradar_persist.py. This docker update alone still does not define recreation settings.
    command('docker', 'update', '--memory', str(MEMORY), '--memory-swap', str(TOTAL_MEMORY_SWAP), EXPECTED_CONTAINER)
    validate_limits(inspect_target(), MEMORY, TOTAL_MEMORY_SWAP)
    command('swapon', str(SWAP_FILE))
    if str(SWAP_FILE) not in swap_usage():
        raise RuntimeError('The new swap file did not become active')
    if sha(FSTAB) != state['fstab_before_sha']:
        raise RuntimeError('fstab changed during preparation; refusing to overwrite')
    replace_fstab(planned, FSTAB.stat())
    command('systemctl', 'daemon-reload')
    state['phase'] = 'applied'
    save(directory/'state.json', state)


def rollback_trial(directory, state):
    if not state.get('created_swap') and not state.get('limits_attempted'):
        return {'status': 'no_changes'}
    secure_swap_path()
    target = inspect_target()
    previous = state['previous']
    if (target['memory'], target['memory_swap']) not in ((MEMORY, TOTAL_MEMORY_SWAP), (previous['memory'], previous['memory_swap'])):
        raise RuntimeError('Container limits changed independently; refusing to overwrite')
    if sha(FSTAB) not in (state['fstab_before_sha'], state['fstab_planned_sha']):
        raise RuntimeError('fstab changed independently; rollback requires review')
    if sha(directory/'fstab.before') != state['fstab_before_sha']:
        raise RuntimeError('fstab backup checksum mismatch')
    if SWAP_FILE.exists() or SWAP_FILE.is_symlink():
        secure_swap_path(require_file=True)
        identity = SWAP_FILE.stat()
        if (identity.st_ino, identity.st_dev) != (state['swap_inode'], state['swap_device']):
            raise RuntimeError('Managed swap file was replaced; refusing to remove it')
    target = wait_for_headroom()
    active = swap_usage().get(str(SWAP_FILE))
    # Do not swap pages back into an already pressured host merely to remove the trial buffer.
    if active and meminfo()['MemAvailable'] < active['used_bytes']+128*MIB:
        raise RuntimeError('Insufficient RAM to safely disable swap; buffer retained for review')
    group = target['cgroup']
    if active and group['memory.current']+group['memory.swap.current'] > previous['memory']-16*MIB:
        raise RuntimeError('TrendRadar pages do not fit safely under its original limit; buffer retained for review')
    if active:
        command('swapoff', str(SWAP_FILE), timeout=90)
    command('docker', 'update', '--memory', str(previous['memory']), '--memory-swap', str(previous['memory_swap']), EXPECTED_CONTAINER)
    restored = inspect_target()
    if (restored['memory'], restored['memory_swap']) != (previous['memory'], previous['memory_swap']):
        raise RuntimeError('Original Docker limits were not restored')
    if sha(FSTAB) not in (state['fstab_before_sha'], state['fstab_planned_sha']):
        raise RuntimeError('fstab changed during rollback; retaining the file for review')
    replace_fstab((directory/'fstab.before').read_bytes(), FSTAB.stat())
    command('systemctl', 'daemon-reload')
    if SWAP_FILE.exists():
        secure_swap_path(require_file=True)
        SWAP_FILE.unlink()
    state['phase'] = 'rolled_back'
    save(directory/'state.json', state)
    return {'status': 'rolled_back', 'original_memory': previous['memory'], 'original_memory_swap': previous['memory_swap']}


def bounded_logs(start, container_id=EXPECTED_CONTAINER):
    import tempfile
    # Keep potentially verbose news output off the small host's Python heap and never export raw logs.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(['docker', 'logs', '--since', str(start), '--timestamps', '--tail', '3000', container_id],
                                stdout=output, stderr=subprocess.STDOUT, timeout=30)
        output.seek(0)
        data = output.read(2*MIB+1)
    return result.returncode, data[:2*MIB].decode('utf-8', errors='replace'), len(data) > 2*MIB


def log_evidence(start, container_id=None):
    code, text, truncated = bounded_logs(start) if container_id is None else bounded_logs(start, container_id)
    reports, errors = [], []
    for line in text.splitlines():
        timestamp, _, message = line.partition(' ')
        try:
            at = int(datetime.fromisoformat(timestamp.replace('Z', '+00:00')).timestamp())
        except ValueError:
            continue
        if message.startswith(('HTML报告已生成:', 'HTML报告已生成（Docker环境）:', '最新报告已更新:')):
            reports.append(at)
        if message.startswith(('分析流程执行出错:', '❌ 程序运行错误:', '❌ 配置文件错误:')) or 'msg="job failed' in message:
            errors.append(diagnostic.stamp(at))
    return {'exit': code, 'report_half_hour_slots': sorted({at//1800 for at in reports if at//1800 > start//1800}),
            'report_times': sorted({diagnostic.stamp(at) for at in reports}), 'error_times': sorted(set(errors)),
            'may_be_truncated': truncated or len(text.splitlines()) >= 3000}


def public_health():
    result = {}
    for path in ('/finevent/healthz', '/TrendRadar/content/'):
        response = command('curl', '--disable', '--noproxy', '*', '--silent', '--show-error', '--fail',
                           '--max-time', '10', '--output', '/dev/null', '--write-out', '%{http_code}',
                           '--resolve', 'www.example.com:443:127.0.0.1', 'https://www.example.com'+path, check=False)
        result[path] = {'exit': response.returncode, 'http_status': response.stdout.strip()}
    return result


def summarize(samples, baseline):
    return {'samples': len(samples),
            'host_available_min_mib': min(row['available'] for row in samples)/MIB,
            'trend_memory_peak_observed_mib': max(row['trend_memory'] for row in samples)/MIB,
            'trend_swap_peak_observed_mib': max(row['trend_swap'] for row in samples)/MIB,
            'host_swap_peak_observed_mib': max(row['host_swap_used'] for row in samples)/MIB,
            'trend_oom_kill_delta': max(row['trend_oom_kill'] for row in samples)-baseline['trend_oom_kill'],
            'finevent_oom_kill_delta': max(row['finevent_oom_kill'] for row in samples)-baseline['finevent_oom_kill'],
            'host_oom_kill_delta': max(row['host_oom_kill'] for row in samples)-baseline['host_oom_kill'],
            'samples_below_64_mib': sum(row['available'] < 64*MIB for row in samples),
            'baseline_counters': {key: baseline[key] for key in ('trend_oom_kill', 'finevent_oom_kill', 'host_oom_kill')},
            'lowest_host_samples': sorted(samples, key=lambda row: row['available'])[:5],
            'max_sample_interval_seconds': max((b['at']-a['at'] for a, b in zip(samples, samples[1:])), default=0)}


def sample(target, finevent_pid):
    group = diagnostic.cgroup_memory(target['pid'])
    finevent = diagnostic.cgroup_memory(finevent_pid)
    host = meminfo()
    vm = diagnostic.read_numbers(Path('/proc/vmstat'), {'oom_kill', 'pswpin', 'pswpout'})
    # Missing counters must stop the observation instead of being treated as zero.
    return {'at': int(time.time()), 'available': host['MemAvailable'],
            'host_swap_used': host['SwapTotal']-host['SwapFree'],
            'trend_memory': group['memory.current'], 'trend_swap': group['memory.swap.current'],
            'trend_oom_kill': group['memory.events']['oom_kill'],
            'finevent_oom_kill': finevent['memory.events']['oom_kill'], 'host_oom_kill': vm['oom_kill'],
            'swap_in_pages': vm['pswpin'], 'swap_out_pages': vm['pswpout']}


def process_identity(pid, proc=Path('/proc')):
    # starttime (field 22) disambiguates reused PIDs; comm may contain spaces or parentheses.
    fields = (proc/str(pid)/'stat').read_text().rsplit(')', 1)[1].split()
    return int(fields[19])


def validate_live_processes(target, finevent_pid, identities):
    if (process_identity(target['pid']), process_identity(finevent_pid)) != identities:
        raise RuntimeError('A monitored process restarted during observation')
    group = diagnostic.cgroup_memory(target['pid'])
    if (group.get('memory.max'), group.get('memory.swap.max')) != (MEMORY, TOTAL_MEMORY_SWAP-MEMORY):
        raise RuntimeError('Live cgroup hard limits changed during observation')


def observe(directory, report, sample_guard=None, on_failure=None, target_provider=None):
    provider = target_provider or inspect_target
    target = provider()
    validate_limits(target, MEMORY, TOTAL_MEMORY_SWAP)
    finevent_pid = int(command('systemctl', 'show', 'finevent.service', '-p', 'MainPID', '--value').stdout.strip())
    identities = (process_identity(target['pid']), process_identity(finevent_pid))
    start = int(time.time())
    # Include two normal half-hour schedule boundaries plus three minutes for the second job.
    end = (start//1800+2)*1800+180
    baseline = sample(target, finevent_pid)
    samples = [baseline]
    report.update(observation_started=diagnostic.stamp(start), planned_end=diagnostic.stamp(end))
    save(directory/'report.json', report)
    print('Observing two normal collection cycles until '+diagnostic.stamp(end)+'. Keep this terminal open.', flush=True)
    last_check = 0
    failure = None
    try:
        while time.time() < end:
            time.sleep(5)
            row = sample(target, finevent_pid); samples.append(row)
            if sample_guard is not None:
                failure = sample_guard(row)
                if failure:
                    break
            if any(row[key] > baseline[key] for key in ('trend_oom_kill', 'finevent_oom_kill', 'host_oom_kill')):
                failure = 'New OOM termination observed'; break
            if len(samples) >= 3 and all(item['available'] < 32*MIB for item in samples[-3:]):
                failure = 'Host available memory below 32 MiB for three consecutive samples'; break
            if time.time()-last_check >= 60:
                # Read kernel state here instead of launching Docker/systemctl clients during a peak.
                validate_live_processes(target, finevent_pid, identities)
                save(directory/'samples.json', samples)
                report['resources'] = summarize(samples, baseline)
                save(directory/'report.json', report)
                print(f"{diagnostic.stamp(row['at'])}: host available {row['available']/MIB:.1f} MiB; TrendRadar RAM {row['trend_memory']/MIB:.1f}, swap {row['trend_swap']/MIB:.1f} MiB", flush=True)
                last_check = time.time()
    finally:
        save(directory/'samples.json', samples)
        report['resources'] = summarize(samples, baseline)
    report['observation_issue'] = failure
    if failure is not None and on_failure is not None:
        on_failure()  # Release experimental throttling before collecting potentially slow diagnostics.
    report['collection'] = log_evidence(start) if target_provider is None else log_evidence(start, target['id'])
    report['health'] = public_health()
    report['container_after'] = provider()
    report['oom_evidence'] = diagnostic.oom_evidence(start, int(time.time()))
    acceptable = (failure is None and report['resources']['host_available_min_mib'] >= 64
                  and len(report['collection']['report_half_hour_slots']) >= 2
                  and not report['collection']['error_times'] and report['collection']['exit'] == 0
                  and not report['collection']['may_be_truncated']
                  and all(value == {'exit': 0, 'http_status': '200'} for value in report['health'].values()))
    report['status'] = 'completed_for_review' if acceptable else 'needs_review'
    report['observation_issue'] = failure
    report['configuration'] = 'trial retained; use the recorded rollback command after review'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'memory-recovery-\d{8}T\d{6}Z-[a-f0-9]{8}', args.run_id) or os.geteuid() != 0:
        raise RuntimeError('Root and a valid run ID are required')
    import fcntl
    os.umask(0o077)
    signal.signal(signal.SIGHUP, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with Path('/run/lock/finevent-memory-recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directory = BACKUPS/args.run_id
        if args.rollback:
            state = json.loads((directory/'state.json').read_text())
            report = json.loads((directory/'report.json').read_text())
            report['rollback'] = rollback_trial(directory, state)
            report['status'] = 'rolled_back'
            save(directory/'report.json', report)
            print('Original settings restored; evidence: '+str(directory))
            return
        directory.mkdir(mode=0o700)
        # Keep the exact implementation with the backup for a later reviewed rollback.
        for name in ('memory_recovery.py', 'memory_diagnostic.py'):
            shutil.copy2(Path(__file__).parent/name, directory/name)
        state = {'id': args.run_id, 'phase': 'preflight'}
        report = {'id': args.run_id, 'created_at': diagnostic.stamp(time.time()), 'status': 'preflight',
                  'rollback_command': f'python3 {directory}/memory_recovery.py --run-id {args.run_id} --rollback'}
        save(directory/'report.json', report)
        save(directory/'state.json', state)
        try:
            print('Preparing 1 GiB host buffer and TrendRadar 192 MiB RAM + 256 MiB swap trial...', flush=True)
            apply_trial(directory, state)
            report['status'] = 'observing'
            report['applied_at'] = diagnostic.stamp(time.time())
            save(directory/'report.json', report)
            observe(directory, report)
        except BaseException as error:
            report['status'] = 'incomplete'
            report['error_type'] = type(error).__name__
            report['error'] = str(error) if isinstance(error, RuntimeError) else 'Operation interrupted; inspect saved state before retrying.'
            if state['phase'] != 'applied':
                try:
                    report['rollback'] = rollback_trial(directory, state)
                except BaseException as recovery_error:
                    report['rollback'] = {'status': 'needs_review', 'error_type': type(recovery_error).__name__,
                                          'error': str(recovery_error) if isinstance(recovery_error, RuntimeError) else 'Could not finish rollback'}
            else:
                report['configuration'] = 'trial retained after observation interruption; do not rerun apply'
            raise
        finally:
            report['finished_at'] = diagnostic.stamp(time.time())
            save(directory/'report.json', report)
            print('Trial status: '+report['status'], flush=True)
            print('Evidence: '+str(directory/'report.json'), flush=True)
            print('Rollback after review: '+report['rollback_command'], flush=True)


if __name__ == '__main__':
    main()
