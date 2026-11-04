"""Read-only inventory for slimming TrendRadar; never imports its application modules."""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

import memory_diagnostic as diagnostic

MIB = 1024**2
BACKUPS = Path('/srv/example/finevent-backups')
CONFIG_RULES = {
    'app.timezone': 'token', 'app.show_version_update': 'bool',
    'schedule.enabled': 'bool', 'schedule.preset': 'token',
    'platforms.enabled': 'bool', 'rss.enabled': 'bool',
    'report.mode': 'token', 'report.display_mode': 'token', 'filter.method': 'token',
    'ai_analysis.enabled': 'bool', 'ai_analysis.save_result': 'bool',
    'ai_translation.enabled': 'bool', 'notification.enabled': 'bool',
    'storage.backend': 'token', 'storage.formats.sqlite': 'bool',
    'storage.formats.html': 'bool', 'storage.formats.txt': 'bool',
    'storage.local.retention_days': 'number',
    'display.regions.rss': 'bool', 'display.regions.standalone': 'bool',
    'display.regions.ai_analysis': 'bool',
}
ENV_RULES = {
    'ENABLE_CRAWLER': 'bool', 'ENABLE_NOTIFICATION': 'bool',
    'AI_ANALYSIS_ENABLED': 'bool', 'AI_TRANSLATION_ENABLED': 'bool',
    'FILTER_METHOD': 'token', 'REPORT_MODE': 'token', 'STORAGE_BACKEND': 'token',
    'SAVE_HTML': 'bool', 'SAVE_TXT': 'bool', 'SAVE_SQLITE': 'bool',
    'RSS_ENABLED': 'bool', 'PLATFORMS_ENABLED': 'bool', 'SCHEDULE_ENABLED': 'bool',
    'RUN_MODE': 'token', 'IMMEDIATE_RUN': 'bool', 'ENABLE_WEBSERVER': 'bool',
    'CRON_SCHEDULE': 'cron', 'TZ': 'token',
}


def allowed_value(value, rule):
    if value is None:
        return None
    if rule == 'bool':
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ('true', 'false'):
            return value.lower() == 'true'
    elif rule == 'number' and isinstance(value, int) and not isinstance(value, bool):
        return value
    elif rule == 'token' and isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_./-]{1,80}', value):
        return value
    elif rule == 'cron' and isinstance(value, str) and re.fullmatch(r'[0-9*/ ,\-]{5,80}', value) and len(value.split()) == 5:
        return value
    return {'status': 'unrecognized_value_redacted'}


def selected_config(config, environment):
    """Export only typed operational fields; never export credentials, endpoints or feed URLs."""
    selected = {}
    for key, rule in CONFIG_RULES.items():
        value = config
        for part in key.split('.'):
            value = value.get(part) if isinstance(value, dict) else None
        selected[key] = allowed_value(value, rule)
    sources = {}
    for section, key in (('platforms', 'sources'), ('rss', 'feeds')):
        block = config.get(section, {})
        rows = block.get(key, []) if isinstance(block, dict) else []
        sources[section] = []
        if isinstance(rows, list):
            for row in rows[:100]:
                if isinstance(row, dict):
                    sources[section].append({'id': allowed_value(row.get('id'), 'token'),
                                             'enabled_explicit': allowed_value(row.get('enabled'), 'bool')})
            sources[section+'_truncated'] = len(rows) > 100
    return {'yaml_fields': selected, 'sources': sources,
            'environment_overrides': {key: allowed_value(environment[key], rule)
                                      for key, rule in ENV_RULES.items() if key in environment},
            'interpretation': 'YAML and environment are separate evidence; null is unspecified, not disabled. Review the installed loader before inferring effective values.'}


