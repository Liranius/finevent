from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import calendar_observation_review as review


class CalendarObservationReviewTests(unittest.TestCase):
    def test_cross_midnight_failure_then_recovery_is_not_automatic_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            news = root/'news'
            news.mkdir()
            for day, times in [('2026-10-31', [('23-30', 'failed')]),
                               ('2026-11-01', [('00-00', 'success'), ('00-30', 'failed')])]:
                with closing(sqlite3.connect(news/f'{day}.db')) as conn:
                    conn.executescript('CREATE TABLE crawl_records(id INTEGER,crawl_time TEXT,total_items INTEGER);'
                                       'CREATE TABLE crawl_source_status(crawl_record_id INTEGER,platform_id TEXT,status TEXT);')
                    for index, (time, status) in enumerate(times):
                        conn.execute('INSERT INTO crawl_records VALUES(?,?,100)', (index, time))
                        conn.executemany('INSERT INTO crawl_source_status VALUES(?,?,?)',
                                         [(index, f'source{i}', status if i == 0 else 'success') for i in range(9)])
                    conn.commit()
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in news.iterdir()}
            start = review.epoch('2026-10-31T23:00:00+08:00')
            end = review.epoch('2026-10-31T23:59:59+08:00')
            now = review.epoch('2026-11-01T00:40:00+08:00')
            cycles, missing = review.read_cycles(root, start, now)
            summary = review.summarize(cycles, start, end)
            self.assertEqual(missing, [])
            self.assertEqual(summary['observation_cycles'], 1)
            self.assertEqual(summary['observation_cycles_with_incomplete_sources'], [])
            failure, = summary['observation_source_failures']
            self.assertEqual(failure['source'], 'source0')
            self.assertEqual(failure['first_later_success']['status'], 'success')
            self.assertFalse(failure['latest_success'])  # A recovery followed by another failure stays visible.
            self.assertEqual(len(summary['followup_source_failures']), 1)
            self.assertEqual(summary['followup_cycles'], 2)
            self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in news.iterdir()})

    def test_missing_database_is_explicit_and_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            at = int(datetime(2026, 10, 31, 0, tzinfo=timezone.utc).timestamp())
            cycles, missing = review.read_cycles(Path(directory), at, at+1)
            self.assertEqual(cycles, [])
            self.assertEqual(len(missing), 1)
            self.assertFalse(Path(missing[0]).exists())
        with self.assertRaises(RuntimeError):
            review.epoch('2026-10-31T12:00:00')

    def test_unrecovered_and_missing_source_rows_stay_visible(self):
        cycles = [{'at': 100, 'time': 'first', 'sources': [{'platform_id': 'a', 'status': 'failed'}]},
                  {'at': 200, 'time': 'second', 'sources': [{'platform_id': 'b', 'status': 'success'}]}]
        summary = review.summarize(cycles, 100, 150)
        failure, = summary['observation_source_failures']
        self.assertIsNone(failure['first_later_success'])
        self.assertFalse(failure['latest_success'])
        self.assertEqual(summary['observation_cycles_with_incomplete_sources'], ['first'])

    def test_original_report_is_preserved_and_count_mismatch_not_hidden(self):
        release_id = 'calendar-release-20261031T085253Z-8e0911b9'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/release_id).mkdir()
            path = root/release_id/'report.json'
            path.write_text(json.dumps({'id': release_id, 'stage': 'completed', 'status': 'needs_review',
                'observation_started': '2026-10-31T10:00:00+08:00', 'finished_at': '2026-10-31T16:15:00+08:00',
                'trendradar': {'cycles': 13, 'source_failures': 2}, 'deployment': {'release': '/example'}}))
            before = path.read_bytes()
            with patch.object(review, 'BACKUPS', root), patch.object(review, 'read_cycles', return_value=([], [])):
                result = review.inspect(release_id, review.epoch('2026-11-01T00:00:00+08:00'))
                self.assertFalse(result['original_counts_match'])
                self.assertEqual(result['acceptance'], 'not_decided')
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(result['original_report_sha256'], hashlib.sha256(before).hexdigest())
                with self.assertRaises(RuntimeError):
                    review.inspect('../escape')


if __name__ == '__main__':
    unittest.main()
