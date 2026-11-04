"""Positive admission after a reviewed remediation; never erase historical failures."""

from datetime import datetime
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import time

import memory_recovery as recovery

MIB = recovery.MIB
COUNTERS = ('trend_oom_kill', 'finevent_oom_kill', 'host_oom_kill')
TRIAL_ID = re.compile(r'trendradar-lazy-\d{8}T\d{6}Z-[a-f0-9]{8}')
VERIFICATION_ID = re.compile(r'verification-\d{8}T\d{6}Z-[a-f0-9]{8}')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load_trial(directory):
    report = json.loads((directory/'report.json').read_text())
    state = json.loads((directory/'state.json').read_text())
    require(report.get('id') == directory.name and report.get('status') == 'completed_for_review'
            and report.get('stage') == 'completed' and not report.get('observation_issue')
            and not report.get('rollback') and state.get('phase') == 'applied',
            'The named remediation is not a completed, retained trial')
    metrics = report['resources']
    require(metrics['samples'] > 0 and metrics['max_sample_interval_seconds'] <= 15
            and metrics['host_available_min_mib'] >= 64 and metrics['samples_below_64_mib'] == 0
            and all(metrics[k+'_delta'] == 0 for k in COUNTERS),
            'The remediation resource evidence does not pass the existing limits')
    collection, outputs = report['collection'], report['outputs']
    require(collection['exit'] == 0 and not collection['may_be_truncated']
            and not collection['error_times'] and len(set(collection['report_half_hour_slots'])) >= 2
            and len(outputs['records']) >= 2 and all(r['items'] > 0 for r in outputs['records'])
            and outputs['source_statuses'] and all(s['status'] == 'success' for s in outputs['source_statuses'])
            and outputs['source_failures'] == 0 and outputs['finevent_poll_count'] > 0
            and outputs['finevent_poll_failures'] == 0,
            'The remediation lacks successful production collection and import evidence')
    require(all(report['health'][path] == {'exit': 0, 'http_status': '200'}
                for path in ('/finevent/healthz', '/TrendRadar/content/')),
            'The remediation HTTPS checks did not pass')
    after = report['container_after']
    require(state['container_id'] == after['id'] and state['container_pid'] == after['pid']
            and state['candidate_sha256'] == report['candidate_sha256'],
            'Remediation state and report disagree')
    return report


def live_check(trial, minimum_mib=128):
    target = recovery.inspect_target()
    after = trial['container_after']
    require(all(target[k] == after[k] for k in ('id', 'pid', 'restarts')),
            'TrendRadar restarted or changed after the reviewed trial')
    recovery.validate_limits(target, recovery.MEMORY, recovery.TOTAL_MEMORY_SWAP)
    require(target['cgroup']['memory.high'] == 160*MIB
            and Path('/proc/sys/vm/swappiness').read_text().strip() == '60',
            'The reviewed reclaim policy changed')
    image = recovery.command('docker', 'inspect', '--format', '{{.Image}}', 'trend-radar').stdout.strip()
    source = Path(f"/proc/{target['pid']}/root/app/trendradar/ai/client.py")
    require(image == trial['image_id'] and recovery.sha(source) == trial['candidate_sha256'],
            'The reviewed TrendRadar image or lazy import patch changed')
    finevent_pid = int(recovery.command('systemctl', 'show', 'finevent.service', '-p', 'MainPID', '--value').stdout.strip())
    require(finevent_pid > 0, 'finevent is not running')
    row = recovery.sample(target, finevent_pid)
    require(all(row[k] == trial['resources']['baseline_counters'][k] for k in COUNTERS),
            'OOM counters increased or reset after the reviewed trial')
    require(row['available'] >= minimum_mib*MIB, 'Insufficient current host memory headroom')
    return target, finevent_pid, row


