import importlib.util
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load("install")
backup_module = load("backup")


class DeploymentTests(unittest.TestCase):
    def test_only_matching_tls_server_is_patched(self):
        text = 'server { listen 80; server_name www.example.com; }\nserver { listen 443 ssl; server_name www.example.com example.com; location / { return 200 "a{b}"; } # } ignored\n}\nserver { listen 443 ssl; server_name another.test; }'
        result = installer.patch_nginx(text)
        self.assertEqual(result.count("include /etc/nginx/snippets/example-app.conf;"), 1)
        self.assertTrue(result.startswith(text.split("\n")[0]))
        self.assertTrue(result.endswith(text.split("\n")[-1]))
        self.assertEqual(installer.patch_nginx(result), result)

    def test_ambiguous_or_existing_route_refuses_changes(self):
        block = 'server { listen 443 ssl; server_name www.example.com; }'
        with self.assertRaises(RuntimeError):
            installer.patch_nginx(block + block)
        with self.assertRaises(RuntimeError):
            installer.patch_nginx('server { listen 443 ssl; server_name www.example.com; location ^~ /finevent/ { return 200; } }')

    def test_commented_include_does_not_prevent_installation(self):
        text = 'server { listen 443 ssl; server_name www.example.com;\n# include /etc/nginx/snippets/example-app.conf;\n}'
        result = installer.patch_nginx(text)
        self.assertIn('\n    include /etc/nginx/snippets/example-app.conf;\n}', result)
        self.assertEqual(installer.patch_nginx(result), result)

    def test_captured_command_failure_exposes_stderr(self):
        result = subprocess.CompletedProcess(["nginx", "-t"], 1, "", "invalid nginx directive")
        with patch.object(installer.subprocess, "run", return_value=result), patch("sys.stderr", new_callable=io.StringIO) as stderr:
            with self.assertRaises(subprocess.CalledProcessError):
                installer.run("nginx", "-t", capture=True)
        self.assertIn("invalid nginx directive", stderr.getvalue())

    def response(self, code=200, body='{"stage":"verification","status":"ok"}', exit_code=0, redirect=""):
        return {"url": "https://www.example.com/finevent/healthz", "http_status": code,
                "curl_exit": exit_code, "body_excerpt": body, "error": "", "redirect_url": redirect}

    def test_reload_transient_404_and_wrong_200_are_retried(self):
        responses = [self.response(404, "Not Found"), self.response(200, "old site"),
                     self.response(), self.response(200, "TrendRadar"),
                     self.response(308, redirect="https://www.example.com/finevent/?probe=canonical")]
        with tempfile.TemporaryDirectory() as directory, patch.object(installer, "probe_route", side_effect=responses) as probe, patch.object(installer.time, "sleep") as sleep:
            observations = installer.wait_for_https(Path(directory))
            saved = json.loads((Path(directory) / "https-checks.json").read_text())
        self.assertEqual([item["http_status"] for item in observations], [404, 200, 200, 200, 308])
        self.assertEqual(saved, observations)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(probe.call_args_list[-1].args, ("https://example.com/finevent/?probe=canonical", "example.com:443:127.0.0.1"))

    def test_alias_must_redirect_to_exact_canonical_address(self):
        expected = "https://www.example.com/finevent/?probe=canonical"
        for response in [self.response(), self.response(308, redirect="https://example.com/finevent/"),
                         self.response(308, redirect="https://www.example.com/finevent/")]:
            with self.subTest(response=response):
                self.assertFalse(installer.route_ready(response, redirect=expected))
        self.assertTrue(installer.route_ready(self.response(308, redirect=expected), redirect=expected))
        responses = [self.response(), self.response(200, "TrendRadar"), self.response(), self.response()]
        with tempfile.TemporaryDirectory() as directory, patch.object(installer, "probe_route", side_effect=responses), patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaisesRegex(RuntimeError, "probe=canonical"):
                installer.wait_for_https(Path(directory), attempts=1)

    def test_persistent_failure_keeps_evidence_and_raises_for_rollback(self):
        responses = [self.response(502, "upstream unavailable") for _ in range(6)] + [self.response()]
        with tempfile.TemporaryDirectory() as directory, patch.object(installer, "probe_route", side_effect=responses), patch.object(installer.time, "sleep") as sleep, patch("sys.stderr", new_callable=io.StringIO) as stderr:
            with self.assertRaisesRegex(RuntimeError, "HTTPS route did not become ready"):
                installer.wait_for_https(Path(directory))
            saved = json.loads((Path(directory) / "https-checks.json").read_text())
            local = json.loads((Path(directory) / "local-health.json").read_text())
        self.assertEqual(len(saved), 6)
        self.assertEqual(sleep.call_count, 5)
        self.assertTrue(installer.route_ready(local, health=True))
        self.assertIn("upstream unavailable", stderr.getvalue())

    def test_trendradar_failure_still_blocks_deployment(self):
        responses = [self.response(), self.response(403), self.response()]
        with tempfile.TemporaryDirectory() as directory, patch.object(installer, "probe_route", side_effect=responses), patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaisesRegex(RuntimeError, "/TrendRadar/content/"):
                installer.wait_for_https(Path(directory), attempts=1)

    def test_health_requires_http_success_and_expected_json(self):
        for response in [self.response(301), self.response(500), self.response(exit_code=60),
                         self.response(body="<html>ok</html>"), self.response(body='{"status":"ok"}')]:
            with self.subTest(response=response):
                self.assertFalse(installer.route_ready(response, health=True))
        self.assertTrue(installer.route_ready(self.response(), health=True))

    def test_real_curl_preserves_http_error_and_ignores_proxy_and_curlrc(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/redirect":
                    self.send_response(308)
                    self.send_header("Location", "https://www.example.com/finevent/?probe=canonical")
                    self.end_headers()
                    return
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"upstream unavailable")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                (Path(directory) / ".curlrc").write_text("fail\n")
                (Path(directory) / "_curlrc").write_text("fail\n")
                with patch.dict(os.environ, {"CURL_HOME": directory, "http_proxy": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
                    result = installer.probe_route(f"http://127.0.0.1:{server.server_port}/finevent/healthz")
                    redirect = installer.probe_route(f"http://127.0.0.1:{server.server_port}/redirect")
            self.assertEqual(result["curl_exit"], 0)
            self.assertEqual(result["http_status"], 503)
            self.assertEqual(result["body_excerpt"], "upstream unavailable")
            self.assertFalse(installer.route_ready(result, health=True))
            self.assertTrue(installer.route_ready(redirect, redirect="https://www.example.com/finevent/?probe=canonical"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_backup_includes_wal_and_is_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with closing(sqlite3.connect(root / "finevent.db")) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE evidence(value TEXT)")
                db.execute("INSERT INTO evidence VALUES('retained')")
                db.commit()
                target = backup_module.backup(root)
            with closing(sqlite3.connect(target)) as restored:
                self.assertEqual(restored.execute("SELECT value FROM evidence").fetchone()[0], "retained")


if __name__ == "__main__":
    unittest.main()
