"""Deploy the calendar release against the accepted persistent TrendRadar baseline.

Run through publish-calendar.ps1. Deployment/observation survive an SSH disconnect.
Only the explicit AI profile accepts a hidden key and one paid business request.
Never change TrendRadar or create synthetic business records.
"""

import argparse
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from zoneinfo import ZoneInfo

import install as installer
import memory_recovery as recovery
import calendar_review

MIB = 1024**2
ROOT = Path('/srv/example/finevent')
DATA = Path('/srv/example/finevent-data')
BACKUPS = Path('/srv/example/finevent-backups')
TREND = Path('/srv/example/trendradar/output')
PERSIST_ID = 'trendradar-persist-20261030T083232Z-9cf5305a'
CONTAINER = 'ceb3b0967e820890eff6f3c149fb010d8201f6f71b291757515bb57cabc6f134'
IMAGE = 'sha256:da2e26b6811b714180f1c2ee72fc0f4ff76ca8ea676847fc23c80bb35e02f536'
PATCH_SHA = 'c405f46e99559221b8e082f8165a96c7b08122c9ab66656ac9aa33feefc39b45'
PATCH = Path('/srv/example/finevent-host/trendradar/client.py')
PARENT = Path('/sys/fs/cgroup/trendradar.slice')
LOCK = Path('/run/lock/finevent-memory-recovery.lock')
ID_PATTERN = re.compile(r'calendar-release-\d{8}T\d{6}Z-[a-f0-9]{8}')
SOURCE_IDS = {'nbs', 'fomc', 'bls', 'bea', 'nyfed'}
OBSERVATION_SECONDS = 6*3600+15*60
# NBS index/two annual pages, FOMC/BLS/BEA and NYFed current/next month: eight bounded requests.
INITIAL_SYNC_SECONDS = 10*60
ZONE = ZoneInfo('Asia/Shanghai')

def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def stamp():
    return datetime.now(ZONE).isoformat()


def readonly(path):
    conn = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    return conn


