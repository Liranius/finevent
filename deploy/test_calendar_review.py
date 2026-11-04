from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

import calendar_review as review
import calendar_release as release
from test_calendar_release import evidence, state


class CalendarReviewTests(unittest.TestCase):
    def setUp(self):
        self.report = {'id': 'calendar-release-20261031T085253Z-8e0911b9', 'status': 'needs_review', 'stage': 'completed',
            'deployment': {'release': '/current'}, 'restart': {'status': 'passed'}, 'backup_verification': {'status': 'passed'},
            'observation_started': '2026-10-31T00:00:00+08:00', 'finished_at': '2026-10-31T06:15:00+08:00',
            'resources': {**evidence()['resources'], 'finevent_current_peak_mib': 40}, 'health': evidence()['health'],
            'calendar': release.calendar_results(state(), state(121601, failed='bls'))}
        end = int(release.datetime.fromisoformat(self.report['finished_at']).timestamp())
        self.report['trendradar'] = {'status': 'needs_review', 'cycles': 13, 'source_failures': 1,
            'polls': 346, 'poll_failures': 0, 'last_import': end-30, 'html_mtime': end-60}
        self.result = {'id': 'calendar-observation-review-20261031T163304Z-f80c1674', 'release_id': self.report['id'],
            'original_report_sha256': review.report_sha(self.report), 'status': 'completed_for_review',
            'active_release': '/current', 'observed_release': '/current', 'missing_databases': [],
            'original_counts_match': True, 'observation_cycles': 13, 'observation_cycles_with_incomplete_sources': [],
            'observation_source_failures': [{'source': 'cls-depth', 'at': end-3600,
                'first_later_success': {'status': 'success', 'at': end-1800}, 'latest_success': True}],
            'followup_cycles': 3, 'followup_source_failures': [],
            'source_latest': {f'source{i}': {'at': end+3600, 'status': 'success'} for i in range(9)},
            'cycles': [{'at': end-1800}], 'inspected_at': release.datetime.fromtimestamp(end+3700, release.ZONE).isoformat()}

    def approved(self, result=None, scope=None):
        raw = json.dumps(result or self.result).encode()
        entries = {self.report['id']: {'report_sha256': review.report_sha(self.report),
            'review_sha256': hashlib.sha256(raw).hexdigest(), 'review_id': self.result['id'],
            'decision': 'accepted_with_recovered_source_failures'}}
        if scope is not None:
            entries[self.report['id']].update(followup_scope=scope,
                expected_sources=[f'source{i}' for i in range(9)])
        return raw, entries

    def finite_result(self):
        end = int(release.datetime.fromisoformat(self.report['finished_at']).timestamp())
        self.report['trendradar']['cycles'] = 2
        result = deepcopy(self.result)
        result.update(original_report_sha256=review.report_sha(self.report), observation_cycles=2, followup_cycles=4,
            inspected_at=release.datetime.fromtimestamp(end+5800, release.ZONE).isoformat(),
            source_latest={f'source{i}': {'at': end+5700, 'status': 'success'} for i in range(9)},
            cycles=[{'at': end+offset, 'sources': [{'platform_id': f'source{i}', 'status': 'success'}
                for i in range(9)]} for offset in (-3600, -1800, 300, 2100, 3900, 5700)])
        result['cycles'][0]['sources'][0]['status'] = 'failed'
        result['observation_source_failures'][0]['source'] = 'source0'
        result['cycles'][4]['sources'][0]['status'] = 'failed'
        result['followup_source_failures'] = [{'source': 'source0', 'at': end+3900, 'status': 'failed'}]
        return result

    def test_fixed_window_preserves_later_failures_and_requires_explicit_scope(self):
        result = self.finite_result()
        before = deepcopy(result)
        raw, entries = self.approved(result, 'first_two_after_observation')
        with patch.object(review, 'manifest', return_value=entries):
            release.validate_previous(self.report, self.report['id'], '/current', raw)
            self.assertEqual(result, before)
            self.assertEqual(json.loads(raw)['followup_source_failures'], before['followup_source_failures'])
            self.assertFalse(review.accepted(self.report, raw+b' '))
            self.assertFalse(review.accepted(self.report, None))
        for scope in (None, 'all_after_observation', 'any_two_successful_cycles'):
            raw, entries = self.approved(result, scope)
            with self.subTest(scope=scope), patch.object(review, 'manifest', return_value=entries):
                self.assertFalse(review.accepted(self.report, raw))

    def test_fixed_window_sorts_all_cycles_before_selecting(self):
        result = self.finite_result()
        result['cycles'].reverse()
        raw, entries = self.approved(result, 'first_two_after_observation')
        with patch.object(review, 'manifest', return_value=entries):
            self.assertTrue(review.accepted(self.report, raw))

    def test_fixed_window_cannot_skip_failed_or_incomplete_cycles(self):
        original = self.finite_result()
        for kind in ('first_failed', 'second_failed', 'missing_source', 'duplicate_source', 'wrong_source',
                     'unknown_status', 'missing_observed_source', 'one_followup', 'duplicate_cycle',
                     'missing_timestamp', 'future_cycle', 'count_mismatch', 'latest_failed', 'latest_stale',
                     'latest_wrong_source', 'unrecovered'):
            result = deepcopy(original)
            if kind == 'first_failed': result['cycles'][2]['sources'][0]['status'] = 'failed'
            if kind == 'second_failed': result['cycles'][3]['sources'][0]['status'] = 'failed'
            if kind == 'missing_source': result['cycles'][2]['sources'].pop()
            if kind == 'duplicate_source': result['cycles'][2]['sources'][0] = result['cycles'][2]['sources'][1].copy()
            if kind == 'wrong_source': result['cycles'][2]['sources'][0]['platform_id'] = 'other'
            if kind == 'unknown_status': result['cycles'][2]['sources'][0]['status'] = 'unknown'
            if kind == 'missing_observed_source': result['cycles'][0]['sources'].pop()
            if kind == 'one_followup': result.update(cycles=result['cycles'][:3], followup_cycles=1)
            if kind == 'duplicate_cycle': result['cycles'][3] = deepcopy(result['cycles'][2])
            if kind == 'missing_timestamp': result['cycles'][2].pop('at')
            if kind == 'future_cycle': result['cycles'][-1]['at'] += 1000
            if kind == 'count_mismatch': result['followup_cycles'] = 3
            if kind == 'latest_failed': result['source_latest']['source0']['status'] = 'failed'
            if kind == 'latest_stale': result['source_latest']['source0']['at'] -= 4000
            if kind == 'latest_wrong_source': result['source_latest']['other'] = result['source_latest'].pop('source0')
            if kind == 'unrecovered': result['observation_source_failures'][0]['first_later_success'] = None
            # Rebind the evidence to exercise the rule, rather than merely failing its checksum.
            raw, entries = self.approved(result, 'first_two_after_observation')
            with self.subTest(kind=kind), patch.object(review, 'manifest', return_value=entries):
                self.assertFalse(review.accepted(self.report, raw))

    def test_fixed_window_does_not_override_other_release_gates(self):
        original = deepcopy(self.report)
        for kind in ('memory', 'oom', 'https', 'backup', 'restart', 'supplement', 'short', 'news_api'):
            self.report = deepcopy(original)
            result = self.finite_result()
            if kind == 'memory': self.report['resources']['host_available_min_mib'] = 63
            if kind == 'oom': self.report['resources']['host_oom_kill_delta'] = 1
            if kind == 'https': self.report['health']['/finevent/healthz']['http_status'] = '502'
            if kind == 'backup': self.report['backup_verification']['status'] = 'failed'
            if kind == 'restart': self.report['restart']['status'] = 'failed'
            if kind == 'supplement': self.report['calendar']['nyfed']['healthy'] = False
            if kind == 'short': self.report['observation_started'] = '2026-10-31T00:01:00+08:00'
            if kind == 'news_api':
                self.report.update(verification_profile='news', news_acceptance={'status': 'passed'},
                    news_api={'status': 'failed'})
            result['original_report_sha256'] = review.report_sha(self.report)
            raw, entries = self.approved(result, 'first_two_after_observation')
            with self.subTest(kind=kind), patch.object(review, 'manifest', return_value=entries):
                self.assertTrue(review.accepted(self.report, raw))
                with self.assertRaises(RuntimeError):
                    release.validate_previous(self.report, self.report['id'], '/current', raw)

    def test_exact_review_accepts_successor_without_rewriting_original(self):
        before = deepcopy(self.report)
        raw, entries = self.approved()
        with patch.object(review, 'manifest', return_value=entries):
            release.validate_previous(self.report, self.report['id'], '/current', raw)
            self.assertEqual(self.report, before)
            downloaded = {**self.report, 'observer_status': {'ActiveState': 'inactive'}}
            self.assertTrue(review.accepted(downloaded, raw))
            with self.assertRaises(RuntimeError):
                release.validate_previous(self.report, self.report['id'], '/current')
            self.assertFalse(review.accepted(self.report, raw+b' '))

    def test_resource_and_source_gates_cannot_be_overridden_by_recovery(self):
        raw, entries = self.approved()
        for kind in ('memory', 'oom', 'https', 'supplement', 'backup', 'short', 'release'):
            changed = deepcopy(self.report)
            if kind == 'memory': changed['resources']['host_available_min_mib'] = 63
            if kind == 'oom': changed['resources']['host_oom_kill_delta'] = 1
            if kind == 'https': changed['health']['/finevent/healthz']['http_status'] = '502'
            if kind == 'supplement': changed['calendar']['nyfed']['healthy'] = False
            if kind == 'backup': changed['backup_verification']['status'] = 'failed'
            if kind == 'short': changed['finished_at'] = changed['observation_started']
            if kind == 'release': changed['deployment']['release'] = '/other'
            with self.subTest(kind=kind), patch.object(review, 'manifest', return_value=entries), self.assertRaises(RuntimeError):
                release.validate_previous(changed, changed['id'], '/current', raw)

    def test_incomplete_or_recurrent_failures_cannot_be_approved(self):
        for kind in ('unrecovered', 'late_recovery', 'recurrent', 'missing', 'counts', 'stale', 'few_sources', 'incomplete', 'identity'):
            result = deepcopy(self.result)
            if kind == 'unrecovered': result['observation_source_failures'][0]['first_later_success'] = None
            if kind == 'late_recovery': result['observation_source_failures'][0]['first_later_success']['at'] += 1800
            if kind == 'recurrent': result['followup_source_failures'] = [{'source': 'cls-depth'}]
            if kind == 'missing': result['missing_databases'] = ['a.db']
            if kind == 'counts': result['original_counts_match'] = False
            if kind == 'stale': result['source_latest']['source0']['at'] -= 4000
            if kind == 'few_sources': result['source_latest'].pop('source0')
            if kind == 'incomplete': result['observation_cycles_with_incomplete_sources'] = ['time']
            if kind == 'identity': result['active_release'] = '/different'
            raw, entries = self.approved(result)
            with self.subTest(kind=kind), patch.object(review, 'manifest', return_value=entries):
                self.assertFalse(review.accepted(self.report, raw))


if __name__ == '__main__':
    unittest.main()
