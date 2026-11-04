"""News-specific acceptance on the existing backed-up, reversible release workflow."""

from contextlib import closing, contextmanager
from datetime import datetime
import hashlib
import json
import re
import secrets
import sqlite3
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import calendar_release as calendar
import memory_recovery as recovery

OBSERVATION_SECONDS = 65*60
INDEX_TIMEOUT = 20*60


def state(rule_version, path=None):
    with closing(calendar.readonly(path or calendar.DATA/'finevent.db')) as conn:
        conn.execute('BEGIN')
        total, maximum = conn.execute('SELECT COUNT(*),COALESCE(MAX(id),0) FROM news').fetchone()
        backlog = conn.execute('SELECT COUNT(*) FROM news_processing').fetchone()[0]
        stale = conn.execute('SELECT COUNT(*) FROM news n LEFT JOIN news_features f ON f.news_id=n.id '
                             'WHERE f.news_id IS NULL OR f.item_version<>n.version OR f.rule_version<>?', (rule_version,)).fetchone()[0]
        invalid = conn.execute('SELECT COUNT(*) FROM news_features WHERE NOT json_valid(payload)').fetchone()[0]
        identity = conn.execute('SELECT last_id FROM news_identity WHERE singleton=1').fetchone()[0]
        watermark = conn.execute('SELECT COALESCE(MAX(seq),0) FROM news_changes').fetchone()[0]
        unknown = conn.execute('SELECT COUNT(*) FROM news WHERE published_at=0').fetchone()[0]
        calendar.require(not invalid and identity >= maximum, 'Invalid news classification or identity state')
        for table in ('news_features', 'news_labels', 'news_processing', 'news_revision_evidence'):
            calendar.require(not conn.execute(f'PRAGMA foreign_key_check({table})').fetchone(), 'Broken news evidence reference')
        return {'items': total, 'backlog': backlog, 'stale': stale, 'unknown_publication': unknown,
                'max_id': maximum, 'identity': identity, 'watermark': watermark, 'rule_version': rule_version}


def verify_preservation(before, live=None):
    """Compare a frozen backup with a live read transaction; concurrent revisions are legitimate."""
    with closing(calendar.readonly(live or calendar.DATA/'finevent.db')) as conn:
        conn.execute('ATTACH DATABASE ? AS old', (before.resolve().as_uri()+'?mode=ro',))
        conn.execute('BEGIN')
        cutoff = int(time.time())-90*86400
        columns = {r['name'] for r in conn.execute('PRAGMA old.table_info(news)')}
        tables = {r[0] for r in conn.execute("SELECT name FROM old.sqlite_master WHERE type='table'")}
        ingested = 'b.ingested_at' if 'ingested_at' in columns else '0'
        updated = 'b.updated_at' if 'updated_at' in columns else '0'
        if 'updated_at' in columns and 'news_features' in tables:
            updated = '(CASE WHEN EXISTS(SELECT 1 FROM old.news_features f WHERE f.news_id=b.id AND f.item_version<>b.version) THEN 0 ELSE b.updated_at END)'
        missing = conn.execute('SELECT COUNT(*) FROM old.news b LEFT JOIN news n ON n.id=b.id '
                               'WHERE b.last_seen>=? AND (n.id IS NULL OR n.source<>b.source OR n.source_id<>b.source_id '
                               'OR n.first_seen<>b.first_seen OR n.version<b.version OR n.ingested_at<>'+ingested+
                               ' OR (n.version=b.version AND n.updated_at<>'+updated+'))', (cutoff,)).fetchone()[0]
        changed = conn.execute('''SELECT COUNT(*) FROM old.news b JOIN news n ON n.id=b.id WHERE b.last_seen>=? AND
            ((n.version=b.version AND (n.title<>b.title OR n.content<>b.content OR n.url<>b.url OR n.published_at<>b.published_at OR n.hash<>b.hash))
            OR (n.version>b.version AND NOT EXISTS(SELECT 1 FROM news_revisions r JOIN news_revision_evidence e
                ON e.news_id=r.news_id AND e.version=r.version WHERE r.news_id=b.id AND r.version=b.version
                AND r.title=b.title AND r.content=b.content AND e.url=b.url AND e.published_at=b.published_at)))''', (cutoff,)).fetchone()[0]
        lost_versions = conn.execute('''SELECT COUNT(*) FROM old.news_revisions b JOIN old.news old_news ON old_news.id=b.news_id
            WHERE old_news.last_seen>=? AND NOT EXISTS(SELECT 1 FROM news_revisions r WHERE r.news_id=b.news_id AND r.version=b.version
            AND r.title=b.title AND r.content=b.content AND r.saved_at=b.saved_at)''', (cutoff,)).fetchone()[0]
        if 'news_revision_evidence' in tables:
            lost_versions += conn.execute('''SELECT COUNT(*) FROM old.news_revision_evidence b JOIN old.news old_news ON old_news.id=b.news_id
                WHERE old_news.last_seen>=? AND NOT EXISTS(SELECT 1 FROM news_revision_evidence e WHERE e.news_id=b.news_id
                AND e.version=b.version AND e.url=b.url AND e.published_at=b.published_at)''', (cutoff,)).fetchone()[0]
        calendar.require(missing == changed == lost_versions == 0, 'Existing news or revision evidence changed during migration/restart')
        counts = conn.execute('SELECT COUNT(*),SUM(last_seen>=?) FROM old.news', (cutoff,)).fetchone()
        return {'status': 'passed', 'before_items': counts[0], 'checked_retained_items': counts[1] or 0,
                'missing_or_changed': missing+changed+lost_versions, 'unknown_legacy_ingestion_preserved': True}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@contextmanager
