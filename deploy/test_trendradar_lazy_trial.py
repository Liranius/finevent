import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import memory_recovery as recovery
import trendradar_lazy_trial as trial

SOURCE = b'''# keep this comment
from litellm import completion

class AIClient:
    def chat(self, value):
        """Keep documentation."""
        # keep implementation
        return completion(value)
'''
SHA = hashlib.sha256(SOURCE).hexdigest()


class LazyTrialTests(unittest.TestCase):
    def test_sqlite_rejects_proc_namespace_paths_before_resolving_them(self):
        with patch.object(trial.sqlite3, 'connect') as connect:
            with self.assertRaisesRegex(RuntimeError, 'verified host bind mount'):
                trial.read_database(PurePosixPath('/proc/123/root/app/output/news/today.db'))
            connect.assert_not_called()

    def test_output_mount_requires_audited_path_and_matching_file_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            host = Path(temporary)
            mounts = [{'Type': 'bind', 'Source': str(host), 'Destination': '/app/output'}]
            with patch.object(trial, 'EXPECTED_OUTPUT', host), patch.object(recovery, 'command', return_value=SimpleNamespace(stdout=json.dumps(mounts))) as command, patch.object(Path, 'samefile', return_value=True) as samefile:
                self.assertEqual(trial.output_directory({'id': 'container', 'pid': 123}), host)
                samefile.assert_called_once_with(host)
                samefile.return_value = False
                with self.assertRaisesRegex(RuntimeError, 'not the same files'):
                    trial.output_directory({'id': 'container', 'pid': 123})
                command.return_value.stdout = json.dumps([dict(mounts[0], Source='/different/output')])
                with self.assertRaisesRegex(RuntimeError, 'differs from the audited'):
                    trial.output_directory({'id': 'container', 'pid': 123})

    def test_backup_reads_host_database_including_live_wal_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            output = directory/'host-output'
            (output/'news').mkdir(parents=True)
            backup = directory/'backup'
            backup.mkdir()
            date = trial.datetime.now(trial.ZONE).strftime('%Y-%m-%d')
            database = output/'news'/(date+'.db')
            writer = sqlite3.connect(database)
            try:
                writer.execute('PRAGMA journal_mode=WAL')
                writer.execute('PRAGMA wal_autocheckpoint=0')
                writer.execute('CREATE TABLE news_items(title TEXT)')
                writer.execute('INSERT INTO news_items VALUES(?)', ('committed WAL news',))
                writer.commit()
                (output/'index.html').write_text('<html>current report</html>')
                result = trial.backup_outputs(backup, output)
                self.assertEqual(result['news_backup_sha256'], recovery.sha(backup/'news.before.db'))
                connection = sqlite3.connect(backup/'news.before.db')
                try:
                    self.assertEqual(connection.execute('SELECT title FROM news_items').fetchall(), [('committed WAL news',)])
                finally:
                    connection.close()
                self.assertEqual(writer.execute('SELECT COUNT(*) FROM news_items').fetchone()[0], 1)
                self.assertEqual((output/'index.html').read_bytes(), (backup/'index.before.html').read_bytes())
            finally:
                writer.close()

    def test_previous_failure_is_reproduced_readonly_or_left_for_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            previous = directory/'previous'
            previous.mkdir()
            recovery.save(previous/'report.json', {'error_type': 'OperationalError', 'rollback': {'status': 'no_changes'}})
            recovery.save(previous/'state.json', {'phase': 'backed_up'})
            output = directory/'output'
            (output/'news').mkdir(parents=True)
            date = trial.datetime.now(trial.ZONE).strftime('%Y-%m-%d')+'.db'
            database = output/'news'/date
            connection = sqlite3.connect(database)
            connection.execute('CREATE TABLE news_items(title TEXT)')
            connection.commit(); connection.close()
            original_resolve = Path.resolve
            legacy = directory/'unmounted/news.db'
            def resolve(path, *args, **kwargs):
                if path.as_posix().startswith('/proc/'):
                    return legacy
                return original_resolve(path, *args, **kwargs)
            with patch.object(recovery, 'BACKUPS', directory), patch.object(Path, 'samefile', return_value=True), patch.object(Path, 'resolve', resolve):
                result = trial.previous_database_diagnostic('previous', {'pid': 123}, output)
                self.assertEqual(result['status'], 'legacy_path_failure_reproduced')
                self.assertEqual(result['legacy_error']['sqlite_name'], 'SQLITE_CANTOPEN')
                self.assertFalse(legacy.exists())
                legacy = database
                result = trial.previous_database_diagnostic('previous', {'pid': 123}, output)
                self.assertEqual(result['status'], 'not_reproduced')
                recovery.save(previous/'state.json', {'change_attempted': True})
                with self.assertRaisesRegex(RuntimeError, 'may have modified'):
                    trial.previous_database_diagnostic('previous', {'pid': 123}, output)

    def test_database_error_details_exclude_message_and_capture_location(self):
        try:
            raise sqlite3.OperationalError('SECRET database path or input')
        except sqlite3.Error as error:
            details = trial.error_details(error)
        self.assertNotIn('SECRET', json.dumps(details))
        self.assertEqual(details['frames'][-1]['function'], 'test_database_error_details_exclude_message_and_capture_location')

    def test_written_patch_matches_tested_ast_and_preserves_comments(self):
        with patch.object(trial, 'ORIGINAL', SHA):
            changed = trial.candidate(SOURCE)
            expected = trial.probe.client_tree(SOURCE, SHA, True)
            self.assertEqual(ast.dump(ast.parse(changed)), ast.dump(expected))
            self.assertIn(b'# keep implementation', changed)
            self.assertIn(b'        from litellm import completion', changed)
            self.assertNotIn(b'\nfrom litellm import completion', changed)
            with self.assertRaisesRegex(RuntimeError, 'differs'):
                trial.candidate(SOURCE+b'# changed\n')

    def test_atomic_write_preserves_unrelated_changes_and_existing_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'client.py'
            path.write_bytes(SOURCE)
            leftover = path.with_name('.finevent-client-'+str(os.getpid())+'.tmp')
            leftover.write_bytes(b'unrelated existing file')
            with self.assertRaises(FileExistsError):
                trial.atomic_replace(path, b'candidate', SHA)
            self.assertEqual(leftover.read_bytes(), b'unrelated existing file')
            leftover.unlink()
            with patch.object(os, 'chown', create=True):
                trial.atomic_replace(path, b'candidate', SHA)
            self.assertEqual(path.read_bytes(), b'candidate')
            with self.assertRaisesRegex(RuntimeError, 'independently changed'):
                trial.atomic_replace(path, SOURCE, SHA)
            self.assertEqual(path.read_bytes(), b'candidate')

    def test_restore_checks_source_backup_and_container_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory/'client.py'
            source.write_bytes(b'candidate')
            backup = directory/'client.before.py'
            backup.write_bytes(SOURCE)
            state = {'change_attempted': True, 'candidate_sha256': hashlib.sha256(b'candidate').hexdigest(),
                     'container_id': 'one', 'container_pid': 123}
            with patch.object(trial, 'ORIGINAL', SHA), patch.object(recovery, 'inspect_target', return_value={'id': 'one', 'pid': 123}) as inspect, patch.object(trial, 'source_path', return_value=source), patch.object(os, 'chown', create=True):
                inspect.return_value = {'id': 'another', 'pid': 123}
                with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                    trial.restore(directory, state)
                inspect.return_value = {'id': 'one', 'pid': 123}
                source.write_bytes(b'changed by someone else')
                with self.assertRaisesRegex(RuntimeError, 'independently'):
                    trial.restore(directory, state)
                source.write_bytes(b'candidate')
                backup.write_bytes(b'wrong backup')
                with self.assertRaisesRegex(RuntimeError, 'backup was modified'):
                    trial.restore(directory, state)
                backup.write_bytes(SOURCE)
                self.assertEqual(trial.restore(directory, state)['status'], 'restored')
                self.assertEqual(source.read_bytes(), SOURCE)
                self.assertEqual(trial.restore(directory, state)['status'], 'restored')

    def test_background_launch_saves_evidence_before_start_and_does_not_wait(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)/'trial'
            def execute(*args, **kwargs):
                evidence = json.loads((directory/'report.json').read_text())
                self.assertEqual(evidence['status'], 'queued')
                self.assertIn('--property=Type=exec', args)
                self.assertIn('--property=MemoryMax=96M', args)
                self.assertIn('--property=RuntimeMaxSec=4500', args)
                self.assertNotIn('--wait', args)
                self.assertNotIn('--pty', args)
                self.assertTrue((directory/'trendradar_lazy_trial.py').exists())
                return SimpleNamespace(returncode=0)
            with patch.object(recovery, 'command', side_effect=execute):
                trial.launch(directory, 'trial-id', 'workflow-id')

    def test_late_acceptance_failure_restores_original_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory/'workflow').mkdir()
            recovery.save(directory/'workflow/report.json', {})
            source = directory/'client.py'
            source.write_bytes(SOURCE)
            state = {'phase': 'preflight'}
            report = {'workflow_id': 'workflow'}
            def fails_after_apply(*_):
                (directory/'client.before.py').write_bytes(SOURCE)
                source.write_bytes(b'candidate')
                state.update(change_attempted=True, candidate_sha256=hashlib.sha256(b'candidate').hexdigest(), container_id='one', container_pid=123)
                raise RuntimeError('The HTML report was not refreshed')
            with patch.object(trial, 'ORIGINAL', SHA), patch.object(recovery, 'BACKUPS', directory), patch.object(trial, 'apply_and_observe', side_effect=fails_after_apply), patch.object(recovery, 'inspect_target', return_value={'id': 'one', 'pid': 123}), patch.object(trial, 'source_path', return_value=source), patch.object(os, 'chown', create=True):
                with self.assertRaisesRegex(RuntimeError, 'HTML report'):
                    trial.run_trial(directory, state, report)
            self.assertEqual(source.read_bytes(), SOURCE)
            self.assertEqual(report['status'], 'incomplete')
            self.assertEqual(report['rollback']['status'], 'restored')
            self.assertEqual(json.loads((directory/'state.json').read_text())['phase'], 'restored')

    def test_status_detects_dead_observer_without_overwriting_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            recovery.save(directory/'report.json', {'status': 'observing'})
            before = (directory/'report.json').read_bytes()
            with patch.object(recovery, 'command', return_value=SimpleNamespace(returncode=0, stdout='ActiveState=failed\nSubState=failed\nResult=oom-kill\nEnvironment=SECRET\n')):
                trial.export_status(directory, 'trial-id')
            result = json.loads((directory/'download.json').read_text())
            self.assertIn('warning', result['observer_status'])
            self.assertNotIn('SECRET', json.dumps(result))
            self.assertEqual(before, (directory/'report.json').read_bytes())

    def fixture_outputs(self, directory):
        output = directory/'output'
        (output/'news').mkdir(parents=True)
        stamp = lambda clock: int(trial.datetime.fromisoformat('2026-10-29T'+clock+':00+08:00').timestamp())
        database = output/'news/2026-10-29.db'
        with sqlite3.connect(database) as connection:
            connection.executescript('CREATE TABLE crawl_records(id INTEGER,crawl_time TEXT,total_items INTEGER); CREATE TABLE crawl_source_status(crawl_record_id INTEGER,platform_id TEXT,status TEXT);')
            for number, clock in ((1, '18-00'), (2, '18-30')):
                connection.execute('INSERT INTO crawl_records VALUES(?,?,?)', (number, clock, 10))
                connection.executemany('INSERT INTO crawl_source_status VALUES(?,?,?)', [(number, 'source'+str(i), 'success') for i in range(9)])
        connection.close()
        html = output/'index.html'
        html.write_text('<html>Actual generated report fixture</html>')
        os.utime(html, (stamp('18:31'), stamp('18:31')))
        finevent = directory/'finevent.db'
        with sqlite3.connect(finevent) as connection:
            connection.executescript('CREATE TABLE polls(source TEXT,started INTEGER,completed INTEGER,success INTEGER); CREATE TABLE news(source TEXT,source_observed_at INTEGER);')
            connection.execute('INSERT INTO polls VALUES(?,?,?,?)', ('trendradar', stamp('18:32'), stamp('18:32'), 1))
            connection.execute('INSERT INTO news VALUES(?,?)', ('trendradar:source1', stamp('18:30')))
        connection.close()
        return output, finevent, stamp

    def test_actual_database_and_finevent_must_show_both_new_cycles(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, finevent, stamp = self.fixture_outputs(Path(temporary))
            result = trial.verify_outputs(output, finevent, stamp('17:55'), stamp('18:33'))
            self.assertEqual(len(result['records']), 2)
            self.assertEqual(len(result['source_statuses']), 18)
            self.assertEqual(result['finevent_poll_failures'], 0)
            with sqlite3.connect(finevent) as connection:
                connection.execute('UPDATE news SET source_observed_at=?', (stamp('18:00'),))
            connection.close()
            with self.assertRaisesRegex(RuntimeError, 'Finevent did not'):
                trial.verify_outputs(output, finevent, stamp('17:55'), stamp('18:33'))
            with sqlite3.connect(output/'news/2026-10-29.db') as connection:
                connection.execute('DELETE FROM crawl_records WHERE id=2')
            connection.close()
            with self.assertRaisesRegex(RuntimeError, 'Two production'):
                trial.verify_outputs(output, finevent, stamp('17:55'), stamp('18:33'))

    def test_stale_html_is_not_accepted_even_when_database_is_fresh(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, finevent, stamp = self.fixture_outputs(Path(temporary))
            os.utime(output/'index.html', (stamp('17:30'), stamp('17:30')))
            with self.assertRaisesRegex(RuntimeError, 'HTML report'):
                trial.verify_outputs(output, finevent, stamp('17:55'), stamp('18:33'))


if __name__ == '__main__':
    unittest.main()
