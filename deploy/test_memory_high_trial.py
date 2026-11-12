from pathlib import Path
from contextlib import ExitStack
import json
import tempfile
import unittest
from unittest.mock import patch

import memory_high_trial as trial
import memory_recovery as recovery


class MemoryHighTests(unittest.TestCase):
    def test_swappiness_readback_and_range_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'swappiness'
            with patch.object(trial, 'SWAPPINESS_FILE', path):
                for value in ('0', '60', '200'):
                    path.write_text(value+'\n')
                    self.assertEqual(trial.read_swappiness(), int(value))
                for value in ('-1', '201', 'not a number'):
                    path.write_text(value)
                    with self.assertRaisesRegex(RuntimeError, 'Unrecognized'):
                        trial.read_swappiness()
        with patch.object(recovery, 'command') as command, patch.object(trial, 'read_swappiness', return_value=0):
            with self.assertRaisesRegex(RuntimeError, 'did not take effect'):
                trial.set_swappiness(60)
            command.assert_called_once_with('sysctl', '-w', 'vm.swappiness=60')
            command.reset_mock()
            with self.assertRaisesRegex(RuntimeError, 'Invalid'):
                trial.set_swappiness(201)
            command.assert_not_called()

    def test_restore_releases_throttle_before_restoring_swap_policy(self):
        state = {'previous_high': 'max', 'change_attempted': True,
                 'previous_swappiness': 0, 'swappiness_change_attempted': True}
        order = []
        with patch.object(trial, 'read_high', return_value=str(trial.HIGH)), patch.object(trial, 'read_swappiness', return_value=60), patch.object(trial, 'set_high', side_effect=lambda value: order.append(('high', value))), patch.object(trial, 'set_swappiness', side_effect=lambda value: order.append(('swappiness', value))):
            result = trial.restore_policy(state)
        self.assertEqual(order, [('high', 'max'), ('swappiness', 0)])
        self.assertEqual(result['vm_swappiness'], 0)
        self.assertTrue(state['restored'])

    def test_restore_preserves_independent_host_policy_edits(self):
        state = {'previous_high': 'max', 'change_attempted': True,
                 'previous_swappiness': 0, 'swappiness_change_attempted': True}
        with patch.object(trial, 'read_high', return_value=str(trial.HIGH)), patch.object(trial, 'read_swappiness', return_value=80), patch.object(trial, 'set_high') as high, patch.object(trial, 'set_swappiness') as swap:
            with self.assertRaisesRegex(RuntimeError, 'independently'):
                trial.restore_policy(state)
            high.assert_called_once_with('max')
            swap.assert_not_called()
        self.assertFalse(state.get('restored', False))

    def test_failed_throttle_restore_does_not_restore_swap_avoidance(self):
        state = {'previous_high': 'max', 'change_attempted': True,
                 'previous_swappiness': 0, 'swappiness_change_attempted': True}
        with patch.object(trial, 'read_high', return_value=str(trial.HIGH)), patch.object(trial, 'set_high', side_effect=RuntimeError('systemctl failed')), patch.object(trial, 'set_swappiness') as swap:
            with self.assertRaisesRegex(RuntimeError, 'systemctl failed'):
                trial.restore_policy(state)
            swap.assert_not_called()
        self.assertFalse(state.get('restored', False))

    def test_host_pressure_watchdog_and_independent_policy_change(self):
        report = {}
        # The container is not stalled, but unrelated host workloads are.
        with patch.object(trial.time, 'monotonic', side_effect=[0, 5, 10, 15]), patch.object(trial, 'full_pressure', side_effect=[0, 0, 0, 3000000, 0, 6000000, 0, 9000000]), patch.object(trial, 'read_high', return_value=str(trial.HIGH)), patch.object(trial, 'read_swappiness', return_value=60):
            guard = trial.pressure_guard(report, monitor_host=True)
            self.assertIsNone(guard({}))
            self.assertIsNone(guard({}))
            self.assertIn('Host stalled', guard({}))
        self.assertEqual(report['reclaim_pressure']['highest_full_stall_fraction'], 0)
        self.assertAlmostEqual(report['reclaim_pressure']['highest_host_full_stall_fraction'], 0.6)
        with patch.object(trial, 'full_pressure', return_value=0), patch.object(trial, 'read_high', return_value=str(trial.HIGH)), patch.object(trial, 'read_swappiness', return_value=80):
            guard = trial.pressure_guard({}, monitor_host=True)
            self.assertIn('swappiness changed', guard({}))

    def test_host_pressure_counter_reset_is_not_accepted(self):
        with patch.object(trial.time, 'monotonic', side_effect=[0, 5]), patch.object(trial, 'full_pressure', side_effect=[0, 10, 0, 0]), patch.object(trial, 'read_high', return_value=str(trial.HIGH)), patch.object(trial, 'read_swappiness', return_value=60):
            guard = trial.pressure_guard({}, monitor_host=True)
            self.assertIn('Host memory pressure counter reset', guard({}))

    def test_trial_persists_baseline_before_applying_and_restores_failed_result(self):
        for enable, result in ((True, 'needs_review'), (True, 'completed_for_review'), (False, 'completed_for_review'), (True, 'preflight_changed'), (True, 'apply_failure')):
            with self.subTest(enable=enable, result=result), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                root = Path(temporary)
                previous = root/'prior'; previous.mkdir()
                (previous/'report.json').write_text(json.dumps({'status': 'needs_review', 'finished_at': '2026-10-28'}))
                directory = root/'trial'; directory.mkdir()
                state = {'previous_run': 'prior', 'enable_swap_policy': enable}
                report = {}
                values = {'high': 'max', 'swap': 80 if result == 'preflight_changed' else 0}
                order = []
                def set_swap(value):
                    saved = json.loads((directory/'state.json').read_text())
                    self.assertEqual(saved['previous_swappiness'], 0)
                    self.assertTrue(saved['swappiness_change_attempted'])
                    values['swap'] = value
                    order.append(('swap', value))
                def set_high(value):
                    order.append(('high', value))
                    if result == 'apply_failure' and value == str(trial.HIGH):
                        raise RuntimeError('systemctl failed')
                    values['high'] = value
                def observe(_directory, observed, **kwargs):
                    self.assertEqual(values, {'high': str(trial.HIGH), 'swap': 60 if enable else 0})
                    observed['status'] = result
                stack.enter_context(patch.object(recovery, 'BACKUPS', root))
                stack.enter_context(patch.object(recovery, 'secure_swap_path'))
                stack.enter_context(patch.object(recovery, 'swap_usage', return_value={str(recovery.SWAP_FILE): {'size_bytes': recovery.SWAP_SIZE}}))
                stack.enter_context(patch.object(recovery, 'wait_for_headroom', return_value={'cgroup': {'path': '/system.slice/'+trial.SCOPE}}))
                stack.enter_context(patch.object(recovery, 'validate_limits'))
                stack.enter_context(patch.object(recovery, 'meminfo', return_value={}))
                stack.enter_context(patch.object(recovery, 'observe', side_effect=observe))
                stack.enter_context(patch.object(trial, 'full_pressure', return_value=0))
                stack.enter_context(patch.object(trial, 'read_swappiness', side_effect=lambda: values['swap']))
                stack.enter_context(patch.object(trial, 'read_high', side_effect=lambda: values['high']))
                stack.enter_context(patch.object(trial, 'set_swappiness', side_effect=set_swap))
                stack.enter_context(patch.object(trial, 'set_high', side_effect=set_high))
                if result == 'preflight_changed':
                    with self.assertRaisesRegex(RuntimeError, 'has changed'):
                        trial.run_trial(directory, state, report)
                    self.assertEqual(order, [])
                    self.assertFalse(state.get('change_attempted', False))
                    continue
                if result == 'apply_failure':
                    with self.assertRaisesRegex(RuntimeError, 'systemctl failed'):
                        trial.run_trial(directory, state, report)
                    trial.restore_policy(state)
                    self.assertEqual(values, {'high': 'max', 'swap': 0})
                    self.assertEqual(order, [('swap', 60), ('high', str(trial.HIGH)), ('swap', 0)])
                    continue
                trial.run_trial(directory, state, report)
                expected = [('swap', 60)] if enable else []
                expected.append(('high', str(trial.HIGH)))
                if result == 'needs_review':
                    expected.extend([('high', 'max'), ('swap', 0)])
                    self.assertTrue(state['restored'])
                else:
                    self.assertTrue(state['retained'])
                self.assertEqual(order, expected)
                self.assertEqual(report['effective_swappiness_after_trial'], values['swap'])

    def test_property_must_match_effective_kernel_value(self):
        with patch.object(trial.recovery, 'command') as command, patch.object(trial, 'read_high', return_value='max'):
            with self.assertRaisesRegex(RuntimeError, 'did not take effect'):
                trial.set_high(str(trial.HIGH))
            self.assertEqual(command.call_args.args, ('systemctl', 'set-property', '--runtime', trial.SCOPE, 'MemoryHigh='+str(trial.HIGH)))

    def test_restore_is_scoped_and_refuses_independent_edits(self):
        state = {'previous_high': 'max', 'change_attempted': True}
        with patch.object(trial, 'read_high', return_value=str(trial.HIGH)), patch.object(trial, 'set_high') as set_high:
            result = trial.restore_high(state)
            set_high.assert_called_once_with('max')
            self.assertEqual(result['status'], 'restored')
        with patch.object(trial, 'read_high', return_value='100000000'), patch.object(trial, 'set_high') as set_high:
            with self.assertRaisesRegex(RuntimeError, 'independently'):
                trial.restore_high(state)
            set_high.assert_not_called()
        self.assertEqual(trial.restore_high({}), {'status': 'no_changes'})

    def test_pressure_watchdog_requires_three_consecutive_bad_intervals(self):
        report = {}
        with patch.object(trial.time, 'monotonic', side_effect=[0, 5, 10, 15, 20, 25]), patch.object(trial, 'full_pressure', side_effect=[0, 3000000, 3000000, 6000000, 9000000, 12000000]), patch.object(trial, 'read_high', return_value=str(trial.HIGH)):
            guard = trial.pressure_guard(report)
            self.assertIsNone(guard({}))
            self.assertIsNone(guard({}))  # A good interval resets the sequence.
            self.assertIsNone(guard({}))
            self.assertIsNone(guard({}))
            self.assertIn('stalled', guard({}))
        self.assertAlmostEqual(report['reclaim_pressure']['highest_full_stall_fraction'], 0.6)

    def test_counter_reset_is_not_treated_as_no_pressure(self):
        with patch.object(trial.time, 'monotonic', side_effect=[0, 5]), patch.object(trial, 'full_pressure', side_effect=[10, 0]), patch.object(trial, 'read_high', return_value=str(trial.HIGH)):
            guard = trial.pressure_guard({})
            self.assertIn('counter reset', guard({}))

    def test_observer_restores_throttle_before_collecting_diagnostics(self):
        row = {'at': 100, 'available': 128*recovery.MIB, 'trend_memory': 150*recovery.MIB,
               'trend_swap': 10*recovery.MIB, 'host_swap_used': 20*recovery.MIB,
               'trend_oom_kill': 19, 'finevent_oom_kill': 0, 'host_oom_kill': 19}
        calls = []
        target = {'pid': 123, 'restarts': 0}
        def log(_start):
            calls.append('logs')
            return {'exit': 0, 'report_half_hour_slots': [], 'error_times': [], 'may_be_truncated': False}
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(recovery, 'inspect_target', return_value=target), patch.object(recovery, 'validate_limits'), patch.object(recovery, 'process_identity', return_value=1), patch.object(recovery, 'command') as command, patch.object(recovery.time, 'time', return_value=100), patch.object(recovery.time, 'sleep'), patch.object(recovery, 'sample', side_effect=[row, dict(row, at=105)]), patch.object(recovery, 'log_evidence', side_effect=log), patch.object(recovery, 'public_health', return_value={}), patch.object(recovery.diagnostic, 'oom_evidence', return_value={}):
                command.return_value.stdout = '456'
                report = {}
                recovery.observe(Path(temporary), report, sample_guard=lambda _: 'pressure guard stopped', on_failure=lambda: calls.append('restore'))
            self.assertEqual(calls, ['restore', 'logs'])
            self.assertEqual(report['status'], 'needs_review')
            self.assertEqual(report['observation_issue'], 'pressure guard stopped')

    def test_low_overhead_monitor_detects_pid_reuse_and_limit_changes(self):
        target = {'pid': 123}
        with patch.object(recovery, 'process_identity', side_effect=[1, 3]):
            with self.assertRaisesRegex(RuntimeError, 'restarted'):
                recovery.validate_live_processes(target, 456, (1, 2))
        with patch.object(recovery, 'process_identity', side_effect=[1, 2]), patch.object(recovery.diagnostic, 'cgroup_memory', return_value={'memory.max': 1, 'memory.swap.max': 2}):
            with self.assertRaisesRegex(RuntimeError, 'limits changed'):
                recovery.validate_live_processes(target, 456, (1, 2))
        with tempfile.TemporaryDirectory() as temporary:
            pid = Path(temporary)/'123'; pid.mkdir()
            (pid/'stat').write_text('123 (name (with) spaces) S '+'0 '*18+'98765 0 0\n')
            self.assertEqual(recovery.process_identity(123, Path(temporary)), 98765)


if __name__ == '__main__':
    unittest.main()
