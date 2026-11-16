"""Renew evidence by observing the unchanged installation; never apply persistence again."""

import argparse
from concurrent.futures import Future
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
from threading import Thread
import time

import calendar_release as calendar

ID = re.compile(r'persistence-check-\d{8}T\d{6}Z-[a-f0-9]{8}')
OBSERVATION_SECONDS = 65*60
SCOPE = 'new_observation_window_and_after; earlier_retention_gap_not_reconstructed'


class WindowComparison:
    """Measure a possible earlier cutoff without participating in the release decision."""

    def __init__(self, directory, report, start):
        self.directory = directory
        self.report = report
        self.start = start
        self.data = report['window_comparison'] = {
            'business_ready_at': None, 'baseline_ready_at': None, 'candidate_at': None,
            'probes_completed': 0, 'probe_errors': 0, 'tail': None,
        }
        self.first_sample = None
        self.pending = None
        self.business_ready = False

    def checkpoint(self, business_check):
        if self.data['candidate_at'] is not None:
            return
        # Reads of the settings lock, SQLite and HTTPS must not stall five-second resource sampling.
        try:
            self.business_ready = business_check()
            now = time.time()
            if self.business_ready and self.data['business_ready_at'] is None:
                self.data['business_ready_at'] = now
            if self.pending is not None or (self.data['baseline_ready_at'] and not self.business_ready):
                return
            if now < (math.ceil(self.start / 1800) + 1) * 1800:
                return
            future = Future()
            Thread(target=self.probe, args=(future, self.start, now), daemon=True).start()
            self.pending = (future, now, self.business_ready)
        except Exception as error:
            self.business_ready = False
            self.error(error)

    @staticmethod
    def probe(future, start, end):
        try:
            trend = calendar.trend_outputs(start, int(end), post_start_cycles=True)
            ready = (trend['status'] == 'passed' and trend['distinct_cycles'] >= 2
                     and trend['html_mtime'] <= trend['last_import'] <= end)
            if ready:
                health = calendar.recovery.public_health()
                ready = calendar.health_passed(health)
            future.set_result(ready)
        except Exception as error:
            # Store only the exception type in private diagnostics.
            future.set_exception(error)

    def error(self, error):
        self.data['probe_errors'] += 1
        self.data['last_probe_error'] = type(error).__name__

    def sample(self, row, baseline):
        if self.first_sample is None:
            self.first_sample = row['at']
        tail = self.data['tail']
        memory = row['finevent_current'] / calendar.MIB
        if tail is not None:
            tail['samples'] += 1
            tail['last_sample_at'] = row['at']
            tail['finevent_last_mib'] = memory
            tail['finevent_peak_mib'] = max(tail['finevent_peak_mib'], memory)
            tail['host_available_min_mib'] = min(tail['host_available_min_mib'], row['available'] / calendar.MIB)
        try:
            calendar.check_sample(row, baseline)
        except RuntimeError as error:
            if tail is not None:
                self.issue(row, str(error))
            raise
        metrics = self.report['resources']
        if tail is not None and metrics['max_sample_interval_seconds'] > 15:
            self.issue(row, 'Resource sampling gap exceeds 15 seconds')
        if self.pending is None or not self.pending[0].done():
            return
        future, requested, business_at_probe = self.pending
        self.pending = None
        try:
            ready = future.result()
            self.data['probes_completed'] += 1
        except Exception as error:
            self.error(error)
            return
        now = time.time()
        if not (ready and 0 <= now-requested <= 60 and -1 < self.first_sample-self.start <= 15
                and 0 <= now-row['at'] <= 15 and calendar.resource_checks_passed(self.report)):
            return
        if self.data['baseline_ready_at'] is None:
            self.data['baseline_ready_at'] = now
        if business_at_probe and self.business_ready:
            self.data['candidate_at'] = now
            self.data['tail'] = {
                'samples': 0, 'last_sample_at': None, 'first_resource_issue': None,
                'finevent_at_candidate_mib': memory, 'finevent_last_mib': memory, 'finevent_peak_mib': memory,
                'host_available_min_mib': row['available'] / calendar.MIB,
            }

    def issue(self, row, reason):
        tail = self.data['tail']
        if tail['first_resource_issue'] is None:
            tail['first_resource_issue'] = {'at': row['at'], 'reason': reason}
            calendar.recovery.save(self.directory/'report.json', self.report)


