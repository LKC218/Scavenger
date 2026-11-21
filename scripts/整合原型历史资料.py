"""按已审核清单逐文件归档原型资料；默认预览，不删除任何清理候选。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = 'local-data/原型/丧尸防线/'
ARCHIVE = BASE + 'V003-01a1a2af/历史资料/'
MANIFEST = 'local-data/维护清理/原型旧版退役方案-V003-01a1aa5e/逐文件审核清单.json'
RECEIPT = 'local-data/维护清理/原型旧版整合退役-V003-01a1aa5e/归档迁移回执.json'
SOURCE_SHA = '00ca74b615ee1b898d82cedb52590c136b5565121d4a1195b563919767d02594'


def checked(relative):
    """拒绝绝对路径、越界、父级跳转及链接，限定在当前项目内。"""
    raw = Path(relative)
    if raw.is_absolute() or '..' in raw.parts or '\\' in relative:
        raise ValueError('仅接受不含父级跳转的项目相对正斜杠路径')
    path = ROOT / raw
    for part in [path, *path.parents]:
        if part == ROOT:
            break
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('拒绝符号链接或目录联接：' + relative)
    if not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('路径越界：' + relative)
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(row, path):
    if not path.is_file() or path.stat().st_size != row['bytes'] or digest(path) != row['sha256']:
        raise ValueError('文件身份已变化：' + str(path))
    if path.stat().st_mtime_ns != row['mtime_ns']:
        raise ValueError('文件时间身份已变化，先核查：' + str(path))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.replace(temporary, path)


def now():
    return datetime.now(timezone.utc).isoformat()


def services():
    for port in [8792, 8793]:
        with socket.socket() as connection:
            connection.settimeout(0.5)
            if connection.connect_ex(('127.0.0.1', port)) == 0:
                raise ValueError('旧端口仍在运行，拒绝迁移：' + str(port))
    with urlopen('http://127.0.0.1:8794/api/status', timeout=3) as response:
        status = json.load(response)
    if status.get('version') != 'V003' or status.get('source') != SOURCE_SHA:
        raise ValueError('当前V003服务身份不符')


def load_rows(args):
    manifest = checked(args.manifest)
    actual = digest(manifest)
    if args.approved_sha and actual != args.approved_sha:
        raise ValueError('审核清单摘要不符')
    data = json.loads(manifest.read_text(encoding='utf-8'))
    rows = [r for r in data['files'] if r['action'] == '整合归档保留待审核']
    if len(data['files']) != 147 or len(rows) != 144 or sum(r['bytes'] for r in rows) != 10275586:
        raise ValueError('清单数量或大小超出已审核范围')
    if len({r['path'].casefold() for r in rows}) != 144 or len({r['target'].casefold() for r in rows}) != 144:
        raise ValueError('清单路径重复')
    for row in rows:
        version = row['version']
        if version not in ['V001', 'V002']:
            raise ValueError('版本越界')
        prefix = BASE + version + '-01a1a2af/'
        if not row['path'].startswith(prefix):
            raise ValueError('源目录越界')
        expected = ARCHIVE + version + '/' + row['path'][len(prefix):]
        if row['target'] != expected:
            raise ValueError('归档目标与源文件映射不一致')
        checked(row['path'])
        checked(row['target'])
    return rows, actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', default=MANIFEST)
    parser.add_argument('--approved-sha')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if (args.apply or args.rollback) and not args.approved_sha:
        raise ValueError('执行必须提供已审核清单摘要')
    rows, manifest_sha = load_rows(args)
    receipt_path = checked(RECEIPT)
    mapping_path = checked(ARCHIVE + '归档映射.json')
    if args.rollback:
        services()
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if receipt['manifest_sha256'] != manifest_sha or receipt['status'] == '已回滚':
            raise ValueError('回执身份不符或已经回滚')
        moved = receipt['files']
        if not moved:
            raise ValueError('没有已迁移文件')
        approved = {r['path']: r for r in rows}
        for row in moved:
            if any(row[k] != approved[row['path']][k] for k in ['target', 'bytes', 'sha256', 'mtime_ns']):
                raise ValueError('回执文件与审核清单不一致')
            verify(row, checked(row['target']))
            if checked(row['path']).exists():
                raise ValueError('旧位置已存在，拒绝覆盖')
        for row in reversed(moved):
            destination = checked(row['path'])
            destination.parent.mkdir(parents=True, exist_ok=True)
            checked(row['target']).rename(destination)
            verify(row, destination)
        receipt.update(status='已回滚', rolled_back_at_utc=now())
        save(receipt_path, receipt)
        save(mapping_path, receipt)
        print(json.dumps({'status': '已回滚', 'files': len(moved)}, ensure_ascii=False))
        return
    for row in rows:
        verify(row, checked(row['path']))
        if checked(row['target']).exists():
            raise ValueError('归档目标已存在，拒绝覆盖：' + row['target'])
    if receipt_path.exists() or mapping_path.exists():
        raise ValueError('已存在迁移回执或映射，先核对已有状态')
    if not args.apply:
        print(json.dumps({'mode': '只读预览', 'files': len(rows), 'bytes': sum(r['bytes'] for r in rows), 'sha256': manifest_sha}, ensure_ascii=False))
        return
    services()
    receipt = {'schema': 1, 'status': '迁移中', 'manifest': args.manifest, 'manifest_sha256': manifest_sha,
               'started_at_utc': now(), 'files': [], 'note': '仅归档保留资料，三张清理候选仍由现有维护工具处理'}
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    with receipt_path.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    for row in rows:
        source, target = checked(row['path']), checked(row['target'])
        verify(row, source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError('归档目标出现冲突，停止迁移')
        source.rename(target)
        receipt['files'].append({**row, 'moved_at_utc': now()})
        save(receipt_path, receipt)
        verify(row, target)
    receipt.update(status='完成', completed_at_utc=now(), file_count=len(rows), bytes=sum(r['bytes'] for r in rows))
    save(receipt_path, receipt)
    save(mapping_path, receipt)
    print(json.dumps({'status': '完成', 'files': len(rows), 'bytes': receipt['bytes'], 'mapping': mapping_path.relative_to(ROOT).as_posix(), 'mapping_sha256': digest(mapping_path)}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError) as error:
        raise SystemExit('停止：' + str(error)) from error
