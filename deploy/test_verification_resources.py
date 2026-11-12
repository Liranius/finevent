from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import verification as verify
import verification_resources as gate
from test_verification import create_database

ID = 'trendradar-lazy-20261029T101814Z-c4d27449'
START = 1793269112
COUNTERS = {'trend_oom_kill': 19, 'finevent_oom_kill': 0, 'host_oom_kill': 19}


def evidence():
    return {'id': ID, 'status': 'completed_for_review', 'stage': 'completed',
            'observation_started': verify.stamp(START), 'finished_at': verify.stamp(START+60),
            'candidate_sha256': 'candidate', 'image_id': 'image',
            'resources': {'samples': 10, 'max_sample_interval_seconds': 6, 'host_available_min_mib': 251,
                          'samples_below_64_mib': 0, 'baseline_counters': COUNTERS,
                          **{k+'_delta': 0 for k in COUNTERS}},
            'collection': {'exit': 0, 'may_be_truncated': False, 'error_times': [], 'report_half_hour_slots': [1, 2]},
            'outputs': {'records': [{'items': 235}, {'items': 235}], 'source_statuses': [{'status': 'success'}],
                        'source_failures': 0, 'finevent_poll_count': 42, 'finevent_poll_failures': 0},
            'health': {p: {'exit': 0, 'http_status': '200'} for p in ('/finevent/healthz', '/TrendRadar/content/')},
            'container_after': {'id': 'container', 'pid': 42, 'restarts': 0}}