def command(*args, input_text=None):
    result = subprocess.run(args, input=input_text, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(args[0]+' failed with exit '+str(result.returncode))
    if len(result.stdout) > 2*MIB:
        raise RuntimeError('Command output exceeded the audit limit')
    return result.stdout


def container_inventory():
    template = ('{"id":{{json .Id}},"name":{{json .Name}},"image_id":{{json .Image}},'
                '"image":{{json .Config.Image}},"pid":{{.State.Pid}},"running":{{.State.Running}},'
                '"memory":{{.HostConfig.Memory}},"memory_swap":{{.HostConfig.MemorySwap}},'
                '"compose_files":{{json (index .Config.Labels "com.docker.compose.project.config_files")}},'
                '"ports":{{json .NetworkSettings.Ports}},'
                '"mounts":[{{range $i,$m := .Mounts}}{{if $i}},{{end}}'
                '{"source":{{json $m.Source}},"destination":{{json $m.Destination}},"writable":{{$m.RW}}}{{end}}]}')
    ids = command('docker', 'ps', '-aq', '--no-trunc').splitlines()
    if len(ids) > 30 or any(not re.fullmatch('[a-f0-9]{64}', value) for value in ids):
        raise RuntimeError('Unexpected container inventory; inspect manually')
    return [json.loads(command('docker', 'inspect', '--format', template, value)) for value in ids]


def application_identity(argv):
    """Only script/module identity for interpreters; never return an arbitrary command line."""
    if not argv:
        return None
    interpreter = Path(argv[0]).name
    if not re.fullmatch(r'(python[0-9.]*|node|nodejs)', interpreter):
        return None
    for index, value in enumerate(argv[1:8], 1):
        if value in ('-c', '-e', '--eval'):
            return {'inline_code': 'redacted'}
        if value == '-m':
            module = argv[index+1] if len(argv) > index+1 else ''
            return {'module': module} if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]{0,120}', module) else None
        if value in ('-u', '-B', '-I', '-S', '-s', '-E'):
            continue
        if re.fullmatch(r'[A-Za-z0-9_./-]{1,240}\.(py|js|mjs)', value):
            return {'script': value}
        return None
    return None


def process_inventory(proc=Path('/proc')):
    rows = diagnostic.process_memory(proc)
    for row in rows:
        entry = proc/str(row['pid'])
        try:
            row['ppid'] = diagnostic.read_numbers(entry/'status', {'PPid'}).get('PPid')
            row['executable'] = os.readlink(entry/'exe')
            row['cgroup_membership'] = (entry/'cgroup').read_text().splitlines()
            row['application'] = application_identity((entry/'cmdline').read_bytes().decode(errors='replace').strip('\0').split('\0'))
            row['proportional_memory_bytes'] = diagnostic.read_numbers(entry/'smaps_rollup', {'Pss', 'Pss_Anon', 'Pss_File', 'Pss_Shmem', 'SwapPss'})
        except OSError:
            row['identity_status'] = 'unavailable_or_process_exited'
    return rows


def import_inventory(path):
    body = path.read_bytes()
    if len(body) > MIB:
        return {'status': 'too_large'}
    tree = ast.parse(body, filename=path.name)
    imports = []
    environment_names = []
    version = None

    def visit(node, deferred=False, guarded=False):
        nonlocal version
        if isinstance(node, ast.Import):
            imports.extend({'line': node.lineno, 'module': item.name, 'function_scope': deferred, 'guarded': guarded} for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append({'line': node.lineno, 'module': '.'*node.level+(node.module or ''),
                            'names': [item.name for item in node.names], 'function_scope': deferred, 'guarded': guarded})
        elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) and re.fullmatch(r'[0-9A-Za-z.+-]{1,30}', node.value.value):
                version = node.value.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ('getenv', 'get'):
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                key = node.args[0].value
                if re.fullmatch(r'[A-Z][A-Z0-9_]{1,79}', key):
                    environment_names.append({'line': node.lineno, 'key': key})
        deferred = deferred or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        guarded = guarded or isinstance(node, (ast.If, ast.Try, ast.For, ast.While, ast.Match))
        for child in ast.iter_child_nodes(node):
            visit(child, deferred, guarded)
    visit(tree)
    return {'sha256': hashlib.sha256(body).hexdigest(), 'size_bytes': len(body),
            'version_literal': version, 'imports': imports, 'configuration_key_references': environment_names}


