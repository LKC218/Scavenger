"""离线核对日常入口的目录、失败诊断、8784只读检查和本轮进程收尾。"""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import 运行日常测试 as entry
from 日常执行 import ExecutionStopped, process_library, run_child
from 日常留存 import compact, record_failure

sys.path.insert(0, str(entry.ROOT / 'scripts'))
import 启动战术演训原型 as launcher
from 只读核对演训入口 import check_only


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='边界-', dir=SANDBOX)
        self.home = Path(self.tmp.name)
        self.registry = self.home / '登记.json'
        cases = self.home / '运行'
        self.registry.write_text(json.dumps({'tasks': {'隔离任务': {'status': 'active', 'paths': [
            (cases / case).relative_to(entry.ROOT).as_posix() for case in entry.CASES
        ]}}}), encoding='utf-8')
        self.patches = contextlib.ExitStack()
        self.patches.enter_context(patch.object(entry, 'REGISTRY', self.registry))
        self.patches.enter_context(patch.object(entry, 'ROUTINE_ROOT', cases))

    def tearDown(self):
        self.patches.close()
        self.tmp.cleanup()

    def test_output_bounds_and_duplicate(self):
        for value in ('../other', 'a/b', 'NUL', '', 'a.b'):
            with self.assertRaises(ValueError):
                entry.reserve('move-v3', value)
        first = entry.reserve('move-v3', 'same')
        (first / '原文件.txt').write_text('保留', encoding='utf8')
        with self.assertRaises(FileExistsError):
            entry.reserve('move-v3', 'same')
        self.assertEqual((first / '原文件.txt').read_text('utf8'), '保留')

    def test_parallel_collision_uses_exclusive_directory(self):
        code = (
            'import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
            'import 运行日常测试 as e;e.ROUTINE_ROOT=Path(sys.argv[2]);e.REGISTRY=Path(sys.argv[3]);'
            'e.reserve("move-v3","concurrent")'
        )
        args = [sys.executable, '-X', 'utf8', '-B', '-c', code, str(entry.TOOLS), str(entry.ROUTINE_ROOT), str(self.registry)]
        procs = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        for child in procs:
            child.communicate(timeout=20)
        self.assertEqual(sorted(p.returncode for p in procs), [0, 1])
        self.assertEqual(len(list((entry.ROUTINE_ROOT / 'move-v3').iterdir())), 1)

    def test_parallel_unique_directories(self):
        a = entry.reserve('move-v3', 'run-a')
        b = entry.reserve('move-v3', 'run-b')
        self.assertNotEqual(a, b)

    def test_capture_failure_keeps_original_and_limits_log(self):
        class Page:
            def is_closed(self): return False
            def screenshot(self, **kwargs): raise RuntimeError('截图不可用')
        result = {'errors': ['长日志' * 10000], 'checks': [{'name': '原检查', 'passed': False, 'evidence': ['a'] * 10000}]}
        error = AssertionError('原来的失败')
        record_failure(self.home, result, error=error, page=Page())
        log = self.home / '失败日志.txt'
        text = log.read_text('utf8')
        self.assertIn('原来的失败', text)
        self.assertIn('截图不可用', text)
        self.assertLessEqual(log.stat().st_size, 32768)
        record_failure(self.home, {}, error=RuntimeError('不能覆盖'))
        self.assertEqual(log.read_text('utf8'), text)

    def test_summary_does_not_copy_large_failure_evidence(self):
        result = {'passed': False, 'failure': '大异常' * 10000,
                  'checks': [{'name': '诊断检查', 'passed': False, 'evidence': list(range(10000))}]}
        small = compact(result)
        self.assertNotIn('evidence', small['checks'][0])
        self.assertLess(len(small['failure']), 4100)

    def test_8784_missing_never_starts(self):
        with patch.object(launcher, 'configuration', return_value=({}, None, None)), patch.object(launcher, 'status', return_value=None), patch.object(launcher, 'start', side_effect=AssertionError('不应启动')), patch.object(launcher.subprocess, 'Popen', side_effect=AssertionError('不应派生进程')):
            with self.assertRaisesRegex(RuntimeError, '不启动服务'):
                check_only()

    def test_8784_changed_entry_is_rejected(self):
        with patch.object(launcher, 'configuration', return_value=({}, None, None)), patch.object(launcher, 'digest', return_value='已改变'), patch.object(launcher, 'status', side_effect=AssertionError('不应继续检查')):
            with self.assertRaisesRegex(RuntimeError, '快捷入口内容变化'):
                check_only('打开战术演训原型.cmd')

    def test_child_success(self):
        result = run_child([sys.executable, '-X', 'utf8', '-B', '-c', 'print("隔离进程通过")'], entry.ROOT)
        self.assertEqual(result.returncode, 0)
        self.assertIn('隔离进程通过', result.stdout)

    def test_interrupt_stops_owned_tree_keeps_external_process(self):
        lib = process_library()
        marker = self.home / 'child-pids.json'
        script = (
            'import json,os,subprocess,sys,time;'
            'from pathlib import Path;'
            'p=subprocess.Popen([sys.executable,"-B","-c","import time;time.sleep(60)"]);'
            f'Path({str(marker)!r}).write_text(json.dumps([os.getpid(),p.pid]));time.sleep(60)'
        )
        external = subprocess.Popen([sys.executable, '-B', '-c', 'import time;time.sleep(60)'])
        original = subprocess.Popen.communicate
        fired = False
        def communicate(process, *args, **kwargs):
            nonlocal fired
            if marker.exists() and not fired:
                fired = True
                raise KeyboardInterrupt()
            return original(process, *args, **kwargs)
        try:
            with patch.object(subprocess.Popen, 'communicate', communicate):
                with self.assertRaises(ExecutionStopped) as caught:
                    run_child([sys.executable, '-B', '-c', script], entry.ROOT, timeout=15)
            self.assertEqual(caught.exception.reason, '执行中断')
            self.assertTrue(caught.exception.cleanup_confirmed)
            for pid in json.loads(marker.read_text()):
                self.assertFalse(lib.pid_exists(pid), pid)
            self.assertIsNone(external.poll())
        finally:
            external.terminate()
            external.wait(timeout=10)

    def test_timeout_stops_own_process(self):
        with self.assertRaises(ExecutionStopped) as caught:
            run_child([sys.executable, '-B', '-c', 'import time;time.sleep(60)'], entry.ROOT, timeout=.2)
        self.assertEqual(caught.exception.reason, '执行超时')
        self.assertTrue(caught.exception.cleanup_confirmed)

    def test_uncertain_cleanup_is_not_success(self):
        identity = {'app': '隔离身份', 'port': 8813, 'pid': 99, 'started_at': 10}
        with patch.object(entry, 'pinned_candidate', return_value=identity), patch.object(entry, 'live_service', return_value=identity), patch.object(entry, 'run_child', side_effect=ExecutionStopped('执行中断', False)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.run_case('move-v3', 'msedge', 'interrupted', automatic=False), 1)
        result = json.loads((entry.ROUTINE_ROOT / 'move-v3/interrupted/结果摘要.json').read_text('utf8'))
        self.assertFalse(result['passed'])
        self.assertFalse(result['cleanup_confirmed'])
        self.assertEqual(result['status'], '执行中断')

    def test_missing_dependency_still_leaves_diagnostics(self):
        identity = {'app': '隔离身份', 'port': 8813, 'pid': 99, 'started_at': 10}
        with patch.object(entry, 'pinned_candidate', return_value=identity), patch.object(entry, 'live_service', return_value=identity), patch.object(entry, 'run_child', side_effect=ImportError('进程库不可用')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(entry.run_case('move-v3', 'msedge', 'dependency', automatic=False), 1)
        output = entry.ROUTINE_ROOT / 'move-v3/dependency'
        self.assertEqual({p.name for p in output.iterdir()}, {'结果摘要.json', '失败日志.txt'})


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sandbox', type=Path, required=True)
    args = parser.parse_args()
    SANDBOX = args.sandbox.resolve()
    SANDBOX.relative_to((entry.ROOT / 'tmp/运行/战术演训日常测试').resolve())
    SANDBOX.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[sys.argv[0]], verbosity=2)