def api_client(path=None):
    """Root-only, short-lived session for local read APIs; never exported or retained."""
    path = path or calendar.DATA/'finevent.db'
    token = secrets.token_urlsafe(32)
    hashed = hashlib.sha256(token.encode()).hexdigest()
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with closing(sqlite3.connect(path, timeout=5)) as conn:
        conn.execute('INSERT INTO sessions(token,expires) VALUES(?,?)', (hashed, int(time.time())+120))
        conn.commit()
    def get(endpoint, authenticated=True):
        link_endpoint = re.fullmatch(r'calendar/[a-zA-Z0-9_-]{1,128}/news-links|calendar-link-search\?from=\d{4}-\d{2}-\d{2}&to=\d{4}-\d{2}-\d{2}', endpoint)
        calendar.require((endpoint.startswith('news') or link_endpoint) and not any(c in endpoint for c in '\r\n#'), 'Invalid local read endpoint')
        headers = {'Cookie': 'finevent_session='+token} if authenticated else {}
        request = Request('http://127.0.0.1:8787/finevent/api/v1/'+endpoint, headers=headers)
        try:
            with opener.open(request, timeout=5) as response:
                data = response.read(2*1024*1024+1)
                calendar.require(len(data) <= 2*1024*1024, 'News API response exceeds verification limit')
                calendar.require('no-store' in response.headers.get('Cache-Control', ''), 'News API unexpectedly cacheable')
                return response.status, json.loads(data)
        except HTTPError as error:
            status = error.code
            error.close()
            return status, None
    try:
        yield get
    finally:
        with closing(sqlite3.connect(path, timeout=5)) as conn:
            conn.execute('DELETE FROM sessions WHERE token=?', (hashed,))
            conn.commit()


def probe_api(ready=True):
    with api_client() as get:
        calendar.require(get('news', False)[0] == 401, 'Anonymous news API access was not rejected')
        code, meta = get('news/meta')
        calendar.require(code == 200 and isinstance(meta.get('rule_version'), str) and meta['rule_version']
                         and meta.get('collection_enabled') and meta.get('trendradar_enabled'), 'News metadata or collection flags invalid')
        code, feed = get('news?order=discovered')
        calendar.require(code == 200 and 0 < len(feed.get('items', [])) <= 30, 'Live news feed is empty or invalid')
        calendar.require(all(row.get('content') == '' for row in feed['items']), 'Feed unexpectedly contains full bodies')
        item = feed['items'][0]
        code, detail = get('news/'+str(item['id']))
        calendar.require(code == 200 and detail['item']['id'] == item['id'] and detail['item']['source_id'] == item['source_id']
                         and detail['item']['version'] >= item['version'] and 'revisions' in detail and 'related' in detail, 'News detail identity invalid')
        if ready:
            calendar.require(not feed['backlog'] and all((row.get('classification') or {}).get('rule_version') == meta['rule_version']
                             for row in feed['items']), 'News feed classification is incomplete')
        pages = 1
        if feed['next']:
            code, second = get('news?'+urlencode({'order': 'discovered', 'cursor': feed['next']}))
            calendar.require(code == 200 and not ({r['id'] for r in second['items']} & {r['id'] for r in feed['items']}), 'News pagination repeats identities')
            pages += 1
        code, filtered = get('news?'+urlencode({'source': item['source'], 'order': 'discovered'}))
        calendar.require(code == 200 and filtered['items'] and all(r['source'] == item['source'] for r in filtered['items']), 'News source filter failed')
        code, changes = get('news/changes?after='+str(feed['watermark']))
        calendar.require(code == 200 and changes['watermark'] >= feed['watermark'], 'News incremental cursor regressed')
        return {'status': 'passed', 'rule_version': meta['rule_version'], 'pages': pages,
                'sample_id': item['id'], 'sample_source': item['source'], 'anonymous_status': 401,
                'detail': True, 'source_filter': True, 'incremental': True, 'api_cache': 'no-store'}