def source_inventory(root):
    package = root/'trendradar'
    paths = sorted(package.rglob('*.py'))
    result = {'files': {}, 'package_exists': package.is_dir(), 'truncated': len(paths) > 160,
              'meaning': 'Static import locations only; does not execute imports or establish runtime memory cost.'}
    for path in paths[:160]:
        if path.is_symlink():
            continue
        try:
            result['files'][str(path.relative_to(root))] = import_inventory(path)
        except (OSError, SyntaxError, ValueError):
            result['files'][str(path.relative_to(root))] = {'status': 'unreadable_or_unparseable'}
    requirements = root/'requirements.txt'
    if requirements.is_file() and requirements.stat().st_size < 65536:
        # URLs, local paths and custom indexes may contain credentials and are never exported.
        lines = requirements.read_text().splitlines()
        safe = [line.strip() for line in lines if re.fullmatch(r'[A-Za-z0-9_.\[\],<>=!~ -]{1,180}', line.strip()) and not line.startswith('-')]
        result['requirements'] = safe
        result['requirements_sha256'] = hashlib.sha256(requirements.read_bytes()).hexdigest()
    entrypoint = root/'docker/entrypoint.sh'
    if entrypoint.is_file() and entrypoint.stat().st_size < 65536:
        body = entrypoint.read_text()
        result['entrypoint'] = {'sha256': hashlib.sha256(body.encode()).hexdigest(),
                                'mentions_start_webserver': 'start_webserver' in body,
                                'mentions_python_module': 'python -m trendradar' in body}
    return result


def read_config_in_container(container_id):
    # Only stdlib plus the installed YAML parser. Do not import trendradar or litellm.
    import inspect
    payload = ('import os, re, json, sys\nfrom pathlib import Path\nimport yaml\n'
               'from importlib import metadata\n'
               'CONFIG_RULES = '+repr(CONFIG_RULES)+'\nENV_RULES = '+repr(ENV_RULES)+'\n'
               +inspect.getsource(allowed_value)+'\n'+inspect.getsource(selected_config)+'\n'+'''
result = {}
try:
    path = Path('/app/config/config.yaml')
    if path.stat().st_size > 1048576:
        raise ValueError('oversize')
    config = yaml.safe_load(path.read_text())
    if not isinstance(config, dict):
        raise ValueError('invalid root')
    result = selected_config(config, os.environ)
    result['python_version'] = sys.version.split()[0]
    result['package_versions'] = {}
    for name in ('litellm', 'pydantic', 'openai', 'httpx', 'requests', 'PyYAML'):
        try:
            result['package_versions'][name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            result['package_versions'][name] = None
    result['status'] = 'selected_fields_read'
except Exception as error:
    result = {'status': 'unavailable', 'error_type': type(error).__name__}
print(json.dumps(result))
''')
    return json.loads(command('docker', 'exec', '-i', '-e', 'PYTHONDONTWRITEBYTECODE=1',
                              container_id, 'python', '-B', '-', input_text=payload))