def continuation_start(previous, trial_id, current_release, current_started):
    require(previous.get('status') == 'completed_for_review'
            and previous.get('resource_admission', {}).get('status') == 'passed'
            and previous['resource_admission']['trial_id'] == trial_id
            and previous.get('ai', {}).get('status') == 'succeeded'
            and previous.get('rollback', {}).get('status') == 'passed',
            'The preceding verification has not passed AI, resource admission and rollback checks')
    require(str(current_release.resolve()) == previous['rollback']['final_release']
            and current_started == previous['system_after']['finevent']['ExecMainStartTimestamp'],
            'finevent changed or restarted after the preceding verified deployment')
    require(all(previous['resources_after'][k] == previous['resource_admission']['live'][k] for k in COUNTERS),
            'The preceding verification changed OOM counters')
    return int(datetime.fromisoformat(previous['finished_at']).timestamp())


def admit(trial_id, database, report, readonly, resource_stats, backups, after_verification=None, root=Path('/srv/example/finevent')):
    require(TRIAL_ID.fullmatch(trial_id), 'Invalid resource trial ID')
    directory = backups/trial_id
    trial = load_trial(directory)
    start = int(datetime.fromisoformat(trial['observation_started']).timestamp())
    finish = int(datetime.fromisoformat(trial['finished_at']).timestamp())
    end = int(time.time())
    require(start < finish <= end and end-start <= 7*86400,
            'The remediation observation window is invalid or older than seven days')
    if after_verification:
        require(VERIFICATION_ID.fullmatch(after_verification), 'Invalid preceding verification ID')
        previous_path = backups/after_verification/'report.json'
        previous = json.loads(previous_path.read_text())
        require(previous.get('id') == after_verification, 'Preceding verification ID mismatch')
        current_started = recovery.command('systemctl', 'show', 'finevent.service', '-p', 'ExecMainStartTimestamp', '--value').stdout.strip()
        next_start = continuation_start(previous, trial_id, root/'current', current_started)
        require(finish <= next_start < end, 'Invalid continuation observation window')
        start = next_start
        report.update(after_verification=after_verification,
                      preceding_report_sha256=hashlib.sha256(previous_path.read_bytes()).hexdigest())
    with closing(readonly(database)) as conn:
        rows = [dict(r) for r in conn.execute('SELECT * FROM resources WHERE at BETWEEN ? AND ? ORDER BY at', (start, end))]
    metrics = resource_stats(rows, start, end)
    report.update(trial_id=trial_id, trial_report_sha256=hashlib.sha256((directory/'report.json').read_bytes()).hexdigest(),
                  from_at=recovery.diagnostic.stamp(start), through_at=end, resources=metrics, status='checking')
    require(metrics['coverage']['observations'] > 0 and metrics['coverage']['intervals_over_threshold'] == 0
            and metrics['missing_linux_metrics'] == 0 and not metrics['counter_reset_candidates']
            and metrics['host_available_min_mib'] >= 64
            and all(r['oom_kills'] == trial['resources']['baseline_counters']['finevent_oom_kill'] for r in rows),
            'Post-remediation resource history is incomplete or shows new pressure; review the saved evidence')
    _, _, report['live'] = live_check(trial)
    report['status'] = 'passed'
    return trial


def trend_jobs(group, proc=Path('/proc'), cgroups=Path('/sys/fs/cgroup')):
    # Inspect only this container. Export identities, never argv or environment values.
    path = (cgroups/group.lstrip('/')).resolve()
    require(path.is_relative_to(cgroups.resolve()), 'Invalid container cgroup path')
    identities = []
    for value in (path/'cgroup.procs').read_text().split():
        pid = int(value)
        try:
            fields = (proc/value/'cmdline').read_bytes().split(b'\0')
            if any(fields[i:i+2] == [b'-m', b'trendradar'] for i in range(1, len(fields)-1)):
                identities.append({'pid': pid, 'start_ticks': recovery.process_identity(pid, proc)})
        except FileNotFoundError:
            continue  # A short-lived process may finish between the two reads.
    return identities


