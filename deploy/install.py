"""Install the verification service; preserve the existing site and TrendRadar.

Run from an uploaded, checksum-verified release as root in an interactive SSH terminal.
"""

import argparse
from contextlib import closing
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import tempfile

ROOT = Path("/srv/example/finevent")
DATA = Path("/srv/example/finevent-data")
TREND = Path("/srv/example/trendradar/output")
SNIPPET = Path("/etc/nginx/snippets/example-app.conf")
HOST = "www.example.com"


def run(*args, capture=False, check=True):
    result = subprocess.run(args, text=True, capture_output=capture, check=False)
    if check and result.returncode:
        # Captured command failures must remain visible before rollback changes the evidence.
        if capture and result.stderr:
            print(result.stderr[-4096:], file=sys.stderr)
        result.check_returncode()
    return result


def probe_route(url, resolve=None):
    """Probe a public local route without curlrc, proxies, credentials or redirects."""
    with tempfile.TemporaryDirectory(prefix="finevent-http-") as temporary:
        body = Path(temporary) / "body"
        args = ["curl", "--disable", "--noproxy", "*", "--silent", "--show-error",
                "--connect-timeout", "3", "--max-time", "5", "--max-filesize", "1048576",
                "--output", str(body), "--write-out", "%{http_code}\n%{redirect_url}"]
        if resolve:
            args.extend(["--resolve", resolve])
        result = run(*args, url, capture=True, check=False)
        excerpt = ""
        if body.exists():
            with body.open("rb") as output:
                excerpt = output.read(2048).decode("utf-8", errors="replace")
        code, _, redirect = result.stdout.strip().partition("\n")
        return {"url": url, "curl_exit": result.returncode,
                "http_status": int(code) if re.fullmatch(r"\d{3}", code) else 0,
                "redirect_url": redirect, "body_excerpt": excerpt, "error": result.stderr[-1024:]}


def route_ready(result, health=False, redirect=None):
    if result["curl_exit"] != 0:
        return False
    if redirect is not None:
        return result["http_status"] == 308 and result.get("redirect_url") == redirect
    if result["http_status"] != 200:
        return False
    if not health:
        return True
    try:
        return json.loads(result["body_excerpt"]) == {"stage": "verification", "status": "ok"}
    except (ValueError, TypeError):
        return False


def wait_for_https(directory, attempts=6):
    # A successful reload only signals the master; allow time for new workers to serve requests.
    if attempts < 1:
        raise ValueError("HTTPS readiness requires at least one attempt")
    observations = []
    checks = [(HOST, "/finevent/healthz", None), (HOST, "/TrendRadar/content/", None),
              ("example.com", "/finevent/?probe=canonical", f"https://{HOST}/finevent/?probe=canonical")]
    for host, path, redirect in checks:
        for attempt in range(1, attempts + 1):
            result = probe_route(f"https://{host}{path}", f"{host}:443:127.0.0.1")
            result.update(attempt=attempt, observed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
            observations.append(result)
            (directory / "https-checks.json").write_text(json.dumps(observations, indent=2))
            if route_ready(result, health=path == "/finevent/healthz", redirect=redirect):
                print(f"HTTPS ready: {host}{path} (attempt {attempt}, HTTP {result['http_status']})", flush=True)
                break
            print(f"HTTPS not ready: {path} (attempt {attempt}/{attempts}, "
                  f"HTTP {result['http_status']}, curl exit {result['curl_exit']})", flush=True)
            if attempt == attempts:
                # Request only public endpoints; never collect an authenticated response or SSH session.
                local = probe_route("http://127.0.0.1:8787/finevent/healthz")
                (directory / "local-health.json").write_text(json.dumps(local, indent=2))
                print(json.dumps({"https_failure": result, "local_health": local}, indent=2), file=sys.stderr)
                raise RuntimeError(f"HTTPS route did not become ready: {path}; evidence: {directory}/https-checks.json")
            time.sleep(1)
    return observations


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def nginx_blocks(text, host):
    # Tokenize quoted values and comments before counting braces; regex locations may contain both.
    pattern = r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\#[^\n]*|[{};]|[^\s{};\#]+'''
    tokens = [(m.group(), m.start(), m.end()) for m in re.finditer(pattern, text)]
    tokens = [token for token in tokens if not token[0].startswith("#")]
    candidates = []
    for index, token in enumerate(tokens[:-1]):
        if token[0] != "server" or tokens[index + 1][0] != "{":
            continue
        depth, end, statement, directives = 1, None, [], []
        for current in tokens[index + 2:]:
            value = current[0]
            if value == "{":
                depth += 1
                statement = []
            elif value == "}":
                depth -= 1
                statement = []
                if depth == 0:
                    end = current[1]
                    break
            elif depth == 1 and value == ";":
                directives.append(statement)
                statement = []
            elif depth == 1:
                statement.append(value.strip("\"'"))
        names = any(d and d[0] == "server_name" and host in d[1:] for d in directives)
        tls = any(d and d[0] == "listen" and any(v == "443" or v.endswith(":443") for v in d[1:]) and "ssl" in d for d in directives)
        if names and tls and end is not None:
            candidates.append((token[1], end))
    return candidates


def patch_nginx(text, host=HOST):
    matches = nginx_blocks(text, host)
    if len(matches) != 1:
        raise RuntimeError("Need exactly one matching HTTPS server block")
    start, end = matches[0]
    block = text[start:end]
    directive = f"include {SNIPPET.as_posix()};"
    uncommented = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\#[^\n]*',
                         lambda match: "" if match.group().startswith("#") else match.group(), block)
    if directive in uncommented:
        return text
    if re.search(r"location\s+[^;{]*\/finevent(?:[/\s{]|$)", uncommented):
        raise RuntimeError("An existing /finevent route needs review; refusing to replace it")
    return text[:end] + f"\n    {directive}\n" + text[end:]


