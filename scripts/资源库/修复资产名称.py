"""修复丧尸来源与独立角色名称，将512规格统一标注为细节档。"""

from collections import Counter
import copy
from pathlib import Path
import time

from 统一资源 import ROOT, HOME, current, read, save, sha
from 迁移可读命名 import execute

TASK = '资产名称修复V003-01a1a4e6'
BASE = ROOT / 'local-data/资源库命名/名称修复V003-01a1a4e6'
RENAMES = {'原始丧尸角色包': '丧尸角色整包（来源对照）', '当前丧尸动态角色': '丧尸角色00'}


def rename_names(value):
    if isinstance(value, str):
        return RENAMES.get(value, value)
    if isinstance(value, list):
        return [rename_names(item) for item in value]
    if isinstance(value, dict):
        return {key: rename_names(item) for key, item in value.items()}
    return value


def update_metadata(data):
    for filename in list(data):
        data[filename] = rename_names(data[filename])
    for card in data['资源索引.json']['models']:
        if card['id'] == 'model:zombie-source':
            card['aliases'] = list(dict.fromkeys([*card.get('aliases', []), '原始丧尸角色包', '丧尸原包', '来源对照']))
            card['tags'] = list(dict.fromkeys([*card.get('tags', []), '来源对照']))
            card['variant'] = '整包来源对照，非独立运行角色'
            note = '用于来源核查与整包对照；当前群体运行使用13个独立角色。页面256／512为整包派生规格，未改原件另存原始归档。'
            card['limitations'] = list(dict.fromkeys([*card.get('limitations', []), note]))
        elif card['id'] == 'model:zombie-runtime':
            card['aliases'] = list(dict.fromkeys([*card.get('aliases', []), '当前丧尸动态角色', '丧尸角色0', '角色00']))
            card['variant'] = '独立角色00与项目自制循环'


def prepare():
    folder = BASE / '准备'
    if folder.exists():
        raise ValueError('本轮准备目录已存在，禁止覆盖凭证')
    pointer, catalog, routes, index = current()
    records = {row['path']: row for row in routes.values()}
    records.update({row['path']: row for row in read(index / '主工程.json').values()})
    files = []
    for old, record in sorted(records.items()):
        new = old.replace('512回退', '512细节档')
        for old_name, new_name in RENAMES.items():
            new = new.replace(old_name, new_name)
        if new == old:
            continue
        path = ROOT / old
        if path.stat().st_size != record['bytes'] or sha(path) != record['sha256']:
            raise ValueError('文件身份发生变化：' + old)
        kind = Path(old).relative_to('local-data/资源库/当前资源').parts[0]
        files.append({'old_path': old, 'new_path': new, 'sha256': record['sha256'], 'bytes': record['bytes'], 'mtime_ns': path.stat().st_mtime_ns, 'kind': kind})
    if not files:
        raise ValueError('没有需要修正的名称')
    manifest = {'schema': 1, 'task': TASK, 'status': '名称修复已授权待执行', 'approval': '工程师：基于以上两个讨论，进行名称修复', 'created_at': time.time(), 'revision': pointer['revision'], 'pointer_sha256': sha(HOME / '当前版本.json'), 'file_count': len(files), 'counts': dict(Counter(row['kind'] for row in files)), 'files': files}
    folder.mkdir(parents=True, exist_ok=False)
    save(folder / '名称修复清单.json', manifest)
    (folder / '界面修改前.mjs').write_bytes((ROOT / 'src/资料库/app.mjs').read_bytes())
    save(folder / '名称修复范围.json', {'names': RENAMES, 'specification': {'old': '512回退', 'new': '512细节档', 'meaning': '贴图最大边长512像素，手动选择的细节档'}, 'default_list_changed': False, 'content_unchanged': True})
    print('名称修复准备完成，文件', len(files), '清单摘要', sha(folder / '名称修复清单.json'), flush=True)


def apply():
    manifest = BASE / '准备/名称修复清单.json'
    execute(sha(manifest), task=TASK, manifest_path=manifest, output=BASE / '执行', metadata_transform=update_metadata, require_full_coverage=False)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'apply'])
    args = parser.parse_args()
    (prepare if args.action == 'prepare' else apply)()
