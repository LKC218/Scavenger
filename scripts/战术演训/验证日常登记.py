"""在独立沙盒验证日常登记交接；七天时间仅在沙盒受控，不触碰本机旧存量。"""
import contextlib
from datetime import datetime
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from 日常登记 import Registration, read_registry, observation_window, members

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('routine_sandbox_cleaner', ROOT / 'scripts/维护清理/清理工具.py')
cleaner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cleaner
spec.loader.exec_module(cleaner)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory(prefix='登记-', dir=SANDBOX)
        self.addCleanup(self.sandbox.cleanup)
        self.root = Path(self.sandbox.name)
        cleaner.ROOT = self.root
        self.commands = []
        self.cli('init')
        self.stock = self.root / 'tmp/旧存量.bin'
        self.stock.parent.mkdir(exist_ok=True)
        self.stock.write_bytes(b'stock')
        self.cli('protect', '--task', '其他会话任务', '--path', 'local-data/其他')
        self.cli('enable-auto', '--approved-sha', cleaner.digest(cleaner.checked(cleaner.STATE + '/登记.json')),
                 '--note', '仅隔离沙盒策略测试')
        self.now = time.time() + 1
        self.clock = patch('time.time', side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def cli(self, *args):
        self.commands.append(args[0])
        with patch('sys.argv', ['清理工具.py', *args]), contextlib.redirect_stdout(io.StringIO()):
            cleaner.main()

    def make(self, *, retained=False, failed=False):
        output = self.root / 'tmp/运行/战术演训日常测试/move-v3/unique'
        reg = Registration(self.root, 'move-v3', 'msedge', 'unique', output, retained=retained)
        reg.cli = self.cli
        reg.start([self.root / 'scripts/战术演训'])
        output.mkdir(parents=True)
        result = {'passed': not failed, 'status': '检查失败' if failed else '通过',
                  'cleanup_confirmed': True, 'started_epoch': self.now, 'finished_epoch': self.now,
                  'retention': reg.descriptor(retained), 'artifacts': []}
        (output / '结果摘要.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf8')
        if failed:
            (output / '检查').mkdir()
            (output / '检查/失败.png').write_bytes(b'isolated-image')
            (output / '检查/失败日志.txt').write_text('原检查失败', encoding='utf8')
            result['artifacts'] = [{'path': name} for name in ('检查/失败.png', '检查/失败日志.txt')]
        return reg, result

    def test_success_freezes_one_summary_and_only_closes_self(self):
        reg, result = self.make()
        other = read_registry(self.root)['tasks']['其他会话任务'].copy()
        summary = (reg.output / '结果摘要.json').read_bytes()
        state = reg.finish(result)
        registry = read_registry(self.root)
        self.assertTrue(state['ok'])
        self.assertEqual(registry['tasks'][reg.task]['status'], 'closed')
        self.assertEqual(registry['tasks']['其他会话任务'], other)
        self.assertEqual(len(registry['items']), 1)
        self.assertEqual(len(registry['items'][0]['files']), 1)
        self.assertEqual((reg.output / '结果摘要.json').read_bytes(), summary)
        self.assertFalse(any(cmd in self.commands for cmd in ('tick', 'quarantine', 'purge', 'accept')))

    def test_failed_scene_freezes_three_diagnostics_and_keeps_failure(self):
        reg, result = self.make(failed=True)
        self.assertTrue(reg.finish(result)['ok'])
        self.assertFalse(result['passed'])
        self.assertEqual(len(read_registry(self.root)['items'][0]['files']), 3)

    def test_uncertain_exit_stays_active_without_enroll(self):
        reg, result = self.make()
        result['cleanup_confirmed'] = False
        self.assertFalse(reg.finish(result)['ok'])
        self.assertEqual(read_registry(self.root)['tasks'][reg.task]['status'], 'active')
        self.assertFalse(read_registry(self.root)['items'])

    def test_manual_consumer_keeps_active_protection(self):
        reg, result = self.make(retained=True)
        self.assertTrue(reg.finish(result, retained=True)['ok'])
        self.assertEqual(read_registry(self.root)['tasks'][reg.task]['status'], 'active')
        self.assertFalse(read_registry(self.root)['items'])

    def test_enroll_failure_keeps_protection_and_original_output(self):
        reg, result = self.make()
        before = members(reg.output)
        def fail(*args):
            if args[0] == 'enroll':
                raise RuntimeError('登记失败')
            return self.cli(*args)
        reg.cli = fail
        with self.assertRaisesRegex(RuntimeError, '登记失败'):
            reg.finish(result)
        self.assertEqual(members(reg.output), before)
        self.assertEqual(read_registry(self.root)['tasks'][reg.task]['status'], 'active')
        self.assertFalse(read_registry(self.root)['items'])

    def test_close_failure_does_not_modify_frozen_summary(self):
        reg, result = self.make()
        before = members(reg.output)
        def fail(*args):
            if args[0] == 'close':
                raise RuntimeError('关闭失败')
            return self.cli(*args)
        reg.cli = fail
        with self.assertRaisesRegex(RuntimeError, '关闭失败'):
            reg.finish(result)
        self.assertEqual(members(reg.output), before)
        self.assertEqual(read_registry(self.root)['tasks'][reg.task]['status'], 'active')
        self.assertEqual(len(read_registry(self.root)['items']), 1)

    def test_changed_after_enroll_stays_protected(self):
        reg, result = self.make()
        def mutate(*args):
            self.cli(*args)
            if args[0] == 'enroll':
                (reg.output / '后来出现.txt').write_text('未经登记', encoding='utf8')
        reg.cli = mutate
        with self.assertRaisesRegex(RuntimeError, '指纹变化'):
            reg.finish(result)
        self.assertEqual(read_registry(self.root)['tasks'][reg.task]['status'], 'active')

    def test_unclassified_output_is_not_enrolled_even_if_listed(self):
        reg, result = self.make(failed=True)
        (reg.output / '检查/整轮轨迹.zip').write_bytes(b'outside retention policy')
        result['artifacts'].append({'path': '检查/整轮轨迹.zip'})
        with self.assertRaisesRegex(RuntimeError, '未归类文件'):
            reg.finish(result)
        registry = read_registry(self.root)
        self.assertFalse(registry['items'])
        self.assertEqual(registry['tasks'][reg.task]['status'], 'active')

    def test_existing_output_and_stock_are_not_adopted(self):
        reg, _ = self.make()
        before = read_registry(self.root)
        other = Registration(self.root, 'move-v3', 'msedge', 'unique', reg.output)
        other.cli = self.cli
        with self.assertRaises(FileExistsError):
            other.start([])
        self.assertEqual(read_registry(self.root), before)
        self.assertEqual(self.stock.read_bytes(), b'stock')

    def test_lock_blocks_before_protection_or_output_creation(self):
        reg = Registration(self.root, 'move-v3', 'msedge', 'locked', self.root / 'tmp/locked')
        reg.cli = self.cli
        before = read_registry(self.root)
        with cleaner.lock():
            with self.assertRaises(FileExistsError):
                reg.start([])
        self.assertEqual(read_registry(self.root), before)
        self.assertFalse(reg.output.exists())

    def test_seven_day_recycle_then_seven_day_purge_preserves_later_files(self):
        reg, result = self.make()
        reg.finish(result)
        extra = reg.output / '未登记新增文件.txt'
        extra.write_bytes(b'not-in-frozen-list')
        self.now += 7 * cleaner.DAY - 1
        self.cli('tick')
        self.assertTrue((reg.output / '结果摘要.json').exists())
        self.now += 2
        self.cli('tick')
        self.assertFalse((reg.output / '结果摘要.json').exists())
        self.assertTrue(extra.exists())
        receipt_path = next((self.root / cleaner.STATE / '回收').glob('*/回执.json'))
        self.assertEqual(json.loads(receipt_path.read_text('utf8'))['status'], '完成')
        self.now += 7 * cleaner.DAY - 1
        self.cli('tick')
        self.assertEqual(json.loads(receipt_path.read_text('utf8'))['status'], '完成')
        self.now += 2
        self.cli('tick')
        self.assertEqual(json.loads(receipt_path.read_text('utf8'))['status'], '已清除')
        self.assertTrue(extra.exists())
        self.assertEqual(self.stock.read_bytes(), b'stock')

    def test_external_consumer_blocks_after_seven_days(self):
        reg, result = self.make()
        reg.finish(result)
        self.cli('protect', '--task', '实际消费者', '--path', reg.output.relative_to(self.root).as_posix())
        self.now += 8 * cleaner.DAY
        self.cli('tick')
        self.assertTrue((reg.output / '结果摘要.json').exists())

    def test_workday_window_rejects_five_runs_on_same_day(self):
        zone = ZoneInfo('America/Los_Angeles')
        def stamp(day): return datetime(2026, 11, day, 12, tzinfo=zone).timestamp()
        rows = [{'finished_epoch': stamp(16)}] * 5
        self.assertFalse(observation_window(rows, stamp(16))['enough_time_and_runs'])
        rows = [{'finished_epoch': stamp(day)} for day in (12, 12, 13, 16, 16)]
        self.assertTrue(observation_window(rows, stamp(16))['enough_time_and_runs'])


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sandbox', type=Path, required=True)
    args = parser.parse_args()
    SANDBOX = args.sandbox.resolve()
    SANDBOX.relative_to((ROOT / 'tmp/运行/战术演训日常测试').resolve())
    SANDBOX.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0]], verbosity=2)
