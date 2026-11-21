"""执行已确认的可读路径清单，原子发布索引并同步冻结交付；失败回滚。"""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid
from urllib.request import urlopen

from 统一资源 import ROOT, HOME, current, read, save, sha

sys.path.insert(0, str(ROOT / 'scripts/维护清理'))
from 清理工具 import lock
from 占用检查 import require_unoccupied

TASK = '前线资产库可读迁移V002-01a1a4e6'
MANIFEST = ROOT / 'local-data/资源库命名/可读命名V001-01a1a4e6/复核版/命名预览.json'
OUTPUT = ROOT / 'local-data/资源库命名/可读迁移V002-01a1a4e6'
DELIVERY = ROOT / 'local-data/丧尸模型/十三款优化/交接/当前交付.json'


def guarded(path, boundary):
    path, boundary = Path(path), Path(boundary).resolve()
    resolved = path.resolve()
    if resolved == boundary or not resolved.is_relative_to(boundary):
        raise ValueError('路径超出本轮范围：' + str(path))
    for part in [path, *path.parents]:
        if part.exists() and (part.is_symlink() or part.is_junction()):
            raise ValueError('拒绝链接或目录联接：' + str(part))
        if part.resolve() == boundary:
            break
    return resolved


def path_mapping(files, root=ROOT):
    mapping = {}
    for row in files:
        mapping[row['old_path']] = row['new_path']
        mapping[(root / row['old_path']).as_posix()] = str(root / row['new_path'])
    return mapping


