"""First business-AI release: one real news request, no paid retry or bulk enablement."""

from contextlib import closing, contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

import calendar_release as calendar
import memory_recovery as recovery
import news_release as news
from verification_resources import trend_jobs

WAIT_SECONDS = 32*60
FOLLOW_SECONDS = 180
POST_SECONDS = 300
AI_TABLES = ('ai_settings', 'ai_jobs', 'ai_links', 'ai_calls', 'ai_call_timings')


def database():
    return calendar.DATA/'finevent.db'


def preflight():
    calendar.require(not (calendar.DATA/'ai-config.json').exists(),
                     'First AI release requires an unconfigured service; review an existing configuration before retrying')
    with closing(calendar.readonly(database())) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('ai_jobs', 'ai_calls'):
            calendar.require(table not in tables or conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0,
                             'Existing business AI work requires review before this one-request release')
        if 'ai_settings' in tables:
            calendar.require(conn.execute('SELECT enabled OR automatic FROM ai_settings WHERE singleton=1').fetchone()[0] == 0,
                             'AI must be paused before release')
        calendar.require(not conn.execute("SELECT 1 FROM ai_probe_runs WHERE status='running' LIMIT 1").fetchone(),
                         'A previous paid verification request is still unresolved')


def stage_key(directory, bundle=None):
    # Direct inherited TTY; neither the key nor the subprocess output is captured.
    stage = directory/'private-ai-input'
    stage.mkdir(mode=0o700)
    binary = (bundle or Path(__file__).resolve().parent.parent)/'finevent'
    # Windows-built tar members do not carry an executable bit. The launcher verified
    # this private bundle's manifest before reaching here; content is not changed.
    binary.chmod(0o700)
    result = subprocess.run([str(binary), '-configure-ai', '-data', str(stage)], check=False)
    calendar.require(result.returncode == 0, 'Hidden AI configuration was not completed; no model request was sent')


def install_key(directory):
    import pwd
    source = directory/'private-ai-input/ai-config.json'
    calendar.require(source.is_file() and not source.is_symlink() and source.stat().st_mode & 0o077 == 0
                     and source.stat().st_size <= 16384, 'Private staged AI configuration is unavailable')
    data = source.read_bytes()
    config = json.loads(data)
    # This first-release experiment uses the reviewed price table, never arbitrary staged rates.
    calendar.require(config['model'] == 'deepseek-flash' and config['peak_pricing'] ==
                     {'version': 'deepseek-2026-11-01', 'input_nano_per_token': 2000,
                      'cached_nano_per_token': 40, 'output_nano_per_token': 8000},
                     'The staged model or prices differ from the reviewed first-release configuration')
    target = calendar.DATA/'ai-config.json'
    account = pwd.getpwnam('finevent')
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchown(stream.fileno(), account.pw_uid, account.pw_gid)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    source.unlink()
    return {'mode': '0600', 'owner': 'finevent', 'model': config['model'],
            'price_version': config['peak_pricing']['version'], 'prices_valid_until': config['prices_valid_until']}


@contextmanager
def api_client(path=None):
    """Short-lived local session with narrowly scoped writes; never persist/export the token."""
    path = path or database()
    token = secrets.token_urlsafe(32)
    hashed = hashlib.sha256(token.encode()).hexdigest()
    opener = build_opener(ProxyHandler({}), news.NoRedirect())
    with closing(sqlite3.connect(path, timeout=5)) as conn:
        conn.execute('INSERT INTO sessions(token,expires) VALUES(?,?)', (hashed, int(time.time())+300))
        conn.commit()
    def request(endpoint, method='GET', body=None, authenticated=True):
        valid = ((method == 'GET' and re.fullmatch(r'ai|ai/analyses/[a-f0-9]{64}|news/[1-9][0-9]*/analysis', endpoint))
                 or (method == 'PUT' and endpoint == 'ai/settings' and body in
                     ({'enabled': True, 'automatic': False}, {'enabled': False, 'automatic': False}))
                 or (method == 'POST' and re.fullmatch(r'news/[1-9][0-9]*/analysis', endpoint) and body == {'retry': False}))
        calendar.require(valid, 'Invalid AI verification endpoint or write')
        headers = {'Origin': 'https://www.example.com', 'Content-Type': 'application/json'}
        if authenticated:
            headers['Cookie'] = 'finevent_session='+token
        req = Request('http://127.0.0.1:8787/finevent/api/v1/'+endpoint, method=method, headers=headers,
                      data=None if body is None else json.dumps(body).encode())
        try:
            with opener.open(req, timeout=5) as response:
                data = response.read(512*1024+1)
                calendar.require(len(data) <= 512*1024 and 'no-store' in response.headers.get('Cache-Control', ''),
                                 'AI API is oversized or unexpectedly cacheable')
                return response.status, json.loads(data)
        except HTTPError as error:
            status = error.code
            error.close()
            return status, None
    try:
        yield request
    finally:
        with closing(sqlite3.connect(path, timeout=5)) as conn:
            conn.execute('DELETE FROM sessions WHERE token=?', (hashed,))
            conn.commit()