def find_site():
    result = run("nginx", "-T", capture=True)
    paths = set(re.findall(r"^# configuration file (.+):$", result.stdout, re.MULTILINE))
    matches = {}
    for name in paths:
        path = Path(name).resolve()
        if not path.is_relative_to(Path("/etc/nginx")):
            continue
        text = path.read_text()
        if nginx_blocks(text, HOST):
            matches[path] = text
    if len(matches) != 1:
        raise RuntimeError("Could not uniquely locate the existing HTTPS site; no configuration changed")
    path, text = next(iter(matches.items()))
    return path, patch_nginx(text)


def available_memory():
    return int(re.search(r"^MemAvailable:\s+(\d+)", Path("/proc/meminfo").read_text(), re.MULTILINE)[1]) * 1024


def save_state(directory, state):
    (directory / "state.json").write_text(json.dumps(state, indent=2))


def replace_file(path, content, directory, state):
    record = {"path": str(path), "backup": None, "installed_sha": hashlib.sha256(content).hexdigest()}
    if path.exists():
        original = path.stat()
        record["uid"] = original.st_uid
        record["gid"] = original.st_gid
        saved = directory / f"config-{len(state['files'])}"
        shutil.copy2(path, saved)
        record["backup"] = str(saved)
    state["files"].append(record)
    save_state(directory, state)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".finevent-", delete=False) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        staged = Path(temporary.name)
    staged.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
    if path.exists():
        original = path.stat()
        os.chown(staged, original.st_uid, original.st_gid)
    staged.replace(path)


def switch_release(target):
    link = ROOT / "next"
    if link.is_symlink():
        link.unlink()
    if link.exists():
        raise RuntimeError("Unexpected non-symlink release pointer")
    link.symlink_to(target)
    link.replace(ROOT / "current")


def restore_database_snapshot(source, target):
    # Validate before replacing the database or discarding any WAL sidecars.
    with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as saved:
        if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Selected database snapshot failed integrity check")
    expected = sha(source)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".finevent-restore-", delete=False) as temporary:
        staged = Path(temporary.name)
    try:
        shutil.copy2(source, staged)
        if sha(staged) != expected:
            raise RuntimeError("Restored database differs from the selected snapshot")
        for suffix in ("-wal", "-shm"):
            Path(str(target)+suffix).unlink(missing_ok=True)
        staged.replace(target)
        return expected
    finally:
        staged.unlink(missing_ok=True)


def pause_calendar_reminders(database):
    # Older binaries treat every delivery as a probe. Pause business deliveries
    # before starting a rollback version; retain history and all user data.
    if not database.exists():
        return 0
    with closing(sqlite3.connect(database)) as conn, conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='calendar_reminder_settings'").fetchone():
            return 0
        row = conn.execute("SELECT payload FROM calendar_reminder_settings WHERE id=1").fetchone()
        if row:
            settings = json.loads(row[0])
            settings['enabled'] = False
            conn.execute("UPDATE calendar_reminder_settings SET payload=? WHERE id=1", (json.dumps(settings),))
        return conn.execute("UPDATE deliveries SET status='cancelled',receipt_token='',error='版本回退，自动提醒已暂停' WHERE scenario='calendar' AND status IN ('queued','sending')").rowcount


