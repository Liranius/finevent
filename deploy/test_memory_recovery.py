import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import memory_recovery as recovery


class RecoveryTests(unittest.TestCase):
    def test_docker_configuration_is_not_proof_of_effective_swap_limit(self):
        target = {'memory': recovery.MEMORY, 'memory_swap': recovery.TOTAL_MEMORY_SWAP,
                  'cgroup': {'memory.max': recovery.MEMORY, 'memory.swap.max': 'max'}}
        with self.assertRaisesRegex(RuntimeError, 'Actual cgroup'):
            recovery.validate_limits(target, recovery.MEMORY, recovery.TOTAL_MEMORY_SWAP)
        target['cgroup']['memory.swap.max'] = 256*recovery.MIB
        recovery.validate_limits(target, recovery.MEMORY, recovery.TOTAL_MEMORY_SWAP)

    def test_fstab_preserves_entries_and_rejects_duplicate_swap(self):
        original = '# Existing mounts\nUUID=example / ext4 defaults 0 1\n'
        updated = recovery.add_fstab_entry(original)
        self.assertTrue(updated.startswith(original))
        self.assertIn(' none swap sw,nofail 0 0\n', updated)
        with self.assertRaisesRegex(RuntimeError, 'existing fstab'):
            recovery.add_fstab_entry(updated)

    def test_allocation_never_claims_or_overwrites_an_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); swap = directory/'swap'
            swap.write_bytes(b'existing-data')
            state = {'phase': 'preparing'}
            with patch.object(recovery, 'SWAP_FILE', swap):
                with self.assertRaises(FileExistsError):
                    recovery.allocate_swap(directory, state, size=4096)
                self.assertNotIn('created_swap', state)
                self.assertEqual(swap.read_bytes(), b'existing-data')
                swap.unlink()
                recovery.allocate_swap(directory, state, size=4096)
            self.assertEqual(swap.stat().st_size, 4096)
            self.assertEqual(swap.read_bytes(), bytes(4096))
            self.assertEqual(state['swap_inode'], swap.stat().st_ino)
            self.assertTrue(json.loads((directory/'state.json').read_text())['created_swap'])

    def test_log_evidence_requires_distinct_future_collection_slots(self):
        text = '\n'.join((
            '2026-10-28T12:01:00Z HTML报告已生成: old-job.html',
            '2026-10-28T12:30:35Z HTML报告已生成: private-path.html',
            '2026-10-28T12:30:36Z 最新报告已更新: same-job.html',
            '2026-10-28T13:00:40Z HTML报告已生成: next-job.html',
            '2026-10-28T13:00:41Z 分析流程执行出错: SECRET-ERROR',
        ))
        start = int(recovery.datetime.fromisoformat('2026-10-28T12:00:30+00:00').timestamp())
        with patch.object(recovery, 'bounded_logs', return_value=(0, text, False)):
            result = recovery.log_evidence(start)
        self.assertEqual(len(result['report_half_hour_slots']), 2)
        self.assertEqual(len(result['error_times']), 1)
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('private-path', json.dumps(result))

    def test_old_oom_counters_are_baselines_not_new_failures(self):
        row = {'at': 100, 'available': 128*recovery.MIB, 'trend_memory': 150*recovery.MIB,
               'trend_swap': 10*recovery.MIB, 'host_swap_used': 20*recovery.MIB,
               'trend_oom_kill': 19, 'finevent_oom_kill': 0, 'host_oom_kill': 19}
        result = recovery.summarize([row, dict(row, at=105)], row)
        self.assertEqual(result['trend_oom_kill_delta'], 0)
        self.assertEqual(result['max_sample_interval_seconds'], 5)
        result = recovery.summarize([row, dict(row, at=105, trend_oom_kill=20)], row)
        self.assertEqual(result['trend_oom_kill_delta'], 1)

    def rollback_fixture(self, directory):
        root = Path(directory); fstab = root/'fstab'; fstab.write_text('original fstab\n')
        (root/'fstab.before').write_bytes(fstab.read_bytes())
        swap = root/'swap'; swap.write_bytes(bytes(4096))
        state = {'created_swap': True, 'limits_attempted': True,
                 'swap_inode': swap.stat().st_ino, 'swap_device': swap.stat().st_dev,
                 'fstab_before_sha': recovery.sha(fstab), 'fstab_planned_sha': 'other-hash',
                 'previous': {'memory': 160*recovery.MIB, 'memory_swap': 160*recovery.MIB}}
        target = {'memory': recovery.MEMORY, 'memory_swap': recovery.TOTAL_MEMORY_SWAP,
                  'cgroup': {'memory.current': 20*recovery.MIB, 'memory.swap.current': 10*recovery.MIB}}
        return root, fstab, swap, state, target

    def test_rollback_refuses_swapoff_without_ram_headroom(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, fstab, swap, state, target = self.rollback_fixture(temporary)
            with patch.object(recovery, 'FSTAB', fstab), patch.object(recovery, 'SWAP_FILE', swap), patch.object(recovery, 'secure_swap_path'), patch.object(recovery, 'inspect_target', return_value=target), patch.object(recovery, 'wait_for_headroom', return_value=target), patch.object(recovery, 'swap_usage', return_value={str(swap): {'used_bytes': 100*recovery.MIB}}), patch.object(recovery, 'meminfo', return_value={'MemAvailable': 150*recovery.MIB}), patch.object(recovery, 'command') as command:
                with self.assertRaisesRegex(RuntimeError, 'Insufficient RAM'):
                    recovery.rollback_trial(root, state)
                command.assert_not_called()
                self.assertTrue(swap.exists())

    def test_rollback_refuses_overwriting_external_fstab_edits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, fstab, swap, state, target = self.rollback_fixture(temporary)
            fstab.write_text('administrator changed mounts\n')
            with patch.object(recovery, 'FSTAB', fstab), patch.object(recovery, 'SWAP_FILE', swap), patch.object(recovery, 'secure_swap_path'), patch.object(recovery, 'inspect_target', return_value=target), patch.object(recovery, 'command') as command:
                with self.assertRaisesRegex(RuntimeError, 'fstab changed independently'):
                    recovery.rollback_trial(root, state)
                command.assert_not_called()

    def test_rollback_keeps_replaced_swap_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, fstab, swap, state, target = self.rollback_fixture(temporary)
            state['swap_inode'] += 1
            with patch.object(recovery, 'FSTAB', fstab), patch.object(recovery, 'SWAP_FILE', swap), patch.object(recovery, 'secure_swap_path'), patch.object(recovery, 'inspect_target', return_value=target), patch.object(recovery, 'command') as command:
                with self.assertRaisesRegex(RuntimeError, 'was replaced'):
                    recovery.rollback_trial(root, state)
                command.assert_not_called()
                self.assertTrue(swap.exists())

    def test_apply_limits_before_activating_host_swap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); evidence = root/'evidence'; evidence.mkdir()
            fstab = root/'fstab'; fstab.write_text('original mounts\n')
            swap = root/'swap-dir'/'swapfile'
            old = {'memory': 160*recovery.MIB, 'memory_swap': 160*recovery.MIB}
            new = {'memory': recovery.MEMORY, 'memory_swap': recovery.TOTAL_MEMORY_SWAP,
                   'cgroup': {'memory.max': recovery.MEMORY, 'memory.swap.max': 256*recovery.MIB}}
            state = {'phase': 'preflight'}
            def allocate(directory, current_state):
                swap.write_bytes(bytes(4096))
                current_state.update(created_swap=True, swap_inode=swap.stat().st_ino, swap_device=swap.stat().st_dev)
            def command(*args, **_kwargs):
                return SimpleNamespace(stdout=json.dumps({'filesystems': [{'fstype': 'ext4', 'options': 'rw,relatime'}]}))
            with patch.object(recovery, 'FSTAB', fstab), patch.object(recovery, 'SWAP_FILE', swap), patch.object(recovery, 'secure_swap_path'), patch.object(recovery, 'meminfo', return_value={'SwapTotal': 0}), patch.object(recovery.shutil, 'disk_usage', return_value=SimpleNamespace(free=4*recovery.SWAP_SIZE)), patch.object(recovery, 'wait_for_headroom', return_value=old), patch.object(recovery, 'inspect_target', return_value=new), patch.object(recovery, 'allocate_swap', side_effect=allocate), patch.object(recovery, 'swap_usage', return_value={str(swap): {'used_bytes': 0}}), patch.object(recovery, 'replace_fstab', side_effect=lambda content, _: fstab.write_bytes(content)), patch.object(recovery, 'command', side_effect=command) as commands:
                recovery.apply_trial(evidence, state)
            calls = [call.args[:2] for call in commands.call_args_list]
            self.assertLess(calls.index(('docker', 'update')), calls.index(('swapon', str(swap))))
            self.assertEqual(state['phase'], 'applied')
            self.assertIn('none swap sw,nofail', fstab.read_text())
            self.assertEqual((evidence/'fstab.before').read_text(), 'original mounts\n')

    def test_successful_rollback_restores_fstab_and_removes_only_owned_swap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, fstab, swap, state, target = self.rollback_fixture(temporary)
            restored = dict(target, memory=160*recovery.MIB, memory_swap=160*recovery.MIB)
            with patch.object(recovery, 'FSTAB', fstab), patch.object(recovery, 'SWAP_FILE', swap), patch.object(recovery, 'secure_swap_path'), patch.object(recovery, 'inspect_target', side_effect=[target, restored]), patch.object(recovery, 'wait_for_headroom', return_value=target), patch.object(recovery, 'swap_usage', return_value={str(swap): {'used_bytes': 10*recovery.MIB}}), patch.object(recovery, 'meminfo', return_value={'MemAvailable': 200*recovery.MIB}), patch.object(recovery, 'replace_fstab', side_effect=lambda content, _: fstab.write_bytes(content)), patch.object(recovery, 'command') as commands:
                result = recovery.rollback_trial(root, state)
            self.assertEqual(result['status'], 'rolled_back')
            self.assertFalse(swap.exists())
            self.assertEqual(fstab.read_text(), 'original fstab\n')
            self.assertEqual(commands.call_args_list[0].args, ('swapoff', str(swap)))


if __name__ == '__main__':
    unittest.main()