def wait_index(directory, report, current, version):
    pid = calendar.service_pid()
    baseline = calendar.sample(current, pid)
    samples = []
    started = time.monotonic()
    with (directory/'news-index-samples.jsonl').open('a', encoding='utf-8') as stream:
        while time.monotonic()-started < INDEX_TIMEOUT:
            row = calendar.sample(current, pid)
            samples.append(row)
            stream.write(json.dumps(row)+'\n')
            stream.flush()
            report['news_initial_resources'] = calendar.resource_summary(samples, baseline)
            calendar.check_sample(row, report['preflight']['baseline'])
            report['news_index'] = {**state(version), 'wait_seconds': round(time.monotonic()-started, 1)}
            recovery.save(directory/'report.json', report)
            if report['news_index']['stale'] == report['news_index']['backlog'] == 0:
                return
            calendar.require(report['news_index']['backlog'] > 0, 'Unclassified news has no durable indexing task')
            time.sleep(5)
    raise RuntimeError('Historical news indexing did not finish within twenty minutes')


def prepare(directory, report, current, backup):
    import calendar_links_release
    report['calendar_links_required'] = True
    link_cutoff = calendar_links_release.retention_cutoff()
    report['calendar_links_migration'] = calendar_links_release.verify(calendar_links_release.state(backup/'database.db', link_cutoff), calendar_links_release.state(calendar.DATA/'finevent.db', link_cutoff))
    import news_push_release
    import source_health_release
    report['source_health_required'] = True
    cutoff = source_health_release.retention_cutoff()
    report['source_health_migration'] = source_health_release.verify(source_health_release.state(backup/'database.db', cutoff), source_health_release.state(calendar.DATA/'finevent.db', cutoff))
    report['source_health_api'] = source_health_release.probe(ready=False)
    report['news_push_required'] = True
    report['news_push_migration'] = news_push_release.verify(news_push_release.state(backup/'database.db'), news_push_release.state(calendar.DATA/'finevent.db'))
    report['stage'] = 'news_index_and_migration'
    recovery.save(directory/'report.json', report)
    version = probe_api(ready=False)['rule_version']
    wait_index(directory, report, current, version)
    report['news_migration'] = verify_preservation(backup/'database.db')
    report['news_installation_available_mib'] = json.loads((backup/'report.json').read_text())['available_mib']
    report['news_api'] = probe_api()
    before = directory/'news-before-restart.db'
    calendar.snapshot(calendar.DATA/'finevent.db', before)
    before_state = state(version, before)
    health_before = source_health_release.state(before, cutoff)
    links_before = calendar_links_release.state(before, link_cutoff)
    report['restart'] = calendar.restart_check(directory, report, current)
    report['news_restart'] = verify_preservation(before)
    report['news_push_restart'] = news_push_release.verify(news_push_release.state(before), news_push_release.state(calendar.DATA/'finevent.db'))
    report['news_push_api'] = news_push_release.probe()
    report['source_health_restart'] = source_health_release.verify(health_before, source_health_release.state(calendar.DATA/'finevent.db', cutoff))
    report['calendar_links_restart'] = calendar_links_release.verify(links_before, calendar_links_release.state(calendar.DATA/'finevent.db', link_cutoff))
    report['calendar_links_api'] = calendar_links_release.probe()
    after_state = state(version)
    calendar.require(after_state['backlog'] == after_state['stale'] == 0 and after_state['identity'] >= before_state['identity']
                     and after_state['watermark'] >= before_state['watermark'], 'News index or durable identity regressed across restart')
    report['news_restart'].update(before=before_state, after=after_state)
    report['news_api'] = probe_api()


def polls(start, end, path=None):
    result = {}
    with closing(calendar.readonly(path or calendar.DATA/'finevent.db')) as conn:
        for source in ('wallstreetcn', 'trendradar'):
            row = conn.execute('''SELECT COUNT(*) AS attempts,COALESCE(SUM(success),0) AS successes,
                COALESCE(SUM(added+changed),0) AS changes,COALESCE(SUM(gap<>''),0) AS gaps,
                MAX(CASE WHEN success=1 THEN completed END) AS last_success FROM polls
                WHERE source=? AND started>=? AND completed<=?''', (source, start, end)).fetchone()
            result[source] = dict(row)
    return result


