"""Read-only memory investigation; writes only a limited report beside existing evidence."""

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess

ZONE = timezone(timedelta(hours=8))
BACKUPS = Path('/srv/example/finevent-backups')


def stamp(value):
    return datetime.fromtimestamp(value, ZONE).isoformat()


def read_numbers(path, allowed):
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    result = {}
    for line in lines:
        fields = line.replace(':', '').split()
        if len(fields) >= 2 and fields[0] in allowed and fields[1].isdigit():
            value = int(fields[1])
            result[fields[0]] = value * 1024 if fields[-1] == 'kB' else value
    return result


def memory_history(database, start, end):
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro&immutable=1', uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute(
            'SELECT at,rss,available,cgroup_current,cgroup_peak,oom_kills FROM resources WHERE at BETWEEN ? AND ? ORDER BY at',
            (start, end))]
    valid = [row for row in rows if row['available'] > 0]
    def public(row):
        return {'at': stamp(row['at']), 'host_available_mib': row['available']/1024**2,
                'finevent_rss_mib': row['rss']/1024**2,
                'finevent_cgroup_current_mib': row['cgroup_current']/1024**2,
                'finevent_cgroup_peak_mib': row['cgroup_peak']/1024**2,
                'finevent_oom_kills': row['oom_kills']}
    ordered = sorted(valid, key=lambda row: row['available'])
    thresholds = {}
    for threshold in (64, 96, 128):
        low = [row for row in valid if row['available'] < threshold*1024**2]
        runs = []
        run = []
        previous = None
        for row in rows:
            if row['available'] > 0 and row['available'] < threshold*1024**2:
                if run and row['at']-previous > 180:
                    runs.append(run); run = []
                run.append(row['at'])
            elif run:
                runs.append(run); run = []
            previous = row['at']
        if run:
            runs.append(run)
        thresholds[str(threshold)] = {
            'samples_below': len(low), 'fraction_of_valid_samples': len(low)/len(valid) if valid else None,
            'sample_runs': len(runs),
            # Discrete observations establish a span, not a continuous duration below the threshold.
            'longest_sample_runs': [{'first': stamp(run[0]), 'last': stamp(run[-1]),
                                    'samples': len(run), 'observed_span_seconds': run[-1]-run[0]}
                                   for run in sorted(runs, key=lambda run: run[-1]-run[0], reverse=True)[:10]]}
    worst = ordered[0]['at'] if ordered else None
    return {'samples': len(rows), 'missing_available': len(rows)-len(valid),
            'first_sample': stamp(rows[0]['at']) if rows else None,
            'last_sample': stamp(rows[-1]['at']) if rows else None,
            'thresholds_mib': thresholds,
            'lowest_samples': [public(row) for row in ordered[:20]],
            'around_minimum': [public(row) for row in rows if worst is not None and abs(row['at']-worst) <= 300],
            'historical_process_attribution': 'not_recorded'}


def run(*args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired) as error:
        return type(error).__name__, ''


def process_memory(proc=Path('/proc')):
    rows = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        status = read_numbers(entry/'status', {'VmRSS', 'RssAnon', 'RssFile', 'VmSwap'})
        if not status or not status.get('VmRSS'):
            continue
        try:
            name = (entry/'comm').read_text().strip()[:80]
        except OSError:
            continue
        # No argv, environment, executable path, usernames or process-owned files.
        rows.append({'pid': int(entry.name), 'name': name, **status})
    return sorted(rows, key=lambda row: row['VmRSS'], reverse=True)[:20]


def cgroup_memory(pid, proc=Path('/proc'), root=Path('/sys/fs/cgroup')):
    try:
        membership = (proc/str(pid)/'cgroup').read_text().splitlines()
        relative = next(line[3:] for line in membership if line.startswith('0::'))
        path = (root/relative.lstrip('/')).resolve()
        if not path.is_relative_to(root.resolve()):
            return {'status': 'invalid_path'}
        result = {'path': relative}
        for name in ('memory.current', 'memory.peak', 'memory.high', 'memory.max', 'memory.swap.current', 'memory.swap.max'):
            try:
                value = (path/name).read_text().strip()
                result[name] = int(value) if value.isdigit() else value[:64]
            except OSError:
                result[name] = None
        for name in ('memory.events', 'memory.events.local'):
            result[name] = read_numbers(path/name, {'low', 'high', 'max', 'oom', 'oom_kill', 'oom_group_kill'})
        result['memory.stat'] = read_numbers(path/'memory.stat', {'anon', 'file', 'kernel', 'kernel_stack', 'pagetables', 'slab', 'sock'})
        return result
    except (OSError, StopIteration):
        return {'status': 'unavailable_or_not_cgroup_v2'}