class InsufficientWindow(RuntimeError):
    """Observation finished, but its evidence is insufficient for future deployment."""


def original():
    directory = calendar.BACKUPS/calendar.PERSIST_ID
    report = json.loads((directory/'report.json').read_text())
    # Verify historical acceptance at its own timestamp, not as fresh evidence today.
    calendar.validate_report(report, datetime.fromisoformat(report['finished_at']).timestamp())
    return report, {'id': calendar.PERSIST_ID, 'report_sha256': calendar.recovery.sha(directory/'report.json'),
                    'state_sha256': calendar.recovery.sha(directory/'state.json')}


def validate_current(report, current):
    before = report['initial']
    calendar.require(all(current[key] == before[key] for key in ('previous_release', 'config_sha256', 'finevent_pid')),
                     'Application identity changed after baseline observation')
    calendar.validate_target(current['container'], before['container'], calendar.parent_metrics())
    identities = [calendar.recovery.process_identity(current['container']['pid']),
                  calendar.recovery.process_identity(current['finevent_pid'])]
    calendar.require(identities == report['process_identities'], 'Observed process identity changed')


def validate(report, run_id, now, basis):
    calendar.require(report.get('id') == run_id and (ID.fullmatch(run_id) or calendar.ID_PATTERN.fullmatch(run_id))
                     and report.get('status') == 'completed_for_review' and report.get('stage') == 'completed'
                     and not report.get('failure') and not report.get('rollback') and report.get('basis') == basis
                     and report.get('history_scope') == SCOPE, 'Baseline evidence is incomplete or has a different origin')
    start = datetime.fromisoformat(report['observation_started']).timestamp()
    end = datetime.fromisoformat(report['observation_ended']).timestamp()
    finished = datetime.fromisoformat(report['finished_at']).timestamp()
    calendar.require(0 <= now-end <= 7*86400 and end-start >= OBSERVATION_SECONDS and 0 <= finished-end <= 180 and now >= finished,
                     'Baseline observation is short, expired or has an invalid timestamp')
    metrics = report['resources']
    calendar.require(calendar.runtime_passed(report) and report['trendradar'].get('distinct_cycles', 0) >= 2
                     and metrics['samples'] >= OBSERVATION_SECONDS//15
                     and -1 < report['first_sample_at']-start <= 15 and 0 <= end-report['last_sample_at'] <= 15
                     and report['first_sample_at'] <= report['last_sample_at'],
                     'Baseline collection, continuous resources or HTTPS checks failed')


def load(run_id, checksum):
    calendar.require(ID.fullmatch(run_id) and re.fullmatch(r'[a-f0-9]{64}', checksum or ''), 'Invalid baseline identity')
    path = calendar.BACKUPS/run_id/'report.json'
    calendar.require(calendar.recovery.sha(path) == checksum, 'Baseline report changed since review')
    report = json.loads(path.read_text())
    _, basis = original()
    validate(report, run_id, time.time(), basis)
    return report


