import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
from threading import Thread
import time
import unittest
from unittest.mock import patch

import install
import release_control as control
import test_release_bundle as bundles
import web_release


class WebReleaseTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'Production uses POSIX atomic symlinks and locks')
    def test_web_operations_preserve_server_release_and_observation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root/'app'
            app.mkdir()
            previous = app/'current'
            previous.symlink_to(app/'releases'/'server-a')
            web = app/'web'
            web_release.stage(web, 'legacy', {'dist/index.html': b'old'})
            web_release.switch(web, 'legacy')
            (web/'ready').touch()
            c = control.Controller({'appRoot': str(app), 'siteHost': 'localhost', 'backupRoot': str(root)}, root/'state', checker=bundles.check_archive)
            backend_id = 'calendar-release-20261101T000000Z-11111111'
            (root/backend_id).mkdir()
            control.save(root/backend_id/'report.json', {'status': 'observing', 'created_at': '2026-11-01T00:00:00Z'})
            evidence_path = root/backend_id/'next-baseline.json'
            evidence_path.write_bytes(b'private server baseline')
            backend_report = (root/backend_id/'report.json').read_bytes()
            self.archive = root/'web.tar.gz'
            bundles.ReleaseBundleTests.write(self, bundles.web_fixture())
            data = self.archive.read_bytes()
            first = 'web-release-20261102T000000Z-22222222'
            second = 'web-release-20261103T000000Z-33333333'
            args = {'action': 'web-deploy', 'id': first, 'after_review': 'initial',
                    'content_sha256': control.content_digest(bundles.check_archive(self.archive, 'generic')),
                    'archive_sha256': hashlib.sha256(data).hexdigest()}
            with patch.dict(control.STATIC, verify=lambda *_: None):
                result = c.publish_web(args, io.BytesIO(data))
                self.assertEqual((result['active'], result['previous']), (first, 'legacy'))
                self.assertEqual(c.publish_web(args, io.BytesIO()), result)
                # Recover a lost process after pointer replacement but before the final journal write.
                control.save(c.state/'web'/(first+'.json'), {**result, 'active': 'legacy', 'status': 'needs_recovery', 'needs_recovery': True})
                self.assertEqual(c.publish_web(args, io.BytesIO()), result)
                with self.assertRaisesRegex(RuntimeError, 'Stale'):
                    c.publish_web({**args, 'id': second}, io.BytesIO(data))
                c.publish_web({'action': 'web-rollback', 'id': second, 'after_review': first}, io.BytesIO())
            self.assertEqual((web/'current/index.html').read_bytes(), b'old')
            self.assertEqual(c.status('latest-web')['id'], second)
            self.assertEqual(c.latest(), backend_id)
            self.assertEqual(evidence_path.read_bytes(), b'private server baseline')
            self.assertEqual((root/backend_id/'report.json').read_bytes(), backend_report)
            self.assertEqual(previous.readlink(), app/'releases'/'server-a')
            self.assertTrue((web/'assets/main-hash.js').is_file())
            def fail_new(_host, path):
                if path.resolve().name != 'legacy':
                    raise RuntimeError('wrong response')
            with patch.dict(control.STATIC, verify=fail_new):
                failed = c.publish_web({**args, 'id': 'web-release-20261104T000000Z-44444444', 'after_review': second}, io.BytesIO(data))
                self.assertEqual(failed['status'], 'failed')
                self.assertEqual(web_release.current(web), 'legacy')
            # Switching the server pointer leaves the active site and its rollback history intact.
            with patch.object(install, 'ROOT', app):
                install.switch_release(app/'releases'/'server-b')
                install.switch_release(app/'releases'/'server-a')
            self.assertEqual(c.status('latest-web'), failed)
            self.assertEqual((web/'current/index.html').read_bytes(), b'old')

    @unittest.skipUnless(os.name == 'posix', 'Production migration holds a POSIX lock')
    def test_cutover_failure_restores_previous_site_then_retry_uses_installed_bytes(self):
        def bundle(path, files):
            for name, data in files.items():
                target = path/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            (path/'sha256.json').write_text(json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in files.items()}))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root/'releases'/'one'
            new = root/'staged'
            server = {'finevent': b'server', 'deploy/finevent.service': b'unit', 'deploy/install.py': b'installer'}
            bundle(old, {**server, 'dist/index.html': b'old shell', 'deploy/finevent.nginx.conf': b'old route'})
            bundle(new, {**server, 'deploy/finevent.nginx.conf': b'new route',
                         'release.json': b'{"kind":"deployment","component":"server"}'})
            (root/'current').symlink_to(old)
            site, snippet = root/'site', root/'snippet'
            site.write_text('include')
            snippet.write_bytes(b'old route')
            with patch.multiple(install, ROOT=root, SNIPPET=snippet, BACKUPS=root, MIGRATION_LOCK=root/'lock'), \
                 patch.object(install, 'find_site', return_value=(site, 'include')), \
                 patch.object(install.os, 'chown'), patch.object(install, 'run') as commands, \
                 patch.object(web_release, 'verify'), patch.object(install, 'wait_for_https', side_effect=[RuntimeError('bad route'), None]):
                with self.assertRaisesRegex(RuntimeError, 'bad route'):
                    install.migrate_web(new)
                self.assertEqual(snippet.read_bytes(), b'old route')
                self.assertFalse((root/'web/ready').exists())
                install.migrate_web(new)
                install.migrate_web(new)
            self.assertEqual(snippet.read_bytes(), b'new route')
            self.assertEqual((root/'web/current/index.html').read_bytes(), b'old shell')
            self.assertEqual((root/'current').resolve(), old)
            self.assertTrue((root/'web/ready').is_file())
            self.assertTrue(all(call.args in (('nginx', '-t'), ('systemctl', 'reload', 'nginx')) for call in commands.call_args_list))