def container_memory():
    code, output = run('docker', 'ps', '-aq', '--no-trunc')
    result = {'list_exit': code, 'containers': []}
    ids = [value for value in output.splitlines() if re.fullmatch('[a-f0-9]{64}', value)]
    result['truncated'] = len(ids) > 20
    for container in ids[:20]:
        # Explicit fields only. Never request the complete inspect document (it includes secrets).
        state_template = '{"Status":{{json .State.Status}},"Running":{{json .State.Running}},"OOMKilled":{{json .State.OOMKilled}},"Pid":{{json .State.Pid}},"ExitCode":{{json .State.ExitCode}},"StartedAt":{{json .State.StartedAt}},"FinishedAt":{{json .State.FinishedAt}}}'
        template = '{"id":{{json .Id}},"name":{{json .Name}},"state":'+state_template+',"restart_count":{{json .RestartCount}},"memory_limit":{{json .HostConfig.Memory}},"memory_swap_limit":{{json .HostConfig.MemorySwap}}}'
        exit_code, body = run('docker', 'inspect', '--format', template, container)
        item = {'id': container, 'inspect_exit': exit_code}
        if exit_code == 0:
            try:
                decoded = json.loads(body)
                item.update({key: decoded.get(key) for key in ('id', 'name', 'restart_count', 'memory_limit', 'memory_swap_limit')})
                state = decoded.get('state', {})
                item['state'] = {key: state.get(key) for key in ('Status', 'Running', 'OOMKilled', 'Pid', 'ExitCode', 'StartedAt', 'FinishedAt')}
                if state.get('Running') and isinstance(state.get('Pid'), int) and state['Pid'] > 0:
                    item['cgroup'] = cgroup_memory(state['Pid'])
            except (ValueError, TypeError):
                item['parse_error'] = True
        result['containers'].append(item)
    return result


def oom_evidence(start, end):
    # Filter before returning logs; bounded kernel OOM records carry process/cgroup attribution.
    code, body = run('journalctl', '-k', '--since', '@'+str(start), '--until', '@'+str(end),
                     '--grep', '(?i)oom|out of memory|killed process', '--no-pager', '-n', '100', '-o', 'json')
    events = []
    for line in body.splitlines():
        try:
            row = json.loads(line)
            message = row.get('MESSAGE', '')
            if isinstance(message, str) and re.search(r'oom|out of memory|killed process', message, re.I):
                events.append({'at': stamp(int(row['__REALTIME_TIMESTAMP'])//1000000), 'message': message[:1200]})
        except (ValueError, KeyError, TypeError):
            continue
    docker_exit, docker_body = run('docker', 'events', '--since', str(start), '--until', str(end),
                                   '--filter', 'event=oom', '--format', '{"time":{{.Time}},"id":"{{.Actor.ID}}"}')
    docker_events = []
    for line in docker_body.splitlines():
        try:
            row = json.loads(line)
            docker_events.append({'at': stamp(int(row['time'])), 'container_id': row['id']})
        except (ValueError, KeyError, TypeError):
            continue
    return {'kernel_exit': code, 'kernel_events': events, 'kernel_may_be_truncated': len(body.splitlines()) >= 100,
            'docker_exit': docker_exit, 'docker_events': docker_events,
            'coverage': 'retained_logs_only; absence of events does not establish absence of historical OOM'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verification-id', required=True)
    parser.add_argument('--report-name', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'verification-\d{8}T\d{6}Z-[a-f0-9]{8}', args.verification_id):
        raise RuntimeError('Invalid verification ID')
    if not re.fullmatch(r'memory-\d{8}T\d{6}Z-[a-f0-9]{8}\.json', args.report_name):
        raise RuntimeError('Invalid report name')
    if os.geteuid() != 0:
        raise RuntimeError('Run in the authorized root SSH terminal')
    os.umask(0o077)
    directory = BACKUPS/args.verification_id
    evidence = json.loads((directory/'report.json').read_text())
    start = int(datetime.fromisoformat(evidence['history']['days'][0]['date']).replace(tzinfo=ZONE).timestamp())
    end = int(datetime.fromisoformat(evidence['created_at']).timestamp())
    now = int(datetime.now(ZONE).timestamp())
    result = {'created_at': stamp(now), 'verification_id': args.verification_id,
              'historical_resources': memory_history(directory/'before.db', start, end),
              'current_meminfo_bytes': read_numbers(Path('/proc/meminfo'), {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree', 'Cached', 'Slab', 'SReclaimable', 'SUnreclaim'}),
              'top_process_rss_bytes': process_memory(), 'containers': container_memory(),
              'oom_evidence': oom_evidence(start-86400, now),
              'status': 'collected_for_review'}
    path = directory/args.report_name
    with path.open('x', encoding='utf-8') as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    print('Read-only memory report: '+str(path))


if __name__ == '__main__':
    main()