def collect(directory, report):
    """Observe under the caller's deployment locks, without changing either service."""
    accepted, basis = original()
    current = calendar.inspect_persistence(accepted)
    identities = [calendar.recovery.process_identity(current['container']['pid']),
                  calendar.recovery.process_identity(current['finevent_pid'])]
    report.update(status='observing', stage='readonly_baseline', basis=basis, initial=current,
                  process_identities=identities, observation_started=calendar.stamp(), history_scope=SCOPE)
    start = time.time()
    comparison = WindowComparison(directory, report, start)
    deadline = time.monotonic()+OBSERVATION_SECONDS
    report['planned_end'] = datetime.fromtimestamp(start+OBSERVATION_SECONDS, calendar.ZONE).isoformat()
    calendar.recovery.save(directory/'report.json', report)
    samples, exported = [], 0
    with (directory/'samples.jsonl').open('x', encoding='utf-8') as output:
        while True:
            calendar.recovery.validate_live_processes(current['container'], current['finevent_pid'], tuple(identities))
            row = calendar.sample(current['container'], current['finevent_pid'])
            samples.append(row)
            output.write(json.dumps(row)+'\n')
            output.flush()
            report['resources'] = calendar.resource_summary(samples, current['baseline'])
            comparison.sample(row, current['baseline'])
            calendar.require(report['resources']['max_sample_interval_seconds'] <= 15, 'Resource sampling gap exceeds 15 seconds')
            if time.monotonic()-exported >= 60:
                comparison.checkpoint(lambda: True)
                calendar.recovery.save(directory/'report.json', report)
                exported = time.monotonic()
            if time.monotonic() >= deadline:
                break
            time.sleep(5)
    report['observation_ended'] = calendar.stamp()
    validate_current(report, calendar.inspect_persistence(accepted))
    _, final_basis = original()
    calendar.require(final_basis == basis, 'Original baseline evidence changed during observation')
    report.update(first_sample_at=samples[0]['at'], last_sample_at=samples[-1]['at'])
    report['trendradar'] = calendar.trend_outputs(start, int(time.time()))
    report['health'] = calendar.recovery.public_health()
    report.update(status='completed_for_review', stage='completed', finished_at=calendar.stamp())
    try:
        validate(report, report['id'], time.time(), basis)
    except RuntimeError as error:
        report.update(status='incomplete', failure=str(error))
        calendar.recovery.save(directory/'report.json', report)
        raise InsufficientWindow(str(error)) from error
    calendar.recovery.save(directory/'report.json', report)


def release_window(report, container, pid, identities):
    """Capture the actual post-restart identities before either acceptance loop samples."""
    _, basis = original()
    initial = {'previous_release': str((calendar.ROOT/'current').resolve()), 'finevent_pid': pid,
               'container': container, 'config_sha256': calendar.recovery.sha(calendar.DATA/'config.json')}
    calendar.require(initial['previous_release'] == report['deployment']['release']
                     and initial['config_sha256'] == report['preflight']['config_sha256'],
                     'Release or configuration changed before acceptance observation')
    return {'id': report['id'], 'basis': basis, 'initial': initial,
            'process_identities': list(identities), 'history_scope': SCOPE}


def save_release_window(directory, report, window, samples, ended):
    if report['status'] not in ('completed_for_review', 'completed_with_source_limits_for_review'):
        return
    window.update(status='completed_for_review', stage='completed', observation_started=report['observation_started'],
                  observation_ended=ended, finished_at=report['finished_at'], first_sample_at=samples[0]['at'],
                  last_sample_at=samples[-1]['at'], resources=report['resources'], health=report['health'],
                  trendradar=report['trendradar'])
    try:
        validate(window, report['id'], time.time(), window['basis'])
    except RuntimeError as error:
        # A successful business acceptance can have insufficient evidence for future reuse.
        report['baseline_unavailable'] = str(error)
        return
    publish(directory, report, window)


def publish(directory, report, window):
    path = directory/'next-baseline.json'
    calendar.recovery.save(path, window)
    report['next_baseline'] = {'report_sha256': calendar.recovery.sha(path),
                               'observation_ended': window['observation_ended']}


def reviewed_window(review):
    """Only consider the exact predecessor whose acceptance just passed under the lock."""
    if not review or review.get('interrupted_for_repair'):
        return None
    directory = calendar.BACKUPS/review['id']
    path = directory/'report.json'
    raw = path.read_bytes()
    calendar.require(hashlib.sha256(raw).hexdigest() == review['report_sha256'], 'Reviewed predecessor report changed')
    previous = json.loads(raw)
    if previous.get('status') not in ('completed_for_review', 'completed_with_source_limits_for_review'):
        return None
    receipt = previous.get('next_baseline')
    path = directory/'next-baseline.json'
    if not receipt or not path.is_file():
        return None
    raw = path.read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    if checksum != receipt.get('report_sha256'):
        return None
    return json.loads(raw), checksum