def state(path=None):
    with closing(calendar.readonly(path or database())) as conn:
        conn.execute('BEGIN')
        values = {table: [tuple(r) for r in conn.execute('SELECT * FROM '+table+' ORDER BY 1,2')]
                  for table in AI_TABLES}
        # Only counts and hashes leave the machine report, never source text or configuration.
        return {'counts': {table: len(rows) for table, rows in values.items()},
                'sha256': hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()}


def migration(before):
    with closing(calendar.readonly(before)) as old, closing(calendar.readonly(database())) as live:
        prior = [tuple(row) for row in old.execute('SELECT * FROM ai_probe_runs ORDER BY id')]
        current = [tuple(row) for row in live.execute('SELECT * FROM ai_probe_runs ORDER BY id')]
        calendar.require(prior == current, 'Historical AI probe ledger changed during migration')
        calendar.require(live.execute('SELECT COUNT(*) FROM ai_budget_entries').fetchone()[0] == len(prior),
                         'Budget view omitted a historical call or contains unexpected new charges')
    result = state()
    calendar.require(result['counts'] == {table: int(table == 'ai_settings') for table in AI_TABLES},
                     'Initial business AI tables are not empty and paused')
    return {'status': 'passed', 'prior_probe_entries': len(prior), **result}


def overview():
    with api_client() as request:
        calendar.require(request('ai', authenticated=False)[0] == 401, 'Anonymous AI API access was not rejected')
        code, result = request('ai')
    calendar.require(code == 200 and result['configured'] and result['model'] == 'deepseek-flash'
                     and result['settings'] == {'enabled': False, 'automatic': False} and result['queued'] == 0
                     and result['budget_nano'] == 30_000_000_000,
                     'AI API configuration, budget or paused state is invalid')
    with closing(calendar.readonly(database())) as conn:
        spent = conn.execute('SELECT COALESCE(SUM(COALESCE(cost_upper_nano,reserved_nano)),0) FROM ai_budget_entries WHERE month=?',
                             (result['month'],)).fetchone()[0]
    calendar.require(result['estimated_nano']+result['reserved_nano'] == spent
                     and result['remaining_nano'] == max(0, result['budget_nano']-spent), 'AI API and durable budget disagree')
    return {k: result[k] for k in ('month', 'model', 'price_version', 'budget_nano', 'estimated_nano',
                                  'reserved_nano', 'probe_nano', 'remaining_nano')}


def prepare(directory, report, current, backup):
    report['stage'] = 'ai_migration_and_configuration'
    recovery.save(directory/'report.json', report)
    report['ai_migration'] = migration(backup/'database.db')
    report['ai_configuration'] = install_key(directory)
    # Existing news preparation includes a restart, loading the key with AI still paused.
    news.prepare(directory, report, current, backup)
    report['ai_budget_before'] = overview()
    calendar.require(report['ai_budget_before']['remaining_nano'] >= 500_000_000,
                     'Less than 0.50 CNY remains; no paid verification request will be sent')


def pause():
    with closing(sqlite3.connect(database(), timeout=5)) as conn:
        conn.execute('UPDATE ai_settings SET enabled=0,automatic=0 WHERE singleton=1')
        conn.commit()


def calls():
    with closing(calendar.readonly(database())) as conn:
        return [dict(r) for r in conn.execute('''SELECT c.id,c.job_id,c.month,c.status,c.reserved_nano,c.cost_upper_nano,
            c.prompt_tokens,c.cached_tokens,c.completion_tokens,c.http_status,c.price_basis,t.started_ms,t.completed_ms
            FROM ai_calls c LEFT JOIN ai_call_timings t ON t.call_id=c.id ORDER BY c.id''')]


def candidate():
    with closing(calendar.readonly(database())) as conn:
        row = conn.execute('''SELECT n.id FROM news n JOIN news_features f ON f.news_id=n.id AND f.item_version=n.version
            WHERE n.source='wallstreetcn' AND n.published_at>=? AND n.published_at<=?
            AND length(n.content) BETWEEN 100 AND 2000 AND n.url LIKE 'https://%'
            ORDER BY f.importance DESC,n.published_at DESC LIMIT 1''', (int(time.time())-86400, int(time.time()))).fetchone()
    calendar.require(row is not None, 'No recent public financial news with enough source text; no paid request sent')
    return row[0]