def checks_passed(report):
    if report.get('calendar_links_required'):
        import calendar_links_release
        if not calendar_links_release.checks_passed(report):
            return False
    if report.get('source_health_required'):
        import source_health_release
        if not source_health_release.checks_passed(report):
            return False
    if report.get('news_push_required'):
        import news_push_release
        if not news_push_release.checks_passed(report):
            return False
    index = report.get('news_index', {})
    samples = report.get('news_installation_available_mib', [])
    observed = report.get('news_polls', {})
    return (all(report.get(key, {}).get('status') == 'passed' for key in ('news_migration', 'news_restart', 'news_api'))
            and index.get('items', 0) > 0 and index.get('backlog') == index.get('stale') == 0
            and bool(samples) and min(samples) >= 64
            and calendar.resource_checks_passed({'resources': report.get('news_initial_resources', {})})
            and report.get('news_api_samples', 0) >= 60
            and set(observed) == {'wallstreetcn', 'trendradar'}
            and all(v['successes'] > 0 and v['gaps'] == 0 and v['last_success']
                    and report['news_poll_end']-v['last_success'] <= 300 for v in observed.values())
            and sum(v['changes'] for v in observed.values()) > 0)


def observe(directory, report, current):
    pid = calendar.service_pid()
    identities = recovery.process_identity(current['pid']), recovery.process_identity(pid)
    baseline = calendar.sample(current, pid)
    calendar.check_sample(baseline, report['preflight']['baseline'])
    started = int(time.time())
    end = started+OBSERVATION_SECONDS
    report.update(status='observing', stage='news_and_resources', observation_started=calendar.stamp(),
                  planned_end=datetime.fromtimestamp(end, calendar.ZONE).isoformat(), news_api_samples=0,
                  calendar_verification='Existing calendar/reminders preserved; next six-hour synchronization is not retested.')
    samples, last_probe = [], 0
    with (directory/'samples.jsonl').open('a', encoding='utf-8') as stream:
        while time.time() < end:
            recovery.validate_live_processes(current, pid, identities)
            row = calendar.sample(current, pid)
            samples.append(row)
            stream.write(json.dumps(row)+'\n')
            report['resources'] = calendar.resource_summary(samples, baseline)
            calendar.check_sample(row, baseline)
            if time.time()-last_probe >= 60:
                last_probe = time.time()
                report['news_api'] = probe_api()
                if report.get('calendar_links_required'):
                    import calendar_links_release
                    report['calendar_links_api'] = calendar_links_release.probe()
                if report.get('source_health_required'):
                    import source_health_release
                    report['source_health_api'] = source_health_release.probe(ready=time.time()-started >= 180)
                if report.get('news_push_required'):
                    import news_push_release
                    report['news_push_api'] = news_push_release.probe()
                report['news_api_samples'] += 1
                report['news_index'] = state(report['news_api']['rule_version'])
                report['news_polls'] = polls(started, int(time.time()))
                recovery.save(directory/'report.json', report)
                stream.flush()
            time.sleep(5)
    report['news_api'] = probe_api()
    report['news_index'] = state(report['news_api']['rule_version'])
    if report.get('calendar_links_required'):
        import calendar_links_release
        report['calendar_links_api'] = calendar_links_release.probe()
    if report.get('news_push_required'):
        import news_push_release
        report['news_push_api'] = news_push_release.probe()
    report['news_poll_end'] = int(time.time())
    if report.get('source_health_required'):
        import source_health_release
        report['source_health_api'] = source_health_release.probe()
    report['news_polls'] = polls(started, report['news_poll_end'])
    report['trendradar'] = calendar.trend_outputs(started, report['news_poll_end'])
    report['health'] = recovery.public_health()
    report['calendar_sources'] = calendar.calendar_state()['sources']
    calendar.validate_target(calendar.target(), report['preflight']['container'], calendar.parent_metrics())
    calendar.require(recovery.sha(calendar.DATA/'config.json') == report['preflight']['config_sha256'], 'finevent secrets changed during verification')
    report['news_acceptance'] = {'status': 'passed' if checks_passed(report) else 'needs_review',
                                'two_trading_days_retested': False, 'ai_load_tested': False}
    good = calendar.runtime_passed(report) and checks_passed(report)
    report.update(status='completed_for_review' if good else 'needs_review', stage='completed', finished_at=calendar.stamp())
