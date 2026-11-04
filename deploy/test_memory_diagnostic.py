from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import memory_diagnostic as diagnostic


class MemoryDiagnosticTests(unittest.TestCase):
    def test_threshold_sample_runs_do_not_bridge_missing_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary)/'before.db'
            with closing(sqlite3.connect(database)) as conn:
                conn.execute('CREATE TABLE resources(at INTEGER,rss INTEGER,available INTEGER,cgroup_current INTEGER,cgroup_peak INTEGER,oom_kills INTEGER)')
                for at, available in ((100, 63), (170, 61), (240, 80), (310, 62), (600, 62), (670, 0)):
                    conn.execute('INSERT INTO resources VALUES(?,1,?,2,3,0)', (at, available*1024**2))
                conn.commit()
            before = database.read_bytes()
            report = diagnostic.memory_history(database, 100, 670)
            threshold = report['thresholds_mib']['64']
            self.assertEqual(threshold['samples_below'], 4)
            self.assertEqual(threshold['sample_runs'], 3)
            self.assertEqual(threshold['fraction_of_valid_samples'], 0.8)
            self.assertEqual(threshold['longest_sample_runs'][0]['observed_span_seconds'], 70)
            self.assertEqual(report['lowest_samples'][0]['at'], diagnostic.stamp(170))
            self.assertEqual(report['missing_available'], 1)
            self.assertEqual(database.read_bytes(), before)

    def test_empty_history_does_not_manufacture_memory_observations(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary)/'before.db'
            with closing(sqlite3.connect(database)) as conn:
                conn.execute('CREATE TABLE resources(at INTEGER,rss INTEGER,available INTEGER,cgroup_current INTEGER,cgroup_peak INTEGER,oom_kills INTEGER)')
            report = diagnostic.memory_history(database, 100, 200)
            self.assertEqual(report['lowest_samples'], [])
            self.assertIsNone(report['thresholds_mib']['64']['fraction_of_valid_samples'])

    def test_proc_allowlist_and_cgroup_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); proc = root/'proc'; group = root/'groups'
            pid = proc/'123'; pid.mkdir(parents=True); group.mkdir()
            (pid/'status').write_text('VmRSS: 1024 kB\nRssAnon: 512 kB\nVmSwap: 0 kB\nSecret: 12345\n')
            (pid/'comm').write_text('python3\n')
            (pid/'cmdline').write_text('SECRET-ARGUMENT')
            result = diagnostic.process_memory(proc)
            self.assertEqual(result[0]['VmRSS'], 1024**2)
            self.assertNotIn('SECRET', json.dumps(result))
            (pid/'cgroup').write_text('0::/../../outside\n')
            self.assertEqual(diagnostic.cgroup_memory(123, proc, group)['status'], 'invalid_path')
            (pid/'cgroup').write_text('0::/\n')
            (group/'memory.events').write_text('oom 4\noom_kill 2\n')
            (group/'memory.max').write_text('167772160')
            result = diagnostic.cgroup_memory(123, proc, group)
            self.assertEqual(result['memory.events']['oom_kill'], 2)
            self.assertEqual(result['memory.max'], 167772160)
            self.assertIsNone(result['memory.peak'])

    def test_oom_records_are_filtered_and_absence_is_not_a_pass(self):
        journal = '\n'.join(json.dumps(value) for value in (
            {'MESSAGE': 'ordinary log SECRET', '__REALTIME_TIMESTAMP': '1000000'},
            {'MESSAGE': 'Memory cgroup out of memory: Killed process 123 (python3)', '__REALTIME_TIMESTAMP': '2000000'}))
        with patch.object(diagnostic, 'run', side_effect=[(0, journal), (0, '')]) as run:
            report = diagnostic.oom_evidence(1, 10)
            self.assertEqual(len(report['kernel_events']), 1)
            self.assertNotIn('SECRET', json.dumps(report))
            self.assertEqual(report['docker_events'], [])
            self.assertIn('retained_logs_only', report['coverage'])
            self.assertIn('--until', run.call_args_list[1].args)


if __name__ == '__main__':
    unittest.main()
