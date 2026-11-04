"""Synthetic HTTP inputs for the audited 6.11.0 pipeline; never use production config."""

from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

SOURCES = ('cls-hot', 'ifeng', 'juejin', 'cankaoxiaoxi', 'zaobao',
           'wallstreetcn-quick', 'v2ex', 'cls-telegraph', 'cls-depth')
PINNED = {
    'trendradar/__main__.py': '0e8a50e315b08d9741af41969d2348c8aec8ff95b7f7ce32e9c9600939221440',
    'trendradar/core/loader.py': '6b704b3e2d91429cd2b8fa91effff9432a9fd6b45945e77351d46c2878fbb3e9',
    'trendradar/crawler/fetcher.py': 'dc4faeaf345a9a564b3b89cf93e0c3265387eec7d9d93a0113fcd9f20770a6ea',
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify_source(root):
    for name, expected in PINNED.items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Workflow fixture requires the audited 6.11.0 source: '+name)


def prepare(directory):
    directory.mkdir()  # Must be a new isolated directory, not an existing output tree.
    config = {
        'app': {'timezone': 'Asia/Shanghai', 'show_version_update': False},
        'platforms': {'enabled': True, 'api_url': 'https://fixture.example.test/api/s',
                      'sources': [{'id': source, 'name': source, 'expected_domain': 'example.test'} for source in SOURCES]},
        'schedule': {'enabled': False, 'preset': 'custom'}, 'report': {'mode': 'daily', 'display_mode': 'keyword'},
        'filter': {'method': 'keyword'}, 'rss': {'enabled': False},
        'ai_analysis': {'enabled': False}, 'ai_translation': {'enabled': False},
        'notification': {'enabled': False},
        'storage': {'backend': 'local', 'formats': {'sqlite': True, 'html': True, 'txt': False},
                    'local': {'data_dir': 'output', 'retention_days': 0}},
        'advanced': {'debug': True, 'crawler': {'request_interval': 50, 'use_proxy': False}},
    }
    # JSON is valid YAML, avoiding any extra host dependency.
    (directory/'config.yaml').write_text(json.dumps(config), encoding='utf-8')
    (directory/'frequency_words.txt').write_text('[GLOBAL_FILTER]\n\n[WORD_GROUPS]\n验证财经\n', encoding='utf-8')
    # An image ENV must not enable AI, change output paths or inject notification credentials.
    path = os.environ.get('PATH', '')
    system_root = os.environ.get('SystemRoot', '')  # Required by Windows Winsock during local rehearsal.
    os.environ.clear()
    os.environ.update(PATH=path, HOME=str(directory), PYTHONDONTWRITEBYTECODE='1',
                      DOCKER_CONTAINER='true', CONFIG_PATH=str(directory/'config.yaml'),
                      FREQUENCY_WORDS_PATH=str(directory/'frequency_words.txt'))
    if os.name == 'nt' and system_root:
        os.environ['SystemRoot'] = system_root
    os.chdir(directory)


class FixedTime(datetime):
    current = datetime.fromisoformat('2026-10-27T10:00:00+08:00')

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


def payload(source, round_number):
    title = '验证财经 '+source+(' 修订' if round_number else ' 初稿')
    items = [{'title': title, 'url': 'https://example.test/'+source+'/1'},
             {'title': title, 'url': 'https://example.test/'+source+'/1'},
             {'title': None}, {'title': ''}]
    if round_number:
        items.append({'title': '验证财经 '+source+' 新增', 'url': 'https://example.test/'+source+'/2'})
    return {'status': 'cache' if round_number else 'success', 'items': items}


def snapshot(directory):
    databases = sorted((directory/'output').rglob('*.db'))
    if len(databases) != 1 or databases[0].relative_to(directory).as_posix() != 'output/news/2026-10-27.db':
        raise RuntimeError('Unexpected fixture database files')
    with closing(sqlite3.connect(databases[0].as_uri()+'?mode=ro', uri=True)) as connection:
        if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise RuntimeError('Fixture SQLite failed integrity check')
        schema = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        tables = {}
        for kind, name, _, _ in schema:
            if kind != 'table':
                continue
            if not re.fullmatch('[a-z_]+', name):
                raise RuntimeError('Unexpected fixture table identifier')
            rows = connection.execute('SELECT * FROM "'+name+'"').fetchall()
            tables[name] = sorted(rows, key=lambda row: json.dumps(row, ensure_ascii=False))
        # The actual projection used by finevent's read-only TrendRadar importer.
        imported = connection.execute("SELECT id,title,platform_id,COALESCE(url,''),first_crawl_time,last_crawl_time FROM news_items ORDER BY id").fetchall()
        if len(imported) != 17 or len(tables['title_changes']) != 8 or len(tables['crawl_records']) != 2:
            raise RuntimeError('New/repeated/revised fixture news did not persist as expected')
        failures = connection.execute("SELECT platform_id FROM crawl_source_status WHERE status='failed'").fetchall()
        if failures != [('cls-depth',)] or len(tables['crawl_source_status']) != 18:
            raise RuntimeError('Fixture source failure was not recorded')
        if any(row[4] not in ('10-00', '10-30') or row[5] not in ('10-00', '10-30') for row in imported):
            raise RuntimeError('Unexpected crawl time format for finevent')
    html = {}
    for file in sorted((directory/'output').rglob('*.html')):
        data = file.read_bytes()
        if len(data) > 2*1024**2:
            raise RuntimeError('Fixture HTML exceeded the bounded size')
        html[file.relative_to(directory).as_posix()] = hashlib.sha256(data).hexdigest()
    latest = directory/'output/html/latest/daily.html'
    if len(html) < 4 or not latest.is_file():
        raise RuntimeError('Expected HTML snapshots and latest daily report are missing')
    content = latest.read_text(encoding='utf-8')
    for source in SOURCES[:-1]:
        if '验证财经 '+source+' 修订' not in content or '验证财经 '+source+' 新增' not in content:
            raise RuntimeError('Updated fixture headlines are missing from HTML')
    return {'database_path': databases[0].relative_to(directory).as_posix(),
            'schema_sha256': digest(schema), 'tables_sha256': digest(tables),
            'table_counts': {name: len(rows) for name, rows in tables.items()},
            'finevent_projection_sha256': digest(imported), 'finevent_rows': len(imported),
            'html_sha256': html}


def run(directory):
    # Import hooks and filesystem/network isolation are established by the parent runner.
    import requests
    import trendradar.__main__ as application
    import trendradar.crawler.fetcher as fetcher
    import trendradar.storage.manager as manager
    import trendradar.utils.time as time_helpers
    time_helpers.datetime = FixedTime
    fetcher.time = SimpleNamespace(sleep=lambda _: None)  # Retry delay only, not process/watchdog clocks.
    calls, unexpected = [], []
    round_number = 0

    def request(_session, method, url, **kwargs):
        parsed = urlsplit(url)
        source = parse_qs(parsed.query).get('id', [''])[0]
        if method.lower() != 'get' or parsed.netloc != 'fixture.example.test' or parsed.path != '/api/s' or source not in SOURCES:
            unexpected.append(True)
            raise RuntimeError('Unexpected network request in offline fixture')
        calls.append([round_number, source])
        response = requests.Response()
        response.status_code = 503 if round_number and source == 'cls-depth' else 200
        response._content = json.dumps(payload(source, round_number), ensure_ascii=False).encode()
        response.encoding = 'utf-8'
        response.url = url
        return response

    requests.sessions.Session.request = request
    for round_number in (0, 1):
        FixedTime.current = datetime.fromisoformat('2026-10-27T10:'+('30' if round_number else '00')+':00+08:00')
        manager._storage_manager = None  # Match independent cron executions; prior run closes its connections.
        application.NewsAnalyzer().run()
    if unexpected or len(calls) != 20:
        raise RuntimeError('Unexpected fixture HTTP calls or retry count')
    result = snapshot(directory)
    result.update(rounds=2, http_requests=len(calls), failed_source='cls-depth',
                  production_data_used=False, source_files=PINNED)
    return result