def pause_news_push(database):
    if not database.exists():
        return 0
    with closing(sqlite3.connect(database)) as conn, conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_push_settings'").fetchone():
            return 0
        row = conn.execute('SELECT payload FROM news_push_settings WHERE id=1').fetchone()
        if row:
            settings = json.loads(row[0])
            settings['enabled'] = False
            conn.execute('UPDATE news_push_settings SET payload=? WHERE id=1', (json.dumps(settings),))
        return conn.execute("UPDATE deliveries SET status='cancelled',receipt_token='',error='版本回退，新闻推送已暂停' WHERE scenario='news' AND status IN ('queued','sending')").rowcount


def preserve_ai_ledger(source, target):
    """Keep irreversible charges when rolling business state back to a database backup."""
    if not source.exists() or not target.exists():
        return
    with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as latest:
        with closing(sqlite3.connect(target)) as restored, restored:
            tables = {row[0] for row in latest.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'ai_calls' in tables:
                restored.execute("""CREATE TABLE IF NOT EXISTS ai_calls(id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,month TEXT NOT NULL,started INTEGER NOT NULL,completed INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,reserved_nano INTEGER NOT NULL,cost_upper_nano INTEGER,prompt_tokens INTEGER,cached_tokens INTEGER,completion_tokens INTEGER,http_status INTEGER NOT NULL DEFAULT 0,pricing TEXT NOT NULL,price_basis TEXT NOT NULL DEFAULT 'reserved_peak')""")
            if 'ai_probe_runs' in tables:
                restored.execute("""CREATE TABLE IF NOT EXISTS ai_probe_runs(id TEXT PRIMARY KEY,month TEXT NOT NULL,started INTEGER NOT NULL,completed INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,reserved_nano INTEGER NOT NULL,cost_upper_nano INTEGER,prompt_tokens INTEGER,cached_tokens INTEGER,completion_tokens INTEGER,http_status INTEGER NOT NULL DEFAULT 0,duration_ms INTEGER NOT NULL DEFAULT 0,pricing TEXT NOT NULL)""")
            columns = {
                'ai_calls': ('id,job_id,month,started,completed,status,reserved_nano,cost_upper_nano,prompt_tokens,cached_tokens,completion_tokens,http_status,pricing,price_basis', ('job_id', 'month', 'started', 'reserved_nano', 'pricing')),
                'ai_probe_runs': ('id,month,started,completed,status,reserved_nano,cost_upper_nano,prompt_tokens,cached_tokens,completion_tokens,http_status,duration_ms,pricing', ('month', 'started', 'reserved_nano', 'pricing')),
            }
            for table, (fields, identity) in columns.items():
                if table not in tables:
                    continue
                names = fields.split(',')
                for row in latest.execute(f"SELECT {fields} FROM {table}"):
                    prior = restored.execute(f"SELECT {fields} FROM {table} WHERE id=?", (row[0],)).fetchone()
                    if prior and any(prior[names.index(name)] != row[names.index(name)] for name in identity):
                        raise RuntimeError('AI ledger identity differs from rollback backup; preserve evidence for review')
                    updates = ','.join(f'{name}=excluded.{name}' for name in names[1:])
                    restored.execute(f"INSERT INTO {table}({fields}) VALUES({','.join('?' for _ in names)}) ON CONFLICT(id) DO UPDATE SET {updates}", row)


def pause_ai(database):
    if not database.exists():
        return
    with closing(sqlite3.connect(database)) as conn, conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_settings'").fetchone():
            conn.execute('UPDATE ai_settings SET enabled=0')


def rollback(directory):
    import pwd
    directory = directory.resolve()
    if not directory.is_relative_to(Path("/srv/example/finevent-backups")):
        raise RuntimeError("Unexpected rollback directory")
    state = json.loads((directory / "state.json").read_text())
    # Do not overwrite configuration modified by the administrator after this release.
    for record in state["files"]:
        current_sha = sha(Path(record["path"]))
        old_sha = sha(Path(record["backup"])) if record["backup"] else None
        if current_sha not in (record["installed_sha"], old_sha):
            raise RuntimeError(f"Configuration changed after deployment: {record['path']}")
    run("systemctl", "stop", "finevent-backup.timer", "finevent.service", check=False, capture=True)
    run("systemctl", "disable", "finevent-backup.timer", "finevent.service", check=False, capture=True)
    for record in reversed(state["files"]):
        path = Path(record["path"])
        if record["backup"]:
            shutil.copy2(record["backup"], path)
            os.chown(path, record["uid"], record["gid"])
        elif path.exists():
            path.unlink()
    if state["previous"]:
        switch_release(Path(state["previous"]))
    elif (ROOT / "current").is_symlink():
        (ROOT / "current").unlink()
    database_sha = None
    if (directory / "database.db").exists():
        if (DATA / "finevent.db").exists():
            with closing(sqlite3.connect(f"{(DATA / 'finevent.db').as_uri()}?mode=ro", uri=True)) as failed:
                with closing(sqlite3.connect(directory / "failed-database.db")) as evidence:
                    failed.backup(evidence)
        database_sha = restore_database_snapshot(directory / "database.db", DATA / "finevent.db")
        preserve_ai_ledger(directory / "failed-database.db", DATA / "finevent.db")
        account = pwd.getpwnam("finevent")
        os.chown(DATA / "finevent.db", account.pw_uid, account.pw_gid)
    paused_reminders = pause_calendar_reminders(DATA / "finevent.db")
    paused_news = pause_news_push(DATA / "finevent.db")
    pause_ai(DATA / "finevent.db")
    database_sha = sha(DATA / "finevent.db") if (DATA / "finevent.db").exists() else None
    run("systemctl", "daemon-reload")
    if state.get("was_enabled"):
        run("systemctl", "enable", "finevent.service")
    if state.get("timer_enabled"):
        run("systemctl", "enable", "finevent-backup.timer")
    run("nginx", "-t", capture=True)
    run("systemctl", "reload", "nginx")
    if state["was_active"]:
        run("systemctl", "start", "finevent.service")
    if state["timer_active"]:
        run("systemctl", "start", "finevent-backup.timer")
    print(f"Previous release and site configuration restored. Evidence: {directory}")
    return {"database_sha256_before_start": database_sha, "previous_release": state["previous"], "calendar_reminders_paused": paused_reminders, "news_push_paused": paused_news}


def verify_bundle(bundle):
    manifest = json.loads((bundle / "sha256.json").read_text(encoding="utf-8-sig"))
    for relative, expected in manifest.items():
        path = (bundle / relative).resolve()
        if not path.is_relative_to(bundle.resolve()) or sha(path) != expected:
            raise RuntimeError(f"Release checksum mismatch: {relative}")
    for required in ("finevent", "dist/index.html", "deploy/finevent.service", "deploy/install.py"):
        if required not in manifest:
            raise RuntimeError(f"Missing release file: {required}")
    metadata = bundle / 'release.json'
    if metadata.exists() and json.loads(metadata.read_text(encoding='utf-8-sig')).get('kind') != 'deployment':
        raise RuntimeError('Render the generic archive with deployment settings before installation')


def install(*, evidence_callback=None):
    import pwd
    if os.geteuid() != 0:
        raise RuntimeError("Run in the authorized root SSH session")
    os.umask(0o077)
    bundle = Path(__file__).resolve().parent.parent
    verify_bundle(bundle)
    site, patched_site = find_site()
    if available_memory() < 128 * 1024 * 1024:
        raise RuntimeError("Less than 128 MiB available before startup; no service changes made")
    if shutil.disk_usage("/opt").free < 1024**3:
        raise RuntimeError("Less than 1 GiB free; no service changes made")
    was_active = run("systemctl", "is-active", "--quiet", "finevent.service", check=False).returncode == 0
    timer_active = run("systemctl", "is-active", "--quiet", "finevent-backup.timer", check=False).returncode == 0
    was_enabled = run("systemctl", "is-enabled", "--quiet", "finevent.service", check=False, capture=True).returncode == 0
    timer_enabled = run("systemctl", "is-enabled", "--quiet", "finevent-backup.timer", check=False, capture=True).returncode == 0
    with socket.socket() as connection:
        if connection.connect_ex(("127.0.0.1", 8787)) == 0 and not was_active:
            raise RuntimeError("Port 8787 belongs to another service")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = Path("/srv/example/finevent-backups") / stamp
    directory.mkdir(parents=True)
    previous = str((ROOT / "current").resolve()) if (ROOT / "current").is_symlink() else None
    if (ROOT / "current").exists() and previous is None:
        raise RuntimeError("Existing current path is not a release symlink")
    state = {"previous": previous, "was_active": was_active, "timer_active": timer_active, "was_enabled": was_enabled, "timer_enabled": timer_enabled, "files": []}
    save_state(directory, state)
    if evidence_callback is not None:
        evidence_callback(directory)
    release = ROOT / "releases" / stamp
    release.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bundle, release)
    # Release assets must be readable by the service user; only state/backups contain secrets.
    for path in [ROOT, release.parent, release, *release.rglob("*")]:
        path.chmod(0o755 if path.is_dir() or path.name == "finevent" else 0o644)
    if run("id", "-u", "finevent", capture=True, check=False).returncode:
        run("useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", "finevent")
    account = pwd.getpwnam("finevent")
    DATA.mkdir(mode=0o700, exist_ok=True)
    os.chown(DATA, account.pw_uid, account.pw_gid)
    sample_files = sorted((TREND / "news").glob("*.db"))[-2:]
    if not sample_files:
        raise RuntimeError("Recent TrendRadar databases not found; existing site unchanged")
    for sample in sample_files:
        if run("runuser", "-u", "finevent", "--", "test", "-r", str(sample), check=False).returncode:
            raise RuntimeError(f"finevent cannot read {sample}. Grant that user read/traverse ACLs on this data path, then rerun; no source permissions changed automatically")
    if not (DATA / "config.json").exists():
        run("runuser", "-u", "finevent", "--", str(release / "finevent"), "-init", "-data", str(DATA), "-origin", f"https://{HOST}")
    try:
        run("systemctl", "stop", "finevent-backup.timer", "finevent.service", check=False, capture=True)
        if (DATA / "finevent.db").exists():
            with closing(sqlite3.connect(DATA / "finevent.db")) as source:
                with closing(sqlite3.connect(directory / "database.db")) as output:
                    source.backup(output)
                    if output.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("Pre-deploy backup failed integrity check")
        switch_release(release)
        for unit in ("finevent.service", "finevent-backup.service", "finevent-backup.timer"):
            replace_file(Path("/etc/systemd/system") / unit, (release / "deploy" / unit).read_bytes(), directory, state)
        replace_file(SNIPPET, (release / "deploy/finevent.nginx.conf").read_bytes(), directory, state)
        replace_file(site, patched_site.encode(), directory, state)
        run("nginx", "-t", capture=True)
        run("systemctl", "daemon-reload")
        run("systemctl", "start", "finevent.service")
        readings = []
        for _ in range(6):
            time.sleep(10)
            run("systemctl", "is-active", "--quiet", "finevent.service")
            available = available_memory()
            readings.append(available)
            if available < 64 * 1024 * 1024:
                raise RuntimeError("Host available memory below 64 MiB; reverting the probe deployment")
            local = probe_route("http://127.0.0.1:8787/finevent/healthz")
            if not route_ready(local, health=True):
                (directory / "local-health.json").write_text(json.dumps(local, indent=2))
                raise RuntimeError(f"Local health check failed: {json.dumps(local)}")
        group = run("systemctl", "show", "finevent", "-p", "ControlGroup", "--value", capture=True).stdout.strip()
        events = Path("/sys/fs/cgroup" + group) / "memory.events"
        if events.exists() and re.search(r"^oom_kill\s+[1-9]", events.read_text(), re.MULTILINE):
            raise RuntimeError("Observed OOM termination; reverting")
        run("systemctl", "reload", "nginx")
        wait_for_https(directory)
        run("systemctl", "enable", "finevent.service", "finevent-backup.timer")
        run("systemctl", "start", "finevent-backup.timer")
        report = {"status": "verification_service_installed", "url": f"https://{HOST}/finevent/", "release": str(release), "backup": str(directory), "available_mib": [round(n / 1024**2, 1) for n in readings], "real_device_push": "pending", "two_trading_days": "pending", "ai_concurrent_load": "pending"}
        (directory / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        print(f"Rollback: python3 {release}/deploy/install.py --rollback {directory}")
        return directory
    except BaseException:
        print("Deployment failed; restoring the previous service/site configuration.", file=sys.stderr)
        rollback(directory)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if args.rollback:
        rollback(args.rollback)
    else:
        install()
