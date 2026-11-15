from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import json
import os
import sqlite3
import tempfile
import unittest

import calendar_release
import calendar_review
import trend_settings as settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.state = self.folder / 'state'
        self.state.mkdir()
        self.config = self.folder / 'config.yaml'
        self.config.write_bytes(b'# retained\nplatforms:\n  sources:\n    - id: first # keep\n      name: First\n'
                                b'    - {id: second, name: Second}\nsecret: private-token\n')
        os.utime(self.config, (1800, 1800))
        (self.folder / 'frequency_words.txt').write_text('[WORD_GROUPS]\nold\n')

    def request(self, op='read', **args):
        return settings.handle(dict(op=op, **args), self.folder, self.state)

    def test_edit_partial_stale_undo_and_raw_retention(self):
        initial = self.request()
        self.assertNotIn('private-token', json.dumps(initial))
        change = dict(section='sources', revision=initial['source_revision'], value=['second'])
        saved = self.request('write', **change)
        self.assertEqual(settings.enabled_ids(saved['sources']), ['second'])
        self.assertIn('# keep', self.config.read_text())
        self.assertIn('secret: private-token', self.config.read_text())
        with self.assertRaises(settings.Conflict):
            self.request('write', **change)
        words = self.request('write', section='keywords', value='[WORD_GROUPS]\n新词\n',
                             revision=initial['keyword_revision'])
        restored = self.request('write', section='sources', revision=words['source_revision'], undo=True)
        self.assertEqual(settings.enabled_ids(restored['sources']), ['first','second'])
        self.assertEqual(restored['keywords'], words['keywords'])
        all_off = self.request('write', section='sources', revision=restored['source_revision'], value=[])
        self.assertEqual(settings.enabled_ids(all_off['sources']), [])
        self.assertEqual(settings.expected_at(self.request('periods'), 1860), ['first','second'])
        self.assertEqual(settings.expected_at(self.request('periods'), 60), None)

    def test_reject_bad_input_and_failed_write_preserves_previous_undo(self):
        initial = self.request()
        for ids in (['unknown'], ['first', 'first'], None):
            with self.assertRaises(settings.Invalid):
                self.request('write', section='sources', revision=initial['source_revision'], value=ids)
        args = dict(section='keywords', revision=initial['keyword_revision'])
        with self.assertRaises(settings.Invalid):
            self.request('write', **args, value='/[/')
        saved = self.request('write', **args, value='new')
        args['revision'] = saved['keyword_revision']
        with patch.object(settings.os, 'replace', side_effect=OSError('disk')), self.assertRaises(OSError):
            self.request('write', **args, value='other')
        self.assertEqual(self.request()['keywords'], 'new')
        self.assertEqual(self.request('write', **args, undo=True)['keywords'], initial['keywords'])
        self.assertFalse(list(self.folder.glob('.finevent-*')))

    def test_release_checks_each_cycles_source_ids(self):
        news = self.folder / 'news'
        news.mkdir()
        times = ['08-30','09-00']
        with closing(sqlite3.connect(news/'1970-01-01.db')) as conn, conn:
            conn.executescript('CREATE TABLE crawl_records(id,crawl_time,total_items);'
                               'CREATE TABLE crawl_source_status(crawl_record_id,platform_id,status);')
            conn.executemany('INSERT INTO crawl_records VALUES(?,?,10)', enumerate(times,1))
            conn.executemany("INSERT INTO crawl_source_status VALUES(?,?,'success')", [(1,'first'),(2,'second')])
        with closing(sqlite3.connect(self.folder/'finevent.db')) as conn, conn:
            conn.executescript("CREATE TABLE polls(source,started,completed,success);"
                               "INSERT INTO polls VALUES('trendradar',1800,3600,1);")
        (self.folder/'index.html').write_text('OK')
        periods = [dict(start=0,end=2700,ids=['first']),dict(start=2700,end=None,ids=['second'])]
        with patch.object(calendar_release, 'TREND', self.folder), patch.object(calendar_release,'DATA',self.folder), \
                patch.object(settings,'observation_periods',return_value=periods):
            report = calendar_release.trend_outputs(1800,3600)
            self.assertEqual(report['status'], 'passed')
            self.assertEqual(report['expected_by_cycle'], {'1800':['first'], '3600':['second']})
            periods[1]['ids'] = ['first']  # Equal count, wrong identity.
            self.assertEqual(calendar_release.trend_outputs(1800,3600)['status'], 'needs_review')

    def test_recovery_review_uses_frozen_and_followup_config(self):
        review = dict(original_trendradar={'expected_by_cycle': {'100':['a']}},
                      observation_cycles=1,followup_cycles=2,cycles=[])
        for at, ids in [(100,['a']), (200,['b','c']), (300,['c'])]:
            review['cycles'].append(dict(at=at,expected_sources=ids,
                sources=[dict(platform_id=s,status='success') for s in ids]))
        scope = {'followup_scope':'first_two_after_observation'}
        self.assertTrue(calendar_review.followup_accepted(scope,review,90,150,305))
        review['cycles'][1]['sources'][0]['platform_id']='a'
        self.assertFalse(calendar_review.followup_accepted(scope,review,90,150,305))
        self.assertFalse(calendar_review.followup_accepted({},review,90,150,305))