def observe_for_operation(directory, operation, folder, reason):
    evidence = directory/folder
    evidence.mkdir(mode=0o700)
    planned = datetime.fromtimestamp(time.time()+OBSERVATION_SECONDS, calendar.ZONE).isoformat()
    operation['planned_end'] = planned
    operation[folder] = {'reason': reason, 'planned_end': planned}
    calendar.recovery.save(directory/'report.json', operation)
    window = {'id': operation['id']}
    try:
        collect(evidence, window)
    finally:
        if 'window_comparison' in window:
            operation[folder]['observation_started'] = window['observation_started']
            operation[folder]['window_comparison'] = window['window_comparison']
    return window, calendar.recovery.sha(evidence/'report.json')


def for_deployment(directory, operation, accepted, current):
    _, basis = original()
    review = operation.get('previous_review')
    candidate = reviewed_window(review)
    counters = accepted['resources']['baseline_counters']
    reason = 'No reusable acceptance window for the reviewed release'
    if candidate:
        window, checksum = candidate
        try:
            validate(window, review['id'], time.time(), basis)
            validate_current(window, current)
        except (RuntimeError, KeyError, ValueError, TypeError) as error:
            reason = str(error)
        else:
            end = datetime.fromisoformat(window['observation_ended']).timestamp()
            try:
                history = calendar.resource_history(end, counters)
            except calendar.HistoryGap as error:
                reason = str(error)
            else:
                receipt = {'id': review['id'], 'report_sha256': checksum, 'source': 'release_acceptance'}
                return current, receipt, history
    window, checksum = observe_for_operation(directory, operation, 'preflight_baseline', reason)
    current = calendar.inspect_persistence(accepted)
    validate_current(window, current)
    end = datetime.fromisoformat(window['observation_ended']).timestamp()
    receipt = {'id': operation['id'], 'report_sha256': checksum, 'source': 'preflight_observation'}
    return current, receipt, calendar.resource_history(end, counters)


def run(directory, report):
    calendar.require(report['status'] == 'queued', 'Baseline already started; do not restart it')
    import fcntl
    try:
        # Same locks as deployments; no service changes, collection triggers or paid requests.
        with calendar.LOCK.with_name(calendar.LOCK.name+'.transition').open('a') as transition, calendar.LOCK.open('a') as lock:
            fcntl.flock(transition, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            collect(directory, report)
    except BaseException as error:
        report.update(status='incomplete', failure_type=type(error).__name__, finished_at=calendar.stamp(),
                      failure=str(error)[:600] if isinstance(error, RuntimeError) else 'Read-only baseline failed; inspect private evidence')
        calendar.recovery.save(directory/'report.json', report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--launch', action='store_true')
    modes.add_argument('--run', action='store_true')
    args = parser.parse_args()
    calendar.require(os.geteuid() == 0 and ID.fullmatch(args.run_id), 'Root and a valid baseline ID are required')
    os.umask(0o077)
    directory = calendar.BACKUPS/args.run_id
    if args.launch:
        calendar.installer.verify_bundle(Path(__file__).resolve().parent.parent)
        directory.mkdir(mode=0o700)
        report = {'id': args.run_id, 'status': 'queued', 'stage': 'queued', 'created_at': calendar.stamp(),
                  'unit': 'finevent-'+args.run_id+'.service', 'verification_profile': 'readonly_baseline'}
        calendar.recovery.save(directory/'report.json', report)
        calendar.recovery.command('systemd-run', '--unit='+report['unit'], '--property=Type=exec', '--property=MemoryMax=64M',
                                  '--property=MemorySwapMax=0', '--property=Nice=10', '--property=RuntimeMaxSec=4800',
                                  '/usr/bin/python3', '-B', str(Path(__file__).resolve()), '--run-id', args.run_id, '--run')
    else:
        run(directory, json.loads((directory/'report.json').read_text()))


if __name__ == '__main__':
    main()