NGINX = os.environ.get('FINEVENT_TEST_NGINX') or shutil.which('nginx')


@unittest.skipUnless(NGINX and os.name == 'posix', 'Requires a local Linux nginx binary')
class NginxRouteTests(unittest.TestCase):
    def test_real_static_routes_proxy_cache_headers_and_release_switch(self):
        class Backend(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path in ('/finevent/api/v1/session', '/finevent/healthz') else 404)
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(self.path.encode())

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as temporary, ThreadingHTTPServer(('127.0.0.1', 0), Backend) as api:
            root = Path(temporary)
            Thread(target=api.serve_forever, daemon=True).start()
            self.addCleanup(api.shutdown)
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            web = root/'web'
            old = {'dist/index.html': b'one', 'dist/sw.js': b'old worker', 'dist/assets/one.js': b'one'}
            web_release.stage(web, 'one', old)
            web_release.switch(web, 'one')
            routes = Path(__file__).with_name('finevent.nginx.conf').read_text().replace('/srv/example/finevent', temporary).replace('127.0.0.1:8787', '127.0.0.1:'+str(api.server_port))
            settings = ' '.join(f'{key}_temp_path {root}/{key};' for key in ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'))
            (root/'nginx.conf').write_text(f'error_log stderr; pid {root}/pid; events {{}} http {{ access_log off; {settings} server {{ listen 127.0.0.1:{port}; {routes} }} }}')
            nginx = subprocess.Popen([NGINX, '-p', temporary, '-c', str(root/'nginx.conf'), '-g', 'daemon off;'], stderr=subprocess.PIPE)
            def stop():
                nginx.terminate()
                nginx.communicate(timeout=5)
            self.addCleanup(stop)
            def get(path, host='www.example.com', data=None):
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                try:
                    conn.request('GET' if data is None else 'POST', '/finevent/'+path, data, headers={'Host': host})
                    response = conn.getresponse()
                    return response.status, dict(response.getheaders()), response.read()
                finally:
                    conn.close()
            for _ in range(100):
                if nginx.poll() is not None:
                    self.fail(nginx.stderr.read().decode())
                try:
                    get('')
                    break
                except ConnectionRefusedError:
                    time.sleep(0.02)
            self.assertEqual(get('')[2], b'one')
            status, headers, body = get('index.html')
            self.assertEqual((status, body, headers['Cache-Control']), (200, b'one', 'no-cache'))
            self.assertIn('Content-Security-Policy', headers)
            self.assertEqual(get('sw.js')[1]['Service-Worker-Allowed'], '/finevent/')
            self.assertEqual(get('sw.js')[1]['Content-Type'], 'application/javascript')
            self.assertIn('immutable', get('assets/one.js')[1]['Cache-Control'])
            for path in ('api/v1/session', 'healthz'):
                self.assertEqual(get(path)[2], ('/finevent/'+path).encode())
                self.assertEqual(get(path)[1]['Cache-Control'], 'no-store')
            for path in ('index.html?q=1', 'sw.js', 'api/v1/session', 'assets/one.js'):
                status, headers, _ = get(path, 'example.com')
                self.assertEqual((status, headers.get('Location')), (308, 'https://www.example.com/finevent/'+path))
            upload = b'x' * (32*1024)
            self.assertEqual(get('api/v1/calendar-imports/bls', data=upload)[0], 204)
            self.assertEqual(get('api/v1/login', data=upload)[0], 413)
            self.assertEqual(get('missing.js')[0], 404)
            self.assertEqual(get('api/missing')[0], 404)
            web_release.stage(web, 'two', {'dist/index.html': b'two', 'dist/assets/two.js': b'two'})
            web_release.switch(web, 'two')
            self.assertEqual(get('')[2], b'two')
            self.assertEqual(get('assets/one.js')[2], b'one')
            web_release.switch(web, 'one')
            self.assertEqual(get('')[2], b'one')