class LiveProbe:
    def __init__(self, trial, evidence):
        self.target, self.pid, self.baseline = live_check(trial)
        self.identities = (recovery.process_identity(self.target['pid']), recovery.process_identity(self.pid))
        self.evidence = evidence
        evidence.update(status='observing', samples=[], baseline=self.baseline)

    def sample(self):
        began_ms = time.time_ns()//1_000_000
        recovery.validate_live_processes(self.target, self.pid, self.identities)
        row = recovery.sample(self.target, self.pid)
        row['at_precise'] = time.time()
        row['trend_jobs'] = trend_jobs(self.target['cgroup']['path'])
        row['sample_started_ms'] = began_ms
        row['sample_completed_ms'] = time.time_ns()//1_000_000
        self.evidence['samples'].append(row)
        if row['available'] < 64*MIB or any(row[k] != self.baseline[k] for k in COUNTERS):
            self.evidence['status'] = 'pressure_requires_review'
            raise RuntimeError('New memory pressure during the bounded AI probe; no automatic retry')
        return row

    def wait_for_next_job(self):
        group = self.target['cgroup']['path']
        initial = trend_jobs(group)
        deadline = time.monotonic()+32*60
        last_sample, last_message = 0, 0
        print('Waiting up to 32 minutes for a NEW normal TrendRadar collection; no AI request sent yet.', flush=True)
        while time.monotonic() < deadline:
            if time.monotonic()-last_sample >= 5:
                self.sample()
                last_sample = time.monotonic()
            new_jobs = [p for p in trend_jobs(group) if p not in initial]
            if new_jobs:
                row = self.sample()
                require(row['available'] >= 128*MIB, 'Insufficient headroom at the actual collection trigger')
                require(any(p in row['trend_jobs'] for p in new_jobs), 'The detected job ended before the trigger; no request sent')
                self.evidence.update(triggered_jobs=new_jobs, triggered_at_ms=row['sample_completed_ms'])
                return
            if time.monotonic()-last_message >= 60:
                print('Still waiting for the next normal TrendRadar process; no paid request sent.', flush=True)
                last_message = time.monotonic()
            time.sleep(0.25)
        raise RuntimeError('No new TrendRadar job within 32 minutes; no paid request sent')

    def follow_triggered_job(self):
        deadline = time.monotonic()+120
        while time.monotonic() < deadline:
            row = self.sample()
            if not any(p in row['trend_jobs'] for p in self.evidence['triggered_jobs']):
                self.evidence['collection'] = recovery.log_evidence(self.evidence['triggered_at_ms']//1000)
                self.evidence['health'] = recovery.public_health()
                return
            time.sleep(0.1)
        raise RuntimeError('TrendRadar job still active after the bounded follow-up; review before retrying')

    def finish(self, ai):
        rows = self.evidence['samples']
        self.evidence['host_available_min_mib'] = min(r['available'] for r in rows)/MIB
        self.evidence['trend_memory_peak_observed_mib'] = max(r['trend_memory'] for r in rows)/MIB
        timing = ai.get('timing')
        if timing:
            begin, end = timing['model_started_ms'], timing['model_completed_ms']
            overlapping = [r for r in rows if begin > 0 and begin <= r['sample_started_ms'] <= r['sample_completed_ms'] < end and r['trend_jobs']]
            simultaneous = [r for r in overlapping if timing['collector_success'] and 0 < timing['collector_started_ms'] <= r['sample_started_ms'] and r['sample_completed_ms'] < timing['collector_completed_ms']]
            self.evidence['timing_precision'] = 'milliseconds'
        else:
            # Legacy ledger timestamps have second precision; never infer subsecond overlap.
            overlapping = [r for r in rows if ai['started']+1 <= r['at_precise'] < ai['completed'] and r['trend_jobs']]
            simultaneous = [r for r in overlapping if any(p['success'] and p['started']+1 <= r['at_precise'] < p['completed'] for p in ai['overlapping_polls'])]
            self.evidence['timing_precision'] = 'legacy_seconds'
        self.evidence['trend_job_overlap_samples'] = len(overlapping)
        self.evidence['status'] = 'observed'
        self.evidence['three_way_overlap_samples'] = len(simultaneous)
        self.evidence['three_way_overlap'] = 'observed' if simultaneous else 'not_established'
        # Even aligned requests need actual overlapping observations; no automatic paid retries.