class Watch:
    def __init__(self, directory, report, current):
        self.current, self.pid = current, calendar.service_pid()
        self.identities = recovery.process_identity(current['pid']), recovery.process_identity(self.pid)
        self.report = report
        self.baseline = calendar.sample(current, self.pid)
        calendar.check_sample(self.baseline, report['preflight']['baseline'])
        self.rows, self.error = [], None
        self.stream = (directory/'ai-resource-samples.jsonl').open('a', encoding='utf-8')

    def take(self):
        start = time.time_ns()//1_000_000
        recovery.validate_live_processes(self.current, self.pid, self.identities)
        jobs_before = trend_jobs(self.current['cgroup']['path'])
        row = calendar.sample(self.current, self.pid)
        jobs_after = trend_jobs(self.current['cgroup']['path'])
        row.update(trend_jobs=[job for job in jobs_before if job in jobs_after], sample_started_ms=start,
                   sample_completed_ms=time.time_ns()//1_000_000)
        self.rows.append(row)
        self.stream.write(json.dumps(row)+'\n')
        self.stream.flush()
        calendar.check_sample(row, self.baseline)
        return row

    def check(self):
        if self.error:
            raise self.error

    @contextmanager
    def fast(self):
        stop = threading.Event()
        def work():
            try:
                while not stop.is_set():
                    self.take()
                    stop.wait(0.1)
            except BaseException as error:
                self.error = error
        thread = threading.Thread(target=work)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join()
        self.check()

    def save(self, directory):
        self.report['resources'] = calendar.resource_summary(list(self.rows), self.baseline)
        recovery.save(directory/'report.json', self.report)


def overlap(rows, call, triggered):
    begin, end = call.get('started_ms') or 0, call.get('completed_ms') or 0
    return sum(0 < begin <= r['sample_started_ms'] <= r['sample_completed_ms'] < end
               and any(job in r['trend_jobs'] for job in triggered) for r in rows)


def one_request(directory, report, watch, triggered):
    item_id = candidate()
    calendar.require(not calls(), 'A business call already exists; this operation will not send another')
    report.update(stage='ai_one_request', ai_news_id=item_id, ai_triggered_jobs=triggered)
    recovery.save(directory/'report.json', report)
    try:
        with watch.fast(), api_client() as request:
            calendar.require(request('ai/settings', 'PUT', {'enabled': True, 'automatic': False})[0] == 200,
                             'Could not enable manual AI for the single request')
            code, result = request('news/'+str(item_id)+'/analysis', 'POST', {'retry': False})
            calendar.require(code == 202 and re.fullmatch('[a-f0-9]{64}', result['id']), 'Single news request was not queued')
            report['ai_job_id'] = result['id']
            recovery.save(directory/'report.json', report)
            deadline, paused = time.monotonic()+FOLLOW_SECONDS, False
            while time.monotonic() < deadline:
                watch.check()
                rows = calls()
                calendar.require(len(rows) <= 1 and all(r['job_id'] == result['id'] for r in rows),
                                 'Unexpected additional AI work; review the ledger before retrying')
                if rows and not paused:
                    pause()  # The claimed request finishes; no next job may start.
                    paused = True
                live = trend_jobs(watch.current['cgroup']['path'])
                if rows and rows[0]['status'] != 'running' and not any(p in live for p in triggered):
                    code, job = request('ai/analyses/'+result['id'])
                    calendar.require(code == 200 and job['id'] == result['id'], 'Completed AI detail unavailable')
                    report['ai_result'] = {'status': job['status'], 'attempts': job['attempts'], 'stale': job['stale'],
                                           'facts': len((job['result'] or {}).get('facts', [])),
                                           'impacts': len((job['result'] or {}).get('impacts', [])),
                                           'evidence': len(job['evidence']), 'content_review': 'pending'}
                    report['ai_call'] = rows[0]
                    report['ai_overlap_samples'] = overlap(list(watch.rows), rows[0], triggered)
                    return
                time.sleep(0.2)
            raise RuntimeError('The single call or TrendRadar follow-up did not finish; no automatic paid retry')
    finally:
        pause()
        report['paid_model_calls'] = len(calls())
        watch.save(directory)


def checks_passed(report):
    call = report.get('ai_call', {})
    result = report.get('ai_result', {})
    after = report.get('ai_budget_after', {})
    before = report.get('ai_budget_before', {})
    poll = report.get('news_polls', {})
    return (report.get('ai_migration', {}).get('status') == 'passed'
            and report.get('ai_restart', {}).get('status') == 'passed'
            and report.get('paid_model_calls') == 1 and call.get('status') == 'succeeded'
            and result.get('status') == 'succeeded' and result.get('attempts') == 1 and not result.get('stale', True)
            and result.get('facts', 0) > 0 and result.get('evidence', 0) > 0
            and isinstance(call.get('cost_upper_nano'), int)
            and 0 <= call['cost_upper_nano'] <= call.get('reserved_nano', -1) <= 500_000_000
            and call.get('http_status') == 200 and report.get('ai_overlap_samples', 0) > 0
            and before.get('month') == after.get('month') == call.get('month')
            and after.get('estimated_nano', -1)-before.get('estimated_nano', -1) == call.get('cost_upper_nano')
            and after.get('reserved_nano') == before.get('reserved_nano')
            and after.get('probe_nano') == before.get('probe_nano')
            and report.get('news_migration', {}).get('status') == report.get('news_restart', {}).get('status') == 'passed'
            and report.get('news_api', {}).get('status') == 'passed'
            and calendar.resource_checks_passed({'resources': report.get('news_initial_resources', {})})
            and set(poll) == {'wallstreetcn', 'trendradar'}
            and all(v['successes'] > 0 and v['gaps'] == 0 and v['last_success']
                    and report['news_poll_end']-v['last_success'] <= 180 for v in poll.values()))


def observe(directory, report, current):
    started = int(time.time())
    report.update(status='observing', stage='ai_wait_natural_collection', observation_started=calendar.stamp(),
                  planned_end=datetime.fromtimestamp(started+WAIT_SECONDS+FOLLOW_SECONDS+POST_SECONDS+90, calendar.ZONE).isoformat(),
                  calendar_verification='Prior source-cycle acceptance retained; only preservation/restart retested.')
    watch = Watch(directory, report, current)
    try:
        initial = trend_jobs(current['cgroup']['path'])
        deadline, last_sample, last_save = time.monotonic()+WAIT_SECONDS, 0, 0
        while time.monotonic() < deadline:
            if time.monotonic()-last_sample >= 5:
                watch.take()
                last_sample = time.monotonic()
            if time.monotonic()-last_save >= 30:
                watch.save(directory)
                last_save = time.monotonic()
            triggered = [job for job in trend_jobs(current['cgroup']['path']) if job not in initial]
            if triggered:
                row = watch.take()
                calendar.require(row['available'] >= 128*calendar.MIB and any(p in row['trend_jobs'] for p in triggered),
                                 'Natural collection ended or headroom insufficient before the request; no paid retry')
                one_request(directory, report, watch, triggered)
                break
            time.sleep(0.25)
        else:
            raise RuntimeError('No new normal TrendRadar job within 32 minutes; no paid request sent')
        report['stage'] = 'ai_post_request_collection'
        end = time.monotonic()+POST_SECONDS
        while time.monotonic() < end:
            watch.take()
            watch.save(directory)
            time.sleep(5)
    finally:
        pause()
        if watch.rows:
            watch.save(directory)
        watch.stream.close()
    report['news_poll_end'] = int(time.time())
    report['news_polls'] = news.polls(started, report['news_poll_end'])
    report['trendradar'] = calendar.trend_outputs(started, report['news_poll_end'], minimum_cycles=1)
    before_restart = state()
    calendar.require(before_restart['counts']['ai_calls'] == 1 and len(calls()) == report['paid_model_calls'],
                     'Unexpected additional AI calls during follow-up; review the retained ledger')
    calendar.snapshot(database(), directory/'ai-before-restart.db')
    report['ai_service_restart'] = calendar.restart_check(directory, report, current)
    calendar.require(before_restart == state(), 'AI result, evidence or billing ledger changed across restart')
    report['ai_restart'] = {'status': 'passed', **before_restart}
    report['ai_budget_after'] = overview()
    report['news_api'] = news.probe_api()
    report['health'] = recovery.public_health()
    calendar.validate_target(calendar.target(), report['preflight']['container'], calendar.parent_metrics())
    calendar.require(recovery.sha(calendar.DATA/'config.json') == report['preflight']['config_sha256'],
                     'Existing finevent secrets changed during verification')
    report['ai_acceptance'] = {'status': 'passed' if checks_passed(report) else 'needs_review',
                               'automatic_enabled': False, 'human_content_review': 'pending',
                               'full_month_load_tested': False, 'calendar_cycle_retested': False}
    good = checks_passed(report) and calendar.runtime_passed(report)
    report.update(status='completed_for_review' if good else 'needs_review', stage='completed', finished_at=calendar.stamp())
