"""Renew evidence by observing the unchanged installation; never apply persistence again."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import time

import calendar_release as calendar

ID = re.compile(r'persistence-check-\d{8}T\d{6}Z-[a-f0-9]{8}')
OBSERVATION_SECONDS = 65*60
SCOPE = 'new_observation_window_and_after; earlier_retention_gap_not_reconstructed'


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
    calendar.require(report.get('id') == run_id and ID.fullmatch(run_id)
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
                     and report['first_sample_at'] <= start+15 and end-report['last_sample_at'] <= 15,
                     'Baseline collection, continuous resources or HTTPS checks failed')


def load(run_id, checksum):
    calendar.require(ID.fullmatch(run_id) and re.fullmatch(r'[a-f0-9]{64}', checksum or ''), 'Invalid baseline identity')
    path = calendar.BACKUPS/run_id/'report.json'
    calendar.require(calendar.recovery.sha(path) == checksum, 'Baseline report changed since review')
    report = json.loads(path.read_text())
    _, basis = original()
    validate(report, run_id, time.time(), basis)
    return report


def run(directory, report):
    calendar.require(report['status'] == 'queued', 'Baseline already started; do not restart it')
    import fcntl
    try:
        # Same locks as deployments; no service changes, collection triggers or paid requests.
        with calendar.LOCK.with_name(calendar.LOCK.name+'.transition').open('a') as transition, calendar.LOCK.open('a') as lock:
            fcntl.flock(transition, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            accepted, basis = original()
            current = calendar.inspect_persistence(accepted)
            identities = [calendar.recovery.process_identity(current['container']['pid']),
                          calendar.recovery.process_identity(current['finevent_pid'])]
            report.update(status='observing', stage='readonly_baseline', basis=basis, initial=current,
                          process_identities=identities, observation_started=calendar.stamp(), history_scope=SCOPE)
            start = time.time()
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
                    calendar.check_sample(row, current['baseline'])
                    calendar.require(report['resources']['max_sample_interval_seconds'] <= 15, 'Resource sampling gap exceeds 15 seconds')
                    if time.monotonic()-exported >= 60:
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
            validate(report, report['id'], time.time(), basis)
    except BaseException as error:
        report.update(status='incomplete', failure_type=type(error).__name__, finished_at=calendar.stamp(),
                      failure=str(error)[:600] if isinstance(error, RuntimeError) else 'Read-only baseline failed; inspect private evidence')
        calendar.recovery.save(directory/'report.json', report)
        raise
    calendar.recovery.save(directory/'report.json', report)


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