def snapshot(source, destination):
    with closing(readonly(source)) as original, closing(sqlite3.connect(destination)) as copy:
        original.backup(copy)
        require(copy.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Database snapshot integrity check failed')
    destination.chmod(0o600)
    return recovery.sha(destination)


def target():
    # Deliberately exclude environment, command arguments and notification credentials.
    template = ('{"id":{{json .Id}},"pid":{{.State.Pid}},"running":{{.State.Running}},'
                '"image":{{json .Image}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
                '"memory":{{.HostConfig.Memory}},"memory_swap":{{.HostConfig.MemorySwap}},'
                '"restarts":{{.RestartCount}},"restart":{{json .HostConfig.RestartPolicy.Name}},'
                '"parent":{{json .HostConfig.CgroupParent}}}')
    result = json.loads(recovery.command('docker', 'inspect', '--format', template, 'trend-radar').stdout)
    require(result['running'] and result['pid'] > 0, 'TrendRadar is not running')
    result['cgroup'] = recovery.diagnostic.cgroup_memory(result['pid'])
    return result


def parent_metrics():
    result = {key: (PARENT/key).read_text().strip() for key in ('memory.high', 'memory.max', 'memory.swap.max')}
    result['oom_kill'] = recovery.diagnostic.read_numbers(PARENT/'memory.events', {'oom_kill'})['oom_kill']
    return result


def validate_target(current, accepted, parent):
    require(current['id'] == CONTAINER == accepted['id'] and current['image'] == IMAGE,
            'TrendRadar container/image differs from the accepted persistence report')
    require(all(current[key] == accepted[key] for key in ('pid', 'restarts')),
            'TrendRadar restarted after the accepted report; review current evidence first')
    require(current['project'] == 'finevent-trendradar-managed' and current['restart'] == 'unless-stopped'
            and current['parent'] == 'trendradar.slice', 'Persistent container settings changed')
    recovery.validate_limits(current, 192*MIB, 448*MIB)
    require(current['cgroup']['path'].startswith('/trendradar.slice/')
            and current['cgroup']['memory.high'] == 'max', 'Unexpected child cgroup placement or reclaim policy')
    require(all(parent[key] == str(value*MIB) for key, value in
                (('memory.high', 160), ('memory.max', 192), ('memory.swap.max', 256)))
            and parent['oom_kill'] == 0, 'Persistent parent limits changed or parent OOM occurred')


def service_pid():
    pid = int(recovery.command('systemctl', 'show', 'finevent.service', '-p', 'MainPID', '--value').stdout.strip())
    require(pid > 0, 'finevent is not running')
    return pid


def sample(current, pid):
    row = recovery.sample(current, pid)
    group = recovery.diagnostic.cgroup_memory(pid)
    require(group['memory.max'] == 96*MIB and group['memory.swap.max'] == 0,
            'finevent 96 MiB / zero-swap limits changed')
    row.update(finevent_current=group['memory.current'], finevent_kernel_peak=group['memory.peak'],
               finevent_rss=recovery.diagnostic.read_numbers(Path(f'/proc/{pid}/status'), {'VmRSS'})['VmRSS'])
    return row


def validate_report(report, now):
    require(report.get('id') == PERSIST_ID and report.get('status') == 'completed_for_review'
            and report.get('stage') == 'completed' and not report.get('rollback')
            and not report.get('observation_issue'), 'The accepted persistence evidence is incomplete')
    finished = datetime.fromisoformat(report['finished_at']).timestamp()
    require(0 <= now-finished <= 7*86400, 'Persistence evidence is older than seven days or has an invalid timestamp')
    metrics = report['resources']
    require(metrics['samples'] > 0 and metrics['host_available_min_mib'] >= 64
            and metrics['max_sample_interval_seconds'] <= 15 and metrics['samples_below_64_mib'] == 0
            and all(metrics[key+'_delta'] == 0 for key in ('host_oom_kill', 'trend_oom_kill', 'finevent_oom_kill')),
            'Persistence resource evidence does not pass')
    outputs = report['outputs']
    require(len({r['at']//1800 for r in outputs['records'] if r['items'] > 0}) >= 2
            and outputs['source_statuses'] and outputs['source_failures'] == 0
            and outputs['finevent_poll_count'] > 0 and outputs['finevent_poll_failures'] == 0,
            'Persistence collection evidence does not pass')
    require(all(report['health'][path] == {'exit': 0, 'http_status': '200'}
                for path in ('/finevent/healthz', '/TrendRadar/content/')), 'Persistence HTTPS evidence does not pass')
    return finished


def inspect_persistence(accepted):
    """Read current invariants without installing, backing up or changing services."""
    evidence = BACKUPS/PERSIST_ID
    state = json.loads((evidence/'state.json').read_text())
    # Read only the managed file identities from the accepted operation; never reapply it.
    files = state.get('files', [])
    require(files and all(Path(record['path']).is_file()
                         and recovery.sha(Path(record['path'])) == record['after'] for record in files),
            'Persisted Compose, slice or sysctl files changed after acceptance')
    current = target()
    validate_target(current, accepted['container_after'], parent_metrics())
    require(recovery.sha(PATCH) == PATCH_SHA
            and recovery.sha(Path(f"/proc/{current['pid']}/root/app/trendradar/ai/client.py")) == PATCH_SHA,
            'The reviewed lazy-import patch changed')
    mounts = json.loads(recovery.command('docker', 'inspect', '--format', '{{json .Mounts}}', 'trend-radar').stdout)
    require(any(m.get('Source') == str(PATCH) and m.get('Destination') == '/app/trendradar/ai/client.py'
                and m.get('Type') == 'bind' and m.get('RW') is False for m in mounts), 'Patch is no longer a read-only bind')
    require(Path('/proc/sys/vm/swappiness').read_text().strip() == '60'
            and recovery.swap_usage().get(str(recovery.SWAP_FILE), {}).get('size_bytes', 0) >= 1023*MIB,
            'Reviewed host swap/reclaim policy changed')
    require((DATA/'config.json').is_file(), 'Existing initialized finevent configuration required; no interactive initialization in worker')
    require(not (DATA/'ai-probe-request.json').exists(), 'A pending paid probe requires review before deployment')
    previous = (ROOT/'current').resolve()
    require(previous.is_relative_to((ROOT/'releases').resolve()), 'Current release is not a managed release')
    installer.verify_bundle(previous)
    require(shutil.disk_usage('/opt').free >= 1024**3 and shutil.disk_usage(BACKUPS).free >= 1024**3,
            'At least 1 GiB free space is required for release and backups')
    pid = service_pid()
    baseline = sample(current, pid)
    counters = accepted['resources']['baseline_counters']
    require(all(baseline[key] == counters[key] for key in counters), 'OOM counters changed or reset after the accepted report')
    require(baseline['available'] >= 128*MIB, 'Less than 128 MiB current host headroom')
    return {'previous_release': str(previous), 'baseline': baseline, 'container': current,
            'config_sha256': recovery.sha(DATA/'config.json'), 'finevent_pid': pid}


def preflight(directory, baseline_id=None, baseline_sha256=None):
    evidence = BACKUPS/PERSIST_ID
    accepted = json.loads((evidence/'report.json').read_text())
    if baseline_id:
        import persistence_baseline
        renewed = persistence_baseline.load(baseline_id, baseline_sha256)
        finished = datetime.fromisoformat(renewed['observation_ended']).timestamp()
    else:
        require(not baseline_sha256, 'Baseline ID and digest must be supplied together')
        finished = validate_report(accepted, time.time())
    current = inspect_persistence(accepted)
    if baseline_id:
        persistence_baseline.validate_current(renewed, current)
    counters = accepted['resources']['baseline_counters']
    with closing(readonly(DATA/'finevent.db')) as conn:
        rows = list(conn.execute('SELECT at,available,oom_kills FROM resources WHERE at>=? ORDER BY at', (int(finished),)))
    require(rows and rows[0]['at'] <= finished+180 and time.time()-rows[-1]['at'] <= 180
            and all(row['available'] >= 64*MIB and row['oom_kills'] == counters['finevent_oom_kill'] for row in rows)
            and all(b['at']-a['at'] <= 180 for a, b in zip(rows, rows[1:])),
            'Post-persistence memory history is incomplete or shows pressure')
    backup = directory/'before.db'
    checksum = snapshot(DATA/'finevent.db', backup)
    return {'status': 'passed', 'persistence_id': PERSIST_ID, 'report_sha256': recovery.sha(evidence/'report.json'),
            **current, 'renewed_baseline': {'id': baseline_id, 'report_sha256': baseline_sha256} if baseline_id else None,
            'history_samples': len(rows), 'history_available_min_mib': min(r['available'] for r in rows)/MIB,
            'database_snapshot_sha256': checksum}


def reminder_state(conn):
    names = {'calendar_reminder_settings', 'calendar_reminder_watches', 'calendar_reminder_jobs'}
    present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} & names
    if not present:
        return {'schema': 'absent'}
    require(present == names, 'Automatic reminder schema is incomplete')
    require(not conn.execute('PRAGMA foreign_key_check(calendar_reminder_jobs)').fetchone(),
            'Automatic reminder jobs contain broken references')
    settings = list(conn.execute('SELECT id,payload FROM calendar_reminder_settings ORDER BY id'))
    watches = list(conn.execute('SELECT event_id,enabled FROM calendar_reminder_watches ORDER BY event_id'))
    jobs = list(conn.execute('SELECT key,delivery_id,event_id,anchor,scheduled_at FROM calendar_reminder_jobs ORDER BY key'))
    digest = lambda rows: hashlib.sha256(json.dumps([list(row) for row in rows], ensure_ascii=False).encode()).hexdigest()
    return {'schema': 'present', 'enabled': any(json.loads(row['payload']).get('enabled', False) for row in settings),
            'settings_sha256': digest(settings), 'watches_sha256': digest(watches),
            'job_identity_sha256': digest(jobs), 'watches': len(watches), 'jobs': len(jobs)}


def calendar_state(path=DATA/'finevent.db'):
    with closing(readonly(path)) as conn:
        conn.execute('BEGIN')
        sources = {row['id']: dict(row) for row in conn.execute('SELECT * FROM calendar_sources ORDER BY id')}
        digest = hashlib.sha256()
        count = 0
        for row in conn.execute('SELECT id,payload FROM calendar_events ORDER BY id'):
            digest.update(row['id'].encode()+b'\0'+row['payload'].encode()+b'\n')
            count += 1
        revisions = conn.execute('SELECT COUNT(*) FROM calendar_revisions').fetchone()[0]
        drafts = conn.execute('SELECT COUNT(*) FROM fund_drafts').fetchone()[0]
        snapshots = []
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='calendar_snapshots'").fetchone():
            for row in conn.execute("SELECT id,source,metadata,body FROM calendar_snapshots ORDER BY id"):
                info = json.loads(row['metadata'])
                active = conn.execute("SELECT COUNT(*),MAX(json_extract(payload,'$.end')) FROM calendar_events WHERE json_extract(payload,'$.provenance.snapshot_id')=?", (row['id'],)).fetchone()
                snapshots.append({'id': row['id'], 'source': row['source'], 'metadata': info,
                                  'body_sha256': hashlib.sha256(row['body']).hexdigest(),
                                  'active_entries': active[0], 'active_coverage_end': active[1]})
        repairs = []
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='calendar_source_repairs'").fetchone():
            repairs = [dict(row) for row in conn.execute('SELECT * FROM calendar_source_repairs ORDER BY id')]
        reminders = reminder_state(conn)
    return {'sources': sources, 'events': count, 'revisions': revisions, 'drafts': drafts,
            'events_sha256': digest.hexdigest(), 'snapshots': snapshots, 'repairs': repairs, 'reminders': reminders}


def verify_reminder_migration(before, after):
    require(after['schema'] == 'present', 'New release did not initialize automatic reminder tables')
    if before['schema'] == 'absent':
        require(not after['enabled'] and after['watches'] == 0 and after['jobs'] == 0,
                'Initial automatic reminders must remain disabled without generated jobs')
    else:
        require(before == after, 'Existing reminder settings, follows or job identities changed during migration')
    return {'status': 'passed', 'before': before, 'after': after, 'device_delivery_verified': False}


def snapshot_manifest():
    bundled = Path(__file__).with_name('calendar-snapshot.json')
    if not bundled.is_file():
        bundled = Path(__file__).resolve().parent.parent/'apps/server/internal/calendar/snapshots/nbs-2026.json'
    return json.loads(bundled.read_text(encoding='utf-8-sig'))


def source_acceptance(results, final, today):
    expected = snapshot_manifest()
    snapshots = final.get('snapshots', [])
    reviewed = any(s['source'] == 'nbs' and s['id'] == expected['id']
                   and s['body_sha256'] == expected['sha256']
                   and all(s['metadata'].get(k) == v for k, v in expected.items())
                   and s['active_entries'] > 0 and (s['active_coverage_end'] or '')[:10] >= today
                   for s in snapshots)
    healthy = lambda key: results[key]['healthy'] and results[key]['synced_again']
    good = (set(results) == SOURCE_IDS and all(r['synced_again'] for r in results.values())
            and all(healthy(k) for k in ('fomc', 'bea', 'nyfed'))
            and (healthy('nbs') or reviewed))
    limits = ['NYFed covers selected BLS events in current/next published month, not the complete BLS calendar.']
    if not results['bls']['healthy']:
        limits.append('BLS primary synchronization remains unavailable; its error is retained.')
    if any(s['active_entries'] for s in snapshots):
        limits.append('Reviewed NBS snapshot entries may miss subsequent rescheduling; original and review time are retained.')
    return {'status': 'accepted_with_source_limits' if good else 'needs_review',
            'nbs_snapshot_available': reviewed, 'snapshots': snapshots, 'limitations': limits}


def resource_checks_passed(report):
    metrics = report.get('resources', {})
    return (metrics.get('samples', 0) > 0 and metrics.get('host_available_min_mib', 0) >= 64
            and metrics.get('max_sample_interval_seconds', 999) <= 15
            and metrics.get('samples_below_64_mib', -1) == 0
            and all(metrics.get(k+'_delta', -1) == 0 for k in ('host_oom_kill', 'trend_oom_kill', 'finevent_oom_kill'))
            and 0 < metrics.get('finevent_current_peak_mib', 999) <= 96)


def runtime_passed(report, trend_review=None):
    return (resource_checks_passed(report)
            and (report.get('trendradar', {}).get('status') == 'passed' or calendar_review.accepted(report, trend_review))
            and all(report.get('health', {}).get(path) == {'exit': 0, 'http_status': '200'}
                    for path in ('/finevent/healthz', '/TrendRadar/content/')))


def validate_previous(report, run_id, active_release, trend_review=None):
    require(ID_PATTERN.fullmatch(run_id) and report.get('id') == run_id
            and report.get('status') in ('completed_for_review', 'completed_with_source_limits_for_review', 'needs_review')
            and report.get('stage') == 'completed' and not report.get('rollback') and not report.get('failure')
            and report.get('deployment', {}).get('release') == active_release,
            'Previous calendar operation is incomplete, rolled back, or no longer the active release')
    require(report.get('restart', {}).get('status') == 'passed'
            and report.get('backup_verification', {}).get('status') == 'passed'
            and runtime_passed(report, trend_review), 'Previous calendar resource/service/backup evidence needs review')
    started = datetime.fromisoformat(report['observation_started']).timestamp()
    finished = datetime.fromisoformat(report['finished_at']).timestamp()
    profile = report.get('verification_profile', 'calendar')
    require(profile in ('calendar', 'news', 'ai'), 'Unknown previous verification profile')
    if profile == 'ai':
        import ai_release
        require(report.get('ai_acceptance', {}).get('status') == 'passed' and ai_release.checks_passed(report),
                'Previous AI verification is incomplete or needs review')
        return
    if profile == 'news':
        import news_release
        require(finished-started >= news_release.OBSERVATION_SECONDS-1
                and report.get('news_acceptance', {}).get('status') == 'passed'
                and news_release.checks_passed(report), 'Previous news verification is incomplete or needs review')
        return
    # Observer deadline uses integer epoch seconds; ISO start retains fractions.
    require(finished-started >= OBSERVATION_SECONDS-1, 'Previous calendar observation has not completed')
    results = report.get('calendar', {})
    require({'nbs', 'bls', 'fomc', 'bea'} <= set(results)
            and all(r.get('synced_again') for r in results.values())
            and all(results[k].get('healthy') for k in ({'fomc', 'bea', 'nyfed'} & set(results))),
            'Previous operation has issues beyond the reviewed source limitations')


def review_previous(run_id):
    require(ID_PATTERN.fullmatch(run_id), 'Invalid previous operation ID')
    path = BACKUPS/run_id/'report.json'
    previous = json.loads(path.read_text())
    trend_review = calendar_review.load(previous, BACKUPS)
    validate_previous(previous, run_id, str((ROOT/'current').resolve()), trend_review)
    unit = 'finevent-'+run_id+'.service'
    active = recovery.command('systemctl', 'show', unit, '-p', 'ActiveState', '--value').stdout.strip()
    require(active in ('inactive', 'failed'), 'Previous calendar observer is still active; do not interrupt it')
    return {'id': run_id, 'report_sha256': recovery.sha(path),
            'trend_source_review': ({**calendar_review.manifest()[run_id]} if trend_review else None)}


def review_failed_preflight(run_id, report_sha256, after_review):
    """Explicitly cross one stopped, pre-install failure; retain its original evidence."""
    require(ID_PATTERN.fullmatch(run_id) and run_id != after_review, 'Invalid failed operation ID')
    path = BACKUPS/run_id/'report.json'
    require(re.fullmatch(r'[a-f0-9]{64}', report_sha256 or '') and recovery.sha(path) == report_sha256,
            'Failed operation report changed')
    failed = json.loads(path.read_text())
    require(failed.get('id') == run_id and failed.get('status') == 'incomplete' and failed.get('stage') == 'preflight'
            and failed.get('failure') and failed.get('finished_at') and failed.get('after_review') == after_review
            and failed.get('verification_profile', 'calendar') in ('calendar', 'news')
            and not any(key in failed for key in ('installer_evidence', 'deployment', 'rollback', 'replace_observation')),
            'Only an explicit pre-install failure can be recovered')
    active = observer_state(run_id)
    require(active['ActiveState'] in ('inactive', 'failed') and active['MainPID'] == '0', 'Failed observer is still active or unknown')
    previous = review_previous(after_review)
    require(failed.get('previous_review') == previous, 'Original predecessor evidence changed or was not reviewed')
    return {'id': run_id, 'report_sha256': report_sha256, 'previous_review': previous}


def validate_interruptible(report, run_id, active_release):
    require(ID_PATTERN.fullmatch(run_id) and report.get('id') == run_id
            and report.get('unit') == 'finevent-'+run_id+'.service'
            and report.get('status') == 'observing' and report.get('stage') == 'scheduled_sync_and_resources'
            and not report.get('failure') and not report.get('rollback')
            and report.get('deployment', {}).get('release') == active_release,
            'Only the named active calendar observation can be interrupted for repair')
    require(report.get('preflight', {}).get('status') == 'passed'
            and report.get('restart', {}).get('status') == 'passed'
            and report.get('backup_verification', {}).get('status') == 'passed'
            and resource_checks_passed(report), 'Existing deployment or resource fault requires review before replacement')


def validate_live_predecessor(previous):
    require(str((ROOT/'current').resolve()) == previous['deployment']['release'], 'Active release changed before observer replacement')
    pid = service_pid()
    require(pid == previous['restart']['after_pid'], 'finevent process changed before observer replacement')
    current = target()
    validate_target(current, previous['preflight']['container'], parent_metrics())
    check_sample(sample(current, pid), previous['preflight']['baseline'])
    require(recovery.sha(DATA/'config.json') == previous['preflight']['config_sha256'], 'finevent configuration changed before observer replacement')


def observer_state(run_id):
    result = recovery.command('systemctl', 'show', 'finevent-'+run_id+'.service',
                              '-p', 'ActiveState', '-p', 'MainPID', check=False)
    state = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    require(state.get('ActiveState') in ('active', 'inactive', 'failed') and state.get('MainPID', '').isdigit(),
            'Cannot determine the named observer state')
    return state


def prepare_replacement(directory, report):
    run_id = report['replace_observation']
    require(ID_PATTERN.fullmatch(run_id) and run_id != report['id'], 'Invalid predecessor operation ID')
    path = BACKUPS/run_id/'report.json'
    raw = path.read_bytes()
    previous = json.loads(raw)
    # Preserve the original byte-for-byte before stopping any worker.
    saved = directory/'previous-observation-before-stop.json'
    saved.write_bytes(raw)
    saved.chmod(0o600)
    report['replacement'] = {'previous_id': run_id, 'evidence_snapshot': str(saved),
                             'evidence_sha256': hashlib.sha256(raw).hexdigest(),
                             'reason': 'User-authorized repair deployment; unfinished observation is not accepted.',
                             'observer_stopped': False}
    recovery.save(directory/'report.json', report)
    if previous.get('stage') == 'completed':
        validate_previous(previous, run_id, str((ROOT/'current').resolve()))
        return
    validate_interruptible(previous, run_id, str((ROOT/'current').resolve()))
    require(0 <= time.time()-path.stat().st_mtime <= 180, 'Observer checkpoint is stale; preserve evidence for review')
    validate_live_predecessor(previous)
    health = recovery.public_health()
    require(all(health.get(p) == {'exit': 0, 'http_status': '200'} for p in ('/finevent/healthz', '/TrendRadar/content/')),
            'Service health failed before observation replacement')
    state = observer_state(run_id)
    require(state['ActiveState'] == 'active' and int(state['MainPID']) > 0, 'Expected observation worker is not active')
    # This unit only monitors; finevent.service and TrendRadar are separate units.
    recovery.command('systemctl', 'stop', 'finevent-'+run_id+'.service', timeout=40)
    report['replacement'].update(observer_stopped=True, stopped_at=stamp())
    recovery.save(directory/'report.json', report)


def finish_replacement(directory, report):
    # Called with both transition and global deployment locks held after the old worker exits.
    run_id = report['replace_observation']
    path = BACKUPS/run_id/'report.json'
    previous = json.loads(path.read_text())
    state = observer_state(run_id)
    require(state['ActiveState'] == 'inactive' and int(state['MainPID']) == 0, 'Previous observer has not stopped')
    validate_live_predecessor(previous)
    if previous.get('stage') == 'completed':
        validate_previous(previous, run_id, str((ROOT/'current').resolve()))
        return {'id': run_id, 'report_sha256': recovery.sha(path), 'observation_complete': True}
    validate_interruptible(previous, run_id, str((ROOT/'current').resolve()))
    require(report['replacement']['observer_stopped'], 'No recorded observer stop; cannot mark an interruption')
    previous.update(status='interrupted_for_repair', stage='observation_interrupted', finished_at=stamp(),
                    observation_acceptance='incomplete', interruption={**report['replacement'], 'replacement_id': report['id']})
    # Keep all samples, source failures, planned end and original evidence; only record the actual interruption.
    recovery.save(path, previous)
    recovery.save(directory/'previous-observation-interrupted.json', previous)
    return {'id': run_id, 'report_sha256': recovery.sha(path), 'observation_complete': False,
            'interrupted_for_repair': True, 'evidence_snapshot': report['replacement']['evidence_snapshot']}


def calendar_results(first, final):
    result = {}
    for source in sorted(SOURCE_IDS):
        start, end = first.get('sources', {}).get(source, {}), final.get('sources', {}).get(source, {})
        result[source] = {'initial': start, 'latest': end,
                          'synced_again': end.get('last_attempt', 0) >= start.get('last_attempt', 0)+6*3600
                          and start.get('last_attempt', 0) > 0,
                          'healthy': end.get('last_success', 0) == end.get('last_attempt', 0)
                          and end.get('last_success', 0) > 0 and not end.get('error') and end.get('count', 0) > 0}
    return result


def wait_initial_sources(directory, report, current):
    deadline = time.monotonic()+INITIAL_SYNC_SECONDS
    pid = service_pid()
    baseline = sample(current, pid)
    samples = [baseline]
    while time.monotonic() < deadline:
        row = sample(current, pid)
        samples.append(row)
        check_sample(row, baseline)
        try:
            state = calendar_state()
        except sqlite3.OperationalError:
            state = {'sources': {}}
        retry_pending = (state['sources'].get('nyfed', {}).get('error') == '纽约联储当前页月份未更新，保留已有日程'
                         or any(state['sources'].get(r['source'], {}).get('last_attempt', 0) < r['requested_at']
                                for r in state.get('repairs', [])))
        if set(state['sources']) == SOURCE_IDS and not retry_pending:
            report['initial_resources'] = resource_summary(samples, baseline)
            recovery.save(directory/'report.json', report)
            return state
        time.sleep(5)
    raise RuntimeError('Initial calendar synchronization did not finish within ten minutes')


def check_sample(row, baseline):
    require(row['available'] >= 64*MIB, 'Host available memory below 64 MiB')
    require(all(row[key] == baseline[key] for key in ('trend_oom_kill', 'finevent_oom_kill', 'host_oom_kill')),
            'New or reset OOM counter during observation')
    require(row['finevent_current'] <= 96*MIB, 'finevent exceeds its 96 MiB limit')


def verify_restart(before, after):
    # No scheduled source is due during this short intentional restart.
    require(before == after, 'Calendar data, reminder settings/job identities or durable cursors changed across restart')


def restart_check(directory, report, current):
    before = calendar_state()
    old_pid = service_pid()
    report['stage'] = 'restart_check'
    recovery.save(directory/'report.json', report)
    recovery.command('systemctl', 'restart', 'finevent.service', timeout=40)
    installer.wait_for_https(directory)
    time.sleep(15)
    pid = service_pid()
    require(pid != old_pid, 'Intentional finevent restart did not replace the process')
    after = calendar_state()
    verify_restart(before, after)
    row = sample(current, pid)
    check_sample(row, report['preflight']['baseline'])
    return {'status': 'passed', 'before_pid': old_pid, 'after_pid': pid, 'calendar': after,
            'host_reboot_tested': False}


def trend_outputs(start, end, minimum_cycles=2):
    records, sources = [], []
    day = datetime.fromtimestamp(start, ZONE).date()
    last = datetime.fromtimestamp(end, ZONE).date()
    while day <= last:
        with closing(readonly(TREND/'news'/f'{day.isoformat()}.db')) as conn:
            for row in conn.execute('SELECT id,crawl_time,total_items FROM crawl_records ORDER BY id'):
                at = int(datetime.strptime(f'{day} {row["crawl_time"]}', '%Y-%m-%d %H-%M').replace(tzinfo=ZONE).timestamp())
                if start <= at <= end:
                    records.append({'at': at, 'items': row['total_items']})
                    sources.extend({'at': at, 'source': r['platform_id'], 'status': r['status']} for r in conn.execute(
                        'SELECT platform_id,status FROM crawl_source_status WHERE crawl_record_id=?', (row['id'],)))
        day += timedelta(days=1)
    with closing(readonly(DATA/'finevent.db')) as conn:
        polls = conn.execute("SELECT COUNT(*),SUM(CASE WHEN success=0 THEN 1 ELSE 0 END),MAX(CASE WHEN success=1 THEN completed END) FROM polls WHERE source='trendradar' AND started>=?", (start,)).fetchone()
    html = TREND/'index.html'
    good = (len({r['at']//1800 for r in records if r['items'] > 0}) >= minimum_cycles
            and all(sum(s['at'] == r['at'] for s in sources) == 9 for r in records)
            and all(s['status'] == 'success' for s in sources) and polls[0] > 0 and polls[1] == 0
            and polls[2] is not None and end-polls[2] <= 180
            and html.is_file() and html.stat().st_size > 0 and html.stat().st_mtime >= max((r['at'] for r in records), default=end))
    return {'status': 'passed' if good else 'needs_review', 'cycles': len(records),
            'distinct_cycles': len({r['at']//1800 for r in records if r['items'] > 0}),
            'source_failures': sum(s['status'] != 'success' for s in sources), 'polls': polls[0],
            'poll_failures': polls[1], 'last_import': polls[2], 'html_mtime': html.stat().st_mtime if html.exists() else None}


def rollback_current(directory, report):
    backup = Path(report['deployment']['backup'])
    require(str((ROOT/'current').resolve()) == report['deployment']['release'],
            'Another release is now active; automatic rollback refused')
    state = json.loads((backup/'state.json').read_text())
    # Keep new calendar/news data. The previous binary ignores additive calendar tables.
    rehearsal = directory/'rollback-latest-data'
    rehearsal.mkdir(mode=0o700)
    recovery.save(rehearsal/'state.json', state)
    recovery.command('systemctl', 'stop', 'finevent-backup.timer', 'finevent.service')
    try:
        snapshot(DATA/'finevent.db', rehearsal/'database.db')
    except BaseException:
        recovery.command('systemctl', 'start', 'finevent.service', check=False)
        if state.get('timer_active'):
            recovery.command('systemctl', 'start', 'finevent-backup.timer', check=False)
        raise
    result = installer.rollback(rehearsal)
    installer.wait_for_https(rehearsal)
    return {'status': 'restored', 'latest_data_preserved': True, **result}


def observe(directory, report, current):
    pid = service_pid()
    identities = recovery.process_identity(current['pid']), recovery.process_identity(pid)
    baseline = sample(current, pid)
    check_sample(baseline, report['preflight']['baseline'])
    first = report['initial_calendar']
    started = int(time.time())
    end = started+OBSERVATION_SECONDS
    report.update(status='observing', stage='scheduled_sync_and_resources', observation_started=stamp(),
                  planned_end=datetime.fromtimestamp(end, ZONE).isoformat())
    samples, last_export = [], 0
    with (directory/'samples.jsonl').open('a', encoding='utf-8') as stream:
        while time.time() < end:
            recovery.validate_live_processes(current, pid, identities)
            row = sample(current, pid)
            samples.append(row)
            stream.write(json.dumps(row)+'\n')
            # Preserve the failing sample before the resource guard raises and rollback starts.
            report['resources'] = resource_summary(samples, baseline)
            check_sample(row, baseline)
            if time.time()-last_export >= 60:
                report['resources'] = resource_summary(samples, baseline)
                report['calendar'] = calendar_results(first, calendar_state())
                recovery.save(directory/'report.json', report)
                stream.flush()
                last_export = time.time()
            time.sleep(5)
    report['resources'] = resource_summary(samples, baseline)
    final = calendar_state()
    report['calendar'] = calendar_results(first, final)
    report['source_acceptance'] = source_acceptance(report['calendar'], final, datetime.now(ZONE).date().isoformat())
    report['trendradar'] = trend_outputs(started, int(time.time()))
    report['health'] = recovery.public_health()
    validate_target(target(), report['preflight']['container'], parent_metrics())
    require(recovery.sha(DATA/'config.json') == report['preflight']['config_sha256'], 'finevent secrets changed during verification')
    good = runtime_passed(report) and report['source_acceptance']['status'] == 'accepted_with_source_limits'
    report.update(status='completed_with_source_limits_for_review' if good else 'needs_review', stage='completed', finished_at=stamp())
    # Source failures are a degraded-data result, not grounds to remove the usable calendar.
    # Human desktop/mobile acceptance remains separate, even when all machine checks pass.
    report['user_acceptance'] = 'pending'


def resource_summary(samples, baseline):
    return {**recovery.summarize(samples, baseline),
            'finevent_current_peak_mib': max(r['finevent_current'] for r in samples)/MIB,
            'finevent_kernel_peak_mib': max(r['finevent_kernel_peak'] for r in samples)/MIB,
            'finevent_rss_peak_mib': max(r['finevent_rss'] for r in samples)/MIB}


def run(directory, report):
    import fcntl
    with LOCK.open('a') as lock:
        require(report['status'] == 'queued', 'This operation has already started; do not run it again')
        try:
            # A transition may stop an old observer which still owns the global lock.
            # Release this shorter lock before observing so a later repair can replace us.
            transition = LOCK.with_name(LOCK.name+'.transition').open('a')
            fcntl.flock(transition, fcntl.LOCK_EX | fcntl.LOCK_NB)
            report.update(status='deploying', stage='preflight')
            recovery.save(directory/'report.json', report)
            if report.get('replace_observation'):
                prepare_replacement(directory, report)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if report.get('replace_observation'):
                report['previous_review'] = finish_replacement(directory, report)
            elif report.get('after_review'):
                report['previous_review'] = review_previous(report['after_review'])
            if report.get('recover_preflight'):
                report['recovery'] = review_failed_preflight(report['recover_preflight'], report['failed_report_sha256'], report['after_review'])
            report['preflight'] = preflight(directory, report.get('baseline_id'), report.get('baseline_sha256'))
            if report.get('verification_profile') == 'ai':
                import ai_release
                ai_release.preflight()
            report['stage'] = 'install'
            recovery.save(directory/'report.json', report)
            def checkpoint(backup):
                report['installer_evidence'] = str(backup)
                recovery.save(directory/'report.json', report)
            backup = installer.install(evidence_callback=checkpoint)
            release = str((ROOT/'current').resolve())
            report['deployment'] = {'backup': str(backup), 'release': release,
                                    'rollback_command': f'python3 {Path(__file__).resolve()} --run-id {report["id"]} --rollback'}
            # Read the frozen pre-deploy copy and restore it to a disposable DB, never to live data.
            backup_sha = snapshot(backup/'database.db', directory/'restore-check.db')
            report['backup_verification'] = {'status': 'passed', 'restored_copy_sha256': backup_sha}
            current = target()
            validate_target(current, report['preflight']['container'], parent_metrics())
            report['stage'] = 'initial_calendar'
            recovery.save(directory/'report.json', report)
            report['initial_calendar'] = wait_initial_sources(directory, report, current)
            with closing(readonly(backup/'database.db')) as old_db:
                report['reminder_migration'] = verify_reminder_migration(
                    reminder_state(old_db), report['initial_calendar']['reminders'])
            if report.get('verification_profile') == 'ai':
                ai_release.prepare(directory, report, current, backup)
            elif report.get('verification_profile') == 'news':
                import news_release
                news_release.prepare(directory, report, current, backup)
            else:
                report['restart'] = restart_check(directory, report, current)
            report['stage'] = 'ready_for_user_checks'
            recovery.save(directory/'report.json', report)
            transition.close()
            if report.get('verification_profile') == 'ai':
                ai_release.observe(directory, report, current)
            elif report.get('verification_profile') == 'news':
                news_release.observe(directory, report, current)
            else:
                observe(directory, report, current)
        except BaseException as error:
            report.update(status='incomplete', failure_type=type(error).__name__, finished_at=stamp())
            # Exceptions contain fixed gate messages; subprocess stderr/private configurations are never exported.
            report['failure'] = str(error)[:600] if isinstance(error, RuntimeError) else 'See the retained server evidence; no automatic retry.'
            recovery.save(directory/'report.json', report)
            if report.get('deployment'):
                try:
                    report['rollback'] = rollback_current(directory, report)
                except BaseException as rollback_error:
                    report['rollback'] = {'status': 'needs_review', 'error_type': type(rollback_error).__name__}
            elif report.get('installer_evidence'):
                report['rollback'] = {'status': 'installer_handled', 'evidence': report['installer_evidence'],
                                      'active_release': str((ROOT/'current').resolve())}
            recovery.save(directory/'report.json', report)
            raise
        finally:
            if 'transition' in locals():
                transition.close()
        recovery.save(directory/'report.json', report)


def export_status(directory, wait_ready=0):
    deadline = time.monotonic()+wait_ready
    while True:
        report = json.loads((directory/'report.json').read_text())
        if report['status'] not in ('queued', 'deploying') or time.monotonic() >= deadline:
            break
        time.sleep(2)
    result = recovery.command('systemctl', 'show', report['unit'], '-p', 'LoadState', '-p', 'ActiveState', '-p', 'ExecMainStatus', check=False)
    status = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    report['observer_status'] = status
    if report['status'] in ('queued', 'deploying', 'observing') and status.get('ActiveState') not in ('active', 'activating'):
        report['observer_status']['warning'] = 'Worker is no longer active; review retained evidence before retrying.'
    recovery.save(directory/'download.json', report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    predecessor = parser.add_mutually_exclusive_group()
    predecessor.add_argument('--after-review')
    predecessor.add_argument('--replace-observation')
    parser.add_argument('--baseline-id')
    parser.add_argument('--baseline-sha256')
    parser.add_argument('--recover-preflight')
    parser.add_argument('--failed-report-sha256')
    parser.add_argument('--wait-ready', type=int, default=0)
    parser.add_argument('--news', action='store_true')
    parser.add_argument('--ai', action='store_true')
    modes = parser.add_mutually_exclusive_group(required=True)
    for name in ('launch', 'run', 'status', 'rollback'):
        modes.add_argument('--'+name, action='store_true')
    args = parser.parse_args()
    require(0 <= args.wait_ready <= 180 and (not args.wait_ready or args.status), 'Wait-ready is only available with status, up to 180 seconds')
    require(not (args.after_review or args.replace_observation) or args.launch, 'Predecessor options are only available when launching')
    require(not args.news or (args.launch and not args.replace_observation), 'News profile requires a new launch after a completed review')
    require(not args.ai or (args.launch and args.after_review and not args.news and not args.replace_observation),
            'AI profile requires a completed predecessor and cannot replace another observation')
    require(bool(args.baseline_id) == bool(args.baseline_sha256) and bool(args.recover_preflight) == bool(args.failed_report_sha256),
            'Evidence IDs and digests must be paired')
    require(not (args.baseline_id or args.recover_preflight) or (args.launch and args.after_review and not args.ai),
            'Baseline renewal and pre-install recovery require a non-AI launch after review')
    require(os.geteuid() == 0 and ID_PATTERN.fullmatch(args.run_id), 'Root and a valid operation ID are required')
    os.umask(0o077)
    directory = BACKUPS/args.run_id
    if args.launch:
        installer.verify_bundle(Path(__file__).resolve().parent.parent)
        directory.mkdir(mode=0o700)
        report = {'id': args.run_id, 'status': 'queued', 'stage': 'queued', 'created_at': stamp(),
                  'unit': 'finevent-'+args.run_id+'.service', 'runner': str(Path(__file__).resolve()),
                  'paid_model_calls': 0, 'user_acceptance': 'pending'}
        report['verification_profile'] = 'ai' if args.ai else 'news' if args.news else 'calendar'
        if args.after_review:
            report['after_review'] = args.after_review
        if args.replace_observation:
            report['replace_observation'] = args.replace_observation
        for key in ('baseline_id', 'baseline_sha256', 'recover_preflight', 'failed_report_sha256'):
            if getattr(args, key):
                report[key] = getattr(args, key)
        recovery.save(directory/'report.json', report)
        if args.ai:
            import ai_release
            try:
                review_previous(args.after_review)
                ai_release.preflight()
                ai_release.stage_key(directory)
            except BaseException:
                report.update(status='incomplete', stage='credential_preparation', failure='Credential preparation did not finish; no deployment or model request was started.')
                recovery.save(directory/'report.json', report)
                raise
        recovery.command('systemd-run', '--unit='+report['unit'], '--property=Type=exec', '--property=MemoryMax=64M',
                         '--property=MemorySwapMax=0', '--property=Nice=10', '--property=RuntimeMaxSec=24000',
                         '/usr/bin/python3', '-B', str(Path(__file__).resolve()), '--run-id', args.run_id, '--run')
        print('Background release/verification launched. Evidence: '+str(directory), flush=True)
    elif args.status:
        report = export_status(directory, args.wait_ready)
        print(json.dumps({k: report.get(k) for k in ('id', 'status', 'stage', 'planned_end', 'failure', 'observer_status')}, ensure_ascii=False, indent=2))
    elif args.rollback:
        import fcntl
        with LOCK.with_name(LOCK.name+'.transition').open('a') as transition:
            fcntl.flock(transition, fcntl.LOCK_EX | fcntl.LOCK_NB)
            pending = json.loads((directory/'report.json').read_text())
            require(pending.get('deployment'), 'Installation is not complete; review installer evidence before rollback')
            recovery.command('systemctl', 'stop', pending['unit'], check=False, timeout=40)
            with LOCK.open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                report = json.loads((directory/'report.json').read_text())
                report['rollback'] = rollback_current(directory, report)
                report['status'] = 'rolled_back'
                recovery.save(directory/'report.json', report)
    else:
        run(directory, json.loads((directory/'report.json').read_text()))


if __name__ == '__main__':
    main()
