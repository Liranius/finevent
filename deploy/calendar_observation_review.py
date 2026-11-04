"""Read existing crawl evidence after a completed calendar observation; never retry it."""

import argparse
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Shanghai')
BACKUPS = Path('/srv/example/finevent-backups')
TREND = Path('/srv/example/trendradar/output')
RELEASE_ID = re.compile(r'calendar-release-\d{8}T\d{6}Z-[a-f0-9]{8}')
REVIEW_ID = re.compile(r'calendar-observation-review-\d{8}T\d{6}Z-[a-f0-9]{8}')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def iso(at):
    return datetime.fromtimestamp(at, ZONE).isoformat()


def epoch(value):
    date = datetime.fromisoformat(value)
    require(date.utcoffset() is not None, 'Evidence timestamp must include its timezone')
    return int(date.timestamp())


def readonly(path):
    conn = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    return conn


def read_cycles(root, start, end):
    cycles, missing = [], []
    day = datetime.fromtimestamp(start, ZONE).date()
    last = datetime.fromtimestamp(end, ZONE).date()
    require(0 <= (last-day).days <= 14, 'Review is limited to fifteen calendar days')
    while day <= last:
        path = root/'news'/f'{day.isoformat()}.db'
        if not path.is_file():
            missing.append(str(path))
            day += timedelta(days=1)
            continue
        with closing(readonly(path)) as conn:
            # A transaction keeps records and per-source statuses from one database snapshot.
            conn.execute('BEGIN')
            for index, row in enumerate(conn.execute('SELECT id,crawl_time,total_items FROM crawl_records ORDER BY id')):
                require(index < 10000, 'Unexpected crawl record volume')
                at = int(datetime.strptime(f'{day} {row["crawl_time"]}', '%Y-%m-%d %H-%M').replace(tzinfo=ZONE).timestamp())
                if start <= at <= end:
                    statuses = [dict(value) for value in conn.execute(
                        'SELECT platform_id,status FROM crawl_source_status WHERE crawl_record_id=? ORDER BY platform_id', (row['id'],))]
                    cycles.append({'at': at, 'time': iso(at), 'record_id': row['id'],
                                   'items': row['total_items'], 'sources': statuses})
        day += timedelta(days=1)
    return sorted(cycles, key=lambda row: (row['at'], row['record_id'])), missing


def summarize(cycles, start, finished):
    history = {}
    for cycle in cycles:
        for source in cycle['sources']:
            history.setdefault(source['platform_id'], []).append(
                {'at': cycle['at'], 'time': cycle['time'], 'status': source['status']})
    observed = [cycle for cycle in cycles if start <= cycle['at'] <= finished]
    failures = []
    for cycle in observed:
        for source in cycle['sources']:
            if source['status'] == 'success':
                continue
            later = [row for row in history[source['platform_id']] if row['at'] > cycle['at']]
            recovered = next((row for row in later if row['status'] == 'success'), None)
            latest = history[source['platform_id']][-1]
            failures.append({'source': source['platform_id'], 'at': cycle['at'], 'time': cycle['time'],
                             'status': source['status'], 'first_later_success': recovered,
                             'latest': latest, 'latest_success': latest['status'] == 'success'})
    return {'observation_cycles': len(observed), 'observation_source_failures': failures,
            'observation_cycles_with_incomplete_sources': [cycle['time'] for cycle in observed
                if len(cycle['sources']) != 9 or len({s['platform_id'] for s in cycle['sources']}) != 9],
            'source_latest': {source: rows[-1] for source, rows in history.items()},
            'followup_cycles': len(cycles)-len(observed),
            'followup_source_failures': [{'source': source['platform_id'], 'time': cycle['time'], 'status': source['status']}
                for cycle in cycles if cycle['at'] > finished for source in cycle['sources'] if source['status'] != 'success']}


def inspect(release_id, now=None):
    require(RELEASE_ID.fullmatch(release_id), 'Invalid calendar release identity')
    path = BACKUPS/release_id/'report.json'
    raw = path.read_bytes()
    report = json.loads(raw)
    require(report['id'] == release_id and report['stage'] == 'completed', 'Observation has not completed')
    start, finished = epoch(report['observation_started']), epoch(report['finished_at'])
    now = int(time.time()) if now is None else now
    require(start <= finished <= now, 'Invalid observation time range')
    cycles, missing = read_cycles(TREND, start, now)
    summary = summarize(cycles, start, finished)
    expected = report['trendradar']
    matches = (summary['observation_cycles'] == expected['cycles']
               and len(summary['observation_source_failures']) == expected['source_failures'])
    return {'release_id': release_id, 'original_report_sha256': hashlib.sha256(raw).hexdigest(),
            'original_status': report['status'], 'original_trendradar': expected,
            'observation_started': iso(start), 'observation_finished': iso(finished), 'inspected_at': iso(now),
            'active_release': str(Path('/srv/example/finevent/current').resolve()),
            'observed_release': report['deployment']['release'],
            'missing_databases': missing, 'original_counts_match': matches,
            **summary, 'cycles': cycles,
            'acceptance': 'not_decided',
            'limitations': 'Only stored crawl statuses; successful recovery does not prove complete news coverage or the cause of failures.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    require(REVIEW_ID.fullmatch(args.run_id), 'Invalid review identity')
    require(RELEASE_ID.fullmatch(args.release_id), 'Invalid calendar release identity')
    directory = BACKUPS/args.run_id
    directory.mkdir(mode=0o700)  # Never overwrite an earlier diagnostic.
    result = {'id': args.run_id, 'status': 'incomplete', 'release_id': args.release_id}
    try:
        result.update(inspect(args.release_id))
        result['status'] = 'completed_for_review'
    except Exception as error:
        result['failure'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        temporary = directory/'report.tmp'
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        temporary.replace(directory/'report.json')
        print(f'Evidence: {directory}/report.json')


if __name__ == '__main__':
    main()
