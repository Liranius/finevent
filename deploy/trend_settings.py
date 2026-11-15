"""Local, socket-activated editor for the two supported TrendRadar 6.11 files."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import tempfile

CONFIG = Path('/srv/example/trendradar/config')
STATE = Path('/var/lib/finevent-trend-settings')
FILES = {'sources': 'config.yaml', 'keywords': 'frequency_words.txt'}
WORD_LIMIT = 128 * 1024


class Invalid(ValueError):
    pass


class Conflict(Exception):
    pass


def read_file(path, limit):
    fd = os.open(path, os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_NONBLOCK',0) | getattr(os,'O_BINARY',0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('Unsupported configuration file')
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('Configuration too large')
        return raw, info


def revision(raw, ns):
    return hashlib.sha256(str(ns).encode() + b':' + raw).hexdigest()


def sources(raw):
    import yaml
    config = yaml.safe_load(raw)
    platforms = config['platforms']
    if platforms.get('enabled', True) is not True:
        raise Invalid('TrendRadar 热榜总开关已关闭，请先核对部署配置。')
    result = []
    seen = set()
    for source in platforms['sources']:
        key = source['id']
        enabled = source.get('enabled', True)
        if not isinstance(key, str) or not key or key in seen or type(enabled) is not bool:
            raise ValueError('Invalid source identity or flag')
        seen.add(key)
        result.append(dict(id=key, name=source.get('name') or key, enabled=enabled))
    return result


def enabled_ids(rows):
    return [s['id'] for s in rows if s['enabled']]


def edit_sources(raw, selected):
    """Change scalar values in place, retaining comments, formatting and other sections."""
    import yaml
    rows = sources(raw)
    if (not isinstance(selected, list) or not all(isinstance(i, str) for i in selected)
            or len(set(selected)) != len(selected) or set(selected) - {s['id'] for s in rows}):
        raise Invalid('来源选项无效，请重新读取配置。')
    text = raw.decode('utf-8')
    expected = yaml.safe_load(text)
    # Detach the desired subtree so aliases elsewhere cannot silently change other settings.
    expected['platforms'] = dict(expected['platforms'])
    expected['platforms']['sources'] = [dict(s) for s in expected['platforms']['sources']]
    root = yaml.compose(text)
    def field(node, key):
        return next(v for k, v in node.value if k.value == key)
    nodes = field(field(root, 'platforms'), 'sources')
    edits = []
    for item, node in zip(expected['platforms']['sources'], nodes.value, strict=True):
        active = item['id'] in selected
        if active == item.get('enabled', True):
            continue
        item['enabled'] = active
        flag = 'true' if active else 'false'
        old = next((v for k, v in node.value if k.value == 'enabled'), None)
        if old is not None:
            edits.append((old.start_mark.index, old.end_mark.index, flag))
        elif node.flow_style:
            at = node.start_mark.index + 1
            edits.append((at, at, 'enabled: ' + flag + ', '))
        else:
            first = node.value[0][0].start_mark
            newline = '\r\n' if '\r\n' in text else '\n'
            edits.append((first.index, first.index, 'enabled: ' + flag + newline + ' ' * first.column))
    for start, end, value in sorted(edits, reverse=True):
        text = text[:start] + value + text[end:]
    if yaml.safe_load(text) != expected or set(enabled_ids(sources(text))) != set(selected):
        raise Invalid('当前 YAML 结构无法单独修改来源，请核对来源定义。')
    return text.encode()


def keywords(value):
    if not isinstance(value, str) or len(value.encode()) > WORD_LIMIT or '\x00' in value:
        raise Invalid('关键词文件须为不超过 128 KiB 的文本。')
    for number, line in enumerate(value.splitlines(), 1):
        term = line.strip()
        if term.startswith('#'):
            continue
        term = term.removeprefix('+') if term.startswith('+') else term.removeprefix('!')
        pattern = re.fullmatch(r'/(.+)/[a-z]*', term.split('=>', 1)[0].strip())
        if pattern:
            try:
                re.compile(pattern[1], re.IGNORECASE)
            except (re.error, OverflowError, RecursionError) as error:
                raise Invalid(f'第 {number} 行正则表达式无效。') from error
    return value.encode()


def snapshot(folder, db):
    raw, info = read_file(folder / FILES['sources'], 1024 * 1024)
    words, word_info = read_file(folder / FILES['keywords'], WORD_LIMIT)
    source_rev = revision(raw, info.st_mtime_ns)
    word_rev = revision(words, word_info.st_mtime_ns)
    return dict(sources=sources(raw), keywords=words.decode('utf-8'), source_revision=source_rev,
                keyword_revision=word_rev, source_since=info.st_mtime_ns / 1e9,
                undo_sources=previous(db, 'sources', source_rev) is not None,
                undo_keywords=previous(db, 'keywords', word_rev) is not None)


def previous(db, section, rev):
    row = db.execute('SELECT previous FROM changes WHERE section=? AND revision=?', (section, rev)).fetchone()
    return json.loads(row[0]) if row else None


def periods(db, raw, info):
    records = [dict(start=start, end=end, ids=json.loads(ids)) for start, end, ids in
               db.execute("SELECT start,end,previous FROM changes WHERE section='sources' ORDER BY start,end")]
    rows = sources(raw)
    records.append(dict(start=info.st_mtime_ns / 1e9, end=None, ids=enabled_ids(rows),
                        catalog=[s['id'] for s in rows]))
    return records


def observation_periods(folder=CONFIG, state=STATE):
    """Read-only baseline also works before the first settings-capable release is installed."""
    if not (state / 'history.db').exists():
        raw, info = read_file(folder / FILES['sources'], 1024 * 1024)
        rows = sources(raw)
        return [dict(start=info.st_mtime_ns / 1e9, end=None, ids=enabled_ids(rows))]
    import fcntl
    with (state / 'lock').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        raw, info = read_file(folder / FILES['sources'], 1024 * 1024)
        with closing(sqlite3.connect((state/'history.db').as_uri()+'?mode=ro', uri=True)) as db:
            return periods(db, raw, info)


def expected_at(records, at):
    # The DB records completion to the minute. The process loads YAML at its half-hour launch.
    launch = at - at % 1800
    return next((r['ids'] for r in reversed(records) if r['start'] <= launch
                 and (r['end'] is None or launch < r['end'])), None)


def update(folder, db, request):
    section = request.get('section')
    if section not in FILES:
        raise Invalid('设置项无效。')
    target = folder / FILES[section]
    raw, info = read_file(target, WORD_LIMIT if section == 'keywords' else 1024 * 1024)
    rev = revision(raw, info.st_mtime_ns)
    if request.get('revision') != rev:
        raise Conflict()
    old = enabled_ids(sources(raw)) if section == 'sources' else raw.decode('utf-8')
    value = request.get('value')
    if request.get('undo') is True:
        value = previous(db, section, rev)
        if value is None:
            raise Conflict()
    data = edit_sources(raw, value) if section == 'sources' else keywords(value)
    if data == raw:
        return
    staged = tempfile.NamedTemporaryFile(dir=folder, prefix='.finevent-', delete=False)
    tmp = Path(staged.name)
    try:
        with staged as output:
            output.write(data)
            output.flush()
            if hasattr(os, 'fchown'):
                os.fchown(output.fileno(), info.st_uid, info.st_gid)
            os.chmod(tmp, stat.S_IMODE(info.st_mode))
            if hasattr(os, 'listxattr'):
                existing = set(os.listxattr(tmp))
                for name in os.listxattr(target, follow_symlinks=False):
                    value = os.getxattr(target, name, follow_symlinks=False)
                    if name not in existing or os.getxattr(tmp, name) != value:
                        os.setxattr(tmp, name, value)
            os.fsync(output.fileno())
        now_ns = tmp.stat().st_mtime_ns
        current, latest = read_file(target, 1024 * 1024)
        if revision(current, latest.st_mtime_ns) != rev:
            raise Conflict()
        # Commit undo and the old source interval first. A failed rename leaves the old
        # revision active and its undo intact. An unused new revision cannot be restored.
        with db:
            db.execute("DELETE FROM changes WHERE section=? AND revision<>? AND (section='keywords' OR end<?)",
                       (section, rev, now_ns / 1e9 - 30 * 86400))
            db.execute('INSERT INTO changes VALUES (?,?,?,?,?)',
                       (section, revision(data, now_ns), json.dumps(old), info.st_mtime_ns / 1e9, now_ns / 1e9))
        os.replace(tmp, target)
        if os.name == 'posix':
            fd = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        tmp.unlink(missing_ok=True)


def handle(request, folder=CONFIG, state=STATE):
    with (state / 'lock').open('a+b') as lock:
        if os.name == 'posix':
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        with closing(sqlite3.connect(state / 'history.db')) as db:
            db.execute('CREATE TABLE IF NOT EXISTS changes(section TEXT, revision TEXT, previous TEXT, '
                       'start REAL, end REAL, PRIMARY KEY(section, revision))')
            op = request.get('op')
            if op == 'periods':
                raw, info = read_file(folder / FILES['sources'], 1024 * 1024)
                return periods(db, raw, info)
            if op == 'write':
                update(folder, db, request)
            elif op != 'read':
                raise Invalid('未知配置操作。')
            return snapshot(folder, db)


def main():
    try:
        line = sys.stdin.buffer.readline(1024 * 1024)
        if not line.endswith(b'\n'):
            raise Invalid('配置请求过大或不完整。')
        result = dict(status=200, data=handle(json.loads(line)))
    except Conflict:
        result = dict(status=409, error='配置已更改，请重新读取后再保存。')
    except Invalid as error:
        result = dict(status=400, error=str(error))
    except Exception as error:
        # Parser errors can contain credentials from neighbouring lines.
        print(type(error).__name__, file=sys.stderr)
        result = dict(status=503, error='无法读取或写入 TrendRadar 配置，请核对服务器配置。')
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode('utf-8') + b'\n')


if __name__ == '__main__':
    main()