def remap(value, mapping):
    """只替换完全相等的已登记路径，媒体网址和证据摘要保持原样。"""
    if isinstance(value, str):
        return mapping.get(value.replace('\\', '/'), value)
    if isinstance(value, list):
        return [remap(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: remap(item, mapping) for key, item in value.items()}
    return value


def atomic_bytes(path, content):
    path = Path(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.writing')
    with temp.open('xb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')


def move_one(source, destination, boundary):
    source, destination = guarded(source, boundary), guarded(destination, boundary)
    if not source.is_file() or destination.exists():
        raise ValueError('迁移源缺失或目标已存在')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Windows同盘重命名；目标存在时拒绝覆盖，不复制媒体载荷。
    source.rename(destination)


def ps(code):
    result = subprocess.run(['powershell', '-NoProfile', '-Command', '[Console]::OutputEncoding=[Text.UTF8Encoding]::new();' + code], capture_output=True, check=True)
    return result.stdout.decode('utf-8-sig').strip()


def inventory():
    code = "$procs=@{}; Get-CimInstance Win32_Process | ForEach-Object { $procs[[int]$_.ProcessId]=$_ }; @(Get-NetTCPConnection -State Listen -LocalAddress 127.0.0.1 | ForEach-Object { $pr=$procs[[int]$_.OwningProcess]; [PSCustomObject]@{port=[int]$_.LocalPort;pid=[int]$_.OwningProcess;command=$pr.CommandLine;created=$pr.CreationDate.ToString('O')} }) | ConvertTo-Json -Compress"
    data = json.loads(ps(code) or '[]')
    return data if isinstance(data, list) else [data]


def get(port, endpoint):
    with urlopen(f'http://127.0.0.1:{port}/{endpoint}', timeout=5) as response:
        return json.load(response)


def service_plan():
    services, unchanged = [], []
    for row in inventory():
        command = (row['command'] or '').replace('\\', '/').lower()
        if ROOT.as_posix().lower() not in command and not any(name in command for name in ['预览载具靶场.py', '预览丧尸群体测试.py']):
            continue
        endpoint = 'api/status'
        if 'scripts/断箭/启动正式资料库.py' in command:
            args, app = ['scripts/断箭/启动正式资料库.py', '--serve'], 'scavenger-library'
            if row['port'] != 8767:
                raise ValueError('资产库服务端口不是约定端口')
        elif 'scripts/预览载具靶场.py' in command:
            args, app = ['scripts/预览载具靶场.py', '--serve', '--port', str(row['port'])], 'scavenger-range'
        elif 'scripts/预览丧尸群体测试.py' in command:
            args, app = ['scripts/预览丧尸群体测试.py', '--serve', '--port', str(row['port'])], 'scavenger-horde'
        elif 'scripts/资源优化/游戏版服务.py' in command:
            manifest = get(row['port'], 'manifest.json')
            variant = manifest['game_variant']
            if variant.get('baseline') or variant['resolution'] not in (256, 512) or variant['level'] not in (0, 1, 2):
                raise ValueError('独立靶场规格不在本轮范围')
            args, app = ['scripts/资源优化/游戏版服务.py', '--app', 'range', '--resolution', str(variant['resolution']), '--level', str(variant['level']), '--port', str(row['port'])], 'scavenger-range'
        else:
            unchanged.append(row)
            continue
        if get(row['port'], endpoint).get('app') != app:
            raise ValueError('端口应用身份不匹配')
        services.append({**row, 'args': args, 'app': app})
    return services, unchanged


def stop_services(services, stopped):
    latest = {row['port']: row for row in inventory()}
    for row in services:
        if latest.get(row['port']) != {key: row[key] for key in ('port', 'pid', 'command', 'created')}:
            raise ValueError('服务进程身份发生变化，停止迁移')
    for row in services:
        ps(f"Stop-Process -Id {int(row['pid'])} -ErrorAction Stop")
        stopped.append(row)


def start_services(services, output):
    started = []
    for row in services:
        with (output / f'服务{row["port"]}.log').open('ab') as log:
            proc = subprocess.Popen([sys.executable, '-X', 'utf8', '-B', str(ROOT / row['args'][0]), *row['args'][1:]], cwd=ROOT, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        started.append({'port': row['port'], 'pid': proc.pid, 'app': row['app'], 'args': row['args']})
    deadline = time.time() + 35
    pending = list(started)
    event = threading.Event()
    while pending:
        for row in list(pending):
            try:
                if get(row['port'], 'api/status').get('app') == row['app']:
                    pending.remove(row)
            except (OSError, ValueError):
                pass
        if pending and time.time() >= deadline:
            raise RuntimeError('服务恢复超时：' + str(pending))
        if pending:
            event.wait(.2)
    return started


def execute(approved_sha, *, task=TASK, manifest_path=MANIFEST, output=OUTPUT, metadata_transform=None, require_full_coverage=True):
    """默认执行完整迁移；名称修复可传入明确的部分文件清单和元数据修正。"""
    TASK, MANIFEST, OUTPUT = task, Path(manifest_path), Path(output)
    if sha(MANIFEST) != approved_sha:
        raise ValueError('命名清单不是本次确认的摘要')
    manifest = read(MANIFEST)
    if manifest['status'] not in ('待命名样例确认未迁移', '名称修复已授权待执行') or manifest['file_count'] != len(manifest['files']):
        raise ValueError('命名清单阶段或数量异常')
    if OUTPUT.exists():
        raise ValueError('迁移轮次已存在，先检查回执，禁止重复执行')
    receipt = {'task': TASK, 'approved_manifest_sha256': approved_sha, 'approval': manifest.get('approval', '工程师确认样例，继续迁移'), 'started_at': time.time(), 'status': '预检', 'moved': [], 'deleted': 0}
    with lock():
        if sha(HOME / '当前版本.json') != manifest['pointer_sha256']:
            raise ValueError('当前指针与已确认样例不同，停止')
        pointer, catalog, routes, index = current()
        files = manifest['files']
        boundary = HOME / '当前资源'
        actual = {p.relative_to(ROOT).as_posix() for p in boundary.rglob('*') if p.is_file()}
        requested = {row['old_path'] for row in files}
        if (actual != requested if require_full_coverage else not requested.issubset(actual)) or len(requested) != len(files) or len({row['new_path'].casefold() for row in files}) != len(files):
            raise ValueError('源集合或目标唯一性变化')
        for row in files:
            source, target = guarded(ROOT / row['old_path'], boundary), guarded(ROOT / row['new_path'], boundary)
            stat = source.stat()
            if target.exists() or stat.st_size != row['bytes'] or stat.st_mtime_ns != row['mtime_ns'] or sha(source) != row['sha256']:
                raise ValueError('源文件身份或目标位置变化：' + row['old_path'])
        old_delivery = DELIVERY.read_bytes()
        delivery = json.loads(old_delivery.decode('utf-8-sig'))
        if delivery['library_pointer_sha256'] != manifest['pointer_sha256']:
            raise ValueError('当前丧尸交付绑定了不同指针')
        services, unchanged = service_plan()
        require_unoccupied([ROOT / row['old_path'] for row in files])
        OUTPUT.mkdir(parents=True, exist_ok=False)
        old_pointer = (HOME / '当前版本.json').read_bytes()
        (OUTPUT / '旧指针.json').write_bytes(old_pointer)
        (OUTPUT / '旧交付.json').write_bytes(old_delivery)
        save(OUTPUT / '服务迁移前.json', {'affected': services, 'unchanged': unchanged})
        save(OUTPUT / '任务登记迁移前.json', read(ROOT / 'local-data/维护清理/登记.json'))
        mapping = path_mapping(files)
        data = {name: remap(read(index / name), mapping) for name in pointer['metadata']}
        if metadata_transform is not None:
            metadata_transform(data)
        aliases = data['来源别名.json']
        for row in files:
            aliases.setdefault(row['old_path'], row['sha256'])
            aliases.setdefault(row['new_path'], aliases[row['old_path']])
        revision = pointer['revision'] + 1
        data['资源索引.json'].update(revision=revision, updated_at=time.time())
        folder = '索引版本/' + f'{revision:03}-可读命名-' + uuid.uuid4().hex[:10]
        new_index = guarded(HOME / folder, HOME)
        new_index.mkdir(parents=True, exist_ok=False)
        for name, value in data.items():
            if value == read(index / name):
                shutil.copyfile(index / name, new_index / name)
            else:
                save(new_index / name, value)
        new_pointer = {**pointer, 'revision': revision, 'directory': folder, 'metadata': {name: sha(new_index / name) for name in pointer['metadata']}}
        new_pointer_bytes = encoded(new_pointer)
        new_pointer_sha = hashlib.sha256(new_pointer_bytes).hexdigest()
        new_delivery = remap(copy.deepcopy(delivery), mapping)
        new_delivery.update(library_revision=revision, library_pointer_sha256=new_pointer_sha, catalog_sha256=sha(new_index / '资源索引.json'), frozen_at=datetime.now(timezone.utc).isoformat(), path_migration={'task': TASK, 'previous_delivery_sha256': hashlib.sha256(old_delivery).hexdigest(), 'content_unchanged': True})
        receipt.update(old_pointer_sha256=manifest['pointer_sha256'], new_pointer_sha256=new_pointer_sha, new_revision=revision, new_index=folder, unchanged_services=unchanged)
        save(OUTPUT / '迁移回执.json', receipt)
        stopped = []
        published = False
        delivery_written = False
        try:
            stop_services(services, stopped)
            # 进程停止后再次确认媒体没有被编辑器占用，再进行逐文件移动。
            require_unoccupied([ROOT / row['old_path'] for row in files])
            if sha(HOME / '当前版本.json') != manifest['pointer_sha256'] or DELIVERY.read_bytes() != old_delivery:
                raise ValueError('提交前索引或交付发生变化')
            receipt['status'] = '移动中'
            save(OUTPUT / '迁移回执.json', receipt)
            with (OUTPUT / '逐文件迁移.jsonl').open('x', encoding='utf-8') as journal:
                for row in files:
                    journal.write(json.dumps({'phase': '准备移动', **row}, ensure_ascii=False) + '\n')
                    journal.flush()
                    os.fsync(journal.fileno())
                    move_one(ROOT / row['old_path'], ROOT / row['new_path'], boundary)
                    receipt['moved'].append(row)
                    journal.write(json.dumps({'phase': '已移动', 'old_path': row['old_path'], 'new_path': row['new_path']}, ensure_ascii=False) + '\n')
                    journal.flush()
            for row in files:
                if sha(ROOT / row['new_path']) != row['sha256']:
                    raise ValueError('迁移后摘要不一致')
            atomic_bytes(DELIVERY, encoded(new_delivery))
            delivery_written = True
            atomic_bytes(HOME / '当前版本.json', new_pointer_bytes)
            published = True
            receipt.update(status='已迁移待服务恢复', files=len(files), bytes=sum(row['bytes'] for row in files), new_delivery_sha256=sha(DELIVERY))
            save(OUTPUT / '迁移回执.json', receipt)
        except Exception as error:
            # 未完成事务恢复原指针与原交付，逐项反向移动，保留失败证据。
            if published or sha(HOME / '当前版本.json') == new_pointer_sha:
                atomic_bytes(HOME / '当前版本.json', old_pointer)
            if delivery_written:
                atomic_bytes(DELIVERY, old_delivery)
            rollback_errors = []
            for row in reversed(receipt['moved']):
                try:
                    move_one(ROOT / row['new_path'], ROOT / row['old_path'], boundary)
                except Exception as rollback_error:
                    rollback_errors.append(str(rollback_error))
            receipt.update(status='回滚异常' if rollback_errors else '已回滚', error=str(error), rollback_errors=rollback_errors)
            save(OUTPUT / '迁移回执.json', receipt)
            if stopped and not rollback_errors:
                receipt['restored_services'] = start_services(stopped, OUTPUT)
                save(OUTPUT / '迁移回执.json', receipt)
            raise
    # 服务启动会建立自身清单，退出维护锁后启动，避免消费者启动取锁时互等。
    receipt['started_services'] = start_services(stopped, OUTPUT)
    receipt.update(status='已迁移服务已恢复待回归', finished_at=time.time())
    save(OUTPUT / '迁移回执.json', receipt)
    save(OUTPUT / '路径交接.json', {'task': TASK, 'new_pointer_sha256': new_pointer_sha, 'new_revision': revision, 'frozen_delivery_sha256': sha(DELIVERY), 'path_map': {row['old_path']: row['new_path'] for row in files}, 'notes': ['历史索引与性能证据保持原字节；新采样使用本轮重新冻结交付', '其他任务保护未关闭或改写；新路径由本轮与消费者独立保护', '旧路径仅记录在历史证据与来源别名中，当前媒体均使用可读路径']})
    print(json.dumps({key: receipt[key] for key in ('status', 'files', 'bytes', 'new_revision', 'new_pointer_sha256', 'deleted')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved-sha', required=True, help='工程师已确认命名样例所绑定的清单摘要')
    execute(parser.parse_args().approved_sha)