def boot_memory_evidence():
    result = {'meminfo_bytes': diagnostic.read_numbers(Path('/proc/meminfo'),
              {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree', 'Slab', 'SReclaimable', 'SUnreclaim'}),
              'sysctls': {}, 'memory_size_attribution': 'unconfirmed; package size is not a kernel measurement'}
    for name in ('swappiness', 'overcommit_memory'):
        result['sysctls'][name] = int((Path('/proc/sys/vm')/name).read_text())
    result['selected_boot_arguments'] = [value for value in Path('/proc/cmdline').read_text().split()
                                        if re.fullmatch(r'(mem|memmap|crashkernel|cma)=[0-9A-Za-z@,.:!$%+-]{1,80}', value)]
    path = Path('/sys/kernel/kexec_crash_size')
    result['crash_reserved_bytes'] = int(path.read_text()) if path.exists() else None
    code, body = diagnostic.run('journalctl', '-k', '-b', '--no-pager', '-n', '80', '-o', 'json',
                                '--grep', '(?i)Memory:|crashkernel|balloon|e820:.*reserved')
    result['boot_log_exit'] = code
    result['boot_memory_messages'] = []
    for line in body.splitlines():
        try:
            message = json.loads(line).get('MESSAGE', '')
            if isinstance(message, str) and re.search(r'Memory:|crashkernel|balloon|e820:.*reserved', message, re.I):
                result['boot_memory_messages'].append(message[:500])
        except ValueError:
            pass
    return result


def nginx_trend_routes(body):
    result = []
    for location, block in re.findall(r'location\s+([^{}\n]*TrendRadar[^{}\n]*)\{([^{}]*)\}', body):
        routes = {'location': location.strip()[:160], 'directives': []}
        for kind, value in re.findall(r'\b(root|alias|proxy_pass)\s+([^;\n]+);', block):
            value = value.strip()
            if kind == 'proxy_pass':
                parsed = urlsplit(value)
                value = {'scheme': parsed.scheme, 'hostname': parsed.hostname, 'port': parsed.port, 'path': parsed.path}
            elif not re.fullmatch(r'[A-Za-z0-9_./$-]{1,240}', value):
                value = 'unrecognized_path_redacted'
            routes['directives'].append({'kind': kind, 'target': value})
        result.append(routes)
    return {'targeted_locations': result, 'complete_nginx_config_exported': False,
            'interpretation': 'Only simple TrendRadar location blocks; empty output requires manual inspection.'}


def require_idle(target):
    available = diagnostic.read_numbers(Path('/proc/meminfo'), {'MemAvailable'}) or {}
    group = diagnostic.cgroup_memory(target['pid'])
    if available.get('MemAvailable', 0) < 128*MIB or not isinstance(group.get('memory.current'), int) or group['memory.current'] >= 64*MIB:
        raise RuntimeError('Not enough idle headroom for the small YAML inspection; retry outside a collection peak')
    # Keep even this small temporary interpreter away from the next half-hour job.
    if time.time() % 1800 > 1740:
        raise RuntimeError('A scheduled collection is due within one minute; inspect after that cycle')
    return group


def overlap_evidence(report):
    observation = report.get('ai_observation', {})
    ai = report.get('ai', {})
    timing = ai.get('timing', {})
    if report.get('status') != 'completed_for_review' or ai.get('status') != 'succeeded' or observation.get('three_way_overlap') != 'observed':
        raise RuntimeError('The named verification has not passed the real overlap probe')
    rows = observation.get('samples', [])
    baseline = observation['baseline']
    keys = ('trend_oom_kill', 'finevent_oom_kill', 'host_oom_kill')
    if not rows or any(r['available'] < 64*MIB or any(r[k] != baseline[k] for k in keys) for r in rows):
        raise RuntimeError('The overlap evidence contains missing resources or new memory pressure')
    begin = max(timing['model_started_ms'], timing['collector_started_ms'])
    end = min(timing['model_completed_ms'], timing['collector_completed_ms'])
    common = [r for r in rows if 0 < begin <= r['sample_started_ms'] <= r['sample_completed_ms'] < end
              and any(p in observation['triggered_jobs'] for p in r['trend_jobs'])]
    collection = observation.get('collection', {})
    if not common or timing['collector_success'] != 1 or collection.get('exit') != 0 or not collection.get('report_times') or collection.get('error_times') or collection.get('may_be_truncated'):
        raise RuntimeError('Actual overlap or successful collection completion is not established')
    if any(observation.get('health', {}).get(path) != {'exit': 0, 'http_status': '200'}
           for path in ('/finevent/healthz', '/TrendRadar/content/')):
        raise RuntimeError('The overlap verification did not pass both HTTPS checks')
    return {'id': report['id'], 'finished_at': report['finished_at'], 'common_samples': len(common),
            'common_interval_ms': end-begin, 'host_available_min_mib': min(r['available'] for r in rows)/MIB,
            'trend_memory_peak_observed_mib': max(r['trend_memory'] for r in rows)/MIB}


def selected_compose_service(service):
    """Keep deployment mechanics only; do not export environment, commands, labels or secrets."""
    result = {}
    for key in ('image', 'container_name', 'cgroup_parent', 'restart', 'network_mode'):
        value = service.get(key)
        result[key] = value if value is None or isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_./:@-]{1,240}', value) else {'status': 'redacted'}
    for key in ('mem_limit', 'memswap_limit', 'mem_reservation', 'read_only', 'init'):
        value = service.get(key)
        result[key] = value if value is None or isinstance(value, (int, bool)) or isinstance(value, str) and re.fullmatch(r'[0-9.]+[kKmMgGbB]*', value) else {'status': 'redacted'}
    result['deploy_memory'] = allowed_value(service.get('deploy', {}).get('resources', {}).get('limits', {}).get('memory'), 'number')
    result['volumes'] = []
    for volume in service.get('volumes', []):
        if not isinstance(volume, dict):
            result['volumes'].append({'status': 'unexpanded_volume'})
            continue
        row = {k: volume[k] for k in ('type', 'read_only') if k in volume and (isinstance(volume[k], bool) or volume[k] in ('bind', 'volume', 'tmpfs'))}
        for key in ('source', 'target'):
            value = volume.get(key)
            if value is None:
                continue
            absolute = isinstance(value, str) and value.startswith('/') and not any(c in value for c in ('\n', '\r', '?', '#'))
            named = key == 'source' and volume.get('type') == 'volume' and isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', value)
            row[key] = value if absolute or named else {'status': 'redacted'}
        result['volumes'].append(row)
    return result


def compose_paths(raw, root=PurePosixPath('/srv/example/trendradar/docker')):
    if not isinstance(raw, str):
        raise RuntimeError('Compose file labels are missing')
    paths = [PurePosixPath(p) for p in raw.split(',')]
    if not 1 <= len(paths) <= 5 or any(not p.is_absolute() or '..' in p.parts or not p.is_relative_to(root) for p in paths):
        raise RuntimeError('Compose files are outside the audited TrendRadar deployment')
    return [str(p) for p in paths]


def launch_comparison(actual, image, service):
    def environment(values):
        return dict(item.split('=', 1) for item in values or [] if isinstance(item, str) and '=' in item)
    expected = environment(image.get('environment'))
    overrides = service.get('environment', {})
    if not isinstance(overrides, dict):
        raise RuntimeError('Compose environment is not normalized')
    for key, value in overrides.items():
        if value is None:
            expected.pop(key, None)
        else:
            expected[key] = str(value)
    current = environment(actual.get('environment'))
    different = sum(current.get(k) != expected.get(k) for k in set(current) | set(expected))
    # These values can contain credentials; export equality only, not values or hashes.
    result = {'environment_equal': different == 0, 'different_environment_count': different}
    for key in ('command', 'entrypoint', 'user', 'working_dir'):
        value = service.get(key)
        expected_value = image.get(key) if value is None else value
        result[key+'_equal'] = actual.get(key) == expected_value
    return result


def swappiness_configuration(root=Path('/')):
    rows = []
    files = [root/'etc/sysctl.conf']
    for directory in ('etc/sysctl.d', 'run/sysctl.d', 'usr/local/lib/sysctl.d', 'usr/lib/sysctl.d', 'lib/sysctl.d'):
        files.extend(sorted((root/directory).glob('*.conf')))
    for path in files:
        if not path.is_file():
            continue
        if path.stat().st_size > 128*1024:
            rows.append({'path': str(path), 'status': 'oversize_not_read'})
            continue
        for number, line in enumerate(path.read_text(errors='replace').splitlines(), 1):
            key, sep, value = line.partition('=')
            if sep and key.strip().lstrip('-').replace('/', '.') == 'vm.swappiness':
                value = re.split(r'[#;]', value, maxsplit=1)[0].strip()
                rows.append({'path': str(path), 'line': number,
                             'value': int(value) if value.isdigit() and 0 <= int(value) <= 200 else 'unrecognized_value_redacted'})
    return {'entries': rows, 'interpretation': 'Candidate definitions only; effective precedence and masks require review.'}


def audit_persistence(verification_id, report):
    path = BACKUPS/verification_id/'report.json'
    verification = json.loads(path.read_text())
    if verification.get('id') != verification_id:
        raise RuntimeError('Verification ID mismatch')
    report.update(created_at=diagnostic.stamp(time.time()), stage='deployment_inventory',
                  verification=overlap_evidence(verification), verification_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    targets = [r for r in container_inventory() if r['name'] == '/trend-radar' and r['running']]
    if len(targets) != 1:
        raise RuntimeError('Exactly one running trend-radar container is required')
    target = targets[0]
    report['container'] = target
    template = ('{"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
                '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
                '"working_dir":{{json (index .Config.Labels "com.docker.compose.project.working_dir")}},'
                '"cgroup_parent":{{json .HostConfig.CgroupParent}},"restart_policy":{{json .HostConfig.RestartPolicy}}}')
    report['deployment_identity'] = json.loads(command('docker', 'inspect', '--format', template, target['id']))
    if report['deployment_identity'].get('working_dir') != '/srv/example/trendradar/docker':
        raise RuntimeError('Compose working directory differs from the audited deployment')
    paths = compose_paths(target['compose_files'])
    report['compose_files'] = [{'path': p, 'sha256': hashlib.sha256(Path(p).read_bytes()).hexdigest()} for p in paths]
    report['compose_version'] = command('docker', 'compose', 'version', '--short').strip()[:80]
    report['docker_runtime'] = json.loads(command('docker', 'info', '--format', '{"cgroup_driver":{{json .CgroupDriver}},"cgroup_version":{{json .CgroupVersion}},"server_version":{{json .ServerVersion}}}'))
    report['systemd_version'] = command('systemctl', '--version').splitlines()[0][:200]
    deadline = time.monotonic()+120
    while True:
        try:
            report['container_memory'] = require_idle(target)
            break
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            print('Waiting briefly for idle headroom before expanding Compose configuration...', flush=True)
            time.sleep(5)
    args = ['docker', 'compose', '--project-directory', '/srv/example/trendradar/docker']
    for file in paths:
        args.extend(['-f', file])
    # Resolved configuration can contain credentials: retain only the allowlisted fields in memory.
    config = json.loads(command(*args, 'config', '--format', 'json'))
    name = report['deployment_identity']['service']
    if name not in config.get('services', {}):
        raise RuntimeError('The current container service is absent from its Compose configuration')
    report['compose_service'] = selected_compose_service(config['services'][name])
    launch_template = ('{"environment":{{json .Config.Env}},"command":{{json .Config.Cmd}},'
                       '"entrypoint":{{json .Config.Entrypoint}},"user":{{json .Config.User}},"working_dir":{{json .Config.WorkingDir}}}')
    actual_launch = json.loads(command('docker', 'inspect', '--format', launch_template, target['id']))
    image_launch = json.loads(command('docker', 'image', 'inspect', '--format', launch_template, target['image_id']))
    report['recreation_input_comparison'] = launch_comparison(actual_launch, image_launch, config['services'][name])
    report['compose_other_service_count'] = len(config['services'])-1
    source = Path(f"/proc/{target['pid']}/root/app/trendradar/ai/client.py")
    report['client_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    report['swappiness_runtime'] = (Path('/proc/sys/vm/swappiness').read_text().strip())
    report['swappiness_configuration'] = swappiness_configuration()
    report['managed_files_present'] = {p: Path(p).exists() for p in (
        '/etc/systemd/system/trendradar.slice', '/etc/systemd/system/finevent-trendradar.service',
        '/etc/sysctl.d/99-finevent-memory.conf', '/srv/example/finevent-host/trendradar')}
    report.update(status='collected_for_review', finished_at=diagnostic.stamp(time.time()),
                  changes='None: no container recreation, configuration write or model request.')


def audit(latest_trial, report):
    report.update({'created_at': diagnostic.stamp(time.time()), 'status': 'collecting',
              'limitations': ['No heavy dependency import or collection benchmark was run.',
                              'Process samples are sequential; do not add RSS to estimate reclaimable host memory.']})
    if latest_trial:
        path = BACKUPS/latest_trial/'report.json'
        report['latest_trial'] = {'id': latest_trial, 'report_exists': path.is_file()}
        if path.is_file():
            data = json.loads(path.read_text())
            report['latest_trial'].update({key: data.get(key) for key in
                ('status', 'finished_at', 'resources', 'reclaim_pressure', 'rollback', 'configuration',
                 'effective_high_after_trial', 'effective_swappiness_after_trial', 'observation_issue')})
    report['host'] = boot_memory_evidence()
    report['host_python_version'] = sys.version.split()[0]
    report['processes'] = process_inventory()
    containers = container_inventory()
    report['containers'] = containers
    targets = [row for row in containers if row['name'] == '/trend-radar' and row['running']]
    if len(targets) != 1:
        raise RuntimeError('Exactly one running trend-radar container is required')
    target = targets[0]
    report['container_memory'] = diagnostic.cgroup_memory(target['pid'])
    root = Path('/proc')/str(target['pid'])/'root/app'
    try:
        require_idle(target)
        report['source'] = source_inventory(root)
        require_idle(target)
        report['configuration'] = read_config_in_container(target['id'])
    except RuntimeError as error:
        report['configuration'] = {'status': 'skipped', 'reason': str(error)}
        report.setdefault('source', {'status': 'skipped', 'reason': str(error)})
    try:
        report['web_route'] = nginx_trend_routes(command('nginx', '-T'))
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        report['web_route'] = {'status': 'unavailable', 'error_type': type(error).__name__}
    report['output_directories'] = {}
    for name in ('news', 'rss', 'html'):
        path = root/'output'/name
        report['output_directories'][name] = {'exists': path.is_dir()}
        if name in ('news', 'rss') and path.is_dir():
            files = sorted(path.glob('*.db'))[-3:]
            report['output_directories'][name]['recent_databases'] = [
                {'name': file.name, 'size': file.stat().st_size, 'modified_at': diagnostic.stamp(file.stat().st_mtime)}
                for file in files if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.db', file.name)]
    report['status'] = 'collected_for_review'
    report['finished_at'] = diagnostic.stamp(time.time())
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--latest-trial')
    parser.add_argument('--persistence-verification')
    args = parser.parse_args()
    if not re.fullmatch(r'trendradar-audit-\d{8}T\d{6}Z-[a-f0-9]{8}', args.run_id):
        raise RuntimeError('Invalid run ID')
    if args.latest_trial and not re.fullmatch(r'memory-high-\d{8}T\d{6}Z-[a-f0-9]{8}', args.latest_trial):
        raise RuntimeError('Invalid trial ID')
    if args.persistence_verification and not re.fullmatch(r'verification-\d{8}T\d{6}Z-[a-f0-9]{8}', args.persistence_verification):
        raise RuntimeError('Invalid verification ID')
    if os.geteuid() != 0:
        raise RuntimeError('Run in the authorized root SSH terminal')
    import fcntl
    os.umask(0o077)
    directory = BACKUPS/args.run_id
    directory.mkdir(mode=0o700)
    report = {'status': 'incomplete'}
    try:
        # Do not add diagnostic work while the preceding memory trial still holds its lock.
        with Path('/run/lock/finevent-memory-recovery.lock').open('r') as lock:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            if args.persistence_verification:
                audit_persistence(args.persistence_verification, report)
            else:
                audit(args.latest_trial, report)
    except Exception as error:
        report['status'] = 'incomplete'
        report['error_type'] = type(error).__name__
        report['error'] = str(error) if isinstance(error, RuntimeError) else 'Read-only audit interrupted; review before repeating.'
        raise
    finally:
        with (directory/'report.json').open('x', encoding='utf-8') as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
        print('Evidence: '+str(directory/'report.json'), flush=True)


if __name__ == '__main__':
    main()
