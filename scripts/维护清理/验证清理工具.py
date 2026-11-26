"""独立临时沙盒中的清理保护验证，不使用项目存量。"""

import importlib.util
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
import json
import sys
from unittest.mock import patch
from contextlib import redirect_stdout

spec = importlib.util.spec_from_file_location("cleaner", Path(__file__).with_name("清理工具.py"))
cleaner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleaner)


class CleaningTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory(prefix="scavenger-clean-test-")
        self.addCleanup(self.sandbox.cleanup)
        cleaner.ROOT = Path(self.sandbox.name)
        with redirect_stdout(io.StringIO()):
            cleaner.initialize()
        self.registry = cleaner.read(cleaner.STATE + "/登记.json")

    def sample(self, age=8):
        path = cleaner.checked("tmp/测试任务/cache.bin")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-cache")
        then = time.time() - age * cleaner.DAY
        os.utime(path, (then, then))
        return path

    def enroll_closed(self):
        self.registry["tasks"] = {"测试任务": {"status": "closed", "paths": ["tmp/测试任务"]}}
        self.registry["items"] = [{"path": "tmp/测试任务", "task": "测试任务", "kind": "cache"}]
        cleaner.save(cleaner.STATE + "/登记.json", self.registry)

    def report(self):
        with redirect_stdout(io.StringIO()):
            path, report = cleaner.preview(self.registry)
        return path, report, cleaner.digest(cleaner.checked(path))

    def move(self):
        manifest, _, sha = self.report()
        with redirect_stdout(io.StringIO()):
            cleaner.quarantine(self.registry, manifest, sha)
        return next(cleaner.checked(cleaner.STATE + "/回收").glob("*/回执.json")).relative_to(cleaner.ROOT).as_posix()

    def test_default_protects_and_init_refuses_overwrite(self):
        self.sample()
        self.assertFalse(self.registry["automatic_cache"])
        self.assertEqual(cleaner.candidates(self.registry)[0], [])
        with self.assertRaises(FileExistsError):
            cleaner.initialize()

    def test_unknown_task_blocks_even_enrolled_cache(self):
        self.sample()
        self.registry["tasks"]["测试任务"] = {"status": "closed", "paths": ["tmp/测试任务"]}
        self.registry["items"] = [{"path": "tmp/测试任务", "task": "测试任务", "kind": "cache"}]
        self.assertEqual(cleaner.candidates(self.registry)[0], [])

    def test_active_task_blocks(self):
        self.sample()
        self.enroll_closed()
        self.registry["tasks"]["其他任务"] = {"status": "active", "paths": ["tmp/测试任务/cache.bin"]}
        self.assertEqual(cleaner.candidates(self.registry)[0], [])

    def test_age_and_locks(self):
        self.sample(age=1)
        self.enroll_closed()
        self.assertEqual(cleaner.candidates(self.registry)[0], [])
        self.sample(age=8)
        lock = cleaner.checked("tmp/测试任务/LOCK")
        lock.write_text("lock", encoding="utf-8")
        then = time.time() - 8 * cleaner.DAY
        os.utime(lock, (then, then))
        self.assertEqual(len(cleaner.candidates(self.registry)[0]), 0)

    def test_paths_and_whitelist(self):
        for name in ("../outside", "/tmp/a", "C:/a", "tmp/../a", "tmp/./a", "tmp\\a", ""):
            with self.assertRaises(ValueError, msg=name):
                cleaner.checked(name)
        for name in ("local-data/tools/cache", "local-data/原型/cache", "scripts/清理工具.py", "tmp"):
            with self.assertRaises(ValueError, msg=name):
                cleaner.allowed(name, "cache")
        with self.assertRaises(ValueError):
            cleaner.allowed("local-data/断箭/正式资料库/版本/v0001/x.json", "history")
        bundle = "local-data/丧尸模型/发布/ZOM-SHARE-261104-001/说明.json"
        path = cleaner.checked(bundle)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            cleaner.allowed(bundle, "history")
        cleaner.allowed(bundle, "history", release_bundle=True)

    def test_approval_hash(self):
        self.sample()
        self.enroll_closed()
        manifest, _, _ = self.report()
        with self.assertRaises(ValueError):
            cleaner.quarantine(self.registry, manifest, "wrong")
        self.assertTrue(cleaner.checked("tmp/测试任务/cache.bin").exists())

    def test_changed_file_rejected(self):
        path = self.sample()
        self.enroll_closed()
        manifest, _, sha = self.report()
        path.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            cleaner.quarantine(self.registry, manifest, sha)
        self.assertTrue(path.exists())

    def test_new_registration_invalidates_approval(self):
        self.sample()
        self.enroll_closed()
        manifest, _, sha = self.report()
        self.registry["tasks"]["新任务"] = {"status": "active", "paths": ["tmp/测试任务"]}
        cleaner.save(cleaner.STATE + "/登记.json", self.registry)
        with self.assertRaises(ValueError):
            cleaner.quarantine(self.registry, manifest, sha)

    def test_quarantine_and_restore(self):
        path = self.sample()
        self.enroll_closed()
        receipt = self.move()
        self.assertFalse(path.exists())
        cleaner.recover_or_purge(self.registry, receipt)
        self.assertEqual(path.read_bytes(), b"test-cache")

    def test_restore_never_overwrites(self):
        path = self.sample()
        self.enroll_closed()
        receipt = self.move()
        path.write_bytes(b"new-task-data")
        with self.assertRaises(ValueError):
            cleaner.recover_or_purge(self.registry, receipt)
        self.assertEqual(path.read_bytes(), b"new-task-data")

    def test_purge_age_and_new_occupation(self):
        self.sample()
        self.enroll_closed()
        receipt = self.move()
        with self.assertRaises(ValueError):
            cleaner.recover_or_purge(self.registry, receipt, purge=True)
        data = cleaner.read(receipt)
        data["files"][0]["moved_at"] = time.time() - 8 * cleaner.DAY
        cleaner.save(receipt, data)
        self.registry["tasks"]["新任务"] = {"status": "active", "paths": ["tmp/测试任务"]}
        cleaner.save(cleaner.STATE + "/登记.json", self.registry)
        with self.assertRaises(ValueError):
            cleaner.recover_or_purge(self.registry, receipt, purge=True)

    def test_expired_purge(self):
        self.sample()
        self.enroll_closed()
        receipt = self.move()
        data = cleaner.read(receipt)
        data["files"][0]["moved_at"] = time.time() - 8 * cleaner.DAY
        cleaner.save(receipt, data)
        cleaner.recover_or_purge(self.registry, receipt, purge=True)
        self.assertFalse(cleaner.checked(data["files"][0]["stored"]).exists())
        self.assertEqual(cleaner.read(receipt)["status"], "已清除")

    def test_scheduled_preview_deduplicates(self):
        self.sample()
        self.enroll_closed()
        with redirect_stdout(io.StringIO()):
            name, _ = cleaner.preview(self.registry, scheduled=True)
            first = cleaner.checked(name).read_bytes()
            cleaner.preview(self.registry, scheduled=True)
        self.assertEqual(cleaner.checked(name).read_bytes(), first)

    def test_exclusive_lock(self):
        with cleaner.lock():
            with self.assertRaises(FileExistsError):
                with cleaner.lock():
                    self.fail("不应取得第二个锁")

    def test_symlink_rejected(self):
        target = cleaner.checked("tmp/target")
        target.mkdir(parents=True)
        link = cleaner.ROOT / "tmp" / "link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("当前系统未允许创建符号链接")
        with self.assertRaises(ValueError):
            cleaner.checked("tmp/link/file")

    def test_history_requires_preserved_proof(self):
        item = {"kind": "history", "path": "local-data/断箭/批次/旧文件", "proof": {}}
        with self.assertRaises(ValueError):
            cleaner.proof_check(item)

    def test_reparse_attribute_rejected(self):
        self.sample()
        original = Path.lstat

        def fake_lstat(path):
            result = original(path)
            if path.name == "测试任务":
                class ReparseInfo:
                    st_file_attributes = 0x400
                    st_mode = result.st_mode
                return ReparseInfo()
            return result

        with patch.object(Path, "lstat", fake_lstat):
            with self.assertRaises(ValueError):
                cleaner.checked("tmp/测试任务/cache.bin")

    def test_single_generation(self):
        self.sample()
        self.enroll_closed()
        self.move()
        self.sample()
        self.assertEqual(cleaner.candidates(self.registry)[0], [])

    def test_tick_defaults_to_preview(self):
        path = self.sample()
        self.enroll_closed()
        with patch("sys.argv", ["清理工具.py", "tick"]), redirect_stdout(io.StringIO()):
            cleaner.main()
        self.assertTrue(path.exists())
        self.assertFalse(cleaner.checked(cleaner.STATE + "/回收").exists())

    def test_tick_enabled_quarantines_and_purges_after_seven_days(self):
        path = self.sample()
        self.enroll_closed()
        self.registry.update(automatic_cache=True, first_review="approved")
        cleaner.save(cleaner.STATE + "/登记.json", self.registry)
        with patch("sys.argv", ["清理工具.py", "tick"]), redirect_stdout(io.StringIO()):
            cleaner.main()
        self.assertFalse(path.exists())
        receipt = next(cleaner.checked(cleaner.STATE + "/回收").glob("*/回执.json")).relative_to(cleaner.ROOT).as_posix()
        data = cleaner.read(receipt)
        data["files"][0]["moved_at"] = time.time() - 8 * cleaner.DAY
        cleaner.save(receipt, data)
        with patch("sys.argv", ["清理工具.py", "tick"]), redirect_stdout(io.StringIO()):
            cleaner.main()
        self.assertEqual(cleaner.read(receipt)["status"], "已清除")


class AutomaticTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory(prefix='scavenger-auto-test-')
        self.addCleanup(self.sandbox.cleanup)
        cleaner.ROOT = Path(self.sandbox.name)
        self.cli('init')
        self.stock = self.file('tmp/存量/old.bin', b'untouched-stock')
        sha = cleaner.digest(cleaner.checked(cleaner.STATE+'/登记.json'))
        self.cli('enable-auto', '--approved-sha', sha, '--note', '工程师确认启用未来自动维护，存量不扩大')
        self.now = time.time()+1
        self.clock = patch('time.time', side_effect=lambda:self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def cli(self, *args):
        output = io.StringIO()
        with patch('sys.argv', ['清理工具.py', *args]), redirect_stdout(output):
            cleaner.main()
        return output.getvalue()

    def file(self, name, data=b'media'):
        path = cleaner.checked(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def registry(self):
        return cleaner.read(cleaner.STATE+'/登记.json')

    def task(self, name='后续制作', scope='local-data/制作'):
        self.cli('protect', '--task', name, '--path', scope)

    def close(self, name='后续制作'):
        self.cli('close', '--task', name, '--note', '明确结束且不再读写')

    def stage(self, old='local-data/制作/测试/旧版.glb', new='local-data/制作/测试/新版.glb', evidence='local-data/制作/测试/通过记录.md', version='v2', release_bundle=False):
        extra = ['--release-bundle'] if release_bundle else []
        text = self.cli('stage','--task','后续制作','--asset','model:test','--version',version,
                        '--path',old,'--replacement',new,'--evidence',evidence,
                        '--note','已检查引用迁移，仅所属制作任务尚待结束',*extra)
        return json.loads(text)

    def accept(self, plan):
        self.cli('accept','--manifest',plan['manifest'],'--approved-sha',plan['sha256'],'--note','工程师明确确认本清单具体新版通过')

    def prepared(self):
        self.task()
        old = self.file('local-data/制作/测试/旧版.glb', b'old')
        self.file('local-data/制作/测试/新版.glb', b'new')
        self.file('local-data/制作/测试/通过记录.md', b'approval-evidence')
        return old, self.stage()

    def receipts(self):
        return [p.relative_to(cleaner.ROOT).as_posix() for p in cleaner.checked(cleaner.STATE+'/回收').glob('*/回执.json')]

    def tick(self):
        return self.cli('tick')

    def test_enabled_does_not_approve_stock_or_legacy_items(self):
        self.assertEqual(self.registry()['first_review'], 'pending')
        self.task(scope='tmp')
        with self.assertRaises(ValueError):
            self.cli('enroll','--task','后续制作','--path','tmp/存量','--note','可重新生成')
        r = self.registry()
        r['tasks']['旧登记任务'] = {'status':'closed','paths':['tmp/存量']}
        r['items'].append({'path':'tmp/存量','task':'旧登记任务','kind':'cache'})
        cleaner.save(cleaner.STATE+'/登记.json', r)
        self.tick()
        self.assertEqual(self.stock.read_bytes(), b'untouched-stock')
        self.assertFalse(self.receipts())

    def test_waiting_acceptance_and_active_task_remain_protected(self):
        old, plan = self.prepared()
        self.close()
        self.tick()
        self.assertTrue(old.exists())
        with self.assertRaises(ValueError):
            self.cli('accept','--manifest',plan['manifest'],'--approved-sha','wrong','--note','工程师确认')
        self.accept(plan)
        self.cli('protect','--task','后续制作','--path','local-data/制作')
        self.tick()
        self.assertTrue(old.exists())

    def test_accepted_history_quarantines_then_purges_at_seven_days(self):
        old, plan = self.prepared()
        self.accept(plan)
        self.close()
        self.tick()
        self.assertFalse(old.exists())
        receipt = self.receipts()[0]
        self.now += 7*cleaner.DAY-1
        self.tick()
        self.assertEqual(cleaner.read(receipt)['status'], '完成')
        self.now += 2
        self.tick()
        self.assertEqual(cleaner.read(receipt)['status'], '已清除')
        self.assertTrue(cleaner.checked('local-data/制作/测试/新版.glb').exists())
        self.assertTrue(cleaner.checked('local-data/制作/测试/通过记录.md').exists())

    def test_cache_waits_after_close_and_does_not_include_new_files(self):
        self.task(scope='tmp/后续')
        first = self.file('tmp/后续/one.bin')
        self.cli('enroll','--task','后续制作','--path','tmp/后续','--note','可重新运行测试生成')
        extra = self.file('tmp/后续/unregistered.bin', b'never-authorized')
        self.close()
        self.tick()
        self.assertTrue(first.exists())
        self.now += 7*cleaner.DAY+1
        self.tick()
        self.assertFalse(first.exists())
        self.assertTrue(extra.exists())
        self.assertTrue(self.stock.exists())

    def test_manual_quarantine_keeps_current_policy_and_seven_day_purge(self):
        old, plan = self.prepared()
        self.accept(plan)
        self.close()
        preview = json.loads(self.cli('preview'))
        receipt_path = self.cli('quarantine', '--manifest', preview['report'], '--approved-sha', preview['sha256']).strip()
        receipt = cleaner.read(receipt_path)
        self.assertEqual(receipt['automatic_policy'], self.registry()['automation']['id'])
        self.assertFalse(old.exists())
        self.now += 7*cleaner.DAY-1
        self.tick()
        self.assertEqual(cleaner.read(receipt_path)['status'], '完成')
        self.now += 2
        self.tick()
        self.assertEqual(cleaner.read(receipt_path)['status'], '已清除')

    def test_explicit_receipt_policy_repair_keeps_original_deadline(self):
        _, plan = self.prepared()
        self.accept(plan)
        self.close()
        self.tick()
        path = self.receipts()[0]
        receipt = cleaner.read(path)
        original_files = receipt['files']
        original_created = receipt['created']
        receipt.pop('automatic_policy')
        cleaner.save(path, receipt)
        with self.assertRaises(ValueError):
            self.cli('repair-receipt', '--manifest', path, '--approved-sha', 'wrong', '--note', '当前已批准清单漏标修复')
        self.now += 6*cleaner.DAY
        sha = cleaner.digest(cleaner.checked(path))
        self.cli('repair-receipt', '--manifest', path, '--approved-sha', sha, '--note', '当前已批准清单漏标修复')
        repaired = cleaner.read(path)
        self.assertEqual(repaired['files'], original_files)
        self.assertEqual(repaired['created'], original_created)
        self.tick()
        self.assertEqual(cleaner.read(path)['status'], '完成')
        self.now += cleaner.DAY+1
        self.tick()
        self.assertEqual(cleaner.read(path)['status'], '已清除')

    def test_receipt_repair_rejects_legacy_or_tampered_rows(self):
        _, plan = self.prepared()
        self.accept(plan)
        self.close()
        self.tick()
        path = self.receipts()[0]
        receipt = cleaner.read(path)
        receipt.pop('automatic_policy')
        for mutation in ['legacy', 'tampered']:
            with self.subTest(mutation=mutation):
                data = json.loads(json.dumps(receipt))
                if mutation == 'legacy':
                    data['files'][0]['item'].pop('automatic_policy')
                else:
                    data['files'][0]['sha256'] = '0'*64
                cleaner.save(path, data)
                sha = cleaner.digest(cleaner.checked(path))
                with self.assertRaises(ValueError):
                    self.cli('repair-receipt', '--manifest', path, '--approved-sha', sha, '--note', '不得扩大范围')
                self.assertNotIn('automatic_policy', cleaner.read(path))

    def test_changed_frozen_cache_is_not_reinterpreted(self):
        self.task(scope='tmp/后续')
        path = self.file('tmp/后续/cache.bin')
        self.cli('enroll','--task','后续制作','--path','tmp/后续','--note','可重新生成')
        self.close()
        path.write_bytes(b'new-unique-work')
        self.now += 8*cleaner.DAY
        self.tick()
        self.assertTrue(path.exists())
        self.assertFalse(self.receipts())

    def test_many_files_do_not_duplicate_full_inventory_in_each_row(self):
        self.task(scope='tmp/批量缓存')
        for index in range(80):
            self.file(f'tmp/批量缓存/{index:03}.bin')
        self.cli('enroll','--task','后续制作','--path','tmp/批量缓存','--note','可重新批量生成')
        self.close()
        self.now += 8*cleaner.DAY
        self.tick()
        receipt = self.receipts()[0]
        self.assertEqual(len(cleaner.read(receipt)['files']), 80)
        self.assertLess(cleaner.checked(receipt).stat().st_size, 150000)

    def test_existing_old_task_cannot_be_adopted(self):
        r = self.registry()
        r['tasks']['旧任务'] = {'status':'closed','paths':['tmp/新文件'],'created_at':0}
        cleaner.save(cleaner.STATE+'/登记.json',r)
        self.file('tmp/新文件/a.bin')
        with self.assertRaises(ValueError):
            self.cli('enroll','--task','旧任务','--path','tmp/新文件','--note','可重新生成')

    def test_old_task_reactivation_cannot_become_a_future_task(self):
        self.cli('protect','--task','既有临时任务待核对','--path','tmp/新增文件')
        r = self.registry()
        r['tasks']['既有临时任务待核对']['created_at'] = self.now
        cleaner.save(cleaner.STATE+'/登记.json', r)
        self.file('tmp/新增文件/a.bin')
        with self.assertRaises(ValueError):
            self.cli('enroll','--task','既有临时任务待核对','--path','tmp/新增文件','--note','可重新生成')

    def test_unknown_scope_refinement_does_not_bypass_new_protection(self):
        old, plan = self.prepared()
        self.accept(plan)
        self.close()
        self.cli('protect','--task','其他读取任务','--path','local-data/制作/测试/旧版.glb')
        self.tick()
        self.assertTrue(old.exists())

    def test_release_bundle_retires_metadata_inside_explicit_candidate(self):
        self.task(scope='local-data/丧尸模型/发布')
        old = self.file(
            'local-data/丧尸模型/发布/ZOM-SHARE-261104-001/说明.json',
            b'old-history',
        )
        self.file('local-data/丧尸模型/发布/正式版/说明.md', b'formal')
        self.file('local-data/丧尸模型/发布/正式版/核验.json', b'evidence')
        plan = self.stage(
            old=old.relative_to(cleaner.ROOT).as_posix(),
            new='local-data/丧尸模型/发布/正式版/说明.md',
            evidence='local-data/丧尸模型/发布/正式版/核验.json',
            release_bundle=True,
        )
        self.accept(plan)
        self.close()
        self.tick()
        self.assertFalse(old.exists())

    def test_occupancy_blocks_without_killing_process(self):
        old, plan = self.prepared()
        self.accept(plan)
        self.close()
        with patch('占用检查.require_unoccupied', side_effect=OSError('正在被进程读取')):
            with self.assertRaises(OSError):
                self.tick()
        self.assertTrue(old.exists())

    def test_recycle_task_and_recreated_source_block_purge(self):
        old, plan = self.prepared()
        self.accept(plan)
        self.close()
        self.tick()
        receipt = self.receipts()[0]
        data = cleaner.read(receipt)
        stored = cleaner.checked(data['files'][0]['stored'])
        self.now += 8*cleaner.DAY
        self.cli('protect','--task','回收读取任务','--path',data['files'][0]['stored'])
        self.tick()
        self.assertTrue(stored.exists())
        self.cli('close','--task','回收读取任务','--note','已停止读取')
        self.file('local-data/制作/测试/旧版.glb', b'recreated')
        self.tick()
        self.assertTrue(stored.exists())
        self.assertEqual(old.read_bytes(), b'recreated')

    def test_changed_replacement_or_evidence_blocks_purge(self):
        old, plan = self.prepared()
        self.accept(plan)
        self.close()
        self.tick()
        receipt = self.receipts()[0]
        self.file('local-data/制作/测试/新版.glb', b'changed-after-acceptance')
        self.now += 8*cleaner.DAY
        self.tick()
        self.assertEqual(cleaner.read(receipt)['status'], '完成')

    def test_restore_suspends_automatic_recycling(self):
        old, plan = self.prepared()
        self.accept(plan)
        self.close()
        self.tick()
        receipt = self.receipts()[0]
        self.cli('restore','--manifest',receipt)
        self.tick()
        self.assertTrue(old.exists())
        self.assertTrue(self.registry()['items'][0]['suspended'])

    def test_originals_and_evidence_are_not_history_payloads(self):
        for path in ('local-data/制作/原件/a.glb', 'local-data/制作/验收证据/a.png', 'local-data/资源库/原始归档/a.7z'):
            with self.assertRaises(ValueError):
                cleaner.allowed(path,'history')
        with self.assertRaises(ValueError):
            cleaner.allowed('tmp/后续/original.glb','cache')

    def test_unenrolled_legacy_receipt_is_not_auto_purged(self):
        path = cleaner.STATE+'/回收/legacy/回执.json'
        cleaner.save(path, {'status':'完成','files':[{'state':'stored','bytes':3,'moved_at':0}]})
        self.tick()
        self.assertEqual(cleaner.read(path)['status'], '完成')

    def test_multiple_acceptances_use_current_successor_and_keep_evidence(self):
        old, plan = self.prepared()
        self.accept(plan)
        self.close()
        self.tick()
        self.task()
        self.file('local-data/制作/测试/第三版.glb', b'version3')
        self.file('local-data/制作/测试/第三版通过.md', b'evidence3')
        next_plan = self.stage(old='local-data/制作/测试/新版.glb', new='local-data/制作/测试/第三版.glb',
                               evidence='local-data/制作/测试/第三版通过.md', version='v3')
        self.accept(next_plan)
        self.close()
        self.tick()
        self.assertEqual(len(self.receipts()), 2)
        self.now += 8*cleaner.DAY
        self.tick()
        self.assertTrue(all(cleaner.read(p)['status']=='已清除' for p in self.receipts()))
        self.assertTrue(cleaner.checked('local-data/制作/测试/第三版.glb').exists())
        self.assertTrue(cleaner.checked('local-data/制作/测试/通过记录.md').exists())
        self.assertTrue(cleaner.checked('local-data/制作/测试/第三版通过.md').exists())

    def test_tampered_receipt_cannot_expand_authorization(self):
        old, plan = self.prepared()
        self.accept(plan)
        self.close()
        self.tick()
        receipt_path = self.receipts()[0]
        receipt = cleaner.read(receipt_path)
        receipt['files'][0]['sha256'] = '0'*64
        cleaner.save(receipt_path, receipt)
        self.now += 8*cleaner.DAY
        self.tick()
        self.assertTrue(cleaner.checked(receipt['files'][0]['stored']).exists())

    def test_accepted_plan_mutation_blocks_retirement(self):
        old, plan = self.prepared()
        self.accept(plan)
        self.close()
        data = cleaner.read(plan['manifest'])
        data['version'] = 'silently-changed'
        cleaner.save(plan['manifest'], data)
        self.tick()
        self.assertTrue(old.exists())

    def test_open_recycle_payload_blocks_automatic_purge(self):
        self.task(scope='tmp/后续')
        self.file('tmp/后续/cache.bin')
        self.cli('enroll','--task','后续制作','--path','tmp/后续','--note','可重新生成')
        self.close()
        self.now += 8*cleaner.DAY
        self.tick()
        receipt = self.receipts()[0]
        self.now += 8*cleaner.DAY
        with patch('占用检查.require_unoccupied', side_effect=OSError('回收仍占用')):
            self.tick()
        self.assertEqual(cleaner.read(receipt)['status'], '完成')

    def test_partial_purge_stops_and_never_automatically_retries(self):
        old, _ = self.prepared()
        self.file('local-data/制作/测试/旧版二.glb', b'old-two')
        plan = json.loads(self.cli('stage','--task','后续制作','--asset','model:test','--version','v2',
            '--path','local-data/制作/测试/旧版.glb','--path','local-data/制作/测试/旧版二.glb',
            '--replacement','local-data/制作/测试/新版.glb','--evidence','local-data/制作/测试/通过记录.md','--note','依赖已核查'))
        self.accept(plan)
        self.close()
        self.tick()
        receipt = self.receipts()[0]
        rows = cleaner.read(receipt)['files']
        first, second = [cleaner.checked(r['stored']) for r in rows]
        original = cleaner.same
        def fail_after_first(row, relative=None):
            if relative == rows[1]['stored'] and not first.exists():
                raise OSError('模拟第一份已删除后的IO异常')
            return original(row, relative)
        self.now += 8*cleaner.DAY
        with patch.object(cleaner, 'same', side_effect=fail_after_first):
            with self.assertRaises(ValueError):
                self.tick()
        self.assertEqual(cleaner.read(receipt)['status'], '中断待核对')
        self.tick()
        self.assertTrue(second.exists())

    def test_audit_detects_buildup_without_modifying_files(self):
        stale_file = self.file('local-data/制作/旧任务/data.bin', b'old-data')
        old_ts = self.now - 10 * cleaner.DAY
        os.utime(stale_file, (old_ts, old_ts))
        self.task(name='过期任务', scope='local-data/制作/旧任务')

        orphan_file = self.file('tmp/未登记大目录/big.bin', b'x' * (2 * 1024 * 1024))
        big_json = self.file('local-data/测试/trace.json', b'{}' + b' ' * (2 * 1024 * 1024))
        for rev in ('r01', 'r02', 'r03'):
            self.file(f'local-data/测试多代/{rev}/chunk.bin', b'y' * (450 * 1024))

        reg_before = cleaner.digest(cleaner.checked(cleaner.STATE + '/登记.json'))
        out = self.cli(
            'audit', '--stale-days', '7', '--orphan-mb', '1', '--big-json-mb', '1', '--multi-rev-mb', '1'
        )
        data = json.loads(out)

        self.assertEqual(reg_before, cleaner.digest(cleaner.checked(cleaner.STATE + '/登记.json')))
        self.assertTrue(all(p.exists() for p in (stale_file, orphan_file, big_json)))
        self.assertEqual([x['task'] for x in data['stale_active_tasks']], ['过期任务'])
        self.assertEqual([x['path'] for x in data['unregistered_tmp_dirs']], ['tmp/未登记大目录'])
        self.assertEqual([x['path'] for x in data['large_json_files']], ['local-data/测试/trace.json'])
        self.assertEqual([x['parent'] for x in data['multi_revision_dirs']], ['local-data/测试多代'])

        report_file = cleaner.checked(cleaner.STATE + '/自动维护/最近体检结果.json')
        self.assertTrue(report_file.exists())
        mtime_before = report_file.stat().st_mtime_ns
        self.now += 60
        self.cli('audit', '--stale-days', '7', '--orphan-mb', '1', '--big-json-mb', '1', '--multi-rev-mb', '1')
        self.assertEqual(mtime_before, report_file.stat().st_mtime_ns)


if __name__ == "__main__":
    unittest.main(verbosity=2)
