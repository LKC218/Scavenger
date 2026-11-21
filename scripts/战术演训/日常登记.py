"""日常测试复用现有清理CLI，仅管理自己新建的任务，不执行回收或删除。"""
import hashlib
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from zoneinfo import ZoneInfo
import time

PREFIX = '战术演训日常V001-'
NOTE = '日常留存V001：'


def read_registry(root):
    return json.loads((root / 'local-data/维护清理/登记.json').read_text('utf-8-sig'))


def members(output):
    rows = []
    for path in (output, *output.parents):
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('日常产物路径经过链接或目录联接，保持保护')

    def visit(directory):
        for path in sorted(directory.iterdir()):
            if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
                raise ValueError('日常产物中出现链接或目录联接，保持保护')
            if path.is_dir():
                yield from visit(path)
            else:
                yield path

    for path in visit(output):
        if path.is_file():
            before = path.stat()
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('日常产物仍在写入，保持保护')
            rows.append({'path': path.relative_to(output).as_posix(), 'bytes': after.st_size,
                         'mtime_ns': after.st_mtime_ns, 'sha256': digest})
    return rows


class Registration:
    def __init__(self, root, case, channel, run_id, output, *, retained=False):
        self.root, self.case, self.channel = root, case, channel
        self.run_id, self.output = run_id, output
        self.task = PREFIX + ('保留-' if retained else '') + case + '-' + run_id + '-' + uuid4().hex[:12]
        self.started = False

    def cli(self, *args):
        # 短命清理CLI只登记元数据；它不启动其他进程，不使用测试子进程执行器。
        completed = subprocess.run([sys.executable, '-X', 'utf8', '-B',
                                    str(self.root / 'scripts/维护清理/清理工具.py'), *args],
                                   cwd=self.root, capture_output=True, text=True, encoding='utf8',
                                   timeout=30,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if completed.returncode:
            raise RuntimeError(f'清理登记失败，保留任务 {self.task}：' + (completed.stderr or completed.stdout)[-3000:])

    def own(self):
        registry = read_registry(self.root)
        task = registry['tasks'].get(self.task)
        relative = self.output.relative_to(self.root).as_posix()
        if not self.started or not task or task['status'] != 'active' or relative not in task['paths']:
            raise RuntimeError('本轮任务身份或保护变化，停止交接')
        return registry

    def start(self, dependencies):
        if self.output.exists():
            raise FileExistsError(f'输出目录已存在，未接管：{self.output}')
        registry = read_registry(self.root)
        if not registry.get('automation', {}).get('enabled'):
            raise RuntimeError('本机自动维护未启用；不复制旧策略或自动开启')
        if self.task in registry['tasks']:
            raise RuntimeError('任务名已存在，拒绝复用')
        args = ['protect', '--task', self.task]
        for path in [self.output, *dependencies]:
            args.extend(['--path', path.relative_to(self.root).as_posix()])
        self.cli(*args)
        self.started = True
        registry = self.own()
        task = registry['tasks'][self.task]
        policy = registry['automation']
        if self.task in policy['preexisting_tasks'] or task['created_at'] < policy['enabled_at']:
            raise RuntimeError('本轮不具备后续缓存登记资格，保持保护')

    def cancel_empty(self):
        # 并发同ID可能在protect以后才发现目录已被另一轮占用；只关闭自己的空任务。
        self.own()
        self.cli('close', '--task', self.task, '--note', '本轮未取得独占输出目录，未执行测试或登记缓存；不修改占用该目录的其他任务和文件')

    def extend(self, paths):
        self.own()
        args = ['protect', '--task', self.task]
        for path in paths:
            args.extend(['--path', path.relative_to(self.root).as_posix()])
        self.cli(*args)
        self.own()

    def descriptor(self, retained):
        return {'task': self.task, 'mode': '人工保留保护' if retained else '本轮自动缓存',
                'authority': 'local-data/维护清理/登记.json',
                'note': '此摘要写完后冻结；登记和关闭的实际结果以任务记录及终端输出为准'}

    def finish(self, result, *, retained=False):
        self.own()
        if retained or not result.get('cleanup_confirmed', False):
            return {'task': self.task, 'state': '保留保护' if retained else '退出状态未确认，保留保护',
                    'ok': retained and result.get('cleanup_confirmed', False)}
        before = members(self.output)
        expected = {'结果摘要.json', *(row['path'] for row in result.get('artifacts', []))}
        allowed = {'结果摘要.json', '失败日志.txt'}
        channels = ('chrome', 'msedge') if self.channel == 'both' else (self.channel,)
        folders = [f'检查/{channel}' for channel in channels] if self.case in ('altitude-v2', 'labels-v3', 'name-8784') else ['检查']
        allowed.update(f'{folder}/{name}' for folder in folders for name in ('失败.png', '失败日志.txt'))
        actual = {row['path'] for row in before}
        if actual != expected or not actual <= allowed or (result['passed'] and len(before) != 1):
            raise RuntimeError('产物与摘要不一致或存在未归类文件，保持保护')
        note = (f'重跑日常入口 --case {self.case} --channel {self.channel}，换新的运行ID；'
                '以摘要内冻结候选和测试源码SHA定位同源输入。仅此目录为可再生成日常输出，'
                '不作为正式验收凭证；原件和正式证据不登记。')
        self.cli('enroll', '--task', self.task, '--kind', 'cache',
                 '--path', self.output.relative_to(self.root).as_posix(), '--note', note)
        registry = self.own()
        items = [item for item in registry['items'] if item['task'] == self.task and item['kind'] == 'cache']
        if len(items) != 1 or members(self.output) != before:
            raise RuntimeError('登记后产物集合或指纹变化，保持保护')
        item = items[0]
        frozen = [{**row, 'path': (self.root / row['path']).relative_to(self.output).as_posix()} for row in item['files']]
        if frozen != before or item.get('automatic_policy') != registry['automation']['id']:
            raise RuntimeError('清理工具冻结范围或策略不符，保持保护')
        observation = {'schema': 1, 'case': self.case, 'channel': self.channel, 'run_id': self.run_id,
                       'passed': result['passed'], 'status': result['status'], 'cleanup_confirmed': True,
                       'started_epoch': result['started_epoch'], 'finished_epoch': result['finished_epoch'],
                       'files': len(before), 'bytes': sum(row['bytes'] for row in before),
                       'cache_id': item['id'], 'output': self.output.relative_to(self.root).as_posix(),
                       'test_sha256': result.get('test_sha256'),
                       'note': '测试及浏览器已退出，产物已冻结；无人工复核或持续消费者，其他任务保护不变'}
        self.cli('close', '--task', self.task, '--note', NOTE + json.dumps(observation, ensure_ascii=False))
        task = read_registry(self.root)['tasks'][self.task]
        if task['status'] != 'closed':
            raise RuntimeError('关闭任务后回读不符，人工核对保护')
        return {'task': self.task, 'state': '已冻结登记并关闭', 'ok': True, 'cache_id': item['id']}


def observation_window(rows, now):
    """按项目时区的周一至周五计算；生产入口只传入真实系统时间。"""
    zone = ZoneInfo('America/Los_Angeles')
    end = datetime.fromtimestamp(now, zone).date()
    days = []
    while len(days) < 3:
        if end.weekday() < 5:
            days.append(end.isoformat())
        end -= timedelta(days=1)
    days.reverse()
    selected = [row for row in rows if datetime.fromtimestamp(row['finished_epoch'], zone).date().isoformat() in days]
    observed = sorted({datetime.fromtimestamp(row['finished_epoch'], zone).date().isoformat() for row in selected})
    return {'required_workdays': days, 'observed_workdays': observed, 'runs': len(selected),
            'enough_time_and_runs': observed == days and len(selected) >= 5,
            'calendar': 'America/Los_Angeles，周一至周五；不推断地区法定节假日'}


def observe(root):
    """只审计此入口新登记的任务；先登记短期读取保护，不扫描旧存量。"""
    snapshot = read_registry(root)
    names = [name for name in snapshot['tasks'] if name.startswith(PREFIX)]
    audit = Registration(root, '观察', '', '', root / 'tmp/未使用的观察占位')
    audit.task = '战术演训日常观察V001-' + uuid4().hex
    scopes = {'local-data/维护清理/登记.json', 'scripts/战术演训', 'scripts/维护清理'}
    for name in names:
        scopes.update(path for path in snapshot['tasks'][name]['paths']
                      if path.startswith('tmp/运行/战术演训日常测试/'))
    args = ['protect', '--task', audit.task]
    for scope in sorted(scopes):
        args.extend(['--path', scope])
    audit.cli(*args)
    warnings, rows, held, unavailable = [], [], [], []
    try:
        registry = read_registry(root)
        for name in names:
            task = registry['tasks'][name]
            if task['status'] != 'closed':
                held.append({'task': name, 'intentional': name.startswith(PREFIX + '保留-')})
                continue
            note = task.get('note', '')
            if not note.startswith(NOTE):
                continue  # 独占目录未取得的空任务没有测试产物，不计入观察。
            row = json.loads(note[len(NOTE):])
            matches = [item for item in registry['items'] if item.get('id') == row['cache_id'] and item['task'] == name]
            if len(matches) != 1 or matches[0].get('automatic_policy') != registry['automation']['id']:
                warnings.append(name + '：找不到对应当前策略的冻结条目')
                continue
            item = matches[0]
            output = root / row['output']
            if row['output'] not in scopes or not output.is_relative_to(root / 'tmp/运行/战术演训日常测试'):
                warnings.append(name + '：输出位置与已登记观察范围不符')
                continue
            frozen = [{**r, 'path': (root / r['path']).relative_to(output).as_posix()} for r in item['files']]
            # 文件不存在不推断其已回收；旧记录保留历史统计，但不算本次现场核验。
            if not any((root / r['path']).exists() for r in item['files']):
                if output.exists() and members(output):
                    warnings.append(name + '：旧冻结文件已离开，但目录出现未登记文件')
                else:
                    unavailable.append(name)
                    if time.time() - task['closed_at'] < 7 * 86400:
                        warnings.append(name + '：未满闲置七天，冻结文件已不可见，需核查')
                continue
            if members(output) != frozen:
                warnings.append(name + '：存在未登记文件或冻结文件变化，保持原有范围，不扩大授权')
                continue
            if row['passed'] and row['files'] != 1:
                warnings.append(name + '：通过结果超过一份摘要')
                continue
            rows.append({**row, 'task': name})
        window = observation_window(rows, time.time())
        unexpected = [item['task'] for item in held if not item['intentional']]
        total = sum(row['bytes'] for row in rows)
        if total > 5 * 2**30:
            warnings.append('本入口已核对日常产物超过5GiB，仅提示；不缩短七天期限')
        groups = {key: [row for row in rows if row['passed'] is passed]
                  for key, passed in [('passed', True), ('failed', False)]}
        by_result = {key: {'runs': len(group), 'files': sum(row['files'] for row in group),
                           'bytes': sum(row['bytes'] for row in group)} for key, group in groups.items()}
        return {'mode': '第三阶段观察', **window, 'verified_runs': len(rows), 'by_result': by_result,
                'files': sum(row['files'] for row in rows), 'bytes': total,
                'held_tasks': held, 'missing_frozen_history_not_reverified': unavailable, 'warnings': warnings,
                'recommended_ready': window['enough_time_and_runs'] and not warnings and not unexpected,
                'scope': '仅本机此日常入口新建任务；不推断未登记的旧产物、外部依赖或人工场景验收'}
    finally:
        audit.cli('close', '--task', audit.task, '--note', '只读观察已结束；未修改载荷、缓存条目、其他任务或清理策略')