class ResourceAdmissionTests(unittest.TestCase):
    def write_trial(self, base, report=None, phase='applied'):
        folder = base/ID
        folder.mkdir()
        (folder/'report.json').write_text(json.dumps(report or evidence()))
        (folder/'state.json').write_text(json.dumps({'phase': phase, 'container_id': 'container',
                                                    'container_pid': 42, 'candidate_sha256': 'candidate'}))
        return folder

    def test_completed_trial_required_and_failed_evidence_is_not_admitted(self):
        variants = [({'status': 'observing'}, 'applied'), ({}, 'restored'),
                    ({'rollback': {'status': 'restored'}}, 'applied'),
                    ({'resources': {**evidence()['resources'], 'trend_oom_kill_delta': 1}}, 'applied'),
                    ({'resources': {**evidence()['resources'], 'host_available_min_mib': 63}}, 'applied'),
                    ({'outputs': {**evidence()['outputs'], 'finevent_poll_failures': 1}}, 'applied')]
        for changes, phase in variants:
            with self.subTest(changes=changes, phase=phase), tempfile.TemporaryDirectory() as temporary:
                folder = self.write_trial(Path(temporary), {**evidence(), **changes}, phase)
                with self.assertRaises(RuntimeError):
                    gate.load_trial(folder)

    def test_post_remediation_window_preserves_old_pressure(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); folder = self.write_trial(base)
            path = base/'data.db'; create_database(path)
            with closing(sqlite3.connect(path)) as db:
                for at, available in [(START-60, 40), (START, 251), (START+60, 240), (START+120, 250)]:
                    db.execute('INSERT INTO resources VALUES(?,100,80,?,120,200,0)', (at, available*gate.MIB))
                db.commit()
            original = (folder/'report.json').read_bytes()
            result = {}
            with patch.object(gate.time, 'time', return_value=START+120), patch.object(gate, 'live_check', return_value=({}, 1, {})):
                gate.admit(ID, path, result, verify.readonly, verify.resource_stats, base)
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(result['resources']['host_available_min_mib'], 240)
            self.assertEqual((folder/'report.json').read_bytes(), original)
            with closing(verify.readonly(path)) as db:
                self.assertEqual(db.execute('SELECT MIN(available) FROM resources').fetchone()[0], 40*gate.MIB)

    def test_incomplete_or_reset_post_trial_history_fails_before_live_check(self):
        variants = [('gap', [0, 300], [251, 250], [200, 200], [0, 0]),
                    ('missing', [0, 60], [251, 0], [200, 200], [0, 0]),
                    ('pressure', [0, 60], [251, 63], [200, 200], [0, 0]),
                    ('reset', [0, 60], [251, 250], [200, 100], [0, 0]),
                    ('oom', [0, 60], [251, 250], [200, 200], [0, 1])]
        for name, offsets, avail, peaks, oom in variants:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary); self.write_trial(base)
                path = base/'data.db'; create_database(path)
                with closing(sqlite3.connect(path)) as db:
                    for i, offset in enumerate(offsets):
                        db.execute('INSERT INTO resources VALUES(?,100,80,?,120,?,?)', (START+offset, avail[i]*gate.MIB, peaks[i], oom[i]))
                    db.commit()
                with patch.object(gate.time, 'time', return_value=START+max(offsets)), patch.object(gate, 'live_check') as live:
                    with self.assertRaisesRegex(RuntimeError, 'Post-remediation'):
                        gate.admit(ID, path, {}, verify.readonly, verify.resource_stats, base)
                    live.assert_not_called()

    def test_live_gate_checks_patch_identity_counters_and_headroom(self):
        target = {**evidence()['container_after'], 'cgroup': {'memory.high': 160*gate.MIB}}
        baseline = {**COUNTERS, 'available': 250*gate.MIB}
        def command(*args):
            return subprocess.CompletedProcess(args, 0, 'image' if args[0] == 'docker' else '123', '')
        variants = [(baseline, 'candidate', target, True),
                    ({**baseline, 'host_oom_kill': 20}, 'candidate', target, False),
                    ({**baseline, 'trend_oom_kill': 0}, 'candidate', target, False),
                    ({**baseline, 'available': 127*gate.MIB}, 'candidate', target, False),
                    (baseline, 'old', target, False),
                    (baseline, 'candidate', {**target, 'pid': 43}, False)]
        for row, sha, current, passed in variants:
            with self.subTest(row=row, sha=sha, pid=current['pid']), patch.object(gate.recovery, 'inspect_target', return_value=current), patch.object(gate.recovery, 'validate_limits'), patch.object(Path, 'read_text', return_value='60'), patch.object(gate.recovery, 'sha', return_value=sha), patch.object(gate.recovery, 'command', side_effect=command), patch.object(gate.recovery, 'sample', return_value=row):
                if passed:
                    self.assertEqual(gate.live_check(evidence())[2], baseline)
                else:
                    with self.assertRaises(RuntimeError):
                        gate.live_check(evidence())

    def test_process_detection_exports_no_arguments_and_ignores_scheduler(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); group = base/'cgroups'/'group'; group.mkdir(parents=True)
            (group/'cgroup.procs').write_text('1 2 3')
            proc = base/'proc'
            for pid, argv in [('1', b'/usr/bin/python3\0-m\0trendradar\0SECRET'), ('2', b'supercronic\0SECRET')]:
                path = proc/pid; path.mkdir(parents=True)
                (path/'cmdline').write_bytes(argv)
            with patch.object(gate.recovery, 'process_identity', return_value=700):
                jobs = gate.trend_jobs('/group', proc, base/'cgroups')
            self.assertEqual(jobs, [{'pid': 1, 'start_ticks': 700}])
            self.assertNotIn('SECRET', json.dumps(jobs))

    def test_overlap_requires_actual_job_and_strictly_overlapping_collector(self):
        monitor = gate.LiveProbe.__new__(gate.LiveProbe)
        sample = {'available': 250*gate.MIB, 'trend_memory': 91*gate.MIB, 'at_precise': 102.5, 'trend_jobs': [{'pid': 2}]}
        ai = {'started': 100, 'completed': 104, 'overlapping_polls': [{'started': 100, 'completed': 103, 'success': 1}]}
        for name, changes, polls, expected in [('yes', {}, ai['overlapping_polls'], 'observed'),
                                             ('idle', {'trend_jobs': []}, ai['overlapping_polls'], 'not_established'),
                                             ('boundary', {'at_precise': 100.5}, ai['overlapping_polls'], 'not_established'),
                                             ('disjoint', {}, [{'started': 100, 'completed': 101, 'success': 1}], 'not_established'),
                                             ('no_poll', {}, [], 'not_established')]:
            with self.subTest(name=name):
                monitor.evidence = {'samples': [{**sample, **changes}]}
                monitor.finish({**ai, 'overlapping_polls': polls})
                self.assertEqual(monitor.evidence['three_way_overlap'], expected)

    def test_live_monitor_does_not_accept_new_pressure(self):
        monitor = gate.LiveProbe.__new__(gate.LiveProbe)
        monitor.target = {'cgroup': {'path': '/group'}}
        monitor.pid = 1; monitor.identities = (1, 2)
        monitor.baseline = deepcopy(COUNTERS); monitor.evidence = {'samples': []}
        with patch.object(gate.recovery, 'validate_live_processes'), patch.object(gate.recovery, 'sample', return_value={**COUNTERS, 'available': 63*gate.MIB}), patch.object(gate, 'trend_jobs', return_value=[]):
            with self.assertRaisesRegex(RuntimeError, 'New memory pressure'):
                monitor.sample()
        self.assertEqual(len(monitor.evidence['samples']), 1)

    def test_continuation_requires_verified_release_and_no_unexplained_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary)
            previous = {'status': 'completed_for_review', 'resource_admission': {'status': 'passed', 'trial_id': ID, 'live': COUNTERS},
                        'ai': {'status': 'succeeded'}, 'rollback': {'status': 'passed', 'final_release': str(release.resolve())},
                        'system_after': {'finevent': {'ExecMainStartTimestamp': 'verified-start'}},
                        'resources_after': COUNTERS, 'finished_at': verify.stamp(START+120)}
            self.assertEqual(gate.continuation_start(previous, ID, release, 'verified-start'), START+120)
            for item, current, began in [(previous, release/'other', 'verified-start'),
                                        (previous, release, 'later-restart'),
                                        ({**previous, 'rollback': {'status': 'incomplete'}}, release, 'verified-start'),
                                        ({**previous, 'resources_after': {**COUNTERS, 'host_oom_kill': 20}}, release, 'verified-start')]:
                with self.subTest(item=item, current=current, began=began), self.assertRaises(RuntimeError):
                    gate.continuation_start(item, ID, current, began)

    def test_millisecond_overlap_captures_short_call_but_not_boundary_straddle(self):
        monitor = gate.LiveProbe.__new__(gate.LiveProbe)
        timing = {'model_started_ms': 100100, 'model_completed_ms': 100900,
                  'collector_started_ms': 100200, 'collector_completed_ms': 100500, 'collector_success': 1}
        row = {'available': 250*gate.MIB, 'trend_memory': 91*gate.MIB, 'sample_started_ms': 100300,
               'sample_completed_ms': 100310, 'trend_jobs': [{'pid': 2}]}
        for changes, expected in [({}, 'observed'), ({'sample_completed_ms': 100510}, 'not_established'),
                                  ({'sample_started_ms': 100190}, 'not_established'), ({'trend_jobs': []}, 'not_established')]:
            with self.subTest(changes=changes):
                monitor.evidence = {'samples': [{**row, **changes}]}
                monitor.finish({'timing': timing})
                self.assertEqual(monitor.evidence['three_way_overlap'], expected)

    def test_alignment_waits_for_new_job_then_stops_waiting_once(self):
        monitor = gate.LiveProbe.__new__(gate.LiveProbe)
        monitor.target = {'cgroup': {'path': '/group'}}; monitor.evidence = {}
        old, new = {'pid': 1, 'start_ticks': 1}, {'pid': 2, 'start_ticks': 2}
        row = {'available': 250*gate.MIB, 'sample_completed_ms': 100000, 'trend_jobs': [new]}
        with patch.object(gate, 'trend_jobs', side_effect=[[old], [old], [], [new]]), patch.object(gate.time, 'monotonic', return_value=1000), patch.object(gate.time, 'sleep') as sleep, patch.object(monitor, 'sample', return_value=row):
            monitor.wait_for_next_job()
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(monitor.evidence['triggered_jobs'], [new])

    def test_alignment_timeout_and_disappearing_job_do_not_trigger(self):
        monitor = gate.LiveProbe.__new__(gate.LiveProbe)
        monitor.target = {'cgroup': {'path': '/group'}}; monitor.evidence = {}
        with patch.object(gate, 'trend_jobs', return_value=[]), patch.object(gate.time, 'monotonic', side_effect=[0, 1921]):
            with self.assertRaisesRegex(RuntimeError, 'no paid request sent'):
                monitor.wait_for_next_job()
        with patch.object(gate, 'trend_jobs', side_effect=[[], [{'pid': 2}]]), patch.object(gate.time, 'monotonic', return_value=1000), patch.object(monitor, 'sample', return_value={'available': 250*gate.MIB, 'trend_jobs': []}):
            with self.assertRaisesRegex(RuntimeError, 'ended before the trigger'):
                monitor.wait_for_next_job()
        self.assertNotIn('triggered_jobs', monitor.evidence)

    def test_followup_covers_job_completion_without_starting_another_job(self):
        monitor = gate.LiveProbe.__new__(gate.LiveProbe)
        job = {'pid': 2}
        monitor.evidence = {'triggered_jobs': [job], 'triggered_at_ms': 100000}
        with patch.object(monitor, 'sample', side_effect=[{'trend_jobs': [job]}, {'trend_jobs': []}]), patch.object(gate.time, 'sleep') as sleep, patch.object(gate.recovery, 'log_evidence', return_value={'exit': 0}), patch.object(gate.recovery, 'public_health', return_value={'ok': True}):
            monitor.follow_triggered_job()
        sleep.assert_called_once_with(0.1)
        self.assertEqual(monitor.evidence['collection'], {'exit': 0})


if __name__ == '__main__':
    unittest.main()
